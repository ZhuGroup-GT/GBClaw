"""Durable recording of one chat exchange while its agent events stream."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

from core.stream_events import StreamEventType
from agents.middleware import ContextCompactionError
from agents.streaming.events import public_stream_event
from core.execution.sessions import ensure_execution_active
from project_workspace.artifacts.presentation import extract_artifact_image_paths
from project_workspace.chat.models import TurnStatus
from project_workspace.chat.repository import ChatRepository
from project_workspace.chat.trace import ChatTraceRecorder
from project_workspace.models import utc_now_iso

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecordedChatResult:
    reply: str
    user_turn_id: str
    assistant_turn_id: str
    run_ids: list[str]
    image_paths: list[str] = field(default_factory=list)
    status: str = "completed"
    recovery: dict | None = None


class RecordedChatExecution:
    """Record one exchange; callers retain leases, streams and completion hooks."""

    def __init__(
        self, chat: ChatRepository, thread_id: str, message: str, *,
        request_id: str | None = None, request_owner: str | None = None,
    ) -> None:
        self.chat = chat
        self.thread_id = thread_id
        self.user, self.assistant = chat.start_exchange(
            thread_id, message, utc_now_iso(),
            request_id=request_id, request_owner=request_owner,
        )
        try:
            self.recorder = ChatTraceRecorder(chat, self.assistant.turn_id)
        except Exception as exc:
            # The exchange already committed; do not leave an orphan when trace
            # setup fails before the caller receives this execution object.
            try:
                chat.fail_turn(
                    self.assistant.turn_id, content=str(exc), completed_at=utc_now_iso(),
                )
            except Exception:
                LOGGER.warning("Failed to finalize chat after trace setup failed.", exc_info=True)
            raise
        self.reply = ""
        self._image_paths: dict[str, None] = {}

    def apply_event(self, event: dict) -> dict:
        """Persist raw evidence and return the API's image-enriched event view."""
        self.recorder.apply_event(event)
        if event.get("type") == StreamEventType.TOOL_END:
            for path in extract_artifact_image_paths(
                [event.get("image_paths", []), event.get("_raw_output", event.get("output"))],
                self.chat.project_root.name,
            ):
                self._image_paths[path] = None
        if event.get("type") == StreamEventType.DONE:
            self.reply = str(event.get("reply") or "").strip()
            for path in extract_artifact_image_paths(self.reply, self.chat.project_root.name):
                self._image_paths[path] = None
            return {**event, "image_paths": list(self._image_paths)}
        return event

    def flush(self) -> None:
        self.recorder.flush()

    def flush_best_effort(self) -> None:
        """Preserve buffered evidence before the caller inspects cancellation."""
        try:
            self.flush()
        except Exception:
            LOGGER.warning("Failed to flush trace while terminating chat execution.", exc_info=True)

    def require_reply(self) -> None:
        if not self.reply:
            raise ValueError("Agent returned an empty reply")

    def complete(self) -> RecordedChatResult:
        """Commit success only after the caller's cancellation checks pass."""
        self.require_reply()
        self.chat.complete_turn(
            self.assistant.turn_id, content=self.reply,
            reasoning=self.recorder.reasoning_text(), completed_at=utc_now_iso(),
        )
        self.export_transcript_best_effort()
        return self.result()

    def terminate(self, *, content: str, interrupted: bool) -> None:
        """Finish after flushing trace, preserving an existing terminal turn."""
        active = self.chat.get_active_assistant_turn(self.thread_id)
        if active is None or active.turn_id != self.assistant.turn_id:
            return
        finish = self.chat.interrupt_turn if interrupted else self.chat.fail_turn
        finish(
            self.assistant.turn_id, content=content,
            reasoning=self.recorder.reasoning_text(), completed_at=utc_now_iso(),
        )
        self.export_transcript_best_effort()

    def export_transcript_best_effort(self) -> None:
        """Refresh ``chat/transcript.md`` once per finished turn; SQLite stays authoritative."""
        try:
            self.chat.ensure_transcript()
        except Exception:
            LOGGER.warning("Failed to refresh derived chat transcript.", exc_info=True)

    def recovery_details(self, error) -> dict:
        recovery = error.as_dict()
        recovery["task_state"] = {"tool": "get_task_state", "thread_id": self.thread_id}
        recovery["project_id"] = self.chat.project_root.name
        return recovery

    def result(
        self, *, reply: str | None = None, status: str = "completed", recovery: dict | None = None,
    ) -> RecordedChatResult:
        return RecordedChatResult(
            reply=self.reply if reply is None else reply,
            user_turn_id=self.user.turn_id, assistant_turn_id=self.assistant.turn_id,
            run_ids=self.recorder.referenced_run_ids(), image_paths=list(self._image_paths),
            status=status, recovery=recovery,
        )


async def execute_recorded_chat(
    *,
    chat: ChatRepository,
    thread_id: str,
    message: str,
    events: AsyncIterator[dict],
    on_event: Callable[[dict], Awaitable[None]] | None = None,
    post_complete: Callable[[RecordedChatResult], Awaitable[None]] | None = None,
    cancel_event: threading.Event | None = None,
    request_id: str | None = None,
    request_owner: str | None = None,
) -> RecordedChatResult:
    execution = RecordedChatExecution(
        chat, thread_id, message, request_id=request_id, request_owner=request_owner,
    )
    try:
        async for event in events:
            event = execution.apply_event(event)
            if on_event is not None:
                await on_event(public_stream_event(event))
        execution.flush()
        ensure_execution_active()
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError
        execution.require_reply()
    except asyncio.CancelledError:
        execution.flush_best_effort()
        execution.terminate(content="Agent execution was cancelled.", interrupted=True)
        raise
    except ContextCompactionError as exc:
        # A context limit pauses the conversation, not the completed science.
        # Do not replay tools or mark completed runs failed during recovery.
        execution.flush_best_effort()
        if cancel_event is not None and cancel_event.is_set():
            execution.terminate(content="Agent execution was cancelled.", interrupted=True)
            raise asyncio.CancelledError from exc
        recovery = execution.recovery_details(exc)
        reply = "Context processing paused safely. " + recovery["message"] + " " + recovery["action"]
        execution.terminate(content=reply, interrupted=True)
        result = execution.result(reply=reply, status="paused", recovery=recovery)
        if on_event is not None:
            await on_event({"type": StreamEventType.DONE, "reply": reply, "status": "paused",
                            "recovery": recovery, "image_paths": result.image_paths})
        return result
    except Exception as exc:
        execution.flush_best_effort()
        execution.terminate(
            content=str(exc), interrupted=cancel_event is not None and cancel_event.is_set(),
        )
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError from exc
        raise

    try:
        result = execution.complete()
    except ValueError:
        if chat.get_turn(execution.assistant.turn_id).status is TurnStatus.INTERRUPTED:
            raise asyncio.CancelledError
        raise
    if post_complete is not None:
        try:
            await post_complete(result)
        except Exception:
            LOGGER.warning(
                "Post-completion semantic update failed; chat remains completed.",
                exc_info=True,
            )
    return result
