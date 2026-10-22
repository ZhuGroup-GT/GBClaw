"""LangChain tools for durable, versioned project plans."""

from __future__ import annotations

import json
import threading

from langchain_core.tools import BaseTool, tool

from core.util.graph_config import runtime_ids
from planning import (
    PlanRevisionAuthor,
    PlanStepDraft,
    PlanStepStatus,
    ProjectPlanDraftEnvelope,
    ProjectPlanDraftService,
    ProjectPlanEnvelope,
    ProjectPlanService,
)
from project_workspace.store import ProjectStore


class ProjectPlanWriteGuard:
    """Allow exactly one successful create/revise write in one planning invocation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reserved_action: str | None = None
        self._completed_action: str | None = None

    def begin(self, action: str) -> None:
        with self._lock:
            existing = self._completed_action or self._reserved_action
            if existing is not None:
                raise RuntimeError(
                    f"Planning invocation already used its plan-write slot for {existing}"
                )
            self._reserved_action = action

    def finish(self, action: str, *, succeeded: bool) -> None:
        with self._lock:
            if self._reserved_action != action:
                return
            self._reserved_action = None
            if succeeded:
                self._completed_action = action


def _json_result(envelope: ProjectPlanEnvelope) -> str:
    return json.dumps(
        envelope.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
    )


def _json_draft_result(envelope: ProjectPlanDraftEnvelope) -> str:
    return json.dumps(
        envelope.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
    )


def make_create_project_plan_tool(
    store: ProjectStore,
    *,
    request_id: str,
    write_guard: ProjectPlanWriteGuard | None = None,
    user_request: str = "",
) -> BaseTool:
    """Create the planner-only draft persistence tool for one delegate request."""

    @tool
    def create_project_plan_draft(
        title: str,
        objective: str,
        steps: list[PlanStepDraft],
        intended_deliverable: str = "",
        known_evidence: list[str] | None = None,
        assumptions: list[str] | None = None,
        unknowns: list[str] | None = None,
        risks: list[str] | None = None,
        user_decisions_required: list[str] | None = None,
    ) -> str:
        """Persist one structured plan draft for explicit user approval.

        Call exactly once after the plan has been fully analyzed. Step IDs must be unique,
        lowercase stable IDs such as ``step-01``. Dependencies may reference only earlier steps.
        Capability must be one of orchestrator, project_memory, csl, lammps, analysis, coding,
        user_input, or external_search. Mark a step initially_blocked only when it cannot proceed,
        and provide its blocking_reason. This does not activate or execute the plan. The returned
        draft_id must be shown to the user and explicitly approved before any planned execution.
        """
        project_id, chat_thread_id = runtime_ids()
        if not project_id:
            return "No active project_id in run config."
        write_succeeded = False
        try:
            if write_guard is not None:
                write_guard.begin("create_project_plan_draft")
            envelope = ProjectPlanDraftService(store, project_id).propose_new_plan(
                request_id=request_id,
                title=title,
                objective=objective,
                steps=steps,
                intended_deliverable=intended_deliverable,
                known_evidence=known_evidence,
                assumptions=assumptions,
                unknowns=unknowns,
                risks=risks,
                user_decisions_required=user_decisions_required,
                chat_thread_id=chat_thread_id,
                user_request=user_request,
            )
            write_succeeded = True
        except (ValueError, RuntimeError) as exc:
            return f"Project plan draft creation failed: {exc}"
        finally:
            if write_guard is not None:
                write_guard.finish(
                    "create_project_plan_draft",
                    succeeded=write_succeeded,
                )
        return _json_draft_result(envelope)

    return create_project_plan_draft


def make_project_plan_tools(
    store: ProjectStore,
    *,
    include_revision_tool: bool = False,
    write_guard: ProjectPlanWriteGuard | None = None,
    draft_request_id: str | None = None,
    user_request: str = "",
) -> list[BaseTool]:
    """Create exact plan tools with an explicit planning-only revision boundary."""

    @tool
    def get_project_plan(
        plan_id: str | None = None,
        revision: int | None = None,
    ) -> str:
        """Read the exact active or named structured project plan.

        Omit plan_id to resolve ``project:active_plan``. The result contains every step's stable
        ID, dependency, capability, status, attempts, timestamps, run IDs, and result/error fields.
        Use this before resuming or revising a plan, or when the working summary may be stale.
        Set both plan_id and revision to inspect an immutable structural-revision snapshot.
        """
        project_id, _chat_thread_id = runtime_ids()
        if not project_id:
            return "No active project_id in run config."
        try:
            service = ProjectPlanService(store, project_id)
            if revision is not None:
                if plan_id is None:
                    return "plan_id is required when revision is specified"
                return _json_result(service.get_revision(plan_id, revision))
            return _json_result(service.get_plan(plan_id))
        except (ValueError, RuntimeError) as exc:
            return str(exc)

    @tool
    def revise_project_plan_draft(
        plan_id: str,
        reason: str,
        requested_by: PlanRevisionAuthor = PlanRevisionAuthor.USER,
        title: str | None = None,
        objective: str | None = None,
        intended_deliverable: str | None = None,
        steps: list[PlanStepDraft] | None = None,
        known_evidence: list[str] | None = None,
        assumptions: list[str] | None = None,
        unknowns: list[str] | None = None,
        risks: list[str] | None = None,
        user_decisions_required: list[str] | None = None,
    ) -> str:
        """Persist an audited structural-revision draft for user approval.

        Use this when the user changes the objective, assumptions, deliverable, or future work.
        First call get_project_plan and explain the user's reason exactly. Omitted fields remain
        unchanged; supplying steps replaces the step definition list. That replacement must retain
        every running or historically attempted step with the same ID and definition, while
        untouched pending or blocked steps may be edited, removed, reordered, or supplemented.
        Runtime status, attempts, results, and run IDs are preserved. A material change of
        scientific objective should usually become a new draft via create_project_plan_draft
        instead of rewriting this one. This tool never activates the revision.
        """
        project_id, chat_thread_id = runtime_ids()
        if not project_id:
            return "No active project_id in run config."
        if not draft_request_id:
            return "Project plan revision draft failed: no request-scoped draft ID"
        write_succeeded = False
        try:
            if write_guard is not None:
                write_guard.begin("revise_project_plan_draft")
            envelope = ProjectPlanDraftService(store, project_id).propose_revision(
                plan_id,
                request_id=draft_request_id,
                reason=reason,
                requested_by=requested_by,
                title=title,
                objective=objective,
                intended_deliverable=intended_deliverable,
                steps=steps,
                known_evidence=known_evidence,
                assumptions=assumptions,
                unknowns=unknowns,
                risks=risks,
                user_decisions_required=user_decisions_required,
                chat_thread_id=chat_thread_id,
                user_request=user_request,
            )
            write_succeeded = True
        except (ValueError, RuntimeError) as exc:
            return f"Project plan revision draft failed: {exc}"
        finally:
            if write_guard is not None:
                write_guard.finish(
                    "revise_project_plan_draft",
                    succeeded=write_succeeded,
                )
        return _json_draft_result(envelope)

    @tool
    def get_project_plan_draft(draft_id: str | None = None) -> str:
        """Read the exact pending or named project-plan draft and proposed full plan."""
        project_id, _chat_thread_id = runtime_ids()
        if not project_id:
            return "No active project_id in run config."
        try:
            return _json_draft_result(
                ProjectPlanDraftService(store, project_id).get_draft(draft_id)
            )
        except (ValueError, RuntimeError) as exc:
            return str(exc)

    @tool
    def approve_project_plan_draft(draft_id: str | None = None) -> str:
        """Approve and activate the exact pending plan draft after explicit user consent.

        Call only when the current user message explicitly approves execution. Omit draft_id to
        resolve project:pending_plan. Approval may fail if the base plan changed structurally or
        an older active plan still has a running step. After success, reread the active plan before
        executing it.
        """
        project_id, _chat_thread_id = runtime_ids()
        if not project_id:
            return "No active project_id in run config."
        try:
            return _json_draft_result(
                ProjectPlanDraftService(store, project_id).approve(draft_id)
            )
        except (ValueError, RuntimeError) as exc:
            return f"Project plan approval failed: {exc}"

    @tool
    def reject_project_plan_draft(
        draft_id: str | None = None,
        feedback: str = "",
    ) -> str:
        """Reject the pending plan draft after explicit user rejection or revision guidance.

        Preserve the user's feedback exactly. If the user requested changes, reject first and then
        call delegate_plan with the feedback to produce a new approval-required draft.
        """
        project_id, _chat_thread_id = runtime_ids()
        if not project_id:
            return "No active project_id in run config."
        try:
            return _json_draft_result(
                ProjectPlanDraftService(store, project_id).reject(
                    draft_id,
                    feedback=feedback,
                )
            )
        except (ValueError, RuntimeError) as exc:
            return f"Project plan rejection failed: {exc}"

    @tool
    def update_project_plan_step(
        plan_id: str,
        step_id: str,
        status: PlanStepStatus,
        result_summary: str = "",
        result_run_ids: list[str] | None = None,
        error: str = "",
        blocking_reason: str = "",
    ) -> str:
        """Apply one validated, versioned transition to an active plan step.

        Use this for orchestrator/project-memory/user-input/external-search steps. Scientific
        delegate tools update their own planned step automatically when called with plan_id and
        step_id. Valid states are pending, running, completed, failed, blocked, and skipped.
        Dependencies must be completed or skipped before a step can run or complete.
        """
        project_id, _chat_thread_id = runtime_ids()
        if not project_id:
            return "No active project_id in run config."
        try:
            service = ProjectPlanService(store, project_id)
            current = service.get_plan(plan_id)
            matching = [
                step for step in current.plan.steps if step.step_id == step_id.strip()
            ]
            if not matching:
                return f"Project plan update failed: unknown step_id {step_id!r}"
            scientific = {
                "csl",
                "lammps",
                "analysis",
            }
            if (
                matching[0].capability.value in scientific
                and status
                in {
                    PlanStepStatus.RUNNING,
                    PlanStepStatus.COMPLETED,
                    PlanStepStatus.FAILED,
                }
            ):
                return (
                    "Project plan update failed: scientific step boundaries are owned "
                    "by the matching delegate; pass plan_id and step_id to that delegate"
                )
            envelope = service.transition_step(
                plan_id,
                step_id,
                status,
                result_summary=result_summary,
                result_run_ids=result_run_ids,
                error=error,
                blocking_reason=blocking_reason,
            )
        except (ValueError, RuntimeError) as exc:
            return f"Project plan update failed: {exc}"
        return _json_result(envelope)

    tools = [
        get_project_plan,
        get_project_plan_draft,
        approve_project_plan_draft,
        reject_project_plan_draft,
        update_project_plan_step,
    ]
    if include_revision_tool:
        tools.insert(2, revise_project_plan_draft)
    return tools


def make_planning_project_plan_tools(
    store: ProjectStore,
    *,
    write_guard: ProjectPlanWriteGuard | None = None,
    request_id: str | None = None,
    user_request: str = "",
) -> list[BaseTool]:
    """Return exact read/revise tools reserved for the planning worker."""
    return [
        plan_tool
        for plan_tool in make_project_plan_tools(
            store,
            include_revision_tool=True,
            write_guard=write_guard,
            draft_request_id=request_id,
            user_request=user_request,
        )
        if plan_tool.name in {"get_project_plan", "revise_project_plan_draft"}
    ]


__all__ = [
    "make_create_project_plan_tool",
    "make_planning_project_plan_tools",
    "make_project_plan_tools",
    "ProjectPlanWriteGuard",
]
