"""Build a bicrystal grain boundary and relax the structure with LAMMPS.

Run in the configured LAMMPS Conda environment, for example with two MPI ranks::

    mpirun -n 2 python src/domains/lammps/minimize/engine.py --output-dir <dir> --config <json>

Adjust the rank count to the cores available for the job.
The launcher must prepare the output directory before calling mpirun.
This script only issues LAMMPS commands; it does not create or clean files.

Structure and simulation parameters are read from a JSON config (``--config``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

# Run as a script in the LAMMPS environment: import packages from src/, never
# from this workflow directory, whose module names (config, tool) are generic.
_HERE = Path(__file__).resolve().parent
_SRC_ROOT = _HERE.parents[2]
sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != _HERE]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from domains.lammps.common.engine_utils import mpi_rank, run_staged_relaxation, write_cfg
from domains.lammps.common.defaults import PBC_X_PADDING_EPS_FACTOR
from domains.lammps.common.artifacts import (
    BOT_BOUNDARY_IDS,
    INITIAL_CFG,
    MINIMIZED_CFG,
    MINIMIZED_LMP,
    TOP_BOUNDARY_IDS,
    TRAJECTORY,
)
from domains.lammps.minimize.config import (
    GBMinimizeConfig,
    RepeatTriplet,
    SimulationConfig,
    load_gb_minimize_config,
)
from domains.lammps.common.live_lammps import lammps

TOP_GROUP = "top_grain"
BOT_GROUP = "bot_grain"
TOP_BOUNDARY_GROUP = "top_boundary"
BOT_BOUNDARY_GROUP = "bot_boundary"
TOP_ATOM_TYPE = 1
BOT_ATOM_TYPE = 2
NUM_ATOM_TYPES = 2


def grain_extent(
    orientation: dict[str, list[int]],
    size: RepeatTriplet,
    a0: float,
) -> tuple[float, float, float]:
    nx, ny, nz = size
    lx = nx * np.linalg.norm(orientation["x"]) * a0
    ly = ny * np.linalg.norm(orientation["y"]) * a0
    lz = nz * np.linalg.norm(orientation["z"]) * a0
    return lx, ly, lz


def x_box_length(
    lx_top: float,
    lx_bot: float,
    sim: SimulationConfig,
) -> float:
    """Return x box length using vacuum or epsilon padding from the config."""
    x_padding = (
        PBC_X_PADDING_EPS_FACTOR * sim.y_eps
        if sim.x_pbc
        else sim.y_vacuum
    )
    return max(lx_top, lx_bot) + 2.0 * x_padding


def orient_cmd(orientation: dict[str, list[int]]) -> str:
    ox, oy, oz = orientation["x"], orientation["y"], orientation["z"]
    return (
        f"orient x {ox[0]} {ox[1]} {ox[2]} "
        f"orient y {oy[0]} {oy[1]} {oy[2]} "
        f"orient z {oz[0]} {oz[1]} {oz[2]}"
    )


def create_boundary_slab_groups(
    lmp: lammps,
    *,
    ly_top: float,
    ly_bot: float,
    y_interface: float,
    slab_thickness: float,
) -> None:
    if slab_thickness <= 0.0:
        raise ValueError(f"slab_thickness must be positive, got {slab_thickness}")
    if slab_thickness >= ly_top - y_interface:
        raise ValueError("top boundary slab is thicker than the top grain")
    if slab_thickness >= ly_bot + y_interface:
        raise ValueError("bottom boundary slab is thicker than the bottom grain")

    y_top_lo = ly_top - slab_thickness
    y_bot_hi = -ly_bot + slab_thickness
    for category, name in (
        ("group", TOP_BOUNDARY_GROUP),
        ("group", BOT_BOUNDARY_GROUP),
        ("region", "top_boundary_slab"),
        ("region", "bot_boundary_slab"),
    ):
        if lmp.has_id(category, name):
            lmp.command(f"{category} {name} delete")

    lmp.command(f"region top_boundary_slab block INF INF {y_top_lo} INF INF INF units box")
    lmp.command(f"region bot_boundary_slab block INF INF INF {y_bot_hi} INF INF units box")
    lmp.command(f"group {TOP_BOUNDARY_GROUP} region top_boundary_slab")
    lmp.command(f"group {BOT_BOUNDARY_GROUP} region bot_boundary_slab")


def export_group_atom_ids(lmp: lammps, group_name: str, output_path: Path, *, rank: int) -> None:
    """Write one atom id per line for later group reconstruction."""
    dump_path = output_path.with_suffix(".dump.lammpstrj")
    lmp.command(f"write_dump {group_name} custom {dump_path} id")
    if rank != 0:
        return

    atom_ids: list[int] = []
    reading = False
    with dump_path.open() as handle:
        for line in handle:
            stripped = line.strip()
            if stripped.startswith("ITEM: ATOMS"):
                reading = True
                continue
            if stripped.startswith("ITEM:"):
                reading = False
                continue
            if reading and stripped:
                atom_ids.append(int(stripped.split()[0]))

    output_path.write_text("\n".join(str(atom_id) for atom_id in atom_ids) + "\n")
    dump_path.unlink(missing_ok=True)


def run_gb_minimize(
    cfg: GBMinimizeConfig,
    output_dir: Path,
    *,
    pair_style: str,
    potential_file: Path,
) -> None:
    """Execute the bicrystal build and relaxation workflow."""
    sim = cfg.simulation
    material = cfg.material
    top_ori = cfg.grains.top.orientation.as_dict()
    bot_ori = cfg.grains.bottom.orientation.as_dict()
    top_size = cfg.grains.top.size
    bot_size = cfg.grains.bottom.size

    rank = mpi_rank()

    lx_top, ly_top, lz_top = grain_extent(top_ori, top_size, sim.a0)
    lx_bot, ly_bot, lz_bot = grain_extent(bot_ori, bot_size, sim.a0)

    y_interface = 0.0
    lx_box = x_box_length(lx_top, lx_bot, sim)
    lz_box = max(lz_top, lz_bot) + 2.0 * sim.y_eps
    ylo_box = -ly_bot - sim.y_vacuum
    yhi_box = ly_top + sim.y_vacuum

    # Select the final logfile at startup for live output from initialization.
    lmp = lammps(cmdargs=["-log", str(output_dir / "log.lammps"), "-nonbuf"])

    lmp.command("units metal")
    lmp.command("atom_style atomic")
    lmp.command("dimension 3")
    lmp.command(f"boundary {sim.boundary}")
    lmp.command(
        f"region sim_box block -{lx_box / 2} {lx_box / 2} "
        f"{ylo_box} {yhi_box} -{lz_box / 2} {lz_box / 2} units box"
    )
    lmp.command(f"create_box {NUM_ATOM_TYPES} sim_box")

    lattice = material.lattice
    # y_eps: small interface gap / face pad so lattice sites on block faces
    # are not dropped by create_atoms/region. Outer vacuum is y_vacuum.
    top_xlo = -lx_top / 2 + sim.y_eps
    top_xhi = lx_top / 2 + sim.y_eps
    top_ylo = y_interface + sim.y_eps
    top_yhi = ly_top + sim.y_eps
    top_zlo = -lz_top / 2 + sim.y_eps
    top_zhi = lz_top / 2 + sim.y_eps
    
    bot_xlo = -lx_bot / 2 + sim.y_eps
    bot_xhi = lx_bot / 2 + sim.y_eps
    bot_ylo = -ly_bot - sim.y_eps
    bot_yhi = y_interface - sim.y_eps
    bot_zlo = -lz_bot / 2 + sim.y_eps
    bot_zhi = lz_bot / 2 + sim.y_eps
    
    
    # Top grain
    lmp.command(f"lattice {lattice} {sim.a0} {orient_cmd(top_ori)}")
    lmp.command(
        f"region {TOP_GROUP} block {top_xlo} {top_xhi} "
        f"{top_ylo} {top_yhi} {top_zlo} {top_zhi} units box"
    )
    lmp.command(f"create_atoms {TOP_ATOM_TYPE} region {TOP_GROUP}")
    lmp.command(f"group {TOP_GROUP} region {TOP_GROUP}")

    # Bottom grain
    lmp.command(f"lattice {lattice} {sim.a0} {orient_cmd(bot_ori)}")
    lmp.command(
        f"region {BOT_GROUP} block {bot_xlo} {bot_xhi} "
        f"{bot_ylo} {bot_yhi} {bot_zlo} {bot_zhi} units box"
    )
    lmp.command(f"create_atoms {BOT_ATOM_TYPE} region {BOT_GROUP}")
    lmp.command(f"group {BOT_GROUP} region {BOT_GROUP}")
    
    # Set masses
    element_map = " ".join(material.element for _ in range(NUM_ATOM_TYPES))
    lmp.command(f"mass {TOP_ATOM_TYPE} {material.mass}")
    lmp.command(f"mass {BOT_ATOM_TYPE} {material.mass}")

    # Set pair style and coefficients
    lmp.command("neighbor 1.0 bin")
    lmp.command("neigh_modify delay 1 every 1 check yes")
    lmp.command(f"pair_style {pair_style}")
    lmp.command(f"pair_coeff * * {potential_file} {material.element} {material.element}")

    # Delete overlapping atoms
    overlap_cut = 0.3 * sim.a0
    lmp.command(f"delete_atoms overlap {overlap_cut} all all")
    lmp.command("run 0")

    create_boundary_slab_groups(
        lmp,
        ly_top=ly_top,
        ly_bot=ly_bot,
        y_interface=y_interface,
        slab_thickness=sim.boundary_slab_thickness,
    )
    lmp.command("run 0")
    export_group_atom_ids(lmp, TOP_BOUNDARY_GROUP, output_dir / TOP_BOUNDARY_IDS, rank=rank)
    write_cfg(lmp, TOP_BOUNDARY_GROUP, output_dir / 'top_slab.cfg', element_map)
    export_group_atom_ids(lmp, BOT_BOUNDARY_GROUP, output_dir / BOT_BOUNDARY_IDS, rank=rank)
    write_cfg(lmp, BOT_BOUNDARY_GROUP, output_dir / 'bottom_slab.cfg', element_map)
    
    # Write initial configuration
    write_cfg(lmp, "all", output_dir / INITIAL_CFG, element_map)
    
    # Fix the y positions of the top and bottom grains
    # lmp.command(f"fix fy_top {TOP_BOUNDARY_GROUP} setforce NULL 0.0 NULL")
    # lmp.command(f"fix fy_bot {BOT_BOUNDARY_GROUP} setforce NULL 0.0 NULL")
    
    # Minimization
    # Set minimization parameters
    lmp.command(f"min_style {sim.min_style}")
    lmp.command("thermo 100")
    lmp.command("thermo_style custom step pe lx ly lz pxx pyy pzz pxy pxz pyz fmax fnorm")
    lmp.command("thermo_modify flush yes")
    lmp.command(
        f"dump min_traj all custom 100 {output_dir / TRAJECTORY} "
        "id type xs ys zs ix iy iz"
    )
    lmp.command("dump_modify min_traj sort id")

    # Run initial 3-step staged relaxation based on the config
    run_staged_relaxation(lmp, sim, top_group=TOP_GROUP, bottom_group=BOT_GROUP, bottom_fix="freeze_bot")
    # Run regular minimization with alternate free relax and box relax.
    relax_cmd = " ".join(
        f"{axis} 0.0"
        for axis, token in zip(("x", "y", "z"), sim.boundary.split(), strict=True)
        if token == "p"
    )
    for _ in range(sim.relax_cycles):
        lmp.command(
            f"minimize {sim.min_etol} {sim.min_ftol} {sim.min_maxiter} {sim.min_maxeval}"
        )
        if relax_cmd:
            lmp.command(f"fix boxrelax all box/relax {relax_cmd}")
            lmp.command(
                f"minimize {sim.min_etol} {sim.min_ftol} {sim.min_maxiter} {sim.min_maxeval}"
            )
            lmp.command("unfix boxrelax")

    lmp.command("undump min_traj")
    write_cfg(lmp, "all", output_dir / MINIMIZED_CFG, element_map)
    # LAMMPS data file (no pair style/coefficients) for downstream read_data workflows.
    lmp.command(f"write_data {output_dir / MINIMIZED_LMP} nocoeff")
    lmp.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Relax a bicrystal grain boundary with LAMMPS."
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="GB minimize JSON config.",
    )
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    if not output_dir.exists():
        raise FileNotFoundError(f"Output directory does not exist: {output_dir}")

    config_path = args.config.resolve()
    cfg = load_gb_minimize_config(config_path)

    pair_style = cfg.potential.pair_style.strip()
    potential_file = cfg.potential.resolve_path(config_dir=config_path.parent)

    if not potential_file.is_file():
        raise FileNotFoundError(f"Potential file not found: {potential_file}")

    run_gb_minimize(
        cfg,
        output_dir,
        pair_style=pair_style,
        potential_file=potential_file,
    )


if __name__ == "__main__":
    main()
