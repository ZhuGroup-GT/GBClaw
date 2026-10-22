"""Bounded conversation continuity, separate from durable scientific memory.

These pure functions prepare only the summarizer's input view. They never
replace checkpoint messages, publish facts, read storage, or invent references.
The caller commits a replacement summary only after validation succeeds.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage, get_buffer_string

from agents.memory.decisions import result_requires_user


SESSION_SUMMARY_TARGET_CHARS = 2_000
SESSION_SUMMARY_MAX_CHARS = 4_000
SESSION_SUMMARY_MAX_REFERENCES = 3
SESSION_SUMMARY_HEADINGS = (
    "## CURRENT FOCUS",
    "## ACTIVE CONSTRAINTS",
    "## OPEN ITEMS",
    "## RETRIEVAL REFERENCES",
)

SESSION_SUMMARY_PROMPT = """<role>
Maintain a small conversation handoff for GBclaw. Its only purpose is to make the
next reply coherent with the user's current request. This is session context,
not project summary, scientific evidence, or a durable memory write.
</role>

<requirements>
- Rewrite the handoff around the CURRENT task; do not append a history of tasks.
- Keep the current user intent and every still-active user constraint, preference,
  or decision needed to continue. Preserve exact values when they are active
  instructions (for example a requested loading increment), not measured results.
- Keep unresolved questions, pending approvals, and the immediate next action.
  Never infer approval, resolve an unanswered choice, or treat model claims as facts.
- Tool statuses can establish that work was attempted or completed. Mention that
  briefly only when it affects the next action or avoids repeating completed work.
- Assistant tool-call arguments are intentionally omitted from this input view.
  Empty argument objects are placeholders, not the original execution parameters.
  Retrieve execution details from saved run/output references when available;
  never guess missing arguments or treat a tool's parameters as user confirmation.
- Do not copy scientific findings, measured values, curve explanations, completed
  work inventories, run provenance chains, or artifact lists. Those belong in
  project memory and saved outputs. Read them with tools when the user needs them.
- Keep at most three exact retrieval references that matter to the CURRENT task.
  Prefer a conclusion_id, a run_id, an exact fact lookup_key, or a read_tool_output
  output_id already present in the source. Never fabricate IDs, paths, or indices.
  References are navigation hints; retrieve the evidence before making claims.
- When the input contains an older session summary, use its still-active user
  constraints and unresolved items, but rewrite it into this compact format.
  Do not preserve its old scientific-results or completed-work sections.
- The CURRENT RETAINED CONTEXT, when provided, contains messages that remain
  verbatim after this handoff. Use it to recognize the user's present focus,
  explicit changes, resolved approvals, and answered questions. Do not revive
  old proposals or decisions that those messages have resolved. Do not copy its
  detailed result explanations into the handoff. Do not discard an earlier
  still-active constraint merely because it is absent from the newest request.
- Preserve source uncertainty. Do not add hidden reasoning, narration about
  summarization, obsolete constraints, or explanations of work already finished.
- Use the user's language. Use brief statements; use 'None' for an empty section.
</requirements>

<output_format>
Return only Markdown with exactly these headings, in this order:
## CURRENT FOCUS
## ACTIVE CONSTRAINTS
## OPEN ITEMS
## RETRIEVAL REFERENCES
Use at most three nonempty lines in RETRIEVAL REFERENCES, one reference per line.
</output_format>

<messages>
{messages}
</messages>
"""

_PENDING_STATUSES = frozenset({
    "awaiting_approval", "awaiting_selection", "pending_user_decision", "awaiting_user",
})
_REFERENCE_CONTAINERS = (
    "full_output", "recover", "args", "conclusion_ref", "task_state_ref", "worker_outcome",
    "result", "data", "references", "evidence_refs", "memory_fact_refs", "runs", "records",
)
_REFERENCE_FIELDS = (
    "conclusion_id", "conclusion_lookup_key", "lookup_key", "run_id", "source_run_id",
)
_STATUSES = frozenset({
    "ok", "error", "completed", "reused", "partial", "failed", "cancelled", "interrupted",
    "running", "queued", "pending", "pending_approval", *_PENDING_STATUSES,
})


class SessionSummaryValidationError(ValueError):
    """Content-only error; the middleware supplies checkpoint recovery behavior."""

    def __init__(self, message: str, *, code: str, diagnostics: dict | None = None):
        super().__init__(message)
        self.code = code
        self.diagnostics = diagnostics or {}


def validate_session_summary(summary: str, *, max_chars: int = SESSION_SUMMARY_MAX_CHARS) -> str:
    """Reject malformed or bloated output without slicing any user constraints."""
    text = str(summary or "").strip()
    if not text:
        raise SessionSummaryValidationError(
            "The summary model returned no summary.", code="summary_empty_response",
        )
    if text.startswith("Error generating summary:"):
        raise SessionSummaryValidationError(
            "The summary service returned an error response.", code="summary_provider_error_response",
        )
    matches = list(re.finditer(r"^#{1,6}[^\S\r\n]+[^\r\n]+", text, flags=re.MULTILINE))
    headings = [match.group() for match in matches]
    if tuple(headings) != SESSION_SUMMARY_HEADINGS or not text.startswith(SESSION_SUMMARY_HEADINGS[0]):
        raise SessionSummaryValidationError(
            "Session context must use the four conversation continuity sections.",
            code="summary_invalid_structure",
            diagnostics={"missing_heading_count": sum(
                heading not in headings for heading in SESSION_SUMMARY_HEADINGS)},
        )
    bodies = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index < 3 else len(text)
        bodies.append(text[start:end].strip())
    if any(not body for body in bodies):
        raise SessionSummaryValidationError(
            "Every conversation continuity section must contain text or None.",
            code="summary_invalid_structure",
        )
    reference_lines = [line for line in bodies[-1].splitlines() if line.strip()]
    if len(reference_lines) > SESSION_SUMMARY_MAX_REFERENCES:
        raise SessionSummaryValidationError(
            "A session handoff must not become an inventory of historical results.",
            code="summary_invalid_structure",
            diagnostics={"retrieval_reference_lines": len(reference_lines)},
        )
    limit = min(SESSION_SUMMARY_MAX_CHARS, max_chars)
    if len(text) > limit:
        raise SessionSummaryValidationError(
            "Session context exceeded its fixed character limit.",
            code="summary_output_exceeds_budget",
            diagnostics={"summary_output_chars": len(text), "summary_character_limit": limit},
        )
    return text


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 240 and not any(c.isspace() for c in value)


def _references(payload: dict) -> list[dict[str, str]]:
    """Copy a few explicit navigation identities, never guess from result prose."""
    references: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(key: str, value: Any) -> None:
        if _identifier(value) and (key, value) not in seen:
            references.append({key: value})
            seen.add((key, value))

    # The archival pointer covers all result details and is preferred over an
    # arbitrary subset of a large run/artifact inventory. No storage claim is
    # made here: the read tool remains responsible for access/hash verification.
    full_output = payload.get("full_output")
    if isinstance(full_output, dict) and full_output.get("tool") == "read_tool_output":
        value = full_output.get("output_id")
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
            add("output_id", value)

    pending: list[Any] = [payload]
    nodes = 0
    while pending and len(references) < SESSION_SUMMARY_MAX_REFERENCES and nodes < 500:
        value = pending.pop(0)
        nodes += 1
        if isinstance(value, dict):
            output_id = value.get("output_id")
            if isinstance(output_id, str) and re.fullmatch(r"[0-9a-f]{64}", output_id):
                add("output_id", output_id)
            for key in _REFERENCE_FIELDS:
                if len(references) >= SESSION_SUMMARY_MAX_REFERENCES:
                    break
                if key in value:
                    add("lookup_key" if key == "conclusion_lookup_key" else key, value[key])
                if len(references) >= SESSION_SUMMARY_MAX_REFERENCES:
                    break
            if len(references) < SESSION_SUMMARY_MAX_REFERENCES:
                kind = value.get("kind")
                identifier = value.get("id")
                if kind == "run":
                    add("run_id", identifier)
                elif kind == "fact" and _identifier(identifier) and (kind, identifier) not in seen:
                    # The typed contract calls this an id. It need not be a
                    # query_project_memory lookup_key; keep its source shape.
                    references.append({"kind": kind, "id": identifier})
                    seen.add((kind, identifier))
            pending.extend(value[key] for key in _REFERENCE_CONTAINERS
                           if isinstance(value.get(key), (dict, list)))
        elif isinstance(value, list):
            pending.extend(item for item in value[:SESSION_SUMMARY_MAX_REFERENCES]
                           if isinstance(item, (dict, list)))
    return references[:SESSION_SUMMARY_MAX_REFERENCES]


def _tool_input_view(message: ToolMessage, *, is_pending_result: Callable | None) -> ToolMessage:
    if (is_pending_result or result_requires_user)(message):
        return message
    try:
        payload = json.loads(message.content) if isinstance(message.content, str) else None
    except (ValueError, TypeError):
        payload = None
    card: dict[str, Any] = {
        "session_input_view": True,
        "tool": message.name,
        "scientific_details_omitted": True,
        "reference_index_complete": False,
    }
    if isinstance(payload, dict):
        containers = [payload.get("worker_outcome"), payload.get("result"), payload]
        for container in containers:
            if not isinstance(container, dict):
                continue
            status = container.get("status")
            if isinstance(status, str) and status in _STATUSES:
                card["status"] = status
                break
        card["references"] = _references(payload)
    return message.model_copy(update={
        "content": json.dumps(card, ensure_ascii=False, separators=(",", ":")),
        "artifact": None,
    })


def _assistant_input_view(message: AIMessage) -> AIMessage:
    """Keep visible answer text and call identity, not scripts or model reasoning."""
    return message.model_copy(update={
        "content": str(message.text),
        "tool_calls": [{"id": call.get("id"), "name": call["name"], "args": {}, "type": "tool_call"}
                       for call in message.tool_calls],
        "invalid_tool_calls": [],
        "additional_kwargs": {},
        "response_metadata": {},
        "usage_metadata": None,
    })


def summary_input_messages(
    messages: Sequence[BaseMessage], *, is_pending_result: Callable | None = None,
) -> list[BaseMessage]:
    """Keep human/old-summary text intact; expose tool identity/status/pointers.

    Pending decision payloads stay complete as a second safeguard. The middleware
    also retains those entire call/result blocks outside summary replacement.
    Ordinary assistant answer text stays intact for conversational references;
    tool-call arguments, hidden reasoning, and provider metadata are excluded.
    This function is for the summarization model only, not main-agent requests.
    """
    return [
        _tool_input_view(message, is_pending_result=is_pending_result)
        if isinstance(message, ToolMessage) else _assistant_input_view(message)
        if isinstance(message, AIMessage) else message
        for message in messages
    ]


def render_session_summary_prompt(
    messages: Sequence[BaseMessage], *, summary_prompt: str = SESSION_SUMMARY_PROMPT,
    max_chars: int = SESSION_SUMMARY_MAX_CHARS, correction: str | None = None,
    is_pending_result: Callable | None = None,
    current_context: Sequence[BaseMessage] = (),
) -> str:
    """Use one fixed content contract for initial, recursive, and retry prompts."""
    hard_limit = min(SESSION_SUMMARY_MAX_CHARS, max_chars)
    if hard_limit <= 0:
        raise ValueError("Session summary character limit must be positive")
    target = min(SESSION_SUMMARY_TARGET_CHARS, max(1, hard_limit // 2))
    projected = summary_input_messages(messages, is_pending_result=is_pending_result)
    serialized = get_buffer_string(projected, format="xml")
    prompt = summary_prompt.format(messages=serialized).rstrip()
    if current_context:
        retained = summary_input_messages(current_context, is_pending_result=is_pending_result)
        prompt += (
            "\n\n<CURRENT_RETAINED_CONTEXT>\n"
            "These messages remain in the checkpoint verbatim. Use them only to identify "
            "current focus, active constraints, and which earlier questions are resolved.\n"
            + get_buffer_string(retained, format="xml")
            + "\n</CURRENT_RETAINED_CONTEXT>"
        )
    prompt += (
        f"\n\nCompression target: at most {target} characters; hard limit: {hard_limit} characters, "
        "including all four headings. The target does not grow with conversation length. "
        "Retain active user constraints and open decisions; omit historical result details."
    )
    if correction:
        prompt += (
            f"\nThe previous attempt failed validation ({correction}). "
            "Produce a corrected summary from the ORIGINAL messages above. "
            "Use every required heading, keep entries brief, and omit commentary "
            "about the summarization process."
        )
    return prompt


__all__ = [
    "SESSION_SUMMARY_HEADINGS", "SESSION_SUMMARY_PROMPT", "SESSION_SUMMARY_TARGET_CHARS",
    "SESSION_SUMMARY_MAX_CHARS", "SESSION_SUMMARY_MAX_REFERENCES", "SessionSummaryValidationError",
    "validate_session_summary", "summary_input_messages", "render_session_summary_prompt",
]
