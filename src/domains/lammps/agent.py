"""LAMMPS worker: runs external LAMMPS commands (does not talk to the user).

Uses the registered LAMMPS tools, which run subprocesses in the Conda environment
configured by GBCLAW_LAMMPS_CONDA_ENV.
Invoked statelessly by delegate_lammps; results return to the orchestrator via WorkerResult.
"""

from __future__ import annotations

from agents.workers.spec import WorkerSpec
from domains.lammps.prompt import LAMMPS_AGENT_PROMPT

LAMMPS_WORKER = WorkerSpec(
    agent="lammps",
    label="LAMMPS agent",
    status_message="LAMMPS simulation running…",
    prompt=LAMMPS_AGENT_PROMPT,
)
create_lammps_agent = LAMMPS_WORKER.create_graph
invoke_lammps_agent = LAMMPS_WORKER.invoke

__all__ = ["LAMMPS_WORKER", "create_lammps_agent", "invoke_lammps_agent"]
