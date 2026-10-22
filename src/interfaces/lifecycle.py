"""Start and stop what domain extensions declare for a host (the API or the CLI).

LLM providers are registered before any model is built; startup hooks run
once per process, and shutdown hooks run in reverse order, each isolated so
one failure does not skip the others.
"""

from __future__ import annotations

import logging
import threading

from core.llm.factory import register_provider
from extensions import get_registry, resolve_attribute, resolve_hook

LOGGER = logging.getLogger(__name__)
_lock = threading.Lock()
_started = False


def start_extensions() -> None:
    global _started
    with _lock:
        if _started:
            return
        registry = get_registry()
        for path in registry.llm_providers:
            register_provider(resolve_hook(path)())
        for path in registry.startup:
            resolve_hook(path)()
        _started = True


def stop_extensions() -> None:
    global _started
    with _lock:
        if not _started:
            return
        for path in reversed(get_registry().shutdown):
            try:
                resolve_hook(path)()
            except Exception:
                LOGGER.exception("Extension shutdown hook %s failed", path)
        _started = False


def extension_routers() -> list:
    """FastAPI routers declared by extensions, in registry order."""
    return [resolve_attribute(path) for path in get_registry().routers]


__all__ = ["extension_routers", "start_extensions", "stop_extensions"]
