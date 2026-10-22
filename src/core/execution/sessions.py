"""Execution sessions: cancellation state and the subprocess trees they own.

One session exists per admitted chat execution. Tools register their
subprocesses under the active session key so a disconnect, a stop request or
server shutdown terminates every scientific or sandboxed process tree.
"""

from __future__ import annotations

import os
import logging
import signal
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from subprocess import Popen
from typing import TYPE_CHECKING, Callable

from core.util.graph_config import runtime_ids

_lock = threading.Lock()
_cancel_events: dict[str, threading.Event] = {}
_active_procs: dict[str, list[Popen[str]]] = {}
_sessions: dict[str, _SessionControl] = {}
_cancel_listeners: list[Callable[[], None]] = []
LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from project_workspace.execution.ownership import ExecutionCancellation


class SessionCancelledError(RuntimeError):
    """The current execution no longer admits model or tool work."""


@dataclass
class _SessionControl:
    key: str
    event: threading.Event
    cancellation: ExecutionCancellation | None = None
    stop_watcher: threading.Event = field(default_factory=threading.Event)
    watcher: threading.Thread | None = None

    @property
    def logical_key(self):
        return self.key.rsplit(":execution:", 1)[0] if self.cancellation else self.key

    def cancelled(self):
        if self.event.is_set():
            return True
        if self.cancellation is not None:
            try:
                requested = self.cancellation.is_requested()
            except (OSError, ValueError):
                LOGGER.exception("Cannot verify shared execution cancellation; stopping locally.")
                requested = True
            if requested:
                self.event.set()
        return self.event.is_set()


_current_session: ContextVar[_SessionControl | None] = ContextVar("gbclaw_execution_session", default=None)


@contextmanager
def execution_session(session_key: str, event: threading.Event):
    with _lock:
        control = _sessions.get(session_key)
        if control is None or control.event is not event:
            raise RuntimeError("Execution session is no longer registered")
    token = _current_session.set(control)
    try:
        yield
    finally:
        _current_session.reset(token)


def current_session_key() -> str | None:
    """Key of the execution session entered by the current context, if any."""
    control = _current_session.get()
    return control.key if control is not None else None


def ensure_execution_active():
    control = _current_session.get()
    if control is not None and control.cancelled():
        raise SessionCancelledError("Agent execution was cancelled.")


def add_cancel_listener(callback: Callable[[], None]) -> None:
    """Call *callback* whenever a session is signalled, e.g. to wake resource waiters."""
    with _lock:
        _cancel_listeners.append(callback)


def make_session_key(project_id: str, thread_id: str, *, execution_owner: str | None = None) -> str:
    logical = f"{project_id}:{thread_id}"
    if execution_owner:
        return f"{logical}:execution:{execution_owner}"
    current = _current_session.get()
    return current.key if current is not None and current.logical_key == logical else logical


def runtime_session_key() -> str | None:
    """Session key of the chat invocation running the current tool, if any."""
    project_id, chat_thread_id = runtime_ids()
    if not project_id or not chat_thread_id:
        return None
    return make_session_key(project_id, chat_thread_id)


def register_session(session_key: str, cancel_event: threading.Event,
                     *, cancellation: ExecutionCancellation | None = None) -> None:
    control = _SessionControl(session_key, cancel_event, cancellation)
    with _lock:
        if session_key in _cancel_events:
            raise RuntimeError("This chat session already has an active execution owner")
        _cancel_events[session_key] = cancel_event
        _sessions[session_key] = control
    if cancellation is not None:
        def watch():
            while not control.stop_watcher.wait(0.1):
                if control.cancelled():
                    cancel_session(session_key, cancel_event)
                    return
        control.watcher = threading.Thread(target=watch, name="gbclaw-execution-cancel", daemon=True)
        try:
            control.watcher.start()
        except BaseException:
            with _lock:
                if _sessions.get(session_key) is control:
                    _sessions.pop(session_key, None)
                    _cancel_events.pop(session_key, None)
            cancel_event.set()
            raise


def unregister_session(session_key: str, cancel_event: threading.Event | None = None) -> None:
    with _lock:
        if cancel_event is not None and _cancel_events.get(session_key) is not cancel_event:
            return
        _cancel_events.pop(session_key, None)
        control = _sessions.pop(session_key, None)
    if control is not None:
        control.stop_watcher.set()
        # A delayed process termination must retain its original event identity.
        if control.watcher is not None and control.watcher is not threading.current_thread():
            control.watcher.join(timeout=0.5)


def is_session_cancelled(session_key: str | None) -> bool:
    if not session_key:
        return False
    current = _current_session.get()
    if current is not None and current.key == session_key:
        return current.cancelled()
    with _lock:
        control = _sessions.get(session_key)
    if control is not None:
        return control.cancelled()
    # Retired generation keys cannot fall back to a newer turn's event. Legacy
    # direct scripts without an execution generation keep their previous behavior.
    return ":execution:" in session_key


def register_process(session_key: str | None, proc: Popen[str]) -> None:
    if not session_key:
        return
    if is_session_cancelled(session_key):
        _terminate_process(proc)
        return
    with _lock:
        _active_procs.setdefault(session_key, []).append(proc)
    if is_session_cancelled(session_key):
        _terminate_process(proc)


def unregister_process(session_key: str | None, proc: Popen[str]) -> None:
    if not session_key:
        return
    with _lock:
        procs = _active_procs.get(session_key)
        if not procs:
            return
        try:
            procs.remove(proc)
        except ValueError:
            pass
        if not procs:
            _active_procs.pop(session_key, None)


def _kill_process_tree(pid: int, *, sig: signal.Signals = signal.SIGTERM) -> None:
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)
    except ProcessLookupError:
        return
    except OSError:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return


def _terminate_process(proc: Popen[str]) -> None:
    if proc.returncode is not None:
        return
    try:
        own_group = os.getpgid(proc.pid) == proc.pid
    except ProcessLookupError:
        return
    # Capture group ownership before poll() reaps a leader that has already
    # exited while its children still keep the output pipes open.
    if proc.poll() is not None:
        if own_group:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return
    _kill_process_tree(proc.pid, sig=signal.SIGTERM)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            if own_group:
                # Children can keep output pipes open after their leader exits.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            return
        time.sleep(0.1)
    _kill_process_tree(proc.pid, sig=signal.SIGKILL)


def terminate_process_tree(proc: Popen[str]) -> None:
    """Terminate a host-owned command and its isolated process group."""
    _terminate_process(proc)


def signal_session_cancel(session_key: str) -> None:
    """Set the local flag promptly; the owning watcher terminates subprocesses."""
    with _lock:
        event = _cancel_events.get(session_key)
    if event is not None:
        event.set()
    with _lock:
        listeners = list(_cancel_listeners)
    for listener in listeners:
        listener()


def cancel_session(session_key: str, cancel_event: threading.Event | None = None) -> None:
    with _lock:
        event = _cancel_events.get(session_key)
        if cancel_event is not None and event is not cancel_event:
            return
        procs = list(_active_procs.get(session_key, ()))
    if event is not None:
        event.set()
    for proc in procs:
        _terminate_process(proc)


def cancel_all_sessions() -> None:
    """Cancel every session and terminate every registered process (shutdown)."""
    with _lock:
        session_keys = list(_cancel_events.keys())
        all_procs = [proc for procs in _active_procs.values() for proc in procs]
    for key in session_keys:
        cancel_session(key)
    for proc in all_procs:
        _terminate_process(proc)
