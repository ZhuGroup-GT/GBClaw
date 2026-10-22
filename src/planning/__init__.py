"""Public API for durable, approval-gated project plans.

Callers should import from this package rather than its internal modules so the
models and persistence services can evolve independently.
"""

from planning.models import (
    ACTIVE_PLAN_LOOKUP_KEY,
    PENDING_PLAN_LOOKUP_KEY,
    PROJECT_PLAN_FACT_KIND,
    PlanCapability,
    PlanDraftAction,
    PlanDraftStatus,
    PlanRevisionAuthor,
    PlanStepDraft,
    PlanStepStatus,
    ProjectPlan,
    ProjectPlanDraft,
    ProjectPlanDraftEnvelope,
    ProjectPlanEnvelope,
    ProjectPlanRevision,
    ProjectPlanStatus,
    ProjectPlanStep,
    plan_draft_lookup_key,
    plan_lookup_key,
    plan_revision_lookup_key,
)
from importlib import import_module


_SERVICES = {
    "ProjectPlanService": "planning.service",
    "ProjectPlanDraftService": "planning.draft_service",
}


def __getattr__(name):
    if name not in _SERVICES:
        raise AttributeError(name)
    value = getattr(import_module(_SERVICES[name]), name)
    globals()[name] = value
    return value


__all__ = [
    "ACTIVE_PLAN_LOOKUP_KEY",
    "PENDING_PLAN_LOOKUP_KEY",
    "PROJECT_PLAN_FACT_KIND",
    "PlanCapability",
    "PlanDraftAction",
    "PlanDraftStatus",
    "PlanRevisionAuthor",
    "PlanStepDraft",
    "PlanStepStatus",
    "ProjectPlan",
    "ProjectPlanDraft",
    "ProjectPlanDraftEnvelope",
    "ProjectPlanDraftService",
    "ProjectPlanEnvelope",
    "ProjectPlanRevision",
    "ProjectPlanService",
    "ProjectPlanStatus",
    "ProjectPlanStep",
    "plan_draft_lookup_key",
    "plan_lookup_key",
    "plan_revision_lookup_key",
]
