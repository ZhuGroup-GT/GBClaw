"""Declarative specialist workers: one prompt, one tool set, one shared run lifecycle.

A worker module declares a ``WorkerSpec``; ``invoke`` builds a fresh graph per
task, streams its events to the delegate, and converts the final model reply
into a ``WorkerResult``. Workers with their own runtime (e.g. Coding's Deep
Agent) keep a custom invoker but reuse ``run_worker_graph``'s pieces.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
import uuid

from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from agents.invoke import assess_worker_reply, worker_run_config
from agents.graph import create_agent_graph
from agents.streaming.events import extract_tool_trace_from_messages, iter_worker_tool_events
from agents.workers.result import WorkerResult, worker_failure
from core.llm.config import LLMEndpoint
from extensions import resolve_hook, worker_definition, worker_tool_providers


@dataclass(frozen=True)
class WorkerRun:
    """The finished graph state of one worker invocation."""

    task: str
    messages: list[Any]
    trace: list[dict[str, Any]]
    reply: str
    fail_reason: str | None


def run_worker_graph(
    graph: CompiledStateGraph,
    task: str,
    *,
    agent_label: str,
    status_message: str,
    project_id: str | None = None,
    chat_thread_id: str | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    thread_id: str | None = None,
) -> WorkerRun:
    """Run *graph* on a fresh thread, forwarding events when a consumer is given."""
    thread_id = thread_id or str(uuid.uuid4())
    config = worker_run_config(thread_id=thread_id, project_id=project_id, chat_thread_id=chat_thread_id)
    if on_event is not None:
        for event in iter_worker_tool_events(
            graph,
            task,
            thread_id=thread_id,
            project_id=project_id,
            chat_thread_id=chat_thread_id,
            status_message=status_message,
            include_raw_tool_io=True,
        ):
            on_event(event)
        state = graph.get_state(config)
        messages = state.values.get("messages", []) if state.values else []
    else:
        messages = graph.invoke({"messages": [HumanMessage(content=task)]}, config=config).get("messages", [])
    reply, fail_reason = assess_worker_reply({"messages": messages}, agent_label=agent_label)
    return WorkerRun(task, messages, extract_tool_trace_from_messages(messages), reply, fail_reason)


def _saved_trace(graph: CompiledStateGraph | None, thread_id: str) -> list[dict[str, Any]]:
    """Tool calls a stopped worker had checkpointed; its in-memory saver ends with this call."""
    if graph is None:
        return []
    try:
        state = graph.get_state(worker_run_config(thread_id=thread_id))
    except Exception:
        return []
    return extract_tool_trace_from_messages((state.values or {}).get("messages", []))


def registered_worker_tools(agent: str) -> list[BaseTool]:
    """Business tools contributed by every registered provider for *agent*."""
    worker_definition(agent)  # an unknown agent key is an error, not an empty tool set
    tools: list[BaseTool] = []
    for provider in worker_tool_providers(agent):
        tools.extend(resolve_hook(provider.factory)())
    names = [getattr(tool, "name", None) for tool in tools]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"Tool providers for worker {agent!r} repeat tool names: {duplicates}")
    return tools


@dataclass(frozen=True)
class WorkerSpec:
    """A specialist's identity, system prompt and business tools.

    ``prompt`` may be a callable so a worker reloads domain knowledge per task.
    Business tools come from the registry's tool providers for ``agent``
    (``DomainExtension.tool_providers``) unless ``tools`` overrides them.
    """

    agent: str
    label: str
    status_message: str
    prompt: str | Callable[[], str]
    tools: Callable[[], Sequence[BaseTool]] | None = None

    def create_graph(
        self,
        endpoint: LLMEndpoint,
        *,
        tools: Sequence[BaseTool] | None = None,
        extra_tools: Sequence[BaseTool] | None = None,
        enable_tracing: bool | None = None,
    ) -> CompiledStateGraph:
        prompt = self.prompt() if callable(self.prompt) else self.prompt
        if tools is not None:
            business = list(tools)
        elif self.tools is not None:
            business = list(self.tools())
        else:
            business = registered_worker_tools(self.agent)
        return create_agent_graph(
            endpoint,
            tools=[*business, *(extra_tools or [])],
            system_prompt=prompt,
            enable_tracing=enable_tracing,
        )

    def invoke(
        self,
        endpoint: LLMEndpoint,
        task: str,
        *,
        project_id: str | None = None,
        chat_thread_id: str | None = None,
        tools: Sequence[BaseTool] | None = None,
        extra_tools: Sequence[BaseTool] | None = None,
        enable_tracing: bool | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        finalize: Callable[[WorkerRun], WorkerResult] | None = None,
    ) -> WorkerResult:
        """Run the worker statelessly and return a structured result.

        ``finalize`` turns a successful run into the result; by default the
        final reply becomes the summary with the full internal trace.
        """
        task = task.strip()
        if not task:
            return WorkerResult.fail("Task description must not be empty.")
        graph, thread_id = None, str(uuid.uuid4())
        try:
            graph = self.create_graph(endpoint, tools=tools, extra_tools=extra_tools,
                                      enable_tracing=enable_tracing)
            run = run_worker_graph(
                graph, task, agent_label=self.label, status_message=self.status_message,
                project_id=project_id, chat_thread_id=chat_thread_id, on_event=on_event,
                thread_id=thread_id,
            )
            if run.fail_reason:
                return WorkerResult.fail(run.fail_reason, summary=run.reply or run.fail_reason, trace=run.trace)
            if finalize is not None:
                return finalize(run)
            return WorkerResult.ok(summary=run.reply or "", task=task, trace=run.trace)
        except Exception as exc:
            # Finished tool calls stay in the trace, so completed runs are verified (partial).
            error, data = worker_failure(exc)
            return WorkerResult.fail(error, summary=f"{self.label} failed.",
                                     trace=_saved_trace(graph, thread_id), **data)


__all__ = ["WorkerRun", "WorkerSpec", "registered_worker_tools", "run_worker_graph"]
