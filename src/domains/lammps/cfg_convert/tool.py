"""Agent tool converting CFG frames into LAMMPS data files with recorded provenance."""

from __future__ import annotations

import math
import re
import shutil
from pathlib import Path

from langchain_core.tools import tool

from core.util.graph_config import runtime_project_id
from core.util.io import sha256_file
from domains.lammps.cfg_convert.config import (
    build_cfg_to_lammps_config,
    config_to_dict,
)
from domains.lammps.cfg_convert.converter import (
    cfg_has_type_column,
    convert_cfg_file,
    read_extended_cfg,
    read_lammps_data_cell,
    read_lammps_reference,
    restricted_cell,
)
from domains.lammps.common.lineage import find_structure_source
from project_memory.models import RunSourceRecord, Workflow
from project_memory.publication.results import RunToolResult
from project_workspace.artifacts.files import resolve_project_path
from project_workspace.artifacts.models import ProvenanceClass
from project_workspace.store import ProjectStore


def _output_basename(source: Path, requested: str | None, used: set[str]) -> str:
    if requested is None:
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", source.stem).strip("._-")
        stem = stem or "structure"
        candidate = f"{stem}.lmp"
    else:
        candidate = str(requested).strip()
        path = Path(candidate)
        if path.name != candidate or path.suffix.lower() != ".lmp":
            raise ValueError("each output name must be a basename ending in .lmp")
        if candidate in used:
            raise ValueError(f"duplicate output name: {candidate}")
        used.add(candidate)
        return candidate
    base = Path(candidate).stem
    suffix = Path(candidate).suffix
    resolved = candidate
    counter = 2
    while resolved in used:
        resolved = f"{base}_{counter}{suffix}"
        counter += 1
    used.add(resolved)
    return resolved


def convert_cfg_files_for_project(
    *,
    project_id: str,
    cfg_paths: list[str],
    output_names: list[str] | None = None,
    mass_override: float | None = None,
    description: str | None = None,
    purpose: str | None = None,
    label: str | None = None,
    store: ProjectStore | None = None,
) -> str:
    """Implementation separated from the LangChain wrapper for deterministic tests."""
    project_store = store or ProjectStore()
    project_root = project_store.project_path(project_id)
    service = project_store.runs(project_id)
    if not isinstance(cfg_paths, list) or not cfg_paths:
        return "cfg_paths must be a non-empty list of project-relative .cfg paths"
    if len(cfg_paths) > 100:
        return "at most 100 CFG files may be converted in one run"
    if output_names is not None and len(output_names) != len(cfg_paths):
        return "output_names must have the same length as cfg_paths"

    used_outputs: set[str] = set()
    raw_inputs: list[dict[str, object]] = []
    conversion_warnings: list[str] = []
    conversion_purpose = (
        str(purpose or "").strip()
        or str(description or "").strip()
        or "Convert selected CFG frames to LAMMPS data."
    )
    try:
        for index, raw_path in enumerate(cfg_paths):
            text = str(raw_path).strip().replace("\\", "/")
            if not text or Path(text).is_absolute():
                raise ValueError("CFG paths must be project-relative")
            source = resolve_project_path(project_root, text)
            if not source.is_file() or source.suffix.lower() != ".cfg":
                raise ValueError(f"CFG input does not exist or is not .cfg: {text}")
            source_relative = source.relative_to(project_root).as_posix()
            origin = find_structure_source(project_root, service, source)
            output_name = _output_basename(
                source,
                output_names[index] if output_names is not None else None,
                used_outputs,
            )
            box_origin: list[float] | None = None
            box_origin_reference: str | None = None
            box_origin_reference_sha256: str | None = None
            type_reference: str | None = None
            has_type_column = cfg_has_type_column(source)
            source_run = (
                service.memory.get_run(origin["run_id"])
                if origin["run_id"] is not None else None
            )
            origin_role = None
            if source_run is not None:
                if source_run.workflow is Workflow.GB_MINIMIZE:
                    origin_role = "minimized_lmp"
                elif source_run.workflow is Workflow.GB_SHEAR:
                    origin_role = "sheared_lmp"
                elif source_run.workflow.value == "structure_minimize":
                    origin_role = "minimized_lmp"
            if origin_role is not None:
                references = service.memory.query_artifacts(
                    run_id=source_run.run_id,
                    role=origin_role,
                )
                if source_run.workflow is Workflow.STRUCTURE_MINIMIZE and origin.get("artifact_id"):
                    source_artifact = service.memory.get_artifact(origin["artifact_id"])
                    item_index = source_artifact.metadata.get("item_index")
                    if item_index is not None:
                        references = [item for item in references
                                      if item.metadata.get("item_index") == item_index]
                if len(references) == 1:
                    reference = project_root / references[0].path
                    if reference.is_file():
                        structure = read_extended_cfg(source)
                        cfg_cell = restricted_cell(structure.cell)
                        lmp_cell = read_lammps_data_cell(reference)
                        cfg_shape = (
                            cfg_cell.lx,
                            cfg_cell.ly,
                            cfg_cell.lz,
                            cfg_cell.xy,
                            cfg_cell.xz,
                            cfg_cell.yz,
                        )
                        lmp_shape = (
                            lmp_cell.lx,
                            lmp_cell.ly,
                            lmp_cell.lz,
                            lmp_cell.xy,
                            lmp_cell.xz,
                            lmp_cell.yz,
                        )
                        if all(
                            math.isclose(
                                cfg_value,
                                lmp_value,
                                rel_tol=2.0e-6,
                                abs_tol=1.0e-3,
                            )
                            for cfg_value, lmp_value in zip(cfg_shape, lmp_shape)
                        ):
                            box_origin = list(lmp_cell.origin)
                            box_origin_reference = reference.relative_to(
                                project_root
                            ).as_posix()
                            box_origin_reference_sha256 = sha256_file(reference)
                        if not has_type_column:
                            reference_types = read_lammps_reference(reference).types_by_id
                            if all(atom.atom_id in reference_types for atom in structure.atoms):
                                type_reference = reference.relative_to(project_root).as_posix()
            if box_origin is None:
                conversion_warnings.append(
                    f"{source_relative}: extended CFG has no Cartesian box origin and no "
                    "compatible registered LMP reference was found; the converted box will "
                    "be centered at the origin"
                )
            if not has_type_column and type_reference is None:
                conversion_warnings.append(
                    f"{source_relative}: CFG has no atom-type column and no registered LMP of "
                    "the same atoms; atom types follow element labels, so LAMMPS types that "
                    "share an element are merged"
                )
            raw_inputs.append(
                {
                    "source_path": source_relative,
                    "source_sha256": origin["sha256"],
                    "source_size_bytes": source.stat().st_size,
                    "source_snapshot": f"sources/{index:03d}_{source.name}",
                    "output_lmp": f"converted/{index:03d}_{output_name}",
                    "box_origin": box_origin,
                    "box_origin_reference": box_origin_reference,
                    "box_origin_reference_sha256": (
                        box_origin_reference_sha256
                    ),
                    "origin": origin,
                    "purpose": conversion_purpose,
                    **({"type_reference": type_reference,
                        "type_reference_sha256": sha256_file(project_root / type_reference)}
                       if type_reference is not None else {}),
                }
            )
        payload = {
            "version": 1,
            "description": str(description or "").strip(),
            "purpose": conversion_purpose,
            "coordinate_origin": "preserve_source_when_available",
            "atom_style": "atomic",
            "mass_override": mass_override,
            "inputs": raw_inputs,
        }
        cfg = build_cfg_to_lammps_config(payload)
        canonical = config_to_dict(cfg)
        sources = [
            RunSourceRecord(
                source_run_id=item.origin["run_id"],
                relation="cfg_frame",
                ordinal=index,
                source_fingerprint=item.source_sha256,
                metadata={
                    "origin": item.origin,
                    "purpose": item.purpose,
                    "source_path": item.source_path,
                    "output_lmp": item.output_lmp,
                },
            )
            for index, item in enumerate(cfg.inputs)
            if item.origin is not None and item.origin.get("run_id")
        ]
        parent_ids = {item.source_run_id for item in sources}
        common_parent = (
            next(iter(parent_ids))
            if len(parent_ids) == 1 and len(sources) == len(cfg.inputs) else None
        )
        run = service.create_configured_run(
            workflow=Workflow.CFG_CONVERT,
            input_payload=canonical,
            parameters={
                "memory_tool": "convert_cfg_to_lammps",
                "description": cfg.description,
                "purpose": cfg.purpose,
                "label": str(label or "").strip(),
                "coordinate_origin": cfg.coordinate_origin,
                "atom_style": cfg.atom_style,
                "input_count": len(cfg.inputs),
                "source_cfg_paths": [item.source_path for item in cfg.inputs],
                "origins": [item.origin for item in cfg.inputs],
                "output_lmp_paths": [item.output_lmp for item in cfg.inputs],
                "warnings": list(conversion_warnings),
                "canonical_config": canonical,
            },
            source_run_id=common_parent,
            source_fingerprint=(cfg.inputs[0].source_sha256 if len(cfg.inputs) == 1 else None),
            sources=sources,
        )
    except (FileNotFoundError, ValueError, FileExistsError) as exc:
        return str(exc)

    output_dir = service.artifacts.run_directory(run.run_id)
    service.start_run(
        run.run_id,
        progress={"stage": "converting", "total": len(cfg.inputs), "completed": 0},
    )
    conversions: list[dict[str, object]] = []
    try:
        for index, item in enumerate(cfg.inputs):
            source = resolve_project_path(project_root, item.source_path)
            if (
                source.stat().st_size != item.source_size_bytes
                or sha256_file(source) != item.source_sha256
            ):
                raise ValueError(f"source CFG changed after configuration: {item.source_path}")
            snapshot = output_dir / item.source_snapshot
            converted = output_dir / item.output_lmp
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, snapshot)
            if sha256_file(snapshot) != item.source_sha256:
                raise ValueError(f"source CFG changed while snapshotting: {item.source_path}")
            source_artifact = service.register_artifact(
                run.run_id,
                "source_cfg",
                item.source_snapshot,
                ProvenanceClass.PRIMARY,
                metadata={
                    "source_project_path": item.source_path,
                    "source_sha256": item.source_sha256,
                    "sequence": index,
                    "origin": item.origin,
                    "purpose": item.purpose,
                },
            )
            if item.box_origin_reference is not None:
                origin_reference = resolve_project_path(
                    project_root,
                    item.box_origin_reference,
                )
                if (
                    not origin_reference.is_file()
                    or sha256_file(origin_reference)
                    != item.box_origin_reference_sha256
                ):
                    raise ValueError(
                        "box-origin LMP reference changed after configuration: "
                        f"{item.box_origin_reference}"
                    )
            if item.type_reference is not None:
                type_reference = resolve_project_path(project_root, item.type_reference)
                if (
                    not type_reference.is_file()
                    or sha256_file(type_reference) != item.type_reference_sha256
                ):
                    raise ValueError(
                        f"atom-type LMP reference changed after configuration: {item.type_reference}"
                    )
            reference_path = item.type_reference or item.box_origin_reference
            conversion = convert_cfg_file(
                snapshot,
                converted,
                mass_override=cfg.mass_override,
                box_origin=item.box_origin,
                reference=(
                    read_lammps_reference(resolve_project_path(project_root, reference_path))
                    if reference_path is not None else None
                ),
            )
            conversion_data = conversion.to_dict()
            output_artifact = service.register_artifact(
                run.run_id,
                "converted_lmp",
                item.output_lmp,
                ProvenanceClass.DERIVED,
                metadata={
                    "source_project_path": item.source_path,
                    "source_cfg_artifact_id": source_artifact.artifact_id,
                    "source_sha256": item.source_sha256,
                    "sequence": index,
                    "origin": item.origin,
                    "purpose": item.purpose,
                    "box_origin_reference": item.box_origin_reference,
                    "box_origin_reference_sha256": (
                        item.box_origin_reference_sha256
                    ),
                    "type_reference": item.type_reference,
                    **conversion_data,
                },
            )
            conversions.append(
                {
                    "source_cfg": item.source_path,
                    "source_snapshot": source_artifact.path,
                    "source_cfg_artifact_id": source_artifact.artifact_id,
                    "origin": item.origin,
                    "purpose": item.purpose,
                    "source_sha256": item.source_sha256,
                    "output_lmp": output_artifact.path,
                    "output_lmp_artifact_id": output_artifact.artifact_id,
                    "box_origin_reference": item.box_origin_reference,
                    "box_origin_reference_sha256": (
                        item.box_origin_reference_sha256
                    ),
                    "type_reference": item.type_reference,
                    **conversion_data,
                }
            )
            service.update_progress(
                run.run_id,
                progress={
                    "stage": "converting",
                    "total": len(cfg.inputs),
                    "completed": index + 1,
                },
            )
    except Exception as exc:
        terminal = service.fail_run(
            run.run_id,
            error=str(exc),
            result={
                "conversions": conversions,
                "purpose": cfg.purpose,
                "origins": [item.origin for item in cfg.inputs],
            },
            progress={
                "stage": "failed",
                "total": len(cfg.inputs),
                "completed": len(conversions),
            },
        )
        return RunToolResult(
            run_id=terminal.run_id,
            workflow=terminal.workflow,
            status=terminal.status,
            manifest=terminal.manifest_path,
            artifacts=[
                {"role": artifact.role, "path": artifact.path}
                for artifact in service.memory.query_artifacts(run_id=run.run_id)
            ],
            result=terminal.result,
            error=terminal.error,
        ).to_json()

    result = {
        "coordinate_origin": cfg.coordinate_origin,
        "purpose": cfg.purpose,
        "origins": [item.origin for item in cfg.inputs],
        "atom_style": "atomic",
        "conversion_count": len(conversions),
        "conversions": conversions,
        "warnings": list(conversion_warnings),
    }
    terminal = service.complete_run(
        run.run_id,
        memory_tool="convert_cfg_to_lammps",
        result=result,
        progress={
            "stage": "finished",
            "total": len(cfg.inputs),
            "completed": len(cfg.inputs),
        },
    )
    artifacts = service.memory.query_artifacts(run_id=run.run_id)
    return RunToolResult(
        run_id=terminal.run_id,
        workflow=terminal.workflow,
        status=terminal.status,
        manifest=terminal.manifest_path,
        artifacts=[{"role": item.role, "path": item.path} for item in artifacts],
        result=terminal.result,
    ).to_json()


@tool
def convert_cfg_to_lammps(
    cfg_paths: list[str],
    output_names: list[str] | None = None,
    mass_override: float | None = None,
    description: str | None = None,
    purpose: str | None = None,
    label: str | None = None,
) -> str:
    """Convert project-local extended CFG files to indexed LAMMPS data artifacts.

    ``cfg_paths`` contains exact project-relative .cfg paths, typically obtained
    from ``list_run_artifacts`` or project memory. ``output_names`` is optional;
    when supplied it must contain one .lmp basename per CFG. The conversion uses
    ``atom_style atomic``, preserves atom IDs/image flags and LAMMPS atom types,
    supports orthogonal and right-handed triclinic boxes. Types come from the
    CFG's ``type`` column (written by GBclaw's LAMMPS workflows) or, by atom ID,
    from the LMP of the CFG's registered minimize/shear parent. Extended CFG omits
    the Cartesian box origin, so the tool reuses a compatible parent LMP box when
    available (its exact cell when the CFG cell is that box as printed).
    Otherwise it explicitly warns, centers the box at (0,0,0), and assigns types
    by element label. ``mass_override`` can replace CFG masses when the dump
    metadata is known to be wrong.

    This is a complete tracked workflow, not a preview: it writes a canonical
    input.json, snapshots every source CFG, registers every converted_lmp with
    source mapping and cell metadata, completes a cfg_convert run, and returns
    stable run/artifact IDs and project-relative paths for later LAMMPS jobs.
    ``purpose`` records why these particular frames are being extracted (for
    example, unloaded minimization near a shear instability). It falls back to
    ``description``. Each source retains its original run/artifact ID, exact
    frame metadata, path and digest in durable project memory, including batches.
    """
    project_id = runtime_project_id()
    if not project_id:
        return "No active project_id in run config."
    return convert_cfg_files_for_project(
        project_id=project_id,
        cfg_paths=cfg_paths,
        output_names=output_names,
        mass_override=mass_override,
        description=description,
        purpose=purpose,
        label=label,
    )
