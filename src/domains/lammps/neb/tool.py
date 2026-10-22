"""Agent tools for configured, project-tracked grain-boundary NEB runs."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from domains.lammps.common.potential_files import current_potential_path
from core.util.graph_config import runtime_project_id
from core.util.io import sha256_file
from domains.lammps.common.defaults import (
    NEB_DUMP_EVERY,
    NEB_ETOL,
    NEB_FTOL,
    NEB_MIN_STYLE,
    NEB_N1,
    NEB_N2,
    NEB_NEVERY,
    NEB_SPRING,
    NEB_TIMESTEP,
    NEB_VERBOSITY,
)
from domains.lammps.common.artifacts import (
    NEB_MASTER_LOG,
    NEB_MEP_CSV,
    NEB_MEP_JSON,
)
from domains.lammps.common.settings import (
    GB_NEB_SCRIPT,
    GB_NEB_TIMEOUT_SEC,
    LAMMPS_NEB_RANKS_PER_REPLICA,
    LAMMPS_NEB_REPLICAS,
    LAMMPS_TOTAL_MPI_RANKS,
)
from project_workspace.artifacts.files import resolve_project_path
from project_memory.publication.service import RunService
from project_workspace.artifacts.models import ProvenanceClass
from project_memory.models import RunSourceRecord, RunStatus, Workflow
from project_workspace.store import ProjectStore
from domains.lammps.neb.config import (
    GBNebConfig,
    build_gb_neb_config,
    config_to_dict as gb_neb_config_to_dict,
    load_gb_neb_config,
)
from domains.lammps.shear.config import (
    config_to_dict as shear_config_to_dict,
    load_gb_shear_config,
    read_atom_ids,
    validate_boundary_ids,
)
from domains.lammps.cfg_convert.converter import read_extended_cfg
from domains.lammps.common.lineage import find_structure_origin, find_structure_source
from domains.lammps.structure_minimize.engine import inspect_atomic_data
from domains.lammps.structure_minimize.tool import _resolve_potential, _source_context
from core.execution.sessions import runtime_session_key
from domains.lammps.common.launch import (
    command_status,
    configured_run,
    finish_run,
    launch,
    mpirun_argv,
    register_launcher_logs,
    run_tool_result,
    save_launcher_logs,
)
from project_memory.publication.results import RunToolResult


def _project_input(
    project_root: Path,
    value: str,
    *,
    label: str,
    suffixes: set[str],
    service: RunService | None = None,
) -> Path:
    text = str(value).strip().replace("\\", "/")
    if service is not None:
        try:
            text = service.memory.get_artifact(text).path
        except ValueError:
            pass
    if not text or Path(text).is_absolute() or ".." in Path(text).parts:
        raise ValueError(f"{label} must be a safe project-relative file path")
    path = resolve_project_path(project_root, text)
    if not path.is_file() or path.suffix.lower() not in suffixes:
        expected = " or ".join(sorted(suffixes))
        raise ValueError(f"{label} must identify an existing {expected} file: {text}")
    return path


def _source_payload(path: Path, project_root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(project_root.resolve()).as_posix(),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _endpoint_identity(
    root: Path, service: RunService, path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = find_structure_source(root, service, path)
    origin = find_structure_origin(root, service, path)
    context, context_identity = _source_context(root, service, source, origin)
    identity = {**source, "origin": origin, **context_identity}
    if source.get("artifact_id"):
        artifact = service.memory.get_artifact(source["artifact_id"])
        if source.get("workflow") == Workflow.STRUCTURE_MINIMIZE.value and artifact.role in {"minimized_lmp", "minimized_cfg"}:
            source_run = service.memory.get_run(artifact.run_id)
            item_status = artifact.metadata.get("status", source_run.status.value)
            if item_status != RunStatus.COMPLETED.value:
                raise ValueError("NEB requires a completed minimization output; the selected item did not complete")
        for key in ("purpose", "item_index", "item_key", "constraints"):
            if key in artifact.metadata:
                identity[key] = artifact.metadata[key]
    return identity, context


def _endpoint_material(path: Path, context: dict, element: str | None) -> dict[str, Any]:
    """Require an explicit single chemical species; never infer it from mass."""
    inherited = (context.get("potential") or {}).get("elements")
    if not inherited and (context.get("material") or {}).get("element"):
        inherited = [context["material"]["element"]]
    if inherited and len(set(inherited)) != 1:
        raise ValueError("GB NEB currently supports one chemical element; mixed-element endpoints are unsupported")
    if element and inherited and set(inherited) != {element}:
        raise ValueError("element differs from the endpoint's recorded chemical species")
    selected = element or (inherited[0] if inherited else None)
    if path.suffix.lower() == ".cfg":
        atoms = read_extended_cfg(path).atoms
        labels = {atom.label for atom in atoms}
        chemical_labels = {label for label in labels if re.fullmatch(r"[A-Z][a-z]?", label)}
        if len(chemical_labels) > 1:
            raise ValueError("CFG endpoint must identify one chemical element consistently")
        if any(not label.isdigit() or int(label) <= 0 for label in labels - chemical_labels):
            raise ValueError("CFG endpoint labels must be chemical symbols or positive integer atom types")
        if chemical_labels:
            actual = next(iter(chemical_labels))
            if selected and selected != actual:
                raise ValueError("CFG element labels differ from the selected NEB element")
            selected = selected or actual
        masses = [atom.mass for atom in atoms]
    else:
        data = inspect_atomic_data(path)
        masses = list(data["masses"].values())
    if not selected or not re.fullmatch(r"[A-Z][a-z]?", selected):
        raise ValueError("No endpoint element is available; supply element explicitly")
    if not masses or any(not math.isclose(mass, masses[0], rel_tol=1.0e-8) for mass in masses):
        raise ValueError("GB NEB requires one consistent atomic mass across endpoint atom types")
    return {"element": selected, "mass": masses[0]}


@tool
def write_gb_neb_config(
    initial_structure_path: str,
    final_structure_path: str,
    top_boundary_ids_path: str | None = None,
    bottom_boundary_ids_path: str | None = None,
    shear_config_path: str | None = None,
    description: str | None = None,
    replicas: int = LAMMPS_NEB_REPLICAS,
    ranks_per_replica: int = LAMMPS_NEB_RANKS_PER_REPLICA,
    spring: float = NEB_SPRING,
    min_style: str = NEB_MIN_STYLE,
    timestep: float = NEB_TIMESTEP,
    etol: float = NEB_ETOL,
    ftol: float = NEB_FTOL,
    n1: int = NEB_N1,
    n2: int = NEB_N2,
    nevery: int = NEB_NEVERY,
    dump_every: int = NEB_DUMP_EVERY,
    verbosity: str = NEB_VERBOSITY,
    label: str | None = None,
    constraint_mode: str | None = None,
    potential_name: str | None = None,
    boundary: str | None = None,
    element: str | None = None,
    purpose: str = "",
) -> str:
    """Configure NEB between exact CFG/LMP paths or artifact IDs.

    Endpoints may be single or batch structure_minimize outputs; their exact
    context supplies potential, element and PBC. Untracked inputs need an explicit
    potential_name, boundary and unambiguous element. Both endpoint contexts must
    agree unless an explicit potential/PBC override is supplied. NEB supports one
    chemical element, including multiple types of that same element.

    constraint_mode='none' leaves all atoms free, with no ID files or shear config
    required. 'shear_boundaries' requires both ID files and applies setforce 0 0 NULL.
    Omitted mode is inferred from supplied IDs; no IDs means none. Constraints are
    never inherited from a CFG/LMP. Optional shear_config_path supplies shear physics.
    CFG conversion happens inside run_gb_neb, which verifies atom IDs/types/cells.
    purpose and each endpoint's original frame and batch item are stored in memory.
    replicas*ranks_per_replica must fit the configured total MPI budget.
    """
    project_id = runtime_project_id()
    if not project_id:
        return "No active project_id in run config."
    store = ProjectStore()
    project_root = store.project_path(project_id).resolve()
    service = store.runs(project_id)
    try:
        for name, value in (("constraint_mode", constraint_mode), ("potential_name", potential_name),
                            ("boundary", boundary), ("element", element)):
            if value is not None and not value.strip():
                raise ValueError(f"{name} must be nonempty when supplied")
        mode = constraint_mode or (
            "shear_boundaries" if top_boundary_ids_path or bottom_boundary_ids_path else "none"
        )
        if mode not in {"none", "shear_boundaries"}:
            raise ValueError("constraint_mode must be none or shear_boundaries")
        if mode == "none" and (top_boundary_ids_path or bottom_boundary_ids_path):
            raise ValueError("constraint_mode=none does not accept boundary ID files")
        if mode == "shear_boundaries" and not (top_boundary_ids_path and bottom_boundary_ids_path):
            raise ValueError("shear_boundaries requires both top and bottom boundary ID files")
        initial_path = _project_input(
            project_root,
            initial_structure_path,
            label="initial_structure_path",
            suffixes={".cfg", ".lmp"},
            service=service,
        )
        final_path = _project_input(
            project_root,
            final_structure_path,
            label="final_structure_path",
            suffixes={".cfg", ".lmp"},
            service=service,
        )
        initial_identity, initial_context = _endpoint_identity(project_root, service, initial_path)
        final_identity, final_context = _endpoint_identity(project_root, service, final_path)
        endpoints = {"initial": initial_identity, "final": final_identity}
        optional_paths: dict[str, Path] = {}
        shear_context = {}
        if shear_config_path:
            shear_path = _project_input(project_root, shear_config_path,
                label="shear_config_path", suffixes={".json"}, service=service)
            shear_context = shear_config_to_dict(load_gb_shear_config(shear_path))
            potential_file = current_potential_path(shear_context["potential"]["file"])
            if not potential_file.is_absolute():
                shear_context["potential"]["file"] = str((project_root / potential_file).resolve())
            optional_paths["shear_config"] = shear_path
        context = shear_context or initial_context or final_context
        resolved_boundary = boundary or (context.get("simulation") or {}).get("boundary")
        if not resolved_boundary:
            raise ValueError("No endpoint boundary context is available; supply boundary explicitly")
        resolved_boundary = " ".join(resolved_boundary.split())
        potential, record = _resolve_potential(context, potential_name)
        for endpoint_context in (initial_context, final_context):
            if not endpoint_context:
                continue
            if boundary is None:
                recorded = (endpoint_context.get("simulation") or {}).get("boundary")
                if recorded and " ".join(recorded.split()) != resolved_boundary:
                    raise ValueError("Endpoint boundaries differ; select an explicit boundary if intended")
            if potential_name is None and endpoint_context.get("potential"):
                recorded, _ = _resolve_potential(endpoint_context, None)
                if any(recorded[key] != potential[key] for key in ("pair_style", "sha256")):
                    raise ValueError("Endpoint potentials differ; select an explicit potential_name if intended")
        material = _endpoint_material(initial_path, initial_context or context, element)
        final_material = _endpoint_material(final_path, final_context or context, element)
        if material["element"] != final_material["element"] or not math.isclose(
            material["mass"], final_material["mass"], rel_tol=1.0e-8
        ):
            raise ValueError("NEB endpoints must have the same chemical element and atomic mass")
        if record and material["element"] not in (record.get("elements") or []):
            raise ValueError("Selected potential does not support the endpoint element")
        if shear_context:
            inherited, _ = _resolve_potential(shear_context, None)
            if any(inherited[key] != potential[key] for key in ("pair_style", "sha256")):
                raise ValueError("Selected potential differs from the supplied shear config")
            if resolved_boundary != shear_context["simulation"]["boundary"]:
                raise ValueError("Selected boundary differs from the supplied shear config")
            if material["element"] != shear_context["material"]["element"] or not math.isclose(
                material["mass"], shear_context["material"]["mass"], rel_tol=1.0e-8
            ):
                raise ValueError("Endpoint material differs from the supplied shear config")
        if mode == "shear_boundaries":
            for name, value in (("top_boundary_ids", top_boundary_ids_path),
                                ("bottom_boundary_ids", bottom_boundary_ids_path)):
                optional_paths[name] = _project_input(project_root, value,
                    label=name, suffixes={".ids"}, service=service)
            validate_boundary_ids(read_atom_ids(optional_paths["top_boundary_ids"]),
                                  read_atom_ids(optional_paths["bottom_boundary_ids"]))
        for name, value in (("replicas", replicas), ("ranks_per_replica", ranks_per_replica)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        resolved_replicas = int(replicas)
        resolved_ranks_per_replica = int(ranks_per_replica)
        requested_ranks = resolved_replicas * resolved_ranks_per_replica
        if requested_ranks > LAMMPS_TOTAL_MPI_RANKS:
            raise ValueError(
                f"NEB requests {resolved_replicas} replicas x "
                f"{resolved_ranks_per_replica} ranks/replica = {requested_ranks} "
                f"MPI ranks, exceeding GBCLAW_LAMMPS_TOTAL_MPI_RANKS="
                f"{LAMMPS_TOTAL_MPI_RANKS}"
            )

        initial_source = _source_payload(initial_path, project_root)
        final_source = _source_payload(final_path, project_root)
        payload = {
            "version": 1,
            "description": str(description or "").strip(),
            "inputs": {
                "initial_structure": {
                    **initial_source,
                    "format": initial_path.suffix.lower().lstrip("."),
                },
                "final_structure": {
                    **final_source,
                    "format": final_path.suffix.lower().lstrip("."),
                },
                **{name: _source_payload(path, project_root) for name, path in optional_paths.items()},
            },
            "material": material,
            "simulation": {"boundary": resolved_boundary},
            "potential": potential,
            "constraints": {"mode": mode},
            "neb": {
                "replicas": resolved_replicas,
                "ranks_per_replica": resolved_ranks_per_replica,
                "spring": spring,
                "min_style": min_style,
                "timestep": timestep,
                "etol": etol,
                "ftol": ftol,
                "n1": n1,
                "n2": n2,
                "nevery": nevery,
            },
            "output": {
                "dump_every": dump_every,
                "verbosity": verbosity,
            },
        }
        cfg = build_gb_neb_config(payload)
        if cfg.neb.total_mpi_ranks > LAMMPS_TOTAL_MPI_RANKS:
            raise ValueError(
                f"NEB requires {cfg.neb.total_mpi_ranks} MPI ranks, exceeding "
                f"GBCLAW_LAMMPS_TOTAL_MPI_RANKS={LAMMPS_TOTAL_MPI_RANKS}"
            )
        canonical = gb_neb_config_to_dict(cfg)
        fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8"))
        resolved_purpose = (purpose.strip() or str(description or "").strip()
                            or "Compute NEB between the selected endpoint structures.")
        sources = []
        for ordinal, (name, identity) in enumerate(endpoints.items()):
            for relation, source in ((f"{name}_endpoint", identity),
                                     ("original_frame", identity["origin"])):
                if source.get("run_id"):
                    sources.append(RunSourceRecord(source_run_id=source["run_id"],
                        relation=relation, ordinal=ordinal, source_fingerprint=source["sha256"],
                        metadata={**source, "endpoint": name, "purpose": resolved_purpose}))
            if identity.get("context_run_id"):
                sources.append(RunSourceRecord(source_run_id=identity["context_run_id"],
                    relation="simulation_context", ordinal=ordinal,
                    source_fingerprint=identity.get("context_config_sha256"),
                    metadata={key: value for key, value in identity.items() if key.startswith("context_")}))
        for name, path in optional_paths.items():
            identity = find_structure_source(project_root, service, path)
            if identity.get("run_id"):
                sources.append(RunSourceRecord(source_run_id=identity["run_id"], relation=name,
                    source_fingerprint=identity["sha256"], metadata=identity))
        run = service.create_configured_run(
            workflow=Workflow.GB_NEB,
            input_payload=canonical,
            source_run_id=initial_identity.get("run_id"),
            sources=sources,
            parameters={
                "memory_tool": "run_gb_neb",
                "description": cfg.description,
                "purpose": resolved_purpose,
                "label": str(label or "").strip(),
                "element": cfg.material.element,
                "initial_structure": cfg.inputs.initial_structure.path,
                "final_structure": cfg.inputs.final_structure.path,
                "shear_config": cfg.inputs.shear_config.path if cfg.inputs.shear_config else None,
                "constraint_mode": mode,
                "endpoints": endpoints,
                "physics_overrides": {"potential_name": potential_name, "boundary": boundary, "element": element},
                "replicas": cfg.neb.replicas,
                "ranks_per_replica": cfg.neb.ranks_per_replica,
                "total_mpi_ranks": cfg.neb.total_mpi_ranks,
                "canonical_config": canonical,
            },
            source_fingerprint=f"sha256:{fingerprint.hexdigest()}",
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return str(exc)

    return RunToolResult(
        run_id=run.run_id,
        workflow=run.workflow,
        status=run.status,
        manifest=run.manifest_path,
        artifacts=[{"role": "config", "path": run.config_path}],
        result={
            "replicas": cfg.neb.replicas,
            "ranks_per_replica": cfg.neb.ranks_per_replica,
            "total_mpi_ranks": cfg.neb.total_mpi_ranks,
            "source_fingerprint": run.source_fingerprint,
            "constraint_mode": mode,
            "endpoints": endpoints,
            "purpose": resolved_purpose,
        },
    ).to_json()


def _validate_sources(config: GBNebConfig, project_root: Path) -> None:
    root = project_root.resolve()
    for label, source in (
        ("initial structure", config.inputs.initial_structure),
        ("final structure", config.inputs.final_structure),
        ("top boundary IDs", config.inputs.top_boundary_ids),
        ("bottom boundary IDs", config.inputs.bottom_boundary_ids),
        ("shear config", config.inputs.shear_config),
    ):
        if source is None:
            continue
        path = (root / source.path).resolve()
        if root not in path.parents or not path.is_file():
            raise FileNotFoundError(f"{label} not found: {source.path}")
        if path.stat().st_size != source.size_bytes or sha256_file(path) != source.sha256:
            raise ValueError(f"{label} changed after NEB configuration: {source.path}")
    potential_path = current_potential_path(config.potential.file)
    if not potential_path.is_absolute():
        potential_path = (root / potential_path).resolve()
    if not potential_path.is_file():
        raise FileNotFoundError(f"potential file not found: {potential_path}")
    if sha256_file(potential_path) != config.potential.sha256:
        raise ValueError("potential file changed after NEB configuration")


def _register_neb_artifacts(
    service: RunService,
    run_id: str,
    output_dir: Path,
) -> None:
    run = service.memory.get_run(run_id)
    common_metadata = {
        "constraint_mode": run.parameters.get("constraint_mode", "shear_boundaries"),
        "purpose": run.parameters.get("purpose", run.parameters.get("description", "")),
        "endpoints": run.parameters.get("endpoints", {}),
    }
    static_files = (
        ("initial_structure", "inputs/initial_source.cfg", ProvenanceClass.PRIMARY),
        ("initial_structure", "inputs/initial_source.lmp", ProvenanceClass.PRIMARY),
        ("final_structure", "inputs/final_source.cfg", ProvenanceClass.PRIMARY),
        ("final_structure", "inputs/final_source.lmp", ProvenanceClass.PRIMARY),
        ("prepared_initial_lmp", "inputs/initial.lmp", ProvenanceClass.DERIVED),
        ("prepared_final_lmp", "inputs/final.lmp", ProvenanceClass.DERIVED),
        ("top_boundary_ids", "inputs/top_boundary.ids", ProvenanceClass.PRIMARY),
        ("bottom_boundary_ids", "inputs/bottom_boundary.ids", ProvenanceClass.PRIMARY),
        ("shear_config", "inputs/shear_input.json", ProvenanceClass.PRIMARY),
        ("neb_final_coordinates", "inputs/final.neb.coords", ProvenanceClass.DERIVED),
        ("log", NEB_MASTER_LOG, ProvenanceClass.PRIMARY),
        ("mep_csv", NEB_MEP_CSV, ProvenanceClass.DERIVED),
        ("mep_json", NEB_MEP_JSON, ProvenanceClass.DERIVED),
    )
    for role, relative, provenance in static_files:
        if (output_dir / relative).is_file():
            metadata = dict(common_metadata)
            endpoint_name = (
                "initial" if role in {"initial_structure", "prepared_initial_lmp"}
                else "final" if role in {"final_structure", "prepared_final_lmp"} else None
            )
            if endpoint_name:
                identity = common_metadata["endpoints"].get(endpoint_name) or {}
                metadata.update(source=identity, origin=identity.get("origin", {}))
            service.register_artifact(run_id, role, relative, provenance, metadata=metadata)
    for path in sorted(output_dir.glob("log.replica*")):
        if path.is_file():
            service.register_artifact(
                run_id,
                "replica_log",
                path.name,
                ProvenanceClass.PRIMARY,
            )
    for pattern, role in (
        ("neb.replica.*.lammpstrj", "replica_trajectory"),
        ("neb.replica.*.final.lmp", "replica_final_structure"),
    ):
        for path in sorted(output_dir.glob(pattern)):
            if not path.is_file():
                continue
            match = re.search(r"neb\.replica\.(\d+)\.", path.name)
            service.register_artifact(
                run_id,
                role,
                path.name,
                ProvenanceClass.DERIVED,
                metadata={**common_metadata, "partition": int(match.group(1)) if match else None},
            )


def _tool_result(service: RunService, run) -> RunToolResult:
    return run_tool_result(service, run, artifact_fields=("role", "path", "metadata"))


@tool
def run_gb_neb(run_id: str) -> str:
    """Run one configured GB-NEB workflow and extract CSV/JSON MEP artifacts.

    The MPI size is exactly replicas*ranks_per_replica from the canonical config.
    The intact global communicator is passed to LAMMPS, which creates replica-local
    communicators via -partition. This runner accepts no independent np override.
    """
    project_id = runtime_project_id()
    if not project_id:
        return "No active project_id in run config."
    store = ProjectStore()
    project_root = store.project_path(project_id)
    service = store.runs(project_id)
    try:
        run = configured_run(service, run_id, Workflow.GB_NEB)
        config_path = project_root / str(run.config_path)
        cfg = load_gb_neb_config(config_path)
        recorded = run.parameters.get("canonical_config")
        if recorded is not None and gb_neb_config_to_dict(cfg) != gb_neb_config_to_dict(build_gb_neb_config(recorded)):
            raise ValueError("NEB configuration changed after the run was configured")
        if cfg.neb.total_mpi_ranks > LAMMPS_TOTAL_MPI_RANKS:
            raise ValueError(
                f"NEB requires {cfg.neb.total_mpi_ranks} MPI ranks, exceeding "
                f"GBCLAW_LAMMPS_TOTAL_MPI_RANKS={LAMMPS_TOTAL_MPI_RANKS}"
            )
        _validate_sources(cfg, project_root)
    except (FileNotFoundError, OSError, ValueError) as exc:
        return str(exc)

    output_dir = service.artifacts.run_directory(run_id)
    service.start_run(
        run_id,
        progress={
            "stage": "running_neb",
            "replicas": cfg.neb.replicas,
            "ranks_per_replica": cfg.neb.ranks_per_replica,
            "total_mpi_ranks": cfg.neb.total_mpi_ranks,
        },
    )
    argv = mpirun_argv(GB_NEB_SCRIPT, cfg.neb.total_mpi_ranks, "--output-dir", str(output_dir.resolve()),
                       "--config", str(config_path.resolve()), "--project-root", str(project_root.resolve()))
    try:
        command = launch(argv, ranks=cfg.neb.total_mpi_ranks, timeout_sec=GB_NEB_TIMEOUT_SEC,
                         session_key=runtime_session_key())
    except Exception as exc:
        _register_neb_artifacts(service, run_id, output_dir)
        terminal = service.fail_run(
            run_id,
            error=str(exc),
            result={
                "replicas": cfg.neb.replicas,
                "ranks_per_replica": cfg.neb.ranks_per_replica,
                "total_mpi_ranks": cfg.neb.total_mpi_ranks,
                "constraint_mode": cfg.constraints.mode,
                "endpoints": run.parameters.get("endpoints", {}),
                "purpose": run.parameters.get("purpose", ""),
            },
            progress={"stage": "failed"},
        )
        return _tool_result(service, terminal).to_json()

    save_launcher_logs(output_dir, command)
    register_launcher_logs(service, run_id, output_dir)
    _register_neb_artifacts(service, run_id, output_dir)
    terminal_status, error = command_status(
        command, output_dir, (NEB_MASTER_LOG, NEB_MEP_CSV, NEB_MEP_JSON), label="gb_neb",
    )

    result: dict[str, Any] = {
        "replicas": cfg.neb.replicas,
        "ranks_per_replica": cfg.neb.ranks_per_replica,
        "total_mpi_ranks": cfg.neb.total_mpi_ranks,
        "source_fingerprint": run.source_fingerprint,
        "constraint_mode": cfg.constraints.mode,
        "endpoints": run.parameters.get("endpoints", {}),
        "purpose": run.parameters.get("purpose", ""),
    }
    mep_path = output_dir / NEB_MEP_JSON
    if mep_path.is_file():
        try:
            mep = json.loads(mep_path.read_text(encoding="utf-8"))
            if not isinstance(mep, dict):
                raise ValueError("mep.json root must be an object")
            profile = mep.get("profile") or []
            if not isinstance(profile, list) or not profile:
                raise ValueError("mep.json profile must be a non-empty list")
            result["profile_points"] = len(profile)
            result["initial_energy_eV"] = profile[0].get("potential_energy_eV")
            result["final_energy_eV"] = profile[-1].get("potential_energy_eV")
            result["final_relative_energy_eV"] = profile[-1].get(
                "relative_energy_eV"
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            if terminal_status is RunStatus.COMPLETED:
                terminal_status = RunStatus.FAILED
                error = "mep.json is not a valid agent-readable JSON object"

    result["summary"] = (
        f"{result['purpose'] or 'NEB between selected endpoints'}; "
        f"constraints={cfg.constraints.mode}; {cfg.neb.replicas} replicas; "
        f"initial={cfg.inputs.initial_structure.path}; final={cfg.inputs.final_structure.path}; "
        f"status={terminal_status.value}"
    )
    stage = {RunStatus.COMPLETED: "finished", RunStatus.CANCELLED: "cancelled"}.get(terminal_status, "failed")
    if terminal_status is RunStatus.FAILED:
        error = error or "GB NEB failed"
    terminal = finish_run(
        service, run_id, terminal_status, result=result, progress={"stage": stage}, error=error,
        memory_tool="run_gb_neb", cancelled_message="GB NEB cancelled",
    )
    return _tool_result(service, terminal).to_json()
