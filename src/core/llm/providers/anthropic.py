"""Anthropic Claude chat models and their manual/adaptive thinking generations."""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.language_models import BaseChatModel

from core.llm.config import LLMEndpoint
from core.llm.providers.base import common_kwargs
from core.llm.thinking import ThinkingSettings, thinking_settings_from_endpoint

LOGGER = logging.getLogger(__name__)

_ANTHROPIC_MANUAL_BUDGET_RATIOS = {
    "low": 0.25,
    "medium": 0.50,
    "high": 0.75,
    "xhigh": 0.85,
    "max": 0.875,
}
_ANTHROPIC_MIN_THINKING_TOKENS = 1_024
_ANTHROPIC_MIN_VISIBLE_TOKENS = 1_024


def _anthropic_model_version(model: str) -> tuple[int, int] | None:
    normalized = model.lower().replace(".", "-")
    match = re.search(r"claude-(?:[a-z]+-)*(\d+)(?:-(\d+))?", normalized)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2) or 0)


def _anthropic_model_generation(model: str) -> str:
    """Classify Claude thinking syntax without adding provider-specific config."""
    normalized = model.lower().replace(".", "-")
    if "fable-5" in normalized or "mythos" in normalized:
        return "always_adaptive"
    version = _anthropic_model_version(normalized)
    if version and version >= (4, 6):
        return "adaptive"
    return "manual"


def _anthropic_supports_xhigh(model: str) -> bool:
    normalized = model.lower().replace(".", "-")
    version = _anthropic_model_version(normalized)
    return bool(
        "fable-5" in normalized
        or "mythos" in normalized
        or (version and version >= (4, 7))
    )


def _anthropic_effort(model: str, requested: str | None) -> str:
    effort = requested or "high"
    if effort == "xhigh" and not _anthropic_supports_xhigh(model):
        return "max" if _anthropic_model_generation(model) == "adaptive" else "high"
    if effort == "max" and _anthropic_model_generation(model) == "manual":
        return "high"
    return effort


def _anthropic_manual_budget(max_tokens: int, effort: str | None) -> int:
    """Derive Claude's legacy thinking budget from common ``max_tokens``."""
    if max_tokens <= _ANTHROPIC_MIN_THINKING_TOKENS:
        raise ValueError(
            "Anthropic manual thinking requires max_tokens > 1024 so its "
            "budget_tokens can leave room for the final answer."
        )
    ratio = _ANTHROPIC_MANUAL_BUDGET_RATIOS.get(effort or "high", 0.75)
    budget = max(_ANTHROPIC_MIN_THINKING_TOKENS, int(max_tokens * ratio))
    return min(budget, max_tokens - _ANTHROPIC_MIN_VISIBLE_TOKENS)


def _apply_anthropic_thinking(
    endpoint: LLMEndpoint,
    kwargs: dict[str, Any],
    settings: ThinkingSettings,
) -> dict[str, Any]:
    """Map common thinking controls to Claude manual/adaptive generations."""
    generation = _anthropic_model_generation(endpoint.model)

    if not settings.enabled:
        if generation == "always_adaptive":
            # Fable/Mythos cannot disable thinking. Keep it undisclosed and use
            # low effort, which is the closest valid behavior to disabled.
            kwargs.pop("temperature", None)
            kwargs["thinking"] = {"type": "adaptive", "display": "omitted"}
            kwargs["reasoning_effort"] = "low"
            LOGGER.warning(
                "Anthropic model %s does not support thinking=disabled; using "
                "adaptive thinking with omitted display and low effort.",
                endpoint.model,
            )
        else:
            kwargs["thinking"] = {"type": "disabled"}
            if generation == "adaptive":
                kwargs.pop("temperature", None)
        return kwargs

    kwargs.pop("temperature", None)
    effort = _anthropic_effort(endpoint.model, settings.effort)
    if generation in {"adaptive", "always_adaptive"}:
        kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
        kwargs["reasoning_effort"] = effort
        return kwargs

    kwargs["thinking"] = {
        "type": "enabled",
        "budget_tokens": _anthropic_manual_budget(
            int(kwargs["max_tokens"]), settings.effort
        ),
    }
    # Of the manual-thinking generation, only Opus 4.5 accepts effort. Other
    # models are controlled solely through the derived budget_tokens value.
    normalized = endpoint.model.lower().replace(".", "-")
    if "opus-4-5" in normalized:
        kwargs["reasoning_effort"] = effort
    return kwargs


class AnthropicProvider:
    name = "anthropic"
    aliases = ("claude",)

    def apply_thinking(self, endpoint: LLMEndpoint, kwargs: dict[str, Any]) -> dict[str, Any]:
        return _apply_anthropic_thinking(endpoint, dict(kwargs), thinking_settings_from_endpoint(endpoint))

    def reasons_when_disabled(self, endpoint: LLMEndpoint) -> bool:
        return _anthropic_model_generation(endpoint.model) == "always_adaptive"

    def build(self, endpoint: LLMEndpoint) -> BaseChatModel:
        if endpoint.api_mode == "responses":
            raise ValueError(
                "provider=anthropic uses the Messages API and does not support "
                "api_mode=responses; use api_mode=chat_completions (the common "
                "non-Responses setting)."
            )
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:
            raise ImportError(
                "Provider 'anthropic' requires the langchain-anthropic package. "
                "Install it in your environment or use an OpenAI-compatible gateway "
                "with provider = openai."
            ) from exc
        llm_kwargs = self.apply_thinking(endpoint, common_kwargs(endpoint))
        llm_kwargs["output_version"] = "v1"
        return ChatAnthropic(**llm_kwargs)
