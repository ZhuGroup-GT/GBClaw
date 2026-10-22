"""Bounded conversation continuity in a persistent thread checkpoint.

Session summary here belongs to the saved chat thread, not a temporary browser
visit. Reopening a project normally reuses its stored thread_id and this state;
elapsed time alone does not create a new conversation. The separate
project_memory.snapshot.session module reads project-wide memory snapshots and
does not summarize this thread's dialogue.
"""
from __future__ import annotations
import logging
import hashlib
import re
from typing import TYPE_CHECKING, Annotated
from typing_extensions import NotRequired
from langchain.agents.middleware import SummarizationMiddleware
from langchain.agents.middleware.types import AgentState, PrivateStateAttr
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from core.llm.generation import is_truncated
from core.settings.runtime_context import count_context_tokens, count_project_summary_tokens
from .contracts import ContextCompactionError
from .archive import archive_summary_inputs
from .recovery import project_old_result_cards
from agents.memory.decisions import result_requires_user
from .projection import (
    _MEMORY_TOOLS, _stable_tokens, _is_session_summary, _working_tokens,
    _runtime_project_id, _tool_archive_reader, _project_working_results,
    _complete_block_boundaries,
)
from .session_policy import (
    SESSION_SUMMARY_MAX_CHARS, SESSION_SUMMARY_PROMPT, SessionSummaryValidationError,
    validate_session_summary, render_session_summary_prompt, summary_input_messages,
)
if TYPE_CHECKING:
    from project_workspace.store import ProjectStore


def summary_model_error(exc, *, attempt, input_tokens):
    """Classify a failed summary call; shared by the session and worker compactors."""
    # Do not log str(exc): provider exceptions can embed headers, URLs or
    # request bodies. Keep the actual cause in-process and safe diagnostics
    # in persisted recovery data, even when checkpoint serde loses causes.
    error_type = re.sub(r"[^A-Za-z0-9_]", "", type(exc).__name__)[:80]
    status = getattr(exc, "status_code", None)
    status = status if isinstance(status, int) and 100 <= status <= 599 else None
    kind = error_type.lower()
    if "timeout" in kind:
        code = "summary_model_timeout"
    elif status in {401, 403} or "authentication" in kind or "permission" in kind:
        code = "summary_model_authentication_error"
    elif status == 429 or "ratelimit" in kind:
        code = "summary_model_rate_limited"
    else:
        code = "summary_model_unavailable"
    diagnostics = {"error_type": error_type, "summary_attempt": attempt,
                   "summary_input_tokens": input_tokens}
    if status is not None:
        diagnostics["http_status"] = status
    return ContextCompactionError("The summary model call failed.", code=code, diagnostics=diagnostics)


class SessionContextState(AgentState):
    session_summary_attempt: NotRequired[Annotated[str | None, PrivateStateAttr]]


class FailSafeSummarizationMiddleware(SummarizationMiddleware):
    """Compact only working dialogue and validate before replacing checkpoints."""

    state_schema = SessionContextState

    def __init__(self, *args, summary_max_tokens: int | None = None,
                 working_max_tokens: int | None = None,
                 memory_max_tokens: int = 65_536,
                 project_store: ProjectStore | None = None,
                 reference_target_tokens: int | None = None,
                 current_invocation_only: bool = False,
                 tool_keep_results: int = 4, **kwargs):
        supplied_counter = kwargs.get("token_counter")
        kwargs.setdefault("token_counter", _stable_tokens)
        kwargs.setdefault("summary_prompt", SESSION_SUMMARY_PROMPT)
        super().__init__(*args, **kwargs)
        if supplied_counter is None:
            self.token_counter = lambda messages: count_context_tokens(self.model, messages)
            self._partial_token_counter = self.token_counter
        self.summary_max_tokens = summary_max_tokens
        # The old token allocation remains a read/recovery limit. New writes
        # have a small, fixed content cap independent of conversation growth.
        self.summary_max_chars = min(SESSION_SUMMARY_MAX_CHARS, summary_max_tokens
                                     if summary_max_tokens is not None else SESSION_SUMMARY_MAX_CHARS)
        self.working_max_tokens = working_max_tokens
        self.memory_max_tokens = memory_max_tokens
        self.project_store = project_store
        self.tool_keep_results = tool_keep_results
        self.reference_target_tokens = (reference_target_tokens if reference_target_tokens is not None
                                        else max(1, working_max_tokens * 3 // 4)
                                        if working_max_tokens is not None else None)
        self.current_invocation_only = current_invocation_only

    def _projected_state(self, state, runtime, *, archive_reader=None):
        # Summarization's before_model hook runs before model wrappers. Apply
        # the same durable result projection here so a raw report cannot fail
        # the budget check before the model-only projection gets a chance.
        from project_workspace.store import ProjectStore
        from agents.memory.projection import project_tool_messages

        project_id = _runtime_project_id(runtime)
        projected = state["messages"]
        if project_id:
            self._ensure_message_ids(state["messages"])
            projected = project_tool_messages(
                projected, store=self.project_store or ProjectStore(), project_id=project_id,
            )
        if self.working_max_tokens is not None:
            projected = _project_working_results(
                projected, token_counter=self.token_counter,
                trigger=self.working_max_tokens // 2, keep=self.tool_keep_results,
                compact_at=self.reference_target_tokens, archive_reader=archive_reader,
            )
        return {**state, "messages": projected}

    @staticmethod
    def _restore_preserved_results(update, original_messages):
        # A new summary may replace old history, but retained ToolMessages
        # remain full checkpoint records; wrappers project them at request time.
        if update is None or "messages" not in update:
            return update
        originals = {message.id: message for message in original_messages if message.id}
        return {**update, "messages": [
            originals.get(message.id, message) if isinstance(message, ToolMessage) else message
            for message in update["messages"]
        ]}

    def before_model(self, state, runtime):
        archive_reader = _tool_archive_reader(
            runtime, self.project_store, current_invocation_only=self.current_invocation_only,
        )
        projected = self._projected_state(state, runtime, archive_reader=archive_reader)
        self._check_current_user_input(projected["messages"])
        self._ensure_message_ids(projected["messages"])
        attempt = self._legacy_attempt(projected["messages"])
        try:
            prepared = self._prepare_summary(projected, attempt)
            if prepared is None:
                return None
            earlier, preserved, current_context = prepared
            earlier, preserved = self._archive_consumed_results(
                earlier, preserved, projected["messages"], state["messages"], runtime,
            )
            if not earlier:
                return None
            summary = self._create_summary(earlier, current_context=current_context)
            update = self._summary_update(summary, preserved)
        except Exception as exc:
            self._recover_summary_failure(projected["messages"], exc, archive_reader=archive_reader)
            return {"session_summary_attempt": attempt} if attempt else None
        return self._restore_preserved_results(update, state["messages"])

    async def abefore_model(self, state, runtime):
        archive_reader = _tool_archive_reader(
            runtime, self.project_store, current_invocation_only=self.current_invocation_only,
        )
        projected = self._projected_state(state, runtime, archive_reader=archive_reader)
        self._check_current_user_input(projected["messages"])
        self._ensure_message_ids(projected["messages"])
        attempt = self._legacy_attempt(projected["messages"])
        try:
            prepared = self._prepare_summary(projected, attempt)
            if prepared is None:
                return None
            earlier, preserved, current_context = prepared
            earlier, preserved = self._archive_consumed_results(
                earlier, preserved, projected["messages"], state["messages"], runtime,
            )
            if not earlier:
                return None
            summary = await self._acreate_summary(earlier, current_context=current_context)
            update = self._summary_update(summary, preserved)
        except Exception as exc:
            self._recover_summary_failure(projected["messages"], exc, archive_reader=archive_reader)
            return {"session_summary_attempt": attempt} if attempt else None
        return self._restore_preserved_results(update, state["messages"])

    @staticmethod
    def _build_new_messages(summary):
        return [HumanMessage(content=summary, additional_kwargs={
            "lc_source": "summarization", "gbclaw_session_summary_version": 1,
        })]

    def _legacy_attempt(self, messages):
        summaries = [message for message in messages if _is_session_summary(message)]
        if not summaries:
            return None
        try:
            if len(summaries) != 1 or summaries[0].additional_kwargs.get(
                    "gbclaw_session_summary_version") != 1:
                raise ValueError("Older summary policy")
            validate_session_summary(summaries[0].text, max_chars=self.summary_max_chars)
            return None
        except ValueError:
            latest = next((message for message in reversed(messages)
                           if isinstance(message, HumanMessage) and not _is_session_summary(message)), None)
            identity = (latest.id or latest.text) if latest is not None else ""
            value = "session-policy-1\0" + identity + "\0" + "\0".join(m.text for m in summaries)
            return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _current_context(messages):
        latest = next((index for index in range(len(messages) - 1, -1, -1)
                       if isinstance(messages[index], HumanMessage)
                       and not _is_session_summary(messages[index])), None)
        if latest is None:
            return []
        previous = next((index for index in range(latest - 1, -1, -1)
                         if isinstance(messages[index], AIMessage) and not messages[index].tool_calls), None)
        # Include every retained user constraint and completed-tool status/ref,
        # not just the latest question. Otherwise a new focus can lose the
        # parameter selection or evidence IDs still present in the recent turn.
        # The policy renderer strips tool data and execution args from this view.
        return [message for index, message in enumerate(messages)
                if index == previous or isinstance(message, ToolMessage)
                or isinstance(message, HumanMessage) and not _is_session_summary(message)
                or isinstance(message, AIMessage) and message.tool_calls]

    def _prepare_summary(self, state, attempt):
        messages = state["messages"]
        self._ensure_message_ids(messages)
        working_tokens = _working_tokens(messages, self.token_counter)
        summary_tokens = self.token_counter([m for m in messages if _is_session_summary(m)])
        hard_pressure = ((self.working_max_tokens is not None and working_tokens > self.working_max_tokens)
                         or (self.summary_max_tokens is not None and summary_tokens > self.summary_max_tokens))
        # A weak model may fail migration. Retry on the next user request, not
        # every tool step; the ordinary guard still enforces the read budgets.
        if attempt and attempt == state.get("session_summary_attempt") and not hard_pressure:
            return None
        if not attempt and not self._should_summarize(messages, working_tokens):
            return None
        cutoff, keep_previous_answer = self._summary_cutoff(messages)
        if attempt:
            # A small legacy summary still needs the new content contract.
            cutoff = max(cutoff, max(index + 1 for index, message in enumerate(messages)
                                     if _is_session_summary(message)))
        if cutoff <= 0:
            return None
        earlier, preserved = self._partition_messages(
            messages, cutoff, keep_previous_answer=keep_previous_answer,
        )
        return (earlier, preserved, self._current_context(preserved)) if earlier else None

    def _summary_update(self, summary, preserved):
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES),
                             *self._build_new_messages(summary), *preserved],
                "session_summary_attempt": None}

    def _archive_consumed_results(self, earlier, preserved, projected, originals, runtime):
        archived = archive_summary_inputs(
            earlier, original_messages=originals, runtime=runtime,
            project_store=self.project_store, current_invocation_only=self.current_invocation_only,
        )
        # The helper returns the identical message when a recoverable archive
        # cannot be established. Preserve that entire call/result block; a
        # shorter summary must not remove the only readable copy of a result.
        protected = {message.id for message in preserved}
        consumed = []
        boundaries = [0, *_complete_block_boundaries(earlier), len(earlier)]
        for start, end in zip(boundaries, boundaries[1:]):
            if any(isinstance(earlier[index], ToolMessage) and archived[index] is earlier[index]
                   for index in range(start, end)):
                protected.update(message.id for message in earlier[start:end])
            else:
                consumed.extend(archived[start:end])
        return consumed, [message for message in projected if message.id in protected]

    def _check_current_user_input(self, messages):
        if self.working_max_tokens is None:
            return
        latest = next((message for message in reversed(messages)
                       if isinstance(message, HumanMessage) and not _is_session_summary(message)), None)
        if latest is not None and self.token_counter([latest]) > self.working_max_tokens:
            raise ContextCompactionError(
                "The current user message alone exceeds the working context limit; "
                "existing results are preserved.",
                code="user_input_exceeds_working_context",
                action="Keep the instructions concise and place large source data in a referenced "
                       "input artifact. Retry the request using that reference; do not rerun completed science.",
            )

    def _recover_summary_failure(self, messages, cause, *, archive_reader=None):
        # A soft-threshold summary failure is not a reason to abandon an input
        # that still fits. Retry no LLM or scientific tool here: stronger cards
        # are a deterministic request-only fallback, applied again by the guard.
        diagnostics = {"stage": "session_summary", "failure_code": getattr(
            cause, "code", "summary_internal_error"),
            **getattr(cause, "diagnostics", {}),
            "working_tokens_before": _working_tokens(messages, self.token_counter),
            "working_limit": self.working_max_tokens,
            "summary_limit": self.summary_max_tokens}
        if self.working_max_tokens is not None:
            compact = project_old_result_cards(
                messages, count_tokens=lambda values: _working_tokens(values, self.token_counter),
                trigger=0, keep=0, exclude_tools=_MEMORY_TOOLS, aggressive=True,
                archive_reader=archive_reader,
            )
            summary_tokens = self.token_counter([
                message for message in compact if _is_session_summary(message)
            ])
            summary_fits = self.summary_max_tokens is None or summary_tokens <= self.summary_max_tokens
            diagnostics.update(working_tokens_after=_working_tokens(compact, self.token_counter),
                               summary_tokens=summary_tokens,
                               protected_user_tokens=self.token_counter([
                                   message for message in compact if isinstance(message, HumanMessage)
                                   and not _is_session_summary(message)]),
                               pending_result_tokens=self.token_counter([
                                   message for message in compact if result_requires_user(message)]))
            if summary_fits and _working_tokens(compact, self.token_counter) <= self.working_max_tokens:
                logging.getLogger(__name__).warning(
                    "Session summary unavailable; request can continue: %s", diagnostics,
                    extra={"context_diagnostics": diagnostics, "context_recovery": "continue"},
                )
                return None
        logging.getLogger(__name__).warning(
            "Session context recovery paused: %s", diagnostics,
            extra={"context_diagnostics": diagnostics, "context_recovery": "paused"},
        )
        raise ContextCompactionError(
            "Context recovery could not fit the current request without discarding user "
            "constraints or unresolved decisions.",
            code="context_recovery_needs_smaller_request",
            action="Resume using the saved task state and conclusion references with a narrower "
                   "request, or retry when the summary model is available. Completed computations "
                   "must be reused rather than rerun.",
            diagnostics=diagnostics,
        ) from cause

    def _should_summarize_based_on_reported_tokens(self, messages, threshold) -> bool:
        return False

    def _should_summarize(self, messages, total_tokens) -> bool:
        if self.summary_max_tokens is not None and self.token_counter([
            message for message in messages if _is_session_summary(message)
        ]) > self.summary_max_tokens:
            return True
        return super()._should_summarize(messages, _working_tokens(messages, self.token_counter))

    def _determine_cutoff_index(self, messages):
        return self._summary_cutoff(messages)[0]

    def _summary_cutoff(self, messages):
        """Return the cutoff and whether the answer before the latest request stays verbatim."""
        if self.working_max_tokens is None or self.keep[0] != "tokens":
            return super()._determine_cutoff_index(messages), True
        # keep is a compression target, not the hard working allocation. If the
        # latest complete block exceeds that target but fits the allocation,
        # summarize earlier blocks and retain it intact. Check call IDs as well
        # as adjacency so parallel/interleaved tool results are never orphaned.
        # When no cutoff fits while the previous answer stays verbatim, summarize
        # it as well: its full text remains in the chat history, and keeping it
        # would otherwise pause every later request in this thread.
        for keep_previous_answer in (True, False):
            candidates = []
            for index in _complete_block_boundaries(messages):
                earlier, preserved = self._partition_messages(
                    messages, index, keep_previous_answer=keep_previous_answer,
                )
                if earlier:
                    candidates.append((index, _working_tokens(preserved, self.token_counter)))
            for limit in (min(self.keep[1], self.working_max_tokens), self.working_max_tokens):
                for index, tokens in candidates:
                    if tokens <= limit:
                        return index, keep_previous_answer
        if _working_tokens(messages, self.token_counter) > self.working_max_tokens:
            raise ContextCompactionError(
                "The latest indivisible message/tool-call block exceeds the working "
                "context limit. Use a smaller input or paginated tool result."
            )
        return 0, True

    @staticmethod
    def _partition_messages(messages, cutoff_index, *, keep_previous_answer=True):
        # Keep the complete current user request even when a long single turn
        # needs compaction. Its immediately preceding answer can contain the
        # options to which "yes" / "use option 2" refers, so retain it as well
        # unless the caller has found that it cannot fit.
        latest_user = next((index for index in range(len(messages) - 1, -1, -1)
                            if isinstance(messages[index], HumanMessage)
                            and not _is_session_summary(messages[index])), None)
        protected = set()
        if latest_user is not None:
            protected.add(latest_user)
            previous_answer = next((index for index in range(latest_user - 1, -1, -1)
                                    if isinstance(messages[index], AIMessage)
                                    and not messages[index].tool_calls), None)
            if previous_answer is not None and keep_previous_answer:
                protected.add(previous_answer)
        # A pending choice can precede the user's short "option 2" reply.
        # Never ask the summarizer to reconstruct those choices from memory;
        # keep their complete call/result blocks together even across turns.
        boundaries = [0, *_complete_block_boundaries(messages), len(messages)]
        for start, end in zip(boundaries, boundaries[1:]):
            if any(result_requires_user(messages[index]) for index in range(start, end)):
                protected.update(range(start, end))
        summarized = [message for index, message in enumerate(messages[:cutoff_index])
                      if index not in protected]
        preserved = [message for index, message in enumerate(messages)
                     if index in protected or index >= cutoff_index]
        return summarized, preserved

    def _validated(self, summary: str) -> str:
        try:
            text = validate_session_summary(summary, max_chars=self.summary_max_chars)
        except SessionSummaryValidationError as exc:
            raise ContextCompactionError(str(exc), code=exc.code,
                                         diagnostics=exc.diagnostics) from exc
        if self.summary_max_tokens is not None:
            wrapped = self._build_new_messages(text)
            # The wrapper occupies the same compressed-history allocation.
            tokens = max(self.token_counter(wrapped), count_project_summary_tokens(
                self.model, wrapped[0].text
            ))
            if tokens > self.summary_max_tokens:
                raise ContextCompactionError(
                    "Session context compaction exceeded its summary token limit.",
                    code="summary_output_exceeds_budget",
                    diagnostics={"summary_output_tokens": tokens,
                                 "summary_output_limit": self.summary_max_tokens},
                )
        return text

    def _summary_prompt_text(self, messages, *, correction=None, current_context=()):
        return render_session_summary_prompt(
            messages, summary_prompt=self.summary_prompt, max_chars=self.summary_max_chars,
            correction=correction, current_context=current_context,
        )

    def _prompt_tokens(self, prompt):
        return max(count_context_tokens(self.model, [HumanMessage(content=prompt)]),
                   count_project_summary_tokens(self.model, prompt))

    def _summary_input_tokens(self, messages, *, current_context=()):
        return self._prompt_tokens(self._summary_prompt_text(messages, current_context=current_context))

    def _trim_messages_for_summary(self, messages):
        # Never silently drop the beginning (including an earlier summary).
        if (self.trim_tokens_to_summarize is not None
                and self._summary_input_tokens(messages) > self.trim_tokens_to_summarize):
            raise ContextCompactionError(
                "A summary input block exceeds the memory model input limit.",
                code="summary_input_exceeds_budget",
            )
        return messages

    def _summary_batches(self, messages):
        # The summarizer receives conversation continuity, not the main
        # model's detailed memory allocation. Strip scientific results before
        # source batching so a large result cannot crowd out user constraints.
        projected = summary_input_messages(messages)
        boundaries = [0, *_complete_block_boundaries(projected), len(projected)]
        return [projected[start:end] for start, end in zip(boundaries, boundaries[1:])
                if start < end]

    def _next_summary_batch(self, groups, carry, *, current_context=()):
        batch = self._build_new_messages(carry) if carry else []
        consumed = 0
        for group in groups:
            candidate = [*batch, *group]
            if (self.trim_tokens_to_summarize is not None
                    and self._summary_input_tokens(candidate, current_context=current_context)
                    > self.trim_tokens_to_summarize):
                break
            batch = candidate
            consumed += 1
        if not consumed:
            raise ContextCompactionError(
                "An indivisible conversation block plus its earlier summary exceeds "
                "the memory model input limit.",
                code="summary_input_exceeds_budget",
            )
        return batch, consumed

    def _summary_request(self, messages, correction, *, current_context=()):
        prompt = self._summary_prompt_text(messages, correction=correction, current_context=current_context)
        tokens = self._prompt_tokens(prompt)
        if self.trim_tokens_to_summarize is not None and tokens > self.trim_tokens_to_summarize:
            raise ContextCompactionError(
                "Summary input exceeds the memory model budget.",
                code="summary_input_exceeds_budget",
                diagnostics={"summary_input_tokens": tokens,
                             "summary_input_limit": self.trim_tokens_to_summarize},
            )
        return prompt, tokens

    @staticmethod
    def _summary_model_error(exc, *, attempt, input_tokens):
        return summary_model_error(exc, attempt=attempt, input_tokens=input_tokens)

    def _validate_summary_attempt(self, response, *, attempt, input_tokens):
        try:
            if is_truncated(response):
                raise ContextCompactionError(
                    "The summary model returned an incomplete response.",
                    code="summary_truncated_response",
                )
            return self._validated(response.text)
        except ContextCompactionError as exc:
            exc.diagnostics.update(summary_attempt=attempt, summary_input_tokens=input_tokens)
            raise

    @staticmethod
    def _can_correct_summary(error, attempt):
        return attempt == 1 and error.code in {
            "summary_empty_response", "summary_invalid_structure", "summary_output_exceeds_budget",
            "summary_truncated_response",
        }

    def _summarize_batch(self, batch, *, current_context=()):
        correction = None
        for attempt in (1, 2):
            prompt, tokens = self._summary_request(batch, correction, current_context=current_context)
            try:
                response = self.model.invoke(prompt, config={"metadata": {"lc_source": "summarization"}})
            except Exception as exc:
                raise self._summary_model_error(exc, attempt=attempt, input_tokens=tokens) from exc
            try:
                return self._validate_summary_attempt(response, attempt=attempt, input_tokens=tokens)
            except ContextCompactionError as exc:
                if not self._can_correct_summary(exc, attempt):
                    raise
                correction = exc.code

    async def _asummarize_batch(self, batch, *, current_context=()):
        correction = None
        for attempt in (1, 2):
            prompt, tokens = self._summary_request(batch, correction, current_context=current_context)
            try:
                response = await self.model.ainvoke(
                    prompt, config={"metadata": {"lc_source": "summarization"}},
                )
            except Exception as exc:
                raise self._summary_model_error(exc, attempt=attempt, input_tokens=tokens) from exc
            try:
                return self._validate_summary_attempt(response, attempt=attempt, input_tokens=tokens)
            except ContextCompactionError as exc:
                if not self._can_correct_summary(exc, attempt):
                    raise
                correction = exc.code

    def _create_summary(self, messages_to_summarize, *, current_context=()):
        groups = self._summary_batches(messages_to_summarize)
        carry = ""
        while groups:
            batch, consumed = self._next_summary_batch(groups, carry, current_context=current_context)
            carry = self._summarize_batch(batch, current_context=current_context)
            groups = groups[consumed:]
        return self._validated(carry)

    async def _acreate_summary(self, messages_to_summarize, *, current_context=()):
        groups = self._summary_batches(messages_to_summarize)
        carry = ""
        while groups:
            batch, consumed = self._next_summary_batch(groups, carry, current_context=current_context)
            carry = await self._asummarize_batch(batch, current_context=current_context)
            groups = groups[consumed:]
        return self._validated(carry)
