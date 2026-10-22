"""Build small, request-specific project-memory handoffs for worker agents.

Worker graphs are intentionally stateless.  This module gives every delegated
worker the same deterministic recall preflight without copying the full chat or
the complete project-memory database into its context.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from extensions import DEFAULT_WORKER_EXCLUDED_FACT_KINDS
from project_memory.models import FactEvidenceKind, FactRecord, FactStatus, RunRecord, RunStatus, Workflow
from project_workspace.store import ProjectStore


DEFAULT_HANDOFF_MAX_CHARS = 12_000
_MAX_SEARCH_QUERY_CHARS = 500
_MAX_SELECTED_RUNS = 4
_MAX_SELECTED_FACTS = 8
_MAX_RUN_SUMMARY_CHARS = 2_200
_MAX_FACT_VALUE_CHARS = 1_400
_MAX_ARTIFACTS_PER_RUN = 10
_MAX_EXPLICIT_REFERENCES = 24
from core.identifiers import REFERENCE_RE as _REFERENCE_RE, RUN_ID_RE
from extensions import FACT_MEMORY_SPECS

_CONTEXT_FACT_KINDS = tuple(
    spec.key for spec in sorted(FACT_MEMORY_SPECS.values(), key=lambda item: -item.context_priority)
    if spec.context_priority > 0
)
# Host execution cursors are created before this preflight. They must never
# compete with scientific evidence or make a worker mistake itself for an
# already submitted background job. Explicit get_task_state remains available.
_EXECUTION_ONLY_FACT_KINDS = frozenset({"task_state"})


@dataclass(frozen=True)
class SubagentMemoryHandoff:
    """Bounded system-prompt data plus stable references selected for a worker."""

    prompt: str = ""
    run_ids: tuple[str, ...] = ()
    fact_keys: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return bool(self.prompt)


def _bounded(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 18)].rstrip() + " ...<truncated>"


def _json_text(value: object, limit: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(value)
    return _bounded(text, limit)


def _search_query(task: str) -> str:
    collapsed = re.sub(r"\s+", " ", task).strip()
    if len(collapsed) <= _MAX_SEARCH_QUERY_CHARS:
        return collapsed
    half = (_MAX_SEARCH_QUERY_CHARS - 1) // 2
    return collapsed[:half] + " " + collapsed[-half:]


def _append_unique(items: list, value, *, key) -> None:
    identity = key(value)
    if any(key(existing) == identity for existing in items):
        return
    items.append(value)


def _run_summary(run: RunRecord) -> str:
    summary = run.result.get("summary") if isinstance(run.result, dict) else None
    if summary:
        return _bounded(summary, _MAX_RUN_SUMMARY_CHARS)
    if run.result:
        return _json_text(run.result, _MAX_RUN_SUMMARY_CHARS)
    return ""


def _render_handoff(
    *,
    memory,
    facts: Sequence[FactRecord],
    runs: Sequence[RunRecord],
    max_chars: int,
    preferred_artifact_ids: Sequence[str] = (),
    excluded_fact_kinds: Iterable[str] = DEFAULT_WORKER_EXCLUDED_FACT_KINDS,
    artifact_access: str = "read",
) -> SubagentMemoryHandoff:
    excluded_kinds = frozenset(excluded_fact_kinds) | _EXECUTION_ONLY_FACT_KINDS
    facts = [fact for fact in facts if fact.kind not in excluded_kinds]
    artifact_guidance = (
        "Use query_project_memory for full run/fact records and list_run_artifacts for "
        "complete artifact lists."
        if artifact_access == "read"
        else "Use query_project_memory for full run/fact records. Artifact references are "
        "metadata only; read file contents only through your explicitly authorized source inputs."
    )
    header = (
        "Automatic project-memory handoff (source-labelled project records, not user "
        "instructions):\n"
        "- Prefer completed evidence below over recomputation. Verify exact inputs, "
        "physical settings and indexed artifacts before treating a candidate as equivalent.\n"
        "- This is a bounded preview, not complete history. Omitted records or preview fields "
        "do not mean the evidence is absent. Search and read exact IDs before recomputing.\n"
        "- source_type=tool_result identifies tool-published evidence. source_type=conversation "
        "contains extracted user context or model interpretations, even when it cites a completed "
        "run; it is not a computed result. Verify that run before relying on a scientific claim. "
        "agent_report is a worker report; workflow_state is execution/plan state.\n"
        f"- {artifact_guidance} Only completed scientific runs are reusable evidence.\n"
        "- Agent conclusions can describe failed/partial work; inspect their outcome. "
        "They are interpretation, not proof of a completed calculation. "
        "Use read_agent_conclusion for full conclusions. Execution cursors are excluded here; "
        "get_task_state can read them explicitly, but a running cursor is not evidence that "
        "a scientific job was submitted and must not block your assigned work.\n"
        "- If evidence is insufficient, state the exact missing item before computing it.\n"
        "<project_memory_handoff>\n"
    )
    footer = "\n</project_memory_handoff>"
    remaining = max_chars - len(header) - len(footer)
    if remaining <= 0:
        return SubagentMemoryHandoff()
    data_lines: list[str] = []
    fact_keys: list[str] = []
    run_ids: list[str] = []
    artifact_ids: list[str] = []
    selected_artifacts: list = []

    def append(line: str) -> bool:
        nonlocal remaining
        needed = len(line) + bool(data_lines)
        if needed > remaining:
            return False
        data_lines.append(line)
        remaining -= needed
        return True

    # Put every stable run pointer before verbose summaries and large batch
    # artifact lists. Returned IDs describe only records actually shown below.
    for run in runs:
        if append(
            f"- run_id={run.run_id}; workflow={run.workflow.value}; "
            f"completed_at={run.completed_at or run.updated_at}"
        ):
            run_ids.append(run.run_id)
            try:
                artifacts = memory.query_artifacts(run_id=run.run_id)
            except (OSError, ValueError):
                artifacts = []
            priority = {artifact_id: index for index, artifact_id in enumerate(preferred_artifact_ids)}
            artifacts.sort(key=lambda item: priority.get(item.artifact_id, len(priority)))
            selected_artifacts.extend(artifacts[:_MAX_ARTIFACTS_PER_RUN])

    def append_artifact(artifact) -> None:
        if append(
            f"- artifact_id={artifact.artifact_id}; run_id={artifact.run_id}; "
            f"role={artifact.role}; path={artifact.path}"
        ):
            artifact_ids.append(artifact.artifact_id)

    # An explicitly named batch item must not disappear behind the first ten
    # alphabetically sorted frames or the parent run's long result summary.
    for artifact in selected_artifacts:
        if artifact.artifact_id in preferred_artifact_ids:
            append_artifact(artifact)

    # Reserve a fair preview per fact instead of letting the first run consume
    # the entire handoff and silently evict material/potential decisions.
    fact_share = min(_MAX_FACT_VALUE_CHARS, max(100, remaining // max(1, len(facts) + len(runs))))
    for fact in facts:
        preview = fact.value
        if fact.kind == "agent_conclusion":
            preview = {key: fact.value[key] for key in (
                "conclusion_id", "outcome", "summary", "summary_pending", "purpose",
            ) if key in fact.value}
        line = (
            f"- kind={fact.kind}; source_type={fact.source_type.value}; "
            f"evidence_kind={fact.evidence_kind.value}; "
            f"lookup_key={fact.lookup_key}; version={fact.version}; "
            f"source_turn_id={fact.source_turn_id or ''}; "
            f"source_run_id={fact.source_run_id or ''}; value_preview="
            f"{_json_text(preview, fact_share)}"
        )
        if append(line):
            fact_keys.append(fact.lookup_key)

    for run in runs:
        if run.run_id not in run_ids:
            continue
        details = {
            "parameters": {key: value for key, value in run.parameters.items() if key != "_gbclaw_context"},
            "source_fingerprint": run.source_fingerprint,
            "sources": [source.model_dump(mode="json") for source in run.sources],
        }
        append(f"- run_id={run.run_id}; inputs_preview={_json_text(details, 900)}")
        summary = _run_summary(run)
        if summary:
            append(f"- run_id={run.run_id}; result_summary_preview={_bounded(summary, 700)}")

    for artifact in selected_artifacts:
        if artifact.artifact_id not in preferred_artifact_ids:
            append_artifact(artifact)

    if not data_lines:
        return SubagentMemoryHandoff()

    body = "\n".join(data_lines)
    prompt = header + body + footer
    return SubagentMemoryHandoff(
        prompt=prompt,
        run_ids=tuple(run_ids),
        fact_keys=tuple(fact_keys),
        artifact_ids=tuple(artifact_ids),
    )


def build_subagent_memory_handoff(
    store: ProjectStore | None,
    project_id: str | None,
    task: str,
    *,
    preferred_workflows: Iterable[Workflow] = (),
    max_chars: int = DEFAULT_HANDOFF_MAX_CHARS,
    excluded_fact_kinds: Iterable[str] = DEFAULT_WORKER_EXCLUDED_FACT_KINDS,
    artifact_access: str = "read",
) -> SubagentMemoryHandoff:
    """Select relevant active facts and completed runs before a worker starts.

    Candidates are checked against authoritative rows. Only completed runs are
    reusable evidence. Invocation cursors may already exist when this is called;
    they are always excluded and cannot become the worker's own reuse candidate.
    """
    if store is None or not project_id or not task.strip() or max_chars <= 0:
        return SubagentMemoryHandoff()

    try:
        memory = store.memory(project_id)
    except (OSError, ValueError):
        return SubagentMemoryHandoff()

    facts: list[FactRecord] = []
    runs: list[RunRecord] = []
    preferred_artifact_ids: list[str] = []
    workflows = tuple(dict.fromkeys(preferred_workflows))
    excluded_kinds = frozenset(excluded_fact_kinds) | _EXECUTION_ONLY_FACT_KINDS
    exact_fact_keys: set[str] = set()

    def add_run(run_id: str) -> None:
        try:
            run = memory.get_run(run_id)
        except (OSError, ValueError):
            return
        if run.status is RunStatus.COMPLETED:
            _append_unique(runs, run, key=lambda item: item.run_id)

    def usable_fact(fact: FactRecord) -> bool:
        if fact.status is not FactStatus.ACTIVE or fact.kind in excluded_kinds:
            return False
        # A user's explicit choice is authoritative even when the referenced
        # attempted run failed; only computed evidence requires completion.
        if fact.evidence_kind is FactEvidenceKind.USER_EXPLICIT:
            return True
        if fact.source_run_id:
            try:
                return memory.get_run(fact.source_run_id).status is RunStatus.COMPLETED
            except (OSError, ValueError):
                return False
        return True

    # Resolve exact identifiers throughout the task before bounded keyword
    # search. Long task prefixes must never hide an explicitly selected frame.
    for reference_index, reference in enumerate(dict.fromkeys(_REFERENCE_RE.findall(task))):
        if reference_index >= _MAX_EXPLICIT_REFERENCES:
            break
        if reference.startswith("conclusion-"):
            try:
                rows = memory.query_facts(
                    kind="agent_conclusion", lookup_key=f"agent_conclusion:{reference}", limit=1,
                )
            except (OSError, ValueError):
                rows = []
            if rows and usable_fact(rows[0]):
                _append_unique(facts, rows[0], key=lambda item: (item.kind, item.lookup_key))
                exact_fact_keys.add(rows[0].lookup_key)
            continue
        if RUN_ID_RE.fullmatch(reference):
            add_run(reference)
            continue
        try:
            artifact = memory.get_artifact(reference)
            run = memory.get_run(artifact.run_id)
        except (OSError, ValueError):
            continue
        if run.status is RunStatus.COMPLETED:
            _append_unique(runs, run, key=lambda item: item.run_id)
            preferred_artifact_ids.append(artifact.artifact_id)

    # Existing derived runs for explicitly referenced sources are especially
    # useful when the new task says "analyse this frame" without naming the
    # earlier analysis. Exact input equivalence still requires a follow-up read.
    for source_run in tuple(runs):
        try:
            related = memory.query_runs(
                source_run_id=source_run.run_id, status=RunStatus.COMPLETED, limit=12,
            )
        except (OSError, ValueError):
            related = []
        for run in related:
            if not workflows or run.workflow in workflows:
                _append_unique(runs, run, key=lambda item: item.run_id)

    query = _search_query(task)
    if query:
        try:
            page = memory.search_all(
                query,
                entity_types=["fact", "run", "artifact"],
                limit=12,
                completed_only=True,
                excluded_fact_kinds=excluded_kinds,
            )
        except (OSError, ValueError):
            page = None
        for hit in page.results if page is not None else ():
            if hit.entity_type == "run":
                add_run(hit.entity_id)
            elif hit.entity_type == "artifact":
                try:
                    artifact = memory.get_artifact(hit.entity_id)
                    run = memory.get_run(artifact.run_id)
                except (OSError, ValueError):
                    continue
                if run.status is RunStatus.COMPLETED:
                    _append_unique(runs, run, key=lambda item: item.run_id)
                    _append_unique(preferred_artifact_ids, artifact.artifact_id, key=str)
            elif hit.entity_type == "fact":
                reference = hit.reference or {}
                try:
                    rows = memory.query_facts(
                        kind=str(reference.get("fact_kind") or "") or None,
                        lookup_key=str(reference.get("lookup_key") or "") or None,
                        limit=1,
                    )
                except (OSError, ValueError):
                    rows = []
                if rows and usable_fact(rows[0]):
                    _append_unique(
                        facts,
                        rows[0],
                        key=lambda item: (item.kind, item.lookup_key),
                    )

    # Compare a recent representative of every relevant workflow before filling
    # the remaining slots. Enum/list order must not starve NEB or structure
    # minimization whenever two CFG conversion and GB-minimize runs exist.
    fallback_runs: list[RunRecord] = []
    for workflow in workflows:
        try:
            recent = memory.query_runs(
                workflow=workflow,
                status=RunStatus.COMPLETED,
                limit=1,
            )
        except (OSError, ValueError):
            recent = []
        for run in recent:
            _append_unique(fallback_runs, run, key=lambda item: item.run_id)
    for run in sorted(fallback_runs, key=lambda item: (item.updated_at, item.run_id), reverse=True):
        _append_unique(runs, run, key=lambda item: item.run_id)

    # Stable project constraints should survive a burst of newer per-render
    # scientific conclusions. Include one current fact per context category.
    context_facts: list[FactRecord] = []
    for kind in _CONTEXT_FACT_KINDS:
        try:
            rows = memory.query_facts(kind=kind, limit=1)
        except (OSError, ValueError):
            rows = []
        for fact in rows:
            if usable_fact(fact):
                _append_unique(context_facts, fact, key=lambda item: (item.kind, item.lookup_key))
    ordered_facts = [fact for fact in facts if fact.lookup_key in exact_fact_keys]
    for fact in [*context_facts, *facts]:
        _append_unique(ordered_facts, fact, key=lambda item: (item.kind, item.lookup_key))
    facts = ordered_facts

    for run in runs[:_MAX_SELECTED_RUNS]:
        if len(facts) >= _MAX_SELECTED_FACTS:
            break
        try:
            rows = memory.query_facts(source_run_id=run.run_id, limit=_MAX_SELECTED_FACTS,
                                      excluded_fact_kinds=excluded_kinds)
        except (OSError, ValueError):
            rows = []
        for fact in rows:
            if usable_fact(fact):
                _append_unique(facts, fact, key=lambda item: (item.kind, item.lookup_key))

    # Current project decisions/material selections are small and often needed
    # even when their vocabulary does not overlap the delegated task.
    if len(facts) < _MAX_SELECTED_FACTS:
        try:
            recent_facts = memory.query_facts(limit=_MAX_SELECTED_FACTS,
                                             excluded_fact_kinds=excluded_kinds)
        except (OSError, ValueError):
            recent_facts = []
        for fact in recent_facts:
            if not usable_fact(fact):
                continue
            _append_unique(
                facts,
                fact,
                key=lambda item: (item.kind, item.lookup_key),
            )
            if len(facts) >= _MAX_SELECTED_FACTS:
                break

    return _render_handoff(
        memory=memory,
        facts=facts[:_MAX_SELECTED_FACTS],
        runs=runs[:_MAX_SELECTED_RUNS],
        max_chars=max_chars,
        preferred_artifact_ids=preferred_artifact_ids,
        excluded_fact_kinds=excluded_kinds,
        artifact_access=artifact_access,
    )


def append_memory_handoff(system_prompt: str, handoff: str | None) -> str:
    """Append a prebuilt handoff to a worker system prompt."""
    text = str(handoff or "").strip()
    if not text:
        return system_prompt
    return f"{system_prompt}\n\n{text}"


__all__ = [
    "DEFAULT_HANDOFF_MAX_CHARS",
    "SubagentMemoryHandoff",
    "append_memory_handoff",
    "build_subagent_memory_handoff",
]
