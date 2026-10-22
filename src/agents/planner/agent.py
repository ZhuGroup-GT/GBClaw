"""On-demand planning worker (does not talk to the user or execute workflows).

The worker reloads the planning domain knowledge on every invocation and returns
an advisory plan to the orchestrator through ``WorkerResult``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from functools import partial
from typing import Any

from langchain_core.tools import BaseTool

from agents.workers.result import WorkerResult
from agents.workers.spec import WorkerRun, WorkerSpec
from core.llm.config import LLMEndpoint
from agents.planner.prompt import build_plan_agent_prompt

_PLAN_DRAFT_WRITES = {"create_project_plan_draft", "revise_project_plan_draft"}

PLAN_WORKER = WorkerSpec(
    agent="plan",
    label="Planning agent",
    status_message="Planning…",
    prompt=build_plan_agent_prompt,
)
create_plan_agent = PLAN_WORKER.create_graph


def _plan_result(run: WorkerRun, *, require_persisted_plan: bool,
                 existing_plan_id: str | None) -> WorkerResult:
    persisted_writes: list[tuple[str, dict[str, Any]]] = []
    for item in run.trace:
        tool_name = str(item.get("name") or "")
        if tool_name not in _PLAN_DRAFT_WRITES:
            continue
        try:
            payload = json.loads(str(item.get("output") or ""))
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("draft"), dict):
            persisted_writes.append((tool_name, payload))
    if require_persisted_plan and not persisted_writes:
        return WorkerResult.fail(
            "Planning agent did not persist a structured project plan draft."
        )
    if len(persisted_writes) > 1:
        return WorkerResult.fail(
            "Planning agent performed more than one successful plan-draft write."
        )

    data: dict[str, Any] = {
        "task": run.task,
        "existing_plan_id": existing_plan_id,
    }
    if persisted_writes:
        tool_name, persisted_plan = persisted_writes[0]
        data["plan_draft"] = persisted_plan
        data["plan_action"] = (
            "revision_draft"
            if tool_name == "revise_project_plan_draft"
            else "creation_draft"
        )
    return WorkerResult.ok(summary=run.reply or "", trace=run.trace, **data)


def invoke_plan_agent(
    endpoint: LLMEndpoint,
    task: str,
    *,
    project_id: str | None = None,
    chat_thread_id: str | None = None,
    extra_tools: Sequence[BaseTool] | None = None,
    enable_tracing: bool | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    require_persisted_plan: bool = False,
    existing_plan_id: str | None = None,
) -> WorkerResult:
    """Run the planning worker statelessly and return its advisory plan."""
    return PLAN_WORKER.invoke(
        endpoint, task, project_id=project_id, chat_thread_id=chat_thread_id,
        extra_tools=extra_tools, enable_tracing=enable_tracing, on_event=on_event,
        finalize=partial(_plan_result, require_persisted_plan=require_persisted_plan,
                         existing_plan_id=existing_plan_id),
    )


__all__ = ["PLAN_WORKER", "create_plan_agent", "invoke_plan_agent"]
