"""Orchestrator delegation tool: forward LAMMPS tasks to the LAMMPS worker.

make_delegate_lammps_tool builds the delegate_lammps tool. When the orchestrator calls it,
invoke_lammps_agent runs and returns WorkerResult JSON for the orchestrator to summarize.
"""

from __future__ import annotations

from dataclasses import replace
import json
from typing import Annotated, Any

from langchain_core.tools import BaseTool, InjectedToolCallId, tool

from core.stream_events import StreamEventType
from domains.lammps.agent import invoke_lammps_agent
from agents.workers.result import WorkerResult
from agents.workers.lifecycle import create_worker_delegate, current_worker_invocation
from core.llm.config import LLMEndpoint
from project_memory.models import RunStatus, Workflow
from project_workspace.store import ProjectStore
from project_memory.publication.results import RunToolResult


def _completed_minimize_run_id(record: dict[str, Any]) -> str | None:
    """Accept successful scientific tool output, never a worker's narrative or data."""
    if (
        record.get("type") not in {StreamEventType.TOOL_END, "tool_call"}
        or record.get("name") != "run_gb_minimize"
        or record.get("status") == "error"
    ):
        return None
    raw = record.get("_raw_output", record.get("output"))
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
        result = RunToolResult.model_validate(payload)
    except (ValueError, TypeError):
        return None
    if result.workflow is Workflow.GB_MINIMIZE and result.status is RunStatus.COMPLETED:
        return result.run_id
    return None


def _with_analysis_followups(
    result: WorkerResult,
    *,
    project_store: ProjectStore | None,
    project_id: str | None,
    completed_run_ids: list[str],
) -> WorkerResult:
    """Promote persisted requests from this invocation into the orchestrator handoff.

    Streamed tool results survive a later worker failure, while the full trace also supports
    callers without streaming. Both only identify candidates; project storage is authoritative.
    """
    run_ids = list(dict.fromkeys([
        *completed_run_ids,
        *(run_id for record in result.trace
          if isinstance(record, dict) and (run_id := _completed_minimize_run_id(record))),
    ]))
    data = dict(result.data)
    # Model-supplied requests cannot bypass the source-run checks below.
    data.pop("analysis_followups", None)
    data.pop("analysis_followup_errors", None)
    followups: list[dict[str, Any]] = []
    errors: list[str] = []
    if run_ids:
        try:
            if not project_id:
                raise ValueError("an active project is required")
            memory = (project_store or ProjectStore()).runs(project_id).memory
        except Exception as exc:
            errors.append(f"Cannot verify post-minimization analysis requests: {exc}")
        else:
            for run_id in run_ids:
                try:
                    run = memory.get_run(run_id)
                except Exception as exc:
                    errors.append(f"Cannot verify analysis follow-up for {run_id}: {exc}")
                    continue
                if run.workflow is not Workflow.GB_MINIMIZE or run.status is not RunStatus.COMPLETED:
                    continue
                requests = run.result.get("analysis_followups") or []
                if not isinstance(requests, list):
                    errors.append(f"Invalid persisted analysis_followups for {run_id}")
                    continue
                for request in requests:
                    if (
                        isinstance(request, dict)
                        and request.get("tool") == "analyze_gb_periodicity"
                        and request.get("agent") == "analysis"
                        and request.get("source_run_id") == run_id
                        and request.get("source_artifact_role") == "minimized_cfg"
                        and request.get("required") is True
                    ):
                        if request not in followups:
                            followups.append(dict(request))
                    else:
                        errors.append(f"Invalid persisted periodicity follow-up for {run_id}")
    if followups:
        data["analysis_followups"] = followups
    if errors:
        data["analysis_followup_errors"] = errors
    return replace(result, data=data)


def make_delegate_lammps_tool(
    endpoint: LLMEndpoint,
    *,
    project_store: ProjectStore | None = None,
    memory_endpoint: LLMEndpoint | None = None,
) -> BaseTool:
    """Create a delegate_lammps tool bound to the given LLM endpoint."""

    @tool
    def delegate_lammps(
        task: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
        plan_id: str | None = None,
        step_id: str | None = None,
    ) -> WorkerResult:
        """Delegate LAMMPS structure building and relaxation to the LAMMPS specialist.

        The specialist accepts either complete grain orientations or partial crystallographic
        input such as a tilt axis plus one plane or cubic plane family per grain. It can use
        infer_tilt_orientation to select concrete perpendicular plane normals and derive x/y/z.
        Preserve parenthesized planes such as (100) as concrete normals and braced planes such
        as {100} as families. Preserve only ambiguous bare plane text as a family in the task.
        Also provide structure type,
        config/potential details when non-default, and desired outputs. Do **not** include MPI
        rank counts unless the user explicitly requested a specific number.
        For work belonging to a persisted project plan, pass its exact plan_id and the LAMMPS
        step_id; this delegate then records running/completed/failed progress automatically.
        Completed X-vacuum GB minimizations also return verified analysis_followups in data;
        delegate those requests to Analysis unless the user explicitly opted out.
        """
        invocation = current_worker_invocation()
        completed_minimize_run_ids: list[str] = []

        def on_event(event: dict[str, Any]) -> None:
            source_run_id = _completed_minimize_run_id(event)
            if source_run_id and source_run_id not in completed_minimize_run_ids:
                completed_minimize_run_ids.append(source_run_id)
            invocation.emit(event)

        result = invoke_lammps_agent(
            endpoint,
            task,
            project_id=invocation.project_id,
            chat_thread_id=invocation.chat_thread_id,
            on_event=on_event,
        )
        return _with_analysis_followups(
            result,
            project_store=project_store,
            project_id=invocation.project_id,
            completed_run_ids=completed_minimize_run_ids,
        )

    return create_worker_delegate(delegate_lammps, agent="lammps", project_store=project_store,
                                     memory_endpoint=memory_endpoint)
