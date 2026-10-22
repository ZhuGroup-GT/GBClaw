"""The interactive chat loop: one owned project, one agent turn at a time."""

from __future__ import annotations

import asyncio
import logging
import queue
import re
import sys
import textwrap
import threading
import time

from core.stream_events import StreamEventType
from interfaces.cli.input import (
    LineHistory, canonical_command, collect_message, format_slash_help, setup_readline,
)
from interfaces.cli.projects import select_or_create_project
from interfaces.cli.render import PRINT_LOCK, Renderer, refresh_term_width, safe_print
from interfaces.turn import (
    TurnInput, TurnInputError, TurnOwnership, TurnServices, parse_turn_input, run_turn,
)
from project_memory.run_artifacts.cleanup import cancel_running_runs_for_chat
from project_workspace.chat.models import TurnStatus
from project_workspace.execution.ownership import ChatBusyError, ProjectFrontendLease
from project_workspace.models import ProjectMeta
from project_workspace.store import ProjectStore

LOGGER = logging.getLogger(__name__)

# Idle-prompt Ctrl+C must be pressed twice within this window to exit.
_INTERRUPT_EXIT_WINDOW_S = 2.0
# A first token that looks like a command (not a path such as /data/x.cfg).
_COMMAND_TOKEN_RE = re.compile(r"^/[A-Za-z]+$")
_CHAT_COMMANDS = {"/plan", "/noplan"}
DEFAULT_HISTORY_LIMIT = 10
MAX_HISTORY_LIMIT = 200
_UNKNOWN = "/unknown"


class TurnControl:
    """Lets the main thread abort the turn coroutine that runs on the worker thread.

    Cooperative cancellation (``TurnOwnership.cancel``) ends the graph at its next
    model step and terminates registered subprocesses; aborting the task also cuts
    a model stream that is still being generated, whose answer a cancelled turn
    would discard anyway. A tool already running in a thread finishes first.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._abort_requested = False

    def attach(self, loop: asyncio.AbstractEventLoop, task: asyncio.Task) -> None:
        with self._lock:
            self._loop, self._task = loop, task
            if self._abort_requested:
                loop.call_soon(task.cancel)

    def abort(self) -> None:
        with self._lock:
            self._abort_requested = True
            loop, task = self._loop, self._task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass  # the loop already finished


def _run_one_turn(
    services: TurnServices,
    project_id: str,
    thread_id: str,
    turn: TurnInput,
    events: queue.Queue,
    cancel_event: threading.Event,
    frontend_token: str | None = None,
    *,
    ownership: TurnOwnership | None = None,
    control: TurnControl | None = None,
) -> None:
    """Run a single user turn on a worker thread, streaming events to *events*.

    Always queues a final ``None`` sentinel so consumers can detect completion.
    Failures and cancellation are queued as one ``error`` event; a context-limit
    pause arrives as a ``done`` event with ``status="paused"``. With *ownership*
    given, the caller keeps the execution lease (and closes it); otherwise this
    function takes and releases it. *control* lets the caller abort the turn.
    """

    async def put(event: dict) -> None:
        events.put(event)

    async def runner() -> None:
        if control is not None:
            control.attach(asyncio.get_running_loop(), asyncio.current_task())
        await run_turn(
            services, ownership, turn, project_id=project_id, thread_id=thread_id,
            source="cli", run_name="gbclaw_cli_chat", tags=["gbclaw", "cli"], on_event=put,
        )

    owned_here = ownership is None
    try:
        if ownership is None:
            ownership = TurnOwnership(services.store, project_id, thread_id,
                                      frontend_token=frontend_token, cancel_event=cancel_event)
        asyncio.run(runner())
    except asyncio.CancelledError:
        events.put({"type": StreamEventType.ERROR, "message": "Agent execution was cancelled."})
    except Exception as exc:
        events.put({"type": StreamEventType.ERROR, "message": str(exc)})
    finally:
        try:
            if owned_here and ownership is not None:
                ownership.close()
        finally:
            events.put(None)


_FINAL_EVENTS = {StreamEventType.DONE, StreamEventType.ERROR}


def _drain_queue(events: queue.Queue, renderer: Renderer, *, muted: bool = False) -> None:
    """Render events until the ``None`` sentinel; *muted* shows only the final event."""
    while True:
        event = events.get()
        if event is None:
            return
        if not muted or event.get("type") in _FINAL_EVENTS:
            safe_print(renderer.render(event))


def _cli_command(message: str) -> tuple[str, str] | None:
    """``(command, argument)`` for a CLI command; None for chat, including /plan and /noplan."""
    if not message.startswith("/"):
        return None
    parts = message.strip().split(maxsplit=1)
    token, argument = parts[0], (parts[1] if len(parts) > 1 else "")
    name = canonical_command(token)
    if name in _CHAT_COMMANDS:
        return None
    if name is not None:
        return name, argument.strip()
    if _COMMAND_TOKEN_RE.match(token):
        return _UNKNOWN, token
    return None


def _print_history(store: ProjectStore, meta: ProjectMeta, limit: int) -> None:
    repository = store.chat(meta.project_id)
    count = repository.count_turns(meta.thread_id)
    turns = repository.list_turns(meta.thread_id, offset=max(0, count - limit), limit=limit,
                                  include_reasoning=False)
    if not turns:
        safe_print("  (no history yet)\n")
        return
    if count > len(turns):
        safe_print(f"  … {count - len(turns)} earlier turns; /history N shows more\n")
    for turn in turns:
        who = "you" if turn.role == "user" else "GBclaw"
        status = getattr(turn, "status", None)
        marker = f" [{status.value}]" if who == "GBclaw" and status not in (None, TurnStatus.COMPLETED) else ""
        stamp = str(turn.created_at)[:19].replace("T", " ")
        safe_print(f"  {stamp}  {who}{marker}:\n{textwrap.indent(turn.content.rstrip(), '    ')}\n\n")


def _announce_project(store: ProjectStore, meta: ProjectMeta, *, history_limit: int) -> None:
    safe_print(f"\nProject: {meta.name}  [{meta.project_id}]\n")
    if meta.goal.strip():
        safe_print(f"Goal: {textwrap.shorten(meta.goal.strip(), width=160, placeholder='…')}\n")
    safe_print(f"thread_id: {meta.thread_id}\n\n")
    if history_limit > 0:
        safe_print("--- recent turns ---\n")
        _print_history(store, meta, history_limit)
        safe_print("--- type a message, or /help ---\n\n")


def _run_command(name: str, argument: str, *, store: ProjectStore, meta: ProjectMeta) -> None:
    if name == "/help":
        safe_print(format_slash_help())
    elif name == "/projects":
        for item in store.list_projects():
            current = "  (current)" if item.project_id == meta.project_id else ""
            safe_print(f"  {item.project_id}  {item.name}{current}\n")
    elif name == "/thread":
        safe_print(f"  project_id: {meta.project_id}\n  thread_id:  {meta.thread_id}\n")
    elif name == "/history":
        try:
            limit = int(argument) if argument else DEFAULT_HISTORY_LIMIT
        except ValueError:
            safe_print(f"  ! /history takes a number of turns, got {argument!r}\n")
            return
        _print_history(store, meta, max(1, min(limit, MAX_HISTORY_LIMIT)))
    elif name == "/clear":
        with PRINT_LOCK:
            sys.stdout.write("\x1b[2J\x1b[H")
            sys.stdout.flush()
    elif name == _UNKNOWN:
        safe_print(f"  ! unknown command {argument}; /help lists the commands. "
                   "Start with a space to send a message that begins with \"/\".\n")


def _run_turn_interactively(services: TurnServices, renderer: Renderer, meta: ProjectMeta,
                            turn: TurnInput, frontend_token: str) -> bool:
    """Run one turn and render its events. Returns False when the user asked to exit."""
    store, project_id, thread_id = services.store, meta.project_id, meta.thread_id
    cancel_event = threading.Event()
    try:
        ownership = TurnOwnership(store, project_id, thread_id,
                                  frontend_token=frontend_token, cancel_event=cancel_event)
    except ChatBusyError as exc:
        safe_print(f"  ! {exc}\n")
        return True
    events: queue.Queue = queue.Queue()
    control = TurnControl()
    worker = threading.Thread(
        target=_run_one_turn,
        args=(services, project_id, thread_id, turn, events, cancel_event, frontend_token),
        kwargs={"ownership": ownership, "control": control}, daemon=True,
        name=f"gbclaw-turn-{thread_id}",
    )
    renderer.reset()
    safe_print(renderer.render({"type": StreamEventType.STATUS, "message": "Preparing project context…"}))
    worker.start()
    interrupted = quit_requested = False
    try:
        try:
            _drain_queue(events, renderer)
        except KeyboardInterrupt:
            interrupted = True
            # Flag the session and terminate registered subprocesses, then cut the
            # turn coroutine so an in-flight model stream stops at once.
            ownership.cancel()
            control.abort()
            safe_print("\n  stopping… (Ctrl+C again exits without waiting)\n")
            try:
                _drain_queue(events, renderer, muted=True)
            except KeyboardInterrupt:
                quit_requested = True
    finally:
        if quit_requested:
            safe_print("\n  leaving; the interrupted turn is repaired when the project is next opened.\n")
        else:
            worker.join(timeout=60.0)
            if worker.is_alive():
                LOGGER.warning("The turn's worker thread did not finish after its final event.")
            ownership.close()
            if interrupted:
                try:
                    cancel_running_runs_for_chat(store, project_id, thread_id,
                                                 reason="User stopped the in-flight agent run.")
                except Exception:
                    LOGGER.warning("Cancelling the stopped turn's runs failed.", exc_info=True)
    return not quit_requested


def _chat_in_project(services: TurnServices, meta: ProjectMeta, frontend: ProjectFrontendLease,
                     renderer: Renderer, *, history_limit: int) -> ProjectMeta | None:
    """Chat in one project. Returns the project to switch to, or None to exit."""
    store = services.store
    _announce_project(store, meta, history_limit=history_limit)
    last_interrupt_at = 0.0
    while True:
        refresh_term_width()
        try:
            message = collect_message(input)
        except KeyboardInterrupt:
            now = time.monotonic()
            if now - last_interrupt_at <= _INTERRUPT_EXIT_WINDOW_S:
                safe_print("\n")
                return None
            last_interrupt_at = now
            safe_print("\n(Press Ctrl+C again to exit, or type /quit)\n")
            continue
        if message is None:
            safe_print("\n")
            return None
        last_interrupt_at = 0.0
        if not message.strip():
            continue
        command = _cli_command(message)
        if command is not None:
            name, argument = command
            if name == "/quit":
                return None
            if name == "/switch":
                chosen = select_or_create_project(store, title="Switch project (q keeps the current one):")
                if chosen is None or chosen.project_id == meta.project_id:
                    safe_print("  (staying on the current project)\n")
                    continue
                return chosen
            _run_command(name, argument, store=store, meta=meta)
            continue
        try:
            turn = parse_turn_input(message)
        except TurnInputError as exc:
            safe_print(f"  ! {exc}\n")
            continue
        if not _run_turn_interactively(services, renderer, meta, turn, frontend.token):
            return None
        meta = store.get(meta.project_id)


def chat_loop(services: TurnServices, meta: ProjectMeta, *,
              history_limit: int = DEFAULT_HISTORY_LIMIT) -> None:
    """Own *meta*'s project and chat in it until the user quits.

    ``/switch`` moves to another project in-process. A project another frontend
    holds (for example an open browser tab) cannot be entered; the menu is shown
    again instead.
    """
    setup_readline()
    history = LineHistory()
    renderer = Renderer()
    store = services.store
    try:
        while meta is not None:
            project_root = store.project_path(meta.project_id)
            try:
                frontend = ProjectFrontendLease(project_root)
            except ChatBusyError as exc:
                safe_print(f"  ! {meta.name} [{meta.project_id}]: {exc}\n")
                meta = select_or_create_project(store, title="Choose another project (q exits):")
                continue
            try:
                history.activate(project_root / "chat" / "cli_history")
                renderer.project_root = project_root
                meta = _chat_in_project(services, meta, frontend, renderer, history_limit=history_limit)
            finally:
                frontend.close()
    finally:
        history.save()
    safe_print("Bye.\n")


__all__ = ["DEFAULT_HISTORY_LIMIT", "TurnControl", "chat_loop"]
