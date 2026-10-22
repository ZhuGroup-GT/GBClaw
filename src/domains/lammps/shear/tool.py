"""Agent tools for structured grain-boundary shear runs."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from domains.lammps.common.potential_files import current_potential_path
from core.util.graph_config import runtime_project_id
from domains.lammps.common.artifacts import (
    SHEAR_BOTTOM_BOUNDARY_IDS,
    SHEAR_CONFIG_USED,
    SHEAR_INPUTS_DIR,
    SHEAR_SOURCE_LMP,
    SHEAR_TOP_BOUNDARY_IDS,
    SHEARED_CFG,
    SHEARED_LMP,
    SHEAR_CURVE,
    SHEAR_RUN_METADATA,
)
from domains.lammps.common.defaults import (
    SHEAR_DUMP_EVERY,
    SHEAR_FINE_STEPS,
    SHEAR_FINE_STRAIN_INCREMENT,
    SHEAR_HALF_CYCLES,
    SHEAR_INITIAL_STEPS,
    SHEAR_INITIAL_STRAIN_INCREMENT,
    SHEAR_LOADING_DIRECTION,
    SHEAR_LOAD_TYPE,
    SHEAR_MIN_ETOL,
    SHEAR_MIN_FTOL,
    SHEAR_MIN_MAXEVAL,
    SHEAR_MIN_MAXITER,
    SHEAR_MIN_STYLE,
    SHEAR_SPRING_PER_ATOM,
    SHEAR_STEPS_PER_HALF_CYCLE,
    SHEAR_THERMO_EVERY,
)
from domains.lammps.common.settings import (
    GB_SHEAR_SCRIPT,
    GB_SHEAR_TIMEOUT_SEC,
)
from project_workspace.models import utc_now_iso
from project_memory.publication.service import RunService
from project_workspace.artifacts.models import ProvenanceClass
from project_memory.models import RunStatus, Workflow
from project_workspace.store import ProjectStore
from domains.lammps.minimize.config import load_gb_minimize_config
from domains.lammps.shear.config import (
    GBShearConfig,
    build_gb_shear_config,
    config_to_dict as gb_shear_config_to_dict,
    load_gb_shear_config,
    target_loading_state,
)
from core.execution.sessions import runtime_session_key
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
from project_memory.publication.results import RunToolResult


@dataclass(frozen=True)
class ResolvedGBShearSource:
    source_run_id: str
    source_workflow: str
    source_fingerprint: str
    output_dir: str
    data: str
    top_boundary_ids: str
    bottom_boundary_ids: str
    data_path: Path
    top_boundary_ids_path: Path
    bottom_boundary_ids_path: Path
    initial_cumulative_shear_strain: float
    element: str
    mass: float
    boundary: str
    pair_style: str
    potential_file: str


@dataclass
class _ShearMetadataSyncState:
    stat_signature: tuple[int, int, int] | None = None
    content_digest: bytes | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    registered_frame_paths: set[str] = field(default_factory=set)


_SHEAR_STRESS_FIELDS = (
    "top_shear_stress_mpa",
    "bottom_shear_stress_mpa",
    "sigma_xx_mpa",
    "sigma_yy_mpa",
    "sigma_zz_mpa",
    "sigma_xy_mpa",
    "sigma_xz_mpa",
    "sigma_yz_mpa",
)


def resolve_gb_shear_source(
    project_root: Path,
    source_run_id: str | None,
) -> ResolvedGBShearSource:
    """Resolve a completed minimization or a completed shear final state."""
    project_root = project_root.resolve()
    service = RunService(project_root)
    if source_run_id:
        source_run = service.memory.get_run(source_run_id)
    else:
        candidates = service.memory.query_runs(
            workflow=Workflow.GB_MINIMIZE, status=RunStatus.COMPLETED, limit=1
        )
        if not candidates:
            raise ValueError("No completed gb_minimize run is available")
        source_run = candidates[0]
    if source_run.status is not RunStatus.COMPLETED or source_run.workflow not in {
        Workflow.GB_MINIMIZE,
        Workflow.GB_SHEAR,
    }:
        raise ValueError(
            "source_run_id must identify a completed gb_minimize or gb_shear run"
        )
    data_role = (
        "minimized_lmp"
        if source_run.workflow is Workflow.GB_MINIMIZE
        else "sheared_lmp"
    )
    indexed = {
        role: service.memory.query_artifacts(run_id=source_run.run_id, role=role)
        for role in ("config", data_role, "top_boundary_ids", "bottom_boundary_ids")
    }
    missing = [role for role, items in indexed.items() if not items]
    if missing:
        raise ValueError(
            f"completed {source_run.workflow.value} run is missing artifacts: "
            + ", ".join(missing)
        )
    paths = {role: project_root / items[0].path for role, items in indexed.items()}
    for role, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"source artifact {role} not found: {path}")
    config_path = paths["config"]
    if source_run.workflow is Workflow.GB_MINIMIZE:
        parent_config = load_gb_minimize_config(config_path)
        initial_cumulative_shear_strain = 0.0
        element = parent_config.material.element
        mass = parent_config.material.mass
        boundary = parent_config.simulation.boundary
        pair_style = parent_config.potential.pair_style
        potential_path = parent_config.potential.resolve_path(
            config_dir=config_path.parent
        )
    else:
        parent_config = load_gb_shear_config(config_path)
        try:
            initial_cumulative_shear_strain = float(
                source_run.result["final_actual_shear_strain"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"completed shear source {source_run.run_id} has no finite final strain"
            ) from exc
        if not math.isfinite(initial_cumulative_shear_strain):
            raise ValueError(
                f"completed shear source {source_run.run_id} has no finite final strain"
            )
        element = parent_config.material.element
        mass = parent_config.material.mass
        boundary = parent_config.simulation.boundary
        pair_style = parent_config.potential.pair_style
        potential_path = current_potential_path(parent_config.potential.file)
        if not potential_path.is_absolute():
            potential_path = project_root / potential_path

    output_dir = service.artifacts.run_directory(source_run.run_id)
    output_rel = output_dir.relative_to(project_root).as_posix()
    potential_path = potential_path.resolve()
    if not potential_path.is_file():
        raise FileNotFoundError(f"Potential file not found: {potential_path}")
    digest = hashlib.sha256()
    for role in (data_role, "top_boundary_ids", "bottom_boundary_ids"):
        digest.update(role.encode("utf-8"))
        digest.update(b"\0")
        digest.update(paths[role].read_bytes())
        digest.update(b"\0")
    return ResolvedGBShearSource(
        source_run_id=source_run.run_id,
        source_workflow=source_run.workflow.value,
        source_fingerprint=f"sha256:{digest.hexdigest()}",
        output_dir=output_rel,
        data=paths[data_role].relative_to(output_dir).as_posix(),
        top_boundary_ids=paths["top_boundary_ids"].relative_to(output_dir).as_posix(),
        bottom_boundary_ids=paths["bottom_boundary_ids"].relative_to(output_dir).as_posix(),
        data_path=paths[data_role],
        top_boundary_ids_path=paths["top_boundary_ids"],
        bottom_boundary_ids_path=paths["bottom_boundary_ids"],
        initial_cumulative_shear_strain=initial_cumulative_shear_strain,
        element=element,
        mass=mass,
        boundary=boundary,
        pair_style=pair_style,
        potential_file=str(potential_path),
    )


def validate_gb_shear_source_snapshot(
    config: GBShearConfig,
    source: ResolvedGBShearSource,
    *,
    expected_fingerprint: str | None,
    project_root: Path,
) -> None:
    """Reject a configured shear run whose inherited source physics changed."""
    mismatches: list[str] = []
    for label, actual, expected in (
        ("source.output_dir", config.source.output_dir, source.output_dir),
        ("source.parent_run_id", config.source.parent_run_id, source.source_run_id),
        ("source.parent_workflow", config.source.parent_workflow, source.source_workflow),
        ("source.data", config.source.data, source.data),
        (
            "source.top_boundary_ids",
            config.source.top_boundary_ids,
            source.top_boundary_ids,
        ),
        (
            "source.bottom_boundary_ids",
            config.source.bottom_boundary_ids,
            source.bottom_boundary_ids,
        ),
        ("material.element", config.material.element, source.element),
        ("simulation.boundary", config.simulation.boundary, source.boundary),
        ("potential.pair_style", config.potential.pair_style, source.pair_style),
    ):
        if actual != expected:
            mismatches.append(f"{label}={actual!r}, expected {expected!r}")
    if not math.isclose(
        config.material.mass,
        source.mass,
        rel_tol=1.0e-12,
        abs_tol=0.0,
    ):
        mismatches.append(
            f"material.mass={config.material.mass!r}, expected {source.mass!r}"
        )
    if not math.isclose(
        config.source.initial_cumulative_shear_strain,
        source.initial_cumulative_shear_strain,
        rel_tol=0.0,
        abs_tol=1.0e-12,
    ):
        mismatches.append(
            "source.initial_cumulative_shear_strain="
            f"{config.source.initial_cumulative_shear_strain!r}, expected "
            f"{source.initial_cumulative_shear_strain!r}"
        )

    configured_potential = current_potential_path(config.potential.file)
    if not configured_potential.is_absolute():
        configured_potential = project_root / configured_potential
    if configured_potential.resolve() != current_potential_path(source.potential_file).resolve():
        mismatches.append(
            f"potential.file={config.potential.file!r}, "
            f"expected {source.potential_file!r}"
        )
    if expected_fingerprint != source.source_fingerprint:
        mismatches.append(
            f"source_fingerprint={expected_fingerprint!r}, "
            f"expected {source.source_fingerprint!r}"
        )
    if mismatches:
        raise ValueError(
            "configured gb_shear source no longer matches its minimization: "
            + "; ".join(mismatches)
        )


@tool
def write_gb_shear_config(
    source_run_id: str | None = None,
    description: str | None = None,
    load_type: str = SHEAR_LOAD_TYPE,
    loading_direction: str = SHEAR_LOADING_DIRECTION,
    initial_strain_increment: float = SHEAR_INITIAL_STRAIN_INCREMENT,
    initial_steps: int = SHEAR_INITIAL_STEPS,
    fine_strain_increment: float = SHEAR_FINE_STRAIN_INCREMENT,
    fine_steps: int = SHEAR_FINE_STEPS,
    steps_per_half_cycle: int | None = SHEAR_STEPS_PER_HALF_CYCLE,
    half_cycles: int = SHEAR_HALF_CYCLES,
    top_spring_per_atom: float = SHEAR_SPRING_PER_ATOM,
    bottom_spring_per_atom: float = SHEAR_SPRING_PER_ATOM,
    min_style: str = SHEAR_MIN_STYLE,
    min_etol: float = SHEAR_MIN_ETOL,
    min_ftol: float = SHEAR_MIN_FTOL,
    min_maxiter: int = SHEAR_MIN_MAXITER,
    min_maxeval: int = SHEAR_MIN_MAXEVAL,
    dump_every: int = SHEAR_DUMP_EVERY,
    thermo_every: int = SHEAR_THERMO_EVERY,
    label: str | None = None,
) -> str:
    """Write a validated unidirectional or cyclic x-axis gb_shear JSON config.

    source_run_id names a completed gb_minimize run or the final state of a
    completed gb_shear run. Omit it to use the latest minimization.
    Source structure, boundary IDs, element/mass, boundary conditions, pair style,
    and potential file are inherited from that run and cannot be overridden here.
    load_type accepts displacement or spring_force. Loading, minimization, and
    output controls are typed parameters whose defaults are exposed by the tool
    schema. min_style accepts cg or fire. loading_direction accepts x or -x and
    sets the first loading direction. A null steps_per_half_cycle keeps the run
    unidirectional; a positive value reverses direction after that many loading
    steps. half_cycles declares how many half-cycles the caller intends. The
    writer returns a warning, without silently adding steps, when the configured
    total is smaller than steps_per_half_cycle*half_cycles. Strain increments
    are non-negative magnitudes.
    In spring_force mode, spring-per-atom values use eV/Angstrom^2/atom and the
    driver multiplies them by the boundary atom count. In displacement mode,
    both values are canonicalized to null. The complete canonical config is
    searchable by run.
    """
    project_id = runtime_project_id()
    if not project_id:
        return "No active project_id in run config."
    project_root = ProjectStore().project_path(project_id)
    try:
        source = resolve_gb_shear_source(project_root, source_run_id)
        normalized_load_type = str(load_type).strip().lower()
        normalized_direction = str(loading_direction).strip().lower()
        payload: dict[str, Any] = {
            "version": 1,
            "description": description or "",
            "source": {
                "output_dir": source.output_dir,
                "parent_run_id": source.source_run_id,
                "parent_workflow": source.source_workflow,
                "initial_cumulative_shear_strain": (
                    source.initial_cumulative_shear_strain
                ),
                "data": source.data,
                "top_boundary_ids": source.top_boundary_ids,
                "bottom_boundary_ids": source.bottom_boundary_ids,
            },
            "material": {"element": source.element, "mass": source.mass},
            "simulation": {"boundary": source.boundary},
            "potential": {
                "pair_style": source.pair_style,
                "file": source.potential_file,
            },
            "loading": {
                "load_type": normalized_load_type,
                "direction": normalized_direction,
                "initial_strain_increment": abs(
                    float(initial_strain_increment)
                ),
                "initial_steps": initial_steps,
                "fine_strain_increment": abs(float(fine_strain_increment)),
                "fine_steps": fine_steps,
                "steps_per_half_cycle": steps_per_half_cycle,
                "half_cycles": half_cycles,
                "top_spring_per_atom": (
                    None
                    if normalized_load_type == "displacement"
                    else top_spring_per_atom
                ),
                "bottom_spring_per_atom": (
                    None
                    if normalized_load_type == "displacement"
                    else bottom_spring_per_atom
                ),
            },
            "minimization": {
                "style": min_style,
                "etol": min_etol,
                "ftol": min_ftol,
                "maxiter": min_maxiter,
                "maxeval": min_maxeval,
            },
            "output": {
                "dump_every": dump_every,
                "thermo_every": thermo_every,
            },
        }
        cfg = build_gb_shear_config(payload)
        canonical_payload = gb_shear_config_to_dict(cfg)
        service = ProjectStore().runs(project_id)
        final_loading_state = target_loading_state(
            cfg.loading.total_steps,
            cfg.loading,
            slab_spacing=1.0,
        )
        parameters = {
            "load_type": cfg.loading.load_type,
            "initial_direction": cfg.loading.direction,
            "steps_per_half_cycle": cfg.loading.steps_per_half_cycle,
            "half_cycles": cfg.loading.half_cycles,
            "cyclic": cfg.loading.cyclic,
            "planned_loading_half_cycles": cfg.loading.loading_half_cycles,
            "planned_loading_cycles": cfg.loading.loading_cycles,
            "normal_direction": "y",
            "strain_reference": "current_slab_spacing",
            "initial_strain_increment": cfg.loading.initial_strain_increment,
            "initial_steps": cfg.loading.initial_steps,
            "fine_strain_increment": cfg.loading.fine_strain_increment,
            "fine_steps": cfg.loading.fine_steps,
            "total_steps": cfg.loading.total_steps,
            "initial_cumulative_shear_strain": (
                cfg.source.initial_cumulative_shear_strain
            ),
            "source_workflow": cfg.source.parent_workflow,
            "target_final_strain": (
                cfg.source.initial_cumulative_shear_strain
                + final_loading_state.target_strain
            ),
            "protocol_warnings": list(cfg.loading.protocol_warnings),
            "top_spring_per_atom": cfg.loading.top_spring_per_atom,
            "bottom_spring_per_atom": cfg.loading.bottom_spring_per_atom,
            "spring_per_atom_unit": (
                "eV/Angstrom^2/atom"
                if cfg.loading.load_type == "spring_force"
                else None
            ),
            "description": cfg.description,
            "element": cfg.material.element,
            "label": (label or "").strip(),
            # Searchable mirror only. input.json remains the authoritative config.
            "canonical_config": canonical_payload,
        }
        run = service.create_configured_run(
            workflow=Workflow.GB_SHEAR,
            input_payload=canonical_payload,
            parameters={**parameters, "memory_tool": "run_gb_shear"},
            source_run_id=source.source_run_id,
            source_fingerprint=source.source_fingerprint,
        )
    except (FileNotFoundError, ValueError, FileExistsError) as exc:
        return str(exc)
    return RunToolResult(
        run_id=run.run_id, workflow=run.workflow, status=run.status,
        manifest=run.manifest_path,
        artifacts=[{"role": "config", "path": run.config_path}],
        result={
            "source_run_id": source.source_run_id,
            "source_workflow": source.source_workflow,
            "source_fingerprint": source.source_fingerprint,
            "initial_cumulative_shear_strain": (
                source.initial_cumulative_shear_strain
            ),
            "warnings": list(cfg.loading.protocol_warnings),
        },
    ).to_json()


def _finalize_output_metadata(
    output_dir: Path,
    *,
    status: str,
    planned_loading_steps: int,
    parent_run_id: str,
    parent_workflow: str,
    load_type: str,
    initial_loading_direction: str,
    steps_per_half_cycle: int | None,
    half_cycles: int,
    initial_cumulative_shear_strain: float,
    protocol_warnings: tuple[str, ...],
    error: str = "",
) -> None:
    from domains.lammps.shear.engine import finalize_run_metadata, write_run_metadata_atomic

    path = output_dir / SHEAR_RUN_METADATA
    if path.is_file():
        metadata = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise ValueError(f"{path} must contain a JSON object")
        version = metadata.get("version")
        if version != 1:
            raise ValueError(
                f"unsupported shear metadata version {version!r} in {path}; "
                "expected version 1"
            )
    else:
        metadata = {}
    if not metadata:
        timestamp = utc_now_iso()
        scheduled_half_cycles = (
            1
            if steps_per_half_cycle is None
            else (
                planned_loading_steps + steps_per_half_cycle - 1
            )
            // steps_per_half_cycle
        )
        metadata = {
            "version": 1,
            "status": "running",
            "started_at": timestamp,
            "updated_at": timestamp,
            "finished_at": None,
            "error": "",
            "loading": {
                "load_type": load_type,
                "initial_direction": initial_loading_direction,
                "steps_per_half_cycle": steps_per_half_cycle,
                "requested_half_cycles": half_cycles,
                "cyclic": steps_per_half_cycle is not None,
                "planned_loading_half_cycles": scheduled_half_cycles,
                "planned_loading_cycles": (scheduled_half_cycles + 1) // 2,
                "normal_direction": "y",
                "control_mode": (
                    "spring_tether"
                    if load_type == "spring_force"
                    else "direct_displacement"
                ),
                "boundary_stress_source": (
                    "spring_reaction"
                    if load_type == "spring_force"
                    else "sigma_xy_top_and_negative_bottom"
                ),
                "y_constraint": "fixed_per_atom",
                "strain_reference": "current_slab_spacing",
            },
            "lineage": {
                "parent_run_id": parent_run_id,
                "parent_workflow": parent_workflow,
                "initial_cumulative_shear_strain": (
                    initial_cumulative_shear_strain
                ),
            },
            "warnings": list(protocol_warnings),
            "reference": None,
            "progress": {
                "planned_loading_steps": planned_loading_steps,
                "planned_points": planned_loading_steps + 1,
                "last_completed_step": -1,
                "completed_points": 0,
                "nonconverged_points": 0,
            },
            "summary": {
                "peak_abs_shear_stress_mpa": None,
                "stress_at_peak_mpa": None,
                "strain_at_peak": None,
                "step_at_peak": None,
                "loading_direction_at_peak": None,
                "loading_half_cycle_at_peak": None,
                "loading_cycle_at_peak": None,
            },
            "current": None,
            "points": [],
            "frames": [],
        }
    finalize_run_metadata(metadata, status=status, error=error)
    write_run_metadata_atomic(path, metadata)


@tool
def run_gb_shear(
    run_id: str,
    np: int | None = None,
) -> str:
    """Execute a configured GB-shear workflow identified by its stable run ID."""
    project_id = runtime_project_id()
    if not project_id:
        return "No active project_id in run config."
    store = ProjectStore()
    project_root = store.project_path(project_id)
    service = store.runs(project_id)
    try:
        run = configured_run(service, run_id, Workflow.GB_SHEAR)
        config_abs = project_root / str(run.config_path)
        cfg = load_gb_shear_config(config_abs)
        source = resolve_gb_shear_source(project_root, run.source_run_id)
        validate_gb_shear_source_snapshot(
            cfg,
            source,
            expected_fingerprint=run.source_fingerprint,
            project_root=project_root,
        )
        output_dir = service.artifacts.run_directory(run_id)
        input_dir = output_dir / SHEAR_INPUTS_DIR
        input_dir.mkdir(parents=True, exist_ok=True)
        source_lmp = input_dir / SHEAR_SOURCE_LMP
        top_ids_snapshot = input_dir / SHEAR_TOP_BOUNDARY_IDS
        bottom_ids_snapshot = input_dir / SHEAR_BOTTOM_BOUNDARY_IDS
        shutil.copy2(source.data_path, source_lmp)
        shutil.copy2(source.top_boundary_ids_path, top_ids_snapshot)
        shutil.copy2(source.bottom_boundary_ids_path, bottom_ids_snapshot)

        runtime_payload = gb_shear_config_to_dict(cfg)
        runtime_payload["source"].update(
            {
                "output_dir": output_dir.relative_to(project_root).as_posix(),
                "data": f"{SHEAR_INPUTS_DIR}/{SHEAR_SOURCE_LMP}",
                "top_boundary_ids": (
                    f"{SHEAR_INPUTS_DIR}/{SHEAR_TOP_BOUNDARY_IDS}"
                ),
                "bottom_boundary_ids": (
                    f"{SHEAR_INPUTS_DIR}/{SHEAR_BOTTOM_BOUNDARY_IDS}"
                ),
            }
        )
        config_abs = output_dir / SHEAR_CONFIG_USED
        config_abs.write_text(
            json.dumps(runtime_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        for role, relative in (
            ("source_lmp", f"{SHEAR_INPUTS_DIR}/{SHEAR_SOURCE_LMP}"),
            (
                "top_boundary_ids",
                f"{SHEAR_INPUTS_DIR}/{SHEAR_TOP_BOUNDARY_IDS}",
            ),
            (
                "bottom_boundary_ids",
                f"{SHEAR_INPUTS_DIR}/{SHEAR_BOTTOM_BOUNDARY_IDS}",
            ),
            ("config_used", SHEAR_CONFIG_USED),
        ):
            service.register_artifact(
                run_id,
                role,
                relative,
                ProvenanceClass.PRIMARY,
                metadata={
                    "parent_run_id": source.source_run_id,
                    "parent_workflow": source.source_workflow,
                    "source_fingerprint": source.source_fingerprint,
                },
            )
        resolved_np = resolve_mpi_ranks(np)
    except (OSError, RuntimeError, ValueError) as exc:
        return str(exc)
    service.start_run(
        run_id,
        progress={
            "planned_loading_steps": cfg.loading.total_steps,
            "initial_loading_direction": cfg.loading.direction,
            "steps_per_half_cycle": cfg.loading.steps_per_half_cycle,
            "half_cycles": cfg.loading.half_cycles,
            "planned_loading_half_cycles": cfg.loading.loading_half_cycles,
            "planned_loading_cycles": cfg.loading.loading_cycles,
            "last_completed_step": -1,
            "completed_points": 0,
            "nonconverged_points": 0,
            "protocol_warnings": list(cfg.loading.protocol_warnings),
        },
    )
    argv = mpirun_argv(GB_SHEAR_SCRIPT, resolved_np, "--output-dir", str(output_dir.resolve()),
                       "--config", str(config_abs.resolve()), "--project-root", str(project_root.resolve()))
    stop_monitor = threading.Event()
    sync_state = _ShearMetadataSyncState()

    def monitor() -> None:
        while not stop_monitor.wait(0.2):
            try:
                _sync_shear_metadata(
                    service,
                    run_id,
                    output_dir,
                    sync_state=sync_state,
                )
            except Exception:
                continue

    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()
    try:
        command = launch(argv, ranks=resolved_np, timeout_sec=GB_SHEAR_TIMEOUT_SEC,
                         session_key=runtime_session_key())
    except Exception as exc:
        stop_monitor.set()
        monitor_thread.join()
        _finalize_output_metadata(
            output_dir,
            status="failed",
            planned_loading_steps=cfg.loading.total_steps,
            parent_run_id=cfg.source.parent_run_id,
            parent_workflow=cfg.source.parent_workflow,
            load_type=cfg.loading.load_type,
            initial_loading_direction=cfg.loading.direction,
            steps_per_half_cycle=cfg.loading.steps_per_half_cycle,
            half_cycles=cfg.loading.half_cycles,
            initial_cumulative_shear_strain=(
                cfg.source.initial_cumulative_shear_strain
            ),
            protocol_warnings=cfg.loading.protocol_warnings,
            error=str(exc),
        )
        metadata = _sync_shear_metadata(
            service,
            run_id,
            output_dir,
            sync_state=sync_state,
        )
        _register_shear_files(service, run_id, output_dir, metadata=metadata)
        partial = _build_shear_result(run, metadata, complete=False)
        terminal = service.fail_run(
            run_id,
            error=str(exc),
            result=partial,
            progress=_semantic_progress(metadata),
        )
        return _shear_tool_result(service, terminal).to_json()
    finally:
        stop_monitor.set()
        monitor_thread.join()

    save_launcher_logs(output_dir, command)
    register_launcher_logs(service, run_id, output_dir)
    status, error = command_status(
        command, output_dir, (SHEAR_CURVE, SHEARED_CFG, SHEARED_LMP, SHEAR_RUN_METADATA),
        label="gb_shear",
    )
    _finalize_output_metadata(
        output_dir,
        status=status.value,
        planned_loading_steps=cfg.loading.total_steps,
        parent_run_id=cfg.source.parent_run_id,
        parent_workflow=cfg.source.parent_workflow,
        load_type=cfg.loading.load_type,
        initial_loading_direction=cfg.loading.direction,
        steps_per_half_cycle=cfg.loading.steps_per_half_cycle,
        half_cycles=cfg.loading.half_cycles,
        initial_cumulative_shear_strain=(
            cfg.source.initial_cumulative_shear_strain
        ),
        protocol_warnings=cfg.loading.protocol_warnings,
        error=error if status is not RunStatus.COMPLETED else "",
    )
    metadata = _sync_shear_metadata(
        service,
        run_id,
        output_dir,
        sync_state=sync_state,
    )
    _register_shear_files(service, run_id, output_dir, metadata=metadata)
    result = _build_shear_result(
        run, metadata, complete=status is RunStatus.COMPLETED
    )
    terminal = finish_run(
        service, run_id, status, result=result, progress=_semantic_progress(metadata), error=error,
        memory_tool="run_gb_shear", cancelled_message="GB shear cancelled",
    )
    return _shear_tool_result(service, terminal).to_json()


def _semantic_progress(metadata: dict[str, Any]) -> dict[str, Any]:
    progress = dict(metadata.get("progress") or {})
    loading = metadata.get("loading") or {}
    current = metadata.get("current") or {}
    progress.update(
        {
            "initial_loading_direction": loading.get("initial_direction"),
            "steps_per_half_cycle": loading.get("steps_per_half_cycle"),
            "requested_half_cycles": loading.get("requested_half_cycles"),
            "planned_loading_half_cycles": loading.get(
                "planned_loading_half_cycles"
            ),
            "planned_loading_cycles": loading.get("planned_loading_cycles"),
            "last_completed_strain": current.get("actual_shear_strain"),
            "current_loading_direction": current.get("loading_direction"),
            "current_loading_half_cycle": current.get("loading_half_cycle"),
            "current_loading_cycle": current.get("loading_cycle"),
            "last_step_converged": current.get("converged"),
            "last_completed_point": dict(current),
            "current_slab_spacing_angstrom": current.get(
                "current_slab_spacing_angstrom"
            ),
            "current_stress_state_mpa": _shear_stress_state(current),
            "running_summary": dict(metadata.get("summary") or {}),
            "metadata_status": metadata.get("status", "running"),
            "warnings": list(metadata.get("warnings") or []),
        }
    )
    return progress


def _register_shear_files(
    service: RunService,
    run_id: str,
    output_dir: Path,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    current = (metadata or {}).get("current") or {}
    final_frame = _shear_frame_metadata(current) if isinstance(current, dict) else {}
    for role, filename in (
        ("log", "log.lammps"),
        ("sheared_cfg", SHEARED_CFG),
        ("sheared_lmp", SHEARED_LMP),
    ):
        if (output_dir / filename).is_file():
            service.register_artifact(
                run_id, role, filename, ProvenanceClass.PRIMARY,
                metadata=final_frame if role in {"sheared_cfg", "sheared_lmp"} else None,
            )


def _shear_frame_metadata(point: dict[str, Any]) -> dict[str, Any]:
    """Keep exact loading-point provenance on every reusable shear structure."""
    fields = (
        "step", "increment_stage", "loading_direction", "loading_half_cycle",
        "loading_cycle", "step_in_loading_half_cycle", "actual_shear_strain",
        "relative_shear_strain", "target_shear_strain", "relative_target_shear_strain",
        "converged", *_SHEAR_STRESS_FIELDS,
    )
    result = {name: point[name] for name in fields if name in point}
    if "actual_shear_strain" in point:
        result["strain"] = point["actual_shear_strain"]
    return result


def _sync_shear_metadata(
    service: RunService,
    run_id: str,
    output_dir: Path,
    *,
    sync_state: _ShearMetadataSyncState | None = None,
) -> dict[str, Any]:
    metadata_path = output_dir / SHEAR_RUN_METADATA
    if not metadata_path.is_file():
        return dict(sync_state.metadata) if sync_state is not None else {}
    try:
        stat = metadata_path.stat()
        stat_signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
        if (
            sync_state is not None
            and sync_state.stat_signature == stat_signature
        ):
            return dict(sync_state.metadata)
        content = metadata_path.read_bytes()
        content_digest = hashlib.sha256(content).digest()
        if (
            sync_state is not None
            and sync_state.content_digest == content_digest
        ):
            sync_state.stat_signature = stat_signature
            return dict(sync_state.metadata)
        metadata = json.loads(content)
        if not isinstance(metadata, dict):
            raise ValueError(f"{metadata_path} must contain a JSON object")
        version = metadata.get("version")
        if version != 1:
            raise ValueError(
                f"unsupported shear metadata version {version!r} in "
                f"{metadata_path}; expected version 1"
            )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return dict(sync_state.metadata) if sync_state is not None else {}
    service.register_artifact(
        run_id, "run_metadata", SHEAR_RUN_METADATA, ProvenanceClass.PRIMARY,
        media_type="application/json",
    )
    points_by_step = {
        int(point["step"]): point
        for point in metadata.get("points") or []
        if isinstance(point, dict) and "step" in point
    }
    for frame in metadata.get("frames") or []:
        relative = str(frame.get("path") or "")
        if not relative or not (output_dir / relative).is_file():
            continue
        if (
            sync_state is not None
            and relative in sync_state.registered_frame_paths
        ):
            continue
        try:
            point = points_by_step[int(frame["step"])]
        except (KeyError, TypeError, ValueError):
            continue
        service.register_artifact(
            run_id,
            "intermediate_structure",
            relative,
            ProvenanceClass.DERIVED,
            metadata=_shear_frame_metadata(point),
        )
        if sync_state is not None:
            sync_state.registered_frame_paths.add(relative)
    current_run = service.memory.get_run(run_id)
    if current_run.status is RunStatus.RUNNING:
        service.update_progress(run_id, progress=_semantic_progress(metadata))
    if sync_state is not None:
        sync_state.stat_signature = stat_signature
        sync_state.content_digest = content_digest
        sync_state.metadata = metadata
    return metadata


def _shear_stress_state(current: dict[str, Any]) -> dict[str, Any]:
    return {name: current.get(name) for name in _SHEAR_STRESS_FIELDS}


def _build_shear_result(
    run,
    metadata: dict[str, Any],
    *,
    complete: bool,
) -> dict[str, Any]:
    loading = metadata.get("loading") or {}
    reference = metadata.get("reference") or {}
    current = metadata.get("current") or {}
    progress = metadata.get("progress") or {}
    summary = metadata.get("summary") or {}
    points = int(progress.get("completed_points", 0))
    return {
        "complete": complete,
        "load_type": loading.get("load_type"),
        "initial_loading_direction": loading.get("initial_direction"),
        "steps_per_half_cycle": loading.get("steps_per_half_cycle"),
        "requested_half_cycles": loading.get("requested_half_cycles"),
        "cyclic": loading.get("cyclic"),
        "planned_loading_half_cycles": loading.get(
            "planned_loading_half_cycles"
        ),
        "planned_loading_cycles": loading.get("planned_loading_cycles"),
        "boundary_stress_source": loading.get("boundary_stress_source"),
        "source_run_id": run.source_run_id,
        "source_fingerprint": run.source_fingerprint,
        "parent_run_id": (
            (metadata.get("lineage") or {}).get("parent_run_id")
            or run.source_run_id
        ),
        "parent_workflow": (metadata.get("lineage") or {}).get(
            "parent_workflow"
        ) or run.parameters.get("source_workflow"),
        "initial_cumulative_shear_strain": (
            metadata.get("lineage") or {}
        ).get(
            "initial_cumulative_shear_strain",
            run.parameters.get("initial_cumulative_shear_strain"),
        ),
        "warnings": list(metadata.get("warnings") or []),
        "completed_step_count": points,
        "points": points,
        "loading_steps": max(0, points - 1),
        **summary,
        "final_actual_shear_strain": current.get("actual_shear_strain"),
        "final_loading_direction": current.get("loading_direction"),
        "final_loading_half_cycle": current.get("loading_half_cycle"),
        "final_loading_cycle": current.get("loading_cycle"),
        "final_step": current.get("step"),
        "final_step_converged": current.get("converged"),
        "final_step_data": dict(current),
        "nonconverged_steps": progress.get("nonconverged_points", 0),
        "current_stress_state_mpa": _shear_stress_state(current),
        "top_com_initial": reference.get("top_com_initial"),
        "bottom_com_initial": reference.get("bottom_com_initial"),
        "top_com_current": current.get("top_com"),
        "bottom_com_current": current.get("bottom_com"),
    }


def _shear_tool_result(service: RunService, run) -> RunToolResult:
    return run_tool_result(service, run, artifact_fields=("role", "path", "metadata"))
