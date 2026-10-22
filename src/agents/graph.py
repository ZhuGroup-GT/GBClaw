"""Shared agent factory: build a LangGraph agent from LLMEndpoint, tools, and system_prompt.

All agents (orchestrator, Structure worker, and future workers) are created here so
build_llm + create_agent logic is not duplicated across agent modules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

from core.llm.factory import build_llm
from agents.middleware.cancellation import ExecutionCancellationMiddleware
from core.settings.langsmith import configure_langsmith
from core.llm.config import LLMEndpoint
from agents.prompts import with_result_handoff_prompt
from project_workspace.execution.checkpoint import ProjectCheckpointRouter
from project_workspace.store import ProjectStore
from project_workspace.paths import get_default_projects_root
from agents.memory.projection import ModelToolOutputMiddleware


def create_agent_graph(
    endpoint: LLMEndpoint,
    *,
    tools: Sequence[BaseTool | Callable[..., Any] | dict[str, Any]] | None = None,
    system_prompt: str,
    enable_tracing: bool | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    projects_root: Path | None = None,
    middleware_factory: (
        Callable[[BaseChatModel], Sequence[AgentMiddleware]] | None
    ) = None,
    context_schema: type[Any] | None = None,
) -> CompiledStateGraph:
    from agents.workers.lifecycle import current_worker_invocation, worker_memory_inputs
    from agents.memory.handoff import append_memory_handoff

    invocation = current_worker_invocation(required=False)
    if invocation is not None:
        tools, memory_handoff = worker_memory_inputs(invocation.definition.agent, tools)
        system_prompt = append_memory_handoff(system_prompt, memory_handoff)
    if enable_tracing is not False:
        configure_langsmith()

    llm = build_llm(endpoint)
    saver = checkpointer
    if saver is None and projects_root is not None:
        saver = ProjectCheckpointRouter(projects_root)
    if saver is None:
        saver = MemorySaver()

    # Project full tool evidence only for model requests; durable traces and
    # scientific artifacts keep the original results. Put this ahead of the
    # context partition middleware so its hard guard counts the compact input.
    output_roots = {
        Path(root).resolve()
        for tool in (tools or [])
        if (root := (getattr(tool, "metadata", None) or {}).get("gbclaw_projects_root"))
    }
    if projects_root is not None:
        output_roots.add(projects_root.resolve())
    if len(output_roots) > 1:
        raise ValueError("Agent memory tools must use one project store")
    output_root = next(iter(output_roots), get_default_projects_root())
    middleware = [ExecutionCancellationMiddleware(), ModelToolOutputMiddleware(projects_root=output_root)]
    if middleware_factory:
        middleware.extend(middleware_factory(llm))
    else:
        from agents.middleware import build_worker_context_middleware
        middleware.extend(build_worker_context_middleware(
            model=llm, endpoint=endpoint, system_prompt=with_result_handoff_prompt(system_prompt),
            tools=tools or [], project_store=ProjectStore(output_root),
        ))
    return create_agent(
        llm,
        tools=tools or [],
        system_prompt=with_result_handoff_prompt(system_prompt),
        checkpointer=saver,
        middleware=middleware,
        context_schema=context_schema,
    )
