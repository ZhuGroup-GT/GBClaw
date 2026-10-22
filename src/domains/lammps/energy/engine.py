"""Calculate grain-boundary energy with relaxed bulk/surface references.

The bicrystal construction follows :mod:`domains.lammps.minimize.engine`.  The final
excess energy subtracts each grain's bulk contribution and one free-surface
contribution from the relaxed bicrystal, then divides by the periodic x-z
interface area.

Run in the configured LAMMPS Conda environment, for example with two MPI ranks::

    mpirun --map-by core --bind-to core -n 2 python -u src/domains/lammps/energy/engine.py \
        --output-dir <dir> --config <input.json>

Adjust the rank count to the cores available for the job.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Run as a script in the LAMMPS environment: import packages from src/, never
# from this workflow directory, whose module names (config, tool) are generic.
_HERE = Path(__file__).resolve().parent
_SRC_ROOT = _HERE.parents[2]
sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != _HERE]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from domains.lammps.common.engine_utils import group_count, mpi_barrier, mpi_rank, run_staged_relaxation, write_cfg
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
from domains.lammps.energy.config import (
    GB_ENERGY_TOPOLOGY,
    GBEnergyConfig,
    load_gb_energy_config,
)
from domains.lammps.minimize.engine import grain_extent, orient_cmd, x_box_length
from domains.lammps.common.live_lammps import lammps


EV_PER_A2_TO_J_PER_M2 = 16.021766208
TOP_GROUP = "top_grain"
BOTTOM_GROUP = "bottom_grain"
_COUNT_PREFIX = "__gb_energy_count_"


@dataclass(frozen=True)
class BulkEnergyResult:
    energy_per_atom_eV: float
    energy_for_bicrystal_atoms_eV: float


@dataclass(frozen=True)
class SurfaceEnergyResult:
    two_surface_excess_eV: float
    one_surface_excess_eV: float
    energy_eV_per_A2: float
    energy_J_per_m2: float


@dataclass(frozen=True)
class GrainBoundaryEnergyResult:
    excess_eV: float
    energy_eV_per_A2: float
    energy_J_per_m2: float


def _prepare_output_directories(output_dir: Path) -> None:
    if mpi_rank() == 0:
        (output_dir / "bicrystal").mkdir(parents=True, exist_ok=True)
        for grain_name in ("top", "bottom"):
            for mode in ("bulk", "surface"):
                (output_dir / ENERGY_REFERENCES_DIR / f"{grain_name}_{mode}").mkdir(
                    parents=True,
                    exist_ok=True,
                )
    mpi_barrier()


def make_lammps(log_path: Path, cfg: GBEnergyConfig) -> lammps:
    lmp = lammps(cmdargs=["-log", str(log_path), "-nonbuf"])
    lmp.command("units metal")
    lmp.command("atom_style atomic")
    lmp.command("dimension 3")
    lmp.command(f"boundary {cfg.simulation.boundary}")
    return lmp


def apply_mass_and_potential(
    lmp: lammps,
    cfg: GBEnergyConfig,
    *,
    potential_file: Path,
) -> None:
    lmp.command(f"mass 1 {cfg.material.mass}")
    lmp.command(f"mass 2 {cfg.material.mass}")
    lmp.command("neighbor 1.0 bin")
    lmp.command("neigh_modify delay 1 every 1 check yes")
    lmp.command(f"pair_style {cfg.potential.pair_style}")
    lmp.command(
        f"pair_coeff * * {potential_file} {cfg.material.element} {cfg.material.element}"
    )


def group_extents(lmp: lammps, group_name: str) -> dict[str, float]:
    compute_ids = {
        key: f"__gb_energy_{group_name}_{key}"
        for key in ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")
    }
    dimensions = {
        "xmin": "x",
        "xmax": "x",
        "ymin": "y",
        "ymax": "y",
        "zmin": "z",
        "zmax": "z",
    }
    try:
        for key, dimension in dimensions.items():
            operation = "min" if key.endswith("min") else "max"
            lmp.command(
                f"compute {compute_ids[key]} {group_name} reduce {operation} {dimension}"
            )
        lmp.command("run 0")
        values = {
            key: float(lmp.extract_compute(compute_id, 0, 0))
            for key, compute_id in compute_ids.items()
        }
    finally:
        for compute_id in compute_ids.values():
            try:
                lmp.command(f"uncompute {compute_id}")
            except Exception:
                pass
    values.update(
        {
            "xspan": values["xmax"] - values["xmin"],
            "yspan": values["ymax"] - values["ymin"],
            "zspan": values["zmax"] - values["zmin"],
        }
    )
    return values


def thermo_state(lmp: lammps) -> dict[str, float | int]:
    lmp.command("run 0")
    return {
        "pe": float(lmp.get_thermo("pe")),
        "lx": float(lmp.get_thermo("lx")),
        "ly": float(lmp.get_thermo("ly")),
        "lz": float(lmp.get_thermo("lz")),
        "pxx": float(lmp.get_thermo("pxx")),
        "pyy": float(lmp.get_thermo("pyy")),
        "pzz": float(lmp.get_thermo("pzz")),
        "fmax": float(lmp.get_thermo("fmax")),
        "natoms": int(lmp.get_natoms()),
    }


def run_minimization(
    lmp: lammps,
    cfg: GBEnergyConfig,
    *,
    dump_path: Path,
    relax_axes: tuple[str, ...],
) -> None:
    sim = cfg.simulation
    lmp.command(f"min_style {sim.min_style}")
    lmp.command(f"thermo {cfg.output.thermo_every}")
    lmp.command(
        "thermo_style custom step pe lx ly lz pxx pyy pzz pxy pxz pyz fmax fnorm"
    )
    lmp.command("thermo_modify flush yes")
    lmp.command(
        f"dump gb_energy_min all custom {cfg.output.dump_every} {dump_path} "
        "id type xs ys zs ix iy iz"
    )
    lmp.command("dump_modify gb_energy_min sort id")
    relax_command = " ".join(f"{axis} 0.0" for axis in relax_axes)
    try:
        for _ in range(sim.relax_cycles):
            lmp.command(
                f"minimize {sim.min_etol} {sim.min_ftol} "
                f"{sim.min_maxiter} {sim.min_maxeval}"
            )
            if relax_command:
                lmp.command(f"fix gb_energy_box all box/relax {relax_command} couple none")
                lmp.command(
                    f"minimize {sim.min_etol} {sim.min_ftol} "
                    f"{sim.min_maxiter} {sim.min_maxeval}"
                )
                lmp.command("unfix gb_energy_box")
    finally:
        try:
            lmp.command("undump gb_energy_min")
        except Exception:
            pass


def _region_bounds(
    *,
    lx: float,
    lz: float,
    ylo: float,
    yhi: float,
    eps: float,
) -> dict[str, float]:
    return {
        "xlo": -lx / 2.0 + eps,
        "xhi": lx / 2.0 + eps,
        "ylo": ylo,
        "yhi": yhi,
        "zlo": -lz / 2.0 + eps,
        "zhi": lz / 2.0 + eps,
    }


def _expanded(bounds: dict[str, float], padding: float) -> dict[str, float]:
    return {
        "xlo": bounds["xlo"] - padding,
        "xhi": bounds["xhi"] + padding,
        "ylo": bounds["ylo"] - padding,
        "yhi": bounds["yhi"] + padding,
        "zlo": bounds["zlo"] - padding,
        "zhi": bounds["zhi"] + padding,
    }


def _create_box_from_bounds(lmp: lammps, bounds: dict[str, float]) -> None:
    lmp.command(
        "region sim_box block "
        f"{bounds['xlo']} {bounds['xhi']} "
        f"{bounds['ylo']} {bounds['yhi']} "
        f"{bounds['zlo']} {bounds['zhi']} units box"
    )
    lmp.command("create_box 2 sim_box")


def _create_grain(
    lmp: lammps,
    cfg: GBEnergyConfig,
    *,
    grain: Any,
    atom_type: int,
    bounds: dict[str, float],
    name: str,
) -> None:
    lmp.command(
        f"lattice {cfg.material.lattice} {cfg.simulation.a0} "
        f"{orient_cmd(grain.orientation.as_dict())}"
    )
    lmp.command(
        f"region {name}_region block "
        f"{bounds['xlo']} {bounds['xhi']} "
        f"{bounds['ylo']} {bounds['yhi']} "
        f"{bounds['zlo']} {bounds['zhi']} units box"
    )
    lmp.command(f"create_atoms {atom_type} region {name}_region")
    lmp.command(f"group {name} region {name}_region")


def _write_structure_pair(lmp: lammps, cfg_path: Path, lmp_path: Path, *, element: str) -> None:
    # Both atom types identify grains of the same configured chemical element.
    write_cfg(lmp, "all", cfg_path, f"{element} {element}")
    lmp.command(f"write_data {lmp_path} nocoeff")


def _write_surface_slabs(
    lmp: lammps,
    output_dir: Path,
    *,
    thickness: float,
    element: str,
) -> dict[str, float]:
    extents = group_extents(lmp, "all")
    top_limit = extents["ymax"] - thickness
    bottom_limit = extents["ymin"] + thickness
    if top_limit <= bottom_limit:
        raise ValueError(
            f"boundary slab thickness {thickness} is invalid for atom y range "
            f"[{extents['ymin']}, {extents['ymax']}]"
        )
    lmp.command(
        f"region top_surface_slab block INF INF {top_limit} INF INF INF units box"
    )
    lmp.command(
        f"region bottom_surface_slab block INF INF INF {bottom_limit} INF INF units box"
    )
    lmp.command("group top_surface_atoms region top_surface_slab")
    lmp.command("group bottom_surface_atoms region bottom_surface_slab")
    write_cfg(lmp, "top_surface_atoms", output_dir / ENERGY_TOP_SLAB_CFG, f"{element} {element}")
    write_cfg(lmp, "bottom_surface_atoms", output_dir / ENERGY_BOTTOM_SLAB_CFG, f"{element} {element}")
    return {
        "atom_ymin": extents["ymin"],
        "atom_ymax": extents["ymax"],
        "top_limit": top_limit,
        "bottom_limit": bottom_limit,
        "thickness": thickness,
    }


def build_and_relax_bicrystal(
    cfg: GBEnergyConfig,
    output_dir: Path,
    *,
    potential_file: Path,
) -> dict[str, Any]:
    sim = cfg.simulation
    top = cfg.grains.top
    bottom = cfg.grains.bottom
    lx_top, ly_top, lz_top = grain_extent(
        top.orientation.as_dict(), top.size, sim.a0
    )
    lx_bottom, ly_bottom, lz_bottom = grain_extent(
        bottom.orientation.as_dict(), bottom.size, sim.a0
    )
    lx_box = x_box_length(lx_top, lx_bottom, sim)
    lz_box = max(lz_top, lz_bottom) + 2.0 * sim.y_eps
    ylo_box = -ly_bottom - sim.y_vacuum
    yhi_box = ly_top + sim.y_vacuum
    box_bounds = {
        "xlo": -lx_box / 2.0,
        "xhi": lx_box / 2.0,
        "ylo": ylo_box,
        "yhi": yhi_box,
        "zlo": -lz_box / 2.0,
        "zhi": lz_box / 2.0,
    }
    grain_bounds = {
        "top": _region_bounds(
            lx=lx_top,
            lz=lz_top,
            ylo=sim.y_eps,
            yhi=ly_top + sim.y_eps,
            eps=sim.y_eps,
        ),
        "bottom": _region_bounds(
            lx=lx_bottom,
            lz=lz_bottom,
            ylo=-ly_bottom - sim.y_eps,
            yhi=-sim.y_eps,
            eps=sim.y_eps,
        ),
    }
    lmp = make_lammps(output_dir / ENERGY_BICRYSTAL_LOG, cfg)
    try:
        _create_box_from_bounds(lmp, box_bounds)
        _create_grain(
            lmp,
            cfg,
            grain=top,
            atom_type=1,
            bounds=grain_bounds["top"],
            name=TOP_GROUP,
        )
        _create_grain(
            lmp,
            cfg,
            grain=bottom,
            atom_type=2,
            bounds=grain_bounds["bottom"],
            name=BOTTOM_GROUP,
        )
        apply_mass_and_potential(lmp, cfg, potential_file=potential_file)

        before = {
            "top": group_count(lmp, TOP_GROUP, variable_prefix=_COUNT_PREFIX),
            "bottom": group_count(lmp, BOTTOM_GROUP, variable_prefix=_COUNT_PREFIX),
        }
        overlap_cutoff = 0.3 * sim.a0
        lmp.command(f"delete_atoms overlap {overlap_cutoff} all all")
        after = {
            "top": group_count(lmp, TOP_GROUP, variable_prefix=_COUNT_PREFIX),
            "bottom": group_count(lmp, BOTTOM_GROUP, variable_prefix=_COUNT_PREFIX),
        }
        overlap = {
            "cutoff_angstrom": overlap_cutoff,
            "before": before,
            "after": after,
            "deleted": {
                grain_name: before[grain_name] - after[grain_name]
                for grain_name in ("top", "bottom")
            },
        }
        _write_structure_pair(
            lmp,
            output_dir / ENERGY_BICRYSTAL_INITIAL_CFG,
            output_dir / ENERGY_BICRYSTAL_INITIAL_LMP,
            element=cfg.material.element,
        )
        run_staged_relaxation(lmp, cfg.simulation, top_group=TOP_GROUP, bottom_group=BOTTOM_GROUP,
                              bottom_fix="freeze_bottom")
        run_minimization(
            lmp,
            cfg,
            dump_path=output_dir / ENERGY_BICRYSTAL_TRAJECTORY,
            relax_axes=("x", "y", "z"),
        )
        _write_structure_pair(
            lmp,
            output_dir / ENERGY_BICRYSTAL_MINIMIZED_CFG,
            output_dir / ENERGY_BICRYSTAL_MINIMIZED_LMP,
            element=cfg.material.element,
        )
        slab = _write_surface_slabs(
            lmp,
            output_dir,
            thickness=sim.boundary_slab_thickness,
            element=cfg.material.element,
        )
        state = thermo_state(lmp)
        counts = {
            "top": group_count(lmp, TOP_GROUP, variable_prefix=_COUNT_PREFIX),
            "bottom": group_count(lmp, BOTTOM_GROUP, variable_prefix=_COUNT_PREFIX),
        }
        if counts["top"] <= 0 or counts["bottom"] <= 0:
            raise RuntimeError("both bicrystal grain groups must be non-empty")
        if counts["top"] + counts["bottom"] != state["natoms"]:
            raise RuntimeError(
                "bicrystal grain-group atom counts do not equal the total atom count"
            )
        return {
            "state": state,
            "counts": counts,
            "extents": {
                "top": group_extents(lmp, TOP_GROUP),
                "bottom": group_extents(lmp, BOTTOM_GROUP),
            },
            "slab": slab,
            "overlap": overlap,
            "initial_lengths": {
                "top": {"lx": lx_top, "ly": ly_top, "lz": lz_top},
                "bottom": {"lx": lx_bottom, "ly": ly_bottom, "lz": lz_bottom},
                "box": {
                    "lx": lx_box,
                    "ly": yhi_box - ylo_box,
                    "lz": lz_box,
                },
            },
            "region_bounds": grain_bounds,
        }
    finally:
        lmp.close()


def run_single_grain_reference(
    cfg: GBEnergyConfig,
    output_dir: Path,
    *,
    potential_file: Path,
    grain_name: str,
    mode: str,
    lx_box: float,
    ly_box: float,
    lz_box: float,
    initial_region_bounds: dict[str, float],
    reference_y_length: float,
) -> dict[str, float | int]:
    if grain_name not in {"top", "bottom"}:
        raise ValueError(f"unsupported grain name: {grain_name}")
    if mode not in {"bulk", "surface"}:
        raise ValueError(f"unsupported reference mode: {mode}")
    if reference_y_length <= 0.0:
        raise ValueError("reference y length must be positive")

    reference_dir = output_dir / ENERGY_REFERENCES_DIR / f"{grain_name}_{mode}"
    lmp = make_lammps(reference_dir / "log.lammps", cfg)
    try:
        _create_box_from_bounds(lmp, _expanded(initial_region_bounds, cfg.simulation.y_eps))
        grain = cfg.grains.top if grain_name == "top" else cfg.grains.bottom
        _create_grain(
            lmp,
            cfg,
            grain=grain,
            atom_type=1 if grain_name == "top" else 2,
            bounds=initial_region_bounds,
            name="reference_grain",
        )
        compact = _expanded(initial_region_bounds, 1.0e-8)
        lmp.command(
            "change_box all "
            f"x final {compact['xlo']} {compact['xhi']} "
            f"y final {compact['ylo']} {compact['yhi']} "
            f"z final {compact['zlo']} {compact['zhi']} units box"
        )
        lmp.command(
            "change_box all "
            f"x final {-lx_box / 2.0} {lx_box / 2.0} "
            f"y final {-reference_y_length / 2.0} {reference_y_length / 2.0} "
            f"z final {-lz_box / 2.0} {lz_box / 2.0} remap units box"
        )
        if mode == "surface":
            if ly_box <= reference_y_length:
                raise ValueError(
                    f"{grain_name} surface reference has no y vacuum: "
                    f"box Ly={ly_box}, grain Ly={reference_y_length}"
                )
            lmp.command(
                f"change_box all y final {-ly_box / 2.0} {ly_box / 2.0} units box"
            )
        apply_mass_and_potential(lmp, cfg, potential_file=potential_file)
        lmp.command("run 0")
        _write_structure_pair(
            lmp,
            reference_dir / "initial.cfg",
            reference_dir / "initial.lmp",
            element=cfg.material.element,
        )
        run_minimization(
            lmp,
            cfg,
            dump_path=reference_dir / "minimization.lammpstrj",
            relax_axes=("y",),
        )
        _write_structure_pair(
            lmp,
            reference_dir / "minimized.cfg",
            reference_dir / "minimized.lmp",
            element=cfg.material.element,
        )
        return thermo_state(lmp)
    finally:
        lmp.close()


def run_reference_calculations(
    cfg: GBEnergyConfig,
    output_dir: Path,
    *,
    potential_file: Path,
    bicrystal: dict[str, Any],
) -> tuple[dict[str, Any], float, dict[str, float]]:
    state = bicrystal["state"]
    y_box_scale = float(state["ly"]) / bicrystal["initial_lengths"]["box"]["ly"]
    reference_y_lengths = {
        grain_name: bicrystal["initial_lengths"][grain_name]["ly"] * y_box_scale
        for grain_name in ("top", "bottom")
    }
    references: dict[str, Any] = {}
    for grain_name in ("top", "bottom"):
        common = {
            "cfg": cfg,
            "output_dir": output_dir,
            "potential_file": potential_file,
            "grain_name": grain_name,
            "lx_box": float(state["lx"]),
            "lz_box": float(state["lz"]),
            "initial_region_bounds": bicrystal["region_bounds"][grain_name],
            "reference_y_length": reference_y_lengths[grain_name],
        }
        references[grain_name] = {
            "bulk": run_single_grain_reference(
                mode="bulk",
                ly_box=reference_y_lengths[grain_name],
                **common,
            ),
            "surface": run_single_grain_reference(
                mode="surface",
                ly_box=float(state["ly"]),
                **common,
            ),
        }
    return references, y_box_scale, reference_y_lengths


def calculate_energies(
    bicrystal: dict[str, Any],
    references: dict[str, Any],
) -> tuple[
    dict[str, BulkEnergyResult],
    dict[str, SurfaceEnergyResult],
    GrainBoundaryEnergyResult,
    float,
]:
    area = float(bicrystal["state"]["lx"]) * float(bicrystal["state"]["lz"])
    if area <= 0.0:
        raise RuntimeError(f"interface area must be positive, got {area}")

    bulk: dict[str, BulkEnergyResult] = {}
    surface: dict[str, SurfaceEnergyResult] = {}
    for grain_name in ("top", "bottom"):
        bulk_reference = references[grain_name]["bulk"]
        if int(bulk_reference["natoms"]) <= 0:
            raise RuntimeError(f"{grain_name} bulk reference contains no atoms")
        energy_per_atom = float(bulk_reference["pe"]) / int(bulk_reference["natoms"])
        bulk[grain_name] = BulkEnergyResult(
            energy_per_atom_eV=energy_per_atom,
            energy_for_bicrystal_atoms_eV=(
                energy_per_atom * int(bicrystal["counts"][grain_name])
            ),
        )

        surface_reference = references[grain_name]["surface"]
        two_surface_excess = (
            float(surface_reference["pe"])
            - int(surface_reference["natoms"]) * energy_per_atom
        )
        one_surface_excess = two_surface_excess / 2.0
        energy_ev_a2 = one_surface_excess / area
        surface[grain_name] = SurfaceEnergyResult(
            two_surface_excess_eV=two_surface_excess,
            one_surface_excess_eV=one_surface_excess,
            energy_eV_per_A2=energy_ev_a2,
            energy_J_per_m2=energy_ev_a2 * EV_PER_A2_TO_J_PER_M2,
        )

    excess = float(bicrystal["state"]["pe"])
    excess -= sum(item.energy_for_bicrystal_atoms_eV for item in bulk.values())
    excess -= sum(item.one_surface_excess_eV for item in surface.values())
    energy_ev_a2 = excess / area
    gb = GrainBoundaryEnergyResult(
        excess_eV=excess,
        energy_eV_per_A2=energy_ev_a2,
        energy_J_per_m2=energy_ev_a2 * EV_PER_A2_TO_J_PER_M2,
    )
    return bulk, surface, gb, area


def _summary_rows(metadata: dict[str, Any]) -> list[tuple[str, Any, str]]:
    bicrystal = metadata["bicrystal"]
    results = metadata["results"]
    topology = metadata["topology"]
    rows: list[tuple[str, Any, str]] = [
        ("cell_topology", topology["name"], ""),
        ("gb_interface_count", topology["interface_count"], "interfaces"),
        ("free_surface_count", topology["free_surface_count"], "surfaces"),
        ("gb_energy_denominator", topology["energy_denominator"], ""),
        ("bicrystal_energy", bicrystal["state"]["pe"], "eV"),
        ("bicrystal_natoms", bicrystal["state"]["natoms"], "atoms"),
        ("top_atoms_in_bicrystal", bicrystal["counts"]["top"], "atoms"),
        ("bottom_atoms_in_bicrystal", bicrystal["counts"]["bottom"], "atoms"),
        ("relaxed_lx", bicrystal["state"]["lx"], "Angstrom"),
        ("relaxed_ly", bicrystal["state"]["ly"], "Angstrom"),
        ("relaxed_lz", bicrystal["state"]["lz"], "Angstrom"),
        ("interface_area", results["interface_area_A2"], "Angstrom^2"),
    ]
    for grain_name in ("top", "bottom"):
        bulk = results["bulk"][grain_name]
        surface = results["surface"][grain_name]
        reference = metadata["references"][grain_name]
        rows.extend(
            [
                (
                    f"{grain_name}_bulk_ref_natoms",
                    reference["bulk"]["natoms"],
                    "atoms",
                ),
                (f"{grain_name}_bulk_ref_energy", reference["bulk"]["pe"], "eV"),
                (
                    f"{grain_name}_bulk_energy_per_atom",
                    bulk["energy_per_atom_eV"],
                    "eV/atom",
                ),
                (
                    f"{grain_name}_surface_ref_natoms",
                    reference["surface"]["natoms"],
                    "atoms",
                ),
                (
                    f"{grain_name}_surface_ref_energy",
                    reference["surface"]["pe"],
                    "eV",
                ),
                (
                    f"{grain_name}_surface_energy_eV_per_A2",
                    surface["energy_eV_per_A2"],
                    "eV/Angstrom^2",
                ),
                (
                    f"{grain_name}_surface_energy_J_per_m2",
                    surface["energy_J_per_m2"],
                    "J/m^2",
                ),
            ]
        )
    rows.extend(
        [
            ("gb_excess_energy", results["gb_energy"]["excess_eV"], "eV"),
            (
                "gb_energy_eV_per_A2",
                results["gb_energy"]["energy_eV_per_A2"],
                "eV/Angstrom^2",
            ),
            (
                "gb_energy_J_per_m2",
                results["gb_energy"]["energy_J_per_m2"],
                "J/m^2",
            ),
        ]
    )
    return rows


def write_results(output_dir: Path, metadata: dict[str, Any]) -> None:
    if mpi_rank() == 0:
        write_json_atomic(output_dir / ENERGY_RUN_METADATA, metadata, fsync=False)
        with (output_dir / ENERGY_SUMMARY_CSV).open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(["quantity", "value", "unit"])
            for quantity, value, unit in _summary_rows(metadata):
                formatted = f"{value:.16e}" if isinstance(value, float) else value
                writer.writerow([quantity, formatted, unit])
        result = metadata["results"]["gb_energy"]
        print(
            "GB energy: "
            f"{result['energy_J_per_m2']:.10g} J/m^2 "
            f"({result['energy_eV_per_A2']:.10g} eV/Angstrom^2)"
        )
    mpi_barrier()


def run_gb_energy(
    cfg: GBEnergyConfig,
    output_dir: Path,
    *,
    potential_file: Path,
) -> dict[str, Any]:
    _prepare_output_directories(output_dir)
    bicrystal = build_and_relax_bicrystal(
        cfg,
        output_dir,
        potential_file=potential_file,
    )
    references, y_box_scale, reference_y_lengths = run_reference_calculations(
        cfg,
        output_dir,
        potential_file=potential_file,
        bicrystal=bicrystal,
    )
    bulk, surface, gb_energy, interface_area = calculate_energies(
        bicrystal,
        references,
    )
    metadata = {
        "version": 1,
        "method": "bulk_and_free_surface_subtraction",
        "topology": dict(GB_ENERGY_TOPOLOGY),
        "units": {
            "energy": "eV",
            "length": "Angstrom",
            "area": "Angstrom^2",
            "surface_and_gb_energy": ["eV/Angstrom^2", "J/m^2"],
        },
        "bicrystal": bicrystal,
        "references": references,
        "reference_scaling": {
            "y_box_scale": y_box_scale,
            "reference_y_lengths_angstrom": reference_y_lengths,
        },
        "results": {
            "interface_area_A2": interface_area,
            "bulk": {key: asdict(value) for key, value in bulk.items()},
            "surface": {key: asdict(value) for key, value in surface.items()},
            "gb_energy": asdict(gb_energy),
        },
    }
    write_results(output_dir, metadata)
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calculate a relaxed bicrystal grain-boundary energy."
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    if not output_dir.is_dir():
        raise FileNotFoundError(f"output directory does not exist: {output_dir}")
    config_path = args.config.resolve()
    cfg = load_gb_energy_config(config_path)
    potential_file = cfg.potential.resolve_path(config_dir=config_path.parent)
    if not potential_file.is_file():
        raise FileNotFoundError(f"potential file not found: {potential_file}")
    run_gb_energy(cfg, output_dir, potential_file=potential_file)


if __name__ == "__main__":
    main()
