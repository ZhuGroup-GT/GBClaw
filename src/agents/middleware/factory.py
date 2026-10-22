"""One assembly point for orchestrator and worker context middleware."""
from __future__ import annotations
from typing import TYPE_CHECKING, Sequence
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from core.llm.factory import auxiliary_endpoint, build_llm
from core.llm.config import LLMEndpoint, default_max_input_tokens
from core.settings.runtime_context import (
    RuntimeContextConfig, WorkerContextBudget, count_context_tokens, model_max_input_tokens,
    resolve_context_budget, resolve_worker_context_budget, load_runtime_context_config,
)
from agents.planner.gates import ForcePlanGateMiddleware, SkipPlanGateMiddleware, PlanApprovalGateMiddleware
from .project import build_project_memory_prompt
from .projection import ContextPartitionMiddleware
from .session import FailSafeSummarizationMiddleware
from .session_policy import SESSION_SUMMARY_PROMPT, SESSION_SUMMARY_MAX_CHARS
from .turn_start import TurnStartCleanupMiddleware
from .worker import WorkerCompactionMiddleware
if TYPE_CHECKING:
    from project_workspace.store import ProjectStore


def build_orchestrator_context_middleware(
    *,
    main_model: BaseChatModel,
    main_endpoint: LLMEndpoint,
    memory_endpoint: LLMEndpoint,
    system_prompt: str,
    tools: Sequence[BaseTool],
    config: RuntimeContextConfig,
    project_store: ProjectStore | None = None,
    current_invocation_only: bool = False,
) -> list[AgentMiddleware]:
    budget = resolve_context_budget(
        model=main_model,
        endpoint=main_endpoint,
        system_prompt=system_prompt,
        tools=tools,
        config=config,
    ) if config.enabled else None
    project_prompt = build_project_memory_prompt(
        model=main_model,
        max_tokens=budget.project_summary_max_tokens if budget else config.project_summary_max_tokens,
        memory_max_tokens=budget.memory_context_tokens if budget else config.memory_context_tokens,
        project_store=project_store,
    )
    approval_gate: list[AgentMiddleware] = (
        [PlanApprovalGateMiddleware(project_store)]
        if project_store is not None
        else []
    )
    planning_mode_gates: list[AgentMiddleware] = [
        ForcePlanGateMiddleware(),
        SkipPlanGateMiddleware(),
    ]
    turn_start: list[AgentMiddleware] = [TurnStartCleanupMiddleware()]
    if not config.enabled:
        return [*turn_start, *approval_gate, *planning_mode_gates, project_prompt]

    assert budget is not None
    # Session continuity has a fixed small write cap. The larger allocation
    # remains available to read old checkpoints during safe policy migration.
    # Two tokens per character covers CJK handoffs at the character cap.
    output_limit = max(1, min(memory_endpoint.resolved_max_tokens(),
                              2 * SESSION_SUMMARY_MAX_CHARS,
                              budget.summary_model_max_tokens - 64))
    summary_endpoint = auxiliary_endpoint(memory_endpoint, answer_tokens=output_limit)
    summary_model = build_llm(summary_endpoint)
    memory_limits = [value for value in (
        memory_endpoint.max_input_tokens, model_max_input_tokens(summary_model)
    ) if value is not None] or [default_max_input_tokens()]
    source_limit = min(budget.summary_source_tokens,
                       min(memory_limits) - summary_endpoint.resolved_max_tokens() - 1_024)
    if source_limit <= output_limit:
        raise ValueError("The memory model cannot fit its previous summary plus new conversation.")
    return [
        *turn_start,
        *approval_gate,
        *planning_mode_gates,
        FailSafeSummarizationMiddleware(
            model=summary_model,
            token_counter=lambda messages: count_context_tokens(main_model, messages),
            trigger=("tokens", budget.summary_trigger_tokens),
            keep=("tokens", budget.summary_keep_tokens),
            trim_tokens_to_summarize=source_limit,
            summary_prompt=SESSION_SUMMARY_PROMPT + (
                f"\nKeep the summary within {output_limit} tokens, preserving all required headings."
            ),
            summary_max_tokens=budget.summary_model_max_tokens,
            working_max_tokens=budget.active_context_tokens,
            memory_max_tokens=budget.memory_context_tokens,
            project_store=project_store,
            current_invocation_only=current_invocation_only,
            reference_target_tokens=budget.summary_trigger_tokens,
            tool_keep_results=config.tool_keep_results,
        ),
        project_prompt,
        ContextPartitionMiddleware(
            budget=budget, model=main_model, system_prompt=system_prompt,
            tool_keep_results=config.tool_keep_results,
            project_store=project_store, current_invocation_only=current_invocation_only,
        ),
    ]


def worker_summary_endpoint(endpoint: LLMEndpoint, budget: WorkerContextBudget) -> LLMEndpoint:
    """The worker's own endpoint with thinking off and output capped to the summary output allocation."""
    return auxiliary_endpoint(
        endpoint, answer_tokens=min(endpoint.resolved_max_tokens(), budget.summary_output_tokens))


def build_worker_context_middleware(
    *, model: BaseChatModel, endpoint: LLMEndpoint, system_prompt: str,
    tools: Sequence[BaseTool], project_store: ProjectStore | None = None,
    config: RuntimeContextConfig | None = None,
) -> list[AgentMiddleware]:
    """Give a disposable worker one compaction policy over its whole window.

    Workers do not share the orchestrator's partitioned budgets: their graph
    state ends with the invocation, so there is no persistent checkpoint to keep
    predictable, and a context failure is reported for re-delegation. The shared
    factory installs result projection before this middleware.
    """
    config = config or load_runtime_context_config()
    if not config.enabled:
        return []
    budget = resolve_worker_context_budget(
        model=model, endpoint=endpoint, system_prompt=system_prompt, tools=tools, config=config)
    return [WorkerCompactionMiddleware(
        model=model, summary_model=build_llm(worker_summary_endpoint(endpoint, budget)),
        budget=budget, project_store=project_store,
    )]
