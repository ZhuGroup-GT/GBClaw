"""Tracked, fixed-cell minimization of an exact existing structure artifact."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from langchain_core.tools import tool

from domains.lammps.common.potential_files import current_potential_path
from core.util.graph_config import runtime_project_id
from core.util.io import sha256_file, write_json_atomic
from domains.lammps.common.settings import (
    STRUCTURE_MINIMIZE_SCRIPT, STRUCTURE_MINIMIZE_TIMEOUT_SEC,
)
from project_workspace.artifacts.files import resolve_project_path
from project_workspace.artifacts.models import ProvenanceClass
from project_memory.models import RunSourceRecord, RunStatus, Workflow
from project_workspace.store import ProjectStore
from domains.lammps.common.lineage import find_structure_origin, find_structure_source
from core.execution.sessions import runtime_session_key
from domains.lammps.common.resources import lammps_resource_slot
from domains.lammps.common.runner import run_in_lammps_env
from domains.lammps.common.launch import (
    command_status,
    configured_run,
    finish_run,
    launch,
    mpirun_argv,
    register_launcher_logs,
    resolve_mpi_ranks,
    run_tool_result,
    save_launcher_logs,
)
from domains.lammps.common.potentials import list_potential_records, resolve_potential_file_for_config
from domains.lammps.structure_minimize.engine import inspect_atomic_data
from domains.lammps.structure_minimize.config import (
    StructureMinimizeConfig,
    build_structure_minimize_config, build_structure_minimize_batch_config,
    config_to_dict, load_structure_minimize_config,
)
from project_memory.publication.results import RunToolResult


def _resolve_input(root: Path, service, value: str) -> Path:
    if Path(value).is_absolute():
        raise ValueError("Input must be a project-relative path or artifact ID")
    try:
        artifact = service.memory.get_artifact(value)
    except ValueError:
        return resolve_project_path(root, value)
    return resolve_project_path(root, artifact.path)


def _source_context(root: Path, service, source: dict, origin: dict) -> tuple[dict, dict]:
    # A direct minimization result has its own resolved potential/PBC. A
    # conversion's context belongs to its selected original frame, not its batch.
    immediate_origin = {}
    immediate_metadata = {}
    if source.get("artifact_id"):
        immediate_metadata = service.memory.get_artifact(source["artifact_id"]).metadata
        # A converted minimized CFG must inherit the immediately selected
        # minimization item's context before following its ultimate shear origin.
        immediate_origin = immediate_metadata.get("source") or immediate_metadata.get("origin") or {}
    candidates = [source.get("run_id"), immediate_origin.get("run_id"), origin.get("run_id")]
    for run_id in dict.fromkeys(candidates):
        if not run_id:
            continue
        run = service.memory.get_run(run_id)
        if run.workflow not in {
            Workflow.GB_MINIMIZE, Workflow.GB_SHEAR, Workflow.STRUCTURE_MINIMIZE,
        } or not run.config_path:
            continue
        path = resolve_project_path(root, run.config_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        canonical = run.parameters.get("canonical_config")
        if canonical is not None and payload != canonical:
            raise ValueError("Source simulation config differs from its recorded canonical configuration")
        context_identity = {"context_run_id": run_id, "context_config_path": run.config_path,
                            "context_config_sha256": sha256_file(path)}
        if run.workflow is Workflow.STRUCTURE_MINIMIZE:
            snapshots = service.memory.query_artifacts(run_id=run_id, role="potential")
            if "structures" in payload:
                metadata = immediate_metadata
                if run_id != source.get("run_id"):
                    artifact_id = immediate_origin.get("artifact_id") or origin.get("artifact_id")
                    if artifact_id:
                        metadata = service.memory.get_artifact(artifact_id).metadata
                item_index = metadata.get("item_index")
                matches = [item for item in payload["structures"] if item["item_index"] == item_index]
                if len(matches) != 1:
                    raise ValueError("Batch source requires an exact registered structure artifact with item_index")
                entry = matches[0]
                payload = entry["config"]
                snapshots = [item for item in snapshots if item.metadata.get("item_index") == item_index]
                context_identity.update(context_item_index=item_index, context_item_key=entry["item_key"])
            if len(snapshots) == 1:
                payload["potential"] = {**payload["potential"],
                    "file": str(resolve_project_path(root, snapshots[0].path))}
        return payload, context_identity
    return {}, {}


def _resolve_potential(context: dict, potential_name: str | None) -> tuple[dict, dict | None]:
    records = list_potential_records()
    record = None
    if potential_name:
        matches = [item for item in records if potential_name in {
            item.get("id"), item.get("file"), item.get("reference"),
            Path(str(item.get("file", ""))).name,
        }]
        if len(matches) != 1:
            raise ValueError("potential_name must identify exactly one registered potential")
        record = matches[0]
        potential = {"pair_style": record["pair_style"], "file": record["file"]}
    else:
        potential = dict(context.get("potential") or {})
        if not potential.get("file") or not potential.get("pair_style"):
            raise ValueError("No source potential is available; supply a registered potential_name")
    path = resolve_potential_file_for_config(str(potential["file"]))
    if not path.is_file():
        raise ValueError(f"Potential file not found: {path}")
    digest = sha256_file(path)
    if not potential_name and potential.get("sha256") and potential["sha256"] != digest:
        raise ValueError("Inherited potential differs from its recorded SHA-256; select an explicit override if intended")
    if potential["pair_style"] not in {"eam/fs", "eam/alloy", "adp", "nep"}:
        raise ValueError("structure_minimize supports eam/fs, eam/alloy, adp and nep potentials")
    if record is None:
        record = next((item for item in records if
            resolve_potential_file_for_config(str(item["file"])) == path), None)
    return {"pair_style": potential["pair_style"], "file": str(path), "sha256": digest}, record


def _elements_for_types(context: dict, source: dict, service, count: int,
                        elements: list[str] | None) -> list[str]:
    if elements is not None:
        resolved = elements
    elif (context.get("material") or {}).get("element"):
        resolved = [context["material"]["element"]] * count
    else:
        inherited = (context.get("potential") or {}).get("elements")
        resolved = inherited
        if source.get("artifact_id"):
            metadata = service.memory.get_artifact(source["artifact_id"]).metadata
            mapping = metadata.get("type_map")
            if mapping:
                resolved = []
                for item in sorted(mapping, key=lambda item: item["type"]):
                    label = str(item["cfg_label"])
                    if inherited and label.isdigit() and 1 <= int(label) <= len(inherited):
                        resolved.append(inherited[int(label) - 1])
                    elif inherited and label in inherited:
                        resolved.append(label)
                    else:
                        resolved = None
                        break
    if not isinstance(resolved, list) or len(resolved) != count:
        raise ValueError("Supply elements with one chemical symbol per LMP atom type; type mapping is ambiguous")
    return resolved


def _fixed_atoms(root: Path, service, fixed_ids: list[int] | None,
                 fixed_ids_paths: list[str] | None) -> tuple[list[int], list[dict]]:
    ids = list(fixed_ids or [])
    sources = []
    for value in fixed_ids_paths or []:
        path = _resolve_input(root, service, value)
        if not path.is_file():
            raise ValueError(f"Fixed-ID file not found: {value}")
        for line in path.read_text(encoding="utf-8").splitlines():
            for token in line.partition("#")[0].split():
                ids.append(int(token))
        sources.append(find_structure_source(root, service, path))
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in ids):
        raise ValueError("fixed_ids must contain positive integer atom IDs")
    if fixed_ids_paths and not ids:
        raise ValueError("The supplied fixed-ID files contain no atom IDs")
    return sorted(set(ids)), sources


def configure_structure_minimize_for_project(
    *, project_id: str, source_lmp: str | None = None, purpose: str,
    fixed_ids: list[int] | None = None, fixed_ids_paths: list[str] | None = None,
    boundary: str | None = None, potential_name: str | None = None,
    elements: list[str] | None = None, min_style: str = "cg", min_etol: float = 0.0,
    min_ftol: float = 1.0e-6, min_maxiter: int = 1000, min_maxeval: int = 10000,
    description: str = "", label: str = "", structures: list[dict] | None = None,
    store: ProjectStore | None = None,
) -> str:
    project_store = store or ProjectStore()
    root = project_store.project_path(project_id)
    service = project_store.runs(project_id)
    try:
        if not isinstance(purpose, str) or not purpose.strip():
            raise ValueError("purpose is required: record why this exact frame is being minimized")
        if (source_lmp is None) == (structures is None):
            raise ValueError("Supply exactly one of source_lmp or structures")
        if structures is not None:
            return _configure_batch(
                root, service, structures, purpose=purpose, description=description, label=label,
                common={"fixed_ids": fixed_ids, "fixed_ids_paths": fixed_ids_paths,
                        "boundary": boundary, "potential_name": potential_name, "elements": elements,
                        "min_style": min_style, "min_etol": min_etol, "min_ftol": min_ftol,
                        "min_maxiter": min_maxiter, "min_maxeval": min_maxeval},
            )
        path = _resolve_input(root, service, source_lmp)
        if not path.is_file():
            raise ValueError(f"Source LMP not found: {source_lmp}")
        data = inspect_atomic_data(path)
        source = find_structure_source(root, service, path)
        origin = find_structure_origin(root, service, path)
        context, context_identity = _source_context(root, service, source, origin)
        potential, record = _resolve_potential(context, potential_name)
        potential["elements"] = _elements_for_types(context, source, service, data["atom_types"], elements)
        if record and not set(potential["elements"]).issubset(set(record.get("elements") or [])):
            raise ValueError("The selected potential does not support all supplied elements")
        resolved_boundary = boundary if boundary is not None else (context.get("simulation") or {}).get("boundary")
        if not resolved_boundary:
            raise ValueError("No source boundary is available; supply boundary explicitly (for example 'p p p')")
        ids, id_sources = _fixed_atoms(root, service, fixed_ids, fixed_ids_paths)
        missing_ids = set(ids) - set(data["ids"])
        if missing_ids:
            raise ValueError(f"fixed_ids are absent from the input: {sorted(missing_ids)[:20]}")
        payload = {
            "version": 1, "purpose": purpose.strip(), "description": description,
            "source": {**source, "origin": origin, **context_identity},
            "potential": potential,
            "simulation": {"boundary": resolved_boundary, "min_style": min_style,
                "min_etol": min_etol, "min_ftol": min_ftol,
                "min_maxiter": min_maxiter, "min_maxeval": min_maxeval},
            "constraints": {"mode": "fixed_ids" if ids else "none", "fixed_ids": ids,
                            "sources": id_sources},
        }
        cfg = build_structure_minimize_config(payload)
        canonical = config_to_dict(cfg)
        sources = []
        for relation, identity in [("input_structure", source), ("original_frame", origin)]:
            if identity.get("run_id"):
                sources.append(RunSourceRecord(
                    source_run_id=identity["run_id"], relation=relation,
                    source_fingerprint=identity["sha256"], metadata={**identity, "purpose": cfg.purpose},
                ))
        if context_identity.get("context_run_id"):
            sources.append(RunSourceRecord(
                source_run_id=context_identity["context_run_id"], relation="simulation_context",
                source_fingerprint=context_identity.get("context_config_sha256"),
                metadata={**context_identity, "purpose": cfg.purpose},
            ))
        for ordinal, identity in enumerate(id_sources):
            if identity.get("run_id"):
                sources.append(RunSourceRecord(
                    source_run_id=identity["run_id"], relation="fixed_ids", ordinal=ordinal,
                    source_fingerprint=identity["sha256"], metadata=identity,
                ))
        run = service.create_configured_run(
            workflow=Workflow.STRUCTURE_MINIMIZE, input_payload=canonical,
            source_run_id=source.get("run_id"), source_fingerprint=source["sha256"], sources=sources,
            parameters={"memory_tool": "run_structure_minimize",
                        "purpose": cfg.purpose, "description": cfg.description, "label": label,
                        "source": canonical["source"], "origin": origin,
                        "constraints": canonical["constraints"], "box_relax": False,
                        "loading": "none", "canonical_config": canonical},
        )
        return RunToolResult(run_id=run.run_id, workflow=run.workflow, status=run.status,
            manifest=run.manifest_path, result={"purpose": cfg.purpose, "source": source,
            "origin": origin, "constraints": canonical["constraints"], "box_relax": False,
            "boundary": resolved_boundary, "potential": potential}).to_json()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return str(exc)


def _configure_batch(root: Path, service, structures: list[dict], *, purpose: str,
                     description: str, label: str, common: dict) -> str:
    if not isinstance(structures, list) or not structures:
        raise ValueError("structures must be a nonempty ordered list")
    entries, sources = [], []
    output_stems: set[str] = set()
    fixed_source_ordinal = 0
    for index, item in enumerate(structures, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"structures[{index - 1}] must be an object")
        unknown = set(item) - (set(common) | {"source_lmp", "purpose", "description", "label"})
        if unknown:
            raise ValueError(f"Unsupported batch item fields: {', '.join(sorted(unknown))}")
        if not isinstance(item.get("source_lmp"), str) or not item["source_lmp"].strip():
            raise ValueError(f"structures[{index - 1}].source_lmp is required")
        options = {**common, **item}
        # Either constraint override replaces the complete common constraint
        # selection, so fixed_ids=[] really means all atoms free for this item.
        if "fixed_ids" in item or "fixed_ids_paths" in item:
            options["fixed_ids"] = item.get("fixed_ids")
            options["fixed_ids_paths"] = item.get("fixed_ids_paths")
        path = _resolve_input(root, service, item["source_lmp"])
        data = inspect_atomic_data(path)
        source = find_structure_source(root, service, path)
        origin = find_structure_origin(root, service, path)
        context, context_identity = _source_context(root, service, source, origin)
        potential, record = _resolve_potential(context, options["potential_name"])
        potential["elements"] = _elements_for_types(context, source, service, data["atom_types"], options["elements"])
        if record and not set(potential["elements"]).issubset(set(record.get("elements") or [])):
            raise ValueError("The selected potential does not support all supplied elements")
        boundary = options["boundary"] if options["boundary"] is not None else (context.get("simulation") or {}).get("boundary")
        if not boundary:
            raise ValueError("No source boundary is available; supply boundary explicitly (for example 'p p p')")
        ids, id_sources = _fixed_atoms(root, service, options["fixed_ids"], options["fixed_ids_paths"])
        if set(ids) - set(data["ids"]):
            raise ValueError(f"fixed_ids are absent from batch item {index}")
        cfg = build_structure_minimize_config({
            "version": 1, "purpose": item.get("purpose", purpose),
            "description": item.get("description", description),
            "source": {**source, "origin": origin, **context_identity}, "potential": potential,
            "simulation": {"boundary": boundary, **{key: options[key] for key in (
                "min_style", "min_etol", "min_ftol", "min_maxiter", "min_maxeval")}},
            "constraints": {"mode": "fixed_ids" if ids else "none", "fixed_ids": ids, "sources": id_sources},
        })
        item_label = str(item.get("label") or path.stem)
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", item_label).strip("_-")[:64] or "structure"
        item_key = f"{index:04d}_{slug}"
        source_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", path.stem).strip("._-")[:180] or "structure"
        base = f"{source_stem}_minimized"
        output_stem = base
        suffix = 2
        while output_stem in output_stems:
            output_stem = f"{base}_{suffix}"
            suffix += 1
        output_stems.add(output_stem)
        entries.append({"item_index": index, "item_key": item_key, "label": item_label,
                        "output_stem": output_stem, "config": config_to_dict(cfg)})
        for relation, identity in [("input_structure", source), ("original_frame", origin)]:
            if identity.get("run_id"):
                sources.append(RunSourceRecord(
                    source_run_id=identity["run_id"], relation=relation, ordinal=index - 1,
                    source_fingerprint=identity["sha256"], metadata={**identity, "purpose": cfg.purpose,
                    "item_index": index, "item_key": item_key},
                ))
        if context_identity.get("context_run_id"):
            sources.append(RunSourceRecord(
                source_run_id=context_identity["context_run_id"], relation="simulation_context",
                ordinal=index - 1, source_fingerprint=context_identity.get("context_config_sha256"),
                metadata={**context_identity, "purpose": cfg.purpose,
                          "item_index": index, "item_key": item_key},
            ))
        for identity in id_sources:
            if identity.get("run_id"):
                sources.append(RunSourceRecord(
                    source_run_id=identity["run_id"], relation="fixed_ids", ordinal=fixed_source_ordinal,
                    source_fingerprint=identity["sha256"], metadata={**identity,
                    "item_index": index, "item_key": item_key},
                ))
                fixed_source_ordinal += 1
    canonical = config_to_dict(build_structure_minimize_batch_config({
        "version": 1, "purpose": purpose, "description": description, "structures": entries,
    }))
    # A batch has no single primary input. Ordered source records preserve every
    # selected frame, including multiple frames from the same original run.
    run = service.create_configured_run(
        workflow=Workflow.STRUCTURE_MINIMIZE, input_payload=canonical, sources=sources,
        parameters={"memory_tool": "run_structure_minimize",
                    "purpose": purpose, "description": description, "label": label,
                    "batch": True, "item_count": len(entries), "execution_order": "input_order",
                    "box_relax": False, "loading": "none", "canonical_config": canonical},
    )
    return RunToolResult(run_id=run.run_id, workflow=run.workflow, status=run.status,
        manifest=run.manifest_path, result={"purpose": purpose, "batch": True,
        "item_count": len(entries), "execution_order": "input_order",
        "items": [_batch_item_result(entry) for entry in entries]}).to_json()


def _batch_item_result(entry: dict) -> dict:
    cfg = entry["config"]
    return {"item_index": entry["item_index"], "item_key": entry["item_key"], "label": entry["label"],
            "output_stem": entry.get("output_stem", f"{entry['item_key']}.minimized"),
            "purpose": cfg["purpose"], "source": cfg["source"], "origin": cfg["source"]["origin"],
            "constraints": cfg["constraints"], "boundary": cfg["simulation"]["boundary"],
            "potential": cfg["potential"], "status": "pending", "error": "", "outputs": {}}


_REQUIRED_OUTPUTS = ("minimized.lmp", "minimized.cfg", "structure_metrics.json")
_RESULT_ARTIFACT_FIELDS = ("artifact_id", "role", "path")


def execute_structure_minimize_for_project(
    *, project_id: str, run_id: str, np: int | None = None,
    session_key: str | None = None, store: ProjectStore | None = None,
) -> str:
    project_store = store or ProjectStore()
    root = project_store.project_path(project_id)
    service = project_store.runs(project_id)
    try:
        run = configured_run(service, run_id, Workflow.STRUCTURE_MINIMIZE)
        config_path = resolve_project_path(root, str(run.config_path))
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        if "structures" in payload:
            return _execute_batch(root, service, run, payload, np=np, session_key=session_key)
        cfg = load_structure_minimize_config(config_path)
        if config_to_dict(cfg) != run.parameters.get("canonical_config"):
            raise ValueError("Configuration changed after the run was configured")
        ranks = resolve_mpi_ranks(np)
        source = resolve_project_path(root, cfg.source["path"])
        potential = current_potential_path(cfg.potential["file"])
        _validate_config_inputs(root, cfg)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return str(exc)

    output = service.artifacts.run_directory(run_id)
    service.start_run(run_id, progress={"stage": "snapshotting", "mpi_ranks": ranks})
    result = {"purpose": cfg.purpose, "source": cfg.source, "origin": cfg.source["origin"],
              "constraints": cfg.constraints, "box_relax": False, "loading": "none",
              "boundary": cfg.simulation["boundary"], "mpi_ranks": ranks}
    error = ""
    status = RunStatus.FAILED
    try:
        inputs = output / "inputs"
        inputs.mkdir(exist_ok=True)
        data_snapshot = inputs / "source.lmp"
        potential_snapshot = inputs / potential.name
        if potential_snapshot == data_snapshot:
            potential_snapshot = inputs / "potential.file"
        shutil.copy2(source, data_snapshot)
        shutil.copy2(potential, potential_snapshot)
        for role, snapshot, digest in [("source_lmp", data_snapshot, cfg.source["sha256"]),
                                       ("potential", potential_snapshot, cfg.potential["sha256"])]:
            if sha256_file(snapshot) != digest:
                raise ValueError(f"Input changed during snapshotting: {snapshot.name}")
            service.register_artifact(run_id, role, snapshot.relative_to(output).as_posix(),
                ProvenanceClass.PRIMARY, metadata={"sha256": digest, "purpose": cfg.purpose,
                                                  "origin": cfg.source["origin"]})
        for index, identity in enumerate(cfg.constraints.get("sources", [])):
            snapshot = inputs / f"fixed_ids_{index:03d}.txt"
            shutil.copy2(resolve_project_path(root, identity["path"]), snapshot)
            if sha256_file(snapshot) != identity["sha256"]:
                raise ValueError("Fixed-ID source changed during snapshotting")
            service.register_artifact(run_id, "fixed_ids_source", snapshot.relative_to(output).as_posix(),
                                      ProvenanceClass.PRIMARY, metadata=identity)
        argv = mpirun_argv(STRUCTURE_MINIMIZE_SCRIPT, ranks, "--config", str(config_path),
                           "--output-dir", str(output), "--data-file", str(data_snapshot),
                           "--potential-file", str(potential_snapshot))
        command = launch(argv, ranks=ranks, timeout_sec=STRUCTURE_MINIMIZE_TIMEOUT_SEC,
                         session_key=session_key)
        save_launcher_logs(output, command)
        metrics_path = output / "structure_metrics.json"
        if metrics_path.is_file():
            result.update(json.loads(metrics_path.read_text(encoding="utf-8")))
        status, error = command_status(command, output, _REQUIRED_OUTPUTS, label="structure_minimize")
        if status is RunStatus.CANCELLED:
            error = "Structure minimization cancelled"
        elif status is RunStatus.COMPLETED and (result.get("minimization_count") != 1 or not all(
            result.get("validation", {}).get(key) is True for key in (
                "cell_preserved", "atom_ids_preserved", "atom_types_preserved",
                "masses_preserved", "fixed_atoms_preserved",
            )
        )):
            status, error = RunStatus.FAILED, "Required single-stage/preservation checks did not pass"
    except Exception as exc:
        status, error = RunStatus.FAILED, str(exc)
    origin = cfg.source["origin"]
    result["warnings"] = (
        ["The minimization did not meet an energy/force tolerance; inspect its stopping criterion."]
        if result.get("converged") is False else []
    )
    result["summary"] = (
        f"{cfg.purpose}; source frame {origin.get('path')}; "
        f"original run {origin.get('run_id') or 'unregistered'}; "
        f"fixed cell, no loading, constraints={cfg.constraints['mode']}; "
        f"status={status.value}, converged={result.get('converged')}"
    )
    metadata = {"version": 1, "status": status.value, **result, "error": error}
    write_json_atomic(output / "minimize_run.json", metadata, sort_keys=False)
    register_launcher_logs(service, run_id, output)
    for role, filename in [("initial_cfg", "initial.cfg"), ("minimized_cfg", "minimized.cfg"),
        ("minimized_lmp", "minimized.lmp"), ("log", "log.lammps"),
        ("trajectory", "minimize.lammpstrj"), ("run_metadata", "minimize_run.json"),
        ("structure_metrics", "structure_metrics.json")]:
        if (output / filename).is_file():
            service.register_artifact(run_id, role, filename, ProvenanceClass.PRIMARY,
                metadata={"origin": origin, "purpose": cfg.purpose, "source_run_id": cfg.source.get("run_id"),
                          "source": cfg.source, "constraints": cfg.constraints["mode"],
                          "fixed_ids": cfg.constraints["fixed_ids"], "box_relax": False,
                          "resolved_context": {"potential": cfg.potential, "simulation": cfg.simulation,
                                               "constraints": cfg.constraints}})
    terminal = finish_run(
        service, run_id, status, result=result, progress={"stage": "finished", "mpi_ranks": ranks},
        error=error, memory_tool="run_structure_minimize", cancelled_message="Structure minimization cancelled",
    )
    return run_tool_result(service, terminal, artifact_fields=_RESULT_ARTIFACT_FIELDS).to_json()


def _validate_config_inputs(root: Path, cfg: StructureMinimizeConfig) -> None:
    for path, expected in [
        (resolve_project_path(root, cfg.source["path"]), cfg.source["sha256"]),
        (current_potential_path(cfg.potential["file"]), cfg.potential["sha256"]),
    ]:
        if sha256_file(path) != expected:
            raise ValueError(f"Input changed after configuration: {path.name}")
    for identity in cfg.constraints.get("sources", []):
        if sha256_file(resolve_project_path(root, identity["path"])) != identity["sha256"]:
            raise ValueError("Fixed-ID source changed after configuration")
    if cfg.source.get("context_config_path") and sha256_file(resolve_project_path(
        root, cfg.source["context_config_path"])) != cfg.source["context_config_sha256"]:
        raise ValueError("Source simulation configuration changed after configuration")


def _execute_batch(root: Path, service, run, payload: dict, *, np: int | None,
                   session_key: str | None) -> str:
    batch = build_structure_minimize_batch_config(payload)
    if config_to_dict(batch) != run.parameters.get("canonical_config"):
        raise ValueError("Configuration changed after the run was configured")
    ranks = resolve_mpi_ranks(np)
    configs = [build_structure_minimize_config(entry["config"]) for entry in batch.structures]
    # Validate the complete batch before starting any simulation.
    for cfg in configs:
        _validate_config_inputs(root, cfg)
    output = service.artifacts.run_directory(run.run_id)
    result = {"purpose": batch.purpose, "batch": True, "item_count": len(configs),
              "execution_order": "input_order", "box_relax": False, "loading": "none",
              "mpi_ranks": ranks, "output_directory": output.relative_to(root).as_posix(),
              "items": [_batch_item_result(entry) for entry in batch.structures]}
    service.start_run(run.run_id, progress={"stage": "snapshotting", "mpi_ranks": ranks,
                                          "item_count": len(configs), "completed_count": 0})
    status, error = RunStatus.FAILED, ""
    active_item = None
    prepared = []
    try:
        for entry, cfg, item in zip(batch.structures, configs, result["items"]):
            active_item = item
            work = output / "items" / entry["item_key"]
            inputs = work / "inputs"
            inputs.mkdir(parents=True)
            metadata = {"item_index": entry["item_index"], "item_key": entry["item_key"],
                        "purpose": cfg.purpose, "source": cfg.source, "origin": cfg.source["origin"],
                        "constraints": cfg.constraints["mode"], "fixed_ids": cfg.constraints["fixed_ids"],
                        "box_relax": False, "resolved_context": {
                            "potential": cfg.potential, "simulation": cfg.simulation,
                            "constraints": cfg.constraints}}
            data_snapshot = inputs / "source.lmp"
            potential_snapshot = inputs / ("potential_" + current_potential_path(cfg.potential["file"]).name)
            for role, path, snapshot, digest in [
                ("source_lmp", resolve_project_path(root, cfg.source["path"]), data_snapshot, cfg.source["sha256"]),
                ("potential", current_potential_path(cfg.potential["file"]), potential_snapshot, cfg.potential["sha256"]),
            ]:
                shutil.copy2(path, snapshot)
                if sha256_file(snapshot) != digest:
                    raise ValueError(f"Input changed during snapshotting: {snapshot.name}")
                service.register_artifact(run.run_id, role, snapshot.relative_to(output).as_posix(),
                    ProvenanceClass.PRIMARY, metadata={**metadata, "sha256": digest})
            for index, identity in enumerate(cfg.constraints.get("sources", [])):
                snapshot = inputs / f"fixed_ids_{index:03d}.txt"
                shutil.copy2(resolve_project_path(root, identity["path"]), snapshot)
                if sha256_file(snapshot) != identity["sha256"]:
                    raise ValueError("Fixed-ID source changed during snapshotting")
                service.register_artifact(run.run_id, "fixed_ids_source", snapshot.relative_to(output).as_posix(),
                    ProvenanceClass.PRIMARY, metadata={**identity, **metadata})
            item_config = work / "input.json"
            item_config.write_text(json.dumps(config_to_dict(cfg), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            service.register_artifact(run.run_id, "item_config", item_config.relative_to(output).as_posix(),
                                      ProvenanceClass.PRIMARY, metadata=metadata)
            prepared.append((cfg, item, work, item_config, data_snapshot, potential_snapshot, metadata))
        active_item = None
        # A single agent/tool call owns one resource allocation and runs the
        # selected structures in the exact order recorded in input.json.
        with lammps_resource_slot(ranks, session_key=session_key):
            for cfg, item, work, item_config, data_snapshot, potential_snapshot, metadata in prepared:
                active_item = item
                item["status"] = "running"
                service.update_progress(run.run_id, progress={
                    "stage": "minimizing", "mpi_ranks": ranks, "item_count": len(configs),
                    "current_item_index": item["item_index"], "current_item_key": item["item_key"],
                    "completed_count": sum(value["status"] == "completed" for value in result["items"]),
                })
                argv = mpirun_argv(STRUCTURE_MINIMIZE_SCRIPT, ranks, "--config", str(item_config),
                                   "--output-dir", str(work), "--data-file", str(data_snapshot),
                                   "--potential-file", str(potential_snapshot))
                command = None
                command_error = ""
                try:
                    command = run_in_lammps_env(argv, timeout_sec=STRUCTURE_MINIMIZE_TIMEOUT_SEC,
                                                session_key=session_key)
                    save_launcher_logs(work, command)
                    register_launcher_logs(service, run.run_id, work,
                                           prefix=f"{work.relative_to(output).as_posix()}/", metadata=metadata)
                except Exception as exc:
                    command_error = str(exc) or type(exc).__name__
                metrics_path = work / "structure_metrics.json"
                if metrics_path.is_file():
                    try:
                        item.update(json.loads(metrics_path.read_text(encoding="utf-8")))
                    except (OSError, ValueError, TypeError) as exc:
                        command_error = command_error or f"Cannot read item metrics: {exc}"
                if command is not None and command.cancelled:
                    item["status"] = "cancelled"
                    item["error"] = "Structure minimization cancelled"
                    status = RunStatus.CANCELLED
                elif command_error:
                    item["status"] = "failed"
                    item["error"] = command_error
                elif failure := command_status(command, work, _REQUIRED_OUTPUTS, label="structure_minimize")[1]:
                    item["status"] = "failed"
                    item["error"] = failure
                elif item.get("minimization_count") != 1 or not all(
                    item.get("validation", {}).get(key) is True for key in (
                        "cell_preserved", "atom_ids_preserved", "atom_types_preserved",
                        "masses_preserved", "fixed_atoms_preserved")
                ):
                    item["status"] = "failed"
                    item["error"] = "Required single-stage/preservation checks did not pass"
                else:
                    item["status"] = "completed"
                item["warnings"] = (["The minimization did not meet an energy/force tolerance; inspect its stopping criterion."]
                                    if item.get("converged") is False else [])
                for role, filename in [("initial_cfg", "initial.cfg"), ("minimized_cfg", "minimized.cfg"),
                    ("minimized_lmp", "minimized.lmp"), ("log", "log.lammps"),
                    ("trajectory", "minimize.lammpstrj"), ("structure_metrics", "structure_metrics.json")]:
                    path = work / filename
                    if not path.is_file():
                        continue
                    if role in {"minimized_cfg", "minimized_lmp"}:
                        published = output / f"{item['output_stem']}{path.suffix}"
                        shutil.copy2(path, published)
                        path = published
                    artifact = service.register_artifact(run.run_id, role, path.relative_to(output).as_posix(),
                        ProvenanceClass.PRIMARY, metadata={**metadata, "status": item["status"],
                        "converged": item.get("converged"), "sha256": sha256_file(path)})
                    item["outputs"][role] = {"artifact_id": artifact.artifact_id, "path": artifact.path}
                write_json_atomic(output / "minimize_run.json", {"version": 1, "status": "running", **result},
                                  sort_keys=False)
                if item["status"] != "completed":
                    error = f"Batch item {item['item_index']} ({item['item_key']}): {item['error']}"
                    break
                active_item = None
            else:
                status = RunStatus.COMPLETED
    except Exception as exc:
        error = str(exc)
        if active_item is not None:
            active_item.update(status="failed", error=error)
    result["completed_count"] = sum(item["status"] == "completed" for item in result["items"])
    result["failed_count"] = sum(item["status"] == "failed" for item in result["items"])
    result["cancelled_count"] = sum(item["status"] == "cancelled" for item in result["items"])
    result["pending_count"] = sum(item["status"] == "pending" for item in result["items"])
    result["converged_count"] = sum(item.get("converged") is True and item["status"] == "completed"
                                    for item in result["items"])
    result["summary"] = (f"{batch.purpose}; {len(configs)} structures in input order; "
        f"{result['completed_count']} completed, {result['converged_count']} converged, "
        f"{result['failed_count']} failed, {result['cancelled_count']} cancelled, {result['pending_count']} pending; "
        f"fixed cell, no loading; outputs {result['output_directory']}; "
        "each item records its selected source, original frame, purpose and constraints")
    write_json_atomic(output / "minimize_run.json", {"version": 1, "status": status.value, **result,
                                                     "error": error}, sort_keys=False)
    service.register_artifact(run.run_id, "run_metadata", "minimize_run.json", ProvenanceClass.PRIMARY,
                              metadata={"batch": True, "purpose": batch.purpose, "item_count": len(configs)})
    progress = {"stage": "finished", "mpi_ranks": ranks, "item_count": len(configs),
                "completed_count": result["completed_count"]}
    terminal = finish_run(
        service, run.run_id, status, result=result, progress=progress, error=error,
        memory_tool="run_structure_minimize", cancelled_message="Structure minimization cancelled",
    )
    return run_tool_result(service, terminal, artifact_fields=_RESULT_ARTIFACT_FIELDS).to_json()


@tool
def write_structure_minimize_config(
    source_lmp: str | None = None, purpose: str = "", fixed_ids: list[int] | None = None,
    fixed_ids_paths: list[str] | None = None, boundary: str | None = None,
    potential_name: str | None = None, elements: list[str] | None = None,
    min_style: str = "cg", min_etol: float = 0.0, min_ftol: float = 1.0e-6,
    min_maxiter: int = 1000, min_maxeval: int = 10000, description: str = "", label: str = "",
    structures: list[dict] | None = None,
) -> str:
    """Configure fixed-box, no-load minimization of one structure or an ordered batch.

    source_lmp is an exact project-relative path or artifact ID, including one
    selected converted_lmp. purpose records the user's reason for selecting and
    relaxing this frame in durable project memory. Never invent a scientific purpose.
    Default: all atoms free. fixed_ids and/or project-local fixed_ids_paths fully
    freeze those IDs in x/y/z (setforce 0 0 0); no inherited shear loads or springs.
    PBC and potential are inherited from the selected source's recorded context;
    for untracked imports supply boundary, registered potential_name and elements
    (one symbol per LMP type). boundary supports fixed cell p/f dimensions only.
    Original frame, source run/artifact/hash and purpose are preserved separately
    from the conversion run. Run only the returned run_id with run_structure_minimize.
    Supply exactly one of source_lmp or structures. structures is a nonempty list
    of objects with source_lmp and optional purpose, label, description, fixed_ids,
    fixed_ids_paths, boundary, potential_name, elements and minimizer overrides.
    label is an optional display name, defaulting to the input filename stem.
    It does not rename the published structures. Omit it for lists of shear frames.
    Common options apply unless the item overrides them. Either per-item fixed-ID
    option replaces the common constraint selection; fixed_ids=[] makes that item
    free. Use one agent, one writer call and one runner call for the whole batch.
    Items run sequentially. Final LMP/CFG files share the run output folder with
    names <input_stem>_minimized.lmp/.cfg; duplicate names gain _2, _3, etc.
    Per-item logs and inputs live in items/<item_key>/.
    Stop at the first failure/cancellation, preserving completed outputs and
    explicit pending item statuses. Every item retains its own source and purpose.
    """
    project_id = runtime_project_id()
    if not project_id:
        return "No active project_id in run config."
    return configure_structure_minimize_for_project(project_id=project_id,
        source_lmp=source_lmp, purpose=purpose, fixed_ids=fixed_ids,
        fixed_ids_paths=fixed_ids_paths, boundary=boundary, potential_name=potential_name,
        elements=elements, min_style=min_style, min_etol=min_etol, min_ftol=min_ftol,
        min_maxiter=min_maxiter, min_maxeval=min_maxeval, description=description, label=label,
        structures=structures)


@tool
def run_structure_minimize(run_id: str, np: int | None = None) -> str:
    """Execute one configured single/batch run sequentially, preserving per-item provenance."""
    project_id = runtime_project_id()
    if not project_id:
        return "No active project_id in run config."
    return execute_structure_minimize_for_project(project_id=project_id, run_id=run_id,
                                                  np=np, session_key=runtime_session_key())
