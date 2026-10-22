"""Classify endpoints by the wire protocol their host actually speaks."""

from __future__ import annotations

from core.llm.config import LLMEndpoint


def is_official_openai_url(base_url: str) -> bool:
    """True when ``base_url`` is OpenAI official API or Azure OpenAI (same wire spec).

    Stock ``ChatOpenAI`` already understands OpenAI/Azure native reasoning fields.
    Other OpenAI-compatible gateways (DeepSeek, OpenRouter, vLLM, …) often put
    reasoning in non-standard ``reasoning_content`` / ``reasoning`` that the stock
    client drops — those need ``ChatOpenAIWithReasoning``.
    """
    base = base_url.strip().lower()
    if "://" in base:
        base = base.split("://", 1)[1]
    host = base.split("/", 1)[0]
    if host == "api.openai.com" or host.endswith(".openai.com"):
        return True
    # Azure OpenAI / Azure AI Services — OpenAI-spec hosted by Microsoft.
    if host.endswith(".openai.azure.com"):
        return True
    if host.endswith(".cognitiveservices.azure.com"):
        return True
    if host.endswith(".services.ai.azure.com"):
        return True
    return False


def is_deepseek_compatible(endpoint: LLMEndpoint) -> bool:
    """True when the endpoint talks to DeepSeek (native or OpenAI-compatible)."""
    provider = endpoint.provider.strip().lower()
    if provider == "deepseek":
        return True
    base = endpoint.base_url.strip().lower()
    model = endpoint.model.strip().lower()
    return "deepseek.com" in base or "deepseek" in model
