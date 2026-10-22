"""Verify scientific worker deliverables before plan/conclusion publication.

Tool requirements come from the central memory registry. Source queries are not
deliverables; an explicitly reused run must satisfy the same artifact checks as
a newly computed one. No prose/keyword inference is used to mark work complete.
"""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from pydantic import BaseModel, Field

from agents.workers.result import WorkerResult
from extensions import TOOL_MEMORY_SPECS
from project_memory.models import RunStatus, Workflow


class DeliverableSpec(BaseModel):
    """A source-bound scientific output requirement, including explicit reuse."""

    deliverable_id: str = Field(min_length=1, max_length=128)
    workflow: Workflow
    source_run_ids: list[str] = Field(min_length=1, max_length=24)
    source_artifact_ids: list[str] = Field(default_factory=list, max_length=48)
    parameters: dict[str, Any] = Field(default_factory=dict)
    required_roles: list[str] = Field(min_length=1, max_length=24)
    count: int = Field(default=1, ge=1, le=100)


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def verify_worker_completion(result: WorkerResult, *, project_store=None,
                             project_id=None, required_artifact_roles=(), deliverables=()) -> WorkerResult:
    """Check attempted scientific tools and explicitly requested output roles.

    Corrected retries for the same tool/source replace an earlier failure. Other
    failed outputs remain partial/failed even if some artifacts were produced.
    Read-only explanation tasks without output requirements remain valid.
    """
    attempts = {}
    candidates = {}
    for event in result.trace:
        name = event.get("name")
        payload = _object(event.get("_raw_output", event.get("output")))
        if name in TOOL_MEMORY_SPECS:
            args = _object(event.get("input"))
            source_ids = tuple(args.get("source_run_ids") or (
                [args["source_run_id"]] if args.get("source_run_id") else []))
            key = (name, source_ids, args.get("source_artifact_id"),
                   json.dumps(args, sort_keys=True, ensure_ascii=False))
            attempts[key] = (payload, args)
        if name in TOOL_MEMORY_SPECS or name == "query_project_memory":
            for item in [payload, *payload.get("runs", [])]:
                if isinstance(item, dict) and isinstance(item.get("run_id"), str):
                    candidates[item["run_id"]] = item
    for item in [result.data.get("run"), *result.data.get("runs", [])]:
        if isinstance(item, dict) and isinstance(item.get("run_id"), str):
            candidates[item["run_id"]] = item
    if not attempts and not required_artifact_roles and not deliverables:
        return result

    failures = []
    verified = {}
    service = None
    if project_store is not None and project_id:
        try:
            service = project_store.runs(project_id)
        except Exception as exc:
            failures.append(f"Cannot verify project artifacts: {exc}")

    def check(run_id, spec=None):
        if service is None:
            if project_store is not None or required_artifact_roles:
                return None
            # Non-project invocation: validate tool status, never claim disk verification.
            item = candidates.get(run_id, {})
            if item.get("status") == "completed" and (spec is None or item.get("workflow") == spec.workflow):
                return {"run_id": run_id, "roles": set(), "run": item}
            return None
        try:
            run = service.memory.get_run(run_id)
            if run.status is not RunStatus.COMPLETED or (spec and run.workflow.value != spec.workflow):
                return None
            artifacts = service.verified_artifacts(run_id)
            roles = {item.role for item in artifacts}
            if spec and not set(spec.required_artifact_roles).issubset(roles):
                return None
            return {"run_id": run_id, "roles": roles,
                    "run": {"run_id": run_id, "workflow": run.workflow.value,
                            "status": run.status.value, "manifest": run.manifest_path},
                    "sources": {item.source_run_id for item in run.sources},
                    "parameters": run.parameters,
                    "workflow": run.workflow,
                    "selected_sources": {item.source_run_id for item in run.sources
                                         if item.relation not in {"origin_frame", "reference_origin_frame", "simulation_context"}},
                    "artifact_ids": {value for item in run.sources for value in (
                        item.metadata.get("source_artifact_id"), item.metadata.get("artifact_id"),
                        (item.metadata.get("structure_source") or {}).get("artifact_id")
                    ) if value} | {run.parameters[key] for key in (
                        "source_artifact_id", "reference_artifact_id"
                    ) if run.parameters.get(key)}}
        except (ValueError, OSError, KeyError):
            return None

    unmatched = []
    for (name, source_ids, source_artifact_id, _request_key), (payload, args) in attempts.items():
        spec = TOOL_MEMORY_SPECS[name]
        match = check(payload.get("run_id"), spec) if payload.get("run_id") and payload.get("status") == "completed" else None
        # An explicit memory read can reuse an exact compatible derived result.
        if match is None and service is not None and source_ids:
            for run_id in candidates:
                candidate = check(run_id, spec)
                if (candidate and set(source_ids) == candidate.get("sources")
                        and (not source_artifact_id or source_artifact_id in candidate.get("artifact_ids", set()))
                        and all(candidate["parameters"].get(key) == value for key, value in args.items()
                                if key not in {"source_run_id", "source_run_ids", "source_artifact_id"})):
                    match = candidate
                    break
        if match:
            verified[match["run_id"]] = match
        else:
            unmatched.append((name, payload.get("run_id")))
    # A failed attempt whose own run a corrected retry later completed is resolved.
    failures.extend(f"{name}: no verified completed result with its required artifacts"
                    for name, run_id in unmatched if run_id not in verified)

    contracts = [item if isinstance(item, DeliverableSpec) else DeliverableSpec.model_validate(item)
                 for item in deliverables]
    if len({item.deliverable_id for item in contracts}) != len(contracts):
        raise ValueError("Deliverable IDs must be distinct")
    contract_checks = []
    for contract in contracts:
        matches = []
        for run_id in candidates:
            candidate = check(run_id)
            if (candidate and candidate.get("workflow") == contract.workflow
                    and set(contract.source_run_ids) == candidate.get("selected_sources")
                    and set(contract.source_artifact_ids).issubset(candidate.get("artifact_ids", set()))
                    and set(contract.required_roles).issubset(candidate["roles"])
                    and all(candidate["parameters"].get(key) == value
                            for key, value in contract.parameters.items())):
                matches.append(run_id)
                verified[run_id] = candidate
        passed = len(matches) >= contract.count
        contract_checks.append({"deliverable_id": contract.deliverable_id,
                                "passed": passed, "run_ids": matches})
        if not passed:
            failures.append(f"{contract.deliverable_id}: missing verified outputs for the specified inputs")
    if required_artifact_roles and not attempts and not contracts:
        failures.append("Reusing scientific outputs requires a source-bound deliverable contract")
    roles = {role for item in verified.values() for role in item["roles"]}
    missing = sorted(set(required_artifact_roles) - roles)
    if missing:
        failures.append(f"Missing requested artifacts: {', '.join(missing)}")
    failed = bool(failures) or result.status == "error"
    outcome = ("partial" if verified else "failed") if failed else "completed"
    audit = {"outcome": outcome, "verified_run_ids": sorted(verified),
             "missing_artifact_roles": missing, "failures": failures,
             "deliverables": contract_checks}
    data = {**result.data, "completion_check": audit}
    # Preserve reusable run references for conclusion provenance without a tool transcript.
    existing = {item["run_id"]: item for item in data.get("runs", []) if isinstance(item, dict) and item.get("run_id")}
    for run_id, item in verified.items():
        existing.setdefault(run_id, item["run"])
    if existing:
        data["runs"] = list(existing.values())
    error = result.error or ("; ".join(failures) if failures else None)
    summary = result.summary
    if failures:
        summary = f"Task {outcome}: {error}.\nWorker report: {summary}"
    return replace(result, status="error" if failed else "ok", data=data, error=error, summary=summary,
                   worker_outcome=None if failed else result.worker_outcome)
