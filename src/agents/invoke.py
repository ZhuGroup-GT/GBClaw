"""Agent runtime helpers: send messages to a LangGraph agent and parse replies.

Provides invoke_agent, extract_reply, and build_run_config for API routes and the CLI.
Does not define an agent itself—only runs an already-built graph (e.g. the orchestrator).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph
from langsmith import traceable

from core.llm.generation import TRUNCATED_FINISH_REASONS, finish_reason
from core.llm.reasoning import (
    reasoning_text_from_additional_kwargs,
    split_message_content,
)



def build_run_config(
    *,
    thread_id: str,
    project_id: str | None = None,
    run_name: str = "gbclaw_chat",
    tags: Sequence[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = {"thread_id": thread_id, **(metadata or {})}
    if project_id:
        meta["project_id"] = project_id

    configurable: dict[str, Any] = {"thread_id": thread_id}
    if project_id:
        configurable["project_id"] = project_id

    return {
        "configurable": configurable,
        "run_name": run_name,
        "tags": list(tags or ["gbclaw"]),
        "metadata": meta,
    }


def worker_run_config(
    *,
    thread_id: str,
    project_id: str | None = None,
    chat_thread_id: str | None = None,
    recursion_limit: int | None = None,
) -> dict[str, Any]:
    """Config for a nested worker graph: its own thread plus the parent chat identity."""
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    if recursion_limit is not None:
        config["recursion_limit"] = max(1, int(recursion_limit))
    if project_id:
        config["configurable"]["project_id"] = project_id
        config["metadata"] = {"project_id": project_id}
    if chat_thread_id:
        config["configurable"]["chat_thread_id"] = chat_thread_id
        config.setdefault("metadata", {})["chat_thread_id"] = chat_thread_id
    return config


@traceable(name="gbclaw_invoke", run_type="chain")
def invoke_agent(
    graph: CompiledStateGraph,
    user_input: str,
    *,
    thread_id: str,
    project_id: str | None = None,
    run_name: str = "gbclaw_chat",
    tags: Sequence[str] | None = None,
    metadata: dict[str, Any] | None = None,
    context: Any | None = None,
) -> dict[str, Any]:
    """Invoke an agent graph and record the run in LangSmith when tracing is enabled."""
    return graph.invoke(
        {"messages": [HumanMessage(content=user_input)]},
        config=build_run_config(
            thread_id=thread_id,
            project_id=project_id,
            run_name=run_name,
            tags=tags,
            metadata={"user_input": user_input, **(metadata or {})},
        ),
        context=context,
    )


def _message_has_tool_calls(message: AIMessage) -> bool:
    return bool(getattr(message, "tool_calls", None))


def _visible_and_thinking(message: AIMessage) -> tuple[str, str]:
    """Split an AIMessage into visible answer text and thinking/reasoning text."""
    visible, thinking = split_message_content(message.content)
    if not thinking:
        thinking = reasoning_text_from_additional_kwargs(
            getattr(message, "additional_kwargs", None) or {}
        )
    return visible.strip(), thinking.strip()


@dataclass(frozen=True)
class FinalModelReply:
    """Final worker/orchestrator answer attempt (last AIMessage without tool calls)."""

    text: str
    truncated: bool
    had_thinking: bool
    finish_reason: str


def extract_final_model_reply(result: dict[str, Any]) -> FinalModelReply | None:
    """Return the last AIMessage that is not a tool-call turn, or None if absent.

    Intermediate tool-call messages often carry short planner prose
    (e.g. \"Let me get the GB planes…\"). Those must not be treated as the
    worker's completed summary.
    """
    for message in reversed(result.get("messages", [])):
        if not isinstance(message, AIMessage):
            continue
        if _message_has_tool_calls(message):
            continue
        text, thinking = _visible_and_thinking(message)
        finish = finish_reason(message)
        return FinalModelReply(
            text=text,
            truncated=finish in TRUNCATED_FINISH_REASONS,
            had_thinking=bool(thinking),
            finish_reason=finish,
        )
    return None


def extract_reply(result: dict[str, Any]) -> str:
    """Return visible final-answer text, or \"\" when missing/unusable."""
    final = extract_final_model_reply(result)
    return final.text if final is not None else ""


def assess_worker_reply(
    result: dict[str, Any],
    *,
    agent_label: str,
) -> tuple[str | None, str | None]:
    """Validate a worker's final reply.

    Returns ``(reply, None)`` on success, or ``(partial_or_empty, error)`` on
    empty / thinking-only / truncated completions so callers can ``WorkerResult.fail``.
    """
    final = extract_final_model_reply(result)
    if final is None:
        return None, f"{agent_label} returned an empty reply."

    if final.truncated:
        detail = final.finish_reason or "length"
        if final.text:
            return (
                final.text,
                f"{agent_label} reply was truncated (finish_reason={detail}).",
            )
        if final.had_thinking:
            return (
                None,
                f"{agent_label} exhausted output tokens on thinking without a "
                f"final answer (finish_reason={detail}).",
            )
        return (
            None,
            f"{agent_label} reply was truncated (finish_reason={detail}).",
        )

    if not final.text:
        if final.had_thinking:
            return (
                None,
                f"{agent_label} produced thinking but no final answer "
                "(likely hit max_tokens during reasoning).",
            )
        return None, f"{agent_label} returned an empty reply."

    return final.text, None
