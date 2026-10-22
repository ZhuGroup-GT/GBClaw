"""Read saved CSL results back from a project's completed runs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from project_memory.models import RunStatus, Workflow
from project_workspace.artifacts.models import ArtifactRecord
from project_workspace.store import ProjectStore
from domains.structure.csl import generator as csl
from domains.structure.csl.recorder import csl_result_text
from domains.structure.csl.tools import MAX_RUN_SOURCE_ROWS, parse_gb_plane_tables

_PLANE_TABLE_TOOLS = frozenset({"list_csl_gb_planes", "list_csl_gb_planes_batch"})


def verified_csl_result_text(project_root: Path, run, artifact: ArtifactRecord) -> str | None:
    """Return a csl_result file's text if its manifest entry and hash still match."""
    try:
        path = (project_root / artifact.path).resolve()
        if not path.is_relative_to(project_root) or not path.is_file():
            return None
        manifest_path = (project_root / run.manifest_path).resolve()
        if not manifest_path.is_relative_to(project_root):
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        indexed = next((entry for entry in manifest.get("files", [])
                        if entry.get("artifact_id") == artifact.artifact_id), None)
        if indexed is None or indexed.get("role") != "csl_result":
            return None
        if (manifest_path.parent / indexed["path"]).resolve() != path:
            return None
        raw = path.read_bytes()
        if artifact.metadata.get("sha256") and (
            hashlib.sha256(raw).hexdigest() != artifact.metadata["sha256"]
        ):
            return None
        return csl_result_text(raw.decode("utf-8"))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def exact_plane_rows_from_run(
    store: ProjectStore,
    project_id: str,
    run_id: str,
    *,
    axis: list[int],
    branches: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return every saved exact plane-table row of ``run_id`` about ``axis``.

    A branch listed by several tables (for example at two plane limits) keeps
    each boundary once: rows of its largest-limit table come first, in table
    order, and a smaller-limit row is added only when it is not equivalent to one
    already kept, under the generator's own fixed-axis pair symmetry. Raises
    ValueError with the reason a run cannot supply rows.
    """
    memory = store.memory(project_id)
    try:
        run = memory.get_run(run_id)
    except ValueError as exc:
        raise ValueError(f"source_run_id {run_id} is not a run of this project") from exc
    if run.workflow is not Workflow.CSL or run.status is not RunStatus.COMPLETED:
        raise ValueError(
            f"source_run_id {run_id} is a {run.status.value} {run.workflow.value} run; "
            "pass a completed CSL run that listed exact GB plane tables"
        )
    target = [int(value) for value in csl.reduce_miller_indices(axis)]
    project_root = store.project_path(project_id).resolve()
    artifacts = sorted(
        (item for item in memory.query_artifacts(run_id=run_id)
         if item.role == "csl_result" and item.metadata.get("tool") in _PLANE_TABLE_TOOLS),
        key=lambda item: int(item.metadata.get("sequence") or 0),
    )
    listed: dict[tuple[int, int, int], list[tuple[int, int, int, str, dict[str, Any]]]] = {}
    other_axes: set[tuple[int, ...]] = set()
    for order, artifact in enumerate(artifacts):
        text = verified_csl_result_text(project_root, run, artifact)
        if text is None:
            continue
        for row in parse_gb_plane_tables(text):
            row_axis = [int(value) for value in csl.reduce_miller_indices(row["axis"])]
            if row_axis != target:
                other_axes.add(tuple(row_axis))
                continue
            branch = (row["sigma"], row["rotation_m"], row["rotation_n"])
            listed.setdefault(branch, []).append(
                (-row["plane_limit"], order, row["row"], artifact.artifact_id, row))
    rows: list[dict[str, Any]] = []
    used: list[str] = []
    basis, plane_limit = set(), set()
    axis_vector = np.array(target, dtype=int)
    for branch, entries in listed.items():
        kept: set[Any] = set()
        for _limit, _order, _row, artifact_id, row in sorted(entries, key=lambda item: item[:3]):
            identity = csl._canonical_gb_pair_key(
                np.array(row["plane1"]), np.array(row["plane2"]), axis_vector)
            if identity in kept:
                continue
            kept.add(identity)
            rows.append({**row, "row": len(kept)})
            basis.add(row["basis"])
            plane_limit.add(row["plane_limit"])
            if artifact_id not in used:
                used.append(artifact_id)
    if not rows:
        found = (f"; it lists rows about {sorted(list(item) for item in other_axes)}"
                 if other_axes else "")
        raise ValueError(f"Run {run_id} has no saved exact GB plane-table rows about {target}{found}")
    if branches:
        wanted = {(int(item["sigma"]), int(item["rotation_m"]), int(item["rotation_n"]))
                  for item in branches}
        available = sorted(listed)
        missing = sorted(wanted - set(available))
        if missing:
            raise ValueError(
                f"Run {run_id} lists no rows for branches {missing}; available "
                f"(sigma, rotation_m, rotation_n): {available}"
            )
        rows = [row for row in rows
                if (row["sigma"], row["rotation_m"], row["rotation_n"]) in wanted]
    if len(rows) > MAX_RUN_SOURCE_ROWS:
        raise ValueError(
            f"Run {run_id} supplies {len(rows)} rows, more than {MAX_RUN_SOURCE_ROWS}; "
            "limit them with source_branches"
        )
    source = {
        "kind": "run",
        "run_id": run_id,
        "artifact_ids": used,
        "basis": next(iter(basis)) if len(basis) == 1 else sorted(basis),
        "plane_limit": next(iter(plane_limit)) if len(plane_limit) == 1 else sorted(plane_limit),
    }
    return rows, source


__all__ = ["exact_plane_rows_from_run", "verified_csl_result_text"]
