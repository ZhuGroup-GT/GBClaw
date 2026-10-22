"""Schemas, enums, and stable keys for project plans."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from project_memory.identifiers import ACTIVE_PLAN_LOOKUP_KEY, PENDING_PLAN_LOOKUP_KEY
from extensions import worker_definitions

PROJECT_PLAN_FACT_KIND = "project_plan"

_HOST_CAPABILITIES = ("orchestrator", "project_memory")
_INPUT_CAPABILITIES = ("user_input", "external_search")
_WORKER_CAPABILITIES = tuple(item.plan_capability for item in worker_definitions() if item.plan_capability)
_PLAN_CAPABILITIES = dict.fromkeys((*_HOST_CAPABILITIES, *_INPUT_CAPABILITIES, *_WORKER_CAPABILITIES))
# The same names as the planner reads them: host steps, workers in registry order, then inputs.
PLAN_CAPABILITY_NAMES = tuple(
    dict.fromkeys((*_HOST_CAPABILITIES, *_WORKER_CAPABILITIES, *_INPUT_CAPABILITIES))
)
PlanCapability = StrEnum(
    "PlanCapability", {key.upper(): key for key in _PLAN_CAPABILITIES}, module=__name__,
)


class PlanStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class ProjectPlanStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SUPERSEDED = "superseded"


class PlanRevisionAuthor(StrEnum):
    PLANNER = "planner"
    USER = "user"
    ORCHESTRATOR = "orchestrator"


class PlanStepDraft(BaseModel):
    step_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
    )
    title: str = Field(min_length=1, max_length=240)
    capability: PlanCapability
    description: str = Field(min_length=1, max_length=4000)
    depends_on: list[str] = Field(default_factory=list, max_length=30)
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected_output: str = Field(default="", max_length=4000)
    validation: str = Field(default="", max_length=4000)
    initially_blocked: bool = False
    blocking_reason: str = Field(default="", max_length=2000)


class ProjectPlanStep(BaseModel):
    step_id: str
    title: str
    capability: PlanCapability
    description: str
    depends_on: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected_output: str = ""
    validation: str = ""
    status: PlanStepStatus = PlanStepStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    result_summary: str = ""
    conclusion_ref: dict[str, Any] | None = None
    result_run_ids: list[str] = Field(default_factory=list)
    error: str = ""
    blocking_reason: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str


class ProjectPlanRevision(BaseModel):
    revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=4000)
    requested_by: PlanRevisionAuthor
    changed_fields: list[str] = Field(default_factory=list)
    prior_memory_version: int | None = Field(default=None, ge=1)
    source_draft_id: str | None = None
    created_at: str


class ProjectPlan(BaseModel):
    plan_id: str
    title: str = Field(min_length=1, max_length=240)
    objective: str = Field(min_length=1, max_length=8000)
    intended_deliverable: str = Field(default="", max_length=4000)
    status: ProjectPlanStatus
    known_evidence: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    steps: list[ProjectPlanStep]
    risks: list[str] = Field(default_factory=list)
    user_decisions_required: list[str] = Field(default_factory=list)
    request_id: str
    chat_thread_id: str | None = None
    revision: int = Field(default=1, ge=1)
    revision_history: list[ProjectPlanRevision] = Field(default_factory=list)
    superseded_by_plan_id: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None


class ProjectPlanEnvelope(BaseModel):
    lookup_key: str
    fact_id: str
    memory_version: int
    plan: ProjectPlan


class PlanDraftAction(StrEnum):
    CREATE = "create"
    REVISE = "revise"


class PlanDraftStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ProjectPlanDraft(BaseModel):
    draft_id: str
    action: PlanDraftAction
    status: PlanDraftStatus = PlanDraftStatus.PENDING
    request_id: str
    chat_thread_id: str | None = None
    target_plan_id: str | None = None
    base_revision: int | None = Field(default=None, ge=1)
    base_memory_version: int | None = Field(default=None, ge=1)
    revision_reason: str = ""
    user_request: str = ""
    requested_by: PlanRevisionAuthor = PlanRevisionAuthor.USER
    title: str | None = None
    objective: str | None = None
    intended_deliverable: str | None = None
    steps: list[PlanStepDraft] | None = None
    known_evidence: list[str] | None = None
    assumptions: list[str] | None = None
    unknowns: list[str] | None = None
    risks: list[str] | None = None
    user_decisions_required: list[str] | None = None
    proposed_plan: ProjectPlan
    approved_plan_id: str | None = None
    approved_revision: int | None = Field(default=None, ge=1)
    decision_feedback: str = ""
    created_at: str
    updated_at: str
    decided_at: str | None = None


class ProjectPlanDraftEnvelope(BaseModel):
    lookup_key: str
    fact_id: str
    memory_version: int
    draft: ProjectPlanDraft


def plan_lookup_key(plan_id: str) -> str:
    value = plan_id.strip()
    if not value or len(value) > 80 or not value.startswith("plan-"):
        raise ValueError(f"Invalid plan_id: {plan_id!r}")
    return f"plan:{value}"


def plan_revision_lookup_key(plan_id: str, revision: int) -> str:
    plan_lookup_key(plan_id)
    if revision < 1:
        raise ValueError("Plan revision must be at least 1")
    return f"plan:{plan_id}:revision:{revision}"


def plan_draft_lookup_key(draft_id: str) -> str:
    value = draft_id.strip()
    if not value or len(value) > 80 or not value.startswith("draft-"):
        raise ValueError(f"Invalid draft_id: {draft_id!r}")
    return f"plan_draft:{value}"


__all__ = [
    "ACTIVE_PLAN_LOOKUP_KEY",
    "PENDING_PLAN_LOOKUP_KEY",
    "PROJECT_PLAN_FACT_KIND",
    "PLAN_CAPABILITY_NAMES",
    "PlanCapability",
    "PlanDraftAction",
    "PlanDraftStatus",
    "PlanRevisionAuthor",
    "PlanStepDraft",
    "PlanStepStatus",
    "ProjectPlan",
    "ProjectPlanDraft",
    "ProjectPlanDraftEnvelope",
    "ProjectPlanEnvelope",
    "ProjectPlanRevision",
    "ProjectPlanStatus",
    "ProjectPlanStep",
    "plan_draft_lookup_key",
    "plan_lookup_key",
    "plan_revision_lookup_key",
]
