"""Prepare registry-defined memory tools and recall for a delegated worker.

A worker declares its memory policy in a domain's ``WorkerDefinition``.
The host lifecycle calls ``prepare_worker_memory`` before entering the delegate.
Scientific tools and explicitly authorized coding inputs stay with the domain.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.tools import BaseTool

from agents.memory.handoff import SubagentMemoryHandoff, build_subagent_memory_handoff
from extensions import agent_memory_spec, query_provider_spec, resolve_hook
from project_workspace.store import ProjectStore
from agents.memory.tools.artifacts import make_project_artifact_tools
from agents.memory.tools.query import (
    make_query_project_memory_tool,
    make_search_project_memory_tool,
)
from agents.memory.tools.output import make_read_tool_output_tool
from agents.memory.tools.conclusions import make_agent_conclusion_tools
from agents.memory.tools.task_state import make_get_task_state_tool


@dataclass(frozen=True)
class PreparedWorkerMemory:
    """The tools and bounded authoritative preview for one worker invocation."""

    handoff: SubagentMemoryHandoff = SubagentMemoryHandoff()
    tools: tuple[BaseTool, ...] = ()


def make_agent_memory_tools(store: ProjectStore | None, *, agent: str) -> tuple[BaseTool, ...]:
    """Assemble registered access for the orchestrator and all workers."""
    spec = agent_memory_spec(agent)
    if store is None:
        return ()
    memory_tools = [make_query_project_memory_tool(store), make_search_project_memory_tool(store)]
    memory_tools.extend(make_agent_conclusion_tools(store))
    memory_tools.append(make_get_task_state_tool(store))
    # Metadata-only workers may retrieve their own overflowed tool replies,
    # never other workers' cached file contents or scientific input files.
    memory_tools.append(make_read_tool_output_tool(
        store, current_invocation_only=spec.artifact_access != "read",
    ))
    for provider_key in spec.query_providers:
        provider = query_provider_spec(provider_key)
        memory_tool = resolve_hook(provider.factory)(store)
        if not isinstance(memory_tool, BaseTool) or memory_tool.name != provider.key:
            raise ValueError(f"Query provider {provider.key!r} must return one tool with the same registered name")
        memory_tool.metadata = {**(memory_tool.metadata or {}), "gbclaw_projects_root": str(store.root)}
        memory_tools.append(memory_tool)
    allowed_artifact_tools = set()
    if spec.artifact_access == "read":
        allowed_artifact_tools.update({"list_run_artifacts", "read_run_artifact"})
    if spec.report_artifact_usage:
        allowed_artifact_tools.add("report_artifact_usage")
    if allowed_artifact_tools:
        memory_tools.extend(
            item for item in make_project_artifact_tools(store)
            if item.name in allowed_artifact_tools
        )
    names = [item.name for item in memory_tools]
    if len(names) != len(set(names)):
        raise ValueError(f"Duplicate memory tools for agent {agent!r}")
    return tuple(memory_tools)


def prepare_worker_memory(
    store: ProjectStore | None,
    project_id: str | None,
    task: str,
    *,
    agent: str,
) -> PreparedWorkerMemory:
    """Use the central agent policy for both memory access and automatic recall.

    Resolve registrations even without a store so an unknown agent cannot
    silently start with missing memory. No project means no automatic preview;
    tools themselves still enforce the active project in their invocation config.
    Coding receives query/search metadata, saved agent conclusions/task cursors,
    and its own cached tool replies. Its scientific file inputs still require the delegate's explicit
    ``source_artifacts`` authorization.
    Task cursors are accessible only through explicit state queries, never the
    automatic evidence preview; the current invocation starts before preflight.
    """
    spec = agent_memory_spec(agent)
    if store is None:
        return PreparedWorkerMemory()

    memory_tools = make_agent_memory_tools(store, agent=agent)

    handoff = build_subagent_memory_handoff(
        store,
        project_id,
        task,
        preferred_workflows=spec.preferred_workflows,
        excluded_fact_kinds=spec.excluded_fact_kinds,
        artifact_access=spec.artifact_access,
    )
    return PreparedWorkerMemory(handoff=handoff, tools=tuple(memory_tools))


__all__ = ["PreparedWorkerMemory", "prepare_worker_memory", "make_agent_memory_tools"]
