"""Process-wide compute-slot admission for disposable coding workers."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from core.execution.resources import SlotLease, SlotPool
from core.execution.sessions import is_session_cancelled
from domains.coding.budget import CodingTaskBudget


@dataclass(frozen=True)
class CodingResourceAllocation:
    """Host-authored resource allocation persisted with one coding run."""

    compute_slots: int
    mpi_ranks: int
    total_compute_slots: int

    @property
    def mpi_enabled(self) -> bool:
        return self.mpi_ranks > 1

    def public_record(self) -> dict[str, int | bool]:
        return {
            "compute_slots": self.compute_slots,
            "mpi_ranks": self.mpi_ranks,
            "mpi_enabled": self.mpi_enabled,
            "total_compute_slots": self.total_compute_slots,
        }


class CodingResourceLease:
    """Idempotently release one allocation back to its owning pool."""

    def __init__(self, lease: SlotLease, allocation: CodingResourceAllocation) -> None:
        self._lease = lease
        self.allocation = allocation

    def release(self) -> None:
        self._lease.release()

    def __enter__(self) -> CodingResourceAllocation:
        return self.allocation

    def __exit__(self, *_args: object) -> None:
        self.release()


class CodingResourcePool:
    """Bound aggregate coding concurrency by declared compute slots."""

    def __init__(self, total_compute_slots: int) -> None:
        total = int(total_compute_slots)
        if total < 1:
            raise ValueError("total_compute_slots must be >= 1")
        self.total_compute_slots = total
        self._slots = SlotPool(total)

    def acquire(
        self,
        *,
        compute_slots: int,
        mpi_ranks: int = 1,
        session_key: str | None = None,
        task_budget: CodingTaskBudget | None = None,
    ) -> CodingResourceLease:
        requested = int(compute_slots)
        ranks = int(mpi_ranks)
        if requested < 1:
            raise ValueError("compute_slots must be >= 1")
        if ranks < 1 or ranks > requested:
            raise ValueError("mpi_ranks must be between 1 and compute_slots")
        if requested > self.total_compute_slots:
            raise ValueError(
                f"coding task requires {requested} compute slots, exceeding "
                f"the total budget of {self.total_compute_slots}"
            )
        if task_budget is not None:
            task_budget.check()
        if is_session_cancelled(session_key):
            raise RuntimeError("Coding task was cancelled before resource allocation.")
        lease = self._slots.acquire(
            requested, session_key=session_key, budget=task_budget,
            cancel_message="Coding task was cancelled while waiting for compute resources.",
        )
        allocation = CodingResourceAllocation(
            compute_slots=requested,
            mpi_ranks=ranks,
            total_compute_slots=self.total_compute_slots,
        )
        return CodingResourceLease(lease, allocation)

    def snapshot(self) -> dict[str, int]:
        state = self._slots.snapshot()
        return {
            "total_compute_slots": state["total_slots"],
            "compute_slots_in_use": state["slots_in_use"],
            "available_compute_slots": state["available_slots"],
            "active_runs": state["active_runs"],
        }


_GLOBAL_POOL_LOCK = threading.Lock()
_GLOBAL_POOL: CodingResourcePool | None = None


def get_coding_resource_pool(total_compute_slots: int) -> CodingResourcePool:
    """Return the one process-wide pool shared by every orchestrator graph."""
    global _GLOBAL_POOL
    with _GLOBAL_POOL_LOCK:
        if _GLOBAL_POOL is None:
            _GLOBAL_POOL = CodingResourcePool(total_compute_slots)
        elif _GLOBAL_POOL.total_compute_slots != int(total_compute_slots):
            raise RuntimeError(
                "coding total compute slots changed after pool initialization; "
                "restart GBclaw to apply the new value"
            )
        return _GLOBAL_POOL


__all__ = [
    "CodingResourceAllocation",
    "CodingResourceLease",
    "CodingResourcePool",
    "get_coding_resource_pool",
]
