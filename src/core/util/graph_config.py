"""Read host identity from the active LangGraph runnable config.

Hosts set the same identifiers in both `metadata` and `configurable`; nested
worker graphs keep the parent chat in `chat_thread_id` and their own graph
thread in `thread_id`. Each lookup takes the first non-blank value.
"""

from __future__ import annotations

from typing import Any

from langgraph.config import get_config


def graph_config() -> dict[str, Any]:
    """Return the active runnable config, or an empty dict outside a graph."""
    try:
        return get_config()
    except RuntimeError:
        return {}


def _first(*values: Any) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def runtime_project_id() -> str | None:
    config = graph_config()
    metadata, configurable = config.get("metadata") or {}, config.get("configurable") or {}
    return _first(metadata.get("project_id"), configurable.get("project_id"))


def runtime_chat_thread_id() -> str | None:
    """The user-facing chat thread, including the parent chat of a nested worker."""
    config = graph_config()
    metadata, configurable = config.get("metadata") or {}, config.get("configurable") or {}
    return _first(metadata.get("chat_thread_id"), configurable.get("chat_thread_id"),
                  configurable.get("thread_id"))


def runtime_ids() -> tuple[str | None, str | None]:
    """Return ``(project_id, chat_thread_id)`` for the active invocation."""
    return runtime_project_id(), runtime_chat_thread_id()


def graph_thread_id() -> str | None:
    """The checkpoint thread of the graph currently executing."""
    config = graph_config()
    return _first((config.get("configurable") or {}).get("thread_id"),
                  (config.get("metadata") or {}).get("thread_id"))
