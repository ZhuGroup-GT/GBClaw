"""One chat turn, from admission to post-completion memory, shared by the API and CLI.

A turn is one request within the existing persistent thread, not a new thread
or a new browser session. Project metadata chooses the thread; preparation
recovers its runtime state and adapts a fresh project-memory snapshot. The
transport (HTTP request, SSE stream or terminal) only parses input, renders
events and decides how long it keeps the project after a disconnect.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
import logging
import threading

from langgraph.graph.state import CompiledStateGraph

from agents.invoke import build_run_config
from agents.middleware import ProjectRuntimeContext
from agents.streaming.events import iter_chat_events
from interfaces.commands import chat_command, parse_chat_command
from planning.mode import PlanningMode
from core.execution.sessions import (
    cancel_session,
    execution_session,
    make_session_key,
    register_session,
    unregister_session,
)
from core.llm.config import LLMEndpoint
from core.settings.runtime_context import RuntimeContextConfig
from interfaces.recording import RecordedChatResult, execute_recorded_chat
from project_memory.snapshot.session import read_project_memory_snapshot
from project_memory.extraction.retry import drain_one_retry_job, process_turn_memory_with_retry
from project_workspace.models import utc_now_iso
from planning.direct_execution import supersede_pending_plan_draft
from project_memory.run_artifacts.cleanup import cancel_running_runs_for_chat
from project_workspace.execution.ownership import ChatLease
from project_workspace.execution.recovery import repair_checkpoint
from project_workspace.store import ProjectStore

LOGGER = logging.getLogger(__name__)


def repair_project_checkpoint(
    graph: CompiledStateGraph, project_id: str, thread_id: str,
) -> bool:
    config = build_run_config(
        thread_id=thread_id, project_id=project_id, run_name="gbclaw_memory_prepare",
    )
    return repair_checkpoint(graph, config)


def prepare_project_session(
    graph: CompiledStateGraph,
    store: ProjectStore,
    project_id: str,
    thread_id: str,
) -> ProjectRuntimeContext:
    """Recover an abandoned execution under the caller's exclusive ChatLease."""
    cancel_running_runs_for_chat(
        store, project_id, thread_id,
        reason="Previous chat turn ended before the delegated run completed.",
    )
    repair_project_checkpoint(graph, project_id, thread_id)
    chat = store.chat(project_id)
    orphaned = chat.get_active_assistant_turn(thread_id)
    if orphaned is not None:
        chat.interrupt_turn(
            orphaned.turn_id,
            content="Previous execution owner ended before completion.",
            reasoning=orphaned.reasoning,
            completed_at=utc_now_iso(),
        )
    try:
        # Replay only completed publications; no LLM or scientific work runs here.
        store.runs(project_id).repair_pending_memory(limit=8)
    except Exception:
        LOGGER.exception("Pending scientific memory recovery failed")
    # Project data is read by the memory layer; only the agent layer constructs
    # request context. Neither operation changes the persistent chat thread.
    return ProjectRuntimeContext.from_memory_snapshot(read_project_memory_snapshot(store, project_id))


class TurnInputError(ValueError):
    """The submitted message cannot start a turn (e.g. a bare /plan)."""


@dataclass(frozen=True)
class TurnInput:
    agent_message: str
    display_message: str
    planning_mode: PlanningMode


def parse_turn_input(message: str, *, planning_mode: PlanningMode | str | None = None) -> TurnInput:
    """Normalize slash commands and return the resolved planning mode."""
    try:
        parsed = parse_chat_command(message, planning_mode=planning_mode)
    except ValueError as exc:
        raise TurnInputError(str(exc)) from exc
    if parsed.planning_mode is not PlanningMode.AUTO and not parsed.message:
        raise TurnInputError(chat_command(parsed.planning_mode).missing_task_message)
    if not parsed.message:
        raise TurnInputError("message must not be empty")
    return TurnInput(parsed.message, parsed.display_message, parsed.planning_mode)


@dataclass(frozen=True)
class TurnServices:
    """Process-wide objects every turn needs, built once at startup."""

    agent: CompiledStateGraph
    store: ProjectStore
    memory_endpoint: LLMEndpoint | None
    runtime_context_config: RuntimeContextConfig | None


class TurnOwnership:
    """The exclusive chat lease plus its registered, cancellable execution session.

    Raises ``ChatBusyError`` when another execution owns the project thread.
    """

    def __init__(
        self,
        store: ProjectStore,
        project_id: str,
        thread_id: str,
        *,
        frontend_token: str | None = None,
        request_id: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.lease = ChatLease(store.project_path(project_id), thread_id,
                               frontend_token=frontend_token, request_id=request_id)
        try:
            self.cancel_event = cancel_event if cancel_event is not None else threading.Event()
            self.session_key = make_session_key(project_id, thread_id, execution_owner=self.lease.owner)
            register_session(self.session_key, self.cancel_event, cancellation=self.lease.cancellation)
        except BaseException:
            self.lease.close()
            raise

    @property
    def owner(self) -> str:
        return self.lease.owner

    @property
    def request_id(self) -> str | None:
        return self.lease.request_id

    def cancel(self) -> None:
        """Signal cancellation and terminate registered processes; keep ownership."""
        self.cancel_event.set()
        cancel_session(self.session_key, self.cancel_event)

    def close(self) -> None:
        try:
            self.cancel()
            unregister_session(self.session_key, self.cancel_event)
        finally:
            self.lease.close()


def semantic_post_complete(
    services: TurnServices, project_id: str,
) -> Callable[[RecordedChatResult], Awaitable[None]]:
    """Extract memory for the completed turn, then drain one queued retry."""
    async def callback(result: RecordedChatResult) -> None:
        await asyncio.to_thread(
            process_turn_memory_with_retry, services.store, services.memory_endpoint, project_id,
            result.assistant_turn_id, result.run_ids, services.runtime_context_config,
        )
        await asyncio.to_thread(
            drain_one_retry_job, services.store, services.memory_endpoint, project_id,
            services.runtime_context_config,
        )

    return callback


def _check_cancelled(ownership: TurnOwnership) -> None:
    if ownership.cancel_event.is_set():
        raise asyncio.CancelledError


async def run_turn(
    services: TurnServices,
    ownership: TurnOwnership,
    turn: TurnInput,
    *,
    project_id: str,
    thread_id: str,
    source: str,
    run_name: str,
    tags: Sequence[str],
    stream: bool = False,
    on_event: Callable[[dict], Awaitable[None]] | None = None,
    request_id: str | None = None,
    request_owner: str | None = None,
) -> RecordedChatResult:
    """Prepare the session, stream the graph, record the exchange and update memory.

    ``request_id`` links the exchange to an admitted idempotent request receipt.
    Cancellation raises ``asyncio.CancelledError`` after the turn is recorded as
    interrupted; a context-limit pause returns a ``paused`` result. Any other
    failure repairs the thread checkpoint before propagating.
    """
    try:
        with execution_session(ownership.session_key, ownership.cancel_event):
            _check_cancelled(ownership)
            if turn.planning_mode is PlanningMode.SKIP:
                await asyncio.to_thread(supersede_pending_plan_draft, services.store, project_id)
                _check_cancelled(ownership)
            runtime_context = await asyncio.to_thread(
                prepare_project_session, services.agent, services.store, project_id, thread_id,
            )
            if turn.planning_mode is not PlanningMode.AUTO:
                runtime_context = replace(runtime_context, planning_mode=turn.planning_mode)
            _check_cancelled(ownership)
            metadata = {"source": source, "project_id": project_id,
                        "planning_mode": turn.planning_mode.value}
            if stream:
                metadata["stream"] = True
            return await execute_recorded_chat(
                chat=services.store.chat(project_id), thread_id=thread_id,
                message=turn.display_message,
                events=iter_chat_events(
                    services.agent, turn.agent_message, thread_id=thread_id, project_id=project_id,
                    run_name=run_name, tags=list(tags), metadata=metadata, context=runtime_context,
                ),
                on_event=on_event,
                post_complete=semantic_post_complete(services, project_id),
                cancel_event=ownership.cancel_event,
                request_id=request_id, request_owner=request_owner,
            )
    except Exception:
        try:
            await asyncio.to_thread(repair_project_checkpoint, services.agent, project_id, thread_id)
        except Exception:
            LOGGER.warning("Checkpoint repair after a failed turn did not succeed.", exc_info=True)
        raise


__all__ = [
    "TurnInput", "TurnInputError", "TurnOwnership", "TurnServices", "parse_turn_input",
    "prepare_project_session", "repair_project_checkpoint", "run_turn", "semantic_post_complete",
]
