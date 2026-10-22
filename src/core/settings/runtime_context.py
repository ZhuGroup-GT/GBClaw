"""Token policy for orchestrator context, disposable-worker pools and project working-summary updates."""

from __future__ import annotations

from configparser import ConfigParser
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.base import BaseLanguageModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from core.paths import REPO_ROOT
from core.settings.env import get_env_bool, get_env_int
from core.llm.config import DISABLED_REASONING_ALLOWANCE_TOKENS, LLMEndpoint, default_max_input_tokens
from core.llm.factory import text_token_counter
from core.llm.providers.tokens import TextTokenCounter
from core.llm.reasoning import reasoning_text_from_mapping


RUNTIME_CONTEXT_SECTION = "runtime_context"
TOOL_KEEP_RESULTS_ENV = "GBCLAW_TOOL_KEEP_RESULTS"
CONTEXT_1M_ENV = "GBCLAW_CONTEXT_1M"


@dataclass(frozen=True)
class RuntimeContextConfig:
    """Base 256K defaults; optional .key overrides are scaled by the mode switch."""

    enabled: bool = True
    total_context_tokens: int = 256 * 1024
    # Working conversation and ordinary tool results, excluding compressed
    # history and separately budgeted memory tool results.
    active_context_tokens: int = 64 * 1024
    tool_keep_results: int = 4
    project_summary_max_tokens: int = 32_768
    project_summary_working_set_max_tokens: int = 65_536
    # Main-call reserve includes output, rather than adding output twice.
    safety_margin_tokens: int = 32_768
    summary_model_max_tokens: int = 32_768
    memory_context_tokens: int = 65_536
    # Disposable workers use one pool: compaction starts at this share of the
    # window left after the output reserve and the static prompt/tool schemas.
    worker_compact_fraction: float = 0.8

    def validate(self) -> None:
        positive = {
            "total_context_tokens": self.total_context_tokens,
            "active_context_tokens": self.active_context_tokens,
            "project_summary_max_tokens": self.project_summary_max_tokens,
            "project_summary_working_set_max_tokens": (
                self.project_summary_working_set_max_tokens
            ),
            "safety_margin_tokens": self.safety_margin_tokens,
            "summary_model_max_tokens": self.summary_model_max_tokens,
            "memory_context_tokens": self.memory_context_tokens,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.tool_keep_results < 0:
            raise ValueError("tool_keep_results must be non-negative")
        if not 0.3 <= self.worker_compact_fraction <= 0.95:
            raise ValueError("worker_compact_fraction must be between 0.3 and 0.95")
        allocations = (
            self.active_context_tokens
            + self.project_summary_max_tokens
            + self.summary_model_max_tokens
            + self.memory_context_tokens
            + self.safety_margin_tokens
        )
        if allocations >= self.total_context_tokens:
            raise ValueError(
                "Context allocations must leave room below total_context_tokens "
                "for system instructions and tool schemas"
            )
        if (
            self.project_summary_working_set_max_tokens
            <= self.project_summary_max_tokens
        ):
            raise ValueError(
                "project_summary_working_set_max_tokens must be greater than "
                "project_summary_max_tokens so incremental changes fit beside the "
                "previous working summary"
            )


@dataclass(frozen=True)
class ContextBudget:
    max_input_tokens: int
    static_context_tokens: int
    active_context_tokens: int
    tool_clear_trigger_tokens: int
    summary_trigger_tokens: int
    summary_keep_tokens: int
    summary_source_tokens: int
    total_context_tokens: int
    project_summary_max_tokens: int
    summary_model_max_tokens: int
    memory_context_tokens: int
    reserved_context_tokens: int


@dataclass(frozen=True)
class WorkerContextBudget:
    """One pool for a disposable worker: no per-bucket partition, one compaction trigger."""

    max_input_tokens: int
    total_context_tokens: int
    static_context_tokens: int
    reserved_context_tokens: int
    usable_tokens: int
    compact_trigger_tokens: int
    compact_keep_tokens: int
    summary_max_tokens: int      # the summary kept in context
    summary_output_tokens: int   # what the summary model may write; the excess COMPLETED moves to a file
    summary_input_tokens: int

    @property
    def summary_max_chars(self) -> int:
        # One character per token is the conservative floor for any tokenizer.
        return self.summary_max_tokens


MIN_WORKER_USABLE_TOKENS = 16_384


def load_runtime_context_config(
    key_path: Path | str | None = None,
) -> RuntimeContextConfig:
    path = (
        Path(key_path)
        if key_path is not None
        else REPO_ROOT / ".key"
    )
    parser = ConfigParser()
    if path.is_file():
        parser.read(path)
    section = RUNTIME_CONTEXT_SECTION
    defaults = RuntimeContextConfig()
    scale = 4 if get_env_bool(CONTEXT_1M_ENV) else 1

    def tokens(name: str) -> int:
        # .key values describe the base 256K profile; the switch scales each
        # token budget exactly once, including explicit per-bucket overrides.
        return parser.getint(section, name, fallback=getattr(defaults, name)) * scale

    config = RuntimeContextConfig(
        enabled=parser.getboolean(section, "enabled", fallback=True),
        total_context_tokens=tokens("total_context_tokens"),
        active_context_tokens=tokens("active_context_tokens"),
        tool_keep_results=get_env_int(TOOL_KEEP_RESULTS_ENV, 4, minimum=0),
        project_summary_max_tokens=tokens("project_summary_max_tokens"),
        project_summary_working_set_max_tokens=tokens(
            "project_summary_working_set_max_tokens"
        ),
        safety_margin_tokens=tokens("safety_margin_tokens"),
        summary_model_max_tokens=tokens("summary_model_max_tokens"),
        memory_context_tokens=tokens("memory_context_tokens"),
        worker_compact_fraction=parser.getfloat(
            section, "worker_compact_fraction", fallback=defaults.worker_compact_fraction),
    )
    config.validate()
    return config


def model_max_input_tokens(model: BaseChatModel) -> int | None:
    profile = getattr(model, "profile", None)
    if not isinstance(profile, dict):
        return None
    value = profile.get("max_input_tokens")
    return value if isinstance(value, int) and value > 0 else None


# Text counts are exact with a bundled vocabulary; per-message chat and tool
# framing is not published, so keep a small margin over the sum.
_FRAMING_MARGIN = 1.05


def _model_text_counter(model) -> TextTokenCounter | None:
    """Return the provider vocabulary counter for this model, or None for 1 char = 1 token."""
    name = str(getattr(model, "tiktoken_model_name", None)
               or getattr(model, "model_name", None)
               or getattr(model, "model", ""))
    return text_token_counter(name)


def _local_message_tokens(messages, count_text: TextTokenCounter | None = None, margin=1.0) -> int:
    """Count payloads, tool arguments and framing, without usage or artifacts.

    With no provider vocabulary for the model, keep the conservative
    one-character estimate rather than guessing an unrelated provider's
    tokenizer. No usage metadata from a previous request changes the count.
    """
    def text_tokens(value):
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return count_text(text) if count_text is not None else len(text)

    count = 0
    for message in messages:
        count += 8 + text_tokens(message.type)
        if message.name:
            count += text_tokens(message.name)
        # Counting serialized multimodal blocks also accounts for embedded
        # data. Remote images have no bounded size available locally, so do not
        # silently count only their URL as if it represented the image tokens.
        blocks = message.content if isinstance(message.content, list) else [message.content]
        for block in blocks:
            if isinstance(block, dict) and block.get("type") in {"image", "image_url"}:
                raise ValueError("Image token budgeting requires an explicit image-size policy")
            count += text_tokens(block)
        if isinstance(message, AIMessage):
            if message.tool_calls:
                count += text_tokens(message.tool_calls)
                # Only tool-call steps replay their reasoning
                # (inject_tool_call_reasoning); final answers are sent as text.
                reasoning = reasoning_text_from_mapping(message.additional_kwargs or {})
                if reasoning:
                    count += text_tokens(reasoning)
            if message.invalid_tool_calls:
                count += text_tokens(message.invalid_tool_calls)
        if isinstance(message, ToolMessage):
            count += text_tokens(message.tool_call_id)
    return math.ceil(count * margin) if count else 0


def _builtin_counter_may_fetch(model, method_name):
    method = getattr(type(model), method_name, None)
    module = getattr(method, "__module__", "")
    if module.startswith(("langchain_openai.", "langchain_anthropic.")):
        return True
    return (method is getattr(BaseLanguageModel, method_name, None)
            and not getattr(model, "custom_get_token_ids", None))


def count_context_tokens(model: BaseChatModel, messages: Sequence[BaseMessage]) -> int:
    """Count the current request locally, independent of stale usage metadata."""
    if not messages:
        return 0
    # Provider SDK counters may fetch a tokenizer or call a remote counting
    # endpoint. Use the bundled provider vocabularies instead.
    if not _builtin_counter_may_fetch(model, "get_num_tokens_from_messages"):
        try:
            count = int(model.get_num_tokens_from_messages(list(messages)))
            if count <= 0:
                raise ValueError("The message token counter returned a non-positive count")
            return count
        except (NotImplementedError, ImportError):
            pass
    count_text = _model_text_counter(model)
    return _local_message_tokens(messages, count_text, _FRAMING_MARGIN if count_text else 1.0)


_MEDIA_BLOCKS = frozenset({"image", "image_url", "file", "audio", "video"})
MEDIA_BLOCK_TOKENS = 2_000  # fixed charge per inline media block, never its base64 length


def count_conversation_tokens(model: BaseChatModel, messages: Sequence[BaseMessage]) -> int:
    """Count a conversation as a worker budget does: no system prompt, a flat charge per media block."""
    counted, media = [], 0
    for message in messages:
        if isinstance(message, SystemMessage):
            continue
        if isinstance(message.content, list):
            kept = [block for block in message.content
                    if not (isinstance(block, dict) and block.get("type") in _MEDIA_BLOCKS)]
            media += len(message.content) - len(kept)
            message = message.model_copy(update={"content": kept})
        counted.append(message)
    return (count_context_tokens(model, counted) if counted else 0) + media * MEDIA_BLOCK_TOKENS


def count_project_summary_tokens(model: BaseChatModel, summary: str) -> int:
    """Count summary tokens with the active model, with a conservative fallback."""
    text = summary.strip()
    if not text:
        return 0
    if not _builtin_counter_may_fetch(model, "get_num_tokens"):
        try:
            count = int(model.get_num_tokens(text))
            if count <= 0:
                raise ValueError("The text token counter returned a non-positive count")
            return count
        except (NotImplementedError, ImportError):
            pass
    count_text = _model_text_counter(model)
    return _local_message_tokens([HumanMessage(content=text)], count_text,
                                 _FRAMING_MARGIN if count_text else 1.0)


def _tool_context(tool: BaseTool | Any) -> str:
    """Render a tool as the function schema a request sends, not its Python model."""
    try:
        return json.dumps(convert_to_openai_tool(tool), ensure_ascii=False, default=str)
    except Exception:
        # A tool that cannot be converted cannot be bound either; count its
        # description and full Python schema rather than nothing.
        pass
    if isinstance(tool, dict):
        return repr(tool)
    name = getattr(tool, "name", getattr(tool, "__name__", type(tool).__name__))
    description = getattr(tool, "description", getattr(tool, "__doc__", "")) or ""
    args_schema = getattr(tool, "args_schema", None)
    schema = ""
    if args_schema is not None and hasattr(args_schema, "model_json_schema"):
        try:
            schema = repr(args_schema.model_json_schema())
        except Exception:
            schema = repr(args_schema)
    return f"{name}\n{description}\n{schema}"


def resolve_context_budget(
    *,
    model: BaseChatModel,
    endpoint: LLMEndpoint,
    system_prompt: str,
    tools: Sequence[BaseTool | Any],
    config: RuntimeContextConfig,
) -> ContextBudget:
    """Resolve conservative absolute thresholds for LangChain middleware."""
    config.validate()
    capacities = [
        capacity
        for capacity in (endpoint.max_input_tokens, model_max_input_tokens(model))
        if capacity is not None
    ] or [default_max_input_tokens()]

    max_input = min(capacities)
    total = min(config.total_context_tokens, max_input)
    static_messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content="\n\n".join(_tool_context(tool) for tool in tools)),
    ]
    static_tokens = count_context_tokens(model, static_messages)
    # Preserve bucket proportions when a model declares a smaller window. The
    # output limit remains an independent hard floor and is never scaled up.
    capacity_scale = total / config.total_context_tokens
    reserved = max(
        endpoint.resolved_max_tokens(), int(config.safety_margin_tokens * capacity_scale)
    )
    available = total - reserved - static_tokens
    requested = [
        config.project_summary_max_tokens,
        config.summary_model_max_tokens,
        config.active_context_tokens,
        config.memory_context_tokens,
    ]
    ratio = min(capacity_scale, available / sum(requested))
    project_summary, session_summary, active, memory = [
        int(value * ratio) for value in requested
    ]
    if active < 8_000:
        raise ValueError(
            "The context budget is too small after reserving output, "
            "system/tool, project-summary, compressed-history, and memory tokens. "
            "Increase max_input_tokens "
            "or lower the configured reserves/output limit."
        )

    # These are soft operating targets inside the working allocation, not
    # extra reserves and not replacements for the independent output reserve.
    tool_clear = max(2_000, active // 2)
    summary_trigger = max(2_000, active * 3 // 4)
    summary_keep = max(2_000, active * 3 // 8)
    summary_source = active + session_summary + memory
    return ContextBudget(
        max_input_tokens=max_input,
        static_context_tokens=static_tokens,
        active_context_tokens=active,
        tool_clear_trigger_tokens=tool_clear,
        summary_trigger_tokens=summary_trigger,
        summary_keep_tokens=summary_keep,
        summary_source_tokens=summary_source,
        total_context_tokens=total,
        project_summary_max_tokens=project_summary,
        summary_model_max_tokens=session_summary,
        memory_context_tokens=memory,
        reserved_context_tokens=reserved,
    )


def resolve_worker_context_budget(
    *,
    model: BaseChatModel,
    endpoint: LLMEndpoint,
    system_prompt: str,
    tools: Sequence[BaseTool | Any],
    config: RuntimeContextConfig,
) -> WorkerContextBudget:
    """Size a disposable worker's single context pool from the endpoint window.

    Everything after the output reserve and the static prompt/tool schemas is
    usable conversation. Compaction starts at ``worker_compact_fraction`` of it
    and keeps about a quarter verbatim. The in-context summary may take a sixth;
    the summary model may write twice that, so completed results that do not fit
    can be moved to a file instead of dropped.
    """
    config.validate()
    capacities = [
        capacity
        for capacity in (endpoint.max_input_tokens, model_max_input_tokens(model))
        if capacity is not None
    ] or [default_max_input_tokens()]
    max_input = min(capacities)
    total = min(config.total_context_tokens, max_input)
    static_tokens = count_context_tokens(model, [
        SystemMessage(content=system_prompt),
        HumanMessage(content="\n\n".join(_tool_context(tool) for tool in tools)),
    ])
    reserved = max(endpoint.resolved_max_tokens(),
                   int(config.safety_margin_tokens * total / config.total_context_tokens))
    usable = total - reserved - static_tokens
    if usable < MIN_WORKER_USABLE_TOKENS:
        raise ValueError(
            f"The worker context budget is too small: {usable} tokens remain after reserving "
            f"{reserved} for output and {static_tokens} for the system prompt and tool schemas. "
            "Increase max_input_tokens or lower the output limit."
        )
    summary_max = max(4_096, usable // 6)
    summary_output = 2 * summary_max
    return WorkerContextBudget(
        max_input_tokens=max_input,
        total_context_tokens=total,
        static_context_tokens=static_tokens,
        reserved_context_tokens=reserved,
        usable_tokens=usable,
        compact_trigger_tokens=int(usable * config.worker_compact_fraction),
        compact_keep_tokens=usable // 4,
        summary_max_tokens=summary_max,
        summary_output_tokens=summary_output,
        # The summary request carries only its prompt, so it has the window minus its
        # output, including the reasoning allowance a floor-reasoning model may spend.
        summary_input_tokens=max(1, total - summary_output - DISABLED_REASONING_ALLOWANCE_TOKENS - 1_024),
    )
