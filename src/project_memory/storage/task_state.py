"""Durable execution cursors; separate from scientific facts and approved plans."""
from __future__ import annotations

import hashlib
import re
import uuid

from project_memory.storage.facts import FactVersionConflict
from project_memory.publication.writes import commit_facts
from project_workspace.models import utc_now_iso
from project_memory.models import FactRecord, FactEvidenceKind
from project_workspace.store import ProjectStore


_CURSOR_INTERPRETATION = (
    "This records delegate execution, not a scientific run or background job. "
    "A running cursor may be the current worker's own entry record before any tool ran. "
    "Verify actual run IDs, status and matching inputs before waiting or reusing results; "
    "a cursor alone is not a reason to skip the assigned work."
)


def _identity(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:32]


class TaskStateService:
    """CAS-merge parallel invocation cursors without rewriting prior conclusions."""
    def __init__(self, store: ProjectStore, project_id: str):
        self.memory = store.memory(project_id)
        self.project_id = project_id

    @staticmethod
    def thread_key(thread_id: str) -> str:
        return f"task:thread:{_identity(thread_id)}"

    @staticmethod
    def invocation_key(thread_id: str, agent: str, invocation_id: str) -> str:
        return f"task:invocation:{_identity(thread_id, agent, invocation_id)}"

    def _current(self, lookup_key):
        rows = self.memory.query_facts(kind="task_state", lookup_key=lookup_key, limit=1)
        return rows[0] if rows else None

    def _mutate(self, *, thread_id, agent, invocation_id, objective="", status,
                conclusion_ref=None, blockers=(), next_action="", plan_id=None, step_id=None,
                retained_result_ref=None, parameters_sha256=None):
        if not thread_id or not invocation_id or not agent:
            raise ValueError("Task state requires thread, agent and invocation identities")
        if status not in {"running", "completed", "reused", "failed", "partial", "awaiting_user", "cancelled", "interrupted", "publication_error"}:
            raise ValueError("Unsupported task invocation status")
        if status == "running" and (
            not isinstance(parameters_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", parameters_sha256)
        ):
            raise ValueError("Task invocation requires the complete business-parameter SHA256")
        thread_key = self.thread_key(thread_id)
        invocation_key = self.invocation_key(thread_id, agent, invocation_id)
        for _attempt in range(12):
            prior_thread, prior_invocation = self._current(thread_key), self._current(invocation_key)
            now = utc_now_iso()
            previous = dict(prior_invocation.value) if prior_invocation else {}
            if previous and status == "running":
                if (previous.get("parameters_sha256") != parameters_sha256
                        or previous.get("objective_sha256") != hashlib.sha256(objective.encode()).hexdigest()
                        or previous.get("plan_id") != plan_id or previous.get("step_id") != step_id):
                    raise ValueError("Invocation identity cannot be reused for a different task")
                # A replay may recover an interrupted running cursor, but must
                # never reopen a terminal invocation or erase its conclusion.
                return {**self.reference(prior_invocation), "already_started": True}
            if previous.get("conclusion_ref") and conclusion_ref and previous["conclusion_ref"].get("conclusion_id") != conclusion_ref.get("conclusion_id"):
                raise ValueError("An invocation cannot replace its historical conclusion identity")
            invocation = {
                **previous, "thread_id": thread_id, "agent": agent, "invocation_id": invocation_id,
                "objective": objective[:600] if objective else previous.get("objective", ""),
                "objective_sha256": hashlib.sha256(objective.encode()).hexdigest() if objective else previous.get("objective_sha256"),
                "parameters_sha256": parameters_sha256 or previous.get("parameters_sha256"),
                "status": status, "current_stage": "worker_running" if status == "running" else "delegate_returned",
                "plan_id": plan_id or previous.get("plan_id"), "step_id": step_id or previous.get("step_id"),
                "blockers": [str(item)[:400] for item in blockers][:8], "next_action": next_action[:600],
                "started_at": previous.get("started_at", now), "updated_at": now,
            }
            if conclusion_ref:
                invocation["conclusion_ref"] = conclusion_ref
            if retained_result_ref:
                invocation["retained_result_ref"] = retained_result_ref
            aggregate = dict(prior_thread.value) if prior_thread else {}
            active = dict(aggregate.get("active_invocations", {}))
            blocked = dict(aggregate.get("blocked_invocations", {}))
            if status == "running":
                active[invocation_key] = {"agent": agent, "invocation_id": invocation_id,
                                          "lookup_key": invocation_key, "plan_id": plan_id, "step_id": step_id}
            else:
                active.pop(invocation_key, None)
                if status in {"failed", "partial", "awaiting_user", "publication_error", "interrupted", "cancelled"}:
                    blocked[invocation_key] = {"agent": agent, "status": status, "lookup_key": invocation_key,
                                               "blockers": invocation["blockers"], "next_action": next_action[:600]}
                else:
                    blocked.pop(invocation_key, None)
            conclusions = list(aggregate.get("completed_conclusion_refs", []))
            if conclusion_ref and all(item.get("conclusion_id") != conclusion_ref.get("conclusion_id") for item in conclusions):
                conclusions.append(conclusion_ref)
            aggregate.update({
                "thread_id": thread_id, "objective": (aggregate.get("objective") if aggregate.get("active_invocations") else invocation["objective"]),
                "status": "running" if active else ("partial" if blocked and status in {"completed", "reused"} else status),
                "current_stage": "delegated_work_in_progress" if active else "ready_for_orchestrator",
                "active_invocations": active, "completed_conclusion_refs": conclusions[-32:],
                "blocked_invocations": blocked,
                "latest_invocation": self._reference_values(invocation_key),
                "history_note": "Older invocation states and stable conclusion IDs remain queryable; this cursor keeps only the latest 32 conclusion references.",
                "blockers": invocation["blockers"], "next_action": next_action[:600], "updated_at": now,
            })
            facts = []
            expected = {}
            for key, value, prior in ((thread_key, aggregate, prior_thread), (invocation_key, invocation, prior_invocation)):
                facts.append(FactRecord(
                    fact_id=prior.fact_id if prior else str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.project_id}:{key}")),
                    kind="task_state", lookup_key=key, value=value,
                    evidence_kind=FactEvidenceKind.DETERMINISTIC_WORKFLOW,
                    created_at=prior.created_at if prior else now, updated_at=now,
                ))
                expected[("task_state", key)] = prior.version if prior else None
            try:
                committed = commit_facts(self.memory, facts, expected_versions=expected, writer="task_state", skip_unchanged=True)
                saved = next((fact for fact in committed if fact.lookup_key == invocation_key), None) or self._current(invocation_key)
                return self.reference(saved)
            except FactVersionConflict:
                continue
        raise FactVersionConflict("Concurrent task-state updates exceeded retry limit")

    @staticmethod
    def _reference_values(lookup_key):
        return {"tool": "get_task_state", "lookup_key": lookup_key}

    def reference(self, fact):
        return {**self._reference_values(fact.lookup_key), "fact_id": fact.fact_id,
                "status": fact.value["status"], "version": fact.version}

    def start(self, *, thread_id, agent, invocation_id, objective, parameters_sha256, plan_id=None, step_id=None):
        return self._mutate(thread_id=thread_id, agent=agent, invocation_id=invocation_id,
                            objective=objective, status="running", plan_id=plan_id, step_id=step_id,
                            parameters_sha256=parameters_sha256,
                            next_action="The assigned delegate should carry out this task. This cursor records invocation entry; check actual scientific run records before deciding to wait or reuse results.")

    def finish(self, *, thread_id, agent, invocation_id, status, conclusion_ref=None, blockers=(), next_action="",
               retained_result_ref=None):
        return self._mutate(thread_id=thread_id, agent=agent, invocation_id=invocation_id, status=status,
                            conclusion_ref=conclusion_ref, blockers=blockers, next_action=next_action,
                            retained_result_ref=retained_result_ref)

    def get(self, *, thread_id=None, lookup_key=None):
        key = lookup_key or self.thread_key(thread_id or "")
        if not key.startswith(("task:thread:", "task:invocation:")):
            raise ValueError("Invalid task-state lookup key")
        fact = self._current(key)
        if fact is None:
            return {"status": "not_found", "lookup_key": key}
        return {**fact.model_dump(mode="json"), "record_type": "execution_cursor",
                "scientific_run_evidence": False, "interpretation": _CURSOR_INTERPRETATION}

    def context_snapshot(self, *, thread_id: str) -> dict:
        """Small current cursor for request-time context injection, without history."""
        result = self.get(thread_id=thread_id)
        value = result.get("value") or {}
        if not value:
            return {"status": "not_found", "thread_id": thread_id}
        return {"lookup_key": result["lookup_key"], "version": result["version"],
                "record_type": "execution_cursor", "scientific_run_evidence": False,
                "interpretation": _CURSOR_INTERPRETATION,
                **{key: value.get(key) for key in ("objective", "status", "current_stage", "active_invocations",
                    "blocked_invocations", "blockers", "next_action", "latest_invocation")},
                "completed_conclusion_refs": value.get("completed_conclusion_refs", [])[-8:],
                "read": {"tool": "get_task_state", "lookup_key": result["lookup_key"]}}
