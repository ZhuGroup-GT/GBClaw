"""Services the application builds at startup (``app.state``), for route handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request

from core.llm.config import LLMEndpoint
from core.settings.runtime_context import RuntimeContextConfig

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from project_workspace.store import ProjectStore


def _state(request: Request, name: str, label: str, kind: type | None = None) -> Any:
    value = getattr(request.app.state, name, None)
    if value is None or (kind is not None and not isinstance(value, kind)):
        raise HTTPException(status_code=503, detail=f"{label} is not initialized")
    return value


def agent(request: Request) -> CompiledStateGraph:
    return _state(request, "agent", "Agent")


def project_store(request: Request) -> ProjectStore:
    return _state(request, "project_store", "Project store")


def memory_endpoint(request: Request) -> LLMEndpoint:
    return _state(request, "memory_endpoint", "Memory endpoint", LLMEndpoint)


def runtime_context_config(request: Request) -> RuntimeContextConfig:
    return _state(request, "runtime_context_config", "Runtime context config", RuntimeContextConfig)


__all__ = ["agent", "memory_endpoint", "project_store", "runtime_context_config"]
