"""Repair LangGraph checkpoint message lists for provider API rules."""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.graph.state import CompiledStateGraph

_REMOVE_ALL_TEXT = str(REMOVE_ALL_MESSAGES)
_INTERRUPTED_TOOL_MSG = (
    "Tool run was interrupted or failed before a result was recorded. "
    "Do not assume this tool succeeded."
)
_TOOL_RESULTS_ACK = "Tool results received; continuing the workflow."


def _human_text(message: HumanMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content.strip()
    return str(content).strip()


def _is_remove_all_sentinel(message: BaseMessage) -> bool:
    return isinstance(message, HumanMessage) and _human_text(message) == _REMOVE_ALL_TEXT


def _strip_remove_all_sentinels(messages: list[BaseMessage]) -> list[BaseMessage]:
    return [m for m in messages if not _is_remove_all_sentinel(m)]


def _pop_incomplete_assistant_turn(messages: list[BaseMessage]) -> None:
    while messages:
        tail = messages[-1]
        if isinstance(tail, ToolMessage):
            messages.pop()
            continue
        if isinstance(tail, AIMessage) and tail.tool_calls:
            messages.pop()
            return
        break


def _sanitize_tool_turns(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Drop orphan ToolMessages and incomplete assistant tool-call turns."""
    repaired: list[BaseMessage] = []
    pending: dict[str, str] = {}

    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            if pending:
                _pop_incomplete_assistant_turn(repaired)
                pending = {}
            pending = {
                tc["id"]: tc.get("name") or "tool"
                for tc in msg.tool_calls
                if tc.get("id")
            }
            repaired.append(msg)
            continue

        if isinstance(msg, ToolMessage):
            tool_id = msg.tool_call_id
            if tool_id and tool_id in pending:
                repaired.append(msg)
                pending.pop(tool_id, None)
            continue

        if pending:
            _pop_incomplete_assistant_turn(repaired)
            pending = {}
        repaired.append(msg)

    if pending:
        _pop_incomplete_assistant_turn(repaired)

    return repaired


def _ensure_assistant_after_tool_responses(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Insert a placeholder assistant turn when user messages follow tool results.

    OpenAI-compatible APIs reject a HumanMessage that immediately follows ToolMessage(s)
    without an intervening assistant message. This happens when a run ends right after
    a tool returns (e.g. server crash) and the user sends the next prompt.
    """
    if not messages:
        return messages

    repaired: list[BaseMessage] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if isinstance(msg, ToolMessage):
            block_end = i
            while block_end + 1 < len(messages) and isinstance(messages[block_end + 1], ToolMessage):
                block_end += 1
            for j in range(i, block_end + 1):
                repaired.append(messages[j])
            next_idx = block_end + 1
            if next_idx < len(messages) and isinstance(messages[next_idx], HumanMessage):
                repaired.append(AIMessage(content=_TOOL_RESULTS_ACK))
            i = block_end + 1
            continue
        repaired.append(msg)
        i += 1
    return repaired


def _dedupe_consecutive_human_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    if not messages:
        return messages

    deduped: list[BaseMessage] = []
    for msg in messages:
        if (
            deduped
            and isinstance(msg, HumanMessage)
            and isinstance(deduped[-1], HumanMessage)
            and _human_text(msg) == _human_text(deduped[-1])
        ):
            continue
        deduped.append(msg)
    return deduped


def repair_message_history(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Normalize message history so LLM providers accept the next request."""
    if not messages:
        return messages

    cleaned = _strip_remove_all_sentinels(messages)
    cleaned = _sanitize_tool_turns(cleaned)
    cleaned = _ensure_assistant_after_tool_responses(cleaned)
    return _dedupe_consecutive_human_messages(cleaned)


def history_needs_repair(messages: list[BaseMessage]) -> bool:
    repaired = repair_message_history(messages)
    if len(repaired) != len(messages):
        return True
    return any(a is not b for a, b in zip(repaired, messages))


def repair_checkpoint(
    graph: CompiledStateGraph,
    config: dict,
    messages: list[BaseMessage] | None = None,
) -> bool:
    """Persist a repaired message list, replacing (not appending) checkpoint state."""
    if messages is None:
        try:
            state = graph.get_state(config)
        except Exception:
            return False
        if not state or not state.values:
            return False
        messages = list(state.values.get("messages") or [])

    if not messages:
        return False

    repaired = repair_message_history(messages)
    if not history_needs_repair(messages) and len(repaired) == len(messages):
        return False

    try:
        graph.update_state(
            config,
            {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *repaired]},
        )
    except Exception:
        # A checkpoint interrupted mid tool-call (e.g. a worker/tool crashed the
        # server before its ToolMessage was written) can leave pending writes that
        # make update_state fail during route re-evaluation (KeyError: 'model').
        # Delete corrupt execution state so the next request can start cleanly.
        # ChatRepository remains the complete transcript source of truth; semantic
        # memory is compact reusable context, not a checkpoint or transcript copy.
        try:
            graph.checkpointer.delete_thread(config)  # type: ignore[union-attr]
        except Exception:
            pass
        return False
    return True
