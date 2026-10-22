"""Build provider-compatible LangChain chat models from ``LLMEndpoint``.

``.key`` exposes a stable, provider-agnostic thinking surface
(``thinking`` / ``reasoning_effort``). Each registered ``ProviderAdapter`` maps
that surface onto its client SDK so DeepSeek, OpenAI (official or compatible),
Anthropic and any added provider behave the same to the rest of the app.
"""

from __future__ import annotations

import threading
from dataclasses import replace

from langchain_core.language_models import BaseChatModel

from core.llm.config import DISABLED_REASONING_ALLOWANCE_TOKENS, LLMEndpoint
from core.llm.providers.anthropic import AnthropicProvider
from core.llm.providers.base import ProviderAdapter
from core.llm.providers.deepseek import DeepSeekProvider
from core.llm.providers.openai import OpenAIProvider
from core.llm.providers.tokens import TextTokenCounter

_lock = threading.Lock()
_PROVIDERS: dict[str, ProviderAdapter] = {}
_ALIASES: dict[str, str] = {}


def register_provider(adapter: ProviderAdapter, *, replace: bool = False) -> None:
    """Make ``adapter.name`` (and its aliases) selectable as a ``.key`` provider."""
    name = adapter.name.strip().lower()
    aliases = tuple(alias.strip().lower() for alias in adapter.aliases)
    with _lock:
        if replace and _PROVIDERS.pop(name, None) is not None:
            for alias in [alias for alias, target in _ALIASES.items() if target == name]:
                del _ALIASES[alias]
        taken = {name, *aliases} & {*_PROVIDERS, *_ALIASES}
        if taken:
            raise ValueError(f"LLM provider name already registered: {sorted(taken)}")
        _PROVIDERS[name] = adapter
        for alias in aliases:
            _ALIASES[alias] = name


def supported_providers() -> tuple[str, ...]:
    with _lock:
        return tuple(_PROVIDERS)


def provider_adapter(provider: str) -> ProviderAdapter:
    raw = provider.strip().lower()
    with _lock:
        adapter = _PROVIDERS.get(_ALIASES.get(raw, raw))
    if adapter is None:
        supported = ", ".join(supported_providers())
        raise ValueError(
            f"Unsupported provider '{provider}'. "
            f"Set provider to one of: {supported} in your .key model section."
        )
    return adapter


def build_llm(endpoint: LLMEndpoint) -> BaseChatModel:
    """Instantiate a chat model for the configured provider."""
    return provider_adapter(endpoint.provider).build(endpoint)


def reasons_when_disabled(endpoint: LLMEndpoint) -> bool:
    """Whether ``thinking=disabled`` still leaves this endpoint's model reasoning."""
    try:
        adapter = provider_adapter(endpoint.provider)
    except ValueError:
        return False
    check = getattr(adapter, "reasons_when_disabled", None)
    return bool(check(endpoint)) if check is not None else False


def auxiliary_endpoint(endpoint: LLMEndpoint, *, answer_tokens: int) -> LLMEndpoint:
    """Clone an endpoint for a host-side summary or extraction call.

    Thinking is turned off and the output cap sized for the answer. A model
    that keeps reasoning at its lowest level gets a fixed allowance on top,
    bounded by the endpoint's own output limit, so an answer-sized cap is not
    consumed by reasoning tokens.
    """
    answer = max(1, int(answer_tokens))
    allowance = DISABLED_REASONING_ALLOWANCE_TOKENS if reasons_when_disabled(endpoint) else 0
    cap = max(answer, endpoint.resolved_max_tokens())
    return replace(endpoint, thinking="disabled", reasoning_effort=None,
                   max_tokens=min(answer + allowance, cap))


def text_token_counter(model_name: str) -> TextTokenCounter | None:
    """Return the exact text counter of the first adapter that recognizes the model.

    Matching is by model name, not by the adapter that built the model, so a
    DeepSeek or GPT model behind an OpenAI-compatible gateway keeps its vocabulary.
    """
    with _lock:
        adapters = tuple(_PROVIDERS.values())
    for adapter in adapters:
        recognize = getattr(adapter, "token_counter", None)
        counter = recognize(model_name) if recognize is not None else None
        if counter is not None:
            return counter
    return None


for _adapter in (DeepSeekProvider(), OpenAIProvider(), AnthropicProvider()):
    register_provider(_adapter)


__all__ = [
    "ProviderAdapter", "build_llm", "provider_adapter", "register_provider", "supported_providers",
    "text_token_counter",
]
