"""User-facing orchestrator: the only agent that talks to the user.

Registers the planning and scientific delegation tools, plus the tools domains
declare for it (``DomainExtension.orchestrator_tools``). Answers simple questions
directly and hands specialized work to temporary workers. Created at app startup;
run through interfaces.turn by the API and the CLI.
"""

from __future__ import annotations

from langgraph.graph.state import CompiledStateGraph

from agents.graph import create_agent_graph
from agents.memory.worker import make_agent_memory_tools
from agents.middleware import (
    ProjectRuntimeContext,
    build_orchestrator_context_middleware,
)
from agents.workers.lifecycle import build_registered_worker_tools
from core.llm.config import ModelRouter
from agents.prompts import with_result_handoff_prompt
from agents.orchestrator.prompt import build_orchestrator_prompt
from agents.orchestrator.web_search_prompt import WEB_SEARCH_PROMPT
from core.settings.web_search import is_web_search_available
from core.settings.runtime_context import RuntimeContextConfig, load_runtime_context_config
from project_workspace.store import ProjectStore
from extensions import validate_hooks
from agents.orchestrator.contributions import build_domain_orchestrator_tools
from agents.planner.tools import make_project_plan_tools
from agents.orchestrator.web_search import make_search_web_tool


def _build_orchestrator_prompt(*, enable_web_search: bool) -> str:
    if not enable_web_search:
        return with_result_handoff_prompt(build_orchestrator_prompt())
    return with_result_handoff_prompt(f"{build_orchestrator_prompt()}\n\n{WEB_SEARCH_PROMPT}")


def create_orchestrator_agent(
    model_router: ModelRouter,
    *,
    project_store: ProjectStore | None = None,
    enable_tracing: bool | None = None,
    runtime_context_config: RuntimeContextConfig | None = None,
) -> CompiledStateGraph:
    validate_hooks()
    orchestrator_endpoint = model_router.for_role("orchestrator")
    literature_endpoint = model_router.for_role("literature")
    memory_endpoint = model_router.for_role("memory")

    domain_tools = build_domain_orchestrator_tools(project_store)
    tools = list(domain_tools.leading)
    enable_web_search = is_web_search_available(literature_endpoint)
    if project_store is not None:
        tools.extend(make_agent_memory_tools(project_store, agent="orchestrator"))
        tools.extend(make_project_plan_tools(project_store))
    if enable_web_search:
        tools.append(make_search_web_tool(literature_endpoint))
    tools.extend(domain_tools.trailing)
    tools.extend(build_registered_worker_tools(
        model_router, project_store=project_store, memory_endpoint=memory_endpoint,
        factory_options=domain_tools.worker_options,
    ))

    system_prompt = _build_orchestrator_prompt(enable_web_search=enable_web_search)
    context_config = runtime_context_config or load_runtime_context_config()
    return create_agent_graph(
        orchestrator_endpoint,
        tools=tools,
        system_prompt=system_prompt,
        enable_tracing=enable_tracing,
        projects_root=project_store.root if project_store is not None else None,
        middleware_factory=lambda main_model: build_orchestrator_context_middleware(
            main_model=main_model,
            main_endpoint=orchestrator_endpoint,
            memory_endpoint=memory_endpoint,
            system_prompt=system_prompt,
            tools=tools,
            config=context_config,
            project_store=project_store,
        ),
        context_schema=ProjectRuntimeContext,
    )
