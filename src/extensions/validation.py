"""Fail-fast, dependency-free validation of memory domain declarations."""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import PurePosixPath
from types import MappingProxyType

from core.identifiers import RUN_PREFIX_RE as PREFIX_PATTERN

from .specs import (
    AgentMemorySpec,
    DomainExtension,
    FactMemorySpec,
    QueryProviderSpec,
    ToolCapabilitySpec,
    ToolMemorySpec,
    OrchestratorToolsSpec,
    ToolProviderSpec,
    WorkerDefinition,
    WorkflowMemorySpec,
)

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
HOOK_PATTERN = re.compile(r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*:[a-zA-Z_]\w*")
WRITERS = {
    "run_publication",
    "project_plan",
    "conversation_extraction",
    "agent_conclusion",
    "task_state",
}


def _hook(value, label):
    if value is not None and (not isinstance(value, str) or not HOOK_PATTERN.fullmatch(value)):
        raise ValueError(f"Invalid {label}: expected module:function, got {value!r}")


def _key(value, label):
    if not isinstance(value, str) or not KEY_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid {label}: {value!r}")


def _tuple(value, label):
    if not isinstance(value, tuple):
        raise ValueError(f"{label} must be an immutable tuple")


def _names(value, label):
    _tuple(value, label)
    for name in value:
        _key(name, label)
    if len(set(value)) != len(value):
        raise ValueError(f"Duplicate {label}")


def _bool(value, label):
    if type(value) is not bool:
        raise ValueError(f"{label} must be boolean")


def validate_aliases(value):
    _tuple(value, "search aliases")
    for pair in value:
        if (
            not isinstance(pair, tuple)
            or len(pair) != 2
            or any(not isinstance(v, str) or not v.strip() for v in pair)
        ):
            raise ValueError("Search aliases must be tuples of nonempty (phrase, replacement)")


def _registry(specs, cls, label):
    result = {}
    for spec in specs:
        if not isinstance(spec, cls):
            raise ValueError(f"Expected {cls.__name__} in {label}")
        key = spec.agent if cls is WorkerDefinition else spec.key
        _key(key, label)
        if key in result:
            raise ValueError(f"Duplicate {label}: {key}")
        result[key] = spec
    return result


def validate_extension(spec):
    if not isinstance(spec, DomainExtension):
        raise ValueError("Memory extension must export a DomainExtension instance")
    _key(spec.key, "domain key")
    for field in fields(spec):
        if field.name != "key":
            _tuple(getattr(spec, field.name), f"{spec.key}.{field.name}")
    validate_aliases(spec.search_aliases)
    for pair in spec.tool_labels:
        if (not isinstance(pair, tuple) or len(pair) != 2
                or any(not isinstance(v, str) or not v.strip() for v in pair)):
            raise ValueError(f"{spec.key}.tool_labels must contain (tool, display name) pairs")
    for field in ("routers", "startup", "shutdown", "llm_providers"):
        for path in getattr(spec, field):
            if path is None:
                raise ValueError(f"Missing {spec.key}.{field} hook")
            _hook(path, f"{spec.key}.{field} hook")


def build_tool_provider_registry(specs, workers):
    result = _registry(specs, ToolProviderSpec, "tool provider")
    for spec in result.values():
        if spec.worker not in workers:
            raise ValueError(f"Tool provider {spec.key} names unknown worker {spec.worker!r}")
        if spec.factory is None:
            raise ValueError(f"Missing tool provider factory: {spec.key}")
        _hook(spec.factory, f"tool provider factory for {spec.key}")
    return MappingProxyType(result)


def build_orchestrator_tools_registry(specs):
    result = _registry(specs, OrchestratorToolsSpec, "orchestrator tools")
    for spec in result.values():
        if spec.placement not in {"leading", "trailing"}:
            raise ValueError(f"Invalid placement for orchestrator tools {spec.key}: {spec.placement!r}")
        _hook(spec.factory, f"orchestrator tools factory for {spec.key}")
    return MappingProxyType(result)


def build_fact_registry(specs):
    result = _registry(specs, FactMemorySpec, "fact kind")
    for spec in result.values():
        if spec.writer not in WRITERS:
            raise ValueError(f"Invalid writer for fact {spec.key}")
        if spec.extraction_policy not in {"deterministic", "user_explicit", "completed_run"}:
            raise ValueError(f"Invalid extraction policy for fact {spec.key}")
        if type(spec.context_priority) is not int or spec.context_priority < 0:
            raise ValueError(f"Invalid context priority for fact {spec.key}")
        if spec.fixed_lookup_key is not None and (
            not isinstance(spec.fixed_lookup_key, str)
            or not spec.fixed_lookup_key.strip()
            or len(spec.fixed_lookup_key) > 240
        ):
            raise ValueError(f"Invalid fixed lookup key for fact {spec.key}")
        if (
            sum(
                value is not None
                for value in (spec.fixed_lookup_key, spec.key_pattern, spec.canonical_key_policy)
            )
            > 1
        ):
            raise ValueError(f"Fact {spec.key} must select one canonical key policy")
        if spec.key_pattern is not None:
            try:
                re.compile(spec.key_pattern)
            except (TypeError, re.error) as exc:
                raise ValueError(f"Invalid canonical key pattern for fact {spec.key}") from exc
        _hook(spec.canonical_key_policy, f"canonical key policy for {spec.key}")
        if not isinstance(spec.extraction_hint, str) or not isinstance(spec.summary_guidance, str):
            raise ValueError(f"Invalid guidance for fact {spec.key}")
        validate_aliases(spec.search_aliases)
    return MappingProxyType(result)


def build_workflow_registry(specs):
    result = _registry(specs, WorkflowMemorySpec, "workflow memory key")
    prefixes, directories = set(), set()
    for spec in result.values():
        if not isinstance(spec.run_prefix, str) or not PREFIX_PATTERN.fullmatch(spec.run_prefix):
            raise ValueError(f"Invalid workflow run prefix: {spec.run_prefix!r}")
        if spec.run_prefix in prefixes:
            raise ValueError(f"Duplicate workflow run prefix: {spec.run_prefix}")
        location = spec.artifact_directory
        if not isinstance(location, str):
            raise ValueError("Artifact directory must be a string")
        directory = PurePosixPath(location)
        if (
            directory.is_absolute()
            or len(directory.parts) < 2
            or directory.parts[0] != "artifacts"
            or ".." in directory.parts
            or "\\" in location
            or directory.as_posix() != location
            or any(not KEY_PATTERN.fullmatch(part) for part in directory.parts)
        ):
            raise ValueError(
                f"Workflow artifact directory must be normalized under artifacts/: {location!r}"
            )
        if location in directories:
            raise ValueError(f"Duplicate workflow artifact directory: {location}")
        prefixes.add(spec.run_prefix)
        directories.add(location)
        _bool(spec.canonical_config, f"{spec.key}.canonical_config")
        _names(spec.query_aliases, "workflow query alias")
        _hook(spec.publication_validator, f"publication validator for {spec.key}")
        _tuple(spec.ephemeral_directories, f"{spec.key}.ephemeral_directories")
        for name in spec.ephemeral_directories:
            if (not isinstance(name, str) or name in {".", ".."}
                    or re.fullmatch(r"[A-Za-z0-9_.-]+", name) is None):
                raise ValueError("Ephemeral directories must be direct run-relative directory names")
        if len(set(spec.ephemeral_directories)) != len(spec.ephemeral_directories):
            raise ValueError(f"Duplicate ephemeral directory for {spec.key}")
        validate_aliases(spec.search_aliases)
        if not isinstance(spec.summary_guidance, str):
            raise ValueError(f"Invalid workflow guidance for {spec.key}")
    if not result:
        raise ValueError("At least one workflow memory declaration is required")
    query_names = set(result)
    for spec in result.values():
        for alias in spec.query_aliases:
            if alias in query_names:
                raise ValueError(f"Duplicate workflow query alias: {alias!r}")
            query_names.add(alias)
    return MappingProxyType(result)


def build_query_registry(specs):
    result = _registry(specs, QueryProviderSpec, "query provider")
    for spec in result.values():
        _hook(spec.factory, f"query provider factory for {spec.key}")
        if spec.factory is None:
            raise ValueError(f"Missing query provider factory: {spec.key}")
    return MappingProxyType(result)


def build_agent_registry(specs, workflows, facts=None, queries=None):
    result = _registry(specs, AgentMemorySpec, "agent memory key")
    for spec in result.values():
        if spec.artifact_access not in {"read", "metadata"}:
            raise ValueError(f"Invalid artifact access for agent {spec.key}")
        for flag in ("all_workflows", "report_artifact_usage"):
            _bool(getattr(spec, flag), f"{spec.key}.{flag}")
        if spec.all_workflows and spec.recall_workflows:
            raise ValueError(f"Agent {spec.key} must choose explicit workflows or all_workflows")
        _names(spec.recall_workflows, "recall workflow")
        _names(spec.excluded_fact_kinds, "excluded fact kind")
        _names(spec.query_providers, "query provider")
        for workflow in spec.recall_workflows:
            if workflow not in workflows:
                raise ValueError(f"Agent {spec.key} references unregistered workflow {workflow!r}")
        for key in spec.query_providers:
            if queries is not None and key not in queries:
                raise ValueError(f"Agent {spec.key} references unregistered query provider {key!r}")
        for key in spec.excluded_fact_kinds:
            if facts is not None and key not in facts:
                raise ValueError(
                    f"Agent {spec.key} references unregistered excluded fact kind {key!r}"
                )
    return MappingProxyType(result)


def build_tool_registry(specs, workflows, facts):
    result = _registry(specs, ToolMemorySpec, "tool memory key")
    workflow_names = {
        name: workflow.key
        for workflow in workflows.values()
        for name in (workflow.key, *workflow.query_aliases)
    }
    for spec in result.values():
        if spec.workflow not in workflows:
            raise ValueError(f"Tool {spec.key} references unregistered workflow {spec.workflow!r}")
        if spec.key in workflow_names and workflow_names[spec.key] != spec.workflow:
            raise ValueError(f"Tool {spec.key} conflicts with a workflow query name")
        template = spec.conclusion_lookup_key
        if template is not None:
            if not isinstance(template, str):
                raise ValueError(f"Invalid conclusion key for tool {spec.key}")
            remainder = template.replace("{run_id}", "")
            if (
                template.count("{run_id}") != 1
                or "{" in remainder
                or "}" in remainder
                or len(template.replace("{run_id}", "r" * 128)) > 240
            ):
                raise ValueError(
                    f"Tool {spec.key} conclusion key requires exactly one run_id field and must fit the fact key limit"
                )
        _names(spec.required_artifact_roles, "required artifact role")
        _names(spec.fact_kinds, "deterministic fact kind")
        _names(spec.model_result_keys, "model result key")
        if not spec.fact_kinds or any(
            key not in facts or facts[key].writer != "run_publication" for key in spec.fact_kinds
        ):
            raise ValueError(f"Invalid deterministic fact kinds for tool {spec.key}")
        if template is not None and "scientific_conclusion" not in spec.fact_kinds:
            raise ValueError(
                f"Tool {spec.key} declares a conclusion key without scientific_conclusion ownership"
            )
        _bool(spec.shared_fact_keys, f"{spec.key}.shared_fact_keys")
        for field in ("fact_builder", "model_result_builder", "publication_validator"):
            _hook(getattr(spec, field), f"{field} for tool {spec.key}")
        if type(spec.contract_version) is not int or spec.contract_version < 1:
            raise ValueError(f"Tool {spec.key} contract version must be a positive integer")
    return MappingProxyType(result)


def build_worker_registry(specs, agents):
    result = _registry(specs, WorkerDefinition, "worker agent")
    delegates = set()
    for spec in result.values():
        _key(spec.delegate_tool, "delegate tool")
        _key(spec.endpoint_role, "endpoint role")
        _hook(spec.factory, f"worker factory for {spec.agent}")
        if spec.factory is None:
            raise ValueError(f"Missing worker factory for {spec.agent}")
        if spec.delegate_tool in delegates:
            raise ValueError(f"Duplicate delegate tool: {spec.delegate_tool}")
        delegates.add(spec.delegate_tool)
        if spec.agent not in agents:
            raise ValueError(f"Worker {spec.agent} has no registered memory policy; set WorkerDefinition.memory "
                             "or add an AgentMemorySpec to DomainExtension.agents")
        if spec.memory is not None and (
            not isinstance(spec.memory, AgentMemorySpec) or spec.memory.key != spec.agent
        ):
            raise ValueError(f"Worker {spec.agent} memory policy key must match its agent")
        if spec.execution_kind not in {"scientific", "planning", "read"}:
            raise ValueError(f"Invalid execution kind for worker {spec.agent}")
        if spec.plan_capability is not None:
            _key(spec.plan_capability, "plan capability")
        _hook(spec.routing_guidance, f"routing guidance for {spec.agent}")
        _hook(spec.planning_guidance, f"planning guidance for {spec.agent}")
        if not isinstance(spec.display_name, str):
            raise ValueError(f"Invalid display name for worker {spec.agent}")
    return MappingProxyType(result)


def build_capability_registry(specs, tools, workers, queries):
    result = _registry(specs, ToolCapabilitySpec, "tool capability")
    for spec in result.values():
        if not isinstance(spec.display_name, str):
            raise ValueError(f"Invalid tool display name for {spec.key}")
        if spec.model_view is not None and not isinstance(spec.model_view, str):
            raise ValueError(f"Invalid model view hook for tool {spec.key}")
        if spec.operation not in {"query", "configuration", "scientific", "interaction"}:
            raise ValueError(f"Invalid operation for tool {spec.key}")
        if spec.plan_effect not in {"none", "draft", "approve", "reject", "update"}:
            raise ValueError(f"Invalid plan effect for tool {spec.key}")
        for field in ("plan_tracking", "memory_read", "preserve_final_stream", "memory_context"):
            _bool(getattr(spec, field), f"{spec.key}.{field}")
        if spec.key in tools and spec.operation != "scientific":
            raise ValueError(f"Publication tool {spec.key} must be scientific")
    for provider in queries.values():
        if provider.key in tools:
            raise ValueError(f"Query provider {provider.key} conflicts with a publication tool")
        if provider.key in result:
            raise ValueError(f"Query provider {provider.key} already owns its tool capability")
        result[provider.key] = ToolCapabilitySpec(
            provider.key,
            memory_read=True,
            memory_context=True,
            preserve_final_stream=True,
        )
    for spec in tools.values():
        result.setdefault(
            spec.key, ToolCapabilitySpec(spec.key, operation="scientific", plan_tracking=True)
        )
    for worker in workers.values():
        if worker.delegate_tool in result:
            raise ValueError(f"Delegate {worker.delegate_tool} conflicts with an ordinary tool")
        result[worker.delegate_tool] = ToolCapabilitySpec(
            worker.delegate_tool,
            operation={"scientific": "scientific", "planning": "interaction", "read": "query"}[
                worker.execution_kind
            ],
            plan_effect="draft" if worker.execution_kind == "planning" else "none",
        )
    return MappingProxyType(result)
