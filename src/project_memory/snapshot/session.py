"""Read a project's durable memory context for a caller's session.

``session`` here means loading project memory for use by a caller. This module
does not own the Agent's Session summary: that conversation-continuity summary
belongs to ``agents.middleware.session`` and is persisted in a thread checkpoint.
The project snapshot below can be reused across threads and does not contain
chat history, runtime request policy, or Agent middleware configuration.

Reads never generate or repair a summary, approve a plan, or change a chat
execution. Agent runtime adapters own prompt serialization and live refresh.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from project_memory.identifiers import ACTIVE_PLAN_LOOKUP_KEY, PENDING_PLAN_LOOKUP_KEY
from project_memory.snapshot.contracts import ProjectMemorySnapshot
from project_memory.summary.contracts import SUMMARY_FORMAT_VERSION

if TYPE_CHECKING:
    from project_memory.storage.repository import MemoryRepository
    from project_workspace.store import ProjectStore


def _plan_pointer(memory: MemoryRepository, lookup_key: str) -> dict[str, Any] | None:
    rows = memory.query_facts(kind="project_plan", lookup_key=lookup_key, limit=1)
    if not rows:
        return None
    pointer = rows[0]
    return {
        "fact_id": pointer.fact_id,
        "lookup_key": pointer.lookup_key,
        "memory_version": pointer.version,
        "updated_at": pointer.updated_at,
        "value": pointer.value,
    }


def read_project_memory_snapshot(
    store: ProjectStore,
    project_id: str,
) -> ProjectMemorySnapshot:
    """Read current summary, authoritative plan pointers, and memory health.

    Unsupported summary formats remain available as diagnostic metadata on
    ``summary`` but cannot enter caller context through ``project_summary``.
    A current-format summary may still be stale while new changes await the
    separately scheduled project-summary refresh.
    """
    memory = store.memory(project_id)
    summary = memory.get_summary()
    return ProjectMemorySnapshot(
        project_id=project_id,
        summary=summary,
        summary_valid=summary.format_version == SUMMARY_FORMAT_VERSION,
        latest_revision=memory.latest_semantic_revision(),
        active_project_plan=_plan_pointer(memory, ACTIVE_PLAN_LOOKUP_KEY),
        pending_project_plan=_plan_pointer(memory, PENDING_PLAN_LOOKUP_KEY),
        extraction_counts=memory.retry_job_counts(),
        publication_counts=memory.publication_counts(),
    )


def build_memory_context_preview(store: ProjectStore, project_id: str) -> dict:
    """Present the same project snapshot with full plan/draft detail for the UI."""
    snapshot = read_project_memory_snapshot(store, project_id)
    memory = store.memory(project_id)
    state = snapshot.summary
    retry_counts = snapshot.extraction_counts
    publication_counts = snapshot.publication_counts
    active_plan = None
    if snapshot.active_project_plan:
        plan_id = str(snapshot.active_project_plan["value"].get("plan_id") or "").strip()
        if plan_id:
            plan_rows = memory.query_facts(
                kind="project_plan", lookup_key=f"plan:{plan_id}", limit=1,
            )
            if plan_rows:
                plan_fact = plan_rows[0]
                active_plan = {
                    "fact_id": plan_fact.fact_id,
                    "lookup_key": plan_fact.lookup_key,
                    "memory_version": plan_fact.version,
                    "updated_at": plan_fact.updated_at,
                    "plan": plan_fact.value,
                }
    pending_plan = None
    if snapshot.pending_project_plan:
        draft_id = str(snapshot.pending_project_plan["value"].get("draft_id") or "").strip()
        if draft_id:
            draft_rows = memory.query_facts(
                kind="project_plan", lookup_key=f"plan_draft:{draft_id}", limit=1,
            )
            if draft_rows:
                draft_fact = draft_rows[0]
                pending_plan = {
                    "fact_id": draft_fact.fact_id,
                    "lookup_key": draft_fact.lookup_key,
                    "memory_version": draft_fact.version,
                    "updated_at": draft_fact.updated_at,
                    "draft": draft_fact.value,
                }
    return {
        "summary": snapshot.project_summary,
        "semantic_digest": snapshot.semantic_digest,
        "source_revision": state.source_revision,
        "latest_revision": snapshot.latest_revision,
        "changes_pending": snapshot.health["summary_stale"],
        "fact_count": state.fact_count,
        "run_count": state.run_count,
        "generated_at": state.generated_at,
        "active_plan": active_plan,
        "pending_plan": pending_plan,
        "retry_pending_count": (
            retry_counts.get("pending", 0)
            + retry_counts.get("processing", 0)
            + retry_counts.get("retry_wait", 0)
        ),
        "retry_dead_letter_count": retry_counts.get("dead_letter", 0),
        "publication_pending_count": publication_counts.get("pending", 0),
        "publication_blocked_count": publication_counts.get("blocked", 0),
    }
