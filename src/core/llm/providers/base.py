"""The contract a chat-model provider implements to be selectable from ``.key``."""

from __future__ import annotations

from typing import Any, Protocol

from langchain_core.language_models import BaseChatModel

from core.llm.config import LLMEndpoint


class ProviderAdapter(Protocol):
    """Adapt GBclaw's provider-agnostic endpoint settings onto one client SDK.

    ``name`` and ``aliases`` are the ``provider`` values accepted in ``.key``.
    ``apply_thinking`` maps ``thinking`` / ``reasoning_effort`` onto client
    constructor arguments; ``build`` returns the configured chat model.

    An adapter may also define ``token_counter(model_name)``, returning a
    function that counts text tokens exactly for the model family it
    recognizes by name, or None. Context budgets ask every registered adapter;
    without a match they count one token per character.

    ``reasons_when_disabled(endpoint)`` may report that ``thinking=disabled``
    still leaves the model reasoning at its lowest level (GPT-6, the o-series,
    always-adaptive Claude). Host-side auxiliary calls then widen their output
    cap so reasoning tokens cannot truncate the answer.
    """

    name: str
    aliases: tuple[str, ...]

    def apply_thinking(self, endpoint: LLMEndpoint, kwargs: dict[str, Any]) -> dict[str, Any]: ...

    def build(self, endpoint: LLMEndpoint) -> BaseChatModel: ...


def common_kwargs(endpoint: LLMEndpoint) -> dict[str, Any]:
    """Constructor arguments shared by every LangChain chat client."""
    return {
        "model": endpoint.model,
        "api_key": endpoint.api_key,
        "base_url": endpoint.base_url,
        "temperature": endpoint.temperature,
        "max_tokens": endpoint.resolved_max_tokens(),
        "timeout": endpoint.timeout,
    }
