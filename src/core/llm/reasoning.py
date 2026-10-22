"""Normalize visible text and provider reasoning summaries from chat messages."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

# Where DeepSeek/OpenAI-compatible providers put reasoning in a message.
REASONING_KEYS = ("reasoning_content", "reasoning", "thinking", "reasoning_details")
# Content blocks that stand for reasoning even when they carry no text.
_REASONING_BLOCK_TYPES = frozenset({"reasoning", "redacted_thinking", "thinking", "reasoning_content"})


def _summary_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_summary_text(item) for item in value)
    if not isinstance(value, dict):
        return ""
    for key in ("text", "thinking", "reasoning", "content", "delta"):
        text = value.get(key)
        if isinstance(text, str) and text:
            return text
    return _summary_text(value.get("summary"))


def reasoning_text_from_mapping(value: Any) -> str:
    """Extract disclosed reasoning text from provider-specific response shapes."""
    if not isinstance(value, dict):
        return ""
    for key in ("reasoning_content", "thinking", "reasoning_details"):
        item = value.get(key)
        if isinstance(item, str) and item:
            return item
        text = _summary_text(item)
        if text:
            return text

    reasoning = value.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        return reasoning
    if isinstance(reasoning, dict):
        text = reasoning_text_from_mapping(reasoning) or _summary_text(
            reasoning.get("summary")
        )
        if text:
            return text
    if isinstance(reasoning, list):
        text = _summary_text(reasoning)
        if text:
            return text

    for key in ("summary", "details", "content"):
        text = _summary_text(value.get(key))
        if text:
            return text
    return ""


def split_message_content(content: Any) -> tuple[str, str]:
    """Return visible answer text and disclosed reasoning/summary text."""
    if content is None:
        return "", ""
    if isinstance(content, str):
        return content, ""

    visible: list[str] = []
    reasoning: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                visible.append(block)
                continue
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            if block_type in {
                "thinking",
                "thinking_delta",
                "reasoning",
                "reasoning_content",
                "reasoning_delta",
            }:
                text = reasoning_text_from_mapping(block)
                if text:
                    reasoning.append(text)
                continue
            if block_type == "redacted_thinking":
                continue
            text = block.get("text") or block.get("content") or ""
            if isinstance(text, str) and text:
                visible.append(text)
    else:
        visible.append(str(content))
    return "".join(visible), "".join(reasoning)


def withheld_reasoning_tokens(message: AIMessage) -> int | None:
    """Reasoning tokens of a reply whose reasoning the provider did not disclose; None otherwise.

    A reasoning item with an empty summary (OpenAI at low effort, or a gateway
    that strips summaries) or a reasoning token count without any text counts
    as withheld. Zero means the provider reported no token count.
    """
    _, disclosed = split_message_content(message.content)
    if disclosed or reasoning_text_from_mapping(message.additional_kwargs or {}):
        return None
    details = (message.usage_metadata or {}).get("output_token_details") or {}
    tokens = details.get("reasoning")
    tokens = tokens if isinstance(tokens, int) and tokens > 0 else 0
    has_block = isinstance(message.content, list) and any(
        isinstance(block, dict) and block.get("type") in _REASONING_BLOCK_TYPES
        for block in message.content
    )
    return tokens if has_block or tokens else None


def withheld_reasoning_note(tokens: int) -> str:
    """One line for transcripts and terminals; never fabricated reasoning text."""
    if tokens > 0:
        return f"The model reasoned for {tokens} tokens, but the provider returned no summary."
    return "The model reasoned, but the provider returned no summary."


def reasoning_text_from_additional_kwargs(extra: Any) -> str:
    """Extract reasoning preserved in LangChain ``additional_kwargs``."""
    return reasoning_text_from_mapping(extra)


def extract_reasoning_from_message(message: Any) -> str | None:
    """Pull DeepSeek/OpenRouter-style reasoning off a raw SDK message object."""
    if message is None:
        return None
    if isinstance(message, dict):
        return reasoning_text_from_mapping(message) or None
    extracted: dict[str, Any] = {}
    for key in REASONING_KEYS:
        value = getattr(message, key, None)
        if value is not None:
            extracted[key] = value
    text = reasoning_text_from_mapping(extracted)
    if text:
        return text
    model_extra = getattr(message, "model_extra", None)
    if isinstance(model_extra, dict):
        text = reasoning_text_from_mapping(model_extra)
        if text:
            return text
    return None


def extract_reasoning_from_delta(delta: Any) -> str | None:
    """Pull reasoning from a streaming delta (dict or SDK object)."""
    if delta is None:
        return None
    if isinstance(delta, dict):
        return reasoning_text_from_mapping(delta) or None
    return extract_reasoning_from_message(delta)


def inject_tool_call_reasoning(
    model: BaseChatModel,
    input_: Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Replay disclosed reasoning required by DeepSeek-compatible tool loops."""
    wire_messages = payload.get("messages")
    if not isinstance(wire_messages, list):
        return payload
    source_messages = model._convert_input(input_).to_messages()  # noqa: SLF001
    if len(source_messages) != len(wire_messages):
        return payload
    for source, target in zip(source_messages, wire_messages):
        if not isinstance(source, AIMessage) or not source.tool_calls:
            continue
        if not isinstance(target, dict):
            continue
        reasoning = reasoning_text_from_mapping(source.additional_kwargs or {})
        if not reasoning:
            continue
        target.setdefault("reasoning_content", reasoning)
        if target.get("content") is None:
            target["content"] = ""
    return payload


def without_reasoning(message: AIMessage) -> AIMessage | None:
    """Return a copy without reasoning in ``additional_kwargs``, or None if it has none.

    Reasoning carried as content blocks is left alone: providers such as the
    OpenAI Responses API reject a tool call whose reasoning item is missing.
    """
    extra = message.additional_kwargs or {}
    if not any(key in extra for key in REASONING_KEYS):
        return None
    return message.model_copy(update={"additional_kwargs": {
        key: value for key, value in extra.items() if key not in REASONING_KEYS
    }})
