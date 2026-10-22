"""Reentrant, process-shared locks for a project's derived file updates."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
from pathlib import Path
import threading

_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}
_held = threading.local()


@contextmanager
def file_lock(path: Path):
    key = str(path.resolve())
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_held, "paths", set())
        if key in held:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            _held.paths = held | {key}
            try:
                yield
            finally:
                _held.paths = held
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
