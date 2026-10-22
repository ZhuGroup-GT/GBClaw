"""Importance and size policy for project memory, never for thread dialogue.

The working summary is a selective index. Completed details remain in SQLite
and previous summaries in the archive; excluding a card never deletes a record.
Registry context_priority lets new domains participate without hardcoded lists.
"""
from __future__ import annotations

import json
import re

from project_memory.identifiers import PLAN_POINTER_LOOKUP_KEYS
from core.settings.runtime_context import count_project_summary_tokens
from extensions import FACT_MEMORY_SPECS, fact_memory_spec
from .contracts import ProjectSummaryTokenLimitError

SUMMARY_TARGET_CHARS = 6_000
SUMMARY_MAX_CHARS = 12_000
SUMMARY_RETRY_RESERVE = 1_024
RECORD_DETAIL_MAX_CHARS = 2_000
SOURCE_HEADINGS = {
    "tool_result": "## Tool results",
    "conversation": "## Conversation-derived context",
    "agent_report": "## Agent reports",
    "workflow_state": "## Workflow state",
}
RETRIEVAL_NOTE = (
    "Details omitted here remain in SQLite. Use query_project_memory(entity='facts', "
    "lookup_key=...) or entity='runs', run_id=... for exact current evidence; "
    "search_project_memory finds older records. Use query_project_memory(entity='summary_archive') "
    "then archive_id=... for historical summaries (also memory/summary_archive/index.md). "
    "Archives are historical context, not current decisions, approval or verified evidence."
)
_OPEN = {"running", "configured", "pending", "pending_approval", "awaiting_user", "partial",
         "failed", "blocked", "interrupted", "publication_error", "approved", "active"}
_FINISHED = {"completed", "reused", "rejected", "superseded", "cancelled"}


def summary_char_limit(max_tokens: int) -> int:
    # Reserve framing and room for callers' memory health / evidence notices.
    return min(SUMMARY_MAX_CHARS, max(0, max_tokens - 256))


def fact_priority(fact) -> int:
    """Prefer durable intent and live blockers to completed execution history."""
    declaration = fact_memory_spec(fact.kind)
    if declaration.context_priority > 0 or declaration.fixed_lookup_key:
        return 1_000 + declaration.context_priority
    if fact.lookup_key in PLAN_POINTER_LOOKUP_KEYS:
        return 1_100
    status = str(fact.value.get("status") or fact.value.get("outcome") or "").lower()
    if fact.source_type.value == "workflow_state":
        return 900 if status in _OPEN else (100 if status in _FINISHED else 500)
    if fact.source_type.value == "agent_report":
        return 800 if status in _OPEN else 200
    return 700 if fact.source_type.value == "tool_result" else 750


def _preview(value, limit: int) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    # Quoted user/report text can itself mention `fact: `...``. Only host-made
    # citations below may be parsed as active evidence references.
    text = " ".join(text.split()).replace("`", r"\u0060")
    if len(text) <= limit:
        return text
    return "[value omitted; query the exact record]"


def _fact_card(fact, *, detail_chars=600) -> str:
    # Prefer a small complete subset to a cut-off JSON/scientific assertion.
    fields = ("goal", "objective", "decision", "selection", "status", "outcome", "summary",
              "abstract", "conclusion", "blockers", "next_action", "plan_id", "draft_id",
              "conclusion_id", "source_run_ids", "active_invocations", "blocked_invocations")
    value = {key: fact.value[key] for key in fields if fact.value.get(key) not in (None, "", [], {})}
    body = _preview(value or fact.value, detail_chars)
    origin = f"{fact.kind}; evidence={fact.evidence_kind.value}"
    source = f"; run={fact.source_run_id}" if fact.source_run_id else ""
    return f"- {origin}{source}: {body} (fact: `{fact.lookup_key}`)"


def priority_facts(memory):
    """Bound recent recall while querying durable domain priorities separately."""
    facts = {fact.fact_id: fact for fact in memory.query_facts(limit=256)}
    # Filter open state in SQLite, before limiting: a recent burst of completed
    # tasks must not hide an older blocker or pending approval.
    facts.update((fact.fact_id, fact) for fact in memory.query_summary_open_facts(limit=128))
    for spec in FACT_MEMORY_SPECS.values():
        if spec.context_priority > 0 or spec.fixed_lookup_key:
            for fact in memory.query_facts(kind=spec.key, limit=64):
                facts[fact.fact_id] = fact
    for key in PLAN_POINTER_LOOKUP_KEYS:
        for fact in memory.query_facts(lookup_key=key, limit=1):
            facts[fact.fact_id] = fact
    return sorted(facts.values(), key=lambda f: (fact_priority(f), f.updated_at, f.fact_id), reverse=True)


def build_priority_summary(memory, *, model, max_tokens: int, max_chars: int | None = None,
                           history_navigation: str = "") -> str:
    """Deterministic, source-separated retrieval index when prose won't fit.

No model interpretation is created here. Even under tight budgets the exact
lookup/run pointers are kept in preference to excerpts of large values.
"""
    char_limit = min(summary_char_limit(max_tokens), max_chars or SUMMARY_MAX_CHARS)
    sections = {source: [] for source in SOURCE_HEADINGS}

    def render():
        return "\n\n".join(
            heading + "\n" + ("\n".join(sections[source]) or "- Query SQLite for details.")
            for source, heading in SOURCE_HEADINGS.items()
        ) + "\n\n" + RETRIEVAL_NOTE + ("\n\n" + history_navigation if history_navigation else "")

    def fits():
        body = render()
        return len(body) <= char_limit and count_project_summary_tokens(model, body) <= max_tokens

    if not fits():
        raise ProjectSummaryTokenLimitError("Project summary budget cannot fit source labels and retrieval guidance")
    candidates = []
    for fact in priority_facts(memory):
        pointer = f"- {fact.kind}; evidence={fact.evidence_kind.value} (fact: `{fact.lookup_key}`)"
        full = _fact_card(fact)
        if fact.source_run_id and fact.source_type.value == "tool_result":
            run = memory.get_run(fact.source_run_id)
            if run.status.value != "completed":
                full = pointer + f"; run={run.run_id}, status={run.status.value}; not completed computation evidence."
                pointer = full
        candidates.append((fact_priority(fact), fact.updated_at, fact.source_type.value, full, pointer))
    runs = {r.run_id: r for r in memory.query_runs(limit=64)}
    # Live runs cannot be crowded out by a large completed history.
    for status in ("running", "configured"):
        from project_memory.models import RunStatus
        runs.update((r.run_id, r) for r in memory.query_runs(status=RunStatus(status), limit=64))
    for run in runs.values():
        priority = 950 if run.status.value in {"running", "configured"} else (780 if run.status.value == "failed" else 300)
        line = f"- Run `{run.run_id}`; {run.workflow.value}; status={run.status.value}."
        candidates.append((priority, run.updated_at, "tool_result", line, line))
    for _, _, source, full, pointer in sorted(candidates, key=lambda c: (c[0], c[1]), reverse=True):
        for line in dict.fromkeys((full, pointer)):
            sections[source].append(line)
            if fits():
                break
            sections[source].pop()
    return render()


def attach_retrieval_note(summary: str, *, history_navigation: str = "") -> str:
    """Attach host-owned guidance and navigation, replacing any model-made index."""
    from .navigation import strip_history_navigation

    body = strip_history_navigation(summary).strip()
    body = body if RETRIEVAL_NOTE in body else body + "\n\n" + RETRIEVAL_NOTE
    return body + ("\n\n" + history_navigation if history_navigation else "")


def required_summary_keys(memory, priority_summary: str) -> set[str]:
    """Require the durable intent/open-state pointers that fit the host index."""
    included = set(re.findall(r"\(fact: `([^`]+)`\)", priority_summary))
    return {fact.lookup_key for fact in priority_facts(memory)
            if fact_priority(fact) >= 900 and fact.lookup_key in included}
