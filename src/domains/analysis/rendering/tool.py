"""Agent tools that render atomic structures and GB plan views with OVITO in a subprocess."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from langchain_core.tools import tool

from core.execution.process import run_cancellable
from core.settings.env import get_env_int
from core.util.graph_config import runtime_project_id
from core.util.io import write_json_atomic
from domains.analysis.common import verified_artifact_path
from project_memory.models import RunSourceRecord, RunStatus, Workflow
from project_memory.publication.results import RunToolResult
from project_workspace.artifacts.models import ProvenanceClass
from project_workspace.store import ProjectStore

_VALID_ANALYSIS_MODES = frozenset({"CNA", "DXA", "none"})


# Rendering runs the ovito_rendering.py CLI in a subprocess (GBclaw env, in-process OVITO
# would crash the server). Uses the same interpreter as the server.
_RENDER_SCRIPT = Path(__file__).resolve().parent / "ovito_rendering.py"


_PLAN_VIEW_SCRIPT = Path(__file__).resolve().parent / "gb_plan_view.py"


RENDER_TIMEOUT_SEC = get_env_int(
    "GBCLAW_RENDER_TIMEOUT_SEC", 600, minimum=30
)


def _run_render_subprocess(
    *,
    cfg_abs: Path,
    output_abs: Path,
    analysis_mode: str,
    hide_structure_types: list[int] | None,
    dxa_lattice: str,
    reference: Path | None = None,
    arrow_width: float = 0.1,
    arrow_offset_z: float = 1.0,
    arrow_target_length: float = 1.5,
    arrow_scale_mode: str = "physical_capped",
    arrow_max_length: float = 2.0,
    minimum_image_convention: bool = True,
    pbc: list[bool] | None = None,
) -> subprocess.CompletedProcess:
    """Render in an isolated subprocess so OVITO crashes cannot kill the server."""
    argv = [
        sys.executable,
        str(_RENDER_SCRIPT),
        str(cfg_abs),
        "-o",
        str(output_abs),
        "--mode",
        analysis_mode,
        "--dxa-lattice",
        dxa_lattice,
        "--arrow-width", str(arrow_width),
        "--arrow-offset-z", str(arrow_offset_z),
        "--arrow-target-length", str(arrow_target_length),
        "--arrow-scale-mode", arrow_scale_mode,
        "--arrow-max-length", str(arrow_max_length),
    ]
    if hide_structure_types:
        argv.append("--hide-types")
        argv.extend(str(int(value)) for value in hide_structure_types)
    if reference is not None:
        argv.extend(["--reference", str(reference)])
    if pbc is not None:
        argv.extend(["--pbc", " ".join("p" if value else "f" for value in pbc)])
    if not minimum_image_convention:
        argv.append("--no-minimum-image")
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment.setdefault(
        "XDG_CACHE_HOME", str(Path(tempfile.gettempdir()) / "gbclaw-render-cache")
    )

    return run_cancellable(
        argv, timeout=RENDER_TIMEOUT_SEC, env=environment, label="OVITO render",
    )


def _run_plan_view_subprocess(
    *, cfg_abs: Path, output_dir: Path, gb_y: float | None,
    slab_thickness: float | None, pbc: list[bool] | None,
    image_width: int, view_width: float | None,
    reference: Path | None = None, atom_scale: float = 0.7,
    arrow_width: float = 0.1, arrow_offset_y: float = 1.0,
    arrow_target_length: float = 1.5, minimum_image_convention: bool = True,
    arrow_scale_mode: str = "physical_capped", arrow_max_length: float = 2.0,
) -> subprocess.CompletedProcess:
    """Keep OVITO and its Qt renderer outside the API server process."""
    argv = [
        sys.executable, str(_PLAN_VIEW_SCRIPT), str(cfg_abs),
        "--output-dir", str(output_dir), "--image-width", str(image_width),
        "--atom-scale", str(atom_scale), "--arrow-width", str(arrow_width),
        "--arrow-offset-y", str(arrow_offset_y),
        "--arrow-target-length", str(arrow_target_length),
        "--arrow-scale-mode", arrow_scale_mode,
        "--arrow-max-length", str(arrow_max_length),
    ]
    for option, value in (
        ("--gb-y", gb_y), ("--slab-thickness", slab_thickness),
        ("--view-width", view_width),
    ):
        if value is not None:
            argv.extend([option, str(value)])
    if pbc is not None:
        argv.extend(["--pbc", " ".join("p" if value else "f" for value in pbc)])
    if reference is not None:
        argv.extend(["--reference", str(reference)])
    if not minimum_image_convention:
        argv.append("--no-minimum-image")
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment.setdefault(
        "XDG_CACHE_HOME", str(Path(tempfile.gettempdir()) / "gbclaw-render-cache")
    )
    return run_cancellable(
        argv, timeout=RENDER_TIMEOUT_SEC, env=environment, label="GB plan-view render",
    )


@tool
def render_atomic_structure(
    source_run_id: str,
    source_artifact_role: str = "minimized_cfg",
    source_step: int | None = None,
    analysis_mode: str = "CNA",
    hide_structure_types: list[int] | None = None,
    dxa_lattice: str = "bcc",
    source_artifact_id: str | None = None,
    reference_run_id: str | None = None,
    reference_artifact_role: str = "minimized_cfg",
    reference_step: int | None = None,
    reference_artifact_id: str | None = None,
    arrow_width: float = 0.1,
    arrow_offset_z: float = 1.0,
    arrow_target_length: float = 1.5,
    arrow_scale_mode: str = "physical_capped",
    arrow_max_length: float = 2.0,
    minimum_image_convention: bool = True,
    pbc: list[bool] | None = None,
    purpose: str = "Inspect atomic structure",
) -> str:
    """Render an OVITO cfg structure to PNG for human inspection.

    Resolves a CFG artifact by source run ID and role, then creates a linked render run.
    source_step: required when ``source_artifact_role="intermediate_structure"`` has
      multiple shear frames; it selects the artifact whose indexed metadata step matches.
    analysis_mode: ``CNA`` (default), ``DXA``, or ``none``. Prefer CNA unless asked otherwise.
    hide_structure_types: optional CNA type ids to remove before rendering
      (0=Other, 1=FCC, 2=HCP, 3=BCC, 4=ICO). **Default is None — keep all atoms.**
      Pass this only when the task explicitly asks to hide structure types.
    dxa_lattice: input lattice for DXA — ``bcc``, ``fcc``, or ``hcp``.
    source_artifact_id: exact current CFG selector for batch output artifacts.

    Optional ``reference_run_id`` enables displacement arrows in this top view
    (direction -Z; displayed plane XY). Reference role/step/artifact ID select its
    exact indexed CFG. Displacements match unique particle IDs before hiding CNA
    types. Both structures must contain the same IDs. Without a reference the
    overlay is disabled and the existing atom appearance is preserved.

    Arrows are centered, width 0.1, and visually offset +1 along Z by default.
    ``physical_capped`` does not magnify small displacements. All 3D arrows are
    uniformly capped by ``arrow_max_length`` (default and hard maximum 2 input
    length units, angstroms for GBclaw structures). ``median_capped`` optionally
    targets ``arrow_target_length`` while retaining the same hard cap.
    Positions and physical displacement vectors remain unchanged; no affine cell
    mapping or drift subtraction is applied. Minimum-image handling defaults on;
    disable only for suitably unwrapped coordinates. ``pbc`` optionally supplies
    X/Y/Z periodic flags. JSON preserves full vectors, settings, both original
    source-frame histories, and purpose; the returned summary omits long arrays.
    """
    project_id = runtime_project_id()
    if not project_id:
        return "No active project_id in run config. Renders must be written under the project."

    mode = analysis_mode.strip().upper()
    if mode == "NONE":
        mode = "none"
    if mode not in _VALID_ANALYSIS_MODES:
        return f"Invalid analysis_mode {analysis_mode!r}. Use CNA, DXA, or none."
    if not source_run_id.strip() or not purpose.strip():
        return "source_run_id and purpose must not be empty"
    if reference_run_id is not None and not reference_run_id.strip():
        return "reference_run_id must not be empty"
    if reference_run_id is None and (reference_step is not None or reference_artifact_id is not None):
        return "reference_step and reference_artifact_id require reference_run_id"
    from domains.analysis.rendering.ovito_rendering import validate_arrow_policy
    try:
        validate_arrow_policy(arrow_scale_mode, arrow_max_length)
    except ValueError as exc:
        return str(exc)
    for name, value in (("arrow_width", arrow_width), ("arrow_offset_z", arrow_offset_z),
                        ("arrow_target_length", arrow_target_length)):
        positive = name != "arrow_offset_z"
        if not math.isfinite(value) or (positive and value <= 0):
            return f"{name} must be finite" + (" and positive" if positive else "")
    if pbc is not None and (len(pbc) != 3 or not all(isinstance(value, bool) for value in pbc)):
        return "pbc must contain exactly three booleans for X, Y, and Z"

    store = ProjectStore()
    service = store.runs(project_id)
    artifacts = []
    try:
        from domains.lammps.common.lineage import (
            find_structure_origin,
            find_structure_source,
        )

        project_root = store.project_path(project_id)

        def resolve_structure(run_id, role, step, artifact_id, selector):
            owner = service.memory.get_run(run_id)
            candidates = service.memory.query_artifacts(run_id=owner.run_id, role=role)
            matches = candidates
            if artifact_id is not None:
                matches = [item for item in matches if item.artifact_id == artifact_id]
            if step is not None:
                matches = [item for item in matches if item.metadata.get("step") is not None
                           and int(item.metadata["step"]) == int(step)]
            if not matches:
                available_steps = sorted({int(item.metadata["step"]) for item in candidates
                                          if item.metadata.get("step") is not None})
                raise ValueError(
                    f"{selector} run {run_id} has no matching artifact role {role!r} "
                    f"for step {step} and artifact ID {artifact_id}; available steps: {available_steps}"
                )
            if len(matches) > 1:
                raise ValueError(
                    f"multiple artifacts match role {role!r}; pass {selector}_artifact_id "
                    f"for a batch item or {selector}_step for a shear frame."
                )
            selected = matches[0]
            path = verified_artifact_path(
                store=store, service=service, project_id=project_id,
                run_id=owner.run_id, artifact=selected,
            )
            if path.suffix.lower() != ".cfg":
                raise ValueError(f"{selector} artifact must be a .cfg file: {selected.path}")
            return (owner, selected, path, find_structure_source(project_root, service, path),
                    find_structure_origin(project_root, service, path))

        source_run, selected, cfg_abs, source, origin = resolve_structure(
            source_run_id, source_artifact_role, source_step, source_artifact_id, "source",
        )
        metadata = {
            "render_kind": "atomic_structure", "source_run_id": source_run.run_id,
            "source_artifact_id": selected.artifact_id, "source_artifact_role": selected.role,
            "source_step": selected.metadata.get("step", source_step),
            "structure_source": source, "origin": origin, "purpose": purpose,
            "analysis_mode": mode,
        }
        fingerprint = "sha256:" + source["sha256"]
        sources = [RunSourceRecord(
            source_run_id=source_run.run_id, relation="structure_input", ordinal=0,
            source_fingerprint=fingerprint, metadata={"structure_source": source, "purpose": purpose},
        )]
        if origin.get("run_id") and origin["run_id"] != source_run.run_id:
            sources.append(RunSourceRecord(
                source_run_id=origin["run_id"], relation="origin_frame", ordinal=0,
                source_fingerprint=("sha256:" + origin["sha256"] if origin.get("sha256") else None),
                metadata=origin,
            ))
        reference_abs = None
        if reference_run_id is not None:
            reference_run, reference_artifact, reference_abs, reference_source, reference_origin = resolve_structure(
                reference_run_id, reference_artifact_role, reference_step, reference_artifact_id, "reference",
            )
            reference_metadata = {
                "reference_run_id": reference_run.run_id,
                "reference_artifact_id": reference_artifact.artifact_id,
                "reference_artifact_role": reference_artifact.role,
                "reference_step": reference_artifact.metadata.get("step", reference_step),
                "reference_structure_source": reference_source, "reference_origin": reference_origin,
            }
            metadata.update(reference_metadata)
            sources.append(RunSourceRecord(
                source_run_id=reference_run.run_id, relation="displacement_reference", ordinal=0,
                source_fingerprint="sha256:" + reference_source["sha256"],
                metadata={**reference_metadata, "purpose": purpose},
            ))
            if reference_origin.get("run_id") and reference_origin["run_id"] != reference_run.run_id:
                sources.append(RunSourceRecord(
                    source_run_id=reference_origin["run_id"], relation="reference_origin_frame", ordinal=0,
                    source_fingerprint=("sha256:" + reference_origin["sha256"] if reference_origin.get("sha256") else None),
                    metadata=reference_origin,
                ))
        with service.tool_run(
            "render_atomic_structure", source_run_id=source_run.run_id,
            source_fingerprint=fingerprint, sources=sources,
            parameters={
                **metadata,
                "hide_structure_types": hide_structure_types or [],
                "dxa_lattice": dxa_lattice, "arrow_width": arrow_width,
                "arrow_offset_z": arrow_offset_z, "arrow_target_length": arrow_target_length,
                "arrow_scale_mode": arrow_scale_mode, "arrow_max_length": arrow_max_length,
                "minimum_image_convention": minimum_image_convention, "pbc": pbc,
            },
        ) as session:
            run = session.run
            output_abs = session.artifact_directory / "render.png"
            completed = _run_render_subprocess(
                cfg_abs=cfg_abs, output_abs=output_abs, analysis_mode=mode,
                hide_structure_types=hide_structure_types, dxa_lattice=dxa_lattice,
                reference=reference_abs, arrow_width=arrow_width, arrow_offset_z=arrow_offset_z,
                arrow_target_length=arrow_target_length,
                arrow_scale_mode=arrow_scale_mode, arrow_max_length=arrow_max_length,
                minimum_image_convention=minimum_image_convention, pbc=pbc,
            )
            data_path = output_abs.with_suffix(".json")
            report = None
            if data_path.is_file():
                report = json.loads(data_path.read_text(encoding="utf-8"))
                if not isinstance(report, dict):
                    raise ValueError("OVITO rendering diagnostics must be a JSON object")
                report.update(metadata)
                write_json_atomic(data_path, report)
                data_artifact = session.register_artifact(
                    "render_data", data_path.name, ProvenanceClass.DERIVED,
                    media_type="application/json", metadata=metadata,
                )
                artifacts.append(data_artifact)
            if completed.returncode != 0 or not output_abs.is_file():
                detail = (report or {}).get("error") or completed.stderr or completed.stdout or "no subprocess output"
                raise ValueError(f"OVITO rendering failed (exit {completed.returncode}): {str(detail).strip()}")
            if report is None or report.get("status") != "completed":
                raise ValueError("OVITO rendering did not produce completed JSON diagnostics")
            artifact = session.register_artifact(
                "render", output_abs.name, ProvenanceClass.DERIVED,
                media_type="image/png", metadata=metadata,
            )
            artifacts.append(artifact)
            result = {
                **metadata, "render": artifact.path, "data": data_artifact.path,
                "camera": report.get("camera"), "warnings": report.get("warnings", []),
                "displacement": ({key: value for key, value in report["displacement"].items()
                                  if key != "selected"} if report.get("displacement") is not None else None),
                "summary": report.get("summary") or (
                    f"{mode} atomic structure rendered in XY top view"
                    + (" with displacement vectors from the selected reference." if reference_abs is not None else ".")
                ),
            }
            terminal = session.complete(
                result=result,
                conclusion={"conclusion_type": "atomic_structure_render", **result},
            )
            result = terminal.result
    except Exception as exc:
        if "run" not in locals():
            return str(exc)
        error = (f"OVITO rendering timed out after {RENDER_TIMEOUT_SEC}s"
                 if isinstance(exc, subprocess.TimeoutExpired) else str(exc))
        terminal = service.memory.get_run(run.run_id)
        if terminal.status is RunStatus.RUNNING:
            terminal = service.fail_run(run.run_id, error=error[:2000])
        return RunToolResult(
            run_id=run.run_id, workflow=terminal.workflow, status=terminal.status,
            manifest=terminal.manifest_path, error=terminal.error,
            artifacts=[{"role": item.role, "path": item.path} for item in artifacts],
        ).to_json()

    return RunToolResult(
        run_id=run.run_id, workflow=terminal.workflow, status=terminal.status,
        manifest=terminal.manifest_path,
        artifacts=[{"role": item.role, "path": item.path} for item in artifacts],
        result=result,
    ).to_json()


@tool
def render_gb_plan_view(
    source_run_id: str,
    source_artifact_role: str = "minimized_cfg",
    source_step: int | None = None,
    source_artifact_id: str | None = None,
    gb_y: float | None = None,
    slab_thickness: float | None = None,
    pbc: list[bool] | None = None,
    image_width: int = 3200,
    view_width: float | None = None,
    purpose: str = "Inspect the grain-boundary plane",
    reference_run_id: str | None = None,
    reference_artifact_role: str = "minimized_cfg",
    reference_step: int | None = None,
    reference_artifact_id: str | None = None,
    atom_scale: float = 0.7,
    arrow_width: float = 0.1,
    arrow_offset_y: float = 1.0,
    arrow_target_length: float = 1.5,
    arrow_scale_mode: str = "physical_capped",
    arrow_max_length: float = 2.0,
    minimum_image_convention: bool = True,
) -> str:
    """Estimate a Y-normal grain-boundary location and render its XZ plan view.

    Select an indexed CFG by source run and role. For a batch, specify its exact
    ``source_artifact_id``; for shear frames use role ``intermediate_structure``
    with ``source_step``. Ambiguous selections require an explicit selector.
    CNA and atomic-layer spacing provide an approximate boundary location, with
    exterior surface atoms excluded from detection. The default slab covers one
    terminal layer on either side. ``gb_y`` and ``slab_thickness`` override the
    corresponding estimates, in imported Cartesian length units (normally angstroms).
    ``pbc`` optionally supplies X/Y/Z flags
    because CFG files do not preserve the original simulation boundary settings.

    Camera direction is (0,-1,0), with X right and Z down. Atoms below the estimated
    boundary are black, and atoms above are milk white. PNG backgrounds are transparent.
    Colors describe geometric sides,
    not an independent crystallographic grain identification. ``view_width`` sets
    the centered image width in the same length units; the default fits the selected slab.
    Outputs include the PNG view, an optional detail view, the Y-profile diagnostic,
    and authoritative JSON with layers, uncertainty, and original-frame provenance.

    ``atom_scale`` scales OVITO's imported particle radii (default 70%). Optionally
    select a reference CFG with ``reference_run_id`` and its role, step, or exact
    artifact ID. Displacements match unique particle IDs and point from reference
    to current coordinates; both structures must contain the same ID set. No
    affine cell mapping or global drift subtraction is applied. The default uses
    minimum-image displacements; disable only for suitably unwrapped coordinates.
    Native arrows are centered, width 0.1, and visually shifted by +1 along Y;
    atomic coordinates and displacement values remain unchanged. By default
    small displacements are not magnified. Both ``physical_capped`` and optional
    ``median_capped`` uniformly cap 3D arrows at ``arrow_max_length`` (at most 2
    input length units, angstroms for GBclaw structures). ``arrow_target_length``
    is used only by median_capped. Full physical vectors remain in JSON.
    """
    project_id = runtime_project_id()
    if not project_id:
        return "No active project_id in run config. Renders must be written under the project."
    if not source_run_id.strip():
        return "source_run_id must not be empty"
    if not purpose.strip():
        return "purpose must not be empty"
    if reference_run_id is not None and not reference_run_id.strip():
        return "reference_run_id must not be empty"
    if reference_run_id is None and (reference_step is not None or reference_artifact_id is not None):
        return "reference_step and reference_artifact_id require reference_run_id"
    from domains.analysis.rendering.ovito_rendering import validate_arrow_policy
    try:
        validate_arrow_policy(arrow_scale_mode, arrow_max_length)
    except ValueError as exc:
        return str(exc)
    for name, value in (
        ("gb_y", gb_y), ("slab_thickness", slab_thickness), ("view_width", view_width),
        ("atom_scale", atom_scale), ("arrow_width", arrow_width),
        ("arrow_offset_y", arrow_offset_y), ("arrow_target_length", arrow_target_length),
    ):
        positive = name not in {"gb_y", "arrow_offset_y"}
        if value is not None and (not math.isfinite(value) or (positive and value <= 0)):
            return f"{name} must be finite" + (" and positive" if positive else "")
    if image_width < 256 or image_width > 8192:
        return "image_width must be between 256 and 8192"
    if pbc is not None and (len(pbc) != 3 or not all(isinstance(value, bool) for value in pbc)):
        return "pbc must contain exactly three booleans for X, Y, and Z"

    store = ProjectStore()
    service = store.runs(project_id)
    artifacts = []
    try:
        from domains.lammps.common.lineage import (
            find_structure_origin,
            find_structure_source,
        )

        project_root = store.project_path(project_id)

        def resolve_structure(run_id, role, step, artifact_id, selector):
            owner = service.memory.get_run(run_id)
            matches = service.memory.query_artifacts(run_id=owner.run_id, role=role)
            if artifact_id is not None:
                matches = [item for item in matches if item.artifact_id == artifact_id]
            if step is not None:
                matches = [item for item in matches if item.metadata.get("step") == step]
            if len(matches) != 1:
                raise ValueError(
                    f"Expected exactly one {role!r} CFG in run {run_id}; "
                    f"found {len(matches)} matching artifacts. Specify {selector}_artifact_id "
                    f"for a batch item or {selector}_step for a shear frame."
                )
            artifact = matches[0]
            path = verified_artifact_path(
                store=store, service=service, project_id=project_id,
                run_id=owner.run_id, artifact=artifact,
            )
            if path.suffix.lower() != ".cfg":
                raise ValueError(f"{selector} artifact must be a .cfg file: {artifact.path}")
            return (owner, artifact, path,
                    find_structure_source(project_root, service, path),
                    find_structure_origin(project_root, service, path))

        source_run, selected, cfg_abs, source, origin = resolve_structure(
            source_run_id, source_artifact_role, source_step, source_artifact_id, "source",
        )
        reference_abs = None
        reference_metadata = {}
        reference_sources = []
        if reference_run_id is not None:
            reference_run, reference_artifact, reference_abs, reference_source, reference_origin = resolve_structure(
                reference_run_id, reference_artifact_role, reference_step, reference_artifact_id, "reference",
            )
            reference_metadata = {
                "reference_run_id": reference_run.run_id,
                "reference_artifact_id": reference_artifact.artifact_id,
                "reference_artifact_role": reference_artifact.role,
                "reference_step": reference_artifact.metadata.get("step", reference_step),
                "reference_structure_source": reference_source,
                "reference_origin": reference_origin,
            }
            reference_sources.append(RunSourceRecord(
                source_run_id=reference_run.run_id, relation="displacement_reference", ordinal=0,
                source_fingerprint="sha256:" + reference_source["sha256"],
                metadata={**reference_metadata, "purpose": purpose},
            ))
            if reference_origin.get("run_id") and reference_origin["run_id"] != reference_run.run_id:
                reference_sources.append(RunSourceRecord(
                    source_run_id=reference_origin["run_id"], relation="reference_origin_frame", ordinal=0,
                    source_fingerprint=("sha256:" + reference_origin["sha256"] if reference_origin.get("sha256") else None),
                    metadata=reference_origin,
                ))
        fingerprint = "sha256:" + source["sha256"]
        source_metadata = {
            "source_run_id": source_run.run_id,
            "source_artifact_id": selected.artifact_id,
            "source_artifact_role": selected.role,
            "source_step": selected.metadata.get("step", source_step),
            "structure_source": source, "origin": origin, "purpose": purpose,
            "render_kind": "gb_plan_view",
            **reference_metadata,
        }
        sources = [RunSourceRecord(
            source_run_id=source_run.run_id, relation="structure_input", ordinal=0,
            source_fingerprint=fingerprint, metadata=source_metadata,
        )]
        if origin.get("run_id") and origin.get("run_id") != source_run.run_id:
            sources.append(RunSourceRecord(
                source_run_id=origin["run_id"], relation="origin_frame", ordinal=1,
                source_fingerprint=("sha256:" + origin["sha256"] if origin.get("sha256") else None),
                metadata=origin,
            ))
        sources.extend(reference_sources)
        run = service.create_running_run(
            workflow=Workflow.RENDER, source_run_id=source_run.run_id,
            memory_tool="render_gb_plan_view",
            source_fingerprint=fingerprint, sources=sources,
            parameters={**source_metadata, "gb_y": gb_y, "slab_thickness": slab_thickness,
                        "pbc": pbc, "image_width": image_width, "view_width": view_width,
                        "atom_scale": atom_scale, "arrow_width": arrow_width,
                        "arrow_offset_y": arrow_offset_y, "arrow_target_length": arrow_target_length,
                        "arrow_scale_mode": arrow_scale_mode, "arrow_max_length": arrow_max_length,
                        "minimum_image_convention": minimum_image_convention},
        )
        output_dir = service.artifacts.run_directory(run.run_id)
        completed = _run_plan_view_subprocess(
            cfg_abs=cfg_abs, output_dir=output_dir, gb_y=gb_y,
            slab_thickness=slab_thickness, pbc=pbc,
            image_width=image_width, view_width=view_width,
            reference=reference_abs, atom_scale=atom_scale, arrow_width=arrow_width,
            arrow_offset_y=arrow_offset_y, arrow_target_length=arrow_target_length,
            arrow_scale_mode=arrow_scale_mode, arrow_max_length=arrow_max_length,
            minimum_image_convention=minimum_image_convention,
        )
        data_path = output_dir / "gb_plan_view.json"
        report = None
        if data_path.is_file():
            report = json.loads(data_path.read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                raise ValueError("OVITO plan-view diagnostics must be a JSON object")
            report.update(source_metadata)
            write_json_atomic(data_path, report)
            artifacts.append(service.register_artifact(
                run.run_id, "gb_plan_view_data", data_path.name,
                ProvenanceClass.DERIVED, media_type="application/json", metadata=source_metadata,
            ))
        if completed.returncode != 0:
            message = (report or {}).get("error") or completed.stderr or completed.stdout
            raise ValueError(
                f"OVITO plan-view rendering failed (exit {completed.returncode}): "
                f"{str(message).strip() or 'no subprocess output'}"
            )
        if report is None or report.get("status") != "completed":
            raise ValueError("OVITO plan-view rendering did not produce completed JSON diagnostics")
        for role, filename, required in (
            ("gb_plan_view", "plan_view.png", True),
            ("gb_plan_view_detail", "plan_view_detail.png", False),
            ("gb_plan_view_profile", "y_profile.png", True),
        ):
            if not (output_dir / filename).is_file():
                if required:
                    raise ValueError(f"OVITO plan-view output is missing: {filename}")
                continue
            image_metadata = dict(source_metadata)
            if role in {"gb_plan_view", "gb_plan_view_detail"}:
                camera_key = "detail_camera" if role == "gb_plan_view_detail" else "camera"
                image_metadata["camera"] = report.get(camera_key)
            artifacts.append(service.register_artifact(
                run.run_id, role, filename, ProvenanceClass.DERIVED,
                media_type="image/png", metadata=image_metadata,
            ))
        paths = {item.role: item.path for item in artifacts}
        result = {
            **source_metadata,
            "boundary": report.get("boundary"),
            "slab": {key: value for key, value in (report.get("slab") or {}).items()
                     if key != "particle_ids"},
            "layers": {key: value for key, value in (report.get("layers") or {}).items()
                       if key != "nearby"},
            "warnings": report.get("warnings", []),
            "displacement": ({key: value for key, value in report["displacement"].items()
                              if key != "selected"} if report.get("displacement") is not None else None),
            "camera": report.get("camera"), "data": paths["gb_plan_view_data"],
            "render": paths["gb_plan_view"], "profile": paths["gb_plan_view_profile"],
            "detail": paths.get("gb_plan_view_detail"),
            "summary": report.get("summary") or (
                f"Estimated Y-normal boundary at y={(report.get('boundary') or {}).get('y')} "
                "in imported Cartesian length units; "
                "plan view uses black for the lower side and milk white for the upper side, "
                f"with a transparent PNG background and atom radius scale {atom_scale:g}. "
                "Inspect the layer profile and warnings before interpreting grain identity."
            ),
        }
        terminal = service.complete_run(
            run.run_id, result=result, memory_tool="render_gb_plan_view",
            conclusion={
                "conclusion_type": "gb_plan_view_geometry", **result,
                "interpretation": "Approximate boundary and geometric Y-side labels; not crystallographic grain identification.",
            },
        )
        result = terminal.result
    except Exception as exc:
        if "run" not in locals():
            return str(exc)
        error = (
            f"OVITO plan-view rendering timed out after {RENDER_TIMEOUT_SEC}s"
            if isinstance(exc, subprocess.TimeoutExpired) else str(exc)
        )
        terminal = service.fail_run(run.run_id, error=error[:2000])
        return RunToolResult(
            run_id=terminal.run_id, workflow=terminal.workflow, status=terminal.status,
            manifest=terminal.manifest_path, error=terminal.error,
            artifacts=[{"role": item.role, "path": item.path} for item in artifacts],
        ).to_json()

    return RunToolResult(
        run_id=terminal.run_id, workflow=terminal.workflow, status=terminal.status,
        manifest=terminal.manifest_path,
        artifacts=[{"role": item.role, "path": item.path} for item in artifacts],
        result=result,
    ).to_json()
