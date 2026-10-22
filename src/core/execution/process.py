"""Run subprocesses under an execution session so cancellation stops their process tree."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
import time
from typing import IO, Any, Sequence

from core.execution.sessions import (
    SessionCancelledError,
    current_session_key,
    is_session_cancelled,
    register_process,
    runtime_session_key,
    terminate_process_tree,
    unregister_process,
)


@dataclass(frozen=True)
class SupervisedExit:
    """How a supervised process ended; output is None for redirected streams."""

    returncode: int | None
    stdout: Any
    stderr: Any
    cancelled: bool = False
    timed_out: bool = False


def supervise(
    proc: subprocess.Popen, *, session_key: str | None, timeout_sec: float | None,
) -> SupervisedExit:
    """Wait for *proc*, terminating its process group on cancellation or timeout."""
    register_process(session_key, proc)
    started = time.monotonic()
    try:
        while True:
            if is_session_cancelled(session_key):
                terminate_process_tree(proc)
                stdout, stderr = proc.communicate()
                return SupervisedExit(proc.returncode, stdout, stderr, cancelled=True)
            if timeout_sec is not None and time.monotonic() - started > timeout_sec:
                terminate_process_tree(proc)
                stdout, stderr = proc.communicate()
                return SupervisedExit(proc.returncode, stdout, stderr, timed_out=True)
            try:
                stdout, stderr = proc.communicate(timeout=0.25)
            except subprocess.TimeoutExpired:
                continue
            # cancel_session() may terminate the tree directly; report that exit
            # as a cancellation rather than as the command's own failure.
            return SupervisedExit(proc.returncode, stdout, stderr,
                                  cancelled=is_session_cancelled(session_key))
    finally:
        unregister_process(session_key, proc)


def run_cancellable(
    args: Sequence[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    stdout: int | IO | None = subprocess.PIPE,
    stderr: int | IO | None = subprocess.PIPE,
    text: bool = True,
    session_key: str | None = None,
    label: str = "Subprocess",
) -> subprocess.CompletedProcess:
    """A ``subprocess.run(check=False)`` counterpart owned by the active session.

    Raises ``subprocess.TimeoutExpired`` after terminating the process tree, and
    ``SessionCancelledError`` when the session is cancelled before or during
    execution. Without an explicit key, the current execution session is used.
    """
    key = session_key or current_session_key() or runtime_session_key()
    if is_session_cancelled(key):
        raise SessionCancelledError(f"{label} was cancelled before launch.")
    proc = subprocess.Popen(
        list(args), cwd=cwd, env=env, stdout=stdout, stderr=stderr, text=text,
        start_new_session=True,
    )
    result = supervise(proc, session_key=key, timeout_sec=timeout)
    if result.cancelled:
        raise SessionCancelledError(f"{label} was cancelled.")
    if result.timed_out:
        raise subprocess.TimeoutExpired(list(args), timeout, output=result.stdout, stderr=result.stderr)
    return subprocess.CompletedProcess(list(args), result.returncode, result.stdout, result.stderr)


__all__ = ["SupervisedExit", "run_cancellable", "supervise"]
