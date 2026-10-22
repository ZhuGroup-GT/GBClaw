"""Dependency-free validated configuration for grain-boundary NEB runs."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from domains.lammps.common.config_parsing import config_object, nonempty_text
from domains.lammps.common.defaults import (
    NEB_DUMP_EVERY,
    NEB_ETOL,
    NEB_FTOL,
    NEB_MIN_STYLE,
    NEB_N1,
    NEB_N2,
    NEB_NEVERY,
    NEB_RANKS_PER_REPLICA,
    NEB_REPLICAS,
    NEB_SPRING,
    NEB_TIMESTEP,
    NEB_VERBOSITY,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class SourceFile:
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class StructureSource(SourceFile):
    format: str


@dataclass(frozen=True)
class InputsConfig:
    initial_structure: StructureSource
    final_structure: StructureSource
    top_boundary_ids: SourceFile | None = None
    bottom_boundary_ids: SourceFile | None = None
    shear_config: SourceFile | None = None


@dataclass(frozen=True)
class ConstraintsConfig:
    mode: str = "none"


@dataclass(frozen=True)
class MaterialConfig:
    element: str
    mass: float


@dataclass(frozen=True)
class SimulationConfig:
    boundary: str


@dataclass(frozen=True)
class PotentialConfig:
    pair_style: str
    file: str
    sha256: str


@dataclass(frozen=True)
class NEBConfig:
    replicas: int = NEB_REPLICAS
    ranks_per_replica: int = NEB_RANKS_PER_REPLICA
    spring: float = NEB_SPRING
    min_style: str = NEB_MIN_STYLE
    timestep: float = NEB_TIMESTEP
    etol: float = NEB_ETOL
    ftol: float = NEB_FTOL
    n1: int = NEB_N1
    n2: int = NEB_N2
    nevery: int = NEB_NEVERY

    @property
    def total_mpi_ranks(self) -> int:
        return self.replicas * self.ranks_per_replica


@dataclass(frozen=True)
class OutputConfig:
    dump_every: int = NEB_DUMP_EVERY
    verbosity: str = NEB_VERBOSITY


@dataclass(frozen=True)
class GBNebConfig:
    version: int
    description: str
    inputs: InputsConfig
    material: MaterialConfig
    simulation: SimulationConfig
    potential: PotentialConfig
    neb: NEBConfig
    output: OutputConfig
    constraints: ConstraintsConfig = ConstraintsConfig()


def _source_file(data: Any, *, label: str) -> SourceFile:
    values = config_object(data, label=label, required=True)
    text = nonempty_text(values.get("path"), label=f"{label}.path").replace("\\", "/")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or path.name in {"", ".", ".."}:
        raise ValueError(f"{label}.path must be a safe project-relative file path")
    sha256 = str(values.get("sha256") or "").strip().lower()
    if not _SHA256_RE.fullmatch(sha256):
        raise ValueError(f"{label}.sha256 must be a lowercase SHA-256 digest")
    size_bytes = int(values.get("size_bytes", 0))
    if size_bytes <= 0:
        raise ValueError(f"{label}.size_bytes must be positive")
    return SourceFile(path=path.as_posix(), sha256=sha256, size_bytes=size_bytes)


def _structure_source(data: Any, *, label: str) -> StructureSource:
    source = _source_file(data, label=label)
    values = config_object(data, label=label, required=True)
    structure_format = str(values.get("format") or "").strip().lower()
    suffix = PurePosixPath(source.path).suffix.lower().lstrip(".")
    if structure_format not in {"cfg", "lmp"}:
        raise ValueError(f"{label}.format must be 'cfg' or 'lmp'")
    if suffix != structure_format:
        raise ValueError(
            f"{label}.format={structure_format!r} does not match {source.path!r}"
        )
    return StructureSource(**asdict(source), format=structure_format)


def build_gb_neb_config(data: dict[str, Any]) -> GBNebConfig:
    if not isinstance(data, dict):
        raise ValueError("gb_neb config root must be an object")
    if "version" not in data:
        raise ValueError("gb_neb config.version is required")
    version = int(data["version"])
    if version != 1:
        raise ValueError(
            f"unsupported gb_neb config version {version}; expected version 1"
        )

    input_values = config_object(data.get("inputs"), label="inputs", required=True)
    inputs = InputsConfig(
        initial_structure=_structure_source(
            input_values.get("initial_structure"), label="inputs.initial_structure"
        ),
        final_structure=_structure_source(
            input_values.get("final_structure"), label="inputs.final_structure"
        ),
        top_boundary_ids=(
            _source_file(input_values["top_boundary_ids"], label="inputs.top_boundary_ids")
            if input_values.get("top_boundary_ids") is not None else None
        ),
        bottom_boundary_ids=(
            _source_file(input_values["bottom_boundary_ids"], label="inputs.bottom_boundary_ids")
            if input_values.get("bottom_boundary_ids") is not None else None
        ),
        shear_config=(
            _source_file(input_values["shear_config"], label="inputs.shear_config")
            if input_values.get("shear_config") is not None else None
        ),
    )
    constraint_values = config_object(data.get("constraints"), label="constraints")
    if set(constraint_values) - {"mode"}:
        raise ValueError("constraints supports only mode")
    # Existing configs carried both boundary-ID files without a mode field.
    default_mode = (
        "shear_boundaries"
        if inputs.top_boundary_ids is not None or inputs.bottom_boundary_ids is not None
        else "none"
    )
    mode = str(constraint_values.get("mode", default_mode)).strip().lower()
    if mode not in {"none", "shear_boundaries"}:
        raise ValueError("constraints.mode must be none or shear_boundaries")
    if mode == "shear_boundaries" and (
        inputs.top_boundary_ids is None or inputs.bottom_boundary_ids is None
    ):
        raise ValueError("shear_boundaries requires both top and bottom boundary-ID inputs")
    if mode == "none" and (
        inputs.top_boundary_ids is not None or inputs.bottom_boundary_ids is not None
    ):
        raise ValueError("constraints.mode=none requires omitting boundary-ID inputs")
    constraints = ConstraintsConfig(mode=mode)
    for name in ("top_boundary_ids", "bottom_boundary_ids"):
        source = getattr(inputs, name)
        if source is not None and not source.path.lower().endswith(".ids"):
            raise ValueError(f"inputs.{name}.path must end with .ids")
    if (
        inputs.shear_config is not None
        and not inputs.shear_config.path.lower().endswith(".json")
    ):
        raise ValueError("inputs.shear_config.path must end with .json")

    material_values = config_object(data.get("material"), label="material", required=True)
    mass = float(material_values.get("mass", 0.0))
    if not math.isfinite(mass) or mass <= 0.0:
        raise ValueError("material.mass must be finite and positive")
    material = MaterialConfig(
        element=nonempty_text(material_values.get("element"), label="material.element"),
        mass=mass,
    )

    simulation_values = config_object(
        data.get("simulation"), label="simulation", required=True
    )
    boundary_tokens = nonempty_text(
        simulation_values.get("boundary"), label="simulation.boundary"
    ).split()
    if len(boundary_tokens) != 3 or any(
        token not in {"p", "f", "s", "m"} for token in boundary_tokens
    ):
        raise ValueError("simulation.boundary must contain three LAMMPS boundary tokens")
    simulation = SimulationConfig(boundary=" ".join(boundary_tokens))

    potential_values = config_object(
        data.get("potential"), label="potential", required=True
    )
    potential_sha256 = str(potential_values.get("sha256") or "").strip().lower()
    if not _SHA256_RE.fullmatch(potential_sha256):
        raise ValueError("potential.sha256 must be a lowercase SHA-256 digest")
    potential = PotentialConfig(
        pair_style=nonempty_text(
            potential_values.get("pair_style"), label="potential.pair_style"
        ),
        file=nonempty_text(potential_values.get("file"), label="potential.file"),
        sha256=potential_sha256,
    )

    neb_values = config_object(data.get("neb"), label="neb")
    neb_defaults = NEBConfig()
    neb = NEBConfig(
        replicas=int(neb_values.get("replicas", neb_defaults.replicas)),
        ranks_per_replica=int(
            neb_values.get("ranks_per_replica", neb_defaults.ranks_per_replica)
        ),
        spring=float(neb_values.get("spring", neb_defaults.spring)),
        min_style=str(neb_values.get("min_style", neb_defaults.min_style))
        .strip()
        .lower(),
        timestep=float(neb_values.get("timestep", neb_defaults.timestep)),
        etol=float(neb_values.get("etol", neb_defaults.etol)),
        ftol=float(neb_values.get("ftol", neb_defaults.ftol)),
        n1=int(neb_values.get("n1", neb_defaults.n1)),
        n2=int(neb_values.get("n2", neb_defaults.n2)),
        nevery=int(neb_values.get("nevery", neb_defaults.nevery)),
    )
    if neb.replicas < 2:
        raise ValueError("neb.replicas must be at least 2")
    if neb.ranks_per_replica < 1:
        raise ValueError("neb.ranks_per_replica must be positive")
    if neb.min_style not in {"quickmin", "fire"}:
        raise ValueError("neb.min_style must be 'quickmin' or 'fire'")
    for label, value in (
        ("neb.spring", neb.spring),
        ("neb.timestep", neb.timestep),
        ("neb.etol", neb.etol),
        ("neb.ftol", neb.ftol),
    ):
        if not math.isfinite(value):
            raise ValueError(f"{label} must be finite")
    if neb.spring <= 0.0 or neb.timestep <= 0.0:
        raise ValueError("neb.spring and neb.timestep must be positive")
    if neb.etol < 0.0 or neb.ftol <= 0.0:
        raise ValueError("NEB tolerances must satisfy etol >= 0 and ftol > 0")
    if neb.nevery <= 0 or neb.n1 <= 0 or neb.n2 <= 0:
        raise ValueError("neb.n1, neb.n2, and neb.nevery must be positive")
    if neb.n1 % neb.nevery or neb.n2 % neb.nevery:
        raise ValueError("neb.n1 and neb.n2 must be multiples of neb.nevery")

    output_values = config_object(data.get("output"), label="output")
    output_defaults = OutputConfig()
    output = OutputConfig(
        dump_every=int(
            output_values.get("dump_every", output_defaults.dump_every)
        ),
        verbosity=str(
            output_values.get("verbosity", output_defaults.verbosity)
        )
        .strip()
        .lower(),
    )
    if output.dump_every <= 0:
        raise ValueError("output.dump_every must be positive")
    if output.verbosity not in {"terse", "default", "verbose"}:
        raise ValueError("output.verbosity must be terse, default, or verbose")

    return GBNebConfig(
        version=version,
        description=str(data.get("description", "")),
        inputs=inputs,
        material=material,
        simulation=simulation,
        potential=potential,
        neb=neb,
        output=output,
        constraints=constraints,
    )


def config_to_dict(config: GBNebConfig) -> dict[str, Any]:
    return asdict(config)


def load_gb_neb_config(path: Path | str) -> GBNebConfig:
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"GB NEB config not found: {config_path}")
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return build_gb_neb_config(data)


