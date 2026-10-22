"""Dependency-free configuration and mechanics helpers for LAMMPS GB shear loading."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from domains.lammps.common.config_parsing import config_object, nonempty_text
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

EV_PER_ANGSTROM3_TO_MPA = 160217.66208


@dataclass(frozen=True)
class SourceConfig:
    output_dir: str
    parent_run_id: str
    parent_workflow: str
    initial_cumulative_shear_strain: float
    data: str
    top_boundary_ids: str = "top_boundary.ids"
    bottom_boundary_ids: str = "bot_boundary.ids"


@dataclass(frozen=True)
class MaterialConfig:
    element: str
    mass: float


@dataclass(frozen=True)
class SimulationConfig:
    boundary: str = "p p p"


@dataclass(frozen=True)
class PotentialConfig:
    pair_style: str
    file: str


@dataclass(frozen=True)
class LoadingConfig:
    """Shear schedule with an optional alternating loading direction.

    ``direction`` is the direction of the first loading half-cycle.  When
    ``steps_per_half_cycle`` is ``None`` the entire run is unidirectional.
    Strain increments are always non-negative magnitudes; their sign is
    derived from the active direction at each step.
    """

    direction: str = SHEAR_LOADING_DIRECTION
    initial_strain_increment: float = SHEAR_INITIAL_STRAIN_INCREMENT
    initial_steps: int = SHEAR_INITIAL_STEPS
    fine_strain_increment: float = SHEAR_FINE_STRAIN_INCREMENT
    fine_steps: int = SHEAR_FINE_STEPS
    steps_per_half_cycle: int | None = SHEAR_STEPS_PER_HALF_CYCLE
    half_cycles: int = SHEAR_HALF_CYCLES
    top_spring_per_atom: float | None = SHEAR_SPRING_PER_ATOM
    bottom_spring_per_atom: float | None = SHEAR_SPRING_PER_ATOM
    load_type: str = SHEAR_LOAD_TYPE

    def __post_init__(self) -> None:
        if self.load_type == "displacement":
            object.__setattr__(self, "top_spring_per_atom", None)
            object.__setattr__(self, "bottom_spring_per_atom", None)

    @property
    def total_steps(self) -> int:
        return self.initial_steps + self.fine_steps

    @property
    def cyclic(self) -> bool:
        return self.steps_per_half_cycle is not None

    @property
    def loading_half_cycles(self) -> int:
        if self.steps_per_half_cycle is None:
            return 1
        return (
            self.total_steps + self.steps_per_half_cycle - 1
        ) // self.steps_per_half_cycle

    @property
    def protocol_warnings(self) -> tuple[str, ...]:
        if self.steps_per_half_cycle is None:
            return ()
        required_steps = self.steps_per_half_cycle * self.half_cycles
        if self.total_steps >= required_steps:
            return ()
        return (
            "cyclic loading requests "
            f"half_cycles={self.half_cycles} with "
            f"steps_per_half_cycle={self.steps_per_half_cycle}, requiring at least "
            f"{required_steps} loading steps, but only {self.total_steps} are configured; "
            "the run will end before all requested half-cycles complete",
        )

    @property
    def loading_cycles(self) -> int:
        return (self.loading_half_cycles + 1) // 2


@dataclass(frozen=True)
class LoadingStepState:
    """Resolved loading state for one output point."""

    increment_stage: str
    loading_direction: str
    loading_half_cycle: int
    loading_cycle: int
    step_in_loading_half_cycle: int
    target_strain: float
    target_displacement: float
    step_displacement: float


@dataclass(frozen=True)
class MinimizationConfig:
    style: str = SHEAR_MIN_STYLE
    etol: float = SHEAR_MIN_ETOL
    ftol: float = SHEAR_MIN_FTOL
    maxiter: int = SHEAR_MIN_MAXITER
    maxeval: int = SHEAR_MIN_MAXEVAL


@dataclass(frozen=True)
class OutputConfig:
    dump_every: int = SHEAR_DUMP_EVERY
    thermo_every: int = SHEAR_THERMO_EVERY


@dataclass(frozen=True)
class GBShearConfig:
    version: int
    description: str
    source: SourceConfig
    material: MaterialConfig
    simulation: SimulationConfig
    potential: PotentialConfig
    loading: LoadingConfig
    minimization: MinimizationConfig
    output: OutputConfig


def _relative_artifact_name(value: Any, *, label: str) -> str:
    text = nonempty_text(value, label=label)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts or path == Path("."):
        raise ValueError(f"{label} must be a relative path inside source.output_dir")
    return path.as_posix()


def build_gb_shear_config(data: dict[str, Any]) -> GBShearConfig:
    if not isinstance(data, dict):
        raise ValueError("gb_shear config root must be an object")
    if "version" not in data:
        raise ValueError("gb_shear config.version is required")
    version = int(data["version"])
    if version != 1:
        raise ValueError(
            f"unsupported gb_shear config version {version}; expected version 1"
        )

    source_values = config_object(data.get("source"), label="source", required=True)
    parent_workflow = nonempty_text(
        source_values.get("parent_workflow"), label="source.parent_workflow"
    )
    if parent_workflow not in {"gb_minimize", "gb_shear"}:
        raise ValueError("source.parent_workflow must be 'gb_minimize' or 'gb_shear'")
    try:
        initial_cumulative_shear_strain = float(
            source_values["initial_cumulative_shear_strain"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "source.initial_cumulative_shear_strain must be a finite number"
        ) from exc
    if not math.isfinite(initial_cumulative_shear_strain):
        raise ValueError("source.initial_cumulative_shear_strain must be finite")
    source = SourceConfig(
        output_dir=nonempty_text(
            source_values.get("output_dir"), label="source.output_dir"
        ),
        parent_run_id=nonempty_text(
            source_values.get("parent_run_id"), label="source.parent_run_id"
        ),
        parent_workflow=parent_workflow,
        initial_cumulative_shear_strain=initial_cumulative_shear_strain,
        data=_relative_artifact_name(
            source_values.get("data"), label="source.data"
        ),
        top_boundary_ids=_relative_artifact_name(
            source_values.get("top_boundary_ids", "top_boundary.ids"),
            label="source.top_boundary_ids",
        ),
        bottom_boundary_ids=_relative_artifact_name(
            source_values.get("bottom_boundary_ids", "bot_boundary.ids"),
            label="source.bottom_boundary_ids",
        ),
    )

    material_values = config_object(
        data.get("material"), label="material", required=True
    )
    element = nonempty_text(material_values.get("element"), label="material.element")
    if "mass" not in material_values:
        raise ValueError("material.mass is required")
    mass = float(material_values["mass"])
    if mass <= 0.0:
        raise ValueError("material.mass must be positive")
    material = MaterialConfig(element=element, mass=mass)

    simulation_values = config_object(data.get("simulation"), label="simulation")
    boundary_tokens = str(
        simulation_values.get("boundary", SimulationConfig().boundary)
    ).split()
    if len(boundary_tokens) != 3 or any(
        token not in {"p", "f", "s", "m"} for token in boundary_tokens
    ):
        raise ValueError(
            "simulation.boundary must contain three LAMMPS boundary tokens"
        )
    simulation = SimulationConfig(boundary=" ".join(boundary_tokens))

    potential_values = config_object(
        data.get("potential"), label="potential", required=True
    )
    potential = PotentialConfig(
        pair_style=nonempty_text(
            potential_values.get("pair_style"), label="potential.pair_style"
        ),
        file=nonempty_text(potential_values.get("file"), label="potential.file"),
    )

    loading_values = config_object(data.get("loading"), label="loading")
    loading_defaults = LoadingConfig()
    if "steps_per_half_cycle" not in loading_values:
        raise ValueError(
            "loading.steps_per_half_cycle is required in config version 1; "
            "use null for unidirectional loading"
        )
    load_type = str(
        loading_values.get("load_type", loading_defaults.load_type)
    ).strip().lower()
    if load_type not in {"spring_force", "displacement"}:
        raise ValueError(
            "loading.load_type must be 'spring_force' or 'displacement' "
            "in config version 1"
        )
    if load_type == "spring_force":
        top_spring_per_atom = loading_values.get(
            "top_spring_per_atom", loading_defaults.top_spring_per_atom
        )
        bottom_spring_per_atom = loading_values.get(
            "bottom_spring_per_atom", loading_defaults.bottom_spring_per_atom
        )
        if top_spring_per_atom is None or bottom_spring_per_atom is None:
            raise ValueError(
                "loading spring stiffness must be non-null for spring_force"
            )
        top_spring_per_atom = float(top_spring_per_atom)
        bottom_spring_per_atom = float(bottom_spring_per_atom)
    else:
        top_spring_per_atom = None
        bottom_spring_per_atom = None
    loading = LoadingConfig(
        load_type=load_type,
        direction=str(
            loading_values.get("direction", loading_defaults.direction)
        ).strip().lower(),
        initial_strain_increment=float(
            loading_values.get(
                "initial_strain_increment",
                loading_defaults.initial_strain_increment,
            )
        ),
        initial_steps=int(
            loading_values.get("initial_steps", loading_defaults.initial_steps)
        ),
        fine_strain_increment=float(
            loading_values.get(
                "fine_strain_increment", loading_defaults.fine_strain_increment
            )
        ),
        fine_steps=int(
            loading_values.get("fine_steps", loading_defaults.fine_steps)
        ),
        steps_per_half_cycle=(
            None
            if loading_values["steps_per_half_cycle"] is None
            else int(loading_values["steps_per_half_cycle"])
        ),
        half_cycles=int(
            loading_values.get("half_cycles", loading_defaults.half_cycles)
        ),
        top_spring_per_atom=top_spring_per_atom,
        bottom_spring_per_atom=bottom_spring_per_atom,
    )
    if loading.direction not in {"x", "-x"}:
        raise ValueError(
            "loading.direction must be 'x' or '-x' in config version 1"
        )
    for label, count in (
        ("initial_steps", loading.initial_steps),
        ("fine_steps", loading.fine_steps),
    ):
        if count < 0:
            raise ValueError(f"loading.{label} must be non-negative")
    if loading.total_steps <= 0:
        raise ValueError("loading must contain at least one loading step")
    if loading.steps_per_half_cycle is not None and loading.steps_per_half_cycle <= 0:
        raise ValueError("loading.steps_per_half_cycle must be positive or null")
    if loading.half_cycles <= 0:
        raise ValueError("loading.half_cycles must be positive")
    if loading.steps_per_half_cycle is None and loading.half_cycles != 1:
        raise ValueError(
            "loading.half_cycles must be 1 when steps_per_half_cycle is null"
        )
    for label, increment in (
        ("initial_strain_increment", loading.initial_strain_increment),
        ("fine_strain_increment", loading.fine_strain_increment),
    ):
        if increment < 0.0:
            raise ValueError(f"loading.{label} must be a non-negative magnitude")
    for label, stiffness in (
        ("top_spring_per_atom", loading.top_spring_per_atom),
        ("bottom_spring_per_atom", loading.bottom_spring_per_atom),
    ):
        if loading.load_type == "spring_force" and (
            stiffness is None or stiffness <= 0.0
        ):
            raise ValueError(f"loading.{label} must be positive")

    minimization_values = config_object(
        data.get("minimization"), label="minimization"
    )
    minimization_defaults = MinimizationConfig()
    minimization = MinimizationConfig(
        style=str(
            minimization_values.get("style", minimization_defaults.style)
        ).strip().lower(),
        etol=float(
            minimization_values.get("etol", minimization_defaults.etol)
        ),
        ftol=float(
            minimization_values.get("ftol", minimization_defaults.ftol)
        ),
        maxiter=int(
            minimization_values.get("maxiter", minimization_defaults.maxiter)
        ),
        maxeval=int(
            minimization_values.get("maxeval", minimization_defaults.maxeval)
        ),
    )
    if minimization.style not in {"cg", "fire"}:
        raise ValueError(
            "minimization.style must be 'cg' or 'fire' in config version 1"
        )
    if minimization.etol < 0.0 or minimization.ftol <= 0.0:
        raise ValueError(
            "minimization tolerances must satisfy etol >= 0 and ftol > 0"
        )
    if minimization.maxiter <= 0 or minimization.maxeval <= 0:
        raise ValueError("minimization maxiter and maxeval must be positive")

    output_values = config_object(data.get("output"), label="output")
    output_defaults = OutputConfig()
    output = OutputConfig(
        dump_every=int(
            output_values.get("dump_every", output_defaults.dump_every)
        ),
        thermo_every=int(
            output_values.get("thermo_every", output_defaults.thermo_every)
        ),
    )
    if output.dump_every <= 0 or output.thermo_every <= 0:
        raise ValueError(
            "output.dump_every and output.thermo_every must be positive"
        )

    return GBShearConfig(
        version=version,
        description=str(data.get("description", "")),
        source=source,
        material=material,
        simulation=simulation,
        potential=potential,
        loading=loading,
        minimization=minimization,
        output=output,
    )


def config_to_dict(config: GBShearConfig) -> dict[str, Any]:
    return {
        "version": config.version,
        "description": config.description,
        "source": asdict(config.source),
        "material": asdict(config.material),
        "simulation": asdict(config.simulation),
        "potential": asdict(config.potential),
        "loading": asdict(config.loading),
        "minimization": asdict(config.minimization),
        "output": asdict(config.output),
    }


def load_gb_shear_config(path: Path | str) -> GBShearConfig:
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"GB shear config not found: {config_path}")
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return build_gb_shear_config(data)


def target_loading_state(
    step: int,
    loading: LoadingConfig,
    *,
    slab_spacing: float,
) -> LoadingStepState:
    """Resolve direction, cycle indices, and target displacement for one step."""
    if slab_spacing <= 0.0:
        raise ValueError("slab_spacing must be positive")
    if step < 0 or step > loading.total_steps:
        raise ValueError(f"step must be between 0 and {loading.total_steps}")

    half_cycle_steps = loading.steps_per_half_cycle
    half_cycle_index = (
        0
        if step == 0 or half_cycle_steps is None
        else (step - 1) // half_cycle_steps
    )
    initial_direction_sign = -1.0 if loading.direction == "-x" else 1.0
    direction_sign = (
        initial_direction_sign
        if half_cycle_index % 2 == 0
        else -initial_direction_sign
    )
    loading_direction = "x" if direction_sign > 0.0 else "-x"
    if step == 0:
        increment_stage = "reference"
        step_in_half_cycle = 0
        increment = 0.0
    elif step <= loading.initial_steps:
        increment_stage = "initial"
        step_in_half_cycle = (
            step if half_cycle_steps is None else (step - 1) % half_cycle_steps + 1
        )
        increment = loading.initial_strain_increment
    else:
        increment_stage = "fine"
        step_in_half_cycle = (
            step if half_cycle_steps is None else (step - 1) % half_cycle_steps + 1
        )
        increment = loading.fine_strain_increment

    strain = 0.0
    increment_ranges = (
        (1, min(step, loading.initial_steps), loading.initial_strain_increment),
        (
            loading.initial_steps + 1,
            step,
            loading.fine_strain_increment,
        ),
    )
    for start_step, end_step, strain_increment in increment_ranges:
        if start_step > end_step or strain_increment == 0.0:
            continue
        if half_cycle_steps is None:
            strain += (
                (end_step - start_step + 1)
                * strain_increment
                * initial_direction_sign
            )
            continue
        cursor = start_step
        while cursor <= end_step:
            segment_index = (cursor - 1) // half_cycle_steps
            segment_end = min(
                end_step, (segment_index + 1) * half_cycle_steps
            )
            segment_sign = (
                initial_direction_sign
                if segment_index % 2 == 0
                else -initial_direction_sign
            )
            strain += (
                (segment_end - cursor + 1)
                * strain_increment
                * segment_sign
            )
            cursor = segment_end + 1

    return LoadingStepState(
        increment_stage=increment_stage,
        loading_direction=loading_direction,
        loading_half_cycle=half_cycle_index + 1,
        loading_cycle=half_cycle_index // 2 + 1,
        step_in_loading_half_cycle=step_in_half_cycle,
        target_strain=strain,
        target_displacement=strain * slab_spacing,
        step_displacement=increment * direction_sign * slab_spacing,
    )


def shear_observables(
    *,
    top_xcm: float,
    bottom_xcm: float,
    top_ycm: float,
    bottom_ycm: float,
    initial_relative_x: float,
    slab_spacing: float,
    top_spring_force_x: float | None,
    lx: float,
    lz: float,
) -> dict[str, float | None]:
    if slab_spacing <= 0.0:
        raise ValueError("slab_spacing must be positive")
    area = lx * lz
    if area <= 0.0:
        raise ValueError("lx * lz must be positive")
    displacement = (top_xcm - bottom_xcm) - initial_relative_x
    current_slab_spacing = abs(top_ycm - bottom_ycm)
    if current_slab_spacing <= 0.0:
        raise ValueError("current top/bottom y COM spacing must be positive")
    return {
        "actual_shear_strain": displacement / current_slab_spacing,
        "current_slab_spacing_angstrom": current_slab_spacing,
        "top_shear_stress_mpa": (
            None
            if top_spring_force_x is None
            else top_spring_force_x / area * EV_PER_ANGSTROM3_TO_MPA
        ),
    }


def read_atom_ids(path: Path | str) -> list[int]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Boundary atom ID file not found: {source}")
    atom_ids: list[int] = []
    for line_no, raw in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        text = raw.strip()
        if not text:
            continue
        try:
            atom_id = int(text)
        except ValueError as exc:
            raise ValueError(
                f"{source}:{line_no} must contain one positive integer atom ID"
            ) from exc
        if atom_id <= 0:
            raise ValueError(f"{source}:{line_no} must contain one positive integer atom ID")
        atom_ids.append(atom_id)
    if not atom_ids:
        raise ValueError(f"Boundary atom ID file is empty: {source}")
    if len(set(atom_ids)) != len(atom_ids):
        raise ValueError(f"Boundary atom ID file contains duplicate IDs: {source}")
    return atom_ids


def validate_boundary_ids(top_ids: list[int], bottom_ids: list[int]) -> None:
    if not top_ids or not bottom_ids:
        raise ValueError("top and bottom boundary ID lists must both be non-empty")
    overlap = set(top_ids).intersection(bottom_ids)
    if overlap:
        sample = ", ".join(str(atom_id) for atom_id in sorted(overlap)[:8])
        raise ValueError(f"top and bottom boundary ID lists overlap: {sample}")
