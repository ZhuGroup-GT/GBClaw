"""Bicrystal grain-boundary minimization configuration schema (JSON input deck).

Uses only the Python standard library so the minimize engine can load configs inside the
configured LAMMPS environment without extra dependencies.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from domains.lammps.common.potential_files import current_potential_path
from core.paths import REPO_ROOT

_REPO_ROOT = REPO_ROOT

_VALID_LATTICES = frozenset({"bcc", "fcc", "hcp"})
_VALID_BOUNDARY_TOKENS = frozenset({"p"})

RepeatCount = int | float
RepeatTriplet = tuple[RepeatCount, RepeatCount, RepeatCount]


@dataclass(frozen=True)
class Orientation:
    x: tuple[int, int, int]
    y: tuple[int, int, int]
    z: tuple[int, int, int]

    def as_dict(self) -> dict[str, list[int]]:
        return {"x": list(self.x), "y": list(self.y), "z": list(self.z)}


@dataclass(frozen=True)
class GrainConfig:
    orientation: Orientation
    size: RepeatTriplet


@dataclass(frozen=True)
class GrainsConfig:
    top: GrainConfig
    bottom: GrainConfig


@dataclass(frozen=True)
class MaterialConfig:
    lattice: str = "bcc"
    element: str = "Fe"
    mass: float = 55.85  # the Fe mass in the default Men03.eam.fs potential


@dataclass(frozen=True)
class SimulationConfig:
    a0: float = 2.8553
    boundary: str = "p p p"
    y_vacuum: float = 20.0
    y_eps: float = 1.0e-2
    x_pbc: bool = False
    boundary_slab_thickness: float = 3.0
    enable_initial_staged_relaxation: bool = False
    min_style: str = "cg"
    min_etol: float = 0.0
    min_ftol: float = 1.0e-6
    min_maxiter: int = 1000
    min_maxeval: int = 10000
    relax_cycles: int = 4


@dataclass(frozen=True)
class PotentialConfig:
    pair_style: str
    file: str

    def resolve_path(self, *, repo_root: Path | None = None, config_dir: Path | None = None) -> Path:
        candidate = Path(self.file)
        if candidate.is_absolute():
            return current_potential_path(candidate).resolve()

        roots: list[Path] = []
        if config_dir is not None:
            roots.append(config_dir)
        if repo_root is not None:
            roots.append(repo_root)
        roots.append(_REPO_ROOT)

        for root in roots:
            resolved = (root / candidate).resolve()
            if resolved.is_file():
                return resolved
        return (_REPO_ROOT / candidate).resolve()


@dataclass(frozen=True)
class GBMinimizeConfig:
    version: int
    description: str
    material: MaterialConfig
    grains: GrainsConfig
    simulation: SimulationConfig
    potential: PotentialConfig


def _as_int_triplet(value: Any, *, label: str) -> tuple[int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{label} must be a list of three integers")
    try:
        parsed: list[int] = []
        for component in value:
            if isinstance(component, bool):
                raise ValueError
            numeric = float(component)
            if not math.isfinite(numeric) or not numeric.is_integer():
                raise ValueError
            parsed.append(int(numeric))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a list of three integers") from exc
    return tuple(parsed)  # type: ignore[return-value]


def _as_repeat_triplet(value: Any, *, label: str) -> RepeatTriplet:
    """Parse positive finite repeat counts without truncating user values."""
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{label} must be a list of three positive numbers")
    parsed: list[RepeatCount] = []
    try:
        for component in value:
            if isinstance(component, bool):
                raise ValueError
            numeric = float(component)
            if not math.isfinite(numeric) or numeric <= 0.0:
                raise ValueError
            parsed.append(int(numeric) if numeric.is_integer() else numeric)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{label} must be a list of three positive finite numbers"
        ) from exc
    return tuple(parsed)  # type: ignore[return-value]


def _parse_orientation(data: Any, *, label: str) -> Orientation:
    if not isinstance(data, dict):
        raise ValueError(f"{label}.orientation must be an object")
    return Orientation(
        x=_as_int_triplet(data.get("x"), label=f"{label}.orientation.x"),
        y=_as_int_triplet(data.get("y"), label=f"{label}.orientation.y"),
        z=_as_int_triplet(data.get("z"), label=f"{label}.orientation.z"),
    )


def _parse_grain(data: Any, *, label: str) -> GrainConfig:
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be an object")
    size = _as_repeat_triplet(data.get("size"), label=f"{label}.size")
    return GrainConfig(
        orientation=_parse_orientation(data.get("orientation"), label=label),
        size=size,
    )


def _parse_material(data: Any) -> MaterialConfig:
    if data is None:
        return MaterialConfig()
    if not isinstance(data, dict):
        raise ValueError("material must be an object")

    defaults = MaterialConfig()
    lattice = str(data.get("lattice", defaults.lattice)).strip().lower()
    if lattice not in _VALID_LATTICES:
        raise ValueError(f"material.lattice must be one of {sorted(_VALID_LATTICES)}")
    element = str(data.get("element", defaults.element)).strip()
    if not element:
        raise ValueError("material.element is required")
    mass = float(data.get("mass", defaults.mass))
    if not math.isfinite(mass) or mass <= 0:
        raise ValueError("material.mass must be a finite number greater than zero")
    return MaterialConfig(
        lattice=lattice,
        element=element,
        mass=mass,
    )


def _parse_simulation(data: Any) -> SimulationConfig:
    if data is None:
        return SimulationConfig()
    if not isinstance(data, dict):
        raise ValueError("simulation must be an object")

    defaults = SimulationConfig()
    x_pbc = data.get("x_pbc", defaults.x_pbc)
    if not isinstance(x_pbc, bool):
        raise ValueError("simulation.x_pbc must be a boolean")
    enable_initial_staged_relaxation = data.get(
        "enable_initial_staged_relaxation",
        defaults.enable_initial_staged_relaxation,
    )
    if not isinstance(enable_initial_staged_relaxation, bool):
        raise ValueError(
            "simulation.enable_initial_staged_relaxation must be a boolean"
        )
    a0 = float(data.get("a0", defaults.a0))
    y_vacuum = float(data.get("y_vacuum", defaults.y_vacuum))
    y_eps = float(data.get("y_eps", defaults.y_eps))
    boundary_slab_thickness = float(
        data.get("boundary_slab_thickness", defaults.boundary_slab_thickness)
    )
    min_etol = float(data.get("min_etol", defaults.min_etol))
    min_ftol = float(data.get("min_ftol", defaults.min_ftol))
    min_maxiter = int(data.get("min_maxiter", defaults.min_maxiter))
    min_maxeval = int(data.get("min_maxeval", defaults.min_maxeval))
    relax_cycles = int(data.get("relax_cycles", defaults.relax_cycles))
    for label, value in (
        ("simulation.a0", a0),
        ("simulation.y_vacuum", y_vacuum),
        ("simulation.y_eps", y_eps),
        ("simulation.boundary_slab_thickness", boundary_slab_thickness),
        ("simulation.min_etol", min_etol),
        ("simulation.min_ftol", min_ftol),
    ):
        if not math.isfinite(value):
            raise ValueError(f"{label} must be finite")
    if a0 <= 0:
        raise ValueError("simulation.a0 must be greater than zero")
    if y_vacuum < 0:
        raise ValueError("simulation.y_vacuum must be non-negative")
    if y_eps <= 0:
        raise ValueError("simulation.y_eps must be greater than zero")
    if boundary_slab_thickness <= 0:
        raise ValueError("simulation.boundary_slab_thickness must be greater than zero")
    if min_etol < 0 or min_ftol < 0:
        raise ValueError("simulation minimization tolerances must be non-negative")
    if min_maxiter <= 0 or min_maxeval <= 0:
        raise ValueError("simulation minimization iteration limits must be positive")
    if relax_cycles <= 0:
        raise ValueError("simulation.relax_cycles must be positive")

    boundary = str(data.get("boundary", defaults.boundary)).strip()
    boundary_tokens = boundary.split()
    if len(boundary_tokens) != 3 or any(
        token not in _VALID_BOUNDARY_TOKENS for token in boundary_tokens
    ):
        raise ValueError(
            "simulation.boundary must be 'p p p' "
            f"(got {' '.join(boundary_tokens)!r}). "
            "Regenerate with boundary='p p p'. For open x faces use "
            "x_pbc=false with y_vacuum; for contiguous periodic x use x_pbc=true."
        )
    min_style = str(data.get("min_style", defaults.min_style)).strip()
    if not min_style:
        raise ValueError("simulation.min_style is required")

    return SimulationConfig(
        a0=a0,
        boundary=" ".join(boundary_tokens),
        y_vacuum=y_vacuum,
        y_eps=y_eps,
        x_pbc=x_pbc,
        boundary_slab_thickness=boundary_slab_thickness,
        enable_initial_staged_relaxation=enable_initial_staged_relaxation,
        min_style=min_style,
        min_etol=min_etol,
        min_ftol=min_ftol,
        min_maxiter=min_maxiter,
        min_maxeval=min_maxeval,
        relax_cycles=relax_cycles,
    )


def _parse_potential(data: Any) -> PotentialConfig:
    if not isinstance(data, dict):
        raise ValueError("potential must be an object")
    pair_style = str(data.get("pair_style", "eam/fs")).strip()
    if not pair_style:
        raise ValueError("potential.pair_style is required")
    file_value = data.get("file")
    if not file_value:
        raise ValueError("potential.file is required")
    return PotentialConfig(pair_style=pair_style, file=str(file_value))


def parse_gb_minimize_config(data: dict[str, Any]) -> GBMinimizeConfig:
    if data.get("version", 1) != 1:
        raise ValueError("only config version 1 is supported")

    grains_data = data.get("grains")
    if grains_data is None and "top" in data and "bottom" in data:
        grains_data = {"top": data["top"], "bottom": data["bottom"]}
    if not isinstance(grains_data, dict):
        raise ValueError("grains must be an object with top and bottom entries")

    return GBMinimizeConfig(
        version=1,
        description=str(data.get("description", "")),
        material=_parse_material(data.get("material")),
        grains=GrainsConfig(
            top=_parse_grain(grains_data.get("top"), label="grains.top"),
            bottom=_parse_grain(grains_data.get("bottom"), label="grains.bottom"),
        ),
        simulation=_parse_simulation(data.get("simulation")),
        potential=_parse_potential(data.get("potential")),
    )


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def config_to_dict(config: GBMinimizeConfig) -> dict[str, Any]:
    """Serialize a validated config to a JSON-compatible dict."""
    return {
        "version": config.version,
        "description": config.description,
        "material": asdict(config.material),
        "grains": {
            "top": {
                "orientation": config.grains.top.orientation.as_dict(),
                "size": list(config.grains.top.size),
            },
            "bottom": {
                "orientation": config.grains.bottom.orientation.as_dict(),
                "size": list(config.grains.bottom.size),
            },
        },
        "simulation": asdict(config.simulation),
        "potential": asdict(config.potential),
    }


def build_gb_minimize_config(data: dict[str, Any] | None = None) -> GBMinimizeConfig:
    """Build a validated config, filling optional schema fields from dataclass defaults."""
    if data is None:
        raise ValueError("gb_minimize config data is required")
    return parse_gb_minimize_config(data)


def load_gb_minimize_config(path: Path | str) -> GBMinimizeConfig:
    """Load and validate a config from *path*."""
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"GB minimize config not found: {config_path}")

    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("GB minimize config root must be a JSON object")
    return parse_gb_minimize_config(data)


