"""DeepSeek chat models and the DeepSeek thinking wire format."""

from __future__ import annotations

from functools import lru_cache
import logging
import re
from typing import Any

from langchain_deepseek import ChatDeepSeek

from core.llm.config import LLMEndpoint
from core.llm.providers.base import common_kwargs
from core.llm.providers.tokens import MemoizedCounter, TextTokenCounter, read_vocab
from core.llm.reasoning import inject_tool_call_reasoning
from core.llm.thinking import ThinkingSettings, thinking_settings_from_endpoint

LOGGER = logging.getLogger(__name__)
_DEEPSEEK_MODELS = re.compile(r"(?:^|[-_/])deepseek(?:[-_.]|$)")
# DeepSeek-V3-0324's tokenizer.json; the API's reported input tokens match it exactly.
_DEEPSEEK_TOKENIZER_SHA256 = "621ac2e32d0dba658404412318818aaa8ce8cda492e59830109d8da6b517fb41"

_DEEPSEEK_EFFORT_MAP = {
    "low": "low",
    "medium": "high",
    "high": "high",
    "xhigh": "max",
    "max": "max",
}


def apply_deepseek_thinking(
    kwargs: dict[str, Any],
    settings: ThinkingSettings,
) -> dict[str, Any]:
    """DeepSeek wire format: ``extra_body.thinking.type`` + optional effort."""
    extra = dict(kwargs.get("extra_body") or {})
    extra["thinking"] = {"type": "enabled" if settings.enabled else "disabled"}
    kwargs["extra_body"] = extra
    if settings.enabled:
        kwargs.pop("temperature", None)
        kwargs["reasoning_effort"] = _DEEPSEEK_EFFORT_MAP.get(
            settings.effort or "high", "high"
        )
    return kwargs


@lru_cache(maxsize=1)
def _deepseek_counter() -> TextTokenCounter | None:
    try:
        from tokenizers import Tokenizer
    except ImportError:
        LOGGER.warning("The tokenizers package is not installed; counting DeepSeek text "
                       "as one token per character.")
        return None
    data = read_vocab("deepseek-v3.tokenizer.json", _DEEPSEEK_TOKENIZER_SHA256)
    if data is None:
        return None
    tokenizer = Tokenizer.from_str(data.decode("utf-8"))
    # The batch-fast path skips offset tracking; it gives the same count.
    return MemoizedCounter(lambda text: len(tokenizer.encode_batch_fast([text], add_special_tokens=False)[0]))


def deepseek_token_counter(model_name: str) -> TextTokenCounter | None:
    """Count text with the DeepSeek-V3 vocabulary for a recognized DeepSeek model."""
    return _deepseek_counter() if _DEEPSEEK_MODELS.search(model_name.lower()) else None


class ChatDeepSeekCompatible(ChatDeepSeek):
    """ChatDeepSeek with reasoning replay for thinking-mode tool calls."""

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        return inject_tool_call_reasoning(self, input_, payload)


class DeepSeekProvider:
    name = "deepseek"
    aliases: tuple[str, ...] = ()

    def apply_thinking(self, endpoint: LLMEndpoint, kwargs: dict[str, Any]) -> dict[str, Any]:
        return apply_deepseek_thinking(dict(kwargs), thinking_settings_from_endpoint(endpoint))

    def build(self, endpoint: LLMEndpoint) -> ChatDeepSeek:
        if endpoint.api_mode == "responses":
            raise ValueError(
                "provider=deepseek does not support api_mode=responses; "
                "use api_mode=chat_completions."
            )
        return ChatDeepSeekCompatible(**self.apply_thinking(endpoint, common_kwargs(endpoint)))

    def token_counter(self, model_name: str) -> TextTokenCounter | None:
        return deepseek_token_counter(model_name)
