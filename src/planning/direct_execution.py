"""State transition used by explicit direct-execution chat requests."""

from __future__ import annotations

from planning.draft_service import ProjectPlanDraftService
from planning.models import (
    PlanDraftStatus,
    ProjectPlanDraftEnvelope,
)
from project_workspace.store import ProjectStore


DIRECT_EXECUTION_FEEDBACK = (
    "Superseded by the user's explicit /noplan direct-execution request."
)


def supersede_pending_plan_draft(
    store: ProjectStore,
    project_id: str,
) -> ProjectPlanDraftEnvelope | None:
    """Reject and archive the pending draft, if any, before direct execution.

    Rejection keeps the immutable draft audit record while removing the pending
    pointer and generated root view. Existing active plans are not modified.
    """

    service = ProjectPlanDraftService(store, project_id)
    pointer = service.pending_pointer()
    if pointer is None:
        return None
    draft_id = str(pointer.value.get("draft_id") or "").strip()
    if not draft_id:
        raise ValueError("Pending project plan pointer is invalid")

    pending = service.get_draft(draft_id)
    if pending.draft.status is not PlanDraftStatus.PENDING:
        raise ValueError(f"Plan draft {draft_id} is not pending")
    return service.reject(
        draft_id,
        feedback=DIRECT_EXECUTION_FEEDBACK,
    )


__all__ = ["DIRECT_EXECUTION_FEEDBACK", "supersede_pending_plan_draft"]
