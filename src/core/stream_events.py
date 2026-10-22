"""The chat stream's event vocabulary, shared by every producer and consumer.

A turn streams plain dicts: over SSE to the Web UI and in-process to the CLI.
The graph stream (``agents.streaming.events``), worker invocations, the turn
pipeline and the API transport produce them; the trace recorder, the CLI
renderer and the Web UI consume them. Each TypedDict below lists one event's
fields. Worker events forwarded into the orchestrator stream also carry
``parent_id`` (the delegate's tool call ID) and ``parent_name`` (its tool name).
In-process consumers may see private ``_raw_*`` fields, which are removed
before an event leaves the process.

The Web UI reads the same type names from ``web/assets/js/stream_events.js``;
regenerate it with ``python -m core.stream_events`` (``--check`` verifies it).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from pydantic import Field, TypeAdapter

from core.paths import REPO_ROOT


class StreamEventType(StrEnum):
    STATUS = "status"
    THINKING = "thinking"
    THINKING_WITHHELD = "thinking_withheld"
    FINAL_START = "final_start"
    TOKEN = "token"
    RESET_STREAM = "reset_stream"
    TOOL_START = "tool_start"
    TOOL_PROGRESS = "tool_progress"
    TOOL_END = "tool_end"
    AGENT_RESULT_DELTA = "agent_result_delta"
    AGENT_RESULT_RESET = "agent_result_reset"
    DONE = "done"
    ERROR = "error"


# Events a worker forwards from its own graph into the orchestrator stream.
WORKER_FORWARDED_EVENTS = frozenset({
    StreamEventType.TOOL_START,
    StreamEventType.TOOL_END,
    StreamEventType.THINKING,
    StreamEventType.THINKING_WITHHELD,
    StreamEventType.STATUS,
    StreamEventType.AGENT_RESULT_DELTA,
    StreamEventType.AGENT_RESULT_RESET,
})


class _Forwarded(TypedDict):
    parent_id: NotRequired[str]
    parent_name: NotRequired[str]


class StatusEvent(_Forwarded):
    """Progress text for the waiting indicator; ``phase`` marks transport stages.

    ``replayed`` marks the API's answer to a repeated request ID.
    """

    type: Literal["status"]
    message: str
    phase: NotRequired[str]
    request_id: NotRequired[str]
    replayed: NotRequired[bool]


class ThinkingEvent(_Forwarded):
    """The next piece of model reasoning."""

    type: Literal["thinking"]
    text: str


class ThinkingWithheldEvent(_Forwarded):
    """The model reasoned, but the provider disclosed no text.

    ``reasoning_tokens`` is the provider's reasoning token count, 0 when unknown.
    """

    type: Literal["thinking_withheld"]
    reasoning_tokens: int


class FinalStartEvent(TypedDict):
    """The final answer begins; ``token`` events follow."""

    type: Literal["final_start"]


class TokenEvent(TypedDict):
    """The next piece of the final answer."""

    type: Literal["token"]
    text: str


class ResetStreamEvent(TypedDict):
    """Discard the final answer streamed so far; a tool call superseded it."""

    type: Literal["reset_stream"]


class ToolStartEvent(_Forwarded):
    """A tool call starts; ``input`` is a bounded preview of its arguments."""

    type: Literal["tool_start"]
    id: str
    name: str
    label: str
    input: str


class ToolProgressEvent(TypedDict):
    """The model is still writing a named tool call."""

    type: Literal["tool_progress"]
    id: str
    name: str
    label: str


class ToolEndEvent(_Forwarded):
    """A tool call returned; ``output`` is a bounded preview of its reply."""

    type: Literal["tool_end"]
    id: str
    name: str
    label: str
    output: str
    image_paths: list[str]
    status: NotRequired[str]
    interactions: NotRequired[list[dict[str, Any]]]


class AgentResultDeltaEvent(_Forwarded):
    """The next piece of a worker's final answer, shown under its delegate."""

    type: Literal["agent_result_delta"]
    text: str


class AgentResultResetEvent(_Forwarded):
    """Discard the worker answer streamed so far."""

    type: Literal["agent_result_reset"]


class DoneEvent(TypedDict):
    """The turn's answer.

    The graph emits ``reply`` and ``thread_id``; the API's terminal event also
    carries the rest of its ``ChatResponse``.
    """

    type: Literal["done"]
    reply: str
    thread_id: NotRequired[str]
    project_id: NotRequired[str]
    request_id: NotRequired[str | None]
    replayed: NotRequired[bool]
    image_paths: NotRequired[list[str]]
    status: NotRequired[str]
    recovery: NotRequired[dict[str, Any] | None]


class ErrorEvent(TypedDict):
    """The turn failed."""

    type: Literal["error"]
    message: str


StreamEvent = Annotated[
    StatusEvent
    | ThinkingEvent
    | ThinkingWithheldEvent
    | FinalStartEvent
    | TokenEvent
    | ResetStreamEvent
    | ToolStartEvent
    | ToolProgressEvent
    | ToolEndEvent
    | AgentResultDeltaEvent
    | AgentResultResetEvent
    | DoneEvent
    | ErrorEvent,
    Field(discriminator="type"),
]

_EVENT_ADAPTER: TypeAdapter[StreamEvent] = TypeAdapter(StreamEvent)
JS_MODULE_PATH = REPO_ROOT / "web" / "assets" / "js" / "stream_events.js"


def validate_stream_event(event: Mapping[str, Any]) -> StreamEvent:
    """Check one event against its declared fields; undeclared fields are ignored."""
    return _EVENT_ADAPTER.validate_python(dict(event))


def javascript_module() -> str:
    members = "".join(f'  {member.name}: "{member.value}",\n' for member in StreamEventType)
    return (
        "/** Chat stream event types. Generated by `python -m core.stream_events`; do not edit. */\n\n"
        f"export const STREAM_EVENT = Object.freeze({{\n{members}}});\n"
    )


def main(argv: list[str] | None = None) -> int:
    import sys

    args = sys.argv[1:] if argv is None else argv
    expected = javascript_module()
    if args == ["--check"]:
        current = JS_MODULE_PATH.read_text(encoding="utf-8") if JS_MODULE_PATH.exists() else ""
        if current != expected:
            print(f"{JS_MODULE_PATH} is out of date; run python -m core.stream_events", file=sys.stderr)
            return 1
        return 0
    if args:
        print("usage: python -m core.stream_events [--check]", file=sys.stderr)
        return 2
    JS_MODULE_PATH.write_text(expected, encoding="utf-8")
    return 0


__all__ = [
    "AgentResultDeltaEvent",
    "AgentResultResetEvent",
    "DoneEvent",
    "ErrorEvent",
    "FinalStartEvent",
    "ResetStreamEvent",
    "StatusEvent",
    "StreamEvent",
    "StreamEventType",
    "ThinkingEvent",
    "ThinkingWithheldEvent",
    "TokenEvent",
    "ToolEndEvent",
    "ToolProgressEvent",
    "ToolStartEvent",
    "WORKER_FORWARDED_EVENTS",
    "validate_stream_event",
]


if __name__ == "__main__":
    raise SystemExit(main())
