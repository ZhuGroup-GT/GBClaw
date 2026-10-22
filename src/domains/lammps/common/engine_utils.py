"""Helpers shared by LAMMPS engine scripts (stdlib only; runs in the LAMMPS environment)."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

# Coordinates in fractional units; 12 significant digits keep a 100 nm cell
# within about 1e-9 A while staying close to the default file size.
_CFG_FLOAT_FORMAT = "%.12g"
_CFG_CELL_RE = re.compile(r"^H0\(([123]),([123])\) =")


def mpi_rank() -> int:
    try:
        from mpi4py import MPI

        return int(MPI.COMM_WORLD.Get_rank())
    except ImportError:
        return 0


def mpi_barrier() -> None:
    try:
        from mpi4py import MPI

        MPI.COMM_WORLD.Barrier()
    except ImportError:
        pass


def group_count(lmp: Any, group_name: str, *, variable_prefix: str) -> int:
    """Count a LAMMPS group's atoms through a temporary equal-style variable."""
    variable = f"{variable_prefix}{group_name}"
    try:
        lmp.command(f"variable {variable} equal count({group_name})")
        lmp.command("run 0")
        value = float(lmp.extract_variable(variable, None, 0))
    finally:
        try:
            lmp.command(f"variable {variable} delete")
        except Exception:
            pass
    return int(round(value))


def run_staged_relaxation(
    lmp: Any, sim: Any, *, top_group: str, bottom_group: str, bottom_fix: str,
) -> None:
    """Optionally relax top then bottom grain with the other frozen, then all atoms.

    ``sim`` supplies ``enable_initial_staged_relaxation`` and the minimizer
    tolerances. Fix and group names are part of each workflow's LAMMPS log.
    """
    if not sim.enable_initial_staged_relaxation:
        return
    minimize = (
        f"minimize {sim.min_etol} {sim.min_ftol} "
        f"{sim.min_maxiter} {sim.min_maxeval}"
    )
    # Step 1: fix bottom grain; top grain relaxes.
    lmp.command(f"fix {bottom_fix} {bottom_group} setforce 0.0 0.0 0.0")
    lmp.command(minimize)
    lmp.command(f"unfix {bottom_fix}")
    # Step 2: fix top grain; bottom grain relaxes.
    lmp.command(f"fix freeze_top {top_group} setforce 0.0 0.0 0.0")
    lmp.command(minimize)
    lmp.command("unfix freeze_top")
    # Step 3: all atoms free.
    lmp.command(minimize)


def write_cfg(lmp: Any, group: str, path: Path | str, elements: str, *, modify: str = "") -> None:
    """Write an extended CFG that converts back to LAMMPS data without loss.

    ``path`` is the LAMMPS argument (it may be quoted). Element labels may repeat
    across atom types, so the LAMMPS type is kept as an auxiliary column. LAMMPS
    prints the H0 cell with six significant digits; rank 0 rewrites it from the
    exact box after the dump is written.
    """
    target = str(path)
    extra = f" {modify}" if modify else ""
    lmp.command(
        f"write_dump {group} cfg {target} mass type xs ys zs ix iy iz id type "
        f"modify element {elements} format float {_CFG_FLOAT_FORMAT}{extra}"
    )
    if mpi_rank() == 0:
        _write_exact_cfg_cell(Path(target.strip('"')), lmp.extract_box())
    mpi_barrier()


def _write_exact_cfg_cell(path: Path, box: tuple) -> None:
    boxlo, boxhi, xy, yz, xz = box[:5]
    xprd, yprd, zprd = (high - low for low, high in zip(boxlo, boxhi))
    # Rows of H0 are the cell vectors a, b and c.
    cell = {(1, 1): xprd, (1, 2): 0.0, (1, 3): 0.0, (2, 1): xy, (2, 2): yprd, (2, 3): 0.0,
            (3, 1): xz, (3, 2): yz, (3, 3): zprd}
    temporary = path.with_name(f".{path.name}.cell")
    written = set()
    with path.open("r", encoding="utf-8") as source, temporary.open("w", encoding="utf-8") as target:
        for line in source:
            if match := _CFG_CELL_RE.match(line):
                key = (int(match.group(1)), int(match.group(2)))
                line = f"H0({key[0]},{key[1]}) = {float(cell[key])!r} A\n"
                written.add(key)
            target.write(line)
            if line.startswith("entry_count"):
                break
        shutil.copyfileobj(source, target)
    if written != set(cell):
        temporary.unlink()
        raise RuntimeError(f"{path}: CFG header lacks H0 entries {sorted(set(cell) - written)}")
    os.replace(temporary, path)

