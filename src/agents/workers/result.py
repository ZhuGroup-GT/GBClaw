"""Structured result format (WorkerResult) returned from workers to the orchestrator.

Workers wrap outcomes in WorkerResult and serialize to JSON via delegate tools;
the orchestrator reads summary fields and explains results to the user.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal

from agents.workers.outcome import WorkerOutcome, parse_worker_outcome
from agents.memory.decisions import payload_requires_user


@dataclass(frozen=True)
class WorkerResult:
    status: Literal["ok", "error"]
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    worker_outcome: WorkerOutcome | None = None

    def to_json(self) -> str:
        """Serialize the handoff, after callers have consumed the internal trace.

        Full tool events are streamed/persisted separately. Keeping them on the
        Python object lets plan tracking and follow-up validation inspect exact
        evidence without replaying that history into the parent model's context.
        New workers get this boundary automatically.
        """
        payload = asdict(compact_worker_result(self))
        if self.worker_outcome is not None:
            payload["worker_outcome"] = WorkerOutcome.model_validate(self.worker_outcome).model_dump(mode="json")
        outcome = parse_worker_outcome(payload)
        payload["worker_outcome"] = outcome.model_dump(mode="json")
        return json.dumps(payload, ensure_ascii=False)

    @classmethod
    def ok(
        cls,
        summary: str,
        *,
        trace: list[dict[str, Any]] | None = None,
        **data: Any,
    ) -> WorkerResult:
        return cls(status="ok", summary=summary, data=dict(data), trace=list(trace or []))

    @classmethod
    def fail(
        cls, error: str, summary: str = "", *,
        trace: list[dict[str, Any]] | None = None, **data: Any,
    ) -> WorkerResult:
        return cls(
            status="error", summary=summary or error, error=error,
            trace=list(trace or []), data=dict(data),
        )


WORKER_CONTEXT_ACTION = (
    "The worker's conversation was discarded and cannot be resumed; the runs it completed are "
    "saved in project memory and listed in this result. Re-delegate a narrower task (fewer inputs, "
    "smaller or referenced data) that reuses those run IDs instead of rerunning them."
)


def worker_failure(exc: BaseException) -> tuple[str, dict[str, Any]]:
    """Error text and result data for a disposable worker stopped by *exc*."""
    from agents.middleware.contracts import ContextCompactionError

    error = ContextCompactionError.from_exception(exc)
    if error is None:
        return str(exc), {}
    failure = {**error.as_dict(checkpoint_preserved=False), "action": WORKER_CONTEXT_ACTION}
    return failure["message"], {"context_failure": failure, "next_action": WORKER_CONTEXT_ACTION}


def compact_worker_result(result: WorkerResult) -> WorkerResult:
    """Return conclusions and durable references, never a nested conversation.

    Scientific result projection is shared with the model-facing tool boundary;
    individual agents do not need their own lists of scientific result fields.
    Structured handoffs such as plan drafts and verified analysis follow-ups stay
    in ``data``. The caller's object and its full trace remain unchanged.
    """
    from project_memory.models import RunStatus, Workflow
    from agents.memory.projection import compact_tool_payload
    from project_memory.publication.results import RunToolResult

    runs: dict[str, dict[str, Any]] = {}

    def run_reference(value: Any, tool_name: str = "") -> dict[str, Any] | None:
        try:
            payload = json.loads(value) if isinstance(value, str) else value
            run = RunToolResult.model_validate(payload)
        except (ValueError, TypeError):
            return None
        if (
            isinstance(payload.get("artifact_roles"), list)
            and isinstance(payload.get("full_output"), dict)
            and payload["full_output"].get("tool") == "query_project_memory"
        ):
            return dict(payload)
        if payload_requires_user(run.model_dump(mode="json")):
            # A handoff may remove the worker conversation, but all unresolved
            # scientific choices must reach the parent before it can decide.
            projected = {"result": run.result}
        else:
            projected = compact_tool_payload(run.model_dump(mode="json"), tool_name=tool_name)
        reference: dict[str, Any] = {
            "run_id": run.run_id,
            "workflow": run.workflow.value,
            "status": run.status.value,
            "manifest": run.manifest,
            "full_output": {
                "tool": "query_project_memory", "entity": "runs", "run_id": run.run_id,
            },
            "artifact_roles": sorted({
                artifact["role"] for artifact in run.artifacts
                if isinstance(artifact.get("role"), str)
            }),
        }
        if run.error:
            reference["error"] = run.error
        if run.result.get("conclusion_lookup_key"):
            reference["conclusion_lookup_key"] = run.result["conclusion_lookup_key"]
        # Follow-up requests must come from the delegate's persisted-run checks,
        # not from a worker or an unverified copy in its tool transcript.
        conclusion = {
            key: value for key, value in projected.get("result", {}).items()
            if key not in {"analysis_followups", "task"}
        }
        if conclusion:
            reference["result"] = conclusion
        if run.workflow is Workflow.GB_MINIMIZE and run.status is RunStatus.COMPLETED:
            reference["final_minimization"] = {
                key: run.result[key] for key in (
                    "final_minimization_converged", "stopping", "energy_final",
                    "force_norm_final", "iterations", "force_evaluations",
                ) if key in run.result
            }
            reference["convergence_basis"] = "final_minimization"
        return reference

    data = {key: value for key, value in result.data.items() if key != "task"}
    # Coding and future workers may already expose an explicit run envelope.
    if "run" in data and (reference := run_reference(data["run"])) is not None:
        data["run"] = reference
        runs[reference["run_id"]] = reference
    existing_runs = data.get("runs")
    for reference in existing_runs if isinstance(existing_runs, list) else []:
        if isinstance(reference, dict) and isinstance(reference.get("run_id"), str):
            runs[reference["run_id"]] = reference
    for record in result.trace:
        if not isinstance(record, dict):
            continue
        reference = run_reference(
            record.get("_raw_output", record.get("output")),
            tool_name=str(record.get("name") or ""),
        )
        if reference is not None:
            # Executed/terminal output replaces the earlier configuration reply.
            runs[reference["run_id"]] = reference
    if runs:
        data["runs"] = list(runs.values())
    artifacts = list(dict.fromkeys([
        *result.artifacts,
        *(reference["manifest"] for reference in runs.values() if reference.get("manifest")),
    ]))
    return replace(result, data=data, artifacts=artifacts, trace=[])
