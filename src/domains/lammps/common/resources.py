"""MPI-rank admission for LAMMPS subprocesses."""

from __future__ import annotations

from contextlib import contextmanager

from core.execution.resources import SlotPool
from core.settings.execution import SERIAL_EXECUTION
from domains.lammps.common.settings import LAMMPS_TOTAL_MPI_RANKS

_POOL = SlotPool(LAMMPS_TOTAL_MPI_RANKS, serial=SERIAL_EXECUTION)


@contextmanager
def lammps_resource_slot(mpi_ranks: int, *, session_key: str | None = None):
    """Reserve the actual MPI ranks used by one LAMMPS subprocess."""
    requested_ranks = int(mpi_ranks)
    if requested_ranks < 1:
        raise ValueError("mpi_ranks must be >= 1")
    if requested_ranks > LAMMPS_TOTAL_MPI_RANKS:
        raise ValueError(
            f"LAMMPS run requires {requested_ranks} MPI ranks, exceeding "
            f"GBCLAW_LAMMPS_TOTAL_MPI_RANKS={LAMMPS_TOTAL_MPI_RANKS}"
        )
    with _POOL.acquire(
        requested_ranks, session_key=session_key,
        cancel_message="LAMMPS task was cancelled while waiting for resources.",
    ):
        yield
