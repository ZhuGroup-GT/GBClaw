"""Host-owned lifecycle shared by registered worker delegates.

Domain delegates keep their public input schemas and scientific setup. This
module supplies invocation identity, memory, events, plan tracking and durable
conclusions without asking each new delegate to wire those concerns together.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import wraps
import inspect
import json
from typing import Any, Sequence

from langchain_core.tools import BaseTool, InjectedToolCallId
from langgraph.config import get_stream_writer

from core.stream_events import StreamEventType, WORKER_FORWARDED_EVENTS
from core.util.graph_config import runtime_ids
from agents.workers.result import WorkerResult
from extensions import WorkerDefinition, worker_definition, worker_definitions, resolve_hook


_CURRENT_INVOCATION: ContextVar[WorkerInvocation | None] = ContextVar(
    "gbclaw_worker_invocation", default=None,
)


@dataclass(frozen=True)
class WorkerInvocation:
    definition: WorkerDefinition
    project_id: str | None
    chat_thread_id: str | None
    tool_call_id: str
    memory: Any
    tracker: Any = None

    def emit(self, event: dict[str, Any]) -> None:
        """Track full internal evidence and stream only its public projection."""
        if self.tracker is not None:
            self.tracker.record_tool_event(event)
        if event.get("type") not in WORKER_FORWARDED_EVENTS:
            return
        try:
            writer = get_stream_writer()
        except (RuntimeError, KeyError):
            return
        from agents.streaming.events import public_stream_event

        writer({**public_stream_event(event), "parent_id": self.tool_call_id,
                "parent_name": self.definition.delegate_tool})


def current_worker_invocation(*, required: bool = True) -> WorkerInvocation | None:
    invocation = _CURRENT_INVOCATION.get()
    if invocation is None and required:
        raise RuntimeError("Worker execution must enter through create_worker_delegate")
    return invocation


def worker_memory_inputs(
    agent: str, extra_tools: Sequence[BaseTool] | None = None,
) -> tuple[list[BaseTool], str | None]:
    """Inject memory according to the active invocation's registered policy."""
    invocation = current_worker_invocation()
    if invocation.definition.agent != agent:
        raise ValueError(f"Worker {agent!r} cannot consume another worker's invocation")
    memory_tools = list(invocation.memory.tools)
    names = {tool.name for tool in memory_tools}
    def tool_name(tool):
        if isinstance(tool, dict):
            return (tool.get("function") or tool).get("name")
        return getattr(tool, "name", getattr(tool, "__name__", None))
    duplicates = {tool_name(tool) for tool in extra_tools or () if tool_name(tool) in names}
    if duplicates:
        raise ValueError(f"Business tools shadow registered memory tools: {sorted(duplicates)}")
    return [*memory_tools, *(extra_tools or ())], invocation.memory.handoff.prompt


def _finish_result(result, *, tracker, project_store, project_id, arguments) -> str:
    from project_memory.publication.results import RunToolResult
    from agents.workers.completion import verify_worker_completion
    from agents.workers.outcome import WorkerOutcome, parse_worker_outcome

    if isinstance(result, WorkerOutcome):
        result = WorkerResult(
            status="error" if result.status in {"failed", "partial", "cancelled", "interrupted"} else "ok",
            summary=result.summary, worker_outcome=result,
        )
    if isinstance(result, WorkerResult):
        result = verify_worker_completion(
            result, project_store=project_store, project_id=project_id,
            required_artifact_roles=arguments.get("required_artifact_roles") or (),
            deliverables=arguments.get("deliverables") or (),
        )
        if tracker is not None:
            tracker.finish_worker(result)
        payload = json.loads(result.to_json())
    elif isinstance(result, RunToolResult):
        if tracker is not None:
            tracker.finish_run(result)
        payload = result.model_dump(mode="json")
    else:
        raise TypeError("Worker delegates must return WorkerResult, RunToolResult, or WorkerOutcome")
    payload["worker_outcome"] = parse_worker_outcome(payload).model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False)


def create_worker_delegate(
    delegate: BaseTool, *, agent: str, project_store=None, memory_endpoint=None,
) -> BaseTool:
    """Install the registered worker lifecycle without changing its tool schema.

    The conclusion wrapper is outermost: it starts a durable task cursor before
    recall, and can recover a prior invocation without repeating scientific work.
    ContextVar tokens are always reset, including cancellation and exceptions.
    """
    from agents.workers.plan_tracking import PlanStepTracker
    from planning import PlanCapability
    from agents.memory.worker import prepare_worker_memory
    from agents.memory.conclusions import wrap_delegate_conclusions

    definition = worker_definition(agent)
    if delegate.name != definition.delegate_tool:
        raise ValueError(f"Worker {agent!r} declares tool {definition.delegate_tool!r}, got {delegate.name!r}")
    if (delegate.metadata or {}).get("worker_agent"):
        raise ValueError(f"Worker delegate {delegate.name!r} is already assembled")
    if getattr(delegate, "coroutine", None) is not None or getattr(delegate, "func", None) is None:
        raise TypeError("Worker delegates require a synchronous host callable")
    original = delegate.func
    signature = inspect.signature(original)
    missing = {"task", "tool_call_id"} - set(signature.parameters)
    if missing:
        raise ValueError(f"Worker delegate is missing host arguments: {sorted(missing)}")
    call_field = delegate.get_input_schema().model_fields.get("tool_call_id")
    if call_field is None or InjectedToolCallId not in call_field.metadata:
        raise ValueError("Worker tool_call_id must be annotated with InjectedToolCallId")
    if definition.plan_capability and not {"plan_id", "step_id"}.issubset(signature.parameters):
        raise ValueError("Workers with a plan capability must expose plan_id and step_id")

    @wraps(original)
    def invoke(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        arguments = bound.arguments
        call_id = str(arguments.get("tool_call_id") or "").strip()
        if not call_id:
            return WorkerResult.fail("Worker delegation requires a host tool_call_id.").to_json()
        project_id, chat_thread_id = runtime_ids()
        task = str(arguments.get("task") or "").strip()
        if not task:
            return WorkerResult.fail("Task description must not be empty.").to_json()
        tracker = None
        tracker_started = False
        token = None
        result = None
        try:
            if not definition.plan_capability and (arguments.get("plan_id") or arguments.get("step_id")):
                raise ValueError(f"Worker {agent!r} does not support persisted plan steps")
            if definition.plan_capability:
                tracker = PlanStepTracker(
                    store=project_store, project_id=project_id,
                    plan_id=arguments.get("plan_id"), step_id=arguments.get("step_id"),
                    capability=PlanCapability(definition.plan_capability),
                )
                tracker.start()
                tracker_started = True
            memory = prepare_worker_memory(project_store, project_id, task, agent=agent)
            invocation = WorkerInvocation(definition, project_id, chat_thread_id, call_id, memory, tracker)
            token = _CURRENT_INVOCATION.set(invocation)
            if memory.handoff.available:
                invocation.emit({"type": StreamEventType.STATUS, "message": (
                    f"{definition.display_name or agent} received project memory: "
                    f"{len(memory.handoff.run_ids)} completed run(s), "
                    f"{len(memory.handoff.fact_keys)} fact(s)."
                )})
            result = original(*args, **kwargs)
            return _finish_result(result, tracker=tracker, project_store=project_store,
                                  project_id=project_id, arguments=arguments)
        except Exception as exc:
            if tracker is not None and tracker_started:
                try:
                    tracker.fail(str(exc))
                except Exception as tracking_error:
                    exc = RuntimeError(f"{exc}; plan failure could not be recorded: {tracking_error}")
            if isinstance(result, WorkerResult):
                return replace(result, status="error", error=str(exc), worker_outcome=None,
                               summary=f"{agent} worker finalization failed: {exc}").to_json()
            from project_memory.publication.results import RunToolResult
            evidence = {"run": result.model_dump(mode="json")} if isinstance(result, RunToolResult) else {}
            return WorkerResult.fail(str(exc), summary=f"{agent} worker failed.", **evidence).to_json()
        finally:
            if token is not None:
                _CURRENT_INVOCATION.reset(token)

    invoke.__gbclaw_worker_agent__ = agent
    delegate.func = invoke
    delegate.metadata = {**(delegate.metadata or {}), "worker_agent": agent,
                         "execution_kind": definition.execution_kind,
                         "plan_capability": definition.plan_capability}
    return wrap_delegate_conclusions(
        delegate, agent=agent, project_store=project_store, memory_endpoint=memory_endpoint,
    )


def build_registered_worker_tools(model_router, *, project_store=None, memory_endpoint=None,
                                  factory_options=None) -> list[BaseTool]:
    """Assemble enabled workers from the same registry consumed by UI and gates."""
    tools = []
    for definition in worker_definitions():
        factory = resolve_hook(definition.factory)
        options = dict((factory_options or {}).get(definition.agent, {}))
        delegate = factory(model_router.for_role(definition.endpoint_role),
                           project_store=project_store, memory_endpoint=memory_endpoint, **options)
        if not isinstance(delegate, BaseTool):
            raise TypeError(f"Factory for {definition.agent!r} must return a BaseTool")
        if not (delegate.metadata or {}).get("worker_agent"):
            delegate = create_worker_delegate(delegate, agent=definition.agent,
                project_store=project_store, memory_endpoint=memory_endpoint)
        if getattr(getattr(delegate, "func", None), "__gbclaw_worker_agent__", None) != definition.agent:
            raise ValueError(f"Factory for {definition.agent!r} bypassed the worker lifecycle")
        if delegate.name != definition.delegate_tool:
            raise ValueError(f"Factory for {definition.agent!r} returned the wrong tool")
        tools.append(delegate)
    return tools


__all__ = ["WorkerDefinition", "WorkerInvocation", "create_worker_delegate",
           "current_worker_invocation", "worker_memory_inputs", "build_registered_worker_tools"]
