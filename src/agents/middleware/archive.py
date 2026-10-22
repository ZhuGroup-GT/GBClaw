"""Make consumed tool results readable before replacing old dialogue with a handoff."""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from .projection import _runtime_project_id, _tool_archive_reader
from agents.memory.decisions import result_requires_user

if TYPE_CHECKING:
    from project_workspace.store import ProjectStore


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _parsed(content: Any) -> Any:
    if not isinstance(content, str):
        return content
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        return content


def _requires_user(message: ToolMessage) -> bool:
    normalized = message if isinstance(message.content, str) else message.model_copy(update={"content": _json(message.content)})
    return result_requires_user(normalized)


def _existing_reference(source: ToolMessage, view: ToolMessage, *, tool_name: str, reader) -> dict | None:
    """Keep a real readable pointer only when it describes this source result."""
    from agents.memory.projection import compact_tool_payload
    from agents.memory.tools.output import _agent_visible_content

    source_payload = _parsed(source.content)
    view_payload = _parsed(view.content)
    visible_source = _parsed(_agent_visible_content({"tool_name": tool_name, "content": source.content}))
    candidates = [value.get("full_output") for value in (source_payload, view_payload)
                  if isinstance(value, dict)]
    for reference in candidates:
        if not (isinstance(reference, dict) and reference.get("tool") == "read_tool_output"
                and isinstance(reference.get("output_id"), str)
                and re.fullmatch(r"[0-9a-f]{64}", reference["output_id"])):
            continue
        try:
            stored = reader(reference["output_id"])
            if stored.get("tool_name") != tool_name:
                continue
            archived = _parsed(_agent_visible_content(stored))
            if _requires_user(source.model_copy(update={"content": _json(archived)})):
                continue
            covered = archived == visible_source
            if isinstance(visible_source, dict):
                source_without_reference = {key: value for key, value in visible_source.items()
                                            if key != "full_output"}
                covered |= archived == source_without_reference
            if not covered and isinstance(archived, dict) and isinstance(source_payload, dict):
                # Old checkpoints may already contain a host projection. An
                # unrelated valid archive still must not license that result.
                projected = compact_tool_payload(archived, tool_name=tool_name)
                projected["full_output"] = reference
                covered = projected == source_payload
            if not covered and isinstance(source_payload, dict) and isinstance(stored["content"], str):
                start = source_payload.get("offset_chars")
                end = source_payload.get("end_chars")
                covered = (
                    source_payload.get("format") == "serialized_json_page"
                    and source_payload.get("snapshot_id") == reference["output_id"]
                    and isinstance(start, int) and isinstance(end, int)
                    and 0 <= start <= end <= len(stored["content"])
                    and source_payload.get("content") == stored["content"][start:end]
                )
            if covered:
                return reference
        except (OSError, ValueError, TypeError, KeyError, IndexError):
            continue
    return None


def archive_summary_inputs(
    messages: Sequence[BaseMessage], *, original_messages: Sequence[BaseMessage],
    runtime, project_store: ProjectStore | None = None, current_invocation_only: bool = False,
) -> list[BaseMessage]:
    """Add verified recovery pointers to copies of consumable tool results.

    Unlike ordinary model projection this also covers small outputs and detail
    readers. Archive complete originals by message identity, never projected
    cards. Delegate archives contain only the existing agent-visible view.
    Pending choices, unavailable storage, or missing originals leave the input
    message intact. This function does not authorize consuming those messages.
    """
    from project_memory.storage.tool_outputs import ToolOutputStore
    from agents.memory.tools.output import _agent_visible_content, invocation_thread_id
    from project_workspace.store import ProjectStore

    project_id = _runtime_project_id(runtime)
    if not project_id:
        return list(messages)
    try:
        store = project_store or ProjectStore()
        outputs = ToolOutputStore(store.project_path(project_id))
        reader = _tool_archive_reader(runtime, store, current_invocation_only=current_invocation_only)
        thread_id = invocation_thread_id()
    except (OSError, ValueError, TypeError):
        return list(messages)
    if reader is None or current_invocation_only and not thread_id:
        return list(messages)

    originals = {message.id: message for message in original_messages
                 if isinstance(message, ToolMessage) and message.id}
    names = {call["id"]: call["name"] for message in original_messages if isinstance(message, AIMessage)
             for call in message.tool_calls}
    output = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            output.append(message)
            continue
        source = originals.get(message.id)
        if source is None and any(message is original for original in original_messages):
            source = message
        tool_name = (source.name or names.get(source.tool_call_id, "")) if source is not None else ""
        if (source is None or source.tool_call_id != message.tool_call_id or not tool_name
                or message.name and message.name != tool_name
                or _requires_user(source) or _requires_user(message)):
            output.append(message)
            continue
        try:
            reference = _existing_reference(source, message, tool_name=tool_name, reader=reader)
            if reference is None:
                source_payload = _parsed(source.content)
                # A prior projected checkpoint is not an original. Retain it
                # unchanged rather than storing a card as scientific evidence.
                if isinstance(source_payload, dict) and (
                    source_payload.get("context_result_card") is True
                    or source_payload.get("session_input_view") is True
                    or isinstance(source_payload.get("model_output"), dict)
                    and source_payload["model_output"].get("mode") == "conclusions_and_references"
                ):
                    output.append(message)
                    continue
                visible = _agent_visible_content({"tool_name": tool_name, "content": source.content})
                content = visible if isinstance(visible, (str, list)) else _json(visible)
                reference = outputs.write(content, tool_name=tool_name, scope_thread_id=thread_id)
                # Check the actual durable bytes and the current worker scope;
                # a successful write call alone is not a readable guarantee.
                saved = reader(reference["output_id"])
                if saved.get("tool_name") != tool_name or _parsed(saved["content"]) != _parsed(content):
                    raise ValueError("The written summary input archive does not match its source")
            payload = _parsed(message.content)
            payload = dict(payload) if isinstance(payload, dict) else {"content": message.content}
            payload["full_output"] = reference
            output.append(message.model_copy(update={"name": tool_name, "content": _json(payload)}))
        except (OSError, ValueError, TypeError, KeyError, IndexError):
            output.append(message)
    return output


__all__ = ["archive_summary_inputs"]
