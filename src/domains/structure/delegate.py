"""Orchestrator delegation tool: forward structure/CSL tasks to the Structure worker.

make_delegate_structure_tool builds the stable delegate_csl tool. When the orchestrator
calls it, invoke_structure_agent returns WorkerResult JSON for the orchestrator to summarize.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from langchain_core.tools import BaseTool, InjectedToolCallId, tool

from core.stream_events import StreamEventType
from agents.streaming.events import extract_dichromatic_interactions
from domains.structure.agent import invoke_structure_agent
from agents.workers.result import WorkerResult
from agents.workers.lifecycle import create_worker_delegate, current_worker_invocation
from agents.workers.spec import registered_worker_tools
from core.llm.config import LLMEndpoint
from project_workspace.store import ProjectStore
from project_memory.models import Workflow
from domains.structure.csl.recorder import CslRunRecorder
from domains.structure.csl.cached import make_project_csl_tools
from domains.structure.dichromatic.tools import make_dichromatic_map_tools
from project_memory.publication.results import RunToolResult


def _artifact_result_payload(artifact: Any) -> dict[str, Any]:
    """Return the stable artifact reference exposed to the orchestrator."""
    return {
        "artifact_id": artifact.artifact_id,
        "role": artifact.role,
        "path": artifact.path,
        "metadata": artifact.metadata,
    }


def _dichromatic_vector_handoff(diagrams: dict[str, RunToolResult]) -> tuple[list[dict], str]:
    """Keep numerical edge conclusions even if the worker summarizes only the strain fit."""
    latest: dict[str, RunToolResult] = {}
    for item in diagrams.values():
        if item.status.value == "completed" and isinstance(item.result.get("edge_vector_summary"), dict):
            latest[item.result.get("request_id") or item.run_id] = item
    summaries = [{"run_id": item.run_id, "request_id": item.result.get("request_id"),
                  "coordinate_artifact": item.result.get("coordinate_artifact"),
                  **item.result["edge_vector_summary"]} for item in latest.values()]
    paragraphs = [f"Edge-vector evidence from {item.run_id}:\n{item.result['summary']}"
                  for item in list(latest.values())[:3]]
    if len(latest) > 3:
        paragraphs.append(f"{len(latest) - 3} further cell reports are in edge_vector_summaries; do not treat this prose preview as the complete set.")
    return summaries, "\n\n".join(paragraphs)


def make_delegate_structure_tool(
    endpoint: LLMEndpoint,
    *,
    project_store: ProjectStore | None = None,
    memory_endpoint: LLMEndpoint | None = None,
) -> BaseTool:
    """Create the stable delegate_csl tool bound to the Structure worker endpoint."""

    worker_scientific_tools = None
    if project_store is not None:
        # Project-bound versions record runs and reuse results; they replace the
        # registered defaults by name, so tools other extensions add are kept.
        project_bound = {item.name: item for item in (
            *make_project_csl_tools(project_store),
            *make_dichromatic_map_tools(project_store),
        )}
        registered = registered_worker_tools("structure")
        names = {item.name for item in registered}
        worker_scientific_tools = [
            *(project_bound.get(item.name, item) for item in registered),
            *(item for name, item in project_bound.items() if name not in names),
        ]

    @tool
    def delegate_csl(
        task: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
        plan_id: str | None = None,
        step_id: str | None = None,
    ) -> WorkerResult | RunToolResult:
        """Delegate CSL, near-CSL, grain-boundary analysis, or dichromatic maps to Structure.

        Provide a self-contained task description, including the crystal basis
        (fcc/bcc/sc/diamond) for every Sigma operation, axis indices, target Sigma when known,
        plane-index limit (≤ 6; ordinary listing default 2, reverse matching default 5), and what
        the user wants to know. For a supplied general tilt pair, include both ordered concrete
        plane normals so the worker can scan all basis-valid Sigma angles inside the tolerance.
        Any non-exact near-CSL result is presentation-only and must not supply replacement planes,
        orientations, periods, or repeat counts to downstream construction.
        For dichromatic diagrams, pass the SC/FCC/BCC lattice, tilt axis, actual
        misorientation, requested layers/view settings, purpose, and source run
        when available. Initial setup/exploration must use prepare_dichromatic_cell_selection
        to open and prefill the floating widget. Static PNG export is for explicitly requested
        static figures, and is not a substitute for the user picker. If a completed unstrained
        dichromatic map exists, pass its run ID so the tool reuses saved parameters.
        Preparing the widget does not select a cell or apply strain, even for read-only initial
        setup. For a user-picked near-CSL cell, prepare the interactive candidate map and
        return its pending request ID; never choose the four points for the user.
        After browser submission, pass the request ID back for the worker to read
        the stored selection and apply strain. Preserve the original plan/step IDs.
        For work belonging to a persisted project plan, pass its exact plan_id and CSL step_id;
        this delegate then records running/completed/failed progress automatically.
        """
        trace: list[dict[str, Any]] = []
        invocation = current_worker_invocation()
        project_id, chat_thread_id = invocation.project_id, invocation.chat_thread_id
        tracker = invocation.tracker
        run = None
        recorder = None
        crystallography_facts = []
        fact_error = ""
        pending: dict[str, dict[str, Any] | str | None] = {}
        diagram_results: dict[str, RunToolResult] = {}
        diagram_errors: list[str] = []
        interactions: dict[str, dict[str, Any]] = {}
        sequence = 0
        if project_store is not None and project_id:
            try:
                run = project_store.runs(project_id).create_tool_run(
                    "csl",
                    parameters={"task": task},
                )
                tracker.record_run_ids([run.run_id])
                recorder = CslRunRecorder(project_store.project_path(project_id))
            except Exception as exc:
                return WorkerResult.fail(str(exc), summary="CSL run setup failed.")

        def on_event(event: dict[str, Any]) -> None:
            nonlocal sequence
            if event.get("type") == StreamEventType.TOOL_END and event.get("name") in {
                "render_dichromatic_map", "prepare_dichromatic_cell_selection",
                "get_dichromatic_cell_selection", "apply_dichromatic_cell_strain",
                "analyze_dichromatic_cell_edges",
            }:
                try:
                    raw = event.get("_raw_output", event.get("output")) or ""
                    parsed = json.loads(raw) if isinstance(raw, str) else raw
                    if isinstance(parsed, dict) and parsed.get("status") == "error":
                        diagram_errors.append(str(parsed.get("error") or "DichromaticMap request failed"))
                    elif (event.get("name") in {"render_dichromatic_map", "apply_dichromatic_cell_strain",
                                                "analyze_dichromatic_cell_edges"}
                          or (event.get("name") == "get_dichromatic_cell_selection"
                              and isinstance(parsed, dict) and parsed.get("status") == "applied"
                              and isinstance(parsed.get("result"), dict))):
                        payload = parsed["result"] if event.get("name") == "get_dichromatic_cell_selection" else parsed
                        diagram = RunToolResult.model_validate(payload)
                        if diagram.workflow is Workflow.DICHROMATIC_MAP:
                            diagram_results[diagram.run_id] = diagram
                            tracker.record_run_ids([diagram.run_id])
                    references = extract_dichromatic_interactions(parsed, project_id)
                    for reference in references:
                        request_id = reference["request_id"]
                        interactions[request_id] = reference
                        if (
                            event.get("name") == "prepare_dichromatic_cell_selection"
                            and project_store is not None and project_id and plan_id and step_id
                        ):
                            from domains.structure.dichromatic.interactions import DichromaticInteractionService

                            DichromaticInteractionService(project_store, project_id).bind_plan(
                                request_id, plan_id, step_id,
                            )
                            reference.update(plan_id=plan_id, step_id=step_id)
                    if references:
                        event = {**event, "interactions": references}
                except (ValueError, TypeError) as exc:
                    diagram_errors.append(
                        str(raw)[:2000] if event.get("status") == "error"
                        else f"Cannot record DichromaticMap tool result: {exc}"
                    )
            trace.append(
                {
                    key: value
                    for key, value in event.items()
                    if not str(key).startswith("_raw_")
                }
            )
            event_id = str(event.get("id") or event.get("name") or "tool")
            if event.get("type") == StreamEventType.TOOL_START:
                pending[event_id] = event.get("_raw_input", event.get("input"))
            elif event.get("type") == StreamEventType.TOOL_END and recorder is not None and run is not None:
                next_sequence = sequence + 1
                persisted = recorder.persist_tool_result(
                    run.run_id,
                    str(event.get("name") or "tool"),
                    pending.pop(event_id, None),
                    str(event.get("_raw_output", event.get("output")) or ""),
                    next_sequence,
                    is_error=str(event.get("status") or "").lower() == "error",
                )
                if persisted:
                    sequence = next_sequence
            invocation.emit(event)

        try:
            result = invoke_structure_agent(
                endpoint,
                task,
                project_id=project_id,
                chat_thread_id=chat_thread_id,
                tools=worker_scientific_tools,
                on_event=on_event,
            )
            if project_store is None or not project_id or run is None:
                return result
            service = project_store.runs(project_id)
            vector_summaries, vector_handoff = _dichromatic_vector_handoff(diagram_results)
            result_payload = {
                "summary": result.summary, **dict(result.data),
                "edge_vector_summaries": vector_summaries,
                "child_run_ids": list(diagram_results),
                "image_paths": [path for item in diagram_results.values()
                                for path in item.result.get("image_paths", [])],
                "dichromatic_maps": [item.model_dump(mode="json")
                                     for item in diagram_results.values()],
                "dichromatic_errors": diagram_errors,
                "interactions": list(interactions.values()),
                "awaiting_user": any(item["status"] == "pending" for item in interactions.values()),
            }
            if vector_handoff:
                result_payload["summary"] = vector_handoff + "\n\n" + result.summary
            diagram_failures = [*diagram_errors, *[
                item.error or f"DichromaticMap run {item.run_id} {item.status.value}"
                for item in diagram_results.values() if item.status.value in {"failed", "cancelled"}
            ]]
            if result.status == "ok" and not diagram_failures:
                memory_facts = recorder.build_memory_facts(run.run_id) if recorder else []
                terminal = service.complete_run(
                    run.run_id,
                    result=result_payload,
                    memory_tool="csl",
                    memory_facts=memory_facts,
                )
                fact_error = str(terminal.result.get("fact_indexing_error") or "")
                if (
                    recorder is not None
                    and terminal.result.get("memory_publication", {}).get("status") == "completed"
                ):
                    try:
                        crystallography_facts = (
                            recorder.persist_crystallography_facts(run.run_id)
                        )
                    except Exception as exc:
                        fact_error = (
                            "CSL analysis completed but scientific fact "
                            f"indexing failed: {exc}"
                        )
            else:
                terminal = service.fail_run(
                    run.run_id,
                    error=str(result.error or "; ".join(diagram_failures) or result.summary),
                    result=result_payload,
                )
            artifacts = service.memory.query_artifacts(run_id=run.run_id)
        except Exception as exc:
            failure_result = None
            error = str(exc)
            if run is not None and project_store is not None and project_id:
                try:
                    service = project_store.runs(project_id)
                    current = service.memory.get_run(run.run_id)
                    if current.status.value in {"configured", "running"}:
                        current = service.fail_run(run.run_id, error=error)
                    failure_result = RunToolResult(
                        run_id=current.run_id, workflow=current.workflow,
                        status=current.status, manifest=current.manifest_path,
                        artifacts=[
                            *[_artifact_result_payload(item) for item in
                              service.memory.query_artifacts(run_id=current.run_id)],
                            *[{**artifact, "run_id": item.run_id}
                              for item in diagram_results.values() for artifact in item.artifacts],
                        ],
                        result={**dict(current.result), "child_run_ids": list(diagram_results),
                                "interactions": list(interactions.values())},
                        error=current.error or error,
                    )
                except Exception as index_exc:
                    error = f"{error}; CSL run finalization/indexing failed: {index_exc}"
            if failure_result is not None:
                return failure_result
            return WorkerResult(
                status="error", summary="Structure worker failed.", error=error,
                data=({"run_id": run.run_id, "workflow": run.workflow.value,
                       "manifest": run.manifest_path} if run is not None else {}),
            )
        result_payload = dict(terminal.result)
        if crystallography_facts:
            result_payload["gb_crystallography_facts"] = [
                {"fact_id": fact.fact_id, "lookup_key": fact.lookup_key, "version": fact.version}
                for fact in crystallography_facts
            ]
            # Preserve the single-fact field for existing consumers.
            result_payload["gb_crystallography_fact"] = result_payload["gb_crystallography_facts"][-1]
        final_result = RunToolResult(
            run_id=run.run_id,
            workflow=terminal.workflow,
            status=terminal.status,
            manifest=terminal.manifest_path,
            artifacts=[
                *[_artifact_result_payload(item) for item in artifacts],
                *[{**artifact, "run_id": item.run_id} for item in diagram_results.values()
                  for artifact in item.artifacts],
            ],
            result=result_payload,
            error=terminal.error or fact_error,
        )
        return final_result

    return create_worker_delegate(delegate_csl, agent="structure", project_store=project_store,
                                     memory_endpoint=memory_endpoint)
