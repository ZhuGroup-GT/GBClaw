"""Collect, validate, then freeze domain declarations before models import them."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import importlib
import inspect
import os
from types import MappingProxyType
from typing import Mapping

from .specs import (
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
from .validation import (
    build_agent_registry,
    build_capability_registry,
    build_fact_registry,
    build_orchestrator_tools_registry,
    build_query_registry,
    build_tool_provider_registry,
    build_tool_registry,
    build_worker_registry,
    build_workflow_registry,
    validate_aliases,
    validate_extension,
)

# The core host vocabulary, then the built-in scientific domains in routing order.
BUILTIN_EXTENSIONS = (
    "extensions.core",
    "domains.structure.extension",
    "domains.lammps.extension",
    "domains.analysis.extension",
    "domains.coding.extension",
)
EXTENSIONS_ENV = "GBCLAW_EXTENSIONS"
LEGACY_EXTENSIONS_ENV = "GBCLAW_MEMORY_EXTENSIONS"


@dataclass(frozen=True)
class MemoryRegistry:
    domains: Mapping[str, DomainExtension]
    facts: Mapping[str, FactMemorySpec]
    workflows: Mapping[str, WorkflowMemorySpec]
    tools: Mapping[str, ToolMemorySpec]
    agents: Mapping[str, AgentMemorySpec]
    workers: Mapping[str, WorkerDefinition]
    query_providers: Mapping[str, QueryProviderSpec]
    capabilities: Mapping[str, ToolCapabilitySpec]
    writers: Mapping[str, MemoryWriterSpec]
    search_aliases: Mapping[str, str]
    Workflow: type[StrEnum]
    tool_providers: Mapping[str, ToolProviderSpec] = MappingProxyType({})
    orchestrator_tools: Mapping[str, OrchestratorToolsSpec] = MappingProxyType({})
    tool_labels: Mapping[str, str] = MappingProxyType({})
    routers: tuple[str, ...] = ()
    startup: tuple[str, ...] = ()
    shutdown: tuple[str, ...] = ()
    llm_providers: tuple[str, ...] = ()


def freeze_registry(extensions) -> MemoryRegistry:
    """Validate the complete graph before exposing any immutable declarations."""
    domains = {}
    for extension in extensions:
        validate_extension(extension)
        if extension.key in domains:
            raise ValueError(f"Duplicate memory domain: {extension.key}")
        domains[extension.key] = extension

    def collect(field):
        return tuple(spec for extension in domains.values() for spec in getattr(extension, field))

    facts = build_fact_registry(collect("facts"))
    workflows = build_workflow_registry(collect("workflows"))
    tools = build_tool_registry(collect("tools"), workflows, facts)
    queries = build_query_registry(collect("query_providers"))
    worker_specs = collect("workers")
    # Worker-embedded declarations are the sole declaration for that new agent.
    embedded = []
    for worker in worker_specs:
        if not isinstance(worker, WorkerDefinition):
            raise ValueError("Worker declarations must be WorkerDefinition instances")
        if worker.memory is not None:
            embedded.append(worker.memory)
    agents = build_agent_registry((*collect("agents"), *embedded), workflows, facts, queries)
    workers = build_worker_registry(worker_specs, agents)
    capabilities = build_capability_registry(collect("capabilities"), tools, workers, queries)
    providers = build_tool_provider_registry(collect("tool_providers"), workers)
    orchestrator_tools = build_orchestrator_tools_registry(collect("orchestrator_tools"))
    labels = {}
    for tool, label in collect("tool_labels"):
        if labels.get(tool, label) != label:
            raise ValueError(f"Conflicting display names for tool {tool!r}")
        labels[tool] = label
    aliases = {}
    for source in (*domains.values(), *facts.values(), *workflows.values()):
        validate_aliases(source.search_aliases)
        for phrase, replacement in source.search_aliases:
            if phrase in aliases and aliases[phrase] != replacement:
                raise ValueError(f"Conflicting search alias: {phrase!r}")
            aliases[phrase] = replacement

    def owned(writer):
        return tuple(key for key, spec in facts.items() if spec.writer == writer)

    writers = (
        MemoryWriterSpec("run_publication", owned("run_publication"), ("deterministic_workflow",)),
        MemoryWriterSpec("project_plan", owned("project_plan"), ("deterministic_workflow",)),
        MemoryWriterSpec("agent_conclusion", owned("agent_conclusion"), ("assistant_report",)),
        MemoryWriterSpec("task_state", owned("task_state"), ("deterministic_workflow",)),
        MemoryWriterSpec(
            "conversation_extraction",
            tuple(
                key
                for key, spec in facts.items()
                if spec.writer not in {"agent_conclusion", "task_state"}
            ),
            ("user_explicit", "completed_run"),
            "extraction",
        ),
    )
    # Storage models own the public enum; domains supply its durable values.
    workflow_enum = StrEnum(
        "Workflow",
        {key.upper(): key for key in workflows},
        module="project_memory.models",
    )
    return MemoryRegistry(
        MappingProxyType(domains),
        facts,
        workflows,
        tools,
        agents,
        workers,
        queries,
        capabilities,
        MappingProxyType({spec.key: spec for spec in writers}),
        MappingProxyType(aliases),
        workflow_enum,
        providers,
        orchestrator_tools,
        MappingProxyType(labels),
        collect("routers"),
        collect("startup"),
        collect("shutdown"),
        collect("llm_providers"),
    )


def builtin_extensions() -> tuple[DomainExtension, ...]:
    return tuple(importlib.import_module(path).MEMORY_EXTENSION for path in BUILTIN_EXTENSIONS)


def load_registry(*, extension_paths=None, include_builtins: bool = True) -> MemoryRegistry:
    """Load trusted Python packages declared in the single startup setting.

    Set GBCLAW_EXTENSIONS (or the older GBCLAW_MEMORY_EXTENSIONS) to comma-separated
    module[:attribute] paths; the default attribute is MEMORY_EXTENSION. Restart
    after configuration changes. Extension modules must only import declaration
    types, not runtime services.
    """
    if extension_paths is None:
        raw = os.environ.get(EXTENSIONS_ENV) or os.environ.get(LEGACY_EXTENSIONS_ENV, "")
        extension_paths = tuple(part.strip() for part in raw.split(",") if part.strip())
    if isinstance(extension_paths, str):
        raise ValueError("extension_paths must be an iterable of module paths, not a string")
    extensions = list(builtin_extensions()) if include_builtins else []
    for path in extension_paths:
        if not isinstance(path, str) or path.count(":") > 1:
            raise ValueError(f"Invalid extension path: {path!r}")
        module_name, _, attribute = path.partition(":")
        if (
            not module_name
            or not all(part.isidentifier() for part in module_name.split("."))
            or (attribute and not attribute.isidentifier())
        ):
            raise ValueError(f"Invalid extension path: {path!r}")
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            raise ImportError(
                f"Cannot import extension module {module_name!r} (from {EXTENSIONS_ENV} or configure()): "
                f"{exc}. Install its package or put its directory on PYTHONPATH."
            ) from exc
        if not hasattr(module, attribute or "MEMORY_EXTENSION"):
            raise ValueError(f"Extension module {module_name!r} has no {attribute or 'MEMORY_EXTENSION'}")
        extension = getattr(module, attribute or "MEMORY_EXTENSION")
        validate_extension(extension)
        extensions.append(extension)
    return freeze_registry(extensions)


def resolve_attribute(path: str):
    """Import ``module:attribute`` for declared objects such as routers and texts."""
    module_name, _, attribute = path.partition(":")
    return getattr(importlib.import_module(module_name), attribute)


def resolve_hook(path: str):
    """Resolve a synchronous callable after declaration/model bootstrap."""
    module_name, _, attribute = path.partition(":")
    value = getattr(importlib.import_module(module_name), attribute)
    if not callable(value):
        raise ValueError(f"Hook must be callable: {path}")
    if inspect.iscoroutinefunction(value) or inspect.iscoroutinefunction(
        getattr(value, "__call__", None)
    ):
        raise ValueError(f"Hook must be synchronous: {path}")
    return value


def _extension_hooks(extension: DomainExtension, include_workers: bool):
    """(field, path, args, kwargs) hook calls and (field, path, type) attributes of one extension."""
    calls, attributes = [], []
    for spec in extension.tools:
        for field, count in (
            ("fact_builder", 2),
            ("model_result_builder", 1),
            ("publication_validator", 3),
        ):
            if path := getattr(spec, field):
                calls.append((f"tools[{spec.key}].{field}", path, (None,) * count, {}))
    calls.extend((f"capabilities[{spec.key}].model_view", spec.model_view, (None,), {})
                 for spec in extension.capabilities if spec.model_view)
    for spec in extension.facts:
        if spec.canonical_key_policy:
            calls.append((f"facts[{spec.key}].canonical_key_policy", spec.canonical_key_policy, (),
                          {"kind": None, "requested_key": None, "source_run_id": None}))
    for spec in extension.workflows:
        if spec.publication_validator:
            calls.append((f"workflows[{spec.key}].publication_validator", spec.publication_validator,
                          (None,) * 3, {}))
    calls.extend((f"query_providers[{spec.key}].factory", spec.factory, (None,), {})
                 for spec in extension.query_providers)
    calls.extend((f"tool_providers[{spec.key}].factory", spec.factory, (), {})
                 for spec in extension.tool_providers)
    calls.extend((f"orchestrator_tools[{spec.key}].factory", spec.factory, (), {"project_store": None})
                 for spec in extension.orchestrator_tools)
    for field in ("startup", "shutdown", "llm_providers"):
        calls.extend((field, path, (), {}) for path in getattr(extension, field))
    if include_workers:
        for spec in extension.workers:
            calls.append((f"workers[{spec.agent}].factory", spec.factory, (None,),
                          {"project_store": None, "memory_endpoint": None}))
            for field in ("routing_guidance", "planning_guidance"):
                if path := getattr(spec, field):
                    attributes.append((f"workers[{spec.agent}].{field}", path, str))
        attributes.extend(("routers", path, None) for path in extension.routers)
    return calls, attributes


def validate_hooks(registry: MemoryRegistry, *, include_workers: bool = True):
    """After models import, validate hook call shapes without invoking them.

    This is an implementation preflight, not evidence acceptance. Publication
    and extraction services still validate every returned record and provenance.
    Errors name the declaring extension and field.
    """
    paths = set()
    for domain, extension in registry.domains.items():
        calls, attributes = _extension_hooks(extension, include_workers)
        for field, path, args, kwargs in calls:
            try:
                inspect.signature(resolve_hook(path)).bind(*args, **kwargs)
            except (ImportError, AttributeError, TypeError, ValueError) as exc:
                raise ValueError(f"{domain} extension, {field}: invalid hook {path!r}: {exc}") from exc
            paths.add(path)
        for field, path, required in attributes:
            try:
                value = resolve_attribute(path)
            except (ImportError, AttributeError) as exc:
                raise ValueError(f"{domain} extension, {field}: invalid attribute {path!r}: {exc}") from exc
            if required is not None and not isinstance(value, required):
                raise ValueError(f"{domain} extension, {field}: {path!r} must be a {required.__name__}")
            paths.add(path)
    return tuple(sorted(paths))
