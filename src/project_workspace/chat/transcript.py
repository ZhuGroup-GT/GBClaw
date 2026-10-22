"""Render the authoritative chat database as a human-readable Markdown view."""

from __future__ import annotations

import html
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from core.llm.reasoning import withheld_reasoning_note
from project_workspace.chat.models import TraceEventRecord, TurnRecord
from core.util.io import write_text_atomic


def _preformatted(value: str | None) -> str:
    text = str(value or "")
    return f"<pre>{html.escape(text)}</pre>" if text else "_Empty._"


def _pretty_json(value: str | None) -> str:
    text = str(value or "")
    if not text:
        return ""
    try:
        return json.dumps(json.loads(text), ensure_ascii=False, indent=2, sort_keys=True)
    except (json.JSONDecodeError, TypeError):
        return text


def _tool_label(event: TraceEventRecord) -> str:
    return event.label or event.tool_name or "tool"


def _coalesce_reasoning_events(
    events: Sequence[TraceEventRecord],
) -> list[TraceEventRecord]:
    """Merge interleaved deltas per parent while retaining tool boundaries."""
    out: list[TraceEventRecord] = []
    open_reasoning: dict[
        tuple[str | None, str | None], TraceEventRecord
    ] = {}
    start_by_name: dict[str, str] = {}
    start_by_call_id: dict[str, str] = {}

    def seal(parent_event_id: str | None) -> None:
        for key in [key for key in open_reasoning if key[0] == parent_event_id]:
            open_reasoning.pop(key, None)

    for source in events:
        if source.event_type == "reasoning":
            key = (source.parent_event_id, source.label)
            existing = open_reasoning.get(key)
            if existing is not None:
                existing.output_text = (
                    existing.output_text or ""
                ) + (source.output_text or "")
                existing.completed_at = source.completed_at or existing.completed_at
                continue
            event = source.model_copy(deep=True)
            open_reasoning[key] = event
            out.append(event)
            continue

        if source.event_type == "tool_start":
            seal(source.parent_event_id)
            if source.tool_name:
                start_by_name[source.tool_name] = source.event_id
            if source.tool_call_id:
                start_by_call_id[source.tool_call_id] = source.event_id
        elif source.event_type == "tool_end":
            seal(source.parent_event_id)
            if source.parent_event_id is None:
                start_id = (
                    start_by_call_id.get(source.tool_call_id or "")
                    or start_by_name.get(source.tool_name or "")
                )
                seal(start_id)
        out.append(source)
    return out


def _reasoning_tokens(input_json: str | None) -> int:
    try:
        return int((json.loads(input_json or "{}") or {}).get("reasoning_tokens") or 0)
    except (ValueError, TypeError, AttributeError):
        return 0


def _render_trace(events: Sequence[TraceEventRecord], fallback_reasoning: str) -> list[str]:
    if not events and not fallback_reasoning:
        return []

    events = _coalesce_reasoning_events(events)
    lines = ["<details>", "<summary>Reasoning and tool trace</summary>", ""]
    event_by_id = {event.event_id: event for event in events}
    has_reasoning_event = False
    for event in events:
        parent = event_by_id.get(event.parent_event_id or "")
        parent_suffix = f" · parent: {_tool_label(parent)}" if parent else ""
        if event.event_type == "reasoning":
            has_reasoning_event = True
            lines.extend(
                [
                    f"#### Reasoning{parent_suffix}",
                    "",
                    _preformatted(event.output_text),
                    "",
                ]
            )
        elif event.event_type == "reasoning_withheld":
            has_reasoning_event = True
            lines.extend([f"#### Reasoning withheld{parent_suffix}", "",
                          withheld_reasoning_note(_reasoning_tokens(event.input_json)), ""])
        elif event.event_type == "tool_start":
            lines.extend(
                [
                    f"#### Tool started: {_tool_label(event)}{parent_suffix}",
                    "",
                    "**Input**",
                    "",
                    _preformatted(_pretty_json(event.input_json)),
                    "",
                ]
            )
        elif event.event_type == "tool_end":
            lines.extend(
                [
                    f"#### Tool completed: {_tool_label(event)}{parent_suffix}",
                    "",
                    "**Output**",
                    "",
                    _preformatted(event.output_text),
                    "",
                ]
            )

    if fallback_reasoning and not has_reasoning_event:
        lines.extend(["#### Reasoning", "", _preformatted(fallback_reasoning), ""])
    lines.extend(["</details>", ""])
    return lines


def render_chat_transcript(
    turns: Sequence[TurnRecord],
    trace_by_turn: Mapping[str, Sequence[TraceEventRecord]],
) -> str:
    lines = [
        "# Chat transcript",
        "",
        "> Generated from `history.sqlite`. Do not edit this file; it is refreshed after each finished turn and when viewed or exported.",
        "",
    ]
    if not turns:
        lines.extend(["_No chat turns recorded._", ""])
        return "\n".join(lines)

    for turn in turns:
        role = "User" if turn.role == "user" else "Assistant"
        lines.extend(
            [
                f"## {turn.sequence:04d} · {role}",
                "",
                (
                    f"> status: `{turn.status.value}` · created: `{turn.created_at}`"
                    f" · thread: `{turn.thread_id}`"
                ),
                "",
                "### Message" if turn.role == "user" else "### Answer",
                "",
                turn.content.strip() or "_No content._",
                "",
            ]
        )
        if turn.role == "assistant":
            lines.extend(
                _render_trace(trace_by_turn.get(turn.turn_id, []), turn.reasoning)
            )
        lines.extend(["---", ""])
    return "\n".join(lines)


def write_chat_transcript(path: Path, content: str) -> None:
    """Atomically replace the derived transcript."""
    write_text_atomic(path, content, skip_unchanged=True)
