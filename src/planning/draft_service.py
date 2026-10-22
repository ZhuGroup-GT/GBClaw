"""Approval-gated create/revise draft lifecycle for durable project plans."""

from __future__ import annotations

import uuid

from project_memory.models import FactRecord, FactStatus
from project_memory.storage.facts import FactVersionConflict
from project_workspace.models import utc_now_iso
from planning.models import (
    PlanDraftAction,
    PlanDraftStatus,
    PlanRevisionAuthor,
    PlanStepDraft,
    ProjectPlan,
    ProjectPlanDraft,
    ProjectPlanDraftEnvelope,
    ProjectPlanEnvelope,
    ProjectPlanRevision,
    ProjectPlanStatus,
)
from planning.repository import (
    MAX_UPDATE_RETRIES,
    PlanFactBatch,
    PlanRepository,
)
from planning.rules import (
    build_revision,
    derive_status,
    new_step,
    validate_step_drafts,
)
from planning.service import ProjectPlanService
from planning.views import (
    clear_pending_plan_draft_view,
    save_project_plan_draft_views,
)
from project_workspace.store import ProjectStore


class ProjectPlanDraftService:
    """Persist plan proposals separately until an explicit user decision."""

    def __init__(self, store: ProjectStore, project_id: str) -> None:
        self.store = store
        self.project_id = project_id
        self.repository = PlanRepository(store.memory(project_id))
        self.plans = ProjectPlanService(store, project_id)

    def get_draft(self, draft_id: str | None = None) -> ProjectPlanDraftEnvelope:
        envelope = self.repository.get_draft(draft_id)
        self._save_views(envelope)
        return envelope

    def propose_new_plan(
        self,
        *,
        request_id: str,
        title: str,
        objective: str,
        steps: list[PlanStepDraft],
        intended_deliverable: str = "",
        known_evidence: list[str] | None = None,
        assumptions: list[str] | None = None,
        unknowns: list[str] | None = None,
        risks: list[str] | None = None,
        user_decisions_required: list[str] | None = None,
        chat_thread_id: str | None = None,
        user_request: str = "",
    ) -> ProjectPlanDraftEnvelope:
        normalized_request_id = request_id.strip()
        if not normalized_request_id:
            raise ValueError("request_id must not be empty")
        if not steps:
            raise ValueError("A project plan draft must contain at least one step")
        if len(steps) > 50:
            raise ValueError("A project plan may contain at most 50 steps")
        validate_step_drafts(steps)
        draft_id = self._draft_id(normalized_request_id)
        try:
            return self.get_draft(draft_id)
        except ValueError:
            pass

        now = utc_now_iso()
        plan_id = "plan-" + uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{self.project_id}:{normalized_request_id}",
        ).hex[:16]
        plan_steps = [new_step(step, now=now) for step in steps]
        proposed_plan = ProjectPlan(
            plan_id=plan_id,
            title=title.strip(),
            objective=objective.strip(),
            intended_deliverable=intended_deliverable.strip(),
            status=derive_status(plan_steps),
            known_evidence=list(known_evidence or []),
            assumptions=list(assumptions or []),
            unknowns=list(unknowns or []),
            steps=plan_steps,
            risks=list(risks or []),
            user_decisions_required=list(user_decisions_required or []),
            request_id=normalized_request_id,
            chat_thread_id=chat_thread_id,
            revision=1,
            revision_history=[
                ProjectPlanRevision(
                    revision=1,
                    reason="Initial plan proposed for user approval",
                    requested_by=PlanRevisionAuthor.PLANNER,
                    changed_fields=["initial_plan"],
                    source_draft_id=draft_id,
                    created_at=now,
                )
            ],
            created_at=now,
            updated_at=now,
        )
        draft = ProjectPlanDraft(
            draft_id=draft_id,
            action=PlanDraftAction.CREATE,
            request_id=normalized_request_id,
            chat_thread_id=chat_thread_id,
            title=title.strip(),
            objective=objective.strip(),
            intended_deliverable=intended_deliverable.strip(),
            steps=steps,
            known_evidence=list(known_evidence or []),
            assumptions=list(assumptions or []),
            unknowns=list(unknowns or []),
            risks=list(risks or []),
            user_decisions_required=list(user_decisions_required or []),
            revision_reason=user_request.strip() or "New project plan requested",
            user_request=user_request.strip(),
            proposed_plan=proposed_plan,
            created_at=now,
            updated_at=now,
        )
        return self._persist_pending(draft)

    def propose_revision(
        self,
        plan_id: str,
        *,
        request_id: str,
        reason: str,
        requested_by: PlanRevisionAuthor | str = PlanRevisionAuthor.USER,
        title: str | None = None,
        objective: str | None = None,
        intended_deliverable: str | None = None,
        steps: list[PlanStepDraft] | None = None,
        known_evidence: list[str] | None = None,
        assumptions: list[str] | None = None,
        unknowns: list[str] | None = None,
        risks: list[str] | None = None,
        user_decisions_required: list[str] | None = None,
        chat_thread_id: str | None = None,
        user_request: str = "",
    ) -> ProjectPlanDraftEnvelope:
        normalized_request_id = request_id.strip()
        normalized_reason = reason.strip()
        if not normalized_request_id:
            raise ValueError("request_id must not be empty")
        if not normalized_reason:
            raise ValueError("A plan revision draft requires a reason")
        if steps is not None:
            if not steps:
                raise ValueError("A revised project plan must contain at least one step")
            if len(steps) > 50:
                raise ValueError("A project plan may contain at most 50 steps")
            validate_step_drafts(steps)
        draft_id = self._draft_id(normalized_request_id)
        try:
            return self.get_draft(draft_id)
        except ValueError:
            pass

        base = self.plans.get_plan(plan_id)
        if base.plan.status is ProjectPlanStatus.SUPERSEDED:
            raise ValueError(f"Cannot draft a revision of superseded plan {plan_id}")
        now = utc_now_iso()
        proposed_plan, changed_fields = build_revision(
            base,
            source_draft_id=draft_id,
            reason=normalized_reason,
            requested_by=PlanRevisionAuthor(requested_by),
            title=title,
            objective=objective,
            intended_deliverable=intended_deliverable,
            steps=steps,
            known_evidence=known_evidence,
            assumptions=assumptions,
            unknowns=unknowns,
            risks=risks,
            user_decisions_required=user_decisions_required,
            now=now,
            no_changes_message="Plan revision draft does not change any plan field",
        )
        draft = ProjectPlanDraft(
            draft_id=draft_id,
            action=PlanDraftAction.REVISE,
            request_id=normalized_request_id,
            chat_thread_id=chat_thread_id,
            target_plan_id=plan_id,
            base_revision=base.plan.revision,
            base_memory_version=base.memory_version,
            revision_reason=normalized_reason,
            user_request=user_request.strip(),
            requested_by=PlanRevisionAuthor(requested_by),
            title=title.strip() if isinstance(title, str) else None,
            objective=objective.strip() if isinstance(objective, str) else None,
            intended_deliverable=(
                intended_deliverable.strip()
                if isinstance(intended_deliverable, str)
                else None
            ),
            steps=steps,
            known_evidence=known_evidence,
            assumptions=assumptions,
            unknowns=unknowns,
            risks=risks,
            user_decisions_required=user_decisions_required,
            proposed_plan=proposed_plan,
            decision_feedback=(
                f"Proposed changes: {', '.join(changed_fields)}"
            ),
            created_at=now,
            updated_at=now,
        )
        return self._persist_pending(draft)

    def approve(self, draft_id: str | None = None) -> ProjectPlanDraftEnvelope:
        envelope = self.get_draft(draft_id)
        draft = envelope.draft
        if draft.status is PlanDraftStatus.APPROVED:
            return envelope
        if draft.status is not PlanDraftStatus.PENDING:
            raise ValueError(
                f"Plan draft {draft.draft_id} is {draft.status.value}, not pending"
            )

        already_approved = self._matching_committed_plan(draft)
        if already_approved is not None:
            return self._decide(
                envelope,
                status=PlanDraftStatus.APPROVED,
                feedback="Approved by user",
                approved_plan=already_approved,
            )

        approval_changes = self._approval_mutation(envelope)
        try:
            if draft.action is PlanDraftAction.CREATE:
                if draft.title is None or draft.objective is None or draft.steps is None:
                    raise ValueError("New-plan draft payload is incomplete")
                approved = self.plans.create_plan(
                    request_id=draft.request_id,
                    title=draft.title,
                    objective=draft.objective,
                    steps=draft.steps,
                    intended_deliverable=draft.intended_deliverable or "",
                    known_evidence=draft.known_evidence,
                    assumptions=draft.assumptions,
                    unknowns=draft.unknowns,
                    risks=draft.risks,
                    user_decisions_required=draft.user_decisions_required,
                    chat_thread_id=draft.chat_thread_id,
                    source_draft_id=draft.draft_id,
                    additional_changes=approval_changes,
                )
            else:
                if draft.target_plan_id is None or draft.base_revision is None:
                    raise ValueError("Revision draft payload is incomplete")
                approved = self.plans.revise_plan(
                    draft.target_plan_id,
                    reason=draft.revision_reason,
                    requested_by=draft.requested_by,
                    title=draft.title,
                    objective=draft.objective,
                    intended_deliverable=draft.intended_deliverable,
                    steps=draft.steps,
                    known_evidence=draft.known_evidence,
                    assumptions=draft.assumptions,
                    unknowns=draft.unknowns,
                    risks=draft.risks,
                    user_decisions_required=draft.user_decisions_required,
                    base_revision=draft.base_revision,
                    source_draft_id=draft.draft_id,
                    additional_changes=approval_changes,
                )
        except FactVersionConflict:
            # A concurrent retry may already have atomically committed this decision.
            latest = self.get_draft(draft.draft_id)
            if latest.draft.status is PlanDraftStatus.APPROVED:
                return latest
            raise
        decided = self.get_draft(draft.draft_id)
        if decided.draft.status is not PlanDraftStatus.APPROVED:
            raise FactVersionConflict(
                f"Draft {draft.draft_id} approval did not commit atomically"
            )
        if (
            decided.draft.approved_plan_id != approved.plan.plan_id
            or decided.draft.approved_revision != approved.plan.revision
        ):
            raise FactVersionConflict(
                f"Draft {draft.draft_id} approval points to an unexpected plan revision"
            )
        return decided

    def reject(
        self,
        draft_id: str | None = None,
        *,
        feedback: str = "",
    ) -> ProjectPlanDraftEnvelope:
        envelope = self.get_draft(draft_id)
        if envelope.draft.status is not PlanDraftStatus.PENDING:
            raise ValueError(
                f"Plan draft {envelope.draft.draft_id} is not pending"
            )
        return self._decide(
            envelope,
            status=PlanDraftStatus.REJECTED,
            feedback=feedback.strip() or "Rejected by user",
        )


    def _matching_committed_plan(
        self,
        draft: ProjectPlanDraft,
    ) -> ProjectPlanEnvelope | None:
        """Recover a legacy/partial approval only when its source draft matches exactly."""
        try:
            committed = self.plans.get_plan(draft.proposed_plan.plan_id)
        except ValueError:
            return None
        history = committed.plan.revision_history
        latest = history[-1] if history else None
        if (
            latest is not None
            and latest.source_draft_id == draft.draft_id
            and committed.plan.revision == draft.proposed_plan.revision
        ):
            return committed
        if draft.action is PlanDraftAction.CREATE:
            raise FactVersionConflict(
                f"Proposed plan ID {draft.proposed_plan.plan_id} already exists without "
                f"approval from draft {draft.draft_id}"
            )
        return None

    def _approval_mutation(
        self,
        envelope: ProjectPlanDraftEnvelope,
    ) -> PlanFactBatch:
        draft = envelope.draft
        now = utc_now_iso()
        decided = draft.model_copy(
            update={
                "status": PlanDraftStatus.APPROVED,
                "decision_feedback": "Approved by user",
                "approved_plan_id": draft.proposed_plan.plan_id,
                "approved_revision": draft.proposed_plan.revision,
                "updated_at": now,
                "decided_at": now,
            }
        )
        pointer = self.repository.pending_pointer(include_retracted=True)
        if pointer is None:
            raise FactVersionConflict("Pending plan pointer disappeared during approval")

        if (
            pointer.status is not FactStatus.ACTIVE
            or pointer.value.get("draft_id") != draft.draft_id
        ):
            raise FactVersionConflict(
                f"Draft {draft.draft_id} is no longer the pending project plan"
            )
        draft_fact = self.repository.draft_fact(decided, created_at=draft.created_at)
        pointer_fact = self.repository.pending_pointer_fact(
            decided,
            created_at=pointer.created_at,
            retracted=True,
        )
        return PlanFactBatch(
            facts=[draft_fact, pointer_fact],
            expected_versions={
                (draft_fact.kind, draft_fact.lookup_key): envelope.memory_version,
                (pointer_fact.kind, pointer_fact.lookup_key): pointer.version,
            },
        )

    def _persist_pending(self, draft: ProjectPlanDraft) -> ProjectPlanDraftEnvelope:
        for _attempt in range(MAX_UPDATE_RETRIES):
            try:
                return self.get_draft(draft.draft_id)
            except ValueError:
                pass
            pointer = self.repository.pending_pointer(include_retracted=True)

            facts: list[FactRecord] = []
            expected: dict[tuple[str, str], int | None] = {}
            superseded_draft_id: str | None = None
            if pointer is not None and pointer.status is FactStatus.ACTIVE:
                previous_id = str(pointer.value.get("draft_id") or "").strip()
                if previous_id and previous_id != draft.draft_id:
                    superseded_draft_id = previous_id
                    previous = self.get_draft(previous_id)
                    superseded = previous.draft.model_copy(
                        update={
                            "status": PlanDraftStatus.SUPERSEDED,
                            "decision_feedback": (
                                f"Superseded by draft {draft.draft_id}"
                            ),
                            "updated_at": draft.updated_at,
                            "decided_at": draft.updated_at,
                        }
                    )
                    previous_fact = self.repository.draft_fact(
                        superseded,
                        created_at=previous.draft.created_at,
                    )
                    facts.append(previous_fact)
                    expected[(previous_fact.kind, previous_fact.lookup_key)] = (
                        previous.memory_version
                    )
            draft_fact = self.repository.draft_fact(draft, created_at=draft.created_at)
            pointer_fact = self.repository.pending_pointer_fact(
                draft,
                created_at=pointer.created_at if pointer is not None else draft.created_at,
            )
            facts.extend((draft_fact, pointer_fact))
            expected[(draft_fact.kind, draft_fact.lookup_key)] = None
            expected[(pointer_fact.kind, pointer_fact.lookup_key)] = (
                pointer.version if pointer is not None else None
            )
            try:
                self.repository.commit(
                    facts,
                    expected_versions=expected,
                )
                if superseded_draft_id is not None:
                    self.get_draft(superseded_draft_id)
                return self.get_draft(draft.draft_id)
            except FactVersionConflict:
                continue
        raise FactVersionConflict("Project plan draft creation exceeded retry limit")

    def _decide(
        self,
        envelope: ProjectPlanDraftEnvelope,
        *,
        status: PlanDraftStatus,
        feedback: str,
        approved_plan: ProjectPlanEnvelope | None = None,
    ) -> ProjectPlanDraftEnvelope:
        now = utc_now_iso()
        draft = envelope.draft.model_copy(
            update={
                "status": status,
                "decision_feedback": feedback,
                "approved_plan_id": (
                    approved_plan.plan.plan_id if approved_plan is not None else None
                ),
                "approved_revision": (
                    approved_plan.plan.revision if approved_plan is not None else None
                ),
                "updated_at": now,
                "decided_at": now,
            }
        )
        pointer = self.repository.pending_pointer(include_retracted=True)
        if pointer is None:
            raise FactVersionConflict("Pending plan pointer disappeared during decision")

        if (
            pointer.status is not FactStatus.ACTIVE
            or pointer.value.get("draft_id") != draft.draft_id
        ):
            raise FactVersionConflict(
                f"Draft {draft.draft_id} is no longer the pending project plan"
            )
        draft_fact = self.repository.draft_fact(draft, created_at=draft.created_at)
        pointer_fact = self.repository.pending_pointer_fact(
            draft,
            created_at=pointer.created_at,
            retracted=True,
        )
        self.repository.commit(
            [draft_fact, pointer_fact],
            expected_versions={
                (draft_fact.kind, draft_fact.lookup_key): envelope.memory_version,
                (pointer_fact.kind, pointer_fact.lookup_key): pointer.version,
            },
        )
        return self.get_draft(draft.draft_id)

    def pending_pointer(self) -> FactRecord | None:
        return self.repository.pending_pointer()

    def _draft_id(self, request_id: str) -> str:
        return "draft-" + uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{self.project_id}:plan-draft:{request_id}",
        ).hex[:16]


    def _save_views(self, envelope: ProjectPlanDraftEnvelope) -> None:
        try:
            pending = self.pending_pointer()
            is_current_pending = bool(
                envelope.draft.status is PlanDraftStatus.PENDING
                and pending is not None
                and pending.value.get("draft_id") == envelope.draft.draft_id
            )
            save_project_plan_draft_views(
                self.store.project_path(self.project_id),
                envelope.model_dump(mode="json"),
                save_pending_view=is_current_pending,
            )
            if pending is None:
                clear_pending_plan_draft_view(
                    self.store.project_path(self.project_id)
                )
        except OSError:
            # SQLite is authoritative. A later draft read can rebuild derived views.
            pass


__all__ = ["ProjectPlanDraftService"]
