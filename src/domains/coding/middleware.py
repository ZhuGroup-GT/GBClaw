"""Model-request middleware for the Deep Agents coding worker: compaction and the step budget."""

from __future__ import annotations

from deepagents.middleware.summarization import (
    SummarizationMiddleware,
    compute_summarization_defaults,
)
from langchain.agents.middleware import AgentMiddleware
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import SystemMessage

from agents.memory.projection import ModelToolOutputMiddleware
from agents.middleware.contracts import provider_overflow
from agents.middleware.session_policy import SessionSummaryValidationError
from agents.middleware.worker import archive_compacted_text
from agents.middleware.worker_policy import (
    WORKER_SUMMARY_PROMPT, fit_worker_summary, validate_worker_summary,
)
from core.settings.runtime_context import WorkerContextBudget, count_conversation_tokens
from core.util.graph_config import graph_config, runtime_project_id
from project_workspace.store import ProjectStore


def coding_summary_prompt(budget: WorkerContextBudget) -> str:
    """The shared worker contract plus Deep Agents' offloaded-history conventions."""
    return WORKER_SUMMARY_PROMPT + (
        f"\nHard limit: {budget.summary_max_chars} characters including the four headings. "
        "Spend them on IN PROGRESS detail and exact references, not on completed process.\n"
        "Media may appear as <image url=\"/conversation_history/media/...\" /> references to "
        "files the next model can read with read_file; keep a reference when the media still "
        "matters, and never infer visual details that are not stated.\n"
    )


class CodingSummarizationMiddleware(SummarizationMiddleware):
    """Project tool replies before Deep Agents counts or summarizes them.

    Deep Agents installs its summary middleware ahead of custom middleware.
    Replacing that slot by name preserves its backend history offload and
    checkpoint behavior while applying the same result policy as other workers.
    With a budget, thresholds, counts and the summary contract are the shared
    worker ones; without one (runtime context disabled), Deep Agents' profile
    defaults apply.
    """

    def __init__(self, model, *, backend, projector: ModelToolOutputMiddleware,
                 budget: WorkerContextBudget | None = None, summary_model=None, projects_root=None):
        if budget is None:
            defaults = compute_summarization_defaults(model)
            settings = {key: defaults[key] for key in ("trigger", "keep", "truncate_args_settings")}
        else:
            keep = ("tokens", budget.compact_keep_tokens)
            settings = {
                "trigger": ("tokens", budget.compact_trigger_tokens),
                "keep": keep,
                "truncate_args_settings": {"trigger": ("tokens", budget.compact_trigger_tokens // 2),
                                           "keep": keep},
                "token_counter": lambda messages: count_conversation_tokens(model, messages),
                "summary_prompt": coding_summary_prompt(budget),
            }
        # Trimming stays off: LangChain trims from the last human message, and a worker has one.
        super().__init__(summary_model or model, backend=backend,
                         trim_tokens_to_summarize=None, **settings)
        self.projector = projector
        self.budget = budget
        self.projects_root = projects_root

    @property
    def name(self) -> str:
        return "SummarizationMiddleware"

    def _get_effective_messages(self, request):
        effective = super()._get_effective_messages(request)
        return self.projector._request(request.override(messages=effective)).messages

    def _create_summary(self, messages_to_summarize):
        """Fit a well-formed summary to its in-context limit; Deep Agents keeps any other text as is."""
        text = super()._create_summary(messages_to_summarize)
        if self.budget is None:
            return text
        try:
            return fit_worker_summary(
                validate_worker_summary(text), max_chars=self.budget.summary_max_chars,
                archive=lambda body: archive_compacted_text(
                    ProjectStore(self.projects_root) if self.projects_root else None,
                    runtime_project_id(), body))
        except SessionSummaryValidationError:
            return text

    # An unclassified provider 400 naming the window becomes ContextOverflowError,
    # so Deep Agents summarizes and retries instead of failing the worker.
    def wrap_model_call(self, request, handler):
        def call(request):
            try:
                return handler(request)
            except Exception as exc:
                if provider_overflow(exc) is None:
                    raise
                raise ContextOverflowError(str(exc)) from exc
        return super().wrap_model_call(request, call)

    async def awrap_model_call(self, request, handler):
        async def call(request):
            try:
                return await handler(request)
            except Exception as exc:
                if provider_overflow(exc) is None:
                    raise
                raise ContextOverflowError(str(exc)) from exc
        return await super().awrap_model_call(request, call)


class StepBudgetMiddleware(AgentMiddleware):
    """Warn the worker before the graph step limit discards unpublished work.

    One tool round costs several graph steps (hook nodes, model, tools); the cost
    is measured between consecutive model calls. The notice is added to the live
    request only, so it never enters the worker's message history.
    """

    def __init__(self, *, warn_rounds: int = 8):
        super().__init__()
        self.warn_rounds = warn_rounds
        self._last_step: int | None = None
        self._round_steps = 3

    def _request(self, request):
        config = graph_config()
        step = (config.get("metadata") or {}).get("langgraph_step")
        limit = config.get("recursion_limit")
        if not isinstance(step, int) or not isinstance(limit, int):
            return request
        if self._last_step is not None and step > self._last_step:
            self._round_steps = step - self._last_step
        self._last_step = step
        rounds = max(0, (limit - step) // self._round_steps)
        if rounds > self.warn_rounds:
            return request
        notice = (
            f"STEP BUDGET: about {rounds} tool round(s) remain before this worker is stopped "
            "and every unpublished file is discarded. Stop exploring and refactoring. Write the "
            "required outputs and call publish_coding_result now; if publication cannot succeed, "
            "stop and report exactly what is missing."
        )
        base = request.system_message.text if request.system_message is not None else ""
        return request.override(system_message=SystemMessage(content=f"{base}\n\n{notice}".strip()))

    def wrap_model_call(self, request, handler):
        return handler(self._request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._request(request))


__all__ = ["CodingSummarizationMiddleware", "StepBudgetMiddleware", "coding_summary_prompt"]
