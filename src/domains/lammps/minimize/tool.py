"""Agent tools that size, configure and run GB minimizations."""

from __future__ import annotations

import math

from langchain_core.tools import tool

from core.execution.sessions import runtime_session_key
from core.util.graph_config import runtime_project_id
from core.util.io import write_json_atomic
from domains.lammps.common.artifacts import (
    BOT_BOUNDARY_IDS,
    INITIAL_CFG,
    MINIMIZE_RUN_METADATA,
    MINIMIZED_CFG,
    MINIMIZED_LMP,
    TOP_BOUNDARY_IDS,
    TRAJECTORY,
)
from domains.lammps.common.geometry import (
    box_size_violations,
    format_gb_box_geometry_report,
    fractional_repeat_warnings,
    gb_box_geometry,
    invalid_geometry_result,
)
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
from domains.lammps.common.log_metrics import extract_minimization_metrics
from domains.lammps.common.potentials import (
    resolve_potential,
    resolve_potential_file_for_config,
)
from domains.lammps.common.potential_files import potential_file_mass
from domains.lammps.common.settings import GB_MINIMIZE_SCRIPT, GB_MINIMIZE_TIMEOUT_SEC
from domains.lammps.minimize.config import (
    GBMinimizeConfig,
    MaterialConfig,
    RepeatCount,
    SimulationConfig,
    build_gb_minimize_config,
    load_gb_minimize_config,
)
from domains.lammps.minimize.config import (
    config_to_dict as gb_minimize_config_to_dict,
)
from project_memory.models import RunStatus, Workflow
from project_memory.publication.results import RunToolResult
from project_workspace.artifacts.models import ProvenanceClass
from project_workspace.store import ProjectStore


def _gb_minimize_analysis_handoff(
    cfg: GBMinimizeConfig,
    *,
    run_id: str,
    status: RunStatus,
    has_minimized_cfg: bool,
) -> dict:
    """Keep construction context and an eligible analysis request with the source run.

    Eligibility describes configured X vacuum. The analysis independently verifies the
    relaxed structure's geometry; imported CFG PBC flags do not describe occupied vacuum.
    This helper records a handoff, not a background execution or a completed analysis.
    """
    vacuum = cfg.simulation.y_vacuum
    structure_metadata = {
        "metadata_basis": "construction_config",
        "material": {
            "element": cfg.material.element,
            "lattice": cfg.material.lattice,
        },
        "reference_a0_angstrom": cfg.simulation.a0,
        "simulation_frame": {"x": "in_plane", "y": "gb_normal", "z": "tilt_axis"},
        "x_pbc": cfg.simulation.x_pbc,
        "boundary": cfg.simulation.boundary,
        "x_vacuum_padding_per_side_angstrom": (
            vacuum if not cfg.simulation.x_pbc else 0.0
        ),
        "top": {
            "orientation": cfg.grains.top.orientation.as_dict(),
            "size": list(cfg.grains.top.size),
        },
        "bottom": {
            "orientation": cfg.grains.bottom.orientation.as_dict(),
            "size": list(cfg.grains.bottom.size),
        },
    }
    followups = []
    if (
        status is RunStatus.COMPLETED
        and has_minimized_cfg
        and cfg.simulation.x_pbc is False
        and math.isfinite(vacuum)
        and vacuum > 0
    ):
        followups.append({
            "tool": "analyze_gb_periodicity",
            "agent": "analysis",
            "source_run_id": run_id,
            "source_artifact_role": "minimized_cfg",
            "reason": "Completed GB minimization with configured vacuum on both X sides.",
            "purpose": (
                "Identify short repeat and possible longer structural-modulation candidates "
                "in the relaxed boundary, then compare them with construction mismatch "
                "and size estimates so the user can choose a continuous-periodic X cell."
            ),
            "required": True,
        })
    return {"structure_metadata": structure_metadata, "analysis_followups": followups}


@tool
def run_gb_minimize(
    run_id: str,
    np: int | None = None,
) -> str:
    """Execute a configured GB minimization and record its analysis handoff when eligible.

    A completed X-vacuum run returns analysis_followups for the orchestrator to delegate
    to Analysis; it does not execute OVITO or choose a periodic cell in the LAMMPS worker.
    """
    project_id = runtime_project_id()
    if not project_id:
        return "No active project_id in run config."
    store = ProjectStore()
    service = store.runs(project_id)
    try:
        run = configured_run(service, run_id, Workflow.GB_MINIMIZE)
        config_abs = store.project_path(project_id) / str(run.config_path)
        cfg = load_gb_minimize_config(config_abs)
        resolved_np = resolve_mpi_ranks(np)
    except (FileNotFoundError, ValueError) as exc:
        return str(exc)
    output_dir = service.artifacts.run_directory(run_id)
    service.start_run(run_id, progress={"stage": "launching", "mpi_ranks": resolved_np})
    argv = mpirun_argv(GB_MINIMIZE_SCRIPT, resolved_np, "--output-dir", str(output_dir),
                       "--config", str(config_abs))
    try:
        command = launch(argv, ranks=resolved_np, timeout_sec=GB_MINIMIZE_TIMEOUT_SEC,
                         session_key=runtime_session_key())
    except Exception as exc:
        return run_tool_result(service, service.fail_run(run_id, error=str(exc))).to_json()

    progress = {"stage": "finished", "mpi_ranks": resolved_np}
    save_launcher_logs(output_dir, command)
    terminal_status, error = command_status(
        command, output_dir, (MINIMIZED_CFG, MINIMIZED_LMP, TOP_BOUNDARY_IDS, BOT_BOUNDARY_IDS),
        label="gb_minimize",
    )

    log_path = output_dir / "log.lammps"
    log_text = (
        log_path.read_text(encoding="utf-8", errors="replace")
        if log_path.is_file()
        else ""
    )
    metrics = extract_minimization_metrics(log_text)
    construction_warnings = list(run.parameters.get("construction_warnings") or [])
    metrics["warnings"] = list(
        dict.fromkeys([*construction_warnings, *(metrics.get("warnings") or [])])
    )
    analysis_handoff = _gb_minimize_analysis_handoff(
        cfg,
        run_id=run_id,
        status=terminal_status,
        has_minimized_cfg=(output_dir / MINIMIZED_CFG).is_file(),
    )
    metrics.update(analysis_handoff)
    metadata = {
        "version": 1,
        "status": terminal_status.value,
        "mpi_ranks": resolved_np,
        "convergence_basis": "final_minimization",
        "minimization_count": metrics.get("minimization_count", 0),
        "nonconverged_minimization_count": metrics.get(
            "nonconverged_minimization_count", 0
        ),
        "all_minimizations_converged": metrics.get(
            "all_minimizations_converged"
        ),
        "final_minimization_converged": metrics.get(
            "final_minimization_converged"
        ),
        "minimizations": list(metrics.get("minimizations") or []),
        "warnings": metrics["warnings"],
        **analysis_handoff,
        "error": error if terminal_status is not RunStatus.COMPLETED else "",
    }
    metadata_path = output_dir / MINIMIZE_RUN_METADATA
    write_json_atomic(metadata_path, metadata, sort_keys=False)

    register_launcher_logs(service, run_id, output_dir)
    artifact_specs = (
        ("log", "log.lammps", ProvenanceClass.PRIMARY),
        ("initial_cfg", INITIAL_CFG, ProvenanceClass.PRIMARY),
        ("trajectory", TRAJECTORY, ProvenanceClass.DERIVED),
        ("minimized_cfg", MINIMIZED_CFG, ProvenanceClass.PRIMARY),
        ("minimized_lmp", MINIMIZED_LMP, ProvenanceClass.PRIMARY),
        ("top_boundary_ids", TOP_BOUNDARY_IDS, ProvenanceClass.PRIMARY),
        ("bottom_boundary_ids", BOT_BOUNDARY_IDS, ProvenanceClass.PRIMARY),
        ("run_metadata", MINIMIZE_RUN_METADATA, ProvenanceClass.PRIMARY),
    )
    for role, filename, provenance in artifact_specs:
        if (output_dir / filename).is_file():
            service.register_artifact(run_id, role, filename, provenance)

    terminal = finish_run(
        service, run_id, terminal_status, result=metrics, progress=progress, error=error,
        memory_tool="run_gb_minimize", cancelled_message="GB minimization cancelled",
    )
    return run_tool_result(service, terminal).to_json()


@tool
def estimate_gb_box_size(
    top_x: list[int],
    top_y: list[int],
    top_z: list[int],
    bottom_x: list[int],
    bottom_y: list[int],
    bottom_z: list[int],
    top_size: list[RepeatCount] | None = None,
    bottom_size: list[RepeatCount] | None = None,
    a0: float | None = None,
    lattice: str = "bcc",
    y_vacuum: float = 20.0,
    y_eps: float = 1.0e-2,
    x_pbc: bool = False,
    target_x_nm: float | None = None,
) -> str:
    """Preview bicrystal box dimensions before writing a gb_minimize config (read-only).

    Simulation frame: y = GB normal, z = tilt axis, x = in-plane direction.
    Omit top_size/bottom_size to preview the deterministic default sizing policy.
    Supply them to inspect an explicit pair of repeat counts.

    top_x/y/z, bottom_x/y/z: integer orientation vectors for each grain.
    top_size, bottom_size: optional positive [nx, ny, nz] repeat counts per
      grain. Integer values are recommended and used for all generated
      candidates; explicit fractional user values are measured unchanged and
      reported with a warning. An omitted side is auto-sized from policy.
    a0: lattice parameter in Å (default: Fe BCC schema value, 2.8553 Å).
    lattice: bcc, fcc, sc, or hcp — used for approximate atom count.
    y_vacuum / y_eps: use the same values intended for the final config.
    x_pbc: if false (default), retain y_vacuum on each x side; if true,
      retain only y_eps on each x side for an effectively contiguous periodic x box.
      This changes padding only, not repeat selection or mismatch validation.
    target_x_nm: optional preferred occupied x length, default 20 nm. It ranks
      valid integer repeat matches and is not a lower bound. Smaller or slightly
      larger candidates remain valid when their mismatch is below the fixed limit.
    Every reported candidate includes material x/y/z dimensions in nm and complete
      top/bottom [nx, ny, nz] sizes; y and z satisfy the construction minima.
    """
    try:
        geometry = gb_box_geometry(
            top_x=top_x,
            top_y=top_y,
            top_z=top_z,
            top_size=top_size,
            bottom_x=bottom_x,
            bottom_y=bottom_y,
            bottom_z=bottom_z,
            bottom_size=bottom_size,
            a0=a0,
            lattice=lattice,
            y_vacuum=y_vacuum,
            y_eps=y_eps,
            x_pbc=x_pbc,
            target_x_nm=target_x_nm,
        )
    except ValueError as exc:
        return str(exc)

    return format_gb_box_geometry_report(geometry)


@tool
def write_gb_minimize_config(
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
    x_pbc: bool = False,
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
    description: str | None = None,
    label: str | None = None,
) -> str:
    """Validate input and create a configured GB-minimization run.

    Call estimate_gb_box_size first to preview explicit sizes. When top_size or
    bottom_size is omitted, the writer deterministically applies the active
    construction policy and stores the resolved repeat counts in the config.

    Orientation frame: y = GB normal, z = tilt axis, x = in-plane ⊥ axis.
    top_size / bottom_size: optional positive [nx, ny, nz] repeat counts.
    Omitted counts are auto-sized with integers. Explicit fractional counts are
    preserved unchanged and produce a commensurability warning.

    user_confirmed_size_override: set true only when the user explicitly
    supplied or confirmed both size triplets and asked to proceed despite an
    agent sizing-policy finding. It converts those findings to recorded
    warnings. Never use it for sizes selected by the agent.

    All GBMinimizeConfig scientific fields are typed tool parameters. Omitted
    material/potential values are filled from the catalog; ordinary simulation
    and minimization fields use the schema defaults shown in the tool signature.
    Keep LAMMPS ``boundary`` as ``p p p``. Open x faces use ``x_pbc=false`` with
    ``y_vacuum`` padding inside that periodic box; ``x_pbc=true`` keeps only
    ``y_eps`` padding for a contiguous periodic x box used by later sliding.
    This boundary choice does not affect repeat matching. target_x_nm defaults
    to 20 nm and only ranks crystallography-dependent integer repeat matches;
    every agent-selected size must satisfy the configured x mismatch limit.
    Non-``p p p`` boundary values are rejected so you can regenerate the config.
    enable_initial_staged_relaxation: if true, run the special preliminary
    three-stage minimization (freeze bottom, freeze top, then all atoms free)
    before the normal cyclic box relaxation. Default false.
    The complete config is validated first. Geometry is then checked from that
    exact canonical config, and the potential is stored as a validated absolute
    path. Invalid geometry creates no run and returns objective evidence only.
    """
    project_id = runtime_project_id()
    if not project_id:
        return "No active project_id in run config."
    if user_confirmed_size_override and (top_size is None or bottom_size is None):
        return (
            "user_confirmed_size_override requires explicit top_size and "
            "bottom_size; it cannot override auto-sizing"
        )

    material_defaults = MaterialConfig()
    simulation_defaults = SimulationConfig()
    resolved = resolve_potential(
        lattice=(lattice or ""),
        element=(element or ""),
    )
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
            x_pbc=x_pbc,
            target_x_nm=target_x_nm,
        )
    except ValueError as exc:
        return str(exc)
    resolved_top_size = sizing_geometry["resolved_top_size"]
    resolved_bottom_size = sizing_geometry["resolved_bottom_size"]

    payload: dict = {
        "description": description or "",
        "material": {
            "lattice": resolved_lattice,
            "element": resolved_element,
            "mass": resolved_mass,
        },
        "grains": {
            "top": {
                "orientation": {
                    "x": list(top_x),
                    "y": list(top_y),
                    "z": list(top_z),
                },
                "size": list(resolved_top_size),
            },
            "bottom": {
                "orientation": {
                    "x": list(bottom_x),
                    "y": list(bottom_y),
                    "z": list(bottom_z),
                },
                "size": list(resolved_bottom_size),
            },
        },
        "simulation": {
            "a0": resolved_a0,
            "boundary": boundary,
            "y_vacuum": y_vacuum,
            "y_eps": y_eps,
            "x_pbc": x_pbc,
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
    }

    try:
        cfg = build_gb_minimize_config(payload)
        resolved_potential_path = resolve_potential_file_for_config(cfg.potential.file)
        if not resolved_potential_path.is_file():
            return f"Potential file not found: {resolved_potential_path}"
        payload["potential"]["file"] = str(resolved_potential_path)
        cfg = build_gb_minimize_config(payload)

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
            x_pbc=cfg.simulation.x_pbc,
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

        canonical_payload = gb_minimize_config_to_dict(cfg)
        service = ProjectStore().runs(project_id)
        run = service.create_configured_run(
            workflow=Workflow.GB_MINIMIZE,
            input_payload=canonical_payload,
            parameters={
                "memory_tool": "run_gb_minimize",
                "description": cfg.description,
                "element": cfg.material.element,
                "lattice": cfg.material.lattice,
                "top_orientation": cfg.grains.top.orientation.as_dict(),
                "bottom_orientation": cfg.grains.bottom.orientation.as_dict(),
                "x_pbc": cfg.simulation.x_pbc,
                "sizing_target_x_nm": geometry["target_x_nm"],
                "auto_sized_top": geometry["auto_sized_top"],
                "auto_sized_bottom": geometry["auto_sized_bottom"],
                "user_confirmed_size_override": user_confirmed_size_override,
                "construction_warnings": construction_warnings,
                "enable_initial_staged_relaxation": (
                    cfg.simulation.enable_initial_staged_relaxation
                ),
                "label": (label or "").strip(),
                # Searchable mirror only. input.json remains the authoritative config.
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
        result={"geometry": geometry, "warnings": construction_warnings},
    ).to_json()
