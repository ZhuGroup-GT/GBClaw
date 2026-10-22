"""Loss-aware request projections; never edit scientific records or raw messages."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Sequence

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from agents.memory import decisions


_REFERENCE_KEYS = frozenset({
    "run_id", "source_run_id", "source_run_ids", "run_ids", "result_run_ids",
    "fact_id", "fact_ids", "lookup_key", "lookup_keys", "memory_fact_refs",
    "conclusion_id", "conclusion_lookup_key", "conclusion_fact_id", "conclusion_ref",
    "task_state_ref", "artifact_id", "artifact_ids", "source_artifact_id",
    "entity_id", "entity_type", "reference", "output_id", "version", "memory_version",
    "manifest", "full_output", "report", "read", "path", "image_paths",
    "plan_id", "draft_id", "step_id", "request_id", "interaction_request",
})
_OPAQUE_PROCESS_KEYS = frozenset({"trace", "messages", "history", "reasoning", "reasoning_content"})
_IDENTITY_KEYS = _REFERENCE_KEYS - {"version", "memory_version", "entity_type", "reference",
                                  "conclusion_ref", "task_state_ref", "full_output", "report", "read"}


class _ArchivedDecisionRequiresUser(Exception):
    """Internal signal: even a short reference index cannot replace this result."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _verified_archive_card(
    message: ToolMessage, payload: dict, *, archive_reader: Callable[[str], dict] | None,
    max_chars: int,
) -> ToolMessage | None:
    """Replace a result with its archive only when that archive covers this view.

    The host reader must verify the content hash and current project/invocation
    access, as ToolOutputStore.read and the scoped output tool do. A valid ID
    alone is insufficient: an unrelated existing archive must not license
    discarding this result. Check either the original bytes or the deterministic
    host projection, including its entire typed worker outcome.
    """
    if archive_reader is None:
        return None
    reference = payload.get("full_output")
    if not (isinstance(reference, dict) and reference.get("tool") == "read_tool_output"
            and isinstance(reference.get("output_id"), str)
            and re.fullmatch(r"[0-9a-f]{64}", reference["output_id"])):
        return None
    try:
        original = archive_reader(reference["output_id"])
        if not isinstance(original, dict) or original.get("tool_name") != message.name:
            return None
        content = original["content"]
        archived = json.loads(content) if isinstance(content, str) else content
        if not isinstance(archived, dict):
            return None
        archived_message = message.model_copy(update={"content": _json(archived)})
        if decisions.result_requires_user(archived_message):
            raise _ArchivedDecisionRequiresUser
        if archived != payload:
            from agents.memory.projection import compact_tool_payload

            expected = compact_tool_payload(archived, tool_name=message.name or "")
            expected["full_output"] = reference
            if expected != payload:
                return None
        # Match what read_tool_output actually exposes, including its refusal
        # to expose legacy conversation-only delegate archives.
        from agents.memory.tools.output import _agent_visible_content

        _agent_visible_content(original)
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return None

    output_id = reference["output_id"]
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    status = result.get("status", payload.get("status", result.get("outcome")))
    card = {
        "context_result_card": True,
        "tool": message.name,
        "full_output": {"tool": "read_tool_output", "output_id": output_id},
        "reference_index_complete": False,
        "recover": {"mode": "read_only", "tool": "read_tool_output",
                    "args": {"output_id": output_id}},
        "note": "Read the archived result and its reference index before drawing conclusions "
                "or repeating work. This card is a preview, not the complete result.",
    }
    if isinstance(status, str) and len(status) <= 100:
        card["status"] = status
    worker_outcome = payload.get("worker_outcome")
    if isinstance(worker_outcome, dict) and isinstance(worker_outcome.get("status"), str):
        card["worker_status"] = worker_outcome["status"][:100]
    # Optional prose must never displace the verified recovery pointer.
    summary = result.get("summary") or payload.get("summary")
    if isinstance(summary, str) and summary.strip():
        with_summary = {**card, "summary_preview": summary[:600]}
        if len(_json(with_summary)) <= max_chars:
            card = with_summary
    error = result.get("error") or payload.get("error")
    if error:
        with_error = {**card, "error_preview": str(error)[:300]}
        if len(_json(with_error)) <= max_chars:
            card = with_error
    content = _json(card)
    if len(content) > max_chars or len(content) >= len(message.content):
        return None
    return message.model_copy(update={
        "content": content,
        "artifact": None,
        "response_metadata": {**message.response_metadata,
                              "context_editing": {"strategy": "verified_archive_card"}},
    })


def result_reference_card(
    message: ToolMessage, *, max_chars: int = 3000,
    archive_reader: Callable[[str], dict] | None = None, prefer_archive: bool = False,
) -> ToolMessage:
    """Preserve stable references and a short status instead of '[result cleared]'.

    An oversized reference index may use a verified, readable archive instead.
    Without such an archive, leave the original intact. Never silently erase
    decisions or present a partial reference index as a complete result.
    """
    if not isinstance(message.content, str) or decisions.result_requires_user(message):
        return message
    try:
        payload = json.loads(message.content)
    except (ValueError, TypeError):
        return message
    if not isinstance(payload, dict):
        return message
    if payload.get("context_result_card") is True:
        return message
    archive_card = None
    if archive_reader is not None:
        try:
            archive_card = _verified_archive_card(
                message, payload, archive_reader=archive_reader, max_chars=max_chars,
            )
        except _ArchivedDecisionRequiresUser:
            return message
    if prefer_archive:
        if archive_card is not None:
            return archive_card

    references: list[dict[str, Any]] = []
    seen = set()
    nodes = 0

    def visit(value):
        nonlocal nodes
        nodes += 1
        if nodes > 5000:
            raise ValueError("Reference scan exceeds its bounded input")
        if isinstance(value, dict):
            row = {key: item for key, item in value.items()
                   if key in _REFERENCE_KEYS and item not in (None, "", [], {})}
            if row:
                encoded = _json(row)
                if encoded not in seen:
                    references.append(row)
                    seen.add(encoded)
            for key, item in value.items():
                if key not in _REFERENCE_KEYS and key not in _OPAQUE_PROCESS_KEYS:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    try:
        visit(payload)
    except ValueError:
        return message
    def has_identity(value):
        if isinstance(value, dict):
            return (any(key in _IDENTITY_KEYS and item not in (None, "", [], {})
                        for key, item in value.items())
                    or any(has_identity(item) for item in value.values()))
        return isinstance(value, list) and any(has_identity(item) for item in value)

    if not references or not has_identity(references):
        return message
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    status = result.get("status", payload.get("status", result.get("outcome")))
    summary = result.get("summary") or payload.get("summary")
    card = {"context_result_card": True, "tool": message.name,
            "status": status, "references": references}
    if isinstance(summary, str) and summary.strip():
        card["summary_preview"] = summary[:600]
    if result.get("error") or payload.get("error"):
        card["error_preview"] = str(result.get("error") or payload["error"])[:300]
    content = _json(card)
    if len(content) > max_chars or len(content) >= len(message.content):
        return archive_card or message
    return message.model_copy(update={
        "content": content,
        "artifact": None,
        "response_metadata": {**message.response_metadata,
                              "context_editing": {"strategy": "reference_card"}},
    })


def project_old_result_cards(
    messages: Sequence[BaseMessage], *, count_tokens: Callable,
    trigger: int, keep: int = 4, exclude_tools=frozenset(), aggressive: bool = False,
    archive_reader: Callable[[str], dict] | None = None,
) -> list[BaseMessage]:
    """Compact eligible old results only in the model request copy."""
    projected = list(messages)
    if not aggressive and count_tokens(projected) <= trigger:
        return projected
    call_names = {call["id"]: call["name"]
                  for message in messages if isinstance(message, AIMessage)
                  for call in message.tool_calls}
    candidates = [index for index, message in enumerate(messages)
                  if isinstance(message, ToolMessage)
                  and (message.name or call_names.get(message.tool_call_id)) not in exclude_tools]
    for index in candidates[:-keep] if keep else candidates:
        original = messages[index]
        named = (original if original.name else original.model_copy(update={
            "name": call_names.get(original.tool_call_id),
        }))
        card = result_reference_card(
            named, archive_reader=archive_reader, prefer_archive=aggressive,
        )
        projected[index] = original if card is named else card
    return projected
