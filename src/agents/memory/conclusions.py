"""One final-conclusion/task-cursor lifecycle around every delegate tool."""
from __future__ import annotations

from functools import wraps
from dataclasses import fields, is_dataclass, replace
from enum import Enum
import inspect
import hashlib
import json
import threading

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from core.util.graph_config import runtime_ids
from project_memory.storage.conclusions import AgentConclusionService
from planning import ProjectPlanService
from project_memory.storage.task_state import TaskStateService
from project_memory.storage.tool_outputs import ToolOutputStore


def _invocation_parameters_digest(parameters) -> str:
    """Hash complete bound business inputs, including defaults and nested models.

    Read model fields directly: presentation serializers and Field(exclude=True)
    must never hide an input from the execution identity. The digest is stored;
    potentially large or sensitive business parameters are not copied to cursors.
    """
    def normalize(value):
        if isinstance(value, BaseModel):
            return normalize({
                **{name: getattr(value, name) for name in type(value).model_fields},
                **(value.model_extra or {}),
            })
        if is_dataclass(value) and not isinstance(value, type):
            return normalize({field.name: getattr(value, field.name) for field in fields(value)})
        if isinstance(value, Enum):
            return normalize(value.value)
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise ValueError("Worker invocation parameter objects require string keys")
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [normalize(item) for item in value]
        if isinstance(value, (set, frozenset)):
            return sorted((normalize(item) for item in value), key=canonical_json)
        return to_jsonable_python(value, by_alias=False, round_trip=True,
                                  inf_nan_mode="constants", serialize_unknown=False)

    def canonical_json(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)

    business_inputs = {key: value for key, value in parameters.items() if key != "tool_call_id"}
    encoded = canonical_json({"schema_version": 1, "parameters": normalize(business_inputs)})
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


from agents.workers.outcome import parse_worker_outcome


def _final_only(value):
    """Keep final evidence while excluding nested conversations/process traces."""
    excluded = {"trace", "raw_trace", "messages", "history", "chat_history", "conversation_history",
                "conversation", "reasoning", "thinking", "chain_of_thought", "tool_events",
                "stdout", "stderr", "logs", "log", "minimization_history"}
    if isinstance(value, dict):
        return {key: _final_only(item) for key, item in value.items()
                if str(key).lower() not in excluded and not str(key).startswith("_raw_")}
    if isinstance(value, list):
        return [_final_only(item) for item in value]
    return value


def wrap_delegate_conclusions(delegate, *, agent, project_store=None, memory_endpoint=None):
    """Bind a shared lifecycle without changing the public tool argument schema."""
    original = delegate.func
    signature = inspect.signature(original)
    model = None
    model_attempted = False
    model_lock = threading.Lock()

    def summary_model():
        nonlocal model, model_attempted
        if memory_endpoint is None:
            return None
        with model_lock:
            if not model_attempted:
                model_attempted = True
                from core.llm.factory import auxiliary_endpoint, build_llm
                # The abstract is at most 800 characters; CJK text can exceed one token per character.
                summary_endpoint = auxiliary_endpoint(
                    memory_endpoint, answer_tokens=min(memory_endpoint.max_tokens or 2048, 2048))
                model = build_llm(summary_endpoint)
        return model

    @wraps(original)
    def invoke(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        parameters = bound.arguments
        project_id, thread_id = runtime_ids()
        if project_store is None or not project_id:
            return original(*args, **kwargs)
        invocation_id = str(parameters.get("tool_call_id") or "")
        if not invocation_id:
            raise ValueError("Project worker invocation requires an injected tool_call_id")
        try:
            parameters_sha256 = _invocation_parameters_digest(parameters)
        except (TypeError, ValueError) as exc:
            from agents.workers.result import WorkerResult
            return WorkerResult.fail(f"Cannot establish complete worker input identity: {exc}").to_json()
        task = str(parameters.get("task") or "")
        plan_id, step_id = parameters.get("plan_id"), parameters.get("step_id")
        thread_id = str(thread_id or f"invocation:{invocation_id}")
        conclusion_invocation_id = (f"thread-{hashlib.sha256(thread_id.encode()).hexdigest()[:24]}:"
                                    f"call-{hashlib.sha256(invocation_id.encode()).hexdigest()}")
        state = TaskStateService(project_store, project_id)
        warnings = []
        recovery_raw = None
        try:
            state_ref = state.start(thread_id=thread_id, agent=agent, invocation_id=invocation_id,
                                    objective=task, parameters_sha256=parameters_sha256,
                                    plan_id=plan_id, step_id=step_id)
        except Exception as exc:
            from agents.workers.result import WorkerResult
            if isinstance(exc, ValueError) and "Invocation identity" in str(exc):
                return WorkerResult.fail(f"Cannot reuse this invocation identity for different work: {exc}").to_json()
            return WorkerResult.fail(
                f"Cannot establish durable worker invocation identity: {exc}",
                summary="Worker was not started because its task cursor could not be recorded.",
            ).to_json()
        if state_ref and state_ref.get("already_started"):
            previous = state.get(lookup_key=state_ref["lookup_key"]).get("value", {})
            conclusion = previous.get("conclusion_ref")
            if not conclusion:
                # The report/index is committed before abstract generation and
                # task finish. Recover that crash window without rerunning work.
                conclusion = AgentConclusionService(project_store, project_id).find_invocation(
                    agent=agent, invocation_id=conclusion_invocation_id)
                if conclusion:
                    state_ref = state.finish(
                        thread_id=thread_id, agent=agent, invocation_id=invocation_id,
                        status=conclusion["outcome"], conclusion_ref=conclusion,
                        next_action="Read the saved conclusion; its prior invocation has already finished.")
            if conclusion:
                restored = AgentConclusionService(project_store, project_id).read(conclusion["conclusion_id"])
                payload = json.loads(restored["content"])
                payload["worker_outcome"] = parse_worker_outcome(payload).model_dump(mode="json")
                container = payload["result"] if payload.get("run_id") else payload.setdefault("data", {})
                container.update(conclusion_ref=conclusion, task_state_ref=state_ref, conclusion_reused=True)
                if payload.get("run_id"):
                    container["summary"] = conclusion["summary"]
                else:
                    payload["summary"] = conclusion["summary"]
                return json.dumps(payload, ensure_ascii=False)
            retained = previous.get("retained_result_ref")
            if previous.get("status") == "publication_error" and isinstance(retained, dict):
                recovery_raw = ToolOutputStore(project_store.project_path(project_id)).read(retained["output_id"])["content"]
            else:
                from agents.workers.result import WorkerResult
                return WorkerResult.fail(
                    "Cannot replay this invocation: its cursor has no saved conclusion or recoverable final result.",
                    summary="Invocation replay is unavailable. Its existing cursor was preserved and no worker was rerun.",
                    task_state_ref=state_ref, invocation_already_started=True,
                    next_action="Inspect get_task_state and the scientific run records before explicitly resuming with a new invocation identity.").to_json()
        try:
            raw = recovery_raw if recovery_raw is not None else original(*args, **kwargs)
        except Exception as exc:
            from agents.workers.result import WorkerResult
            raw = WorkerResult.fail(str(exc), summary=f"{agent} worker failed.").to_json()
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Delegate did not return an object")
            outcome_contract = parse_worker_outcome(payload)
        except (ValueError, TypeError) as exc:
            # Invalid output may be a traceback or an intermediate transcript.
            # Archive it as diagnostics instead of presenting it as a conclusion.
            diagnostic = ToolOutputStore(project_store.project_path(project_id)).write(
                str(raw), tool_name=delegate.name, scope_thread_id=thread_id)
            payload = {"status": "error", "summary": "Worker did not return a valid final-result object.",
                       "data": {"diagnostic_output_ref": diagnostic}, "error": str(exc)}
            outcome_contract = parse_worker_outcome(payload)
        final_payload = _final_only(payload)
        # This report is the replay source, not a model preview. Preserve every
        # final structured field and every typed evidence reference verbatim.
        final_payload["worker_outcome"] = outcome_contract.model_dump(mode="json")
        final_payload.pop("model_output", None)
        is_run = bool(payload.get("run_id")) and isinstance(payload.get("result"), dict)
        source_runs = [ref.id for ref in outcome_contract.evidence_refs if ref.kind == "run"]
        source_facts = [ref.id for ref in outcome_contract.evidence_refs if ref.kind == "fact"]
        source_artifacts = [ref.id for ref in outcome_contract.evidence_refs if ref.kind == "artifact"]
        verified_runs, verified_facts = [], []
        try:
            memory = project_store.memory(project_id)
            for run_id in source_runs:
                try:
                    memory.get_run(run_id)
                    verified_runs.append(run_id)
                except ValueError:
                    warnings.append(f"Worker mentioned an unverified source run: {run_id}")
            for key in source_facts:
                if memory.query_facts(lookup_key=key, include_retracted=True, limit=1):
                    verified_facts.append(key)
                else:
                    warnings.append(f"Worker mentioned an unverified source fact: {key}")
            if source_artifacts:
                service = project_store.runs(project_id)
                for artifact_id in source_artifacts:
                    try:
                        artifact = memory.get_artifact(artifact_id)
                        service.resolve_artifact(artifact)
                        if artifact.run_id not in verified_runs:
                            verified_runs.append(artifact.run_id)
                    except (OSError, ValueError):
                        warnings.append(f"Worker mentioned an unverified source artifact: {artifact_id}")
        except Exception as exc:
            warnings.append(f"Source reference validation could not finish: {exc}")
        if warnings:
            final_payload["source_reference_warnings"] = list(warnings)
        report = json.dumps(final_payload, ensure_ascii=False, indent=2)
        outcome = outcome_contract.status
        conclusion_ref = None
        try:
            try:
                summarizer = summary_model()
            except Exception as exc:
                summarizer = None
                warnings.append(f"Conclusion summary model unavailable; full conclusion retained: {exc}")
            conclusion_ref = AgentConclusionService(project_store, project_id).publish(
                agent=agent, invocation_id=conclusion_invocation_id, purpose=task, report=report,
                outcome=outcome, source_run_ids=verified_runs, source_fact_keys=verified_facts,
                plan_id=plan_id, step_id=step_id, summary_model=summarizer)
            # Preserve the original structured controls and scientific references.
            # Only replace prose after the full report has been saved/indexed.
            container = payload["result"] if is_run else payload.setdefault("data", {})
            container["conclusion_ref"] = conclusion_ref
            if is_run:
                payload["result"]["summary"] = conclusion_ref["summary"]
            else:
                payload["summary"] = conclusion_ref["summary"]
            if plan_id and step_id:
                try:
                    ProjectPlanService(project_store, project_id).attach_conclusion(
                        plan_id, step_id, conclusion_ref=conclusion_ref, summary=conclusion_ref["summary"])
                except Exception as exc:
                    warnings.append(f"Conclusion saved but plan reference update failed: {exc}")
        except Exception as exc:
            warnings.append(f"Final conclusion publication failed; original result retained: {exc}")
        retained_result_ref = None
        if not conclusion_ref:
            try:
                retained_result_ref = ToolOutputStore(project_store.project_path(project_id)).write(
                    raw, tool_name=delegate.name, scope_thread_id=thread_id)
            except Exception as exc:
                warnings.append(f"Final result recovery copy could not be written; full result remains in this response: {exc}")
        try:
            state_ref = state.finish(thread_id=thread_id, agent=agent, invocation_id=invocation_id,
                status=outcome if conclusion_ref else "publication_error", conclusion_ref=conclusion_ref,
                retained_result_ref=retained_result_ref,
                blockers=warnings or ([str(payload.get("error"))] if payload.get("error") else []),
                next_action=(outcome_contract.next_action or ("Read the conclusion and continue from the recorded scientific results; do not rerun solely to reconstruct context."
                             if conclusion_ref else "Retry conclusion publication from the retained final result; do not repeat the scientific calculation.")))
        except Exception as exc:
            warnings.append(f"Task-state completion could not be recorded: {exc}")
        container = payload["result"] if is_run else payload.setdefault("data", {})
        if state_ref:
            container["task_state_ref"] = state_ref
        if retained_result_ref:
            container["retained_result_ref"] = retained_result_ref
        if warnings:
            container["conclusion_warnings"] = warnings
        return json.dumps(payload, ensure_ascii=False)

    delegate.func = invoke
    delegate.metadata = {**(delegate.metadata or {}), "agent_conclusion_agent": agent}
    return delegate
