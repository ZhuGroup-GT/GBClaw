"""Shared execution-mode settings for Structure and LAMMPS workloads."""

from core.settings.env import get_env_int


SERIAL_EXECUTION = (
    get_env_int("GBCLAW_SERIAL_EXECUTION", 1, minimum=0, maximum=1) == 1
)
