"""The LAMMPS worker's tool set and the potential catalog tool."""

from __future__ import annotations

import json

from langchain_core.tools import tool

from domains.lammps.cfg_convert.tool import convert_cfg_to_lammps
from domains.lammps.common.potentials import (
    format_potentials_catalog,
)
from domains.lammps.energy.tool import run_gb_energy, write_gb_energy_config
from domains.lammps.minimize.tool import (
    estimate_gb_box_size,
    run_gb_minimize,
    write_gb_minimize_config,
)
from domains.lammps.neb.tool import run_gb_neb, write_gb_neb_config
from domains.lammps.orientation import infer_tilt_orientation
from domains.lammps.shear.tool import run_gb_shear, write_gb_shear_config
from domains.lammps.structure_minimize.tool import (
    run_structure_minimize,
    write_structure_minimize_config,
)


@tool
def list_available_potentials(element: str = "") -> str:
    """List interatomic potentials registered in GBclaw metadata.

    Reads ``potentials.json`` from the configured potentials directory (not a
    directory scan).
    Use before write_gb_minimize_config when the task specifies a non-Fe element
    or asks which potentials are available.

    element: optional chemical symbol filter (e.g. ``Ni``) — return only potentials
    that include that element.
    """
    try:
        return format_potentials_catalog(element=element)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return f"Failed to load potentials metadata: {exc}"


LAMMPS_AGENT_TOOLS = [
    list_available_potentials,
    convert_cfg_to_lammps,
    infer_tilt_orientation,
    estimate_gb_box_size,
    write_gb_minimize_config,
    run_gb_minimize,
    write_structure_minimize_config,
    run_structure_minimize,
    write_gb_energy_config,
    run_gb_energy,
    write_gb_shear_config,
    run_gb_shear,
    write_gb_neb_config,
    run_gb_neb,
]


def lammps_agent_tools() -> list:
    """Tool provider for the LAMMPS worker (see domains/lammps/extension.py)."""
    return list(LAMMPS_AGENT_TOOLS)


def lammps_orchestrator_tools(*, project_store=None):
    """The orchestrator's own potential catalog (see domains/lammps/extension.py)."""
    from agents.orchestrator.contributions import OrchestratorTools

    return OrchestratorTools(tools=(list_available_potentials,))

