"""Deterministic plan-step tracking around scientific delegate calls."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from core.stream_events import StreamEventType
from extensions import tool_capability
from agents.workers.outcome import parse_worker_outcome
from agents.workers.result import WorkerResult
from agents.streaming.events import extract_dichromatic_interactions
from planning import (
    PlanCapability,
    PlanStepStatus,
    ProjectPlanService,
)
from project_memory.models import RunStatus
from project_workspace.store import ProjectStore
from project_memory.publication.results import RunToolResult


_FAILURE_STATUSES = {
    "error",
    RunStatus.FAILED.value,
    RunStatus.CANCELLED.value,
}


def _tracks_plan_results(name: str) -> bool:
    capability = tool_capability(name)
    return bool(capability and capability.plan_tracking)



def _collect_result_metadata(value: Any) -> tuple[list[str], list[str]]:
    run_ids: list[str] = []
    failures: list[str] = []
    visited_strings: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            run_id = item.get("run_id")
            if isinstance(run_id, str) and run_id.strip():
                run_ids.append(run_id.strip())
            status = str(item.get("status") or "").strip().lower()
            if status in _FAILURE_STATUSES:
                failures.append(
                    str(item.get("error") or item.get("summary") or status).strip()
                )
            for nested in item.values():
                visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)
        elif isinstance(item, str):
            text = item.strip()
            if not text or text in visited_strings or text[0] not in "[{":
                return
            visited_strings.add(text)
            try:
                visit(json.loads(text))
            except (json.JSONDecodeError, TypeError):
                return

    visit(value)
    return list(dict.fromkeys(run_ids)), [item for item in failures if item]


class PlanStepTracker:
    """Start and finish one planned delegate step with exact memory updates."""

    def __init__(
        self,
        *,
        store: ProjectStore | None,
        project_id: str | None,
        plan_id: str | None,
        step_id: str | None,
        capability: PlanCapability,
    ) -> None:
        normalized_plan = str(plan_id or "").strip() or None
        normalized_step = str(step_id or "").strip() or None
        if (normalized_plan is None) != (normalized_step is None):
            raise ValueError("plan_id and step_id must be provided together")
        if normalized_plan is not None and (store is None or project_id is None):
            raise ValueError("planned delegation requires an active project")
        self.plan_id = normalized_plan
        self.step_id = normalized_step
        self.capability = capability
        self.service = (
            ProjectPlanService(store, project_id)
            if store is not None and project_id is not None and normalized_plan is not None
            else None
        )
        self.project_id = project_id

    def start(self) -> None:
        if self.service is None or self.plan_id is None or self.step_id is None:
            return
        self.service.transition_step(
            self.plan_id,
            self.step_id,
            PlanStepStatus.RUNNING,
            capability=self.capability,
        )

    def finish_worker(self, result: WorkerResult) -> None:
        if self.service is None or self.plan_id is None or self.step_id is None:
            return
        payload = asdict(result)
        outcome = parse_worker_outcome(payload)
        run_ids, nested_failures = _collect_result_metadata(payload.get("data"))
        for item in result.trace:
            if not _tracks_plan_results(str(item.get("name") or "")):
                continue
            item_run_ids, item_failures = _collect_result_metadata(item.get("output"))
            run_ids.extend(item_run_ids)
            # The completion check distinguishes corrected retries from unresolved failures.
            # Retain all run references, but do not fail a recovered task on an old error.
            if not result.data.get("completion_check"):
                nested_failures.extend(item_failures)
        run_ids = list(dict.fromkeys(run_ids))
        if outcome.status in {"failed", "partial", "cancelled", "interrupted"} or nested_failures:
            error = str(result.error or "").strip()
            if not error:
                error = "; ".join(nested_failures) or result.summary
            self.service.transition_step(
                self.plan_id,
                self.step_id,
                PlanStepStatus.FAILED,
                capability=self.capability,
                result_summary=result.summary[:800],
                result_run_ids=run_ids,
                error=error,
            )
            return
        interactions = extract_dichromatic_interactions(payload.get("data"), self.project_id)
        if self._wait_for_cell_completion(interactions, run_ids):
            return
        if outcome.status == "awaiting_user":
            self.record_run_ids(run_ids)
            self.service.transition_step(
                self.plan_id, self.step_id, PlanStepStatus.BLOCKED,
                capability=self.capability,
                blocking_reason=outcome.next_action or outcome.summary or "Waiting for user input",
            )
            return
        self.service.transition_step(
            self.plan_id,
            self.step_id,
            PlanStepStatus.COMPLETED,
            capability=self.capability,
            result_summary=result.summary[:800],
            result_run_ids=run_ids,
        )

    def record_tool_event(self, event: dict[str, Any]) -> None:
        """Attach a newly created scientific run while the step is still running."""
        if self.service is None or self.plan_id is None or self.step_id is None:
            return
        if event.get("type") != StreamEventType.TOOL_END:
            return
        if not _tracks_plan_results(str(event.get("name") or "")):
            return
        output = event.get("_raw_output", event.get("output"))
        run_ids, _failures = _collect_result_metadata(output)
        if run_ids:
            self.record_run_ids(run_ids)

    def record_run_ids(self, run_ids: list[str]) -> None:
        if self.service is None or self.plan_id is None or self.step_id is None:
            return
        normalized = [
            run_id.strip()
            for run_id in run_ids
            if isinstance(run_id, str) and run_id.strip()
        ]
        if not normalized:
            return
        self.service.transition_step(
            self.plan_id,
            self.step_id,
            PlanStepStatus.RUNNING,
            capability=self.capability,
            result_run_ids=normalized,
        )

    def finish_run(self, result: RunToolResult) -> None:
        if self.service is None or self.plan_id is None or self.step_id is None:
            return
        if result.status is RunStatus.COMPLETED and self._wait_for_cell_completion(
            extract_dichromatic_interactions(result.result, self.project_id),
            [result.run_id],
        ):
            return
        target = (
            PlanStepStatus.COMPLETED
            if result.status is RunStatus.COMPLETED
            else PlanStepStatus.FAILED
        )
        self.service.transition_step(
            self.plan_id,
            self.step_id,
            target,
            capability=self.capability,
            result_summary=str(result.result.get("summary") or "").strip()[:800],
            result_run_ids=[result.run_id],
            error=(result.error if target is PlanStepStatus.FAILED else ""),
        )

    def _wait_for_cell_completion(
        self, interactions: list[dict[str, Any]], run_ids: list[str],
    ) -> bool:
        unfinished = [item for item in interactions if item["status"] != "applied"]
        if not unfinished or self.service is None or self.plan_id is None or self.step_id is None:
            return False
        reasons = {
            "pending": "Waiting for the user to select four near-CSL cell points",
            "selected": "The user selected a near-CSL cell; strain has not been applied",
            "applying": "The selected near-CSL cell strain calculation is still in progress",
            "cancelled": "The user cancelled the near-CSL cell selection",
        }
        # BLOCKED does not attach result runs; retain preparation evidence first.
        self.record_run_ids(run_ids)
        self.service.transition_step(
            self.plan_id,
            self.step_id,
            PlanStepStatus.BLOCKED,
            capability=self.capability,
            blocking_reason="; ".join(
                f"{reasons[item['status']]}: {item['request_id']}" for item in unfinished
            )[:2000],
        )
        return True

    def fail(self, error: str) -> None:
        if self.service is None or self.plan_id is None or self.step_id is None:
            return
        self.service.transition_step(
            self.plan_id,
            self.step_id,
            PlanStepStatus.FAILED,
            capability=self.capability,
            error=error,
        )


__all__ = ["PlanStepTracker"]
