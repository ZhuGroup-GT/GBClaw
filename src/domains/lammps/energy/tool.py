"""Agent tools for configuring and running grain-boundary energy calculations."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from domains.lammps.common.log_metrics import extract_minimization_metrics
from domains.lammps.common.geometry import box_size_violations, fractional_repeat_warnings, gb_box_geometry, invalid_geometry_result
from core.util.graph_config import runtime_project_id
from core.util.io import write_json_atomic
from domains.lammps.common.artifacts import (
    ENERGY_BICRYSTAL_INITIAL_CFG,
    ENERGY_BICRYSTAL_INITIAL_LMP,
    ENERGY_BICRYSTAL_LOG,
    ENERGY_BICRYSTAL_MINIMIZED_CFG,
    ENERGY_BICRYSTAL_MINIMIZED_LMP,
    ENERGY_BICRYSTAL_TRAJECTORY,
    ENERGY_BOTTOM_SLAB_CFG,
    ENERGY_REFERENCES_DIR,
    ENERGY_RUN_METADATA,
    ENERGY_SUMMARY_CSV,
    ENERGY_TOP_SLAB_CFG,
)
from domains.lammps.common.settings import (
    GB_ENERGY_SCRIPT,
    GB_ENERGY_TIMEOUT_SEC,
)
from project_workspace.artifacts.models import ProvenanceClass
from project_memory.models import RunStatus, Workflow
from project_workspace.store import ProjectStore
from domains.lammps.energy.config import (
    EnergyOutputConfig,
    GB_ENERGY_TOPOLOGY,
    build_gb_energy_config,
    config_to_dict as gb_energy_config_to_dict,
    load_gb_energy_config,
)
from domains.lammps.minimize.config import (
    MaterialConfig,
    RepeatCount,
    SimulationConfig,
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
from domains.lammps.common.potentials import (
    resolve_potential,
    resolve_potential_file_for_config,
)
from domains.lammps.common.potential_files import potential_file_mass
from project_memory.publication.results import RunToolResult


@tool
def write_gb_energy_config(
    top_x: list[int],
    top_y: list[int],
    top_z: list[int],
    bottom_x: list[int],
    bottom_y: list[int],
    bottom_z: list[int],
    top_size: list[RepeatCount] | None = None,
    bottom_size: list[RepeatCount] | None = None,
    a0: float | None = None,
    lattice: str | None = None,
    element: str | None = None,
    mass: float | None = None,
    pair_style: str | None = None,
    potential_path: str | None = None,
    boundary: str = "p p p",
    y_vacuum: float = 20.0,
    y_eps: float = 1.0e-2,
    x_pbc: bool = True,
    target_x_nm: float | None = None,
    user_confirmed_size_override: bool = False,
    boundary_slab_thickness: float = 3.0,
    enable_initial_staged_relaxation: bool = False,
    min_style: str = "cg",
    min_etol: float = 0.0,
    min_ftol: float = 1.0e-6,
    min_maxiter: int = 1000,
    min_maxeval: int = 10000,
    relax_cycles: int = 4,
    thermo_every: int = 100,
    dump_every: int = 100,
    description: str | None = None,
    label: str | None = None,
) -> str:
    """Validate input and create a configured ``gb_energy`` run.

    The crystallographic/material/minimization arguments match
    ``write_gb_minimize_config``. Call ``estimate_gb_box_size`` first; omitted
    repeat counts are deterministically auto-sized with the same policy.

    This energy method requires ``x_pbc=true`` and ``boundary='p p p'``. It
    calculates one GB plus two y free surfaces, constructs relaxed bulk and
    surface references for both grains, subtracts the two one-surface excesses,
    and divides by the periodic x-z interface area. ``y_vacuum`` must be
    positive. The writer creates only a configured run; call ``run_gb_energy``
    with its returned run ID to execute it.

    Integer repeats remain the agent recommendation. Explicit fractional
    repeats are preserved and warned. Set ``user_confirmed_size_override=true``
    only when the user explicitly supplied/confirmed both size triplets and
    requested execution despite an agent sizing-policy finding.
    """
    project_id = runtime_project_id()
    if not project_id:
        return "No active project_id in run config."
    if user_confirmed_size_override and (top_size is None or bottom_size is None):
        return (
            "user_confirmed_size_override requires explicit top_size and "
            "bottom_size; it cannot override auto-sizing"
        )
    if not x_pbc:
        return (
            "gb_energy requires x_pbc=true because its reference subtraction "
            "does not include x free-surface contributions"
        )
    if y_vacuum <= 0.0:
        return "gb_energy requires y_vacuum > 0"

    material_defaults = MaterialConfig()
    simulation_defaults = SimulationConfig()
    output_defaults = EnergyOutputConfig()
    resolved = resolve_potential(lattice=(lattice or ""), element=(element or ""))
    explicit_pair_style = (pair_style or "").strip()
    explicit_potential_path = (potential_path or "").strip()
    if bool(explicit_pair_style) != bool(explicit_potential_path):
        return (
            "pair_style and potential_path must be provided together, or both "
            "omitted so the catalog can supply them."
        )
    if explicit_pair_style:
        config_pair_style = explicit_pair_style
        config_potential_file = explicit_potential_path
    elif resolved is not None:
        config_pair_style = str(resolved["pair_style"])
        config_potential_file = str(resolved["file"])
    else:
        return (
            "No registered potential matches the requested material. Provide "
            "pair_style and potential_path together."
        )
    if resolved is None:
        missing_material = [
            name
            for name, value in (
                ("lattice", lattice),
                ("element", element),
                ("mass", mass),
                ("a0", a0),
            )
            if value is None or (isinstance(value, str) and not value.strip())
        ]
        if missing_material:
            return (
                "A custom potential without a matching catalog record requires "
                "explicit material parameters: "
                + ", ".join(missing_material)
                + "."
            )

    resolved_lattice = (
        (lattice or "").strip().lower()
        or str((resolved or {}).get("lattice") or material_defaults.lattice)
    )
    resolved_element = (
        (element or "").strip()
        or str((resolved or {}).get("element") or material_defaults.element)
    )
    resolved_mass = (
        float(mass)
        if mass is not None
        else float((resolved or {}).get("mass") or material_defaults.mass)
    )
    # pair_coeff for setfl-family styles replaces the configured mass in LAMMPS.
    mass_warnings: list[str] = []
    file_mass = potential_file_mass(
        resolve_potential_file_for_config(config_potential_file), config_pair_style, resolved_element
    )
    if file_mass is not None:
        if mass is not None and not math.isclose(float(mass), file_mass, rel_tol=1.0e-12):
            mass_warnings.append(
                f"material.mass={float(mass)} was replaced by {file_mass}, the {resolved_element} "
                f"mass in the {config_pair_style} potential file that LAMMPS applies"
            )
        resolved_mass = file_mass
    resolved_a0 = (
        float(a0)
        if a0 is not None
        else float((resolved or {}).get("a0") or simulation_defaults.a0)
    )
    try:
        sizing_geometry = gb_box_geometry(
            top_x=top_x,
            top_y=top_y,
            top_z=top_z,
            top_size=top_size,
            bottom_x=bottom_x,
            bottom_y=bottom_y,
            bottom_z=bottom_z,
            bottom_size=bottom_size,
            a0=resolved_a0,
            lattice=resolved_lattice,
            y_vacuum=y_vacuum,
            y_eps=y_eps,
            x_pbc=True,
            target_x_nm=target_x_nm,
        )
    except ValueError as exc:
        return str(exc)

    payload: dict[str, Any] = {
        "description": description or "",
        "material": {
            "lattice": resolved_lattice,
            "element": resolved_element,
            "mass": resolved_mass,
        },
        "grains": {
            "top": {
                "orientation": {"x": list(top_x), "y": list(top_y), "z": list(top_z)},
                "size": list(sizing_geometry["resolved_top_size"]),
            },
            "bottom": {
                "orientation": {
                    "x": list(bottom_x),
                    "y": list(bottom_y),
                    "z": list(bottom_z),
                },
                "size": list(sizing_geometry["resolved_bottom_size"]),
            },
        },
        "simulation": {
            "a0": resolved_a0,
            "boundary": boundary,
            "y_vacuum": y_vacuum,
            "y_eps": y_eps,
            "x_pbc": True,
            "boundary_slab_thickness": boundary_slab_thickness,
            "enable_initial_staged_relaxation": enable_initial_staged_relaxation,
            "min_style": min_style,
            "min_etol": min_etol,
            "min_ftol": min_ftol,
            "min_maxiter": min_maxiter,
            "min_maxeval": min_maxeval,
            "relax_cycles": relax_cycles,
        },
        "potential": {
            "pair_style": config_pair_style,
            "file": config_potential_file,
        },
        "output": {
            "thermo_every": (
                thermo_every if thermo_every is not None else output_defaults.thermo_every
            ),
            "dump_every": dump_every if dump_every is not None else output_defaults.dump_every,
        },
    }
    try:
        cfg = build_gb_energy_config(payload)
        resolved_potential_path = resolve_potential_file_for_config(cfg.potential.file)
        if not resolved_potential_path.is_file():
            return f"Potential file not found: {resolved_potential_path}"
        payload["potential"]["file"] = str(resolved_potential_path)
        cfg = build_gb_energy_config(payload)

        top_orientation = cfg.grains.top.orientation.as_dict()
        bottom_orientation = cfg.grains.bottom.orientation.as_dict()
        geometry = gb_box_geometry(
            top_x=top_orientation["x"],
            top_y=top_orientation["y"],
            top_z=top_orientation["z"],
            top_size=list(cfg.grains.top.size),
            bottom_x=bottom_orientation["x"],
            bottom_y=bottom_orientation["y"],
            bottom_z=bottom_orientation["z"],
            bottom_size=list(cfg.grains.bottom.size),
            a0=cfg.simulation.a0,
            lattice=cfg.material.lattice,
            y_vacuum=cfg.simulation.y_vacuum,
            y_eps=cfg.simulation.y_eps,
            x_pbc=True,
            target_x_nm=target_x_nm,
        )
        geometry["auto_sized_top"] = top_size is None
        geometry["auto_sized_bottom"] = bottom_size is None
        violations = box_size_violations(geometry)
        if violations and not user_confirmed_size_override:
            return invalid_geometry_result(geometry, violations)
        construction_warnings = [*mass_warnings, *fractional_repeat_warnings(geometry)]
        if violations:
            construction_warnings.extend(
                f"User-confirmed size override: {violation}"
                for violation in violations
            )

        canonical_payload = gb_energy_config_to_dict(cfg)
        service = ProjectStore().runs(project_id)
        run = service.create_configured_run(
            workflow=Workflow.GB_ENERGY,
            memory_tool="run_gb_energy",
            input_payload=canonical_payload,
            parameters={
                "description": cfg.description,
                "method": "bulk_and_free_surface_subtraction",
                "topology": GB_ENERGY_TOPOLOGY["name"],
                "interface_count": GB_ENERGY_TOPOLOGY["interface_count"],
                "free_surface_count": GB_ENERGY_TOPOLOGY["free_surface_count"],
                "energy_denominator": GB_ENERGY_TOPOLOGY["energy_denominator"],
                "element": cfg.material.element,
                "lattice": cfg.material.lattice,
                "top_orientation": cfg.grains.top.orientation.as_dict(),
                "bottom_orientation": cfg.grains.bottom.orientation.as_dict(),
                "x_pbc": True,
                "sizing_target_x_nm": geometry["target_x_nm"],
                "auto_sized_top": geometry["auto_sized_top"],
                "auto_sized_bottom": geometry["auto_sized_bottom"],
                "user_confirmed_size_override": user_confirmed_size_override,
                "construction_warnings": construction_warnings,
                "enable_initial_staged_relaxation": (
                    cfg.simulation.enable_initial_staged_relaxation
                ),
                "label": (label or "").strip(),
                "canonical_config": canonical_payload,
            },
        )
    except (ValueError, FileExistsError) as exc:
        return str(exc)
    return RunToolResult(
        run_id=run.run_id,
        workflow=run.workflow,
        status=run.status,
        manifest=run.manifest_path,
        artifacts=[{"role": "config", "path": run.config_path}],
        result={
            "geometry": geometry,
            "topology": dict(GB_ENERGY_TOPOLOGY),
            "warnings": construction_warnings,
        },
    ).to_json()


def _reference_artifact_specs() -> list[tuple[str, str, ProvenanceClass]]:
    specs: list[tuple[str, str, ProvenanceClass]] = []
    for grain_name in ("top", "bottom"):
        for mode in ("bulk", "surface"):
            prefix = f"{grain_name}_{mode}"
            root = f"{ENERGY_REFERENCES_DIR}/{prefix}"
            specs.extend(
                [
                    (f"{prefix}_log", f"{root}/log.lammps", ProvenanceClass.PRIMARY),
                    (
                        f"{prefix}_initial_cfg",
                        f"{root}/initial.cfg",
                        ProvenanceClass.PRIMARY,
                    ),
                    (
                        f"{prefix}_initial_lmp",
                        f"{root}/initial.lmp",
                        ProvenanceClass.PRIMARY,
                    ),
                    (
                        f"{prefix}_trajectory",
                        f"{root}/minimization.lammpstrj",
                        ProvenanceClass.DERIVED,
                    ),
                    (
                        f"{prefix}_minimized_cfg",
                        f"{root}/minimized.cfg",
                        ProvenanceClass.PRIMARY,
                    ),
                    (
                        f"{prefix}_minimized_lmp",
                        f"{root}/minimized.lmp",
                        ProvenanceClass.PRIMARY,
                    ),
                ]
            )
    return specs


def _artifact_specs() -> list[tuple[str, str, ProvenanceClass]]:
    return [
        ("summary_csv", ENERGY_SUMMARY_CSV, ProvenanceClass.PRIMARY),
        ("run_metadata", ENERGY_RUN_METADATA, ProvenanceClass.PRIMARY),
        ("bicrystal_log", ENERGY_BICRYSTAL_LOG, ProvenanceClass.PRIMARY),
        (
            "bicrystal_initial_cfg",
            ENERGY_BICRYSTAL_INITIAL_CFG,
            ProvenanceClass.PRIMARY,
        ),
        (
            "bicrystal_initial_lmp",
            ENERGY_BICRYSTAL_INITIAL_LMP,
            ProvenanceClass.PRIMARY,
        ),
        (
            "bicrystal_trajectory",
            ENERGY_BICRYSTAL_TRAJECTORY,
            ProvenanceClass.DERIVED,
        ),
        (
            "bicrystal_minimized_cfg",
            ENERGY_BICRYSTAL_MINIMIZED_CFG,
            ProvenanceClass.PRIMARY,
        ),
        (
            "bicrystal_minimized_lmp",
            ENERGY_BICRYSTAL_MINIMIZED_LMP,
            ProvenanceClass.PRIMARY,
        ),
        ("top_surface_slab_cfg", ENERGY_TOP_SLAB_CFG, ProvenanceClass.DERIVED),
        (
            "bottom_surface_slab_cfg",
            ENERGY_BOTTOM_SLAB_CFG,
            ProvenanceClass.DERIVED,
        ),
        *_reference_artifact_specs(),
    ]


def _load_energy_metadata(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid gb_energy metadata: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("gb_energy metadata root must be an object")
    return payload


def _minimization_metrics(output_dir: Path) -> dict[str, Any]:
    logs = {"bicrystal": output_dir / ENERGY_BICRYSTAL_LOG}
    for grain_name in ("top", "bottom"):
        for mode in ("bulk", "surface"):
            key = f"{grain_name}_{mode}"
            logs[key] = output_dir / ENERGY_REFERENCES_DIR / key / "log.lammps"
    calculations: dict[str, Any] = {}
    warnings: list[str] = []
    total = 0
    nonconverged = 0
    for key, path in logs.items():
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        metrics = extract_minimization_metrics(text)
        records = list(metrics.get("minimizations") or [])
        calculations[key] = {
            "minimization_count": len(records),
            "nonconverged_minimization_count": int(
                metrics.get("nonconverged_minimization_count") or 0
            ),
            "all_minimizations_converged": metrics.get("all_minimizations_converged"),
            "minimizations": records,
        }
        total += len(records)
        nonconverged += calculations[key]["nonconverged_minimization_count"]
        warnings.extend(f"{key}: {item}" for item in metrics.get("warnings") or [])
    return {
        "minimization_count": total,
        "nonconverged_minimization_count": nonconverged,
        "all_minimizations_converged": total > 0 and nonconverged == 0,
        "calculations": calculations,
        "warnings": warnings,
    }


def _compact_result(metadata: dict[str, Any]) -> dict[str, Any]:
    results = metadata.get("results") or {}
    bicrystal = metadata.get("bicrystal") or {}
    execution = metadata.get("execution") or {}
    warnings = list(
        dict.fromkeys(
            [
                *(metadata.get("construction_warnings") or []),
                *(execution.get("warnings") or []),
            ]
        )
    )
    return {
        "method": metadata.get("method"),
        "topology": dict(metadata.get("topology") or GB_ENERGY_TOPOLOGY),
        "gb_energy": dict(results.get("gb_energy") or {}),
        "interface_area_A2": results.get("interface_area_A2"),
        "bulk": dict(results.get("bulk") or {}),
        "surface": dict(results.get("surface") or {}),
        "bicrystal": {
            "state": dict(bicrystal.get("state") or {}),
            "counts": dict(bicrystal.get("counts") or {}),
            "overlap": dict(bicrystal.get("overlap") or {}),
        },
        "minimization_count": execution.get("minimization_count"),
        "nonconverged_minimization_count": execution.get(
            "nonconverged_minimization_count"
        ),
        "all_minimizations_converged": execution.get(
            "all_minimizations_converged"
        ),
        "warnings": warnings,
        "summary_csv": ENERGY_SUMMARY_CSV,
        "run_metadata": ENERGY_RUN_METADATA,
        "conclusion_fact_id": None,
        "conclusion_lookup_key": None,
    }


@tool
def run_gb_energy(run_id: str, np: int | None = None) -> str:
    """Execute a configured GB-energy run and persist its run, artifacts, and fact."""
    project_id = runtime_project_id()
    if not project_id:
        return "No active project_id in run config."
    store = ProjectStore()
    service = store.runs(project_id)
    try:
        run = configured_run(service, run_id, Workflow.GB_ENERGY)
        config_abs = store.project_path(project_id) / str(run.config_path)
        load_gb_energy_config(config_abs)
        resolved_np = resolve_mpi_ranks(np)
    except (FileNotFoundError, ValueError) as exc:
        return str(exc)

    output_dir = service.artifacts.run_directory(run_id)
    service.start_run(run_id, progress={"stage": "launching", "mpi_ranks": resolved_np})
    argv = mpirun_argv(GB_ENERGY_SCRIPT, resolved_np, "--output-dir", str(output_dir.resolve()),
                       "--config", str(config_abs.resolve()))
    try:
        command = launch(argv, ranks=resolved_np, timeout_sec=GB_ENERGY_TIMEOUT_SEC,
                         session_key=runtime_session_key())
    except Exception as exc:
        return run_tool_result(service, service.fail_run(run_id, error=str(exc))).to_json()

    save_launcher_logs(output_dir, command)
    status, error = command_status(command, output_dir, (
        ENERGY_SUMMARY_CSV,
        ENERGY_RUN_METADATA,
        ENERGY_BICRYSTAL_MINIMIZED_CFG,
        ENERGY_BICRYSTAL_MINIMIZED_LMP,
    ), label="gb_energy")

    try:
        metadata = _load_energy_metadata(output_dir / ENERGY_RUN_METADATA)
    except ValueError as exc:
        metadata = {}
        if status is RunStatus.COMPLETED:
            status = RunStatus.FAILED
            error = str(exc)
    metrics = _minimization_metrics(output_dir)
    metadata.setdefault("version", 1)
    metadata.setdefault("method", "bulk_and_free_surface_subtraction")
    metadata.setdefault("topology", dict(GB_ENERGY_TOPOLOGY))
    metadata["construction_warnings"] = list(
        run.parameters.get("construction_warnings") or []
    )
    metadata["status"] = status.value
    metadata["execution"] = {
        "mpi_ranks": resolved_np,
        **metrics,
        "error": error if status is not RunStatus.COMPLETED else "",
    }
    write_json_atomic(output_dir / ENERGY_RUN_METADATA, metadata)

    register_launcher_logs(service, run_id, output_dir)
    registered: dict[str, Any] = {}
    for role, relative_path, provenance in _artifact_specs():
        if (output_dir / relative_path).is_file():
            registered[role] = service.register_artifact(
                run_id,
                role,
                relative_path,
                provenance,
                metadata={"calculation": role.rsplit("_", 1)[0]},
            )

    compact = _compact_result(metadata)
    progress = {"stage": "finished", "mpi_ranks": resolved_np}
    conclusion = None
    if status is RunStatus.COMPLETED:
        cfg = load_gb_energy_config(config_abs)
        summary_artifact = registered.get("run_metadata")
        gb = compact["gb_energy"]
        conclusion = {
            "conclusion_type": "grain_boundary_energy",
            "method": compact["method"],
            "topology": compact["topology"],
            "interface_count": compact["topology"].get("interface_count"),
            "free_surface_count": compact["topology"].get("free_surface_count"),
            "energy_denominator": compact["topology"].get("energy_denominator"),
            "element": cfg.material.element,
            "lattice": cfg.material.lattice,
            "top_orientation": cfg.grains.top.orientation.as_dict(),
            "bottom_orientation": cfg.grains.bottom.orientation.as_dict(),
            "gb_energy_J_per_m2": gb.get("energy_J_per_m2"),
            "gb_energy_eV_per_A2": gb.get("energy_eV_per_A2"),
            "gb_excess_energy_eV": gb.get("excess_eV"),
            "interface_area_A2": compact["interface_area_A2"],
            "bulk": compact["bulk"],
            "surface": compact["surface"],
            "all_minimizations_converged": compact["all_minimizations_converged"],
            "nonconverged_minimization_count": compact["nonconverged_minimization_count"],
            "warnings": compact["warnings"],
            "run_metadata_artifact_path": (
                summary_artifact.path if summary_artifact is not None else None
            ),
        }
    terminal = finish_run(
        service, run_id, status, result=compact, progress=progress, error=error,
        memory_tool="run_gb_energy", cancelled_message="GB energy calculation cancelled",
        conclusion=conclusion,
    )

    fact_error = (
        f"gb_energy completed but conclusion indexing failed: {terminal.result['fact_indexing_error']}"
        if terminal.result.get("fact_indexing_error") else ""
    )

    return run_tool_result(service, terminal, error=fact_error or terminal.error).to_json()


__all__ = ["run_gb_energy", "write_gb_energy_config"]
