"""Run quasi-static grain-boundary shear with LAMMPS.

The input structure is the final state of a completed minimization or shear
run. Boundary atom IDs travel with that parent state. Spring-force and
direct-displacement loading are supported. This script is intended to run
under MPI in the dedicated LAMMPS environment.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

# Run as a script in the LAMMPS environment: import packages from src/, never
# from this workflow directory, whose module names (config, tool) are generic.
_HERE = Path(__file__).resolve().parent
_SRC_ROOT = _HERE.parents[2]
sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != _HERE]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from domains.lammps.common.potential_files import current_potential_path
from domains.lammps.common.engine_utils import group_count, mpi_rank, write_cfg
from core.util.io import write_json_atomic
from domains.lammps.common.artifacts import (
    SHEARED_CFG,
    SHEARED_LMP,
    SHEAR_CURVE,
    SHEAR_RUN_METADATA,
    SHEAR_STRUCTURES_DIR,
)
from domains.lammps.shear.config import (
    EV_PER_ANGSTROM3_TO_MPA,
    GBShearConfig,
    LoadingStepState,
    load_gb_shear_config,
    read_atom_ids,
    shear_observables,
    target_loading_state,
    validate_boundary_ids,
)

TOP_BOUNDARY_GROUP = "top_boundary"
BOTTOM_BOUNDARY_GROUP = "bottom_boundary"
TOP_SPRING_FIX = "gb_shear_top"
BOTTOM_SPRING_FIX = "gb_shear_bottom"

BAR_TO_MPA = 0.1

SHEAR_CURVE_FIELDS = (
    "step",
    "increment_stage",
    "loading_direction",
    "loading_half_cycle",
    "loading_cycle",
    "step_in_loading_half_cycle",
    "relative_target_shear_strain",
    "target_shear_strain",
    "relative_shear_strain",
    "actual_shear_strain",
    "current_slab_spacing_angstrom",
    "top_shear_stress_mpa",
    "bottom_shear_stress_mpa",
    "sigma_xx_mpa",
    "sigma_yy_mpa",
    "sigma_zz_mpa",
    "sigma_xy_mpa",
    "sigma_xz_mpa",
    "sigma_yz_mpa",
    "converged",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def initialize_run_metadata(
    *,
    loading: Any,
    initial_top_x: float,
    initial_top_y: float,
    initial_bottom_x: float,
    initial_bottom_y: float,
    slab_spacing: float,
    initial_geometry: dict[str, float],
    n_top: int,
    n_bottom: int,
    top_spring_total: float | None,
    bottom_spring_total: float | None,
    dump_every: int,
    parent_run_id: str,
    parent_workflow: str,
    initial_cumulative_shear_strain: float,
    protocol_warnings: tuple[str, ...],
    started_at: str | None = None,
) -> dict[str, Any]:
    timestamp = started_at or _utc_now_iso()
    return {
        "version": 1,
        "status": "running",
        "started_at": timestamp,
        "updated_at": timestamp,
        "finished_at": None,
        "error": "",
        "loading": {
            "load_type": loading.load_type,
            "initial_direction": loading.direction,
            "steps_per_half_cycle": loading.steps_per_half_cycle,
            "requested_half_cycles": loading.half_cycles,
            "cyclic": loading.cyclic,
            "planned_loading_half_cycles": loading.loading_half_cycles,
            "planned_loading_cycles": loading.loading_cycles,
            "normal_direction": "y",
            "control_mode": (
                "spring_tether"
                if loading.load_type == "spring_force"
                else "direct_displacement"
            ),
            "boundary_stress_source": (
                "spring_reaction"
                if loading.load_type == "spring_force"
                else "sigma_xy_top_and_negative_bottom"
            ),
            "y_constraint": "fixed_per_atom",
            "strain_reference": "current_slab_spacing",
            "initial_strain_increment": loading.initial_strain_increment,
            "initial_steps": loading.initial_steps,
            "fine_strain_increment": loading.fine_strain_increment,
            "fine_steps": loading.fine_steps,
            "dump_every": dump_every,
        },
        "lineage": {
            "parent_run_id": parent_run_id,
            "parent_workflow": parent_workflow,
            "initial_cumulative_shear_strain": initial_cumulative_shear_strain,
        },
        "warnings": list(protocol_warnings),
        "reference": {
            "top_com_initial": {"x": initial_top_x, "y": initial_top_y},
            "bottom_com_initial": {"x": initial_bottom_x, "y": initial_bottom_y},
            "slab_spacing_initial_angstrom": slab_spacing,
            "gb_area_angstrom2": initial_geometry["gb_area"],
            "atom_bounds_initial_angstrom": {
                axis: {
                    "min": initial_geometry[f"atom_{axis}min"],
                    "max": initial_geometry[f"atom_{axis}max"],
                    "span": initial_geometry[f"atom_l{axis}"],
                }
                for axis in "xyz"
            },
            "atom_region_volume_initial_angstrom3": initial_geometry[
                "atom_volume"
            ],
            "box_lx_angstrom": initial_geometry["box_lx"],
            "box_ly_angstrom": initial_geometry["box_ly"],
            "box_lz_angstrom": initial_geometry["box_lz"],
            "box_volume_initial_angstrom3": initial_geometry["box_volume"],
            "stress_volume_correction_factor": initial_geometry[
                "stress_volume_correction"
            ],
            "top_group_atoms": n_top,
            "bottom_group_atoms": n_bottom,
            "top_spring_total_ev_per_angstrom2": top_spring_total,
            "bottom_spring_total_ev_per_angstrom2": bottom_spring_total,
            "top_spring_per_atom_ev_per_angstrom2": (
                loading.top_spring_per_atom
                if loading.load_type == "spring_force"
                else None
            ),
            "bottom_spring_per_atom_ev_per_angstrom2": (
                loading.bottom_spring_per_atom
                if loading.load_type == "spring_force"
                else None
            ),
        },
        "progress": {
            "planned_loading_steps": loading.total_steps,
            "planned_points": loading.total_steps + 1,
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


def record_completed_step(
    metadata: dict[str, Any],
    *,
    step: int,
    loading_state: LoadingStepState,
    top_xcm: float,
    top_ycm: float,
    bottom_xcm: float,
    bottom_ycm: float,
    observables: dict[str, float | None],
    stress_state: dict[str, float | None],
    converged: bool,
    updated_at: str | None = None,
) -> None:
    timestamp = updated_at or _utc_now_iso()
    current = {
        "step": step,
        "increment_stage": loading_state.increment_stage,
        "loading_direction": loading_state.loading_direction,
        "loading_half_cycle": loading_state.loading_half_cycle,
        "loading_cycle": loading_state.loading_cycle,
        "step_in_loading_half_cycle": (
            loading_state.step_in_loading_half_cycle
        ),
        "relative_target_shear_strain": loading_state.target_strain,
        "target_shear_strain": (
            float(metadata["lineage"]["initial_cumulative_shear_strain"])
            + loading_state.target_strain
        ),
        "relative_shear_strain": observables["relative_shear_strain"],
        "actual_shear_strain": observables["actual_shear_strain"],
        "current_slab_spacing_angstrom": observables[
            "current_slab_spacing_angstrom"
        ],
        "top_shear_stress_mpa": observables["top_shear_stress_mpa"],
        **stress_state,
        "top_com": {"x": top_xcm, "y": top_ycm},
        "bottom_com": {"x": bottom_xcm, "y": bottom_ycm},
        "converged": bool(converged),
    }
    metadata["updated_at"] = timestamp
    metadata["progress"]["last_completed_step"] = step
    metadata["progress"]["completed_points"] = step + 1
    if not converged:
        metadata["progress"]["nonconverged_points"] = (
            int(metadata["progress"].get("nonconverged_points", 0)) + 1
        )
    summary = metadata["summary"]
    top_stress = current["top_shear_stress_mpa"]
    peak_abs = summary.get("peak_abs_shear_stress_mpa")
    if top_stress is not None and (
        peak_abs is None or abs(top_stress) > float(peak_abs)
    ):
        summary.update(
            {
                "peak_abs_shear_stress_mpa": abs(top_stress),
                "stress_at_peak_mpa": top_stress,
                "strain_at_peak": current["actual_shear_strain"],
                "step_at_peak": step,
                "loading_direction_at_peak": current["loading_direction"],
                "loading_half_cycle_at_peak": current["loading_half_cycle"],
                "loading_cycle_at_peak": current["loading_cycle"],
            }
        )
    metadata["current"] = current
    metadata["points"].append(dict(current))


def finalize_run_metadata(
    metadata: dict[str, Any],
    *,
    status: str,
    error: str = "",
    finished_at: str | None = None,
) -> None:
    if status not in {"completed", "failed", "cancelled"}:
        raise ValueError(f"invalid terminal gb_shear status: {status}")
    timestamp = finished_at or _utc_now_iso()
    metadata["status"] = status
    metadata["updated_at"] = timestamp
    metadata["finished_at"] = timestamp
    metadata["error"] = error[:1000]


def write_run_metadata_atomic(path: Path, metadata: dict[str, Any]) -> None:
    write_json_atomic(path, metadata, sort_keys=False, fsync=False)


def extract_initial_geometry(lmp: Any) -> dict[str, float]:
    """Measure the initial occupied atom region and simulation box."""
    variable_names = {
        key: f"__gb_shear_all_{key}"
        for key in ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")
    }
    try:
        for direction, variable_name in variable_names.items():
            lmp.command(f"variable {variable_name} equal bound(all,{direction})")
        lmp.command("run 0")
        bounds = {
            direction: _extract_equal_variable(lmp, variable_name)
            for direction, variable_name in variable_names.items()
        }
    finally:
        for variable_name in variable_names.values():
            try:
                lmp.command(f"variable {variable_name} delete")
            except Exception:
                pass

    atom_lx = bounds["xmax"] - bounds["xmin"]
    atom_ly = bounds["ymax"] - bounds["ymin"]
    atom_lz = bounds["zmax"] - bounds["zmin"]
    box_lx = float(lmp.get_thermo("lx"))
    box_ly = float(lmp.get_thermo("ly"))
    box_lz = float(lmp.get_thermo("lz"))
    dimensions = {
        "atom_lx": atom_lx,
        "atom_ly": atom_ly,
        "atom_lz": atom_lz,
        "box_lx": box_lx,
        "box_ly": box_ly,
        "box_lz": box_lz,
    }
    invalid = {
        name: value
        for name, value in dimensions.items()
        if not math.isfinite(value) or value <= 0.0
    }
    if invalid:
        detail = ", ".join(f"{name}={value}" for name, value in invalid.items())
        raise RuntimeError(f"invalid initial stress-reference geometry: {detail}")

    atom_volume = atom_lx * atom_ly * atom_lz
    box_volume = box_lx * box_ly * box_lz
    return {
        **{f"atom_{key}": value for key, value in bounds.items()},
        **dimensions,
        "gb_area": atom_lx * atom_lz,
        "atom_volume": atom_volume,
        "box_volume": box_volume,
        "stress_volume_correction": box_volume / atom_volume,
    }


def rebuild_group_from_ids(
    lmp: Any,
    group_name: str,
    atom_ids: list[int],
    *,
    chunk_size: int = 200,
) -> int:
    """Rebuild a LAMMPS group without creating an oversized command line."""
    if not atom_ids:
        raise ValueError(f"{group_name} atom IDs must not be empty")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    lmp.command(f"group {group_name} empty")
    for start in range(0, len(atom_ids), chunk_size):
        chunk = " ".join(str(atom_id) for atom_id in atom_ids[start : start + chunk_size])
        lmp.command(f"group {group_name} id {chunk}")
    actual = group_count(lmp, group_name, variable_prefix="__gb_shear_count_")
    if actual != len(atom_ids):
        raise RuntimeError(
            f"{group_name}: expected {len(atom_ids)} atoms, rebuilt {actual}; "
            "the ID file does not match the LAMMPS data file"
        )
    return actual


def _resolve_project_path(
    project_root: Path,
    value: str,
    *,
    label: str,
    allow_external: bool = False,
) -> Path:
    raw = Path(value)
    resolved = raw.resolve() if raw.is_absolute() else (project_root / raw).resolve()
    root = project_root.resolve()
    if not allow_external and root not in (resolved, *resolved.parents):
        raise ValueError(f"{label} escapes the project root: {value}")
    return resolved


def _extract_equal_variable(lmp: Any, name: str) -> float:
    return float(lmp.extract_variable(name, None, 0))


def _write_step_structure(lmp: Any, path: Path, *, element_map: str) -> None:
    write_cfg(lmp, "all", path, element_map)


def run_gb_shear(
    config: GBShearConfig,
    output_dir: Path,
    *,
    project_root: Path,
) -> None:
    """Execute the configured unidirectional or cyclic x-axis shear protocol."""
    source_dir = _resolve_project_path(
        project_root, config.source.output_dir, label="source.output_dir"
    )
    data_path = source_dir / config.source.data
    top_ids_path = source_dir / config.source.top_boundary_ids
    bottom_ids_path = source_dir / config.source.bottom_boundary_ids
    for label, path in (
        ("source data", data_path),
        ("top boundary IDs", top_ids_path),
        ("bottom boundary IDs", bottom_ids_path),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{label} not found: {path}")

    potential_path = _resolve_project_path(
        project_root,
        config.potential.file,
        label="potential.file",
        allow_external=True,
    )
    potential_path = current_potential_path(potential_path)
    if not potential_path.is_file():
        raise FileNotFoundError(f"Potential file not found: {potential_path}")

    top_ids = read_atom_ids(top_ids_path)
    bottom_ids = read_atom_ids(bottom_ids_path)
    validate_boundary_ids(top_ids, bottom_ids)

    structures_dir = output_dir / SHEAR_STRUCTURES_DIR
    structures_dir.mkdir(parents=True, exist_ok=True)
    rank = mpi_rank()

    from domains.lammps.common.live_lammps import lammps

    lmp = lammps(cmdargs=["-log", str(output_dir / "log.lammps"), "-nonbuf"])
    curve_handle: TextIO | None = None
    run_metadata: dict[str, Any] | None = None
    metadata_path = output_dir / SHEAR_RUN_METADATA
    try:
        lmp.command("units metal")
        lmp.command("atom_style atomic")
        lmp.command("dimension 3")
        lmp.command(f"boundary {config.simulation.boundary}")
        lmp.command(f"read_data {data_path}")
        lmp.command("neighbor 1.0 bin")
        lmp.command("neigh_modify delay 1 every 1 check yes")
        lmp.command(f"pair_style {config.potential.pair_style}")
        atom_types = int(lmp.extract_global("ntypes"))
        element_map = " ".join(config.material.element for _ in range(atom_types))
        lmp.command(f"pair_coeff * * {potential_path} {element_map}")
        lmp.command(f"thermo {config.output.thermo_every}")
        lmp.command(
            "thermo_style custom step pe lx ly lz "
            "pxx pyy pzz pxy pxz pyz fmax fnorm"
        )
        lmp.command("thermo_modify flush yes")
        lmp.command("run 0")

        initial_geometry = extract_initial_geometry(lmp)
        n_top = rebuild_group_from_ids(lmp, TOP_BOUNDARY_GROUP, top_ids)
        n_bottom = rebuild_group_from_ids(lmp, BOTTOM_BOUNDARY_GROUP, bottom_ids)

        for name, expression in (
            ("gb_top_xcm", f"xcm({TOP_BOUNDARY_GROUP},x)"),
            ("gb_top_ycm", f"xcm({TOP_BOUNDARY_GROUP},y)"),
            ("gb_bottom_xcm", f"xcm({BOTTOM_BOUNDARY_GROUP},x)"),
            ("gb_bottom_ycm", f"xcm({BOTTOM_BOUNDARY_GROUP},y)"),
        ):
            lmp.command(f"variable {name} equal {expression}")
        lmp.command("run 0")

        initial_top_x = _extract_equal_variable(lmp, "gb_top_xcm")
        initial_top_y = _extract_equal_variable(lmp, "gb_top_ycm")
        initial_bottom_x = _extract_equal_variable(lmp, "gb_bottom_xcm")
        initial_bottom_y = _extract_equal_variable(lmp, "gb_bottom_ycm")
        initial_relative_x = initial_top_x - initial_bottom_x
        slab_spacing = abs(initial_top_y - initial_bottom_y)
        if slab_spacing <= 0.0:
            raise RuntimeError("top and bottom boundary layer centers have zero y spacing")

        # Fix the y positions of the top and bottom grains
        lmp.command(f"fix fy_top {TOP_BOUNDARY_GROUP} setforce NULL 0.0 NULL")
        lmp.command(f"fix fy_bot {BOTTOM_BOUNDARY_GROUP} setforce NULL 0.0 NULL")

        bottom_stiffness: float | None = None
        top_stiffness: float | None = None
        if config.loading.load_type == "spring_force":
            if (
                config.loading.top_spring_per_atom is None
                or config.loading.bottom_spring_per_atom is None
            ):
                raise RuntimeError(
                    "spring_force loading requires top and bottom spring stiffness"
                )
            bottom_stiffness = config.loading.bottom_spring_per_atom * n_bottom
            top_stiffness = config.loading.top_spring_per_atom * n_top

            lmp.command(
                f"fix {BOTTOM_SPRING_FIX} {BOTTOM_BOUNDARY_GROUP} spring tether "
                f"{bottom_stiffness:.16e} {initial_bottom_x:.16e} NULL NULL 0.0"
            )
            lmp.command(f"fix_modify {BOTTOM_SPRING_FIX} energy yes")
            lmp.command(f"fix_modify {BOTTOM_SPRING_FIX} virial no")
        else:
            lmp.command(f"fix fx_top {TOP_BOUNDARY_GROUP} setforce 0.0 NULL NULL")
            lmp.command(f"fix fx_bot {BOTTOM_BOUNDARY_GROUP} setforce 0.0 NULL NULL")

        if rank == 0:
            run_metadata = initialize_run_metadata(
                loading=config.loading,
                initial_top_x=initial_top_x,
                initial_top_y=initial_top_y,
                initial_bottom_x=initial_bottom_x,
                initial_bottom_y=initial_bottom_y,
                slab_spacing=slab_spacing,
                initial_geometry=initial_geometry,
                n_top=n_top,
                n_bottom=n_bottom,
                top_spring_total=top_stiffness,
                bottom_spring_total=bottom_stiffness,
                dump_every=config.output.dump_every,
                parent_run_id=config.source.parent_run_id,
                parent_workflow=config.source.parent_workflow,
                initial_cumulative_shear_strain=(
                    config.source.initial_cumulative_shear_strain
                ),
                protocol_warnings=config.loading.protocol_warnings,
            )
            write_run_metadata_atomic(metadata_path, run_metadata)

        if rank == 0:
            curve_handle = (output_dir / SHEAR_CURVE).open("w", encoding="utf-8", newline="")
            curve_writer: csv.DictWriter | None = csv.DictWriter(
                curve_handle, fieldnames=SHEAR_CURVE_FIELDS
            )
            curve_writer.writeheader()
            curve_handle.flush()
        else:
            curve_writer = None

        top_fix_active = False
        force_variables_defined = False

        for step in range(config.loading.total_steps + 1):
            loading_state = target_loading_state(
                step, config.loading, slab_spacing=slab_spacing
            )
            if config.loading.load_type == "spring_force":
                if top_fix_active:
                    lmp.command(f"unfix {TOP_SPRING_FIX}")
                target_top_x = initial_top_x + loading_state.target_displacement
                lmp.command(
                    f"fix {TOP_SPRING_FIX} {TOP_BOUNDARY_GROUP} spring tether "
                    f"{top_stiffness:.16e} {target_top_x:.16e} NULL NULL 0.0"
                )
                lmp.command(f"fix_modify {TOP_SPRING_FIX} energy yes")
                lmp.command(f"fix_modify {TOP_SPRING_FIX} virial no")
                top_fix_active = True

                if not force_variables_defined:
                    lmp.command(f"variable gb_top_spring_fx equal f_{TOP_SPRING_FIX}[1]")
                    lmp.command(f"variable gb_bottom_spring_fx equal f_{BOTTOM_SPRING_FIX}[1]")
                    force_variables_defined = True
            elif loading_state.step_displacement != 0.0:
                lmp.command(
                    f"displace_atoms {TOP_BOUNDARY_GROUP} move "
                    f"{loading_state.step_displacement:.16e} 0.0 0.0 units box"
                )

            lmp.command(f"min_style {config.minimization.style}")
            lmp.command(
                "minimize "
                f"{config.minimization.etol} {config.minimization.ftol} "
                f"{config.minimization.maxiter} {config.minimization.maxeval}"
            )
            converged = float(lmp.get_thermo("fmax")) <= config.minimization.ftol
            top_xcm = _extract_equal_variable(lmp, "gb_top_xcm")
            top_ycm = _extract_equal_variable(lmp, "gb_top_ycm")
            bottom_xcm = _extract_equal_variable(lmp, "gb_bottom_xcm")
            bottom_ycm = _extract_equal_variable(lmp, "gb_bottom_ycm")
            if config.loading.load_type == "spring_force":
                top_force = _extract_equal_variable(lmp, "gb_top_spring_fx")
                bottom_force = _extract_equal_variable(
                    lmp, "gb_bottom_spring_fx"
                )
            else:
                top_force = None
                bottom_force = None
            observables = shear_observables(
                top_xcm=top_xcm,
                bottom_xcm=bottom_xcm,
                top_ycm=top_ycm,
                bottom_ycm=bottom_ycm,
                initial_relative_x=initial_relative_x,
                slab_spacing=slab_spacing,
                top_spring_force_x=top_force,
                lx=initial_geometry["atom_lx"],
                lz=initial_geometry["atom_lz"],
            )
            observables["relative_shear_strain"] = observables[
                "actual_shear_strain"
            ]
            observables["actual_shear_strain"] = (
                config.source.initial_cumulative_shear_strain
                + float(observables["relative_shear_strain"])
            )
            volume_correction = initial_geometry["stress_volume_correction"]
            stress_state = {
                "bottom_shear_stress_mpa": (
                    None
                    if bottom_force is None
                    else bottom_force
                    / initial_geometry["gb_area"]
                    * EV_PER_ANGSTROM3_TO_MPA
                ),
                **{
                    f"sigma_{component}_mpa": (
                        -float(lmp.get_thermo(f"p{component}"))
                        * BAR_TO_MPA
                        * volume_correction
                    )
                    for component in ("xx", "yy", "zz", "xy", "xz", "yz")
                },
            }
            if config.loading.load_type == "displacement":
                sigma_xy = stress_state["sigma_xy_mpa"]
                if sigma_xy is None:
                    raise RuntimeError(
                        "sigma_xy is unavailable for displacement-controlled stress"
                    )
                observables["top_shear_stress_mpa"] = sigma_xy
                stress_state["bottom_shear_stress_mpa"] = -sigma_xy

            if curve_writer is not None and curve_handle is not None:
                curve_writer.writerow(
                    {
                        "step": step,
                        "increment_stage": loading_state.increment_stage,
                        "loading_direction": loading_state.loading_direction,
                        "loading_half_cycle": loading_state.loading_half_cycle,
                        "loading_cycle": loading_state.loading_cycle,
                        "step_in_loading_half_cycle": (
                            loading_state.step_in_loading_half_cycle
                        ),
                        "relative_target_shear_strain": (
                            loading_state.target_strain
                        ),
                        "target_shear_strain": (
                            config.source.initial_cumulative_shear_strain
                            + loading_state.target_strain
                        ),
                        "relative_shear_strain": observables[
                            "relative_shear_strain"
                        ],
                        "actual_shear_strain": observables["actual_shear_strain"],
                        "current_slab_spacing_angstrom": observables[
                            "current_slab_spacing_angstrom"
                        ],
                        "top_shear_stress_mpa": observables[
                            "top_shear_stress_mpa"
                        ],
                        **stress_state,
                        "converged": converged,
                    }
                )
                curve_handle.flush()

            if rank == 0 and run_metadata is not None:
                record_completed_step(
                    run_metadata,
                    step=step,
                    loading_state=loading_state,
                    top_xcm=top_xcm,
                    top_ycm=top_ycm,
                    bottom_xcm=bottom_xcm,
                    bottom_ycm=bottom_ycm,
                    observables=observables,
                    stress_state=stress_state,
                    converged=converged,
                )
                write_run_metadata_atomic(metadata_path, run_metadata)

            if step % config.output.dump_every == 0:
                structure_path = structures_dir / f"gb_shear_step_{step:04d}.cfg"
                _write_step_structure(lmp, structure_path, element_map=element_map)
                if rank == 0 and run_metadata is not None:
                    run_metadata["frames"].append(
                        {
                            "step": step,
                            "path": structure_path.relative_to(
                                output_dir
                            ).as_posix(),
                        }
                    )
                    write_run_metadata_atomic(metadata_path, run_metadata)

        _write_step_structure(lmp, output_dir / SHEARED_CFG, element_map=element_map)
        lmp.command(f"write_data {output_dir / SHEARED_LMP} nocoeff")
        if rank == 0 and run_metadata is not None:
            finalize_run_metadata(run_metadata, status="completed")
            write_run_metadata_atomic(metadata_path, run_metadata)
    except BaseException as exc:
        if rank == 0 and run_metadata is not None:
            try:
                finalize_run_metadata(run_metadata, status="failed", error=str(exc))
                write_run_metadata_atomic(metadata_path, run_metadata)
            except Exception:
                pass
        raise
    finally:
        if curve_handle is not None:
            curve_handle.close()
        lmp.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run quasi-static GB shear with LAMMPS."
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    if not output_dir.is_dir():
        raise FileNotFoundError(f"Output directory does not exist: {output_dir}")
    config_path = args.config.resolve()
    config = load_gb_shear_config(config_path)
    run_gb_shear(config, output_dir, project_root=args.project_root.resolve())


if __name__ == "__main__":
    main()
