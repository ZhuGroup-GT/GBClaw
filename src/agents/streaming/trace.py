from __future__ import annotations

from typing import Any

from core.stream_events import StreamEventType


def thinking_delta(streamed: str, full: str) -> str:
    if not full:
        return ""
    if full.startswith(streamed):
        return full[len(streamed) :]
    return full


class TraceSegmentAccumulator:
    """Build ordered reasoning/tool/agent-result segments from SSE events."""

    def __init__(self) -> None:
        self._segments: list[dict[str, Any]] = []
        self._tool_by_id: dict[str, dict[str, Any]] = {}
        self._open_reasoning: dict[str, dict[str, Any]] = {}
        self._agent_result_by_parent: dict[str, dict[str, Any]] = {}

    def apply_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == StreamEventType.THINKING:
            text = event.get("text") or ""
            if not text:
                return
            parent_id = event.get("parent_id") or event.get("parent") or ""
            parent_name = event.get("parent_name") or event.get("parent") or ""
            open_reasoning = self._open_reasoning.get(parent_id)
            if open_reasoning is not None:
                open_reasoning["text"] += text
            else:
                open_reasoning = {
                    "kind": "reasoning",
                    "text": text,
                    "parent_id": parent_id,
                    "parent_name": parent_name,
                    "depth": 1 if parent_id else 0,
                }
                self._open_reasoning[parent_id] = open_reasoning
                self._segments.append(open_reasoning)
            return

        if event_type == StreamEventType.THINKING_WITHHELD:
            parent_id = event.get("parent_id") or event.get("parent") or ""
            self._open_reasoning.pop(parent_id, None)
            self._segments.append({
                "kind": "reasoning_withheld",
                "reasoning_tokens": int(event.get("reasoning_tokens") or 0),
                "parent_id": parent_id,
                "parent_name": event.get("parent_name") or event.get("parent") or "",
                "depth": 1 if parent_id else 0,
            })
            return

        if event_type == StreamEventType.AGENT_RESULT_DELTA:
            text = event.get("text") or ""
            if not text:
                return
            parent_id = event.get("parent_id") or event.get("parent") or ""
            parent_name = event.get("parent_name") or event.get("parent") or ""
            result = self._agent_result_by_parent.get(parent_id)
            if result is None:
                result = {
                    "kind": "agent_result",
                    "text": "",
                    "parent_id": parent_id,
                    "parent_name": parent_name,
                    "depth": 1 if parent_id else 0,
                    "streaming": True,
                }
                self._agent_result_by_parent[parent_id] = result
                self._segments.append(result)
            result["text"] += text
            result["streaming"] = True
            return

        if event_type == StreamEventType.AGENT_RESULT_RESET:
            parent_id = event.get("parent_id") or event.get("parent") or ""
            result = self._agent_result_by_parent.pop(parent_id, None)
            if result is not None:
                self._segments = [item for item in self._segments if item is not result]
            return

        if event_type == StreamEventType.TOOL_START:
            parent_id = event.get("parent_id") or event.get("parent") or ""
            parent_name = event.get("parent_name") or event.get("parent") or ""
            self._open_reasoning.pop(parent_id, None)
            tool_id = event.get("id") or event.get("name") or "tool"
            seg = {
                "kind": "tool",
                "id": tool_id,
                "name": event.get("name") or "tool",
                "label": event.get("label") or "",
                "input": event.get("input") or "",
                "output": "",
                "depth": 1 if parent_id else 0,
                "parent_id": parent_id,
                "parent_name": parent_name,
            }
            self._tool_by_id[tool_id] = seg
            self._segments.append(seg)
            return

        if event_type == StreamEventType.TOOL_END:
            parent_id = event.get("parent_id") or event.get("parent") or ""
            parent_name = event.get("parent_name") or event.get("parent") or ""
            self._open_reasoning.pop(parent_id, None)
            if not parent_id:
                self._open_reasoning.pop(event.get("id") or event.get("name") or "", None)
                self._open_reasoning.pop(event.get("name") or "", None)
            tool_id = event.get("id")
            seg = self._tool_by_id.get(tool_id) if tool_id else None
            if seg is None and tool_id:
                # late tool_end without start — still record
                seg = {
                    "kind": "tool",
                    "id": tool_id,
                    "name": event.get("name") or "tool",
                    "label": event.get("label") or "",
                    "input": "",
                    "output": "",
                    "depth": 1 if parent_id else 0,
                    "parent_id": parent_id,
                    "parent_name": parent_name,
                }
                self._tool_by_id[tool_id] = seg
                self._segments.append(seg)
            if seg is not None:
                seg["output"] = event.get("output") or ""
                if str(seg.get("name") or "").startswith("delegate_"):
                    result_parent = str(seg.get("id") or "")
                    result = self._agent_result_by_parent.get(result_parent)
                    output = event.get("output") or ""
                    if result is None and output:
                        result = {
                            "kind": "agent_result",
                            "text": output,
                            "parent_id": result_parent,
                            "parent_name": seg.get("name") or "",
                            "depth": 1,
                            "streaming": False,
                        }
                        self._agent_result_by_parent[result_parent] = result
                        self._segments.append(result)
                    elif result is not None:
                        if output:
                            result["text"] = output
                        result["streaming"] = False

    def segments(self) -> list[dict[str, Any]]:
        return [dict(s) for s in self._segments]

    def to_assistant_extra(self) -> dict[str, Any] | None:
        segs = self.segments()
        if not segs:
            return None
        return {"trace": segs}
