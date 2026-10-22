"""Validated final worker handoffs and a bounded current result normalization.

An outcome describes the worker report. Scientific success still requires the
host's completed-run and deliverable checks; this object grants no write rights.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OutcomeStatus = Literal["completed", "reused", "partial", "failed", "awaiting_user", "cancelled", "interrupted"]


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["run", "fact", "artifact"]
    id: str = Field(min_length=1, max_length=240, pattern=r"^\S+$")


class WorkerOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    status: OutcomeStatus
    summary: str
    evidence_refs: tuple[EvidenceReference, ...] = ()
    next_action: str = ""

    def to_payload(self, *, data: dict | None = None) -> dict:
        """Return the model-facing envelope with its explicit host contract."""
        return {
            "status": "error" if self.status == "failed" else "ok",
            "summary": self.summary,
            "data": dict(data or {}),
            "worker_outcome": self.model_dump(mode="json"),
        }


def result_sources(value) -> tuple[list[str], list[str]]:
    """Normalize references from the current WorkerResult/RunToolResult envelopes."""
    runs, facts = [], []

    def visit(item):
        if isinstance(item, dict):
            for key, nested in item.items():
                if key in {"run_id", "source_run_id"} and isinstance(nested, str):
                    runs.append(nested)
                elif key in {"lookup_key", "conclusion_lookup_key"} and isinstance(nested, str):
                    facts.append(nested)
                elif key in {"source_run_ids", "child_run_ids"} and isinstance(nested, list):
                    runs.extend(x for x in nested if isinstance(x, str))
                elif key == "source_fact_keys" and isinstance(nested, list):
                    facts.extend(x for x in nested if isinstance(x, str))
                else:
                    visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return list(dict.fromkeys(runs)), list(dict.fromkeys(facts))


def parse_worker_outcome(payload: dict) -> WorkerOutcome:
    # Recognize the supported result envelopes, never infer success from an
    # unknown status (including queued/running/configured or a missing status).
    status = payload.get("status")
    if not isinstance(status, str) or status not in {
        "ok", "completed", "reused", "error", "failed", "partial",
        "awaiting_user", "cancelled", "interrupted",
    }:
        raise ValueError(f"Unsupported final worker status: {status!r}")
    explicit = payload.get("worker_outcome")
    if explicit is not None:
        outcome = WorkerOutcome.model_validate(explicit)
        if status in {"error", "failed", "cancelled", "interrupted"} and outcome.status in {"completed", "reused"}:
            raise ValueError("Worker outcome conflicts with its failed result envelope")
        return outcome
    data = payload.get("result") if "run_id" in payload else payload.get("data", {})
    data = data if isinstance(data, dict) else {}
    check = data.get("completion_check") or {}
    if isinstance(check, dict) and check.get("outcome") in {"failed", "partial"}:
        resolved = check["outcome"]
    elif status in {"cancelled", "interrupted", "partial"}:
        resolved = status
    elif status in {"error", "failed"}:
        completed = [item for item in data.get("runs", []) if isinstance(item, dict) and item.get("status") == "completed"]
        resolved = "partial" if completed else "failed"
    elif status == "awaiting_user" or data.get("awaiting_user") or data.get("requires_user_decision") or any(
        isinstance(item, dict) and item.get("status") in {"pending", "selected"}
        for item in data.get("interactions", []) or []
    ):
        resolved = "awaiting_user"
    elif status == "reused" or data.get("reused") is True:
        resolved = "reused"
    else:
        resolved = "completed"
    runs, facts = result_sources(payload)
    return WorkerOutcome(
        status=resolved,
        summary=str((data if "run_id" in payload else payload).get("summary") or ""),
        evidence_refs=tuple(EvidenceReference(kind=kind, id=identifier)
                            for kind, identifiers in (("run", runs), ("fact", facts))
                            for identifier in identifiers),
        next_action=str(data.get("next_action") or ""),
    )
