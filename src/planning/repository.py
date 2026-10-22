"""Map project plans to memory records and commit related changes atomically."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from project_memory.models import FactEvidenceKind, FactRecord, FactStatus
from project_memory.publication.writes import commit_facts
from project_memory.storage.repository import MemoryRepository
from planning.models import (
    ACTIVE_PLAN_LOOKUP_KEY, PENDING_PLAN_LOOKUP_KEY, PROJECT_PLAN_FACT_KIND,
    PlanStepStatus, ProjectPlan, ProjectPlanDraft, ProjectPlanDraftEnvelope,
    ProjectPlanEnvelope, plan_draft_lookup_key, plan_lookup_key, plan_revision_lookup_key,
)

MAX_UPDATE_RETRIES = 8


@dataclass
class PlanFactBatch:
    """Related writes that must share the plan update's versioned transaction."""

    facts: list[FactRecord] = field(default_factory=list)
    expected_versions: dict[tuple[str, str], int | None] = field(default_factory=dict)


def _fact_id(lookup_key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"project_plan:{lookup_key}"))


def _pointer_value(plan: ProjectPlan) -> dict[str, Any]:
    counts = {status.value: 0 for status in PlanStepStatus}
    for step in plan.steps:
        counts[step.status.value] += 1
    terminal = {
        step.step_id
        for step in plan.steps
        if step.status in {PlanStepStatus.COMPLETED, PlanStepStatus.SKIPPED}
    }
    latest_revision = plan.revision_history[-1] if plan.revision_history else None
    return {
        "plan_id": plan.plan_id,
        "plan_lookup_key": plan_lookup_key(plan.plan_id),
        "revision": plan.revision,
        "latest_revision_reason": (
            latest_revision.reason if latest_revision is not None else ""
        ),
        "latest_revision_requested_by": (
            latest_revision.requested_by.value if latest_revision is not None else ""
        ),
        "title": plan.title,
        "objective": plan.objective,
        "intended_deliverable": plan.intended_deliverable,
        "status": plan.status.value,
        "step_counts": counts,
        "running_step_ids": [
            step.step_id
            for step in plan.steps
            if step.status is PlanStepStatus.RUNNING
        ],
        "next_step_ids": [
            step.step_id
            for step in plan.steps
            if step.status is PlanStepStatus.PENDING
            and all(dependency in terminal for dependency in step.depends_on)
        ],
        "blocked_step_ids": [
            step.step_id
            for step in plan.steps
            if step.status is PlanStepStatus.BLOCKED
        ],
        "updated_at": plan.updated_at,
    }


class PlanRepository:
    """Persist domain state through the registered project-plan writer only."""

    def __init__(self, memory: MemoryRepository) -> None:
        self.memory = memory

    def find_fact(self, lookup_key: str, *, include_retracted: bool = False) -> FactRecord | None:
        rows = self.memory.query_facts(
            kind=PROJECT_PLAN_FACT_KIND, lookup_key=lookup_key,
            include_retracted=include_retracted, limit=1,
        )
        return rows[0] if rows else None

    def active_pointer(self) -> FactRecord | None:
        return self.find_fact(ACTIVE_PLAN_LOOKUP_KEY)

    def pending_pointer(self, *, include_retracted: bool = False) -> FactRecord | None:
        return self.find_fact(PENDING_PLAN_LOOKUP_KEY, include_retracted=include_retracted)

    def find_conclusion(self, lookup_key: str) -> FactRecord | None:
        rows = self.memory.query_facts(kind="agent_conclusion", lookup_key=lookup_key, limit=1)
        return rows[0] if rows else None

    def commit(
        self,
        facts: list[FactRecord],
        *,
        expected_versions: dict[tuple[str, str], int | None],
        additional_changes: PlanFactBatch | None = None,
    ) -> list[FactRecord]:
        """Commit the plan, revision, pointers, and approval decision together.

        The shared memory writer validates every key and expected version before
        one SQLite transaction. It never retries or partly commits an approval.
        """
        records = list(facts)
        expected = dict(expected_versions)
        if additional_changes is not None:
            records.extend(additional_changes.facts)
            expected.update(additional_changes.expected_versions)
        return commit_facts(
            self.memory, records, expected_versions=expected, writer="project_plan",
        )

    def get_plan(self, plan_id: str | None = None) -> ProjectPlanEnvelope:
        if plan_id is None:
            pointer = self.active_pointer()
            if pointer is None:
                raise ValueError("No active project plan")
            resolved_plan_id = str(pointer.value.get("plan_id") or "").strip()
            if not resolved_plan_id:
                raise ValueError("Active project plan pointer is invalid")
        else:
            resolved_plan_id = plan_id.strip()

        lookup_key = plan_lookup_key(resolved_plan_id)
        fact = self.find_fact(lookup_key)
        if fact is None:
            raise ValueError(f"Project plan not found: {resolved_plan_id}")
        return ProjectPlanEnvelope(
            lookup_key=lookup_key,
            fact_id=fact.fact_id,
            memory_version=fact.version,
            plan=ProjectPlan.model_validate(fact.value),
        )

    def get_revision(self, plan_id: str, revision: int) -> ProjectPlanEnvelope:
        lookup_key = plan_revision_lookup_key(plan_id.strip(), revision)
        fact = self.find_fact(lookup_key)
        if fact is None:
            raise ValueError(f"Project plan revision not found: {plan_id} revision {revision}")
        return ProjectPlanEnvelope(
            lookup_key=lookup_key,
            fact_id=fact.fact_id,
            memory_version=fact.version,
            plan=ProjectPlan.model_validate(fact.value),
        )

    def get_draft(self, draft_id: str | None = None) -> ProjectPlanDraftEnvelope:
        if draft_id is None:
            pointer = self.pending_pointer()
            if pointer is None:
                raise ValueError("No project plan draft is awaiting approval")
            resolved_draft_id = str(pointer.value.get("draft_id") or "").strip()
            if not resolved_draft_id:
                raise ValueError("Pending project plan pointer is invalid")
        else:
            resolved_draft_id = draft_id.strip()
        lookup_key = plan_draft_lookup_key(resolved_draft_id)
        fact = self.find_fact(lookup_key)
        if fact is None:
            raise ValueError(f"Project plan draft not found: {resolved_draft_id}")
        return ProjectPlanDraftEnvelope(
            lookup_key=lookup_key,
            fact_id=fact.fact_id,
            memory_version=fact.version,
            draft=ProjectPlanDraft.model_validate(fact.value),
        )

    @staticmethod
    def plan_fact(plan: ProjectPlan, *, created_at: str) -> FactRecord:
        lookup_key = plan_lookup_key(plan.plan_id)
        return FactRecord(
            fact_id=_fact_id(lookup_key),
            kind=PROJECT_PLAN_FACT_KIND,
            lookup_key=lookup_key,
            value=plan.model_dump(mode="json"),
            evidence_kind=FactEvidenceKind.DETERMINISTIC_WORKFLOW,
            created_at=created_at,
            updated_at=plan.updated_at,
        )

    @staticmethod
    def revision_fact(plan: ProjectPlan, *, created_at: str) -> FactRecord:
        lookup_key = plan_revision_lookup_key(plan.plan_id, plan.revision)
        return FactRecord(
            fact_id=_fact_id(lookup_key),
            kind=PROJECT_PLAN_FACT_KIND,
            lookup_key=lookup_key,
            value=plan.model_dump(mode="json"),
            evidence_kind=FactEvidenceKind.DETERMINISTIC_WORKFLOW,
            created_at=created_at,
            updated_at=plan.updated_at,
        )

    @staticmethod
    def active_pointer_fact(plan: ProjectPlan, *, created_at: str) -> FactRecord:
        return FactRecord(
            fact_id=_fact_id(ACTIVE_PLAN_LOOKUP_KEY),
            kind=PROJECT_PLAN_FACT_KIND,
            lookup_key=ACTIVE_PLAN_LOOKUP_KEY,
            value=_pointer_value(plan),
            evidence_kind=FactEvidenceKind.DETERMINISTIC_WORKFLOW,
            created_at=created_at,
            updated_at=plan.updated_at,
        )

    @staticmethod
    def draft_fact(draft: ProjectPlanDraft, *, created_at: str) -> FactRecord:
        lookup_key = plan_draft_lookup_key(draft.draft_id)
        return FactRecord(
            fact_id=_fact_id(lookup_key),
            kind=PROJECT_PLAN_FACT_KIND,
            lookup_key=lookup_key,
            value=draft.model_dump(mode="json"),
            evidence_kind=FactEvidenceKind.DETERMINISTIC_WORKFLOW,
            created_at=created_at,
            updated_at=draft.updated_at,
        )

    @staticmethod
    def pending_pointer_fact(
        draft: ProjectPlanDraft,
        *,
        created_at: str,
        retracted: bool = False,
    ) -> FactRecord:
        plan = draft.proposed_plan
        return FactRecord(
            fact_id=_fact_id(PENDING_PLAN_LOOKUP_KEY),
            kind=PROJECT_PLAN_FACT_KIND,
            lookup_key=PENDING_PLAN_LOOKUP_KEY,
            value={
                "draft_id": draft.draft_id,
                "draft_lookup_key": plan_draft_lookup_key(draft.draft_id),
                "action": draft.action.value,
                "status": draft.status.value,
                "plan_id": plan.plan_id,
                "base_plan_id": draft.target_plan_id,
                "base_revision": draft.base_revision,
                "proposed_revision": plan.revision,
                "title": plan.title,
                "objective": plan.objective,
                "intended_deliverable": plan.intended_deliverable,
                "step_count": len(plan.steps),
                "step_ids": [step.step_id for step in plan.steps],
                "updated_at": draft.updated_at,
            },
            status=(FactStatus.RETRACTED if retracted else FactStatus.ACTIVE),
            evidence_kind=FactEvidenceKind.DETERMINISTIC_WORKFLOW,
            created_at=created_at,
            updated_at=draft.updated_at,
        )

