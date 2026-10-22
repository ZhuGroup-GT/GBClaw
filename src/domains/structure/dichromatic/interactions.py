"""Project-scoped numeric cell selection between browser and Structure agent.

Pending requests are durable interaction state, not scientific conclusions. A
confirmed selection and its subsequent strain calculation are separate scientific
runs, linked through the existing memory publisher and permanent JSON artifacts.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import math
from pathlib import Path
import re
import uuid

from core.util.io import write_json_atomic
from project_workspace.models import utc_now_iso
from project_workspace.artifacts.models import ProvenanceClass
from project_memory.models import RunStatus, Workflow
from project_workspace.store import ProjectStore
from project_memory.publication.results import RunToolResult
from domains.structure.dichromatic.adapter import DichromaticMapRequest, build_dichromatic_map


def _current_edge_analysis(report: dict | None) -> bool:
    return bool(report and report.get("method") == "exact_all_layers_finite_edge_integer_lattices"
                and report.get("analysis_version") == 2)


def _edge_vector_summary(report: dict) -> dict:
    """Preserve distances and both grain notations independently of large atom/tie arrays."""
    edges = []
    for edge in report.get("edges", [])[:4]:
        directions = {}
        for vector in edge.get("vectors", []):
            grains = vector.get("grain_coordinates", [])
            if len(grains) != 2:
                continue
            key = tuple(item["lattice_notation"] for item in grains)
            if key in directions:
                continue
            directions[key] = {
                "example_vector_id": vector["id"], "layer": vector.get("layer", report.get("layer")),
                "layer_name": vector.get("layer_name"),
                "length_a0": vector["length_a0"], "length_angstrom": vector["length_angstrom"],
                "displacement_a0": vector["displacement_a0"],
                "G1": grains[0]["lattice_notation"], "G2": grains[1]["lattice_notation"],
                "reconstruction_errors_a0": [item["reconstruction_error_a0"] for item in grains],
                "rational": [item["rational"]["is_rational"] for item in grains],
                "candidate_secondary_misfit": vector.get("candidate_secondary_misfit", False),
            }
        edges.append({
            "edge_index": edge["edge_index"], "edge_label": edge["edge_label"], "status": edge["status"],
            "minimum_distance_a0": edge.get("minimum_distance_a0"),
            "minimum_distance_angstrom": edge.get("minimum_distance_angstrom"),
            "minimum_layers": edge.get("minimum_layers", [report.get("layer")]),
            "vector_count": edge.get("vector_count", len(edge.get("vectors", []))),
            "candidate_secondary_misfit": edge.get("candidate_secondary_misfit", False),
            "directions": list(directions.values())[:4], "distinct_direction_count": len(directions),
            "directions_truncated": len(directions) > 4,
            "warnings": edge.get("warnings", []),
        })
    return {
        "method": report.get("method"), "analysis_version": report.get("analysis_version"),
        "status": report.get("status"), "all_layers_analyzed": _current_edge_analysis(report),
        "analyzed_layers": report.get("analyzed_layers", [report.get("layer")]),
        "selected_layer": report.get("selected_layer", report.get("layer")),
        "edges": edges,
        "convention": "G1 and G2 are representations of the SAME directed G1-to-G2 spatial displacement in the two strained conventional lattice bases.",
        "interpretation": "Geometric Burgers-vector candidates only when candidate_secondary_misfit is true; not identified dislocations or a Burgers-circuit measurement.",
        "complete_vectors_location": "coordinate_artifact -> edge_analysis.edges[].vectors",
    }


def _edge_summary_text(summary: dict) -> str:
    """Small deterministic run summary survives the 2,200-character memory handoff."""
    lines = ["All-layer finite-edge G1→G2 vectors; geometric candidates, not identified dislocations."]
    if not summary["all_layers_analyzed"]:
        lines[0] = "Historical selected-layer edge vectors; all-layer reanalysis is required."
    for edge in summary["edges"]:
        prefix = f"{edge['edge_label']}: "
        minimum = edge.get("minimum_distance_a0")
        if minimum is None:
            lines.append(prefix + f"no resolved minimum ({edge['status']}).")
            continue
        prefix += (f"min={minimum:.7g} a0 ({edge['minimum_distance_angstrom']:.7g} Å), "
                   f"layers={edge['minimum_layers']}, ties={edge['vector_count']}; ")
        notations = [f"G1={item['G1']}, G2={item['G2']}" for item in edge["directions"][:2]]
        if edge["distinct_direction_count"] > 2:
            notations.append("further directions in edge_vector_summary/artifact")
        line = prefix + "; ".join(notations)
        if not edge["candidate_secondary_misfit"]:
            line += "; no secondary-misfit evidence"
        lines.append(line + ".")
    return "\n".join(lines)


class InteractionConflict(ValueError):
    """The user selection or another caller has advanced this request."""


class InteractionNotFound(ValueError):
    """The request does not exist in this project."""


class DichromaticInteractionService:
    def __init__(self, store: ProjectStore, project_id: str):
        self.store = store
        self.project_id = project_id
        self.root = store.project_path(project_id) / "runtime" / "dichromatic_requests"

    def _path(self, request_id: str) -> Path:
        if not re.fullmatch(r"dmap-pick-[0-9a-f]{32}", request_id):
            raise InteractionNotFound("Invalid DichromaticMap request ID")
        return self.root / f"{request_id}.json"

    @contextmanager
    def _locked(self, request_id: str):
        path = self._path(request_id)
        if not path.is_file():
            raise InteractionNotFound("DichromaticMap request not found in this project")
        with path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads(path.read_text(encoding="utf-8"))
            if state.get("project_id") != self.project_id or state.get("request_id") != request_id:
                raise InteractionConflict("DichromaticMap request identity mismatch")
            self._reconcile(state)
            yield state

    def _save(self, state: dict) -> None:
        state["updated_at"] = utc_now_iso()
        write_json_atomic(self._path(state["request_id"]), state)

    @contextmanager
    def _execution_locked(self, request_id: str):
        """One process owns an apply attempt; process exit releases this lease."""
        path = self._path(request_id)
        if not path.is_file():
            raise InteractionNotFound("DichromaticMap request not found in this project")
        with path.with_suffix(".apply.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise InteractionConflict("This cell selection is already being applied") from exc
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _terminate_attempt(self, run_id: str, reason: str, *, cancelled: bool) -> None:
        service = self.store.runs(self.project_id)
        run = service.memory.get_run(run_id)
        if run.status is RunStatus.CONFIGURED:
            # The existing lifecycle requires RUNNING before any terminal state.
            # This is bookkeeping only: no scientific calculation is started.
            service.memory.transition_run(run_id, RunStatus.RUNNING, at=utc_now_iso(),
                                          progress={"abandoned_before_execution": True})
        elif run.status is not RunStatus.RUNNING:
            return
        if cancelled:
            service.cancel_run(run_id, error=reason)
        else:
            service.fail_run(run_id, error=reason)

    def _reconcile(self, state: dict, *, execution_owned: bool = False) -> None:
        """Recover a finished publication if a process stopped before updating UI state."""
        if state["status"] == "applied" and state.get("edge_analysis_run_id"):
            analysis = self.store.memory(self.project_id).get_run(state["edge_analysis_run_id"])
            if (analysis.status is RunStatus.COMPLETED
                    and (state.get("result") or {}).get("run_id") != analysis.run_id):
                state["result"] = self._run_result(analysis.run_id).model_dump(mode="json")
                state["revision"] += 1
                self._save(state)
        stage = "apply" if state["status"] == "applying" else "selection"
        run_id = state.get(f"{stage}_run_id")
        if not run_id or state["status"] not in {"pending", "applying"}:
            return
        run = self.store.memory(self.project_id).get_run(run_id)
        if run.status is RunStatus.COMPLETED:
            state["status"] = "applied" if stage == "apply" else "selected"
            state["revision"] += 1
            if stage == "apply":
                state["result"] = self._run_result(run_id).model_dump(mode="json")
                state.pop("last_error", None)
            self._save(state)
        elif stage == "apply" and run.status in {RunStatus.FAILED, RunStatus.CANCELLED}:
            state.update(status="selected", last_error=run.error)
            state["revision"] += 1
            self._save(state)
        elif stage == "apply" and run.status in {RunStatus.CONFIGURED, RunStatus.RUNNING}:
            reason = "The strain calculation process stopped before completion; the confirmed selection is retained for an explicit retry."
            if execution_owned:
                self._terminate_attempt(run_id, reason, cancelled=True)
            else:
                try:
                    with self._execution_locked(state["request_id"]):
                        self._terminate_attempt(run_id, reason, cancelled=True)
                except InteractionConflict:
                    return  # A live calculation owns the execution lease.
            state.update(status="selected", last_error=reason)
            state["revision"] += 1
            self._save(state)

    @staticmethod
    def _expect(state: dict, revision: int) -> None:
        if state["revision"] != revision:
            raise InteractionConflict("The selection request changed; reload it before submitting")

    def interaction(self, state: dict) -> dict:
        return {
            "type": "dichromatic_cell_selection", "ui_action": "pick_dichromatic_cell",
            "request_id": state["request_id"], "project_id": self.project_id,
            "status": state["status"], "revision": state["revision"],
            "api_path": f"/api/projects/{self.project_id}/dichromatic-map/requests/{state['request_id']}",
            **{key: state[key] for key in ("plan_id", "step_id") if state.get(key)},
        }

    def public(self, state: dict, *, compact: bool = False) -> dict:
        result = {key: value for key, value in state.items() if key != "candidates" or not compact}
        result["interaction"] = self.interaction(state)
        result["candidate_count"] = len(state.get("candidates", []))
        if state["status"] == "applied":
            scientific = (state.get("result") or {}).get("result", {})
            current = _current_edge_analysis(scientific.get("edge_analysis"))
            result["edge_analysis_upgrade_required"] = not current
            if not current:
                result["edge_analysis_next_action"] = "Call analyze_dichromatic_cell_edges to derive all-layer measurements from the saved F; do not refit strain or report the historical selected-layer minimum as current."
            if compact and state.get("result"):
                result["result"] = {**state["result"], "result": self.compact_result(scientific)}
        return result

    @staticmethod
    def compact_result(result: dict) -> dict:
        """Agent view keeps the complete bounded conclusion and refers large tie sets to JSON."""
        report = result.get("edge_analysis")
        if not isinstance(report, dict):
            return result
        summary = result.get("edge_vector_summary") or _edge_vector_summary(report)
        compact = {key: value for key, value in result.items() if key not in {"annotations", "edge_analysis"}}
        compact["edge_vector_summary"] = summary
        compact["summary"] = _edge_summary_text(summary)
        compact["edge_analysis"] = {
            key: report[key] for key in ("method", "analysis_version", "status", "selected_layer", "layer", "analyzed_layers", "layer_count", "annotation_count") if key in report
        }
        compact["edge_analysis"].update(vectors_in_artifact=True,
            complete_vectors_location="coordinate_artifact -> edge_analysis.edges[].vectors")
        compact["annotation_count"] = result.get("annotation_count", len(result.get("annotations", [])))
        compact["complete_annotations_location"] = "coordinate_artifact -> annotations"
        return compact

    def create(self, parameters: dict | None, *, purpose: str, source_run_id: str | None = None) -> dict:
        purpose = purpose.strip()
        if not purpose or len(purpose) > 2000:
            raise ValueError("purpose must contain between 1 and 2000 characters")
        source = None
        if source_run_id:
            source = self.store.memory(self.project_id).get_run(source_run_id)
            if source.status is not RunStatus.COMPLETED:
                raise ValueError("The source run must be completed")
        if parameters is None:
            if source is None or source.workflow is not Workflow.DICHROMATIC_MAP:
                raise ValueError("Provide parameters or a completed unstrained dichromatic_map source_run_id")
            if source.result.get("strain"):
                raise ValueError("This map already has applied strain; reopen its saved request instead of preparing an unstrained selection")
            parameters = source.result.get("parameters") or source.parameters.get("parameters")
            if not isinstance(parameters, dict):
                raise ValueError("The source map has no saved geometry parameters")
        params = DichromaticMapRequest.model_validate(parameters).model_dump(mode="json")
        if params.get("local_cutoff") is None:
            params["local_cutoff"] = 0.08
        diagram = build_dichromatic_map(params)
        request_id = f"dmap-pick-{uuid.uuid4().hex}"
        timestamp = utc_now_iso()
        state = {
            "request_id": request_id, "project_id": self.project_id,
            "status": "pending", "revision": 1, "purpose": purpose,
            "parameters": diagram["parameters"], "source_run_id": source_run_id,
            "software": diagram["software"],
            "candidates": diagram.get("pick_candidates", []),
            "created_at": timestamp, "updated_at": timestamp,
            "selection_instructions": "Select four paired sites in one axial layer, in perimeter order, then confirm.",
        }
        self.root.mkdir(parents=True, exist_ok=True)
        self._save(state)
        return self.public(state)

    def get(self, request_id: str, *, compact: bool = False) -> dict:
        with self._locked(request_id) as state:
            return self.public(state, compact=compact)

    def list_requests(self, status: str = "pending") -> list[dict]:
        if status not in {"pending", "selected", "applied", "cancelled", "applying", "all"}:
            raise ValueError("Invalid request status")
        if not self.root.exists():
            return []
        result = []
        for path in sorted(self.root.glob("dmap-pick-*.json"), reverse=True):
            state = self.get(path.stem, compact=True)
            if status == "all" or state["status"] == status:
                result.append(state)
        return sorted(result, key=lambda state: state["created_at"], reverse=True)

    def bind_plan(self, request_id: str, plan_id: str, step_id: str) -> None:
        with self._locked(request_id) as state:
            if state.get("plan_id") and (state["plan_id"], state["step_id"]) != (plan_id, step_id):
                raise InteractionConflict("This request already belongs to another plan step")
            state.update(plan_id=plan_id, step_id=step_id)
            self._save(state)

    @staticmethod
    def _edge_result_fields(diagram: dict) -> dict:
        """Keep large tie sets out of agent context; all pairs remain in the JSON artifact."""
        report, annotations = diagram["edge_analysis"], diagram["annotations"]
        summary = _edge_vector_summary(report)
        conclusion = {"edge_vector_summary": summary, "summary": _edge_summary_text(summary)}
        if len(annotations) <= 64:
            return {**conclusion, "edge_analysis": report, "annotations": annotations}
        edges = [{**edge, "vectors": edge["vectors"][:8], "vector_count": len(edge["vectors"]),
                  "vectors_truncated": len(edge["vectors"]) > 8} for edge in report["edges"]]
        return {
            **conclusion,
            "edge_analysis": {**report, "edges": edges, "vectors_truncated": True,
                              "complete_vectors_location": "coordinate_artifact -> edge_analysis.edges[].vectors"},
            "annotations": annotations[:64], "annotations_truncated": True,
            "annotation_count": len(annotations),
            "complete_annotations_location": "coordinate_artifact -> annotations",
        }

    def _saved_edge_analysis(self, run) -> dict | None:
        report = run.result.get("edge_analysis")
        if report and report.get("vectors_truncated"):
            path = self.store.runs(self.project_id).artifacts.run_directory(run.run_id) / "dichromatic_map.json"
            return json.loads(path.read_text(encoding="utf-8"))["edge_analysis"]
        return report

    def preview(self, request_id: str, viewport: dict) -> dict:
        if set(viewport) - {"center", "width", "height"}:
            raise ValueError("Only the viewport can change while a cell selection is pending")
        with self._locked(request_id) as state:
            if state["status"] == "applied":
                from domains.structure.dichromatic.adapter import rebuild_strained_dichromatic_view

                run = self.store.memory(self.project_id).get_run(state["apply_run_id"])
                if run.status is not RunStatus.COMPLETED:
                    raise InteractionConflict("The applied diagram is not recorded successfully")
                if state.get("edge_analysis_run_id"):
                    analysis = self.store.memory(self.project_id).get_run(state["edge_analysis_run_id"])
                    if analysis.status is RunStatus.COMPLETED:
                        run = analysis
                diagram = rebuild_strained_dichromatic_view(
                    {**run.result["parameters"], **viewport}, run.result["strain"], run.result["selected_cell"],
                    edge_analysis=self._saved_edge_analysis(run),
                )
                return {**diagram, "request_revision": state["revision"]}
            if state["status"] == "selected":
                run = self.store.memory(self.project_id).get_run(state["selection_run_id"])
                diagram = build_dichromatic_map({**run.result["parameters"], **viewport})
                return {**diagram, "request_revision": state["revision"], "selected_cell": run.result["selection"]}
            if state["status"] != "pending":
                raise InteractionConflict("This request is not currently available for preview")
            diagram = build_dichromatic_map({**state["parameters"], **viewport})
            candidates = {item["id"]: item for item in state["candidates"]}
            candidates.update({item["id"]: item for item in diagram.get("pick_candidates", [])})
            if len(candidates) > 100_000:
                raise ValueError("Cell search exceeds 100,000 candidates; start a smaller search")
            state["candidates"] = list(candidates.values())
            self._save(state)
            return {**diagram, "request_revision": state["revision"]}

    def select(self, request_id: str, candidate_ids: list[str], *, expected_revision: int) -> dict:
        from domains.structure.dichromatic.adapter import validate_dichromatic_selection

        with self._locked(request_id) as state:
            if state["status"] == "selected" and candidate_ids == state.get("candidate_ids"):
                return self.public(state)
            self._expect(state, expected_revision)
            if state["status"] != "pending":
                raise InteractionConflict("This request is no longer awaiting selection")
            selection = validate_dichromatic_selection(
                state["parameters"], candidate_ids, selection_candidates=state["candidates"],
            )
            service = self.store.runs(self.project_id)
            if state.get("selection_run_id"):
                run = service.memory.get_run(state["selection_run_id"])
                if run.status in {RunStatus.FAILED, RunStatus.CANCELLED}:
                    state.pop("selection_run_id")
                elif candidate_ids != state.get("candidate_ids"):
                    raise InteractionConflict("A previous selection is being recorded; reload before changing it")
            if not state.get("selection_run_id"):
                run = service.create_configured_run(
                    workflow=Workflow.DICHROMATIC_MAP,
                    input_payload={"parameters": state["parameters"], "candidate_ids": candidate_ids},
                    parameters={"memory_tool": "select_dichromatic_cell", "purpose": state["purpose"],
                                "request_id": request_id, "origin": "browser_selection"},
                    source_run_id=state.get("source_run_id"),
                )
                state.update(selection_run_id=run.run_id, candidate_ids=candidate_ids, selection=selection)
                try:
                    self._save(state)
                except Exception:
                    self._terminate_attempt(run.run_id, "Could not persist the cell selection interaction state.",
                                            cancelled=True)
                    raise
            if run.status is RunStatus.CONFIGURED:
                service.start_run(run.run_id)
            root = service.artifacts.run_directory(run.run_id)
            snapshot = {key: value for key, value in state.items() if key != "candidates"}
            snapshot["selected_candidates"] = [item for item in state["candidates"] if item["id"] in candidate_ids]
            write_json_atomic(root / "cell_selection.json", snapshot)
            artifact = service.register_artifact(
                run.run_id, "dichromatic_cell_selection", "cell_selection.json", ProvenanceClass.PRIMARY,
                media_type="application/json", metadata={"request_id": request_id, "purpose": state["purpose"]},
            )
            result = {
                "summary": "User confirmed four paired lattice sites defining a near-CSL cell; strain is not applied.",
                "request_id": request_id, "purpose": state["purpose"], "parameters": state["parameters"],
                "selection": selection, "candidate_ids": candidate_ids,
                "selection_artifact": artifact.path, "selection_origin": "browser_user",
                "software": state.get("software", {}),
                "strain_applied": False,
            }
            service.complete_run(run.run_id, result=result, memory_tool="select_dichromatic_cell", conclusion=result)
            state.update(status="selected", revision=state["revision"] + 1)
            self._save(state)
            return self.public(state)

    def cancel(self, request_id: str, *, expected_revision: int) -> dict:
        with self._locked(request_id) as state:
            self._expect(state, expected_revision)
            if state["status"] != "pending":
                raise InteractionConflict("Only a pending selection request can be cancelled")
            if state.get("selection_run_id"):
                self._terminate_attempt(state["selection_run_id"], "The user cancelled this cell selection request.",
                                        cancelled=True)
            state.update(status="cancelled", revision=state["revision"] + 1)
            self._save(state)
            return self.public(state)

    def _run_result(self, run_id: str) -> RunToolResult:
        memory = self.store.memory(self.project_id)
        run = memory.get_run(run_id)
        return RunToolResult(
            run_id=run_id, workflow=run.workflow, status=run.status,
            manifest=run.manifest_path, result=run.result, error=run.error,
            artifacts=[{"artifact_id": item.artifact_id, "role": item.role, "path": item.path,
                        "media_type": item.media_type} for item in memory.query_artifacts(run_id=run_id)],
        )

    def apply(self, request_id: str, *, strain_limit_percent: float = 2.0,
              max_rotation_deg: float = 1.0) -> RunToolResult:
        if (not math.isfinite(strain_limit_percent) or not 0 < strain_limit_percent <= 10
                or not math.isfinite(max_rotation_deg) or not 0 <= max_rotation_deg <= 5):
            raise ValueError("Use strain limits in (0, 10] percent and rotation limits in [0, 5] degrees")
        with self._execution_locked(request_id):
            result = self._apply_owned(request_id, strain_limit_percent=strain_limit_percent,
                                       max_rotation_deg=max_rotation_deg)
            if result.status is RunStatus.COMPLETED and not _current_edge_analysis(result.result.get("edge_analysis")):
                return self._analyze_edges_owned(request_id)
            return result

    def _apply_owned(self, request_id: str, *, strain_limit_percent: float,
                     max_rotation_deg: float) -> RunToolResult:
        from domains.structure.dichromatic.adapter import apply_dichromatic_strain, save_dichromatic_result

        with self._locked(request_id) as state:
            # Holding the execution lease proves that any previous nonterminal
            # attempt has no live owner. Reads alone never rerun the solver.
            self._reconcile(state, execution_owned=True)
            settings = {"strain_limit_percent": strain_limit_percent, "max_rotation_deg": max_rotation_deg}
            if state["status"] == "applied":
                if state.get("strain_settings") != settings:
                    raise InteractionConflict("This selection is already applied with different limits; prepare a new request")
                if state.get("edge_analysis_run_id"):
                    previous = self.store.memory(self.project_id).get_run(state["edge_analysis_run_id"])
                    if previous.status is RunStatus.COMPLETED:
                        return self._run_result(previous.run_id)
                return self._run_result(state["apply_run_id"])
            if state["status"] != "selected":
                raise InteractionConflict("Wait for the user's confirmed cell before applying strain")
            service = self.store.runs(self.project_id)
            selection_run = service.memory.get_run(state["selection_run_id"])
            if selection_run.status is not RunStatus.COMPLETED:
                raise InteractionConflict("The confirmed selection has not been recorded successfully")
            confirmed = selection_run.result
            if (confirmed.get("request_id") != request_id
                    or confirmed.get("selection_origin") != "browser_user"
                    or any(confirmed.get(key) != state.get(key) for key in ("parameters", "candidate_ids", "selection"))):
                raise InteractionConflict("The interaction state differs from the recorded user selection")
            snapshot = dict(state)
            run = service.create_configured_run(
                workflow=Workflow.DICHROMATIC_MAP,
                input_payload={"parameters": confirmed["parameters"], "candidate_ids": confirmed["candidate_ids"], **settings},
                parameters={"memory_tool": "apply_dichromatic_cell_strain", "purpose": confirmed["purpose"],
                            "request_id": request_id, "origin": "confirmed_user_cell", **settings},
                source_run_id=selection_run.run_id,
            )
            state.update(status="applying", apply_run_id=run.run_id, strain_settings=settings,
                         revision=state["revision"] + 1)
            try:
                self._save(state)
            except Exception:
                self._terminate_attempt(run.run_id, "Could not persist the strain calculation interaction state.",
                                        cancelled=True)
                raise
        try:
            service.start_run(run.run_id)
            diagram = apply_dichromatic_strain(
                confirmed["parameters"], confirmed["candidate_ids"],
                selection_candidates=confirmed["selection"]["candidates"],
                **settings,
            )
            root = service.artifacts.run_directory(run.run_id)
            diagram["provenance"] = {"request_id": request_id, "purpose": confirmed["purpose"],
                                     "source_run_id": selection_run.run_id, "origin": "confirmed_user_cell"}
            save_dichromatic_result(diagram, root)
            artifacts = [service.register_artifact(run.run_id, role, filename, provenance) for role, filename, provenance in (
                ("dichromatic_map_data", "dichromatic_map.json", ProvenanceClass.PRIMARY),
                ("dichromatic_map_png", "dichromatic_map.png", ProvenanceClass.DERIVED),
            )]
            result = {
                "summary": "Applied bounded geometric strain and measured all tied shortest nonzero same-layer cross-grain vectors on the four selected edges.",
                "request_id": request_id, "purpose": confirmed["purpose"], "source_run_id": selection_run.run_id,
                "parameters": diagram["parameters"], "candidate_ids": confirmed["candidate_ids"],
                "selection": confirmed["selection"], "strain": diagram["strain"],
                "selected_cell": diagram.get("selected_cell"), "strain_applied": True,
                **self._edge_result_fields(diagram),
                "software": diagram["software"], "units": diagram["units"], "warnings": diagram["warnings"],
                "image_paths": [item.path for item in artifacts if item.role == "dichromatic_map_png"],
                "coordinate_artifact": next(item.path for item in artifacts if item.role == "dichromatic_map_data"),
                "scientific_scope": "Geometric lattice deformation; not energy minimization or atomic relaxation.",
                "interaction": {**self.interaction(snapshot), "status": "applied", "revision": snapshot["revision"] + 2},
            }
            service.complete_run(run.run_id, result=result, memory_tool="apply_dichromatic_cell_strain", conclusion=result)
        except Exception as exc:
            current = service.memory.get_run(run.run_id)
            if current.status in {RunStatus.CONFIGURED, RunStatus.RUNNING}:
                self._terminate_attempt(run.run_id, str(exc)[:2500], cancelled=False)
            with self._locked(request_id) as state:
                if state.get("apply_run_id") == run.run_id and state["status"] != "applied":
                    state.update(status="selected", last_error=str(exc)[:2500])
                    self._save(state)
                elif state.get("apply_run_id") == run.run_id and state["status"] == "applied":
                    return self._run_result(run.run_id)
            raise
        with self._locked(request_id) as state:
            # _reconcile acknowledges completion and stores the exact result.
            return self._run_result(run.run_id)

    def analyze_edges(self, request_id: str) -> RunToolResult:
        """Analyze a previously applied cell without rerunning or mutating its strain run."""
        with self._execution_locked(request_id):
            return self._analyze_edges_owned(request_id)

    def _analyze_edges_owned(self, request_id: str) -> RunToolResult:
        from domains.structure.dichromatic.adapter import (
            attach_dichromatic_edge_analysis, rebuild_strained_dichromatic_view, save_dichromatic_result,
        )

        with self._locked(request_id) as state:
            if state["status"] != "applied":
                raise InteractionConflict("Apply strain to the confirmed selection before measuring its edges")
            service = self.store.runs(self.project_id)
            source = service.memory.get_run(state["apply_run_id"])
            if source.status is not RunStatus.COMPLETED:
                raise InteractionConflict("The strain run has not completed successfully")
            if _current_edge_analysis(source.result.get("edge_analysis")):
                return self._run_result(source.run_id)
            replaced = source
            if state.get("edge_analysis_run_id"):
                previous = service.memory.get_run(state["edge_analysis_run_id"])
                if previous.status is RunStatus.COMPLETED:
                    if _current_edge_analysis(previous.result.get("edge_analysis")):
                        return self._run_result(previous.run_id)
                    replaced = previous
                else:
                    self._terminate_attempt(previous.run_id, "Previous edge analysis was interrupted; explicit retry requested.",
                                            cancelled=True)
            previous_report = replaced.result.get("edge_analysis") or {}
            upgrade = {"previous_run_id": replaced.run_id, "previous_method": previous_report.get("method"),
                       "previous_analysis_version": previous_report.get("analysis_version"),
                       "reason": "Derive all-layer finite-edge vectors from the saved strain; preserve historical results."}
            run = service.create_configured_run(
                workflow=Workflow.DICHROMATIC_MAP, source_run_id=source.run_id,
                input_payload={"request_id": request_id, "source_run_id": source.run_id, "analysis_upgrade": upgrade},
                parameters={"memory_tool": "analyze_dichromatic_cell_edges", "request_id": request_id,
                            "purpose": source.result["purpose"], "origin": "saved_strain_edge_analysis",
                            "analysis_upgrade": upgrade},
            )
            state["edge_analysis_run_id"] = run.run_id
            try:
                self._save(state)
                service.start_run(run.run_id)
                diagram = rebuild_strained_dichromatic_view(
                    source.result["parameters"], source.result["strain"], source.result["selected_cell"],
                )
                attach_dichromatic_edge_analysis(diagram)
                diagram["provenance"] = {"request_id": request_id, "purpose": source.result["purpose"],
                                         "source_run_id": source.run_id, "origin": "saved_strain_edge_analysis"}
                root = service.artifacts.run_directory(run.run_id)
                save_dichromatic_result(diagram, root)
                artifacts = [service.register_artifact(run.run_id, role, filename, provenance) for role, filename, provenance in (
                    ("dichromatic_map_data", "dichromatic_map.json", ProvenanceClass.PRIMARY),
                    ("dichromatic_map_png", "dichromatic_map.png", ProvenanceClass.DERIVED),
                )]
                result = {
                    "summary": "Measured the shortest nonzero edge vectors using saved strain; no strain refitting was performed.",
                    "request_id": request_id, "purpose": source.result["purpose"], "source_run_id": source.run_id,
                    **{key: diagram[key] for key in ("parameters", "strain", "selected_cell", "software", "units", "warnings")},
                    **self._edge_result_fields(diagram),
                    "strain_applied": True, "strain_reused": True,
                    "analysis_upgrade": upgrade,
                    "image_paths": [item.path for item in artifacts if item.role == "dichromatic_map_png"],
                    "coordinate_artifact": next(item.path for item in artifacts if item.role == "dichromatic_map_data"),
                    "interaction": {**self.interaction(state), "revision": state["revision"] + 1},
                }
                service.complete_run(run.run_id, result=result, memory_tool="analyze_dichromatic_cell_edges", conclusion=result)
            except Exception as exc:
                self._terminate_attempt(run.run_id, str(exc)[:2500], cancelled=False)
                raise
            self._reconcile(state)
            return self._run_result(run.run_id)
