"""Disposable Deep Agents worker for bounded general-purpose coding tasks."""

from __future__ import annotations

import threading
import queue
from contextvars import copy_context
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

from core.llm.factory import build_llm
from agents.invoke import assess_worker_reply, worker_run_config
from agents.memory.handoff import append_memory_handoff
from agents.streaming.events import (
    extract_tool_trace_from_messages,
    iter_worker_tool_events,
)
from agents.workers.lifecycle import worker_memory_inputs
from agents.workers.result import WorkerResult, worker_failure
from core.settings.langsmith import configure_langsmith
from core.settings.runtime_context import load_runtime_context_config, resolve_worker_context_budget
from agents.middleware.factory import worker_summary_endpoint
from domains.coding.middleware import CodingSummarizationMiddleware, StepBudgetMiddleware
from core.llm.config import LLMEndpoint
from agents.prompts import with_result_handoff_prompt
from domains.coding.prompt import CODING_AGENT_PROMPT
from project_memory.models import RunStatus
from domains.coding.run import CodingRunSession
from core.execution.sessions import is_session_cancelled
from agents.middleware.cancellation import ExecutionCancellationMiddleware
from agents.memory.projection import ModelToolOutputMiddleware


_PROFILE_LOCK = threading.Lock()
_PROFILE_KEYS: set[str] = set()
_MAX_RESULT_TRACE_RECORDS = 50


def _disable_nested_general_agent(endpoint: LLMEndpoint) -> None:
    """Keep one delegated coding worker from recursively creating more workers."""
    key = f"{endpoint.provider.strip().lower()}:{endpoint.model.strip()}"
    with _PROFILE_LOCK:
        if key in _PROFILE_KEYS:
            return
        register_harness_profile(
            key,
            HarnessProfile(
                general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)
            ),
        )
        _PROFILE_KEYS.add(key)


def create_coding_agent(
    endpoint: LLMEndpoint,
    *,
    session: CodingRunSession,
    extra_tools: Sequence[BaseTool] | None = None,
    enable_tracing: bool | None = None,
) -> CompiledStateGraph:
    extra_tools, memory_handoff = worker_memory_inputs("coding", extra_tools)
    if enable_tracing is not False:
        configure_langsmith()
    _disable_nested_general_agent(endpoint)
    tools = [*(extra_tools or []), session.make_request_inputs_tool(), session.make_publish_tool()]
    system_prompt = with_result_handoff_prompt(
        append_memory_handoff(CODING_AGENT_PROMPT, memory_handoff)
    )
    model = build_llm(endpoint)
    projects_root = session.service.project_root.parent
    projector = ModelToolOutputMiddleware(projects_root=projects_root)
    context_config = load_runtime_context_config()
    budget = summary_model = None
    if context_config.enabled:
        # Deep Agents adds its file tools to every request; their schemas are static context too.
        budget = resolve_worker_context_budget(
            model=model, endpoint=endpoint, system_prompt=system_prompt, config=context_config,
            tools=[*tools, *FilesystemMiddleware(backend=session.backend).tools],
        )
        summary_model = build_llm(worker_summary_endpoint(endpoint, budget))
    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=[
            ExecutionCancellationMiddleware(),
            CodingSummarizationMiddleware(model, backend=session.backend, projector=projector,
                                          budget=budget, summary_model=summary_model,
                                          projects_root=projects_root),
            projector,
            StepBudgetMiddleware(),
        ],
        subagents=[],
        memory=None,
        permissions=None,
        backend=session.backend,
        checkpointer=MemorySaver(),
        name="gbclaw-coding-worker",
    )


def _invoke_graph_bounded(endpoint, task, *, session, project_id, chat_thread_id,
                          extra_tools, enable_tracing, on_event):
    """Return at the task deadline; late model calls cannot mutate a closed sandbox."""
    pending = queue.Queue(maxsize=8)

    def put(kind, value):
        while True:
            session.task_budget.check()
            try:
                pending.put((kind, value), timeout=0.1)
                return
            except queue.Full:
                pass

    def produce():
        try:
            session.task_budget.check()
            graph = create_coding_agent(endpoint, session=session, extra_tools=extra_tools,
                enable_tracing=enable_tracing)
            worker_thread_id = str(uuid.uuid4())
            config = worker_run_config(thread_id=worker_thread_id, project_id=project_id,
                                       chat_thread_id=chat_thread_id,
                                       recursion_limit=session.config.recursion_limit)
            if on_event is not None:
                for event in iter_worker_tool_events(graph, task, thread_id=worker_thread_id,
                        project_id=project_id, chat_thread_id=chat_thread_id,
                        status_message="Coding in an isolated workspace…", include_raw_tool_io=True,
                        recursion_limit=session.config.recursion_limit):
                    put("event", event)
                state = graph.get_state(config)
                result = {"messages": state.values.get("messages", []) if state.values else []}
            else:
                result = graph.invoke({"messages": [HumanMessage(content=task)]}, config=config)
            put("result", result)
        except Exception as exc:
            try:
                put("error", exc)
            except (RuntimeError, TimeoutError):
                pass

    context = copy_context()
    threading.Thread(target=lambda: context.run(produce), daemon=True,
                     name=f"coding-{session.run_id}").start()
    while True:
        session.task_budget.check()
        if is_session_cancelled(session.backend.session_key):
            session.task_budget.cancel("Coding task was cancelled.")
            session.task_budget.check()
        try:
            kind, value = pending.get(timeout=min(0.1, session.task_budget.remaining()))
        except queue.Empty:
            continue
        if kind == "error":
            raise value
        if kind == "result":
            return value
        on_event(value)


def invoke_coding_agent(
    endpoint: LLMEndpoint,
    task: str,
    *,
    reported_task: str | None = None,
    session: CodingRunSession,
    project_id: str,
    chat_thread_id: str | None = None,
    extra_tools: Sequence[BaseTool] | None = None,
    enable_tracing: bool | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> WorkerResult:
    """Run one fresh worker graph; only the project run/artifacts survive."""
    normalized_task = task.strip()
    result_task = str(reported_task or normalized_task).strip()
    if not normalized_task:
        session.fail_if_running("Task description must not be empty.")
        session.cleanup_ephemeral()
        return WorkerResult.fail("Task description must not be empty.")

    try:
        result = _invoke_graph_bounded(endpoint, normalized_task, session=session,
            project_id=project_id, chat_thread_id=chat_thread_id, extra_tools=extra_tools,
            enable_tracing=enable_tracing, on_event=on_event)

        messages = result.get("messages", [])
        reply, fail_reason = assess_worker_reply(result, agent_label="Coding agent")
        run = session.service.memory.get_run(session.run_id)
        # Completion checks parse each tool's exact arguments and reply, so the
        # records stay whole; WorkerResult.to_json drops them before the parent model.
        trace = extract_tool_trace_from_messages(messages)[-_MAX_RESULT_TRACE_RECORDS:]
        if run.status is RunStatus.COMPLETED:
            durable_summary = str(run.result.get("summary") or reply or "").strip()
            tool_result = session.tool_result()
            return WorkerResult.ok(
                summary=durable_summary,
                task=result_task,
                run=tool_result.model_dump(mode="json"),
                trace=trace,
            )

        cancelled = is_session_cancelled(session.backend.session_key)
        reason = (
            "Coding task was cancelled."
            if cancelled
            else "Additional inputs require expanded delegate scope."
            if run.progress.get("missing_input_scope")
            else fail_reason
            or "Coding agent exited without calling publish_coding_result successfully."
        )
        if not cancelled and not run.progress.get("missing_input_scope"):
            salvaged, reason = _salvage(session, reason, result_task)
            if salvaged is not None:
                return salvaged
        session.fail_if_running(reason, cancelled=cancelled)
        return WorkerResult.fail(reason, summary=reply or reason,
            missing_input_scope=run.progress.get("missing_input_scope", []),
            run=session.tool_result().model_dump(mode="json"))
    except Exception as exc:
        # A context failure carries host diagnostics instead of the provider's text.
        error, context = worker_failure(exc)
        run = session.service.memory.get_run(session.run_id)
        if run.status is RunStatus.COMPLETED:
            return WorkerResult.ok(summary=run.result.get("summary", "Coding output published."),
                task=result_task, run=session.tool_result().model_dump(mode="json"),
                post_publication_warning=error)
        cancelled = is_session_cancelled(session.backend.session_key)
        reason, context = ("Coding task was cancelled.", {}) if cancelled else (error, context)
        if not cancelled:
            salvaged, reason = _salvage(session, reason, result_task, **context)
            if salvaged is not None:
                return salvaged
        session.fail_if_running(reason, cancelled=cancelled)
        return WorkerResult.fail(reason, summary="Coding agent failed.", **context)
    finally:
        session.cleanup_ephemeral()


def _salvage(session: CodingRunSession, reason: str, task: str,
             **data: Any) -> tuple[WorkerResult | None, str]:
    """Save declared outputs of an unpublished run, reported as partial.

    The error envelope with a completed run resolves to a ``partial`` outcome:
    the files are durable, but the worker never validated or summarized them.
    """
    try:
        tool_result = session.salvage_declared_outputs(reason)
    except Exception as exc:
        return None, f"{reason}; the host could not save the declared outputs: {exc}"
    if tool_result is None:
        return None, reason
    return WorkerResult.fail(
        f"Coding worker stopped without publishing: {reason}",
        summary=str(tool_result.result.get("summary") or reason),
        task=task, run=tool_result.model_dump(mode="json"), **data,
    ), reason


__all__ = ["create_coding_agent", "invoke_coding_agent"]
