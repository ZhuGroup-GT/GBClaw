"""Pure plan validation and revision rules shared by proposals and execution."""

from __future__ import annotations

from typing import Any

from planning.models import (
    PlanRevisionAuthor, PlanStepDraft, PlanStepStatus, ProjectPlan,
    ProjectPlanEnvelope, ProjectPlanRevision, ProjectPlanStatus, ProjectPlanStep,
)


def derive_status(steps: list[ProjectPlanStep]) -> ProjectPlanStatus:
    if all(
        step.status in {PlanStepStatus.COMPLETED, PlanStepStatus.SKIPPED}
        for step in steps
    ):
        return ProjectPlanStatus.COMPLETED
    if any(step.status is PlanStepStatus.FAILED for step in steps):
        return ProjectPlanStatus.FAILED
    if any(step.status is PlanStepStatus.RUNNING for step in steps):
        return ProjectPlanStatus.ACTIVE

    completed = {
        step.step_id
        for step in steps
        if step.status in {PlanStepStatus.COMPLETED, PlanStepStatus.SKIPPED}
    }
    if any(
        step.status is PlanStepStatus.PENDING
        and all(dependency in completed for dependency in step.depends_on)
        for step in steps
    ):
        return ProjectPlanStatus.ACTIVE
    return ProjectPlanStatus.BLOCKED


def validate_step_drafts(steps: list[PlanStepDraft]) -> None:
    seen: set[str] = set()
    for step in steps:
        if step.step_id in seen:
            raise ValueError(f"Duplicate plan step_id: {step.step_id}")
        missing = [dependency for dependency in step.depends_on if dependency not in seen]
        if missing:
            raise ValueError(
                f"Step {step.step_id} dependencies must refer to earlier steps: "
                + ", ".join(missing)
            )
        if step.initially_blocked and not step.blocking_reason.strip():
            raise ValueError(
                f"Initially blocked step {step.step_id} requires blocking_reason"
            )
        seen.add(step.step_id)


def step_definition(step: ProjectPlanStep) -> dict[str, Any]:
    return {
        "step_id": step.step_id,
        "title": step.title,
        "capability": step.capability,
        "description": step.description,
        "depends_on": step.depends_on,
        "inputs": step.inputs,
        "expected_output": step.expected_output,
        "validation": step.validation,
    }


def merge_revised_steps(
    current_steps: list[ProjectPlanStep],
    drafts: list[PlanStepDraft],
    *,
    now: str,
) -> list[ProjectPlanStep]:
    current_by_id = {step.step_id: step for step in current_steps}
    draft_by_id = {step.step_id: step for step in drafts}
    protected_statuses = {
        PlanStepStatus.RUNNING,
        PlanStepStatus.COMPLETED,
        PlanStepStatus.FAILED,
        PlanStepStatus.SKIPPED,
    }

    def is_protected(step: ProjectPlanStep) -> bool:
        return bool(
            step.status in protected_statuses
            or step.attempts
            or step.started_at
            or step.completed_at
            or step.result_run_ids
            or step.result_summary
            or step.error
        )

    missing_protected = [
        step.step_id
        for step in current_steps
        if is_protected(step) and step.step_id not in draft_by_id
    ]
    if missing_protected:
        raise ValueError(
            "A revision cannot remove running or historical steps: "
            + ", ".join(missing_protected)
        )

    revised: list[ProjectPlanStep] = []
    for draft in drafts:
        definition = draft.model_dump(
            exclude={"initially_blocked", "blocking_reason"}
        )
        current = current_by_id.get(draft.step_id)
        if current is None:
            revised.append(new_step(draft, now=now))
            continue

        if is_protected(current):
            if step_definition(current) != definition:
                raise ValueError(
                    f"A revision cannot rewrite running or historical step "
                    f"{current.step_id}"
                )
            revised.append(current)
            continue

        target_status = (
            PlanStepStatus.BLOCKED
            if draft.initially_blocked
            else PlanStepStatus.PENDING
        )
        target_blocking_reason = (
            draft.blocking_reason if draft.initially_blocked else ""
        )
        if (
            step_definition(current) == definition
            and current.status is target_status
            and current.blocking_reason == target_blocking_reason
        ):
            revised.append(current)
            continue
        revised.append(
            current.model_copy(
                update={
                    **definition,
                    "status": target_status,
                    "blocking_reason": target_blocking_reason,
                    "error": "",
                    "completed_at": None,
                    "updated_at": now,
                }
            )
        )
    return revised


def validate_transition(current: PlanStepStatus, target: PlanStepStatus) -> None:
    allowed = {
        PlanStepStatus.PENDING: {
            PlanStepStatus.RUNNING,
            PlanStepStatus.COMPLETED,
            PlanStepStatus.BLOCKED,
            PlanStepStatus.SKIPPED,
        },
        PlanStepStatus.RUNNING: {
            PlanStepStatus.COMPLETED,
            PlanStepStatus.FAILED,
            PlanStepStatus.BLOCKED,
        },
        PlanStepStatus.FAILED: {
            PlanStepStatus.PENDING,
            PlanStepStatus.RUNNING,
            PlanStepStatus.SKIPPED,
        },
        PlanStepStatus.BLOCKED: {
            PlanStepStatus.PENDING,
            PlanStepStatus.RUNNING,
            PlanStepStatus.COMPLETED,
            PlanStepStatus.SKIPPED,
        },
        PlanStepStatus.COMPLETED: set(),
        PlanStepStatus.SKIPPED: set(),
    }
    if target not in allowed[current]:
        raise ValueError(
            f"Plan step cannot transition from {current.value} to {target.value}"
        )


def new_step(draft: PlanStepDraft, *, now: str) -> ProjectPlanStep:
    return ProjectPlanStep(
        **draft.model_dump(exclude={"initially_blocked", "blocking_reason"}),
        status=(
            PlanStepStatus.BLOCKED
            if draft.initially_blocked
            else PlanStepStatus.PENDING
        ),
        blocking_reason=(draft.blocking_reason if draft.initially_blocked else ""),
        updated_at=now,
    )


def build_revision(
    base: ProjectPlanEnvelope,
    *,
    source_draft_id: str | None,
    reason: str,
    requested_by: PlanRevisionAuthor,
    title: str | None,
    objective: str | None,
    intended_deliverable: str | None,
    steps: list[PlanStepDraft] | None,
    known_evidence: list[str] | None,
    assumptions: list[str] | None,
    unknowns: list[str] | None,
    risks: list[str] | None,
    user_decisions_required: list[str] | None,
    now: str,
    no_changes_message: str = "Plan revision does not change any plan field",
) -> tuple[ProjectPlan, list[str]]:
    """Build the same revision for draft preview and approval without I/O.

    Callers validate the request and authorize the operation. Time is supplied
    explicitly so plan/step changes and the revision audit share one timestamp.
    """
    plan = base.plan
    next_steps = (
        merge_revised_steps(plan.steps, steps, now=now)
        if steps is not None
        else list(plan.steps)
    )
    changed_fields: list[str] = []
    updates: dict[str, Any] = {}
    values = {
        "title": title,
        "objective": objective,
        "intended_deliverable": intended_deliverable,
        "known_evidence": known_evidence,
        "assumptions": assumptions,
        "unknowns": unknowns,
        "risks": risks,
        "user_decisions_required": user_decisions_required,
    }
    for field_name, raw_value in values.items():
        if raw_value is None:
            continue
        value = raw_value.strip() if isinstance(raw_value, str) else list(raw_value)
        if getattr(plan, field_name) != value:
            changed_fields.append(field_name)
            updates[field_name] = value
    if steps is not None and next_steps != plan.steps:
        changed_fields.append("steps")
        updates["steps"] = next_steps
    if not changed_fields:
        raise ValueError(no_changes_message)
    next_revision = plan.revision + 1
    next_status = derive_status(next_steps)
    updates.update(
        {
            "revision": next_revision,
            "revision_history": [
                *plan.revision_history,
                ProjectPlanRevision(
                    revision=next_revision,
                    reason=reason,
                    requested_by=requested_by,
                    changed_fields=changed_fields,
                    prior_memory_version=base.memory_version,
                    source_draft_id=source_draft_id,
                    created_at=now,
                ),
            ],
            "status": next_status,
            "updated_at": now,
            "completed_at": (
                now if next_status is ProjectPlanStatus.COMPLETED else None
            ),
        }
    )
    return (
        ProjectPlan.model_validate(
            {**plan.model_dump(mode="python"), **updates}
        ),
        changed_fields,
    )
