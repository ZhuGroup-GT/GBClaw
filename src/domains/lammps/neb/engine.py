"""Run a partitioned grain-boundary nudged elastic band calculation."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
import traceback
from pathlib import Path
from typing import Any, Sequence

# Run as a script in the LAMMPS environment: import packages from src/, never
# from this workflow directory, whose module names (config, tool) are generic.
_HERE = Path(__file__).resolve().parent
_SRC_ROOT = _HERE.parents[2]
sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != _HERE]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from domains.lammps.common.potential_files import current_potential_path
from core.util.io import sha256_file
from domains.lammps.common.artifacts import (
    NEB_MASTER_LOG,
    NEB_MEP_CSV,
    NEB_MEP_JSON,
    NEB_REPLICA_LOG,
)
from domains.lammps.cfg_convert.converter import (
    convert_cfg_file,
    read_lammps_atomic_data,
    read_lammps_reference,
)
from domains.lammps.neb.config import GBNebConfig, load_gb_neb_config
from domains.lammps.shear.config import (
    load_gb_shear_config,
    read_atom_ids,
    validate_boundary_ids,
)


def _lammps_path(path: Path) -> str:
    value = str(path.resolve())
    if any(character in value for character in ('"', "\n", "\r")):
        raise ValueError(f"unsupported character in LAMMPS path: {value!r}")
    return f'"{value}"'


def _replica_lammps_path(path: Path) -> str:
    value = str(path.resolve())
    if any(character.isspace() for character in value) or any(
        character in value for character in ('"', "'", "\n", "\r")
    ):
        raise ValueError(
            "NEB replica output paths cannot contain whitespace or quotes because "
            "LAMMPS must expand the partition variable $i"
        )
    return value


def _define_id_group(lmp: Any, group_name: str, atom_ids: Sequence[int]) -> None:
    if not atom_ids:
        raise ValueError(f"cannot create empty LAMMPS group {group_name}")
    lmp.command(f"group {group_name} empty")
    for start in range(0, len(atom_ids), 100):
        chunk = " ".join(str(atom_id) for atom_id in atom_ids[start : start + 100])
        lmp.command(f"group {group_name} id {chunk}")


def _prepare_inputs(
    config: GBNebConfig,
    output_dir: Path,
    project_root: Path,
) -> dict[str, Any]:
    """Snapshot and validate endpoints, potential, and optional boundary inputs."""
    root = project_root.resolve()
    sources: dict[str, Path] = {}
    for label, source in (
        ("initial_structure", config.inputs.initial_structure),
        ("final_structure", config.inputs.final_structure),
        ("top_boundary_ids", config.inputs.top_boundary_ids),
        ("bottom_boundary_ids", config.inputs.bottom_boundary_ids),
        ("shear_config", config.inputs.shear_config),
    ):
        if source is None:
            continue
        path = (root / source.path).resolve()
        if root not in path.parents or not path.is_file():
            raise FileNotFoundError(f"{label} not found inside project: {source.path}")
        if path.stat().st_size != source.size_bytes or sha256_file(path) != source.sha256:
            raise ValueError(f"{label} changed after NEB configuration: {source.path}")
        sources[label] = path

    configured_potential = current_potential_path(config.potential.file).resolve()
    if "shear_config" in sources:
        shear = load_gb_shear_config(sources["shear_config"])
        mismatches: list[str] = []
        for label, actual, expected in (
            ("material.element", shear.material.element, config.material.element),
            ("simulation.boundary", shear.simulation.boundary, config.simulation.boundary),
            ("potential.pair_style", shear.potential.pair_style, config.potential.pair_style),
        ):
            if actual != expected:
                mismatches.append(f"{label}={actual!r}, expected {expected!r}")
        if not math.isclose(
            shear.material.mass, config.material.mass, rel_tol=1.0e-8, abs_tol=1.0e-8
        ):
            mismatches.append(
                f"material.mass={shear.material.mass!r}, expected {config.material.mass!r}"
            )
        shear_potential = current_potential_path(shear.potential.file)
        if not shear_potential.is_absolute():
            shear_potential = (root / shear_potential).resolve()
        if not shear_potential.is_file():
            raise FileNotFoundError(f"shear potential file not found: {shear_potential}")
        if sha256_file(shear_potential) != config.potential.sha256:
            mismatches.append(
                "potential content differs from the configured NEB potential"
            )
        if mismatches:
            raise ValueError(
                "NEB physics no longer matches the supplied shear config: "
                + "; ".join(mismatches)
            )
    if not configured_potential.is_file():
        raise FileNotFoundError(f"potential file not found: {configured_potential}")
    if sha256_file(configured_potential) != config.potential.sha256:
        raise ValueError("potential file changed after NEB configuration")

    input_dir = output_dir / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    auxiliary_snapshots: dict[str, Path] = {}
    for label, filename in (
        ("top_boundary_ids", "top_boundary.ids"),
        ("bottom_boundary_ids", "bottom_boundary.ids"),
        ("shear_config", "shear_input.json"),
    ):
        if label in sources:
            auxiliary_snapshots[label] = input_dir / filename
            shutil.copy2(sources[label], auxiliary_snapshots[label])

    endpoint_paths: dict[str, Path] = {}
    source_snapshots: dict[str, Path] = {}
    endpoints = (
        ("initial", config.inputs.initial_structure),
        ("final", config.inputs.final_structure),
    )
    # A LAMMPS-data endpoint gives a CFG endpoint its atom types and exact box.
    data_endpoint = next(
        (sources[f"{name}_structure"] for name, item in endpoints if item.format != "cfg"), None
    )
    reference = read_lammps_reference(data_endpoint) if data_endpoint is not None else None
    for name, source_config in endpoints:
        source_path = sources[f"{name}_structure"]
        source_snapshot = input_dir / f"{name}_source.{source_config.format}"
        shutil.copy2(source_path, source_snapshot)
        source_snapshots[name] = source_snapshot
        data_path = input_dir / f"{name}.lmp"
        if source_config.format == "cfg":
            converted = convert_cfg_file(
                source_snapshot,
                data_path,
                mass_override=config.material.mass,
                reference=reference,
            )
            if any(
                item["cfg_label"] != config.material.element
                and not (
                    str(item["cfg_label"]).isdigit() and int(item["cfg_label"]) > 0
                )
                for item in converted.type_map
            ):
                raise ValueError(
                    f"{name} CFG element labels must match material.element="
                    f"{config.material.element!r}; mixed-element NEB is not supported"
                )
        else:
            shutil.copy2(source_snapshot, data_path)
        endpoint_paths[name] = data_path

    initial = read_lammps_atomic_data(endpoint_paths["initial"])
    final = read_lammps_atomic_data(endpoint_paths["final"])
    initial_ids = set(initial["atoms"])
    final_ids = set(final["atoms"])
    if initial_ids != final_ids:
        raise ValueError(
            "NEB endpoint atom IDs differ: "
            f"only initial={sorted(initial_ids - final_ids)[:10]}, "
            f"only final={sorted(final_ids - initial_ids)[:10]}"
        )
    if initial["atom_types"] != final["atom_types"]:
        raise ValueError("NEB endpoint atom-type counts differ")
    changed_types = [
        atom_id for atom_id in sorted(initial_ids)
        if initial["types_by_id"][atom_id] != final["types_by_id"][atom_id]
    ]
    if changed_types:
        raise ValueError(f"NEB endpoint atom types differ for IDs: {changed_types[:10]}")
    for name, endpoint in (("initial", initial), ("final", final)):
        expected_types = set(range(1, int(endpoint["atom_types"]) + 1))
        if set(endpoint["masses"]) != expected_types:
            raise ValueError(f"{name} NEB endpoint must define a mass for every atom type")
        if any(
            not math.isclose(mass, config.material.mass, rel_tol=1.0e-8, abs_tol=1.0e-8)
            for mass in endpoint["masses"].values()
        ):
            raise ValueError(
                f"{name} NEB endpoint masses do not match material.mass="
                f"{config.material.mass}; only a single chemical element is supported"
            )
    for index, (initial_value, final_value) in enumerate(
        zip(initial["cell"], final["cell"])
    ):
        if not math.isclose(
            initial_value, final_value, rel_tol=0.0, abs_tol=1.0e-8
        ):
            raise ValueError(
                "NEB endpoints must use the same simulation cell; "
                f"cell component {index} differs ({initial_value} vs {final_value})"
            )

    top_ids: list[int] = []
    bottom_ids: list[int] = []
    if config.constraints.mode == "shear_boundaries":
        top_ids = read_atom_ids(auxiliary_snapshots["top_boundary_ids"])
        bottom_ids = read_atom_ids(auxiliary_snapshots["bottom_boundary_ids"])
        validate_boundary_ids(top_ids, bottom_ids)
        missing_top = sorted(set(top_ids) - initial_ids)
        missing_bottom = sorted(set(bottom_ids) - initial_ids)
        if missing_top or missing_bottom:
            raise ValueError(
                "boundary ID files do not match the NEB endpoints: "
                f"missing top={missing_top[:10]}, missing bottom={missing_bottom[:10]}"
            )

    final_coordinates = input_dir / "final.neb.coords"
    with final_coordinates.open("w", encoding="utf-8") as handle:
        handle.write(f"{len(final_ids)}\n")
        for atom_id in sorted(final_ids):
            x, y, z = final["atoms"][atom_id]
            handle.write(f"{atom_id} {x:.16g} {y:.16g} {z:.16g}\n")
    library_input = input_dir / "partition.in"
    library_input.write_text(
        "# Placeholder required by LAMMPS -partition in Python library mode.\n",
        encoding="utf-8",
    )
    return {
        "initial_data": str(endpoint_paths["initial"]),
        "final_data": str(endpoint_paths["final"]),
        "initial_source": str(source_snapshots["initial"]),
        "final_source": str(source_snapshots["final"]),
        "final_coordinates": str(final_coordinates),
        "top_ids_file": (
            str(auxiliary_snapshots["top_boundary_ids"])
            if "top_boundary_ids" in auxiliary_snapshots else None
        ),
        "bottom_ids_file": (
            str(auxiliary_snapshots["bottom_boundary_ids"])
            if "bottom_boundary_ids" in auxiliary_snapshots else None
        ),
        "shear_config": (
            str(auxiliary_snapshots["shear_config"])
            if "shear_config" in auxiliary_snapshots else None
        ),
        "library_input": str(library_input),
        "potential": str(configured_potential),
        "top_ids": top_ids,
        "bottom_ids": bottom_ids,
        "atoms": len(initial_ids),
        "atom_types": int(initial["atom_types"]),
        "constraint_mode": config.constraints.mode,
    }


def extract_mep_outputs(
    master_log: Path,
    replicas: int,
    ranks_per_replica: int,
    output_csv: Path,
    output_json: Path,
    replica_energies: dict[int, float] | None = None,
) -> dict[str, Any]:
    """Extract the last complete NEB table and write human/agent records.

    ``replica_energies`` maps each 1-based replica to its final potential energy
    at full precision. They replace the table's PE columns when every value
    agrees with the printed one; otherwise the table values are kept.
    """
    lines = master_log.read_text(encoding="utf-8", errors="replace").splitlines()
    required = {f"RD{i}" for i in range(1, replicas + 1)} | {
        f"PE{i}" for i in range(1, replicas + 1)
    }
    header: list[str] | None = None
    last_values: list[float] | None = None
    for line in lines:
        tokens = line.split()
        if tokens and tokens[0] == "Step" and required.issubset(tokens):
            header = tokens
            last_values = None
            continue
        if header is None or not tokens:
            continue
        try:
            values = [float(token) for token in tokens]
        except ValueError:
            continue
        if len(values) >= len(header) and all(
            math.isfinite(value) for value in values[: len(header)]
        ):
            last_values = values
    if header is None or last_values is None:
        raise ValueError(f"no complete NEB energy table found in {master_log}")

    column = {name: index for index, name in enumerate(header)}
    energies = {replica: last_values[column[f"PE{replica}"]] for replica in range(1, replicas + 1)}
    energy_source = "neb_table"
    if replica_energies and set(replica_energies) == set(energies) and all(
        math.isfinite(replica_energies[replica])
        and math.isclose(replica_energies[replica], printed, rel_tol=1.0e-7, abs_tol=1.0e-6)
        for replica, printed in energies.items()
    ):
        energies = dict(replica_energies)
        energy_source = "replica_evaluation"
    initial_energy = energies[1]
    profile: list[dict[str, float | int]] = []
    for replica in range(1, replicas + 1):
        energy = energies[replica]
        profile.append(
            {
                "replica": replica - 1,
                "reaction_coordinate": last_values[column[f"RD{replica}"]],
                "potential_energy_eV": energy,
                "relative_energy_eV": energy - initial_energy,
            }
        )
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(profile[0]))
        writer.writeheader()
        writer.writerows(profile)

    result = {
        "version": 2,
        "status": "completed",
        "replicas": replicas,
        "ranks_per_replica": ranks_per_replica,
        "total_mpi_ranks": replicas * ranks_per_replica,
        "energy_source": energy_source,
        "profile": profile,
    }
    output_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def _configure_neb_path(lmp: Any, config: GBNebConfig, prepared: dict[str, Any]) -> None:
    """Apply run-selected forces; structure files never carry LAMMPS fixes."""
    if config.constraints.mode == "shear_boundaries":
        _define_id_group(lmp, "top_boundary", prepared["top_ids"])
        _define_id_group(lmp, "bottom_boundary", prepared["bottom_ids"])
        lmp.command("group fixed_xy union top_boundary bottom_boundary")
        for group_name, expected_count in (
            ("top_boundary", len(prepared["top_ids"])),
            ("bottom_boundary", len(prepared["bottom_ids"])),
        ):
            variable_name = f"__gb_neb_count_{group_name}"
            lmp.command(f"variable {variable_name} equal count({group_name})")
            actual_count = int(round(float(lmp.extract_variable(variable_name, None, 0))))
            lmp.command(f"variable {variable_name} delete")
            if actual_count != expected_count:
                raise RuntimeError(
                    f"{group_name}: expected {expected_count} atoms, rebuilt {actual_count}"
                )

    lmp.command(f"min_style {config.neb.min_style}")
    lmp.command(f"timestep {config.neb.timestep:.16g}")
    lmp.command(f"fix neb_path all neb {config.neb.spring:.16g}")
    if config.constraints.mode == "shear_boundaries":
        # setforce follows fix neb to remove both physical and nudging x/y forces.
        lmp.command("fix hold_boundary_xy fixed_xy setforce 0.0 0.0 NULL")


def run_gb_neb(
    config: GBNebConfig,
    output_dir: Path,
    *,
    project_root: Path,
) -> None:
    """Execute one NEB run using global and replica communicators correctly."""
    try:
        from mpi4py import MPI
    except ImportError as exc:
        raise RuntimeError("GB NEB requires mpi4py") from exc
    from domains.lammps.common.live_lammps import lammps

    universe = MPI.COMM_WORLD
    universe_rank = int(universe.Get_rank())
    universe_size = int(universe.Get_size())
    expected_size = config.neb.total_mpi_ranks
    if universe_size != expected_size:
        raise ValueError(
            f"global MPI size {universe_size} does not match "
            f"{config.neb.replicas} replicas x "
            f"{config.neb.ranks_per_replica} ranks/replica = {expected_size}"
        )

    prepared: dict[str, Any] | None = None
    preparation_error: str | None = None
    if universe_rank == 0:
        try:
            prepared = _prepare_inputs(config, output_dir, project_root)
        except Exception:
            preparation_error = traceback.format_exc()
    preparation_error = universe.bcast(preparation_error, root=0)
    if preparation_error is not None:
        raise RuntimeError(f"NEB input preparation failed on universe rank 0:\n{preparation_error}")
    prepared = universe.bcast(prepared, root=0)
    universe.Barrier()
    if prepared is None:
        raise RuntimeError("universe rank 0 did not broadcast prepared NEB inputs")

    master_log = output_dir / NEB_MASTER_LOG
    replica_log = output_dir / NEB_REPLICA_LOG
    cmdargs = [
        "-in",
        str(Path(prepared["library_input"])),
        "-partition",
        f"{config.neb.replicas}x{config.neb.ranks_per_replica}",
        "-log",
        str(master_log),
        "-plog",
        str(replica_log),
        "-screen",
        "none",
        "-pscreen",
        "none",
        "-nocite",
        "-nonbuf",
    ]

    # Passing the intact universe communicator is essential.  LAMMPS creates
    # one local world per partition while retaining universe communication for
    # fix neb.  Python's global rank is used for shared-file ownership below.
    lmp = lammps(comm=universe, cmdargs=cmdargs)
    try:
        replica_comm = lmp.get_mpi_comm()
        if replica_comm is None:
            raise RuntimeError("LAMMPS did not expose its replica communicator")
        replica_local_rank = int(replica_comm.Get_rank())
        replica_local_size = int(replica_comm.Get_size())
        if replica_local_size != config.neb.ranks_per_replica:
            raise RuntimeError(
                "LAMMPS replica communicator size mismatch: "
                f"expected {config.neb.ranks_per_replica}, got {replica_local_size}"
            )
        if not 0 <= replica_local_rank < replica_local_size:
            raise RuntimeError(f"invalid LAMMPS replica-local rank {replica_local_rank}")

        lmp.command("clear")
        lmp.command("units metal")
        lmp.command("dimension 3")
        lmp.command("atom_style atomic")
        lmp.command(f"boundary {config.simulation.boundary}")
        lmp.command("atom_modify map array")
        lmp.command(f"read_data {_lammps_path(Path(prepared['initial_data']))}")
        lmp.command("neighbor 1.0 bin")
        lmp.command("neigh_modify delay 0 every 1 check yes")
        lmp.command(f"pair_style {config.potential.pair_style}")
        atom_types = int(lmp.extract_global("ntypes"))
        element_map = " ".join(config.material.element for _ in range(atom_types))
        lmp.command(
            f"pair_coeff * * {_lammps_path(Path(prepared['potential']))} "
            f"{element_map}"
        )

        _configure_neb_path(lmp, config, prepared)
        lmp.command(f"thermo {config.neb.nevery}")
        lmp.command(
            "thermo_style custom step part pe fmax fnorm "
            "lx ly lz pxx pyy pzz pxy pxz pyz"
        )
        lmp.command("thermo_modify flush yes")
        lmp.command("variable i equal part")
        trajectory = output_dir / "neb.replica.$i.lammpstrj"
        lmp.command(
            f"dump neb_dump all custom {config.output.dump_every} "
            f"{_replica_lammps_path(trajectory)} "
            "id type x y z ix iy iz fx fy fz"
        )
        lmp.command("dump_modify neb_dump sort id")
        lmp.command("run 0")
        lmp.command(
            f"neb {config.neb.etol:.16g} {config.neb.ftol:.16g} "
            f"{config.neb.n1} {config.neb.n2} {config.neb.nevery} final "
            f"{_lammps_path(Path(prepared['final_coordinates']))} "
            f"verbosity {config.output.verbosity}"
        )
        final_data = output_dir / "neb.replica.$i.final.lmp"
        lmp.command(
            f"write_data {_replica_lammps_path(final_data)} nocoeff"
        )
        # The NEB table prints PE with eight significant digits (1 meV for a
        # 10^4 eV cell); evaluate the final replica again at full precision.
        lmp.command("undump neb_dump")
        lmp.command("run 0")
        # LAMMPS numbers partitions from 0; the NEB table's PE columns from 1.
        replica_energy = (int(lmp.extract_variable("i", None, 0)) + 1, float(lmp.get_thermo("pe")))
    finally:
        lmp.close()

    universe.Barrier()
    gathered = universe.gather(replica_energy if replica_local_rank == 0 else None, root=0)
    extraction_error: str | None = None
    if universe_rank == 0:
        try:
            extract_mep_outputs(
                master_log,
                config.neb.replicas,
                config.neb.ranks_per_replica,
                output_dir / NEB_MEP_CSV,
                output_dir / NEB_MEP_JSON,
                replica_energies=dict(item for item in gathered if item is not None),
            )
        except Exception:
            extraction_error = traceback.format_exc()
    extraction_error = universe.bcast(extraction_error, root=0)
    universe.Barrier()
    if extraction_error is not None:
        raise RuntimeError(f"NEB MEP extraction failed on universe rank 0:\n{extraction_error}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run partitioned GB NEB with LAMMPS.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    if not output_dir.is_dir():
        raise FileNotFoundError(f"output directory does not exist: {output_dir}")
    config = load_gb_neb_config(args.config.resolve())
    run_gb_neb(config, output_dir, project_root=args.project_root.resolve())


if __name__ == "__main__":
    main()
