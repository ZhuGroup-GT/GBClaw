"""Model-context middleware: session continuity, project recall and tool-result projection."""
from .contracts import ContextCompactionError, ProjectRuntimeContext

# Tool-result projection and workspace readers also import this package. Keep
# the public API lightweight; importing a pure contract must not build models
# or eagerly resolve the worker registry.
_EXPORTS = {
    "build_orchestrator_context_middleware": "factory",
    "build_worker_context_middleware": "factory",
    "build_project_memory_prompt": "project",
    "ContextPartitionMiddleware": "projection",
    "FailSafeSummarizationMiddleware": "session",
    "WorkerCompactionMiddleware": "worker",
}


def __getattr__(name):
    from importlib import import_module

    if name not in _EXPORTS:
        raise AttributeError(name)
    value = getattr(import_module(f"{__name__}.{_EXPORTS[name]}"), name)
    globals()[name] = value
    return value

__all__ = [
    "ContextCompactionError", "ProjectRuntimeContext", "ContextPartitionMiddleware",
    "FailSafeSummarizationMiddleware", "WorkerCompactionMiddleware", "build_project_memory_prompt",
    "build_orchestrator_context_middleware", "build_worker_context_middleware",
]
