"""Shared protection for unresolved choices in structured tool results.

The JSON predicate belongs to the result contract, independent of Agent
middleware, summary prompts, or storage. Callers must preserve the complete
result when it requires user input; prose and internal conversation traces are
not interpreted as current decisions.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage


_OPAQUE_PROCESS_KEYS = frozenset({"trace", "messages", "history", "reasoning", "reasoning_content"})
_PENDING_FLAGS = frozenset({
    "approval_required", "requires_user_decision", "awaiting_user_selection", "awaiting_user",
})


def payload_requires_user(payload: Any) -> bool:
    """Inspect an acyclic JSON tree for explicit unresolved-choice markers.

    Visit every container so large scientific arrays neither masquerade as
    pending decisions nor hide a decision after an arbitrary scan limit.
    """
    pending = [payload]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if any(value.get(key) is True for key in _PENDING_FLAGS):
                return True
            status = value.get("status")
            if value.get("draft_id") and status in ("pending", "pending_approval"):
                return True
            if isinstance(status, str) and status in {
                "awaiting_approval", "awaiting_selection", "pending_user_decision", "awaiting_user",
            }:
                return True
            pending.extend(item for key, item in value.items()
                           if key not in _OPAQUE_PROCESS_KEYS and isinstance(item, (dict, list)))
        elif isinstance(value, list):
            pending.extend(item for item in value if isinstance(item, (dict, list)))
    return False


def result_requires_user(message: BaseMessage) -> bool:
    """Adapt a JSON ToolMessage without treating ordinary dialogue as evidence.

    Content-block callers can normalize their structured content explicitly or
    call ``payload_requires_user`` after parsing it.
    """
    from langchain_core.messages import ToolMessage

    if not isinstance(message, ToolMessage) or not isinstance(message.content, str):
        return False
    try:
        payload = json.loads(message.content)
    except (ValueError, TypeError):
        return False
    return payload_requires_user(payload)
