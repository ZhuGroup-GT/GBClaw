"""Process-wide compute-slot admission that yields to session cancellation."""

from __future__ import annotations

import threading
from typing import Protocol

from core.execution.sessions import SessionCancelledError, add_cancel_listener, is_session_cancelled


class AdmissionBudget(Protocol):
    """A deadline that may also stop admission (e.g. a coding task timeout)."""

    def check(self) -> None: ...

    def remaining(self) -> float: ...


class SlotLease:
    """Idempotently return one admitted allocation to its pool."""

    def __init__(self, pool: SlotPool, slots: int) -> None:
        self.pool = pool
        self.slots = slots
        self._released = False
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self.pool._release(self.slots)

    def __enter__(self) -> SlotLease:
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


class SlotPool:
    """Bound concurrent work by the slots each run declares.

    In serial mode a pool admits one run at a time regardless of its size.
    Waiters poll their session so a cancelled turn never takes resources.
    """

    def __init__(self, total_slots: int, *, serial: bool = False, poll_interval: float = 0.1) -> None:
        total = int(total_slots)
        if total < 1:
            raise ValueError("total_slots must be >= 1")
        self.total_slots = total
        self.serial = serial
        self.poll_interval = poll_interval
        self._condition = threading.Condition()
        self._slots_in_use = 0
        self._active_runs = 0
        add_cancel_listener(self._wake)

    def acquire(
        self,
        slots: int,
        *,
        session_key: str | None = None,
        budget: AdmissionBudget | None = None,
        cancel_message: str = "Execution was cancelled while waiting for resources.",
    ) -> SlotLease:
        requested = int(slots)
        if requested < 1 or requested > self.total_slots:
            raise ValueError(f"slots must be between 1 and {self.total_slots}")
        with self._condition:
            while True:
                if budget is not None:
                    budget.check()
                if is_session_cancelled(session_key):
                    raise SessionCancelledError(cancel_message)
                if (self._active_runs == 0 if self.serial
                        else self._slots_in_use + requested <= self.total_slots):
                    break
                timeout = self.poll_interval
                if budget is not None:
                    timeout = min(timeout, budget.remaining())
                self._condition.wait(timeout=timeout)
            self._slots_in_use += requested
            self._active_runs += 1
        return SlotLease(self, requested)

    def snapshot(self) -> dict[str, int]:
        with self._condition:
            return {
                "total_slots": self.total_slots,
                "slots_in_use": self._slots_in_use,
                "available_slots": self.total_slots - self._slots_in_use,
                "active_runs": self._active_runs,
            }

    def _release(self, slots: int) -> None:
        with self._condition:
            self._slots_in_use -= slots
            self._active_runs -= 1
            if self._slots_in_use < 0 or self._active_runs < 0:
                raise RuntimeError("resource pool accounting underflow")
            self._condition.notify_all()

    def _wake(self) -> None:
        with self._condition:
            self._condition.notify_all()


__all__ = ["AdmissionBudget", "SlotLease", "SlotPool"]
