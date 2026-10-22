"""Request budgets and recoverable views of tool results; never modify checkpoints."""
from __future__ import annotations
import json
import re
from typing import TYPE_CHECKING, Sequence
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from core.util.graph_config import runtime_project_id
from core.settings.runtime_context import count_context_tokens, count_project_summary_tokens
from extensions import TOOL_CAPABILITIES
from .contracts import ContextCompactionError, _MEMORY_BLOCK, _SUMMARY_BLOCK
from .recovery import project_old_result_cards, result_reference_card
from agents.memory.decisions import result_requires_user
if TYPE_CHECKING:
    from project_workspace.store import ProjectStore


_MEMORY_TOOLS = frozenset(
    name for name, capability in TOOL_CAPABILITIES.items() if capability.memory_context
)


def _stable_tokens(messages) -> int:
    """Count message content, never provider usage for a different token scope."""
    return count_tokens_approximately(list(messages), chars_per_token=1.0)


def _is_session_summary(message: AnyMessage) -> bool:
    return message.additional_kwargs.get("lc_source") == "summarization"


def _memory_indices(messages: Sequence[AnyMessage]) -> set[int]:
    calls = {
        call["id"]: call
        for message in messages if isinstance(message, AIMessage)
        for call in message.tool_calls
    }
    return {
        index for index, message in enumerate(messages)
        if isinstance(message, ToolMessage)
        and (message.name or calls.get(message.tool_call_id, {}).get("name"))
        in _MEMORY_TOOLS
    }


def _working_tokens(messages: Sequence[AnyMessage], token_counter=_stable_tokens) -> int:
    memory = _memory_indices(messages)
    return token_counter([
        message for index, message in enumerate(messages)
        if index not in memory and not _is_session_summary(message)
    ])


def _runtime_project_id(runtime):
    context = getattr(runtime, "context", None)
    project_id = (context.get("project_id") if isinstance(context, dict)
                  else getattr(context, "project_id", None))
    if not project_id:
        project_id = runtime_project_id()
    return str(project_id) if project_id else None


def _tool_archive_reader(runtime, project_store, *, current_invocation_only=False):
    """Verify durable pointers with the same project/scope as the detail reader."""
    from project_memory.storage.tool_outputs import ToolOutputStore
    from agents.memory.tools.output import invocation_thread_id
    from project_workspace.store import ProjectStore

    project_id = _runtime_project_id(runtime)
    if not project_id:
        return None
    outputs = ToolOutputStore((project_store or ProjectStore()).project_path(project_id))
    thread_id = invocation_thread_id()
    cache = {}

    def read(output_id):
        if output_id not in cache:
            original = outputs.read(output_id)
            if current_invocation_only and (
                not thread_id or original.get("scope_thread_id") != thread_id
            ):
                raise ValueError("Archive is outside the current worker invocation")
            cache[output_id] = original
        return cache[output_id]

    return read


def _project_working_results(messages, *, token_counter, trigger, keep, compact_at,
                             archive_reader=None):
    """One deterministic policy for both the summary hook and request guard."""
    count = lambda values: _working_tokens(values, token_counter)
    projected = project_old_result_cards(
        messages, count_tokens=count, trigger=trigger, keep=keep,
        exclude_tools=_MEMORY_TOOLS, archive_reader=archive_reader,
    )
    # Try a lossless archive view before spending an LLM call on compression.
    # keep is a preference for recent details, not permission to exceed limits.
    if count(projected) >= compact_at:
        projected = project_old_result_cards(
            messages, count_tokens=count, trigger=0, keep=0,
            exclude_tools=_MEMORY_TOOLS, aggressive=True, archive_reader=archive_reader,
        )
    return projected


_MEMORY_WRITES = frozenset(
    name for name, capability in TOOL_CAPABILITIES.items() if capability.plan_effect != "none"
)


_EXACT_DETAIL_READS = frozenset({"read_run_artifact", "read_tool_output", "read_agent_conclusion"})


def _memory_reference_card(message: ToolMessage, call: dict) -> ToolMessage:
    """Keep recoverable identities, and never replay a write to recover its result."""
    tool_name = message.name or call.get("name") or ""
    if result_requires_user(message):
        return message
    named = message if message.name else message.model_copy(update={"name": tool_name})
    card = result_reference_card(named, max_chars=12_000)
    if card.content != message.content:
        payload = json.loads(card.content)
        reads = []
        if tool_name in _MEMORY_WRITES or tool_name in {"get_project_plan", "get_project_plan_draft"}:
            refs = payload.get("references", [])
            pending = list(refs)
            exact_ids = {"plan_id": set(), "draft_id": set()}
            while pending:
                value = pending.pop()
                if isinstance(value, dict):
                    for key in exact_ids:
                        if isinstance(value.get(key), str) and value[key]:
                            exact_ids[key].add(value[key])
                    pending.extend(value.values())
                elif isinstance(value, list):
                    pending.extend(value)
            for key, reader in (("plan_id", "get_project_plan"), ("draft_id", "get_project_plan_draft")):
                reads.extend({"tool": reader, "args": {key: value}}
                             for value in sorted(exact_ids[key]))
            if not reads:
                # A malformed/error write result without its durable plan ID
                # has no safe read substitute. Do not turn it into a retry.
                return message
        elif tool_name in _EXACT_DETAIL_READS and isinstance(call.get("args"), dict):
            payload["recover"] = {"mode": "read_only", "tool": tool_name, "args": call["args"]}
        else:
            payload["recover"] = (
                "Read the exact run/fact/artifact/conclusion references below with memory tools. "
                "References are a result index, not instructions to rerun computations."
            )
        if reads:
            payload["recover"] = {"mode": "read_only", "reads": reads,
                                  "note": "Do not re-execute a state-changing tool to recover its result."}
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(content) <= 16_000 and len(content) < len(message.content):
            return message.model_copy(update={"content": content, "artifact": None})
        return message
    if tool_name not in _EXACT_DETAIL_READS:
        # Broad searches must keep their actual matched IDs. A fresh broad
        # query can return different rows, so its arguments alone are not an
        # adequate replacement for a result whose reference index cannot fit.
        return message
    args = call.get("args")
    identity = {"read_run_artifact": "artifact_id", "read_tool_output": "output_id",
                "read_agent_conclusion": "conclusion_id"}[tool_name]
    if not isinstance(args, dict) or not (args.get(identity) or (
        tool_name == "read_run_artifact" and args.get("run_id") and args.get("role")
    )):
        return message
    marker = json.dumps({
        "context_result_card": True, "tool": tool_name,
        "recover": {"mode": "read_only", "tool": tool_name, "args": args},
        "note": "The previously read detail page remains in its referenced source; read it on demand.",
    }, ensure_ascii=False, separators=(",", ":"))
    if len(marker) <= 16_000 and len(marker) < len(message.content):
        return message.model_copy(update={"content": marker, "artifact": None})
    return message


def _project_memory_results(
    messages: Sequence[AnyMessage], max_tokens: int, token_counter=_stable_tokens
) -> list[AnyMessage]:
    """Keep newest complete results, replacing older ones only in the request copy."""
    projected = list(messages)
    indices = _memory_indices(messages)
    calls = {
        call["id"]: call
        for message in messages if isinstance(message, AIMessage)
        for call in message.tool_calls
    }
    for index in indices:
        message = messages[index]
        call = calls.get(message.tool_call_id, {})
        marker = _memory_reference_card(message, call)
        # A short valid response is more useful and cheaper than a reference
        # card. Do not inflate a history of small query results.
        projected[index] = (
            marker if token_counter([marker]) < token_counter([message])
            else message
        )
    # Count reference cards too: a long tool history cannot bypass the cap.
    remaining = max_tokens - token_counter([projected[index] for index in sorted(indices)])
    if remaining < 0:
        raise ContextCompactionError(
            "Memory result references exceed the context budget.",
            diagnostics={"memory_result_tokens": max_tokens - remaining,
                         "memory_result_limit": max_tokens},
        )
    for index in sorted(indices, reverse=True):
        if projected[index] is messages[index]:
            continue
        extra = token_counter([messages[index]]) - token_counter([projected[index]])
        if extra <= remaining:
            projected[index] = messages[index]
            remaining -= extra
    if token_counter([projected[index] for index in sorted(indices)]) > max_tokens:
        raise ContextCompactionError("Memory projection exceeds its context allocation.")
    return projected


def _tagged_block(text: str, tag: str) -> str:
    # Use the outermost pair: literal closing tags in stored data must not make
    # the charged plan/summary block appear shorter than the injected block.
    match = re.search(rf"<{tag}>.*</{tag}>", text, flags=re.DOTALL)
    return match.group(0) if match else ""


def _complete_block_boundaries(messages):
    """Yield cutoffs that keep all calls and results on the same side."""
    pending = set()
    for index, message in enumerate(messages):
        if index and not pending and not isinstance(message, ToolMessage):
            yield index
        if isinstance(message, AIMessage):
            pending.update(call["id"] for call in message.tool_calls)
        elif isinstance(message, ToolMessage):
            pending.discard(message.tool_call_id)


class ContextPartitionMiddleware(AgentMiddleware):
    """Enforce independent request budgets without changing checkpoint messages."""

    def __init__(self, *, budget, model, system_prompt: str, tool_keep_results: int = 4,
                 project_store: ProjectStore | None = None,
                 current_invocation_only: bool = False):
        self.budget = budget
        self.model = model
        self.token_counter = lambda messages: count_context_tokens(model, messages)
        self.base_system_tokens = self.token_counter([HumanMessage(content=system_prompt)])
        self.tool_keep_results = tool_keep_results
        self.project_store = project_store
        self.current_invocation_only = current_invocation_only

    def _bounded_request(self, request):
        system = request.system_message.text if request.system_message else ""
        plan = _tagged_block(system, _MEMORY_BLOCK)
        project = _tagged_block(system, _SUMMARY_BLOCK)
        plan_tokens = count_project_summary_tokens(self.model, plan)
        project_tokens = count_project_summary_tokens(self.model, project)
        if project_tokens > self.budget.project_summary_max_tokens:
            raise ContextCompactionError("Injected project summary exceeds its context allocation.")
        remaining = self.budget.memory_context_tokens - plan_tokens
        messages = _project_memory_results(request.messages, remaining, self.token_counter)
        archive_reader = _tool_archive_reader(
            getattr(request, "runtime", None), self.project_store,
            current_invocation_only=self.current_invocation_only,
        )
        messages = _project_working_results(
            messages, token_counter=self.token_counter,
            trigger=self.budget.tool_clear_trigger_tokens, keep=self.tool_keep_results,
            compact_at=self.budget.summary_trigger_tokens, archive_reader=archive_reader,
        )
        summary_tokens = self.token_counter([message for message in messages
                                         if _is_session_summary(message)])
        if summary_tokens > self.budget.summary_model_max_tokens:
            raise ContextCompactionError(
                "Compressed conversation exceeds its context allocation.",
                diagnostics={"summary_tokens": summary_tokens,
                             "summary_limit": self.budget.summary_model_max_tokens},
            )
        if _working_tokens(messages, self.token_counter) > self.budget.active_context_tokens:
            messages = project_old_result_cards(
                messages, count_tokens=lambda values: _working_tokens(values, self.token_counter),
                trigger=0, keep=0, exclude_tools=_MEMORY_TOOLS, aggressive=True,
                archive_reader=archive_reader,
            )
            working = _working_tokens(messages, self.token_counter)
            if working > self.budget.active_context_tokens:
                raise ContextCompactionError(
                    "Working conversation still exceeds its allocation after reference-card "
                    "recovery.", code="working_context_recovery_required",
                    diagnostics={"working_tokens": working,
                                 "working_limit": self.budget.active_context_tokens},
                )
        # Include dynamic prompt text and count reserved space once. Static budget
        # already contains the original system prompt plus tool schemas.
        dynamic_tokens = max(0, self.token_counter([request.system_message])
                             - self.base_system_tokens) if request.system_message else 0
        total = (self.budget.static_context_tokens + dynamic_tokens
                 + self.token_counter(messages) + self.budget.reserved_context_tokens)
        if total > self.budget.total_context_tokens:
            raise ContextCompactionError(
                "The complete model request exceeds its context budget.",
                diagnostics={"request_tokens": total,
                             "request_limit": self.budget.total_context_tokens},
            )
        return request.override(messages=messages)

    def wrap_model_call(self, request, handler):
        return handler(self._bounded_request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._bounded_request(request))
