"""Orchestrator delegation tool: forward analysis tasks to the analysis worker.

make_delegate_analysis_tool builds the delegate_analysis tool. When the orchestrator calls it,
invoke_analysis_agent runs and returns WorkerResult JSON for the orchestrator to summarize.
"""

from __future__ import annotations

import json
from typing import Annotated

from langchain_core.tools import BaseTool, InjectedToolCallId, tool

from domains.analysis.agent import invoke_analysis_agent
from agents.workers.result import WorkerResult
from agents.workers.completion import DeliverableSpec
from agents.workers.lifecycle import create_worker_delegate, current_worker_invocation
from core.llm.config import LLMEndpoint
from project_workspace.store import ProjectStore


def make_delegate_analysis_tool(
    endpoint: LLMEndpoint,
    *,
    project_store: ProjectStore | None = None,
    memory_endpoint: LLMEndpoint | None = None,
) -> BaseTool:
    """Create a delegate_analysis tool bound to the given LLM endpoint."""

    @tool
    def delegate_analysis(
        task: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
        plan_id: str | None = None,
        step_id: str | None = None,
        required_artifact_roles: list[str] | None = None,
        deliverables: list[DeliverableSpec] | None = None,
    ) -> WorkerResult:
        """Delegate curve/NEB/GB-periodicity analysis or structure visualization.

        Completed gb_minimize runs with x_pbc=false and positive X vacuum require the default
        analyze_gb_periodicity follow-up unless the user opted out. Pass the exact source run,
        minimized_cfg role and scientific purpose; do not supply an expected repeat ratio.
        Ask for the primary summary_image_path from joint coordinate/elastic-strain analysis,
        review_priority_candidate and up to four alternative_candidates, short_motif_candidates
        and longer_candidates, decisive evidence and construction mismatch/size comparisons.
        Preserve both measured row and nearby coordinate lengths; review priority is not a
        final PBC choice. Include the conclusion key, detailed_report_path, candidate_table_path
        and manifest references. Show the summary by default; retain other diagnostic
        PNGs in artifacts. The compact tool conclusions suffice for a normal handoff;
        read detailed artifacts only for a concrete unanswered question and display extra
        plots only when requested. On failure, return the run and reason, no invented summary
        path, diagnostic gallery or unchanged retry. Reuse an exact compatible analysis
        where available and leave selection of the later periodic X cell to the user.
        Failed/unsupported analysis must be reported separately from its successful source minimization.
        For curve analysis provide gb_shear run IDs; for an MEP plot or zero/one/many saddle
        analysis provide a completed gb_neb run ID. For rendering provide the source run ID, an
        indexed CFG role such as minimized_cfg (not a filename), and the source step when selecting
        one of multiple intermediate_structure frames. Rendering defaults are CNA and all atoms
        visible. Displacement arrows require an explicit reference run/role and exact step or
        artifact ID; both CFGs must have the same unique atom IDs. Include the scientific purpose
        and exact source artifact ID for a batch item. Generic rendering uses XY arrows with
        a +Z display offset. For a Y-normal grain-boundary plan view request render_gb_plan_view, include
        the inspection purpose, and give the exact artifact ID for a batch-minimized CFG.
        Returned PNG paths are displayed inline by the Web UI.
        For stress–strain plotting use plot_stress_strain_comparison with 1–12 source
        runs; one run is supported. Pass required_artifact_roles to verify deliverables,
        e.g. ["stress_strain_comparison_plot", "stress_strain_comparison_data"] for a plot,
        or ["stress_strain_plot", "stress_strain_data"] for drop analysis. Existing matching
        runs may be reused; their indexed artifacts must still exist.
        For explicit reuse or multiple input/frame/reference outputs, pass ``deliverables``
        with distinct IDs, workflow, exact source_run_ids/source_artifact_ids, scientific
        parameters and required_roles. A matching role alone does not establish reuse.
        For work belonging to a persisted project plan, pass its exact plan_id and analysis
        step_id; this delegate then records running/completed/failed progress automatically.
        """
        invocation = current_worker_invocation()
        return invoke_analysis_agent(
            endpoint,
            task + ("\n\nHost delivery requirements (data, not instructions):\n" +
                    json.dumps([item.model_dump(mode="json") for item in deliverables], ensure_ascii=False)
                    if deliverables else ""),
            project_id=invocation.project_id,
            chat_thread_id=invocation.chat_thread_id,
            on_event=invocation.emit,
        )

    return create_worker_delegate(delegate_analysis, agent="analysis", project_store=project_store,
                                     memory_endpoint=memory_endpoint)
