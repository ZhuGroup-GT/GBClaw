"""Structure-agent and browser exports through the shared headless map CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

from langchain_core.tools import BaseTool, tool

from core.paths import SRC_ROOT
from core.execution.process import run_cancellable
from core.util.graph_config import runtime_project_id
from core.settings.env import get_env_int
from project_workspace.artifacts.models import ProvenanceClass
from project_memory.models import RunStatus, Workflow
from project_workspace.store import ProjectStore
from project_memory.publication.results import RunToolResult
from domains.structure.dichromatic.adapter import DichromaticMapRequest, dichromatic_options


def export_project_dichromatic_map(
    store: ProjectStore,
    project_id: str,
    parameters: dict[str, Any],
    *,
    purpose: str = "Inspect a grain-boundary dichromatic pattern.",
    source_run_id: str | None = None,
    origin: str = "structure_agent",
) -> RunToolResult:
    """Persist inputs, numeric data, PNG, and purpose in one project workflow."""
    params = DichromaticMapRequest.model_validate(parameters).model_dump(mode="json")
    purpose = purpose.strip()
    if not purpose or len(purpose) > 2000:
        raise ValueError("purpose must contain between 1 and 2000 characters")
    service = store.runs(project_id)
    if source_run_id:
        source = service.memory.get_run(source_run_id)
        if source.status is not RunStatus.COMPLETED:
            raise ValueError("The source run must be completed")
    run = service.create_configured_run(
        workflow=Workflow.DICHROMATIC_MAP,
        memory_tool="render_dichromatic_map",
        input_payload=params,
        parameters={"purpose": purpose, "origin": origin, "parameters": params},
        source_run_id=source_run_id,
    )
    root = service.artifacts.run_directory(run.run_id)
    artifacts = []
    try:
        service.start_run(run.run_id)
        command = [
            sys.executable, "-m", "domains.structure.dichromatic.adapter",
            "--input-json", str(root / "input.json"), "--output-dir", str(root),
        ]
        environment = os.environ.copy()
        src = str(SRC_ROOT)
        environment["PYTHONPATH"] = os.pathsep.join(
            part for part in (src, environment.get("PYTHONPATH", "")) if part
        )
        environment["MPLBACKEND"] = "Agg"
        timeout = get_env_int("GBCLAW_RENDER_TIMEOUT_SEC", 600, minimum=30)
        with (root / "render.log").open("w", encoding="utf-8") as log:
            process = run_cancellable(
                command, env=environment, cwd=src, stdout=log,
                stderr=subprocess.STDOUT, timeout=timeout, label="DichromaticMap export",
            )
        if process.returncode:
            error = (root / "render.log").read_text(encoding="utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"DichromaticMap export failed: {error}")
        metadata = json.loads((root / "dichromatic_map.json").read_text(encoding="utf-8"))
        metadata["provenance"] = {
            "run_id": run.run_id, "workflow": Workflow.DICHROMATIC_MAP.value,
            "purpose": purpose, "origin": origin, "source_run_id": source_run_id,
        }
        (root / "dichromatic_map.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        for role, filename, provenance in (
            ("dichromatic_map_png", "dichromatic_map.png", ProvenanceClass.DERIVED),
            ("dichromatic_map_data", "dichromatic_map.json", ProvenanceClass.PRIMARY),
            ("render_log", "render.log", ProvenanceClass.DEBUG),
        ):
            artifacts.append(service.register_artifact(
                run.run_id, role, filename, provenance,
                metadata={"tool": "render_dichromatic_map", "purpose": purpose},
            ))
        image_paths = [item.path for item in artifacts if item.role == "dichromatic_map_png"]
        # Keep large coordinate arrays in the scientific artifact, outside the
        # agent's immediate context and the semantic-summary working set.
        result = {
            "summary": f"Dichromatic pattern rendered: {purpose}",
            "purpose": purpose,
            "origin": origin,
            "parameters": metadata.get("parameters", params),
            "image_paths": image_paths,
            "source_run_id": source_run_id,
            "counts": metadata.get("summary", {}),
            "geometry": metadata.get("geometry", {}),
            "exact_csl_cell": metadata.get("csl_cell"),
            "units": metadata.get("units", {}),
            "software": metadata.get("software", {}),
            "warnings": metadata.get("warnings", []),
            "coordinate_artifact": next(item.path for item in artifacts if item.role == "dichromatic_map_data"),
            "scientific_scope": "Ideal cubic lattice projection; no atomic relaxation or elastic-energy minimization.",
        }
        terminal = service.complete_run(
            run.run_id, result=result, memory_tool="render_dichromatic_map",
            conclusion={"conclusion_type": "dichromatic_map", **result},
        )
        result = terminal.result
    except Exception as exc:
        error = (f"DichromaticMap export timed out after {timeout}s"
                 if isinstance(exc, subprocess.TimeoutExpired) else str(exc))
        terminal = service.fail_run(run.run_id, error=error[:2500])
        if (root / "render.log").is_file():
            artifacts.append(service.register_artifact(
                run.run_id, "render_log", "render.log", ProvenanceClass.DEBUG,
            ))
        result = {}
    return RunToolResult(
        run_id=run.run_id, workflow=terminal.workflow, status=terminal.status,
        manifest=terminal.manifest_path,
        artifacts=[{
            "artifact_id": item.artifact_id, "role": item.role, "path": item.path,
            "media_type": item.media_type,
        } for item in artifacts],
        result=result, error=terminal.error,
    )


def make_dichromatic_map_tools(store: ProjectStore | None = None) -> list[BaseTool]:
    def interactions():
        from domains.structure.dichromatic.interactions import DichromaticInteractionService

        project_id = runtime_project_id()
        if store is None or not project_id:
            raise ValueError("Select a project before preparing an interactive cell selection")
        return DichromaticInteractionService(store, project_id)

    @tool
    def prepare_dichromatic_cell_selection(
        purpose: str, parameters: DichromaticMapRequest | None = None, source_run_id: str | None = None,
    ) -> str:
        """Open and prefill the floating DichromaticMap widget for initial setup and user picking.

        Use for initial setup, interactive exploration, or near-CSL cell selection.
        Supply lattice, actual axis/angle, a0, layers, cutoff and view in parameters.
        To open a previously rendered unstrained map, omit parameters and pass its
        completed dichromatic_map source_run_id; saved settings and lineage are reused.
        No PNG export is needed. Preparation stops before picking a cell or applying
        strain, including when the task says not to select points or apply strain yet.
        The frontend opens the numerical picker with these settings automatically.
        Return the interaction ID and wait for the
        user's next turn; never guess candidate IDs or infer them from a PNG.
        Four clicks are four paired sites in one axial layer, in perimeter order.
        """
        try:
            service = interactions()
            request = service.create(parameters.model_dump(mode="json") if parameters is not None else None, purpose=purpose,
                                     source_run_id=source_run_id)
            request.pop("candidates", None)
            request["status"] = "awaiting_user"
            return json.dumps(request, ensure_ascii=False)
        except (ValueError, ImportError) as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

    @tool
    def get_dichromatic_cell_selection(request_id: str) -> str:
        """Read the user's saved cell selection by request ID in the active project.

        Use the stored numeric selection as authoritative. Pending requests need
        user input; selected requests can proceed to apply_dichromatic_cell_strain.
        """
        try:
            return json.dumps(interactions().get(request_id, compact=True), ensure_ascii=False)
        except (ValueError, ImportError) as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

    @tool
    def apply_dichromatic_cell_strain(
        request_id: str, strain_limit_percent: float = 2.0, max_rotation_deg: float = 1.0,
    ) -> str:
        """Apply library strain fitting only to a user-confirmed near-CSL cell.

        Loads the four actual paired sites from project state, checks the library's
        strain/rotation limits, saves the deformed map and numerical transformations,
        and publishes memory linked to the user selection. This is geometric
        fitting, not LAMMPS relaxation. Repeating an applied request reuses saved F;
        obsolete selected-layer measurements are upgraded in a separate linked run.
        edge_vector_summary reports each edge's all-layer minimum, tied-pair count,
        and explicit G1/G2 lattice notations for representative directed vectors.
        Include these numbers and notations in the answer, even when strain is the
        main task. Full ties and annotations remain in the coordinate artifact.
        These are geometric Burgers-vector candidates, subject to the reported flags
        and reconstruction errors, not proof of a physical dislocation.
        """
        try:
            service = interactions()
            result = service.apply(request_id, strain_limit_percent=strain_limit_percent,
                                   max_rotation_deg=max_rotation_deg)
            return result.model_copy(update={"result": service.compact_result(result.result)}).to_json()
        except (ValueError, ImportError) as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

    @tool
    def analyze_dichromatic_cell_edges(request_id: str) -> str:
        """Measure edge-vector candidates on a saved applied cell without refitting.

        Current all-layer results are reused. Older selected-layer results create
        a derived run linked to the saved strain, retaining all tied shortest nonzero
        same-layer G1-to-G2 pairs after comparing every axial layer on each edge.
        Report edge_vector_summary: per-edge distances, minimum layers, G1/G2
        lattice notations, and candidate flags. Full ties stay in the artifact.
        Does not refit strain or identify physical dislocations.
        """
        try:
            service = interactions()
            result = service.analyze_edges(request_id)
            return result.model_copy(update={"result": service.compact_result(result.result)}).to_json()
        except (ValueError, ImportError) as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

    @tool
    def get_dichromatic_map_options(lattice: str = "FCC", axis: str = "110") -> str:
        """Get supported dichromatic-map layers, angle range and CSL presets for an axis."""
        try:
            return json.dumps(dichromatic_options(lattice, axis), ensure_ascii=False)
        except (ValueError, ImportError) as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

    @tool
    def render_dichromatic_map(
        parameters: DichromaticMapRequest,
        purpose: str,
        source_run_id: str | None = None,
    ) -> str:
        """Render an SC/FCC/BCC grain dichromatic map via the headless CLI.

        Preserve the actual supplied misorientation. Include why this diagram is
        needed in purpose, and source_run_id when reusing completed crystallography.
        Returns a saved PNG, numeric JSON, normalized inputs and a durable memory run.
        Coordinates are in a0 units. Near pairs do not establish an exact CSL or a
        physically relaxed boundary; the displayed 2D cell is layer-preserving.
        """
        project_id = runtime_project_id()
        if store is None or not project_id:
            return json.dumps({"status": "error", "error": "Select a project before exporting a dichromatic map."})
        try:
            return export_project_dichromatic_map(
                store, project_id, parameters.model_dump(mode="json"),
                purpose=purpose, source_run_id=source_run_id,
            ).to_json()
        except (ValueError, ImportError) as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

    return [get_dichromatic_map_options, render_dichromatic_map,
            prepare_dichromatic_cell_selection, get_dichromatic_cell_selection,
            apply_dichromatic_cell_strain, analyze_dichromatic_cell_edges]
