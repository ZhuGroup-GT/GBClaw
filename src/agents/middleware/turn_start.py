"""Checkpoint cleanup when a new user request starts an orchestrator turn."""
from __future__ import annotations
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from core.llm.reasoning import without_reasoning
from .projection import _is_session_summary


def _is_request(message) -> bool:
    return isinstance(message, HumanMessage) and not _is_session_summary(message)


def turn_start_updates(messages) -> list:
    """Return the message updates that leave completed turns as answers only.

    Reasoning is shown to the user and archived in the chat history; only a
    tool loop still in progress replays it to the provider. Once a new request
    arrives, earlier turns are complete, so their reasoning leaves the
    checkpoint. A request that paused or failed before any reply stays in the
    checkpoint; when the user sends the same text again, the earlier copies
    are removed so the model sees the request once. Updates keep message IDs,
    so the reducer replaces or removes messages in place.
    """
    if not messages or not _is_request(messages[-1]):
        return []
    request = messages[-1].text.strip()
    updates = []
    index = len(messages) - 2
    while index >= 0 and _is_request(messages[index]):
        if messages[index].id and messages[index].text.strip() == request:
            updates.append(RemoveMessage(id=messages[index].id))
        index -= 1
    for message in messages[:-1]:
        stripped = without_reasoning(message) if isinstance(message, AIMessage) and message.id else None
        if stripped is not None:
            updates.append(stripped)
    return updates


class TurnStartCleanupMiddleware(AgentMiddleware):
    """Apply ``turn_start_updates`` once per request, before the first model call."""

    def before_agent(self, state, runtime):
        updates = turn_start_updates(state["messages"])
        return {"messages": updates} if updates else None

    async def abefore_agent(self, state, runtime):
        return self.before_agent(state, runtime)
