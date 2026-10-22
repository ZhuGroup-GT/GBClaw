"""Validated configuration for the grain-boundary energy workflow.

The construction/material/minimization fields intentionally reuse the
``GBMinimizeConfig`` schema.  GB energy adds only output cadence and two
scientific invariants required by the implemented reference subtraction:
continuous periodic x and non-zero y vacuum. The topology is fixed to one
internal GB plus two outer y free surfaces; its energy denominator is A.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from domains.lammps.minimize.config import (
    GBMinimizeConfig,
    config_to_dict as gb_minimize_config_to_dict,
    parse_gb_minimize_config,
)


GB_ENERGY_TOPOLOGY = {
    "name": "single_gb_with_two_y_free_surfaces",
    "interface_count": 1,
    "free_surface_count": 2,
    "gb_normal_axis": "y",
    "periodic_in_plane_axes": ["x", "z"],
    "vacuum_axis": "y",
    "energy_denominator": "A",
}


@dataclass(frozen=True)
class EnergyOutputConfig:
    thermo_every: int = 100
    dump_every: int = 100


@dataclass(frozen=True)
class GBEnergyConfig:
    construction: GBMinimizeConfig
    output: EnergyOutputConfig = EnergyOutputConfig()

    @property
    def version(self) -> int:
        return self.construction.version

    @property
    def description(self) -> str:
        return self.construction.description

    @property
    def material(self):
        return self.construction.material

    @property
    def grains(self):
        return self.construction.grains

    @property
    def simulation(self):
        return self.construction.simulation

    @property
    def potential(self):
        return self.construction.potential


def _parse_output(data: Any) -> EnergyOutputConfig:
    if data is None:
        return EnergyOutputConfig()
    if not isinstance(data, dict):
        raise ValueError("output must be an object")
    defaults = EnergyOutputConfig()
    thermo_every = int(data.get("thermo_every", defaults.thermo_every))
    dump_every = int(data.get("dump_every", defaults.dump_every))
    if thermo_every <= 0:
        raise ValueError("output.thermo_every must be positive")
    if dump_every <= 0:
        raise ValueError("output.dump_every must be positive")
    return EnergyOutputConfig(
        thermo_every=thermo_every,
        dump_every=dump_every,
    )


def parse_gb_energy_config(data: dict[str, Any]) -> GBEnergyConfig:
    if not isinstance(data, dict):
        raise ValueError("gb_energy config root must be an object")
    construction = parse_gb_minimize_config(data)
    if not construction.simulation.x_pbc:
        raise ValueError(
            "gb_energy requires simulation.x_pbc=true: the implemented excess-energy "
            "formula subtracts y free surfaces and assumes a continuous periodic x-z "
            "interface"
        )
    if construction.simulation.y_vacuum <= 0.0:
        raise ValueError(
            "gb_energy requires simulation.y_vacuum > 0 to isolate the y free surfaces"
        )
    return GBEnergyConfig(
        construction=construction,
        output=_parse_output(data.get("output")),
    )


def build_gb_energy_config(data: dict[str, Any] | None = None) -> GBEnergyConfig:
    if data is None:
        raise ValueError("gb_energy config data is required")
    return parse_gb_energy_config(data)


def config_to_dict(config: GBEnergyConfig) -> dict[str, Any]:
    payload = gb_minimize_config_to_dict(config.construction)
    payload["output"] = asdict(config.output)
    return payload


def load_gb_energy_config(path: Path | str) -> GBEnergyConfig:
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"GB energy config not found: {config_path}")
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("gb_energy config root must be a JSON object")
    return parse_gb_energy_config(data)


def save_gb_energy_config(config: GBEnergyConfig, path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(config_to_dict(config), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


__all__ = [
    "EnergyOutputConfig",
    "GB_ENERGY_TOPOLOGY",
    "GBEnergyConfig",
    "build_gb_energy_config",
    "config_to_dict",
    "load_gb_energy_config",
    "parse_gb_energy_config",
    "save_gb_energy_config",
]
