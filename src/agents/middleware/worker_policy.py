"""Content contract for a disposable worker's compacted history.

A worker keeps its task and its newest tool-call blocks verbatim; the middle is
replaced by one summary. Unlike the session handoff, this summary may hold the
worker's own results and parameters: finished work shrinks to its results and
references, work in progress keeps the detail needed to continue, and consumed
tool results stay readable through their archived ``output_id``. Completed
results that do not fit the in-context limit move to an archived file with a
pointer left in their place; they are never silently dropped.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage, get_buffer_string

from .session_policy import SessionSummaryValidationError, summary_input_messages

WORKER_SUMMARY_HEADINGS = (
    "## COMPLETED",
    "## IN PROGRESS",
    "## OPEN ITEMS",
    "## LOOK-BACK REFERENCES",
)
# Each consumed tool result is shown to the summarizer up to this size; the
# complete result stays readable through read_tool_output.
WORKER_SUMMARY_RESULT_CHARS = 8_000
WORKER_SUMMARY_ARGS_CHARS = 2_000

WORKER_SUMMARY_PROMPT = """<role>
Compact the working history of a GBclaw worker that is still running one delegated task.
The task message stays verbatim before your summary and the newest messages stay verbatim
after it; everything in between is replaced by what you write.
</role>

<requirements>
- Judge each piece of work by its state.
  - COMPLETED work keeps only its results: run IDs, artifact IDs, output_ids, the key
    numbers and settled conclusions, and whether the result was verified. Drop the process:
    commands tried, intermediate outputs, fixed errors and superseded attempts.
  - IN-PROGRESS work keeps what is needed to continue without re-reading: the current step,
    its exact parameters and file paths, the working hypothesis, the last observed state or
    error (verbatim where the wording matters) and the immediate next action.
- Carry forward every constraint, decision and instruction from the task or discovered since,
  with exact values.
- When the input contains an earlier summary, carry its COMPLETED and OPEN ITEMS forward and
  rewrite IN PROGRESS from the newer messages.
- A tool result whose full_output names an output_id can be re-read with read_tool_output.
  Under LOOK-BACK REFERENCES list each output_id that may still be needed, one line each,
  with what the result holds. Never invent IDs, paths, values or results.
- The RETAINED MESSAGES block, when present, stays verbatim after your summary. Use it to
  tell which work is current; do not repeat its content.
- IN PROGRESS, OPEN ITEMS and LOOK-BACK REFERENCES must fit the in-context limit with room to
  spare. COMPLETED may run longer than fits: list its results most-important-first, one result
  per line. The host keeps the beginning of COMPLETED in context and moves the rest to a file
  the worker can read back, so order matters more than brevity there.
- Do not comment on the summarization itself and do not resolve open questions yourself.
- Use the task's language.
</requirements>

<output_format>
Return only Markdown with exactly these headings, in this order:
## COMPLETED
## IN PROGRESS
## OPEN ITEMS
## LOOK-BACK REFERENCES
Use 'None' for an empty section.
</output_format>

<messages>
{messages}
</messages>
"""


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"… [{len(text) - limit} more characters]"


def _result_view(message: ToolMessage) -> ToolMessage:
    """Bound one consumed result; its full_output pointer survives in front."""
    content = message.content if isinstance(message.content, str) else json.dumps(
        message.content, ensure_ascii=False)
    if len(content) <= WORKER_SUMMARY_RESULT_CHARS:
        return message
    try:
        payload = json.loads(content)
    except ValueError:
        payload = None
    pointer = payload.get("full_output") if isinstance(payload, dict) else None
    head = json.dumps({"full_output": pointer}, ensure_ascii=False) + "\n" if pointer else ""
    return message.model_copy(update={
        "content": head + _clip(content, WORKER_SUMMARY_RESULT_CHARS), "artifact": None,
    })


def _call_view(message: AIMessage) -> AIMessage:
    """Keep the worker's own call arguments, bounded; drop provider metadata and reasoning."""
    calls = []
    for call in message.tool_calls:
        args = json.dumps(call.get("args") or {}, ensure_ascii=False)
        if len(args) > WORKER_SUMMARY_ARGS_CHARS:
            args = json.dumps({"_truncated_args": _clip(args, WORKER_SUMMARY_ARGS_CHARS)})
        calls.append({"id": call.get("id"), "name": call["name"], "args": json.loads(args),
                      "type": "tool_call"})
    return message.model_copy(update={
        "content": str(message.text), "tool_calls": calls, "invalid_tool_calls": [],
        "additional_kwargs": {}, "response_metadata": {}, "usage_metadata": None,
    })


def worker_summary_input_messages(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """The summarizer's view of consumed messages: bounded results, bounded arguments."""
    return [_result_view(message) if isinstance(message, ToolMessage)
            else _call_view(message) if isinstance(message, AIMessage) else message
            for message in messages]


def render_worker_summary_prompt(
    messages: Sequence[BaseMessage], *, max_chars: int, retained: Sequence[BaseMessage] = (),
    correction: str | None = None,
) -> str:
    serialized = get_buffer_string(worker_summary_input_messages(messages), format="xml")
    prompt = WORKER_SUMMARY_PROMPT.format(messages=serialized).rstrip()
    if retained:
        # Status and references only: the retained tail is sent verbatim anyway.
        prompt += ("\n\n<RETAINED_MESSAGES>\n"
                   + get_buffer_string(summary_input_messages(retained), format="xml")
                   + "\n</RETAINED_MESSAGES>")
    prompt += (f"\n\nIn-context limit: {max_chars} characters including the four headings. Spend them "
               "on IN PROGRESS detail and exact references, not on completed process; COMPLETED "
               "results beyond the limit are moved to a readable file, not lost.")
    if correction:
        prompt += (f"\nThe previous attempt failed validation ({correction}). Produce a corrected "
                   "summary from the ORIGINAL messages above with every required heading, "
                   "within the limit, without commentary.")
    return prompt


def validate_worker_summary(summary: str) -> str:
    """Check the shape of a summary; its size is fitted separately."""
    text = str(summary or "").strip()
    if not text:
        raise SessionSummaryValidationError(
            "The summary model returned no summary.", code="summary_empty_response")
    if text.startswith("Error generating summary:"):
        raise SessionSummaryValidationError(
            "The summary service returned an error response.", code="summary_provider_error_response")
    headings = [match.group() for match in re.finditer(r"^#{1,6}[^\S\r\n]+[^\r\n]+", text, flags=re.MULTILINE)]
    if tuple(headings) != WORKER_SUMMARY_HEADINGS or not text.startswith(WORKER_SUMMARY_HEADINGS[0]):
        raise SessionSummaryValidationError(
            "A worker summary must use the four work-state sections.", code="summary_invalid_structure",
            diagnostics={"missing_heading_count": sum(h not in headings for h in WORKER_SUMMARY_HEADINGS)})
    return text


def _sections(text: str) -> list[str]:
    """Bodies of the four sections, in heading order."""
    starts = [text.index(heading) for heading in WORKER_SUMMARY_HEADINGS]
    return [text[start + len(heading):end].strip("\n")
            for heading, start, end in zip(WORKER_SUMMARY_HEADINGS, starts, [*starts[1:], len(text)])]


def _render(bodies: Sequence[str]) -> str:
    return "\n".join(f"{heading}\n{body}" for heading, body in zip(WORKER_SUMMARY_HEADINGS, bodies))


def fit_worker_summary(summary: str, *, max_chars: int, archive: Callable[[str], str | None]) -> str:
    """Fit a valid summary to its in-context limit by moving excess COMPLETED lines to a file.

    ``archive`` stores text and returns an ``output_id`` readable with
    read_tool_output, or None when nothing can be stored. The detail sections
    must fit on their own; a summary whose IN PROGRESS/OPEN ITEMS/LOOK-BACK
    REFERENCES already exceed the limit is rejected for correction.
    """
    text = summary.strip()
    if len(text) <= max_chars:
        return text
    completed, *details = _sections(text)
    pointer_room = 220
    room = max_chars - len(_render(["", *details])) - pointer_room
    if room < 200:
        raise SessionSummaryValidationError(
            "The worker summary's in-progress and open-item sections exceed the in-context limit.",
            code="summary_output_exceeds_budget",
            diagnostics={"summary_output_chars": len(text), "summary_character_limit": max_chars})
    output_id = archive(completed)
    if output_id is None:
        raise SessionSummaryValidationError(
            "The worker summary exceeded its in-context limit and no archive is available.",
            code="summary_output_exceeds_budget",
            diagnostics={"summary_output_chars": len(text), "summary_character_limit": max_chars})
    kept = completed[:room]
    kept = kept[:kept.rfind("\n")] if "\n" in kept else kept
    moved = completed.count("\n") - kept.count("\n")
    pointer = (f"[{moved} more COMPLETED lines are out of context; read the complete COMPLETED "
               f"section with read_tool_output output_id={output_id}]")
    return _render([f"{kept}\n{pointer}", *details])


__all__ = [
    "WORKER_SUMMARY_HEADINGS", "WORKER_SUMMARY_PROMPT", "fit_worker_summary",
    "render_worker_summary_prompt", "validate_worker_summary", "worker_summary_input_messages",
]
