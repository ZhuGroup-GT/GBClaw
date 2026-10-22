"""Translate LangGraph agent streams into UI-friendly SSE event dicts."""

from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator, Callable, Iterator

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph

from core.stream_events import StreamEventType, WORKER_FORWARDED_EVENTS
from extensions import tool_capability, worker_definition_for_delegate, tool_display_name
from agents.invoke import build_run_config, worker_run_config
from core.llm.reasoning import (
    reasoning_text_from_additional_kwargs,
    withheld_reasoning_tokens,
    split_message_content,
)
from agents.streaming.trace import thinking_delta
from core.settings.env import get_env_int
from project_workspace.artifacts.presentation import extract_artifact_image_paths

MAX_TOOL_OUTPUT = get_env_int("GBCLAW_MAX_TOOL_OUTPUT", 400, minimum=1)
MAX_TOOL_INPUT = get_env_int("GBCLAW_MAX_TOOL_INPUT", 280, minimum=1)
MAX_DELEGATE_OUTPUT = get_env_int("GBCLAW_MAX_DELEGATE_OUTPUT", 8000, minimum=1)
MAX_DELEGATE_INPUT = get_env_int("GBCLAW_MAX_DELEGATE_INPUT", 1200, minimum=1)

def _preserve_final_stream(tool_names: list[str] | tuple[str, ...] | set[str]) -> bool:
    names = [str(name or "").strip() for name in tool_names if str(name or "").strip()]
    return bool(names) and all((capability := tool_capability(name)) is not None and capability.preserve_final_stream
                               for name in names)


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def public_stream_event(event: dict[str, Any]) -> dict[str, Any]:
    """Return the browser/terminal-safe projection of an internal event."""
    return {
        key: value
        for key, value in event.items()
        if not str(key).startswith("_raw_")
    }


def extract_dichromatic_interactions(
    value: Any, project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Extract compact widget references before ordinary tool output is truncated.

    Geometry and arbitrary tool fields stay in the project-scoped service. The
    browser receives only enough information to fetch that authoritative record.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    found: dict[str, dict[str, Any]] = {}

    def visit(item: Any, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(item, list):
            for nested in item:
                visit(nested, depth + 1)
            return
        if not isinstance(item, dict):
            return
        if item.get("type") == "dichromatic_cell_selection":
            request_id = item.get("request_id")
            owner = item.get("project_id")
            status = item.get("status")
            if (
                isinstance(request_id, str)
                and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id)
                and isinstance(owner, str)
                and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", owner)
                and (project_id is None or owner == project_id)
                and isinstance(status, str)
                and status in {"pending", "selected", "applying", "applied", "cancelled"}
            ):
                reference = {
                    "type": "dichromatic_cell_selection",
                    "ui_action": "pick_dichromatic_cell",
                    "request_id": request_id,
                    "project_id": owner,
                    "status": status,
                    "api_path": f"/api/projects/{owner}/dichromatic-map/requests/{request_id}",
                }
                revision = item.get("revision")
                if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 1:
                    reference["revision"] = revision
                for key in ("plan_id", "step_id"):
                    if isinstance(item.get(key), str) and len(item[key]) <= 128:
                        reference[key] = item[key]
                found[request_id] = reference
        for key in ("interaction", "interactions", "result", "data"):
            if key in item:
                visit(item[key], depth + 1)

    visit(value)
    return list(found.values())


def _content_to_text(content: Any) -> tuple[str, str]:
    """Return (visible_text, thinking_text) from message content."""
    return split_message_content(content)


def _chunk_to_parts(chunk: AIMessageChunk) -> tuple[str, str]:
    text, thinking = _content_to_text(chunk.content)
    extra = chunk.additional_kwargs or {}
    if not thinking:
        thinking = reasoning_text_from_additional_kwargs(extra)
    return text, thinking


def thinking_events_from_message(
    message: AIMessage,
    streamed_thinking: str,
) -> tuple[list[dict[str, Any]], str]:
    _, full = _content_to_text(message.content)
    if not full:
        full = reasoning_text_from_additional_kwargs(message.additional_kwargs or {})
    if not full:
        tokens = withheld_reasoning_tokens(message) if not streamed_thinking else None
        if tokens is not None:
            # Distinguish "did not reason" from "reasoned, nothing disclosed".
            return [{"type": StreamEventType.THINKING_WITHHELD, "reasoning_tokens": tokens}], streamed_thinking
        return [], streamed_thinking
    delta = thinking_delta(streamed_thinking, full)
    events: list[dict[str, Any]] = []
    if delta:
        events.append({"type": StreamEventType.THINKING, "text": delta})
    return events, full


def _delegate_task(args: Any) -> str | None:
    payload = args
    if isinstance(args, str):
        try:
            payload = json.loads(args)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(payload, dict):
        return None
    task = payload.get("task")
    return str(task).strip() if task is not None else None


def _delegate_summary(output: Any) -> str | None:
    payload = output
    if isinstance(output, str):
        try:
            payload = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(payload, dict):
        return None

    candidates = [payload.get("summary")]
    result = payload.get("result")
    if isinstance(result, dict):
        candidates.append(result.get("summary"))
    candidates.append(payload.get("error"))
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    return None


def tool_input_preview(args: Any, *, tool_name: str | None = None) -> str:
    """Return the bounded input representation used by interactive clients."""
    if worker_definition_for_delegate(tool_name) is not None:
        task = _delegate_task(args)
        if task:
            return _truncate(task, MAX_DELEGATE_INPUT)
    if isinstance(args, str):
        return _truncate(args, MAX_TOOL_INPUT)
    try:
        return _truncate(json.dumps(args, ensure_ascii=False), MAX_TOOL_INPUT)
    except TypeError:
        return _truncate(str(args), MAX_TOOL_INPUT)


def tool_output_preview(output: Any, *, tool_name: str | None = None) -> str:
    """Return the bounded output representation used by interactive clients."""
    if worker_definition_for_delegate(tool_name) is not None:
        summary = _delegate_summary(output)
        if summary:
            return _truncate(summary, MAX_DELEGATE_OUTPUT)
    return _truncate(str(output or ""), MAX_TOOL_OUTPUT)


def tool_label(name: str) -> str:
    worker = worker_definition_for_delegate(name)
    if worker is not None:
        return worker.display_name or f"{worker.agent.title()} agent"
    return tool_display_name(name) or name


def _is_summarization_stream(metadata: Any) -> bool:
    if not isinstance(metadata, dict):
        return False
    if metadata.get("lc_source") in {"summarization", "agent_conclusion_index"}:
        return True
    return "SummarizationMiddleware" in str(metadata.get("langgraph_node") or "")


def _is_active_summarization_stream(metadata: Any) -> bool:
    """Return true only while the summary model is actually being invoked."""
    return isinstance(metadata, dict) and metadata.get("lc_source") == "summarization"


def _is_summarization_node(node: Any) -> bool:
    return "SummarizationMiddleware" in str(node or "")


def _is_turn_start_cleanup_node(node: Any) -> bool:
    # It re-emits earlier messages to replace them in the checkpoint; their
    # tool calls and text belong to completed turns, not to this one.
    return "TurnStartCleanupMiddleware" in str(node or "")


def _contains_compaction_update(update: Any) -> bool:
    """Detect the state replacement emitted by a completed compaction."""
    if not isinstance(update, dict):
        return False
    return any(
        getattr(message, "additional_kwargs", {}).get("lc_source")
        == "summarization"
        for message in update.get("messages", [])
    )


def _iter_tool_updates(
    update: dict[str, Any],
    *,
    active_tool_calls: set[str],
    label_for: Callable[[str], str],
    include_raw_tool_io: bool = False,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build public tool events, optionally carrying private untruncated tool I/O.

    ``input`` and ``output`` are interactive-client previews and remain bounded.
    Persistence callers may request private ``_raw_*`` fields and must remove them
    before forwarding the event outside the process boundary.
    """
    events: list[dict[str, Any]] = []
    if not isinstance(update, dict):
        return events

    for message in update.get("messages", []):
        if isinstance(message, AIMessage) and message.tool_calls:
            for tool_call in message.tool_calls:
                name = tool_call.get("name", "tool")
                tool_id = tool_call.get("id") or name
                if tool_id in active_tool_calls:
                    continue
                active_tool_calls.add(tool_id)
                raw_input = tool_call.get("args")
                event = {
                    "type": StreamEventType.TOOL_START,
                    "id": tool_id,
                    "name": name,
                    "label": label_for(name),
                    "input": tool_input_preview(raw_input, tool_name=name),
                }
                if include_raw_tool_io:
                    event["_raw_input"] = raw_input
                events.append(event)
        elif isinstance(message, ToolMessage):
            name = message.name or "tool"
            tool_id = getattr(message, "tool_call_id", None) or name
            text, _ = _content_to_text(message.content)
            event = {
                "type": StreamEventType.TOOL_END,
                "id": tool_id,
                "name": name,
                "label": label_for(name),
                "output": tool_output_preview(text, tool_name=name),
                "image_paths": extract_artifact_image_paths(text, project_id),
            }
            interactions = extract_dichromatic_interactions(text, project_id)
            if interactions:
                event["interactions"] = interactions
            status = getattr(message, "status", None)
            if status:
                event["status"] = str(status)
            if include_raw_tool_io:
                event["_raw_output"] = text
            events.append(event)
    return events


def extract_tool_trace_from_messages(
    messages: list[Any],
    *,
    max_output: int | None = None,
) -> list[dict[str, Any]]:
    """Build ordered tool-call records from a LangGraph message list."""
    pending: dict[str, dict[str, Any]] = {}
    trace: list[dict[str, Any]] = []

    for message in messages:
        if isinstance(message, AIMessage) and message.tool_calls:
            for tool_call in message.tool_calls:
                tool_id = tool_call.get("id") or tool_call.get("name") or "tool"
                name = tool_call.get("name", "tool")
                pending[tool_id] = {
                    "type": "tool_call",
                    "id": tool_id,
                    "name": name,
                    "label": tool_label(name),
                    "input": tool_call.get("args"),
                }
        elif isinstance(message, ToolMessage):
            tool_id = getattr(message, "tool_call_id", None) or message.name or "tool"
            name = message.name or "tool"
            text, _ = _content_to_text(message.content)
            if max_output is not None:
                text = _truncate(text, max_output)
            start = pending.pop(tool_id, {})
            trace.append(
                {
                    "type": "tool_call",
                    "id": tool_id,
                    "name": name,
                    "label": start.get("label") or tool_label(name),
                    "input": start.get("input"),
                    "output": text,
                }
            )
        elif isinstance(message, HumanMessage) and message.additional_kwargs.get("compacted_tool_trace"):
            # A worker summary carries the records of the tool calls it replaced.
            for item in message.additional_kwargs["compacted_tool_trace"]:
                record = dict(item)
                if max_output is not None and isinstance(record.get("output"), str):
                    record["output"] = _truncate(record["output"], max_output)
                trace.append(record)

    return trace


def iter_worker_tool_events(
    graph: CompiledStateGraph,
    user_input: str,
    *,
    thread_id: str,
    project_id: str | None = None,
    chat_thread_id: str | None = None,
    status_message: str = "Worker running…",
    include_raw_tool_io: bool = False,
    recursion_limit: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield tool/status/thinking events plus the worker's final-answer stream.

    ``include_raw_tool_io`` is for in-process persistence consumers. Private
    ``_raw_*`` fields must be removed before an event reaches an interactive
    client.
    """
    active_tool_calls: set[str] = set()
    streamed_thinking = ""
    streamed_result = ""

    yield {"type": StreamEventType.STATUS, "message": status_message}

    config = worker_run_config(
        thread_id=thread_id, project_id=project_id, chat_thread_id=chat_thread_id,
        recursion_limit=recursion_limit,
    )

    for mode, data in graph.stream(
        {"messages": [HumanMessage(content=user_input)]},
        config=config,
        stream_mode=["updates", "messages"],
    ):
        if mode == "messages":
            chunk, _meta = data
            if _is_summarization_stream(_meta):
                continue
            if not isinstance(chunk, AIMessageChunk):
                continue
            text, thinking = _chunk_to_parts(chunk)
            if thinking:
                streamed_thinking += thinking
                yield {"type": StreamEventType.THINKING, "text": thinking}
            if chunk.tool_call_chunks:
                if streamed_result:
                    streamed_result = ""
                    yield {"type": StreamEventType.AGENT_RESULT_RESET}
                # A model turn that calls a tool is not the worker's conclusion.
                # Text that preceded the first tool-call delta was provisional.
                continue
            if text:
                streamed_result += text
                yield {"type": StreamEventType.AGENT_RESULT_DELTA, "text": text}
            continue

        if mode != "updates":
            continue
        for _node, update in data.items():
            if _is_summarization_node(_node) or _is_turn_start_cleanup_node(_node):
                continue
            if isinstance(update, dict):
                for message in update.get("messages", []):
                    if isinstance(message, AIMessage):
                        events, streamed_thinking = thinking_events_from_message(
                            message, streamed_thinking
                        )
                        for event in events:
                            yield event
                        if message.tool_calls:
                            if streamed_result:
                                streamed_result = ""
                                yield {"type": StreamEventType.AGENT_RESULT_RESET}
                        else:
                            visible, _ = _content_to_text(message.content)
                            if visible.startswith(streamed_result):
                                delta = visible[len(streamed_result) :]
                            elif visible != streamed_result:
                                if streamed_result:
                                    yield {"type": StreamEventType.AGENT_RESULT_RESET}
                                streamed_result = ""
                                delta = visible
                            else:
                                delta = ""
                            if delta:
                                streamed_result += delta
                                yield {"type": StreamEventType.AGENT_RESULT_DELTA, "text": delta}
            for event in _iter_tool_updates(
                update,
                active_tool_calls=active_tool_calls,
                label_for=tool_label,
                include_raw_tool_io=include_raw_tool_io,
                project_id=project_id,
            ):
                if event["type"] == StreamEventType.TOOL_START:
                    streamed_thinking = ""
                    if streamed_result:
                        streamed_result = ""
                        yield {"type": StreamEventType.AGENT_RESULT_RESET}
                yield event


async def iter_chat_events(
    graph: CompiledStateGraph,
    user_input: str,
    *,
    thread_id: str,
    project_id: str | None = None,
    run_name: str = "gbclaw_chat",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    context: Any | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield structured events for the chat SSE endpoint."""
    had_tool_calls = False
    allow_final_stream = False
    final_started = False
    final_parts: list[str] = []
    pending_final: list[str] = []
    active_tool_calls: set[str] = set()
    streamed_thinking = ""
    current_model_reply = ""
    compaction_announced = False
    # While a top-level delegate tool is running, suppress unlabeled thinking
    # from the parent messages stream (worker thinking arrives via custom
    # events already tagged with their delegate tool-call parent_id).
    open_top_level_tools = 0

    def _flush_pending() -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        nonlocal final_started, allow_final_stream
        if not pending_final:
            return events
        allow_final_stream = True
        if not final_started:
            final_started = True
            events.append({"type": StreamEventType.FINAL_START})
        for piece in pending_final:
            final_parts.append(piece)
            events.append({"type": StreamEventType.TOKEN, "text": piece})
        pending_final.clear()
        return events

    yield {"type": StreamEventType.STATUS, "message": "Planning your request…"}

    async for mode, data in graph.astream(
        {"messages": [HumanMessage(content=user_input)]},
        config=build_run_config(
            thread_id=thread_id,
            project_id=project_id,
            run_name=run_name,
            tags=tags,
            metadata=metadata,
        ),
        context=context,
        stream_mode=["updates", "messages", "custom"],
    ):
        if mode == "custom":
            if isinstance(data, dict) and data.get("type") in WORKER_FORWARDED_EVENTS:
                yield data
            continue

        if mode == "updates":
            for _node, update in data.items():
                if _is_turn_start_cleanup_node(_node):
                    continue
                if _is_summarization_node(_node):
                    if _contains_compaction_update(update) and not compaction_announced:
                        compaction_announced = True
                        yield {
                            "type": StreamEventType.STATUS,
                            "message": "Compressing conversation context…",
                        }
                    continue
                if isinstance(update, dict) and open_top_level_tools == 0:
                    for message in update.get("messages", []):
                        if isinstance(message, AIMessage):
                            events, streamed_thinking = thinking_events_from_message(
                                message, streamed_thinking
                            )
                            for event in events:
                                yield event
                for event in _iter_tool_updates(
                    update,
                    active_tool_calls=active_tool_calls,
                    label_for=tool_label,
                    include_raw_tool_io=True,
                    project_id=project_id,
                ):
                    if event["type"] == StreamEventType.TOOL_START:
                        had_tool_calls = True
                        allow_final_stream = False
                        preserve = _preserve_final_stream([str(event.get("name") or "")])
                        if not preserve:
                            final_parts.clear()
                            pending_final.clear()
                            if final_started:
                                final_started = False
                                yield {"type": StreamEventType.RESET_STREAM}
                        else:
                            # Keep already-streamed deliverable; ignore tokens until tool ends.
                            pending_final.clear()
                        if not (event.get("parent_id") or event.get("parent")):
                            streamed_thinking = ""
                            open_top_level_tools += 1
                    elif event["type"] == StreamEventType.TOOL_END:
                        allow_final_stream = True
                        pending_final.clear()
                        yield event
                        if not (event.get("parent_id") or event.get("parent")):
                            open_top_level_tools = max(0, open_top_level_tools - 1)
                            yield {"type": StreamEventType.STATUS, "message": "Writing final answer…"}
                        continue
                    yield event
                if not isinstance(update, dict):
                    continue
                for message in update.get("messages", []):
                    if (
                        str(_node) == "model"
                        and isinstance(message, AIMessage)
                        and not message.tool_calls
                    ):
                        candidate, _ = _content_to_text(message.content)
                        if candidate.strip():
                            current_model_reply = candidate.strip()
                    if (
                        isinstance(message, AIMessage)
                        and not message.tool_calls
                        and not had_tool_calls
                        and pending_final
                    ):
                        for pending_event in _flush_pending():
                            yield pending_event

        elif mode == "messages":
            chunk, _meta = data
            if _is_summarization_stream(_meta):
                if (
                    _is_active_summarization_stream(_meta)
                    and not compaction_announced
                ):
                    compaction_announced = True
                    yield {
                        "type": StreamEventType.STATUS,
                        "message": "Compressing conversation context…",
                    }
                continue
            if not isinstance(chunk, AIMessageChunk):
                continue

            text, thinking = _chunk_to_parts(chunk)
            if thinking and open_top_level_tools == 0:
                streamed_thinking += thinking
                yield {"type": StreamEventType.THINKING, "text": thinking}

            if chunk.tool_call_chunks:
                had_tool_calls = True
                allow_final_stream = False
                chunk_names = [
                    str(tool_chunk.get("name") or "").strip()
                    for tool_chunk in chunk.tool_call_chunks
                    if str(tool_chunk.get("name") or "").strip()
                ]
                # Named non-bookkeeping tools discard the prior final stream.
                # Unknown names (first delta) keep existing tokens until named.
                if chunk_names and not _preserve_final_stream(chunk_names):
                    final_parts.clear()
                    pending_final.clear()
                    if final_started:
                        final_started = False
                        yield {"type": StreamEventType.RESET_STREAM}
                else:
                    pending_final.clear()
                for tool_chunk in chunk.tool_call_chunks:
                    name = tool_chunk.get("name")
                    if name:
                        yield {
                            "type": StreamEventType.TOOL_PROGRESS,
                            "id": tool_chunk.get("id") or "",
                            "name": name,
                            "label": tool_label(name),
                        }

            if text:
                if allow_final_stream or not had_tool_calls:
                    if not final_started:
                        final_started = True
                        yield {"type": StreamEventType.FINAL_START}
                    final_parts.append(text)
                    yield {"type": StreamEventType.TOKEN, "text": text}
                else:
                    pending_final.append(text)

    for pending_event in _flush_pending():
        yield pending_event

    reply = "".join(final_parts).strip()
    if not reply:
        reply = current_model_reply

    if not reply:
        yield {"type": StreamEventType.ERROR, "message": "Agent returned an empty reply"}
        return

    yield {"type": StreamEventType.DONE, "reply": reply, "thread_id": thread_id}
