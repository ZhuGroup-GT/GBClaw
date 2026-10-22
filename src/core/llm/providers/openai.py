"""OpenAI (official, Azure or compatible gateway) chat models and reasoning controls."""

from __future__ import annotations

import base64
from functools import lru_cache
import re
from types import FunctionType
from typing import Any

from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI
import tiktoken
from tiktoken_ext import openai_public

from core.llm.config import LLMEndpoint
from core.llm.endpoints import is_deepseek_compatible, is_official_openai_url
from core.llm.providers.base import common_kwargs
from core.llm.providers.deepseek import apply_deepseek_thinking
from core.llm.providers.tokens import MemoizedCounter, TextTokenCounter, read_vocab
from core.llm.reasoning import (
    extract_reasoning_from_delta,
    extract_reasoning_from_message,
    inject_tool_call_reasoning,
)
from core.llm.thinking import ThinkingSettings, thinking_settings_from_endpoint

# Public GPT names, also when embedded in gateway deployment aliases.
_O200K_MODELS = re.compile(r"(?:^|[-_/])(?:gpt-4o|gpt-4\.[15]|gpt-[5-9](?:[.\-]|$)|o[134](?:[.\-]|$))")
_CL100K_MODELS = re.compile(r"(?:^|[-_/])gpt-(?:3\.5|4)(?:[.\-]|$)")


@lru_cache(maxsize=2)
def _bundled_counter(name: str) -> TextTokenCounter | None:
    """Count with a tiktoken encoding built from ``vocab/``, not tiktoken's download path.

    The copied constructor reads the bundled file and keeps tiktoken's own
    expected hash, without replacing any process-global function.
    """
    def load_bundled(blobpath, expected_hash=None):
        data = read_vocab(blobpath.rsplit("/", 1)[-1], expected_hash)
        if data is None:
            raise FileNotFoundError(blobpath)
        return {
            base64.b64decode(token): int(rank)
            for token, rank in (line.split() for line in data.splitlines() if line)
        }

    constructor = getattr(openai_public, name)
    local_constructor = FunctionType(
        constructor.__code__,
        {**constructor.__globals__, "load_tiktoken_bpe": load_bundled},
        constructor.__name__,
        constructor.__defaults__,
        constructor.__closure__,
    )
    try:
        encoding = tiktoken.Encoding(**local_constructor())
    except FileNotFoundError:
        return None
    return MemoizedCounter(lambda text: len(encoding.encode(text, disallowed_special=())))


def gpt_token_counter(model_name: str) -> TextTokenCounter | None:
    """Count text with the public vocabulary of a recognized GPT model."""
    name = model_name.lower()
    if _O200K_MODELS.search(name):
        return _bundled_counter("o200k_base")
    if _CL100K_MODELS.search(name):
        return _bundled_counter("cl100k_base")
    return None


def _openai_model_version(model: str) -> tuple[int, int] | None:
    """Return the GPT major/minor embedded in public or prefixed deployment IDs."""
    match = re.search(r"gpt[-_.]?([0-9]+)(?:[.-]([0-9]+))?", model.lower())
    if not match:
        return None
    minor = int(match.group(2) or 0)
    # A dated snapshot such as gpt-5-2025-08-07 carries a year, not a minor version.
    return int(match.group(1)), (minor if minor <= 99 else 0)


def _openai_effort(model: str, requested: str | None) -> str:
    """Map the common effort ladder to the nearest known OpenAI model level."""
    effort = requested or "high"
    version = _openai_model_version(model)
    normalized = model.lower()

    # GPT-5.6 introduced max. GPT-5.1–5.5 top out at xhigh, while the
    # original GPT-5 and o-series use high as their portable upper bound.
    if effort == "max":
        if version and version >= (5, 6):
            return "max"
        if version and version >= (5, 1):
            return "xhigh"
        if version == (5, 0) or re.search(r"(?:^|[-_/])o[134](?:[-_.]|$)", normalized):
            return "high"
    if effort == "xhigh" and (
        version == (5, 0)
        or re.search(r"(?:^|[-_/])o[134](?:[-_.]|$)", normalized)
    ):
        return "high"

    # Opaque enterprise deployment IDs cannot be classified reliably. Passing
    # the canonical value is preferable to silently weakening their model.
    return effort


def _openai_disabled_effort(model: str) -> str | None:
    """The lowest reasoning level a known generation accepts, for thinking=disabled.

    GPT-6 and the o-series reject ``none``/``minimal``; ``low`` is their floor.
    An unrecognized deployment name sends no level and keeps the model default.
    """
    version = _openai_model_version(model)
    if version and version >= (6, 0):
        return "low"
    if version and version >= (5, 1):
        return "none"
    if version == (5, 0):
        return "minimal"
    if re.search(r"(?:^|[-_/])o[134](?:[-_.]|$)", model.lower()):
        return "low"
    return None


def _apply_openai_native_thinking(
    kwargs: dict[str, Any],
    settings: ThinkingSettings,
    *,
    model: str,
    api_mode: str,
) -> dict[str, Any]:
    """Map thinking onto Chat Completions or Responses API parameters."""
    if api_mode == "responses":
        # LangChain normally removes temperature for recognized reasoning model
        # names. Enterprise deployments use opaque/prefixed names, so perform the
        # same compatibility normalization without relying on the public slug.
        kwargs.pop("temperature", None)
        kwargs["use_responses_api"] = True
        kwargs["output_version"] = "responses/v1"
        if settings.enabled:
            effort = _openai_effort(model, settings.effort)
            kwargs["reasoning"] = {"effort": effort, "summary": "auto"}
        elif effort := _openai_disabled_effort(model):
            # GPT-5.6 defaults to medium reasoning when omitted. Explicitly honor
            # GBclaw's provider-agnostic thinking=disabled setting with the level
            # this generation accepts as its minimum.
            kwargs["reasoning"] = {"effort": effort}
        return kwargs

    if not settings.enabled:
        if effort := _openai_disabled_effort(model):
            kwargs["reasoning_effort"] = effort
            if effort == "low":
                # The model still reasons at its floor, and reasoning models reject sampling controls.
                kwargs.pop("temperature", None)
        return kwargs
    kwargs.pop("temperature", None)
    effort = _openai_effort(model, settings.effort)
    kwargs["reasoning_effort"] = effort
    return kwargs


class ChatOpenAIWithReasoning(ChatOpenAI):
    """ChatOpenAI that preserves non-standard ``reasoning_content`` fields.

    Stock ``ChatOpenAI`` intentionally drops third-party fields such as DeepSeek's
    ``reasoning_content`` (see langchain_openai docs). For OpenAI-compatible
    gateways that are *not* api.openai.com, the API may still stream reasoning in
    those fields — this subclass copies them into
    ``additional_kwargs["reasoning_content"]`` the same way ``ChatDeepSeek`` does,
    so ``stream_events`` can emit thinking UI events.

    Official OpenAI endpoints keep using stock ``ChatOpenAI``, which already
    handles OpenAI's native reasoning surface.
    """

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        return inject_tool_call_reasoning(self, input_, payload)

    def _create_chat_result(
        self,
        response: dict | Any,
        generation_info: dict | None = None,
    ) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        choices = (
            response.get("choices")
            if isinstance(response, dict)
            else getattr(response, "choices", None)
        )
        if not choices:
            return result
        first = choices[0]
        raw_message = (
            first.get("message")
            if isinstance(first, dict)
            else getattr(first, "message", None)
        )
        reasoning = extract_reasoning_from_message(raw_message)
        if reasoning and result.generations:
            result.generations[0].message.additional_kwargs["reasoning_content"] = (
                reasoning
            )
        return result

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk,
            default_chunk_class,
            base_generation_info,
        )
        if not generation_chunk or not isinstance(
            generation_chunk.message, AIMessageChunk
        ):
            return generation_chunk
        choices = chunk.get("choices") if isinstance(chunk, dict) else None
        if not choices:
            return generation_chunk
        top = choices[0] if isinstance(choices[0], dict) else {}
        delta = top.get("delta", {}) if isinstance(top, dict) else {}
        reasoning = extract_reasoning_from_delta(delta)
        if reasoning:
            generation_chunk.message.additional_kwargs["reasoning_content"] = reasoning
        return generation_chunk


class OpenAIProvider:
    name = "openai"
    aliases = ("openai-compatible", "openai_compatible")

    def apply_thinking(self, endpoint: LLMEndpoint, kwargs: dict[str, Any]) -> dict[str, Any]:
        settings = thinking_settings_from_endpoint(endpoint)
        out = dict(kwargs)
        # Same DeepSeek API via ChatOpenAI → same thinking body as ChatDeepSeek.
        if is_deepseek_compatible(endpoint):
            if endpoint.api_mode == "responses":
                raise ValueError(
                    "DeepSeek-compatible OpenAI endpoints do not support "
                    "api_mode=responses; use api_mode=chat_completions."
                )
            return apply_deepseek_thinking(out, settings)
        return _apply_openai_native_thinking(
            out,
            settings,
            model=endpoint.model,
            api_mode=endpoint.api_mode,
        )

    def build(self, endpoint: LLMEndpoint) -> ChatOpenAI:
        llm_kwargs = self.apply_thinking(endpoint, common_kwargs(endpoint))
        # Non-OpenAI gateways often emit reasoning_content over the OpenAI-compatible
        # wire format; stock ChatOpenAI drops it. Official api.openai.com keeps the
        # stock client (native reasoning fields are already handled there).
        if not is_official_openai_url(endpoint.base_url):
            return ChatOpenAIWithReasoning(**llm_kwargs)
        return ChatOpenAI(**llm_kwargs)

    def token_counter(self, model_name: str) -> TextTokenCounter | None:
        return gpt_token_counter(model_name)

    def reasons_when_disabled(self, endpoint: LLMEndpoint) -> bool:
        return (not is_deepseek_compatible(endpoint)
                and _openai_disabled_effort(endpoint.model) == "low")
