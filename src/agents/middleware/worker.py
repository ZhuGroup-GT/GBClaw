"""Compaction for disposable worker graphs: one pool, one trigger, content-aware summaries."""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from agents.memory.decisions import result_requires_user
from core.llm.generation import is_truncated
from core.settings.runtime_context import WorkerContextBudget, count_conversation_tokens
from .archive import archive_summary_inputs
from .contracts import ContextCompactionError, provider_overflow
from .projection import _complete_block_boundaries, _is_session_summary, _runtime_project_id
from .session import summary_model_error
from .session_policy import SessionSummaryValidationError
from .worker_policy import (
    fit_worker_summary, render_worker_summary_prompt, validate_worker_summary,
    worker_summary_input_messages,
)
if TYPE_CHECKING:
    from project_workspace.store import ProjectStore

LOGGER = logging.getLogger(__name__)
COMPACTED_RESULTS_TOOL = "compacted_worker_results"


def archive_compacted_text(project_store, project_id, text: str) -> str | None:
    """Store summary text the worker may re-read with read_tool_output; None without a project."""
    from agents.memory.tools.output import invocation_thread_id
    from project_memory.storage.tool_outputs import ToolOutputStore
    from project_workspace.store import ProjectStore

    if not project_id:
        return None
    try:
        store = project_store or ProjectStore()
        reference = ToolOutputStore(store.project_path(project_id)).write(
            text, tool_name=COMPACTED_RESULTS_TOOL, scope_thread_id=invocation_thread_id())
        return reference["output_id"]
    except (OSError, ValueError, TypeError, KeyError):
        return None


class WorkerCompactionMiddleware(AgentMiddleware):
    """Compact a worker's history once it nears its window; keep the task and the recent tail.

    A disposable worker has one pool: everything up to ``compact_trigger_tokens``
    is sent as is. Compaction keeps the task message and the newest complete
    tool-call blocks verbatim and replaces the rest with one summary that keeps
    results for finished work and detail for work in progress. Consumed tool
    results are archived first so ``read_tool_output`` can bring any of them
    back, and their trace records ride on the summary for completion checks.
    A provider overflow compacts the request in place and retries once.
    """

    def __init__(self, *, model, summary_model, budget: WorkerContextBudget,
                 project_store: ProjectStore | None = None):
        super().__init__()
        self.model = model
        self.summary_model = summary_model
        self.budget = budget
        self.project_store = project_store
        self._compact_next = False
        self._cached_summary: tuple[tuple[str, ...], str] | None = None

    @property
    def name(self) -> str:
        # Stream converters skip summarization nodes by this name fragment.
        return "WorkerSummarizationMiddleware"

    def _tokens(self, messages) -> int:
        return count_conversation_tokens(self.model, messages)

    @staticmethod
    def _ensure_ids(messages):
        for message in messages:
            if message.id is None:
                message.id = str(uuid.uuid4())

    def _projected(self, messages, runtime):
        """The request view: counts must match what the model receives."""
        from agents.memory.projection import project_tool_messages
        from project_workspace.store import ProjectStore

        project_id = _runtime_project_id(runtime)
        if not project_id:
            return list(messages)
        return project_tool_messages(messages, store=self.project_store or ProjectStore(),
                                     project_id=project_id)

    def _cutoff(self, messages) -> int:
        """The earliest block boundary whose tail fits the keep target, else the last one.

        Zero means nothing can be compacted: only the task (and an earlier
        summary) precede the newest block, or a pending decision pins it.
        """
        boundaries = [0, *_complete_block_boundaries(messages), len(messages)]
        candidates = []
        for start, end in zip(boundaries, boundaries[1:]):
            if start and any(not isinstance(m, HumanMessage) for m in messages[:start]):
                candidates.append(start)
            # A block awaiting the user's decision, and everything after it, stays verbatim.
            if any(isinstance(m, ToolMessage) and result_requires_user(m) for m in messages[start:end]):
                break
        for index in candidates:
            if self._tokens(messages[index:]) <= self.budget.compact_keep_tokens:
                return index
        return candidates[-1] if candidates else 0

    def _cached_cutoff(self, projected) -> int | None:
        """Cut where an overflow retry already summarized, so one summary serves both."""
        if self._cached_summary is None:
            return None
        summarized = self._cached_summary[0]
        ids = tuple(m.id or "" for m in projected
                    if _is_session_summary(m) or not isinstance(m, HumanMessage))
        if not summarized or ids[:len(summarized)] != summarized:
            return None
        return next(index for index, m in enumerate(projected) if m.id == summarized[-1]) + 1

    def _compacted(self, originals, projected, runtime, *, forced):
        """Return the replacement message list, or None when nothing needs to change."""
        from agents.streaming.events import extract_tool_trace_from_messages

        cutoff = self._cached_cutoff(projected) or self._cutoff(projected)
        total = self._tokens(projected)
        if cutoff == 0:
            if forced or total > self.budget.usable_tokens:
                raise ContextCompactionError(
                    "The latest indivisible message/tool-call block exceeds the worker's context window.",
                    code="worker_block_exceeds_context",
                    diagnostics={"block_tokens": total, "usable_tokens": self.budget.usable_tokens},
                )
            return None
        by_id = {message.id: message for message in originals}
        earlier, tail = projected[:cutoff], projected[cutoff:]
        task = next((m for m in earlier if isinstance(m, HumanMessage) and not _is_session_summary(m)), None)
        # Consumed results get verified archive pointers first, so the summary can cite them.
        archived = archive_summary_inputs(
            earlier, original_messages=originals, runtime=runtime,
            project_store=self.project_store, current_invocation_only=True,
        )
        consumed = [m for m in archived if task is None or m.id != task.id]
        summary = self._summary(consumed, tail, runtime)
        records = extract_tool_trace_from_messages([by_id.get(m.id, m) for m in consumed])
        kept = [by_id.get(m.id, m) for m in tail]  # the checkpoint keeps complete results
        return [*([by_id.get(task.id, task)] if task else []),
                self._summary_message(summary, records), *kept]

    @staticmethod
    def _summary_message(summary: str, records: list) -> HumanMessage:
        return HumanMessage(
            content=("Earlier working history was compacted; the task above and the messages below "
                     "are verbatim. Results listed under LOOK-BACK REFERENCES can be re-read with "
                     f"read_tool_output.\n\n{summary}"),
            additional_kwargs={"lc_source": "summarization", "gbclaw_worker_summary_version": 1,
                               "compacted_tool_trace": records},
        )

    def _summary(self, consumed, tail, runtime) -> str:
        # An overflow retry summarizes the request copy; the next step reuses that text.
        key = tuple(message.id or "" for message in consumed)
        if self._cached_summary is not None and self._cached_summary[0] == key:
            return self._cached_summary[1]
        text = self._create_summary(consumed, tail, runtime)
        self._cached_summary = (key, text)
        return text

    def _prompt(self, carry, batch, tail, correction=None) -> str:
        source = [*([self._summary_message(carry, [])] if carry else []), *batch]
        return render_worker_summary_prompt(
            source, max_chars=self.budget.summary_max_chars, retained=tail, correction=correction)

    def _create_summary(self, consumed, tail, runtime) -> str:
        """Summarize in block batches that fit the summary model, carrying the text forward."""
        boundaries = [0, *_complete_block_boundaries(consumed), len(consumed)]
        groups = [consumed[s:e] for s, e in zip(boundaries, boundaries[1:]) if s < e]
        overhead = self._tokens([HumanMessage(content=self._prompt("", [], tail))])
        carry = ""
        while groups:
            batch, used, size = [], 0, overhead + self._tokens([HumanMessage(content=carry)])
            for group in groups:
                size += self._tokens(worker_summary_input_messages(group))
                if batch and size > self.budget.summary_input_tokens:
                    break
                batch, used = [*batch, *group], used + 1
            carry = self._summarize(carry, batch, tail, runtime)
            groups = groups[used:]
        return carry

    def _summarize(self, carry, batch, tail, runtime) -> str:
        project_id = _runtime_project_id(runtime)
        correction = None
        for attempt in (1, 2):
            prompt = self._prompt(carry, batch, tail, correction)
            tokens = self._tokens([HumanMessage(content=prompt)])
            try:
                response = self.summary_model.invoke(
                    prompt, config={"metadata": {"lc_source": "summarization"}})
            except Exception as exc:
                raise summary_model_error(exc, attempt=attempt, input_tokens=tokens) from exc
            try:
                if is_truncated(response):
                    raise SessionSummaryValidationError(
                        "The summary model returned an incomplete response.",
                        code="summary_truncated_response")
                # Excess COMPLETED lines move to an archived file instead of failing the summary.
                return fit_worker_summary(
                    validate_worker_summary(response.text), max_chars=self.budget.summary_max_chars,
                    archive=lambda body: archive_compacted_text(self.project_store, project_id, body))
            except SessionSummaryValidationError as exc:
                if attempt == 2:
                    raise ContextCompactionError(str(exc), code=exc.code, diagnostics={
                        **exc.diagnostics, "summary_attempt": attempt, "summary_input_tokens": tokens,
                    }) from exc
                correction = exc.code
        raise AssertionError("unreachable")

    def before_model(self, state, runtime):
        messages = state["messages"]
        self._ensure_ids(messages)
        projected = self._projected(messages, runtime)
        forced, self._compact_next = self._compact_next, False
        if not forced and self._tokens(projected) <= self.budget.compact_trigger_tokens:
            return None
        try:
            replacement = self._compacted(messages, projected, runtime, forced=forced)
        except ContextCompactionError as exc:
            if (exc.code == "worker_block_exceeds_context" or forced
                    or self._tokens(projected) > self.budget.usable_tokens):
                raise
            # The summary model is unavailable but the request still fits; try again next step.
            LOGGER.warning("Worker compaction unavailable; the request continues: %s",
                           {"code": exc.code, **exc.diagnostics})
            return None
        if replacement is None:
            return None
        self._cached_summary = None
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *replacement]}

    async def abefore_model(self, state, runtime):
        return self.before_model(state, runtime)

    def _retry_request(self, request, overflow: ContextCompactionError):
        """The provider rejected the request: compact the request copy, and the checkpoint next step."""
        messages = list(request.messages)  # already the projected request view
        self._ensure_ids(messages)
        try:
            replacement = self._compacted(messages, messages, request.runtime, forced=True)
        except ContextCompactionError as exc:
            exc.diagnostics.update(cause=overflow.code, **overflow.diagnostics)
            raise
        self._compact_next = True
        return request.override(messages=replacement)

    def _exhausted(self, request, cause):
        return ContextCompactionError(
            "The request still exceeds the model's context window after compaction.",
            code="worker_context_exhausted",
            diagnostics={"request_tokens": self._tokens(request.messages),
                         "usable_tokens": self.budget.usable_tokens},
        )

    def wrap_model_call(self, request, handler):
        try:
            return handler(request)
        except Exception as exc:
            overflow = provider_overflow(exc)
            if overflow is None:
                raise
            retry = self._retry_request(request, overflow)
        try:
            return handler(retry)
        except Exception as exc:
            if provider_overflow(exc) is None:
                raise
            raise self._exhausted(retry, exc) from exc

    async def awrap_model_call(self, request, handler):
        try:
            return await handler(request)
        except Exception as exc:
            overflow = provider_overflow(exc)
            if overflow is None:
                raise
            retry = self._retry_request(request, overflow)
        try:
            return await handler(retry)
        except Exception as exc:
            if provider_overflow(exc) is None:
                raise
            raise self._exhausted(retry, exc) from exc


__all__ = ["WorkerCompactionMiddleware"]
