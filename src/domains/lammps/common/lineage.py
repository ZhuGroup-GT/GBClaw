"""Resolve a structure file to the run, artifact and original frame it came from."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from core.util.io import sha256_file
from domains.lammps.common.artifacts import SHEARED_CFG, SHEARED_LMP
from project_memory.models import RunRecord, Workflow
from project_memory.publication.service import RunService
from project_workspace.artifacts.files import resolve_project_path
from project_workspace.artifacts.models import ArtifactRecord

_FRAME_FIELDS = (
    "step", "loading_step", "timestep", "frame_index", "increment_stage",
    "loading_direction", "loading_half_cycle", "loading_cycle",
    "step_in_loading_half_cycle", "strain", "actual_shear_strain",
    "relative_shear_strain", "target_shear_strain", "relative_target_shear_strain",
    "converged", "top_shear_stress_mpa", "bottom_shear_stress_mpa",
    "sigma_xx_mpa", "sigma_yy_mpa", "sigma_zz_mpa", "sigma_xy_mpa",
    "sigma_xz_mpa", "sigma_yz_mpa",
)


def _find_owner(
    project_root: Path, service: RunService, path: Path,
) -> tuple[RunRecord | None, ArtifactRecord | None]:
    relative = path.relative_to(project_root).as_posix()
    for parent in path.parents:
        if parent == project_root:
            break
        if not (parent / "manifest.json").is_file():
            continue
        try:
            run = service.memory.get_run(parent.name)
        except ValueError:
            continue
        if resolve_project_path(project_root, run.manifest_path).parent != parent:
            continue
        artifacts = service.memory.query_artifacts(run_id=run.run_id)
        matching = [item for item in artifacts if item.path == relative]
        if len(matching) > 1:
            raise ValueError(f"multiple registered artifacts refer to {relative}")
        return run, matching[0] if matching else None
    return None, None


def _frame_metadata(
    project_root: Path, service: RunService, path: Path,
    run: RunRecord | None, artifact: ArtifactRecord | None,
) -> dict[str, Any]:
    # Indexed metadata is authoritative for this exact path. Older runs may
    # have a metadata entry for a frame whose artifact was never indexed.
    frame = {
        key: deepcopy(artifact.metadata[key])
        for key in _FRAME_FIELDS
        if artifact is not None and key in artifact.metadata
    }
    if frame or run is None:
        return frame
    run_directory = resolve_project_path(project_root, run.manifest_path).parent
    relative = path.relative_to(run_directory).as_posix()
    for metadata_artifact in service.memory.query_artifacts(
        run_id=run.run_id, role="run_metadata",
    ):
        metadata_path = resolve_project_path(project_root, metadata_artifact.path)
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        entries = [
            entry for entry in data.get("frames") or []
            if isinstance(entry, dict) and entry.get("path") == relative
        ]
        if len(entries) != 1:
            # Older shear finals were indexed without point metadata and were
            # never listed in frames[]. Only these exact final output names
            # may inherit current; an intermediate frame must keep its own step.
            if (
                not entries
                and run.workflow is Workflow.GB_SHEAR
                and relative in {SHEARED_CFG, SHEARED_LMP}
                and (artifact is None or artifact.role in {"sheared_cfg", "sheared_lmp"})
            ):
                current = data.get("current")
                if isinstance(current, dict) and "step" in current:
                    final_frame = {
                        key: deepcopy(current[key]) for key in _FRAME_FIELDS if key in current
                    }
                    if "actual_shear_strain" in current:
                        final_frame["strain"] = current["actual_shear_strain"]
                    return final_frame
            continue
        entry = entries[0]
        points = [
            point for point in data.get("points") or []
            if isinstance(point, dict) and "step" in entry
            and point.get("step") == entry["step"]
        ]
        exact = {**(points[0] if len(points) == 1 else {}), **entry}
        return {key: deepcopy(exact[key]) for key in _FRAME_FIELDS if key in exact}
    return frame


def find_structure_source(
    project_root: Path, service: RunService, path: Path | str,
) -> dict[str, Any]:
    """Return immediate file identity, owner, and only its exact frame metadata.

    Unregistered local inputs remain usable: owner IDs are null, while their
    project-relative path and content digest still make the source reproducible.
    """
    project_root = project_root.resolve()
    candidate = Path(path)
    relative = (
        candidate.resolve().relative_to(project_root).as_posix()
        if candidate.is_absolute() else str(candidate)
    )
    source = resolve_project_path(project_root, relative)
    if not source.is_file():
        raise ValueError(f"structure input does not exist: {path}")
    run, artifact = _find_owner(project_root, service, source)
    return {
        "run_id": run.run_id if run is not None else None,
        "workflow": run.workflow.value if run is not None else None,
        "artifact_id": artifact.artifact_id if artifact is not None else None,
        "path": source.relative_to(project_root).as_posix(),
        "sha256": sha256_file(source),
        "frame": _frame_metadata(project_root, service, source, run, artifact),
    }


def find_structure_origin(
    project_root: Path, service: RunService, path: Path | str,
) -> dict[str, Any]:
    """Follow durable artifact lineage to its original frame without rehashing it.

    The original frame may have been removed; its recorded digest still names
    the exact input used. Repeated minimizations preserve this ultimate origin.
    """
    source = find_structure_source(project_root, service, path)
    visited: set[str] = set()
    while source.get("artifact_id") is not None:
        artifact_id = source["artifact_id"]
        if artifact_id in visited:
            raise ValueError("cycle in stored structure origin provenance")
        visited.add(artifact_id)
        try:
            artifact = service.memory.get_artifact(artifact_id)
        except ValueError:
            break
        origin = artifact.metadata.get("origin")
        if isinstance(origin, dict) and origin.get("path") and origin.get("sha256"):
            source = deepcopy(origin)
            continue
        # Pre-lineage cfg_convert runs already recorded the exact CFG path and
        # digest. Recover its owner/frame from memory without using a batch's
        # first input or assuming the final strain applies to this frame.
        legacy_path = artifact.metadata.get("source_project_path")
        legacy_digest = artifact.metadata.get("source_sha256")
        if artifact.role == "converted_lmp" and legacy_path and legacy_digest:
            root = project_root.resolve()
            original_path = resolve_project_path(root, str(legacy_path))
            run, original = _find_owner(root, service, original_path)
            source = {
                "run_id": run.run_id if run else None,
                "workflow": run.workflow.value if run else None,
                "artifact_id": original.artifact_id if original else None,
                "path": original_path.relative_to(root).as_posix(),
                "sha256": legacy_digest,
                "frame": _frame_metadata(root, service, original_path, run, original),
            }
            continue
        break
    return source
