"""Frozen declarations assembled from the built-in and configured domain extensions.

Declarations cover memory records and workflows as well as workers, their
tools, orchestrator tools, tool capabilities, query providers and host hooks
(routers, startup and shutdown, LLM providers). Each trusted module exports
MEMORY_EXTENSION: DomainExtension and imports types from .specs. Runtime hooks
are deliberately resolved only after model bootstrap.

The registry is built on first use and then fixed for the process. Choose the
extension modules with GBCLAW_EXTENSIONS, or call ``configure()`` before
anything reads the registry (storage models read it when they are imported).
"""

from __future__ import annotations

from .specs import (
    DEFAULT_WORKER_EXCLUDED_FACT_KINDS,
    AgentMemorySpec,
    DomainExtension,
    FactMemorySpec,
    MemoryWriterSpec,
    OrchestratorToolsSpec,
    QueryProviderSpec,
    ToolCapabilitySpec,
    ToolMemorySpec,
    ToolProviderSpec,
    WorkerDefinition,
    WorkflowMemorySpec,
)
from .loader import (
    EXTENSIONS_ENV,
    MemoryRegistry,
    builtin_extensions,
    freeze_registry,
    load_registry,
    resolve_attribute,
    resolve_hook,
    validate_hooks as _validate_hooks,
)

_registry: MemoryRegistry | None = None
_configuration: tuple[tuple[str, ...] | None, bool] | None = None


def configure(*, extension_paths=None, include_builtins: bool = True) -> None:
    """Select extension modules programmatically, before the registry is first used."""
    global _configuration
    if _registry is not None:
        raise RuntimeError("The extension registry is already loaded; configure it before first use")
    if isinstance(extension_paths, str):
        raise ValueError("extension_paths must be an iterable of module paths, not a string")
    paths = None if extension_paths is None else tuple(extension_paths)
    _configuration = (paths, include_builtins)


def get_registry() -> MemoryRegistry:
    global _registry
    if _registry is None:
        paths, include_builtins = _configuration or (None, True)
        _registry = load_registry(extension_paths=paths, include_builtins=include_builtins)
    return _registry


# Module attributes derived from the registry (PEP 562), read on first access.
_DERIVED = {
    "REGISTRY": lambda registry: registry,
    "FACT_MEMORY_SPECS": lambda registry: registry.facts,
    "WORKFLOW_MEMORY_SPECS": lambda registry: registry.workflows,
    "TOOL_MEMORY_SPECS": lambda registry: registry.tools,
    "AGENT_MEMORY_SPECS": lambda registry: registry.agents,
    "MEMORY_WRITER_SPECS": lambda registry: registry.writers,
    "WORKER_DEFINITIONS": lambda registry: registry.workers,
    "QUERY_PROVIDERS": lambda registry: registry.query_providers,
    "TOOL_CAPABILITIES": lambda registry: registry.capabilities,
    "TOOL_PROVIDERS": lambda registry: registry.tool_providers,
    "ORCHESTRATOR_TOOLS": lambda registry: registry.orchestrator_tools,
    "TOOL_LABELS": lambda registry: registry.tool_labels,
    "SEARCH_ALIASES": lambda registry: registry.search_aliases,
    "Workflow": lambda registry: registry.Workflow,
}


def __getattr__(name):
    if name in _DERIVED:
        return _DERIVED[name](get_registry())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _get(mapping, key, label):
    try:
        return mapping[str(key)]
    except KeyError as exc:
        raise ValueError(f"Unregistered memory {label}: {key!r}") from exc


def fact_memory_spec(kind: str) -> FactMemorySpec:
    return _get(get_registry().facts, kind, "fact kind")


def workflow_memory_spec(workflow) -> WorkflowMemorySpec:
    return _get(get_registry().workflows, workflow, "workflow")


def agent_memory_spec(agent: str) -> AgentMemorySpec:
    return _get(get_registry().agents, agent, "agent")


def tool_memory_spec(tool: str) -> ToolMemorySpec:
    return _get(get_registry().tools, tool, "tool")


def memory_writer_spec(writer: str) -> MemoryWriterSpec:
    return _get(get_registry().writers, writer, "writer")


def query_provider_spec(key: str) -> QueryProviderSpec:
    return _get(get_registry().query_providers, key, "query provider")


def worker_definition(agent: str) -> WorkerDefinition:
    return _get(get_registry().workers, agent, "worker")


def worker_definitions() -> tuple[WorkerDefinition, ...]:
    return tuple(get_registry().workers.values())


def worker_definition_for_delegate(name: str) -> WorkerDefinition | None:
    return next((spec for spec in get_registry().workers.values() if spec.delegate_tool == name), None)


def worker_tool_providers(agent: str) -> tuple[ToolProviderSpec, ...]:
    """Providers of one worker's business tools, in registry order."""
    return tuple(spec for spec in get_registry().tool_providers.values() if spec.worker == agent)


def tool_capability(name: str) -> ToolCapabilitySpec | None:
    return get_registry().capabilities.get(name)


def tool_display_name(name: str) -> str | None:
    """A declared display name: capability name, tool label, or the delegate's worker."""
    registry = get_registry()
    capability = registry.capabilities.get(name)
    if capability is not None and capability.display_name:
        return capability.display_name
    if label := registry.tool_labels.get(name):
        return label
    worker = worker_definition_for_delegate(name)
    return worker.display_name or None if worker is not None else None


def is_scientific_result_tool(name: str) -> bool:
    policy = tool_capability(name)
    return bool(policy and policy.operation == "scientific" and policy.plan_tracking)


def is_memory_read_tool(name: str) -> bool:
    policy = tool_capability(name)
    return bool(policy and policy.memory_read)


def canonical_config_workflows() -> tuple:
    registry = get_registry()
    return tuple(
        registry.Workflow(key) for key, spec in registry.workflows.items() if spec.canonical_config
    )


def resolve_workflow_filter(value):
    registry = get_registry()
    key = str(value).strip()
    if key in registry.workflows:
        return registry.Workflow(key)
    for spec in registry.workflows.values():
        if key in spec.query_aliases:
            return registry.Workflow(spec.key)
    if key in registry.tools:
        return registry.Workflow(registry.tools[key].workflow)
    raise ValueError(
        f"Unknown workflow {key!r}. Allowed workflow values: {', '.join(registry.workflows)}. "
        "Workflow names are storage categories, not inferred analysis/tool names."
    )


def workflow_query_help() -> str:
    registry = get_registry()
    return (
        "Canonical workflow filters: " + ", ".join(registry.workflows) + ". "
        "Result-producing tools map to workflows as follows: "
        + "; ".join(f"{spec.key} -> {spec.workflow}" for spec in registry.tools.values())
        + ". Use entity=runs to verify scientific execution; task_state facts describe delegate "
        "activity and do not establish that a scientific tool has started."
    )


def validate_hooks(registry: MemoryRegistry | None = None, *, include_workers: bool = True):
    return _validate_hooks(registry or get_registry(), include_workers=include_workers)


__all__ = [
    "DEFAULT_WORKER_EXCLUDED_FACT_KINDS",
    "AgentMemorySpec",
    "DomainExtension",
    "FactMemorySpec",
    "MemoryWriterSpec",
    "OrchestratorToolsSpec",
    "QueryProviderSpec",
    "ToolCapabilitySpec",
    "ToolMemorySpec",
    "ToolProviderSpec",
    "WorkerDefinition",
    "WorkflowMemorySpec",
    "MemoryRegistry",
    "REGISTRY",
    "FACT_MEMORY_SPECS",
    "WORKFLOW_MEMORY_SPECS",
    "TOOL_MEMORY_SPECS",
    "AGENT_MEMORY_SPECS",
    "MEMORY_WRITER_SPECS",
    "WORKER_DEFINITIONS",
    "QUERY_PROVIDERS",
    "TOOL_CAPABILITIES",
    "TOOL_PROVIDERS",
    "ORCHESTRATOR_TOOLS",
    "TOOL_LABELS",
    "SEARCH_ALIASES",
    "Workflow",
    "EXTENSIONS_ENV",
    "builtin_extensions",
    "configure",
    "freeze_registry",
    "get_registry",
    "load_registry",
    "resolve_attribute",
    "resolve_hook",
    "validate_hooks",
    "fact_memory_spec",
    "workflow_memory_spec",
    "agent_memory_spec",
    "tool_memory_spec",
    "memory_writer_spec",
    "query_provider_spec",
    "worker_definition",
    "worker_definitions",
    "worker_definition_for_delegate",
    "worker_tool_providers",
    "tool_capability",
    "tool_display_name",
    "is_scientific_result_tool",
    "is_memory_read_tool",
    "canonical_config_workflows",
    "resolve_workflow_filter",
    "workflow_query_help",
]
