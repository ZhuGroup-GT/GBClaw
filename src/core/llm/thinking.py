"""Provider-agnostic thinking settings read from an ``LLMEndpoint``."""

from __future__ import annotations

from dataclasses import dataclass

from core.llm.config import LLMEndpoint


@dataclass(frozen=True)
class ThinkingSettings:
    """Normalized thinking config (independent of .key / provider field names)."""

    enabled: bool
    effort: str | None  # canonical: low | medium | high | xhigh | max | None


def thinking_settings_from_endpoint(endpoint: LLMEndpoint) -> ThinkingSettings:
    """Map ``LLMEndpoint`` (.key canonical fields) → normalized thinking settings.

    If ``.key`` later renames fields, prefer adding aliases in ``core.llm.config`` so
    ``LLMEndpoint.thinking`` / ``reasoning_effort`` stay stable; this function
    is the single place that interprets those endpoint attributes.
    """
    enabled = str(endpoint.thinking).strip().lower() == "enabled"
    effort = endpoint.reasoning_effort
    if effort is not None:
        effort = str(effort).strip().lower() or None
    if not enabled:
        effort = None
    return ThinkingSettings(enabled=enabled, effort=effort)
