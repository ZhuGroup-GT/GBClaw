"""Classify how a chat-model response ended, across provider metadata shapes."""

from __future__ import annotations

from typing import Any

# Chat Completions, Anthropic and Responses-API ways of saying "output was cut off".
TRUNCATED_FINISH_REASONS = frozenset({
    "length", "max_tokens", "max_length", "model_context_window_exceeded", "incomplete",
})


def finish_reason(message: Any) -> str:
    """Normalized finish/stop reason; ``"incomplete"`` for an incomplete Responses-API run."""
    metadata = getattr(message, "response_metadata", None) or {}
    extra = getattr(message, "additional_kwargs", None) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    if not isinstance(extra, dict):
        extra = {}
    if (metadata.get("status") == "incomplete" or extra.get("status") == "incomplete"
            or metadata.get("incomplete_details") or extra.get("incomplete_details")):
        return "incomplete"
    for key in ("finish_reason", "stop_reason"):
        value = metadata.get(key)
        if value:
            return str(value).strip().lower()
    return ""


def is_truncated(message: Any) -> bool:
    return finish_reason(message) in TRUNCATED_FINISH_REASONS


def response_text(message: Any) -> str:
    """Visible text of a response whose content may be a list of blocks."""
    content = message.content
    if isinstance(content, list):
        content = "".join(
            str(block.get("text") or "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content or "").strip()
