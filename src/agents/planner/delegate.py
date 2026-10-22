"""Orchestrator delegation tool for deep, on-demand planning."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from langchain_core.tools import BaseTool, InjectedToolCallId, tool

from agents.workers.result import WorkerResult
from agents.planner.agent import invoke_plan_agent
from agents.workers.lifecycle import create_worker_delegate, current_worker_invocation
from core.llm.config import LLMEndpoint
from project_workspace.store import ProjectStore
from agents.planner.tools import (
    ProjectPlanWriteGuard,
    make_create_project_plan_tool,
    make_planning_project_plan_tools,
)


def make_delegate_plan_tool(
    endpoint: LLMEndpoint,
    *,
    project_store: ProjectStore | None = None,
    memory_endpoint: LLMEndpoint | None = None,
    extra_tools: Sequence[BaseTool] | None = None,
) -> BaseTool:
    """Create the planner with read-only context and constrained plan persistence.

    ``extra_tools`` is an explicit extension point for a future web-search or
    server-side retrieval tool. No external-search tool is injected by default.
    """
    shared_worker_tools: list[BaseTool] = list(extra_tools or [])

    @tool
    def delegate_plan(
        task: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
        existing_plan_id: str | None = None,
    ) -> WorkerResult:
        """Ask the planning specialist to create or revise a durable plan.

        Use this for ambiguous, unfamiliar, multi-stage, or method-selection problems where a
        dependency-aware plan would improve execution. Include the user's objective, constraints,
        relevant run IDs, known evidence, and desired deliverable. The result is advisory: after
        it returns, execute the appropriate CSL, LAMMPS, analysis, memory, or direct-answer steps.
        When the user asks to change an existing plan, pass its exact existing_plan_id and include
        the user's complete original modification request in task. The planning specialist will
        read the exact stored plan and either create an audited revision or, for a materially new
        objective, supersede it with a new plan. Do not use this for routine single-step tasks or
        merely to restate the user's request.
        """

        invocation = current_worker_invocation()
        project_id, chat_thread_id = invocation.project_id, invocation.chat_thread_id
        worker_tools = list(shared_worker_tools)
        require_persisted_plan = project_store is not None and project_id is not None
        if require_persisted_plan:
            write_guard = ProjectPlanWriteGuard()
            if existing_plan_id and existing_plan_id.strip():
                worker_tools.extend(
                    make_planning_project_plan_tools(
                        project_store,
                        write_guard=write_guard,
                        request_id=tool_call_id,
                        user_request=task,
                    )
                )
            worker_tools.append(
                make_create_project_plan_tool(
                    project_store,
                    request_id=tool_call_id,
                    write_guard=write_guard,
                    user_request=task,
                )
            )
        planning_task = task
        normalized_plan_id = str(existing_plan_id or "").strip()
        if normalized_plan_id:
            planning_task = (
                "Revise or supersede the existing durable project plan with ID "
                f"{normalized_plan_id}. First read that exact plan using get_project_plan. "
                "The user's complete modification request follows; preserve it as the reason "
                f"for the decision and revision:\n\n{task}"
            )
        result = invoke_plan_agent(
            endpoint,
            planning_task,
            project_id=project_id,
            chat_thread_id=chat_thread_id,
            extra_tools=worker_tools,
            on_event=invocation.emit,
            require_persisted_plan=require_persisted_plan,
            existing_plan_id=normalized_plan_id or None,
        )
        return result

    return create_worker_delegate(delegate_plan, agent="plan", project_store=project_store,
                                     memory_endpoint=memory_endpoint)


__all__ = ["make_delegate_plan_tool"]
