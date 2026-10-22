"""Bounded model generation; source selection and persistence live elsewhere."""
from typing import Any, Callable
import json
import logging
import re
from langchain_core.messages import HumanMessage, SystemMessage
from core.llm.generation import is_truncated, response_text
from core.settings.runtime_context import count_project_summary_tokens
from .contracts import ProjectSummaryTokenLimitError
from .batch import _summary_messages_input_tokens

MAX_SUMMARY_OUTPUT_ATTEMPTS = 2
LOGGER = logging.getLogger(__name__)

def _generate_summary_within_limit(
    *, summary_model, system_prompt: str, batch_payload: dict[str, Any],
    max_tokens: int, max_input_tokens: int | None = None,
    fallback_summary: str | None = None,
    required_fact_keys: set[str] | None = None,
    history_navigation: str = "",
    validate_citations: Callable[[str], None] | None = None,
) -> str:
    """Try one explicit correction, then use a validated database index.

    A rejected draft never expands the next request. Correction space is
    reserved by the batch builder; feedback is never silently discarded.
    Character limits are explicit even when local token counts are estimates.
    A citation the corrected draft still misattributes raises instead: the
    committed summary stays and its source changes remain pending.
    """
    from .policy import attach_retrieval_note, summary_char_limit, SUMMARY_TARGET_CHARS

    max_chars = summary_char_limit(max_tokens)
    last_error = "summary generation failed its size/completion checks"
    for attempt in range(MAX_SUMMARY_OUTPUT_ATTEMPTS):
        payload = dict(batch_payload)
        if attempt:
            payload["output_correction"] = (
                f"Previous output rejected: {last_error}. Rewrite under "
                f"{min(SUMMARY_TARGET_CHARS, max_chars)} characters, not tokens. "
                "Keep current goals, constraints, pending approval, blockers and key evidence IDs. "
                "Drop completed task narratives, old reports and parameter lists first. "
                "Details remain queryable in SQLite; never invent or relabel evidence."
            )
        messages = [SystemMessage(content=system_prompt), HumanMessage(content=json.dumps(
            payload, ensure_ascii=False, indent=2, sort_keys=True))]
        if max_input_tokens is not None and _summary_messages_input_tokens(summary_model, messages) > max_input_tokens:
            last_error = "summary correction cannot fit the reserved input budget"
            break
        response = summary_model.invoke(messages)
        draft = response_text(response)
        if not draft or is_truncated(response):
            last_error = "empty or incomplete generation"
            continue
        summary = attach_retrieval_note(draft, history_navigation=history_navigation)
        present = set(re.findall(r"\bfact\s*:\s*`([^`]+)`", summary, re.IGNORECASE))
        if (required_fact_keys or set()) - present:
            last_error = "output omitted required current goal/constraint/open-state fact pointers from current_priorities"
            continue
        measured = count_project_summary_tokens(summary_model, summary)
        if len(summary) <= max_chars and measured <= max_tokens:
            try:
                if validate_citations is not None:
                    validate_citations(summary)
            except ValueError as exc:
                if attempt + 1 == MAX_SUMMARY_OUTPUT_ATTEMPTS:
                    raise
                last_error = str(exc)
                LOGGER.warning("Project summary citation rejected on attempt %s/%s; %s",
                               attempt + 1, MAX_SUMMARY_OUTPUT_ATTEMPTS, last_error)
                continue
            return summary
        last_error = f"output had {len(summary)} characters / {measured} local budget units"
        LOGGER.warning("Project summary exceeded its budget on attempt %s/%s; %s",
                       attempt + 1, MAX_SUMMARY_OUTPUT_ATTEMPTS, last_error)
    if fallback_summary is not None:
        summary = attach_retrieval_note(fallback_summary, history_navigation=history_navigation)
        if len(summary) <= max_chars and count_project_summary_tokens(summary_model, summary) <= max_tokens:
            LOGGER.warning("Using bounded database retrieval index after project-summary generation: %s", last_error)
            return summary
    raise ProjectSummaryTokenLimitError(last_error + "; existing summary preserved")
