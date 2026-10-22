"""Convert streamed agent events into permanent chat trace records."""

from __future__ import annotations

import json
import uuid
from typing import Any

from core.stream_events import StreamEventType
from project_workspace.chat.repository import ChatRepository
from project_workspace.models import utc_now_iso
from project_workspace.chat.models import TraceEventRecord


class ChatTraceRecorder:
    def __init__(self, repository: ChatRepository, turn_id: str) -> None:
        repository.get_turn(turn_id)
        self.repository = repository
        self.turn_id = turn_id
        existing = repository.list_trace_events(turn_id)
        self._sequence = max((event.sequence for event in existing), default=-1) + 1
        self._reasoning_parts: list[str] = []
        self._run_ids: list[str] = []
        self._start_by_stream_id: dict[str, str] = {}
        self._start_by_name: dict[str, str] = {}
        self._pending: list[TraceEventRecord] = []
        self._open_reasoning: dict[
            tuple[str | None, str | None], TraceEventRecord
        ] = {}

    def apply_event(self, event: dict) -> None:
        event_type = str(event.get("type") or "")
        if event_type == StreamEventType.THINKING:
            text = str(event.get("text") or "")
            if not text:
                return
            self._reasoning_parts.append(text)
            parent_event_id = self._resolve_parent(self._parent_key(event))
            label = str(event.get("label") or "") or None
            key = (parent_event_id, label)
            open_reasoning = self._open_reasoning.get(key)
            if open_reasoning is not None:
                open_reasoning.output_text = (open_reasoning.output_text or "") + text
                open_reasoning.completed_at = utc_now_iso()
            else:
                self._open_reasoning[key] = self._append(
                    event_type="reasoning",
                    parent_event_id=parent_event_id,
                    label=label,
                    output_text=text,
                    status="completed",
                    completed_at=utc_now_iso(),
                )
            return

        if event_type == StreamEventType.THINKING_WITHHELD:
            # A note, not reasoning text: it never joins the turn's reasoning.
            self._append(
                event_type="reasoning_withheld",
                parent_event_id=self._resolve_parent(self._parent_key(event)),
                label=str(event.get("label") or "") or None,
                input_json=self._serialize(
                    {"reasoning_tokens": int(event.get("reasoning_tokens") or 0)}),
                status="completed",
                completed_at=utc_now_iso(),
            )
            self._persist_ready()
            return

        if event_type == StreamEventType.TOOL_START:
            stream_id = str(event.get("id") or event.get("name") or uuid.uuid4())
            name = str(event.get("name") or "tool")
            parent_event_id = self._resolve_parent(self._parent_key(event))
            self._seal_reasoning(parent_event_id)
            input_value = (
                event["_raw_input"] if "_raw_input" in event else event.get("input")
            )
            input_json = self._serialize(input_value)
            self._collect_run_ids(input_json or "")
            record = self._append(
                event_type="tool_start",
                parent_event_id=parent_event_id,
                tool_call_id=stream_id,
                tool_name=name,
                label=str(event.get("label") or "") or None,
                input_json=input_json,
                status="running",
                started_at=utc_now_iso(),
            )
            self._start_by_stream_id[stream_id] = record.event_id
            self._start_by_name[name] = record.event_id
            self._persist_ready()
            return

        if event_type == StreamEventType.TOOL_END:
            output_value = (
                event["_raw_output"]
                if "_raw_output" in event
                else event.get("output")
            )
            output_text = self._output_text(output_value)
            self._collect_run_ids(output_text)
            parent_event_id = self._resolve_parent(self._parent_key(event))
            self._seal_reasoning(parent_event_id)
            if parent_event_id is None:
                self._seal_reasoning(self._tool_start_id(event))
            self._append(
                event_type="tool_end",
                parent_event_id=parent_event_id,
                tool_call_id=str(event.get("id") or "") or None,
                tool_name=str(event.get("name") or "tool"),
                label=str(event.get("label") or "") or None,
                output_text=output_text,
                status="completed",
                completed_at=utc_now_iso(),
            )
            self._persist_ready()
            return

        if event_type in {StreamEventType.FINAL_START, StreamEventType.DONE, StreamEventType.ERROR}:
            self.flush()

    def reasoning_text(self) -> str:
        return "".join(self._reasoning_parts)

    def referenced_run_ids(self) -> list[str]:
        return list(self._run_ids)

    def flush(self) -> None:
        """Persist buffered trace events with a single SQLite transaction."""
        self._open_reasoning.clear()
        self._persist_ready()

    def _persist_ready(self) -> None:
        """Persist sealed events while concurrent reasoning segments stay mutable."""
        if not self._pending:
            return
        open_ids = {record.event_id for record in self._open_reasoning.values()}
        ready = [record for record in self._pending if record.event_id not in open_ids]
        if not ready:
            return
        self.repository.append_trace_events(ready)
        ready_ids = {record.event_id for record in ready}
        self._pending = [
            record for record in self._pending if record.event_id not in ready_ids
        ]

    def _seal_reasoning(self, parent_event_id: str | None) -> None:
        keys = [
            key for key in self._open_reasoning if key[0] == parent_event_id
        ]
        for key in keys:
            self._open_reasoning.pop(key, None)

    def _tool_start_id(self, event: dict) -> str | None:
        stream_id = str(event.get("id") or "")
        if stream_id and stream_id in self._start_by_stream_id:
            return self._start_by_stream_id[stream_id]
        name = str(event.get("name") or "")
        return self._start_by_name.get(name) if name else None

    def _append(self, **values: Any) -> TraceEventRecord:
        record = TraceEventRecord(
            event_id=str(uuid.uuid4()),
            turn_id=self.turn_id,
            sequence=self._sequence,
            **values,
        )
        self._pending.append(record)
        self._sequence += 1
        return record

    def _resolve_parent(self, parent: Any) -> str | None:
        key = str(parent or "")
        if not key:
            return None
        return self._start_by_stream_id.get(key) or self._start_by_name.get(key)

    @staticmethod
    def _parent_key(event: dict) -> Any:
        return event.get("parent_id") or event.get("parent")

    @staticmethod
    def _serialize(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value)

    @classmethod
    def _output_text(cls, value: Any) -> str:
        serialized = cls._serialize(value)
        return serialized or ""

    def _collect_run_ids(self, serialized_text: str) -> None:
        try:
            payload = json.loads(serialized_text)
        except (json.JSONDecodeError, TypeError):
            return

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    # Comparisons, batches and frame derivations carry source,
                    # reference, context and plural run IDs. These are only
                    # candidates: the extractor verifies them against this
                    # project's authoritative run index before accepting evidence.
                    if key == "run_id" or key.endswith("_run_id"):
                        add(child)
                    elif key == "run_ids" or key.endswith("_run_ids"):
                        if isinstance(child, list):
                            for run_id in child:
                                add(run_id)
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
            elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
                # Worker wrappers may embed a JSON tool result as a string.
                try:
                    visit(json.loads(value))
                except (json.JSONDecodeError, TypeError, RecursionError):
                    pass

        def add(value: Any) -> None:
            if isinstance(value, str):
                run_id = value.strip()
                if run_id and run_id not in self._run_ids:
                    self._run_ids.append(run_id)

        visit(payload)
