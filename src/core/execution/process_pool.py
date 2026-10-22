"""Shared CPU-bound process pool bound to distinct physical CPU cores (used by Structure).

Uses the "forkserver" start method on POSIX so worker processes are forked from
a clean server process (safe with uvicorn threads) without re-executing the
application entry script (unlike "spawn").
"""

from __future__ import annotations

import atexit
import os
import signal
import sys
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path
from threading import Lock
from typing import Callable, TypeVar

from threadpoolctl import threadpool_limits

from core.settings.execution import SERIAL_EXECUTION
from core.settings.env import get_env_int

T = TypeVar("T")
R = TypeVar("R")

_executor: ProcessPoolExecutor | None = None
_executor_lock = Lock()
_serial_pool_use_lock = Lock()
_worker_thread_limit = None


def _physical_core_cpu_sets() -> tuple[tuple[int, ...], ...]:
    """Group allowed Linux CPU IDs by socket/core, including their SMT siblings."""
    if not hasattr(os, "sched_getaffinity") or not hasattr(os, "sched_setaffinity"):
        raise RuntimeError("Structure physical-core binding requires Linux CPU affinity support")
    cores: dict[tuple[int, int], list[int]] = {}
    for cpu in sorted(os.sched_getaffinity(0)):
        topology = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
        try:
            socket_id = int((topology / "physical_package_id").read_text())
            core_id = int((topology / "core_id").read_text())
            if socket_id < 0 or core_id < 0:
                raise ValueError("physical CPU topology is unavailable")
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Cannot determine the physical core for CPU {cpu}") from exc
        cores.setdefault((socket_id, core_id), []).append(cpu)
    return tuple(tuple(cpus) for cpus in cores.values())


def _worker_count_for_cores(core_count: int) -> int:
    return min(get_env_int("GBCLAW_WORKERS", 18, minimum=1), core_count)


def _mp_context():
    if sys.platform == "win32":
        return get_context("spawn")
    return get_context("forkserver")


def _worker_init(core_cpu_sets: tuple[tuple[int, ...], ...], next_core_index) -> None:
    """Bind each pool process to its own physical core and ignore SIGINT.

    Ctrl+C is delivered to the whole process group. Without this, idle
    ForkServerProcess workers dump KeyboardInterrupt tracebacks while the
    main CLI/web process is still handling the signal cleanly.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    for name in (
        "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
    ):
        os.environ[name] = "1"
    with next_core_index.get_lock():
        core_index = next_core_index.value
        next_core_index.value += 1
    cpu_set = core_cpu_sets[core_index]
    os.sched_setaffinity(0, cpu_set)
    # Libraries imported before this initializer may already have helper threads.
    for thread in Path("/proc/self/task").iterdir():
        try:
            os.sched_setaffinity(int(thread.name), cpu_set)
        except ProcessLookupError:
            pass  # The thread exited between listing it and setting its affinity.
    global _worker_thread_limit
    _worker_thread_limit = threadpool_limits(limits=1)


def worker_count() -> int:
    """Return the shared pool limit, capped by the available physical core count."""
    return _worker_count_for_cores(len(_physical_core_cpu_sets()))


def get_process_pool() -> ProcessPoolExecutor:
    """Return the shared process pool, creating it on first use."""
    global _executor
    with _executor_lock:
        if _executor is None:
            core_cpu_sets = _physical_core_cpu_sets()
            n_workers = _worker_count_for_cores(len(core_cpu_sets))
            context = _mp_context()
            _executor = ProcessPoolExecutor(
                max_workers=n_workers,
                mp_context=context,
                initializer=_worker_init,
                initargs=(core_cpu_sets[:n_workers], context.Value("i", 0)),
            )
            atexit.register(shutdown_process_pool)
        return _executor


def shutdown_process_pool() -> None:
    """Shut down the shared pool (app shutdown or process exit)."""
    global _executor
    with _executor_lock:
        executor = _executor
        _executor = None
    if executor is None:
        return
    executor.shutdown(wait=False, cancel_futures=True)


def _noop_worker(_: int) -> int:
    return 0


def warmup_process_pool() -> None:
    """Start forkserver + worker processes before the first user request."""
    pool = get_process_pool()
    list(pool.map(_noop_worker, range(min(worker_count(), 4))))


def parallel_map(func: Callable[[T], R], items: list[T]) -> list[R]:
    """Map *func* over *items* within the shared Structure worker budget."""
    if not items:
        return []
    n_workers = min(worker_count(), len(items))
    chunksize = max(1, len(items) // (n_workers * 4))
    pool = get_process_pool()
    if not SERIAL_EXECUTION:
        return list(pool.map(func, items, chunksize=chunksize))
    with _serial_pool_use_lock:
        return list(pool.map(func, items, chunksize=chunksize))
