"""Validated persistence and execution-state transitions for approved project plans."""

from __future__ import annotations

import uuid
from typing import Any

from project_memory.models import FactRecord
from project_memory.storage.facts import FactVersionConflict
from project_workspace.models import utc_now_iso
from planning.models import (
    PlanCapability,
    PlanRevisionAuthor,
    PlanStepDraft,
    PlanStepStatus,
    ProjectPlan,
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
    validate_transition,
)
from planning.views import (
    save_project_plan_revision_view,
    save_project_plan_views,
)
from project_workspace.store import ProjectStore


class ProjectPlanService:
    def __init__(self, store: ProjectStore, project_id: str) -> None:
        self.store = store
        self.project_id = project_id
        self.repository = PlanRepository(store.memory(project_id))

    def get_plan(self, plan_id: str | None = None) -> ProjectPlanEnvelope:
        envelope = self.repository.get_plan(plan_id)
        if plan_id is None:
            self._save_views(envelope)
        return envelope

    def get_revision(self, plan_id: str, revision: int) -> ProjectPlanEnvelope:
        envelope = self.repository.get_revision(plan_id, revision)
        try:
            save_project_plan_revision_view(
                self.store.project_path(self.project_id),
                envelope.model_dump(mode="json"),
            )
        except OSError:
            pass
        return envelope

    def create_plan(
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
        source_draft_id: str | None = None,
        additional_changes: PlanFactBatch | None = None,
    ) -> ProjectPlanEnvelope:
        normalized_request_id = request_id.strip()
        if not normalized_request_id:
            raise ValueError("request_id must not be empty")
        if not steps:
            raise ValueError("A project plan must contain at least one step")
        if len(steps) > 50:
            raise ValueError("A project plan may contain at most 50 steps")
        validate_step_drafts(steps)

        plan_id = "plan-" + uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{self.project_id}:{normalized_request_id}",
        ).hex[:16]
        try:
            return self.get_plan(plan_id)
        except ValueError:
            pass

        now = utc_now_iso()
        plan_steps = [new_step(draft, now=now) for draft in steps]
        plan = ProjectPlan(
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
                    reason="Initial plan created",
                    requested_by=PlanRevisionAuthor.PLANNER,
                    changed_fields=["initial_plan"],
                    source_draft_id=source_draft_id,
                    created_at=now,
                )
            ],
            created_at=now,
            updated_at=now,
        )

        for _attempt in range(MAX_UPDATE_RETRIES):
            try:
                return self.get_plan(plan_id)
            except ValueError:
                pass
            pointer = self.repository.active_pointer()

            facts: list[FactRecord] = []
            expected: dict[tuple[str, str], int | None] = {}
            superseded_plan_id: str | None = None

            if pointer is not None:
                previous_id = str(pointer.value.get("plan_id") or "").strip()
                if previous_id and previous_id != plan_id:
                    previous = self.get_plan(previous_id)
                    if any(
                        step.status is PlanStepStatus.RUNNING
                        for step in previous.plan.steps
                    ):
                        raise ValueError(
                            f"Cannot supersede plan {previous_id} while it has running steps"
                        )
                    if previous.plan.status is not ProjectPlanStatus.COMPLETED:
                        superseded_plan_id = previous_id
                        previous_plan = previous.plan.model_copy(
                            update={
                                "status": ProjectPlanStatus.SUPERSEDED,
                                "superseded_by_plan_id": plan_id,
                                "updated_at": now,
                            }
                        )
                        previous_fact = self.repository.plan_fact(
                            previous_plan,
                            created_at=previous.plan.created_at,
                        )
                        facts.append(previous_fact)
                        expected[(previous_fact.kind, previous_fact.lookup_key)] = (
                            previous.memory_version
                        )

            plan_fact = self.repository.plan_fact(plan, created_at=now)
            revision_fact = self.repository.revision_fact(plan, created_at=now)
            pointer_fact = self.repository.active_pointer_fact(
                plan,
                created_at=pointer.created_at if pointer is not None else now,
            )
            facts.extend((plan_fact, revision_fact, pointer_fact))
            expected[(plan_fact.kind, plan_fact.lookup_key)] = None
            expected[(revision_fact.kind, revision_fact.lookup_key)] = None
            expected[(pointer_fact.kind, pointer_fact.lookup_key)] = (
                pointer.version if pointer is not None else None
            )
            try:
                self.repository.commit(
                    facts,
                    expected_versions=expected,
                    additional_changes=additional_changes,
                )
                created = self.get_plan(plan_id)
                if superseded_plan_id is not None:
                    self._save_views(
                        self.get_plan(superseded_plan_id),
                        save_active_view=False,
                    )
                self._save_views(created, save_revision=True)
                return created
            except FactVersionConflict:
                continue
        raise FactVersionConflict("Project plan creation exceeded retry limit")

    def revise_plan(
        self,
        plan_id: str,
        *,
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
        base_revision: int | None = None,
        source_draft_id: str | None = None,
        additional_changes: PlanFactBatch | None = None,
    ) -> ProjectPlanEnvelope:
        """Create an audited structural revision while preserving execution history."""
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("A plan revision requires a reason")
        author = PlanRevisionAuthor(requested_by)
        if steps is not None:
            if not steps:
                raise ValueError("A revised project plan must contain at least one step")
            if len(steps) > 50:
                raise ValueError("A project plan may contain at most 50 steps")
            validate_step_drafts(steps)

        for _attempt in range(MAX_UPDATE_RETRIES):
            envelope = self.get_plan(plan_id)
            plan = envelope.plan
            if plan.status is ProjectPlanStatus.SUPERSEDED:
                raise ValueError(f"Cannot revise superseded plan {plan_id}")
            if base_revision is not None and plan.revision != base_revision:
                raise FactVersionConflict(
                    f"Plan {plan_id} structural revision changed: expected "
                    f"{base_revision}, got {plan.revision}"
                )

            now = utc_now_iso()
            next_plan, _ = build_revision(
                envelope, source_draft_id=source_draft_id, reason=normalized_reason,
                requested_by=author, title=title, objective=objective,
                intended_deliverable=intended_deliverable, steps=steps,
                known_evidence=known_evidence, assumptions=assumptions, unknowns=unknowns,
                risks=risks, user_decisions_required=user_decisions_required, now=now,
            )

            pointer = self.repository.active_pointer()
            if pointer is None or pointer.value.get("plan_id") != plan_id:
                raise ValueError(f"Plan {plan_id} is no longer the active project plan")

            plan_fact = self.repository.plan_fact(next_plan, created_at=plan.created_at)
            revision_fact = self.repository.revision_fact(next_plan, created_at=now)
            pointer_fact = self.repository.active_pointer_fact(next_plan, created_at=pointer.created_at)
            facts = [plan_fact, revision_fact, pointer_fact]
            expected_versions = {
                (plan_fact.kind, plan_fact.lookup_key): envelope.memory_version,
                (revision_fact.kind, revision_fact.lookup_key): None,
                (pointer_fact.kind, pointer_fact.lookup_key): pointer.version,
            }
            try:
                self.repository.commit(
                    facts,
                    expected_versions=expected_versions,
                    additional_changes=additional_changes,
                )
                revised = self.get_plan(plan_id)
                self._save_views(revised, save_revision=True)
                return revised
            except FactVersionConflict:
                continue
        raise FactVersionConflict("Project plan revision exceeded retry limit")

    def transition_step(
        self,
        plan_id: str,
        step_id: str,
        status: PlanStepStatus | str,
        *,
        capability: PlanCapability | str | None = None,
        result_summary: str = "",
        result_run_ids: list[str] | None = None,
        error: str = "",
        blocking_reason: str = "",
    ) -> ProjectPlanEnvelope:
        target = PlanStepStatus(status)
        expected_capability = PlanCapability(capability) if capability is not None else None
        normalized_step_id = step_id.strip()
        normalized_result_run_ids = list(
            dict.fromkeys(
                run_id.strip()
                for run_id in (result_run_ids or [])
                if isinstance(run_id, str) and run_id.strip()
            )
        )
        now = utc_now_iso()

        for _attempt in range(MAX_UPDATE_RETRIES):
            envelope = self.get_plan(plan_id)
            plan = envelope.plan
            if plan.status is ProjectPlanStatus.SUPERSEDED:
                raise ValueError(f"Cannot update superseded plan {plan_id}")
            indexes = {
                step.step_id: index for index, step in enumerate(plan.steps)
            }
            if normalized_step_id not in indexes:
                raise ValueError(
                    f"Step {normalized_step_id!r} is not part of plan {plan_id}"
                )
            index = indexes[normalized_step_id]
            current = plan.steps[index]
            if expected_capability is not None and current.capability is not expected_capability:
                raise ValueError(
                    f"Step {normalized_step_id} requires capability "
                    f"{current.capability.value}, not {expected_capability.value}"
                )
            same_running_with_progress = (
                current.status is target
                and target is PlanStepStatus.RUNNING
                and (
                    (
                        bool(result_summary.strip())
                        and result_summary.strip() != current.result_summary
                    )
                    or any(
                        run_id not in current.result_run_ids
                        for run_id in normalized_result_run_ids
                    )
                )
            )
            if current.status is target and not same_running_with_progress:
                return envelope
            if current.status is not target:
                validate_transition(current.status, target)

            if target in {PlanStepStatus.RUNNING, PlanStepStatus.COMPLETED}:
                by_id = {step.step_id: step for step in plan.steps}
                unresolved = [
                    dependency
                    for dependency in current.depends_on
                    if by_id[dependency].status
                    not in {PlanStepStatus.COMPLETED, PlanStepStatus.SKIPPED}
                ]
                if unresolved:
                    raise ValueError(
                        f"Step {normalized_step_id} has unresolved dependencies: "
                        + ", ".join(unresolved)
                    )

            updates: dict[str, Any] = {"status": target, "updated_at": now}
            if target is PlanStepStatus.RUNNING:
                if current.status is PlanStepStatus.RUNNING:
                    updates.update(
                        {
                            "attempts": current.attempts,
                            "started_at": current.started_at or now,
                            "completed_at": None,
                            "result_summary": (
                                result_summary.strip() or current.result_summary
                            ),
                            "result_run_ids": list(
                                dict.fromkeys(
                                    [
                                        *current.result_run_ids,
                                        *normalized_result_run_ids,
                                    ]
                                )
                            ),
                            "error": "",
                            "blocking_reason": "",
                        }
                    )
                else:
                    updates.update(
                        {
                            "attempts": current.attempts + 1,
                            "started_at": now,
                            "completed_at": None,
                            "result_summary": result_summary.strip(),
                            "result_run_ids": list(
                                dict.fromkeys(normalized_result_run_ids)
                            ),
                            "error": "",
                            "blocking_reason": "",
                        }
                    )
            elif target is PlanStepStatus.COMPLETED:
                updates.update(
                    {
                        "completed_at": now,
                        "result_summary": result_summary.strip() or current.result_summary,
                        "result_run_ids": list(
                            dict.fromkeys(
                                [*current.result_run_ids, *normalized_result_run_ids]
                            )
                        ),
                        "error": "",
                        "blocking_reason": "",
                    }
                )
            elif target is PlanStepStatus.FAILED:
                updates.update(
                    {
                        "completed_at": now,
                        "result_summary": result_summary.strip() or current.result_summary,
                        "result_run_ids": list(
                            dict.fromkeys(
                                [*current.result_run_ids, *normalized_result_run_ids]
                            )
                        ),
                        "error": error.strip() or "Plan step failed",
                        "blocking_reason": "",
                    }
                )
            elif target is PlanStepStatus.BLOCKED:
                updates.update(
                    {
                        "completed_at": None,
                        "blocking_reason": blocking_reason.strip() or "Plan step is blocked",
                        "error": "",
                    }
                )
            elif target is PlanStepStatus.SKIPPED:
                updates.update(
                    {
                        "completed_at": now,
                        "result_summary": result_summary.strip(),
                        "blocking_reason": blocking_reason.strip(),
                        "error": "",
                    }
                )
            elif target is PlanStepStatus.PENDING:
                updates.update(
                    {
                        "completed_at": None,
                        "result_summary": "",
                        "result_run_ids": [],
                        "error": "",
                        "blocking_reason": "",
                    }
                )

            next_steps = list(plan.steps)
            next_steps[index] = current.model_copy(update=updates)
            next_status = derive_status(next_steps)
            next_plan = plan.model_copy(
                update={
                    "steps": next_steps,
                    "status": next_status,
                    "updated_at": now,
                    "completed_at": (
                        now if next_status is ProjectPlanStatus.COMPLETED else None
                    ),
                }
            )

            pointer = self.repository.active_pointer()
            if pointer is None or pointer.value.get("plan_id") != plan_id:
                raise ValueError(f"Plan {plan_id} is no longer the active project plan")

            plan_fact = self.repository.plan_fact(next_plan, created_at=plan.created_at)
            pointer_fact = self.repository.active_pointer_fact(
                next_plan,
                created_at=pointer.created_at,
            )
            try:
                self.repository.commit(
                    [plan_fact, pointer_fact],
                    expected_versions={
                        (plan_fact.kind, plan_fact.lookup_key): envelope.memory_version,
                        (pointer_fact.kind, pointer_fact.lookup_key): pointer.version,
                    },
                )
                updated = self.get_plan(plan_id)
                self._save_views(updated)
                return updated
            except FactVersionConflict:
                continue
        raise FactVersionConflict("Project plan update exceeded retry limit")

    def attach_conclusion(self, plan_id: str, step_id: str, *, conclusion_ref: dict,
                          summary: str) -> ProjectPlanEnvelope:
        """Attach an indexed delegate conclusion without changing execution status.

        This neither approves nor starts a plan; the deterministic tracker has
        already processed the worker's complete result and scientific statuses.
        """
        conclusion = self.repository.find_conclusion(str(conclusion_ref.get("lookup_key") or ""))
        if (conclusion is None or conclusion.value.get("conclusion_id") != conclusion_ref.get("conclusion_id")
                or conclusion.value.get("plan_id") != plan_id or conclusion.value.get("step_id") != step_id):
            raise ValueError("Conclusion must be indexed for this exact plan and step")
        for _attempt in range(MAX_UPDATE_RETRIES):
            envelope = self.get_plan(plan_id)
            plan = envelope.plan
            indexes = {step.step_id: index for index, step in enumerate(plan.steps)}
            if step_id not in indexes:
                raise ValueError("Conclusion step is not part of the plan")
            index = indexes[step_id]
            current = plan.steps[index]
            if current.conclusion_ref == conclusion_ref and current.result_summary == summary[:800]:
                return envelope
            steps = list(plan.steps)
            steps[index] = current.model_copy(update={"conclusion_ref": conclusion_ref,
                "result_summary": summary[:800], "updated_at": utc_now_iso()})
            next_plan = plan.model_copy(update={"steps": steps, "updated_at": utc_now_iso()})
            fact = self.repository.plan_fact(next_plan, created_at=plan.created_at)
            try:
                self.repository.commit(
                    [fact],
                    expected_versions={
                        (fact.kind, fact.lookup_key): envelope.memory_version,
                    },
                )
                updated = self.get_plan(plan_id)
                self._save_views(updated)
                return updated
            except FactVersionConflict:
                continue
        raise FactVersionConflict("Plan conclusion attachment exceeded retry limit")

    def _save_views(
        self,
        envelope: ProjectPlanEnvelope,
        *,
        save_revision: bool = False,
        save_active_view: bool = True,
    ) -> None:
        try:
            save_project_plan_views(
                self.store.project_path(self.project_id),
                envelope.model_dump(mode="json"),
                save_active_view=save_active_view,
            )
            if save_revision:
                self.get_revision(
                    envelope.plan.plan_id,
                    envelope.plan.revision,
                )
        except OSError:
            # SQLite is authoritative. A later plan read/update can rebuild derived views.
            pass


__all__ = ["ProjectPlanService"]
