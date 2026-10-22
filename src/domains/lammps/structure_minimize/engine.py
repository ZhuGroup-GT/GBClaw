"""One fixed-cell minimization of existing atomic LAMMPS data, without loading.

The configuration and data inspector use only the standard library. LAMMPS is
imported only when execution starts, so the application can validate inputs in
its own environment.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

# Run as a script in the LAMMPS environment: import packages from src/, never
# from this workflow directory, whose module names (config, tool) are generic.
_HERE = Path(__file__).resolve().parent
_SRC_ROOT = _HERE.parents[2]
sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != _HERE]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from core.util.io import sha256_file
from domains.lammps.common.engine_utils import write_cfg
from domains.lammps.structure_minimize.config import (
    StructureMinimizeConfig,
    load_structure_minimize_config,
)


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("LAMMPS data must contain finite coordinates and masses")
    return result


def inspect_atomic_data(path: Path | str) -> dict[str, Any]:
    """Inspect numeric atomic data with orthogonal or restricted triclinic cell.

    IDs need not be contiguous. Element identity is deliberately not inferred
    from masses or from the numeric type labels.
    """
    natoms = atom_types = None
    cell = {"xy": 0.0, "xz": 0.0, "yz": 0.0}
    masses: dict[int, float] = {}
    atoms: dict[int, dict[str, Any]] = {}
    section = "header"
    allowed_sections = {"Masses", "Atoms", "Velocities", "Pair Coeffs", "PairIJ Coeffs"}
    with Path(path).open(encoding="utf-8") as handle:
        title = next(handle, "")  # LAMMPS treats the first line as a title.
        units = re.search(r"\bunits\s*=\s*([A-Za-z]+)", title)
        if units and units.group(1) != "metal":
            raise ValueError("structure_minimize requires data in LAMMPS metal units")
        for line_number, line in enumerate(handle, start=2):
            content, _, comment = line.partition("#")
            content = content.strip()
            if not content:
                continue
            fields = content.split()
            if content in allowed_sections:
                section = content
                if section == "Atoms" and comment.strip() and comment.strip() != "atomic":
                    raise ValueError("structure_minimize accepts only Atoms # atomic")
                continue
            if section == "header":
                if len(fields) == 2 and fields[-1] == "atoms":
                    natoms = int(fields[0])
                elif len(fields) == 3 and fields[-2:] == ["atom", "types"]:
                    atom_types = int(fields[0])
                elif len(fields) == 4 and fields[-2:] in (["xlo", "xhi"], ["ylo", "yhi"], ["zlo", "zhi"]):
                    cell[fields[-2]], cell[fields[-1]] = map(_finite_float, fields[:2])
                elif len(fields) == 6 and fields[-3:] == ["xy", "xz", "yz"]:
                    cell.update(zip(("xy", "xz", "yz"), map(_finite_float, fields[:3])))
                elif len(fields) >= 2 and fields[-1] in {"bonds", "angles", "dihedrals", "impropers"} and int(fields[0]) == 0:
                    continue
                else:
                    raise ValueError(f"unsupported atomic data header at line {line_number}: {content}")
            elif section == "Masses":
                if len(fields) != 2:
                    raise ValueError(f"invalid Masses row at line {line_number}")
                type_id, mass = int(fields[0]), _finite_float(fields[1])
                if type_id in masses or type_id <= 0 or mass <= 0:
                    raise ValueError("Masses must contain unique positive type IDs and masses")
                masses[type_id] = mass
            elif section == "Atoms":
                if len(fields) not in {5, 8}:
                    raise ValueError(f"Atoms must use atomic style (5 or 8 fields), line {line_number}")
                atom_id, atom_type = int(fields[0]), int(fields[1])
                if atom_id <= 0 or atom_id in atoms:
                    raise ValueError("atom IDs must be positive and unique")
                atoms[atom_id] = {
                    "type": atom_type,
                    "position": list(map(_finite_float, fields[2:5])),
                    "image": list(map(int, fields[5:8])) if len(fields) == 8 else [0, 0, 0],
                }
            elif not re.match(r"^[+-]?\d", content):
                raise ValueError(f"unsupported atomic data section at line {line_number}: {content}")
    if natoms is None or natoms <= 0 or len(atoms) != natoms:
        raise ValueError("Atoms rows must match the positive atom count in the header")
    if atom_types is None or atom_types <= 0 or set(masses) != set(range(1, atom_types + 1)):
        raise ValueError("Masses must define every declared atom type")
    if any(atom["type"] not in masses for atom in atoms.values()):
        raise ValueError("atom type is outside the declared type range")
    for axis in "xyz":
        low, high = cell.get(f"{axis}lo"), cell.get(f"{axis}hi")
        if low is None or high is None or high <= low:
            raise ValueError("data must define three finite, positive cell lengths")
    return {
        "natoms": natoms, "atom_types": atom_types, "ids": sorted(atoms),
        "atoms": atoms, "masses": masses, "cell": cell,
    }


def _quote_path(path: Path | str) -> str:
    text = str(Path(path).resolve())
    # LAMMPS expands variables/wildcards even in some quoted arguments.
    if any(character in text for character in ('"', "'", "$", "\n", "\r", "*", "%", "\\")):
        raise ValueError("LAMMPS file paths cannot contain quotes, variables, newlines or wildcards")
    return f'"{text}"'


def _unwrapped(atom: dict[str, Any], cell: dict[str, float]) -> list[float]:
    x, y, z = atom["position"]
    ix, iy, iz = atom["image"]
    return [
        x + ix * (cell["xhi"] - cell["xlo"]) + iy * cell["xy"] + iz * cell["xz"],
        y + iy * (cell["yhi"] - cell["ylo"]) + iz * cell["yz"],
        z + iz * (cell["zhi"] - cell["zlo"]),
    ]


def _state(lmp: Any) -> dict[str, Any]:
    lmp.command("run 0")
    return {
        "natoms": int(lmp.get_natoms()),
        "energy_eV": float(lmp.get_thermo("pe")),
        "force_max_eV_per_A": float(lmp.get_thermo("fmax")),
        "force_two_norm_eV_per_A": float(lmp.get_thermo("fnorm")),
        "cell": {key: float(lmp.get_thermo(key)) for key in (
            "xlo", "xhi", "ylo", "yhi", "zlo", "zhi", "xy", "xz", "yz"
        )},
    }


def _minimization_stats(log_path: Path) -> dict[str, Any]:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    blocks = text.split("Minimization stats:")[1:]
    if len(blocks) != 1:
        raise RuntimeError(f"expected exactly one minimization summary, found {len(blocks)}")
    stopping = re.search(r"Stopping criterion\s*=\s*([^\n]+)", blocks[0])
    iterations = re.search(r"Iterations, force evaluations\s*=\s*(\d+)\s+(\d+)", blocks[0])
    if not stopping:
        raise RuntimeError("LAMMPS did not report a minimization stopping criterion")
    criterion = stopping.group(1).strip()
    return {
        "minimization_count": 1,
        "stopping_criterion": criterion,
        "converged": criterion.casefold() in {"force tolerance", "energy tolerance"},
        "iterations": int(iterations.group(1)) if iterations else None,
        "force_evaluations": int(iterations.group(2)) if iterations else None,
    }


def run_structure_minimize(
    cfg: StructureMinimizeConfig,
    output_dir: Path,
    *,
    data_file: Path,
    potential_file: Path,
) -> dict[str, Any] | None:
    """Execute exactly one minimize command; all MPI ranks must call this."""
    if sha256_file(data_file) != cfg.source["sha256"]:
        raise ValueError("source data SHA-256 differs from the configuration")
    if sha256_file(potential_file) != cfg.potential["sha256"]:
        raise ValueError("potential SHA-256 differs from the configuration")
    source = inspect_atomic_data(data_file)
    if len(cfg.potential["elements"]) != source["atom_types"]:
        raise ValueError("potential.elements must match the declared data atom types exactly")
    fixed_ids = cfg.constraints["fixed_ids"]
    unknown = set(fixed_ids) - set(source["ids"])
    if unknown:
        raise ValueError(f"fixed_ids contains atoms absent from the data: {sorted(unknown)[:20]}")
    # Validate paths before importing LAMMPS or creating files.
    quoted_data, quoted_potential = _quote_path(data_file), _quote_path(potential_file)
    _quote_path(output_dir)
    from domains.lammps.common.live_lammps import lammps

    try:
        from mpi4py import MPI
        comm = MPI.COMM_WORLD
    except ImportError:
        comm = None
    rank = comm.Get_rank() if comm is not None else 0
    output_dir.mkdir(parents=True, exist_ok=True)
    if comm is not None:
        comm.Barrier()
    log_path = output_dir / "log.lammps"
    lmp = lammps(cmdargs=["-log", str(log_path), "-nonbuf"])
    try:
        lmp.command("units metal")
        lmp.command("atom_style atomic")
        lmp.command("dimension 3")
        lmp.command(f"boundary {cfg.simulation['boundary']}")
        lmp.command(f"read_data {quoted_data} nocoeff")
        lmp.command(f"pair_style {cfg.potential['pair_style']}")
        lmp.command(f"pair_coeff * * {quoted_potential} {' '.join(cfg.potential['elements'])}")
        # EAM-family pair_coeff may replace masses with values from the potential.
        # The selected frame remains the authority for its per-type masses.
        for type_id, mass in source["masses"].items():
            lmp.command(f"mass {type_id} {mass:.17g}")
        lmp.command("neighbor 1.0 bin")
        lmp.command("neigh_modify delay 0 every 1 check yes")
        # Inertial minimizers must not inherit loading velocities from the frame.
        lmp.command("velocity all set 0.0 0.0 0.0")
        if fixed_ids:
            lmp.command("group fixed_atoms empty")
            for start in range(0, len(fixed_ids), 512):
                lmp.command("group fixed_atoms id " + " ".join(map(str, fixed_ids[start:start + 512])))
            lmp.command("fix freeze_atoms fixed_atoms setforce 0.0 0.0 0.0")
        lmp.command("thermo 100")
        lmp.command("thermo_style custom step atoms pe fmax fnorm xlo xhi ylo yhi zlo zhi xy xz yz")
        lmp.command("thermo_modify flush yes lost error norm no")
        before = _state(lmp)
        write_cfg(lmp, "all", _quote_path(output_dir / 'initial.cfg'), ' '.join(cfg.potential['elements']),
                  modify="sort id")
        lmp.command(
            f"dump minimize_trajectory all custom 100 {_quote_path(output_dir / 'minimize.lammpstrj')} "
            "id type x y z ix iy iz fx fy fz"
        )
        lmp.command("dump_modify minimize_trajectory sort id first yes")
        lmp.command(f"min_style {cfg.simulation['min_style']}")
        lmp.command(
            f"minimize {cfg.simulation['min_etol']} {cfg.simulation['min_ftol']} "
            f"{cfg.simulation['min_maxiter']} {cfg.simulation['min_maxeval']}"
        )
        lmp.command("undump minimize_trajectory")
        after = _state(lmp)
        # Retain the final frame even when the final iteration is not a multiple
        # of the trajectory interval.
        lmp.command(
            f"write_dump all custom {_quote_path(output_dir / 'minimize.lammpstrj')} "
            "id type x y z ix iy iz fx fy fz modify append yes sort id"
        )
        lmp.command(f"write_data {_quote_path(output_dir / 'minimized.lmp')} nocoeff")
        write_cfg(lmp, "all", _quote_path(output_dir / 'minimized.cfg'), ' '.join(cfg.potential['elements']),
                  modify="sort id")
    finally:
        lmp.close()
    if comm is not None:
        comm.Barrier()
    if rank != 0:
        return None
    final = inspect_atomic_data(output_dir / "minimized.lmp")
    cell_preserved = all(math.isclose(
        value, final["cell"][key], rel_tol=1e-12, abs_tol=1e-10,
    ) for key, value in source["cell"].items())
    ids_preserved = source["ids"] == final["ids"]
    types_preserved = ids_preserved and all(
        atom["type"] == final["atoms"][atom_id]["type"]
        for atom_id, atom in source["atoms"].items()
    )
    masses_preserved = source["masses"].keys() == final["masses"].keys() and all(
        math.isclose(value, final["masses"][type_id], rel_tol=1e-14, abs_tol=1e-14)
        for type_id, value in source["masses"].items()
    )
    fixed_displacement = max((math.dist(
        _unwrapped(source["atoms"][atom_id], source["cell"]),
        _unwrapped(final["atoms"][atom_id], final["cell"]),
    ) for atom_id in fixed_ids if atom_id in final["atoms"]), default=0.0)
    validation = {
        "cell_preserved": cell_preserved, "atom_ids_preserved": ids_preserved,
        "atom_types_preserved": types_preserved, "masses_preserved": masses_preserved,
        "fixed_atoms_preserved": ids_preserved and fixed_displacement <= 1e-9,
        "max_fixed_atom_displacement_A": fixed_displacement,
        "fixed_atom_tolerance_A": 1e-9,
    }
    metrics = {
        "workflow": "structure_minimize", "purpose": cfg.purpose,
        "source": cfg.source, "potential_sha256": cfg.potential["sha256"],
        "boundary": cfg.simulation["boundary"], "constraints": cfg.constraints,
        "force_scope": "unconstrained atom degrees of freedom (fixed atom forces zeroed)",
        "before": before, "after": after,
        "energy_change_eV": after["energy_eV"] - before["energy_eV"],
        "validation": validation, **_minimization_stats(log_path),
    }
    (output_dir / "structure_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )
    if not all(validation[key] for key in (
        "cell_preserved", "atom_ids_preserved", "atom_types_preserved", "masses_preserved", "fixed_atoms_preserved"
    )):
        raise RuntimeError("structure minimization violated preservation checks; see structure_metrics.json")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--data-file", type=Path, required=True)
    parser.add_argument("--potential-file", type=Path, required=True)
    args = parser.parse_args()
    run_structure_minimize(
        load_structure_minimize_config(args.config), args.output_dir,
        data_file=args.data_file, potential_file=args.potential_file,
    )


if __name__ == "__main__":
    main()
