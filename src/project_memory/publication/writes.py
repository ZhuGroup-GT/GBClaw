"""Registered, policy-checked entry points for durable semantic fact writes.

Scientific publication and planning use atomic versioned fact batches. Chat
extraction uses its evidence policy before applying mutations. Domain services
still decide what facts mean and when approval is valid; this layer never retries
an approval or promotes a proposed fact to a scientific result.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from project_memory.extraction.policy import MemoryPolicyError, validate_mutations
from project_memory.publication.contracts import validate_source_publication
from project_workspace.chat.repository import ChatRepository
from project_workspace.models import utc_now_iso
from extensions import memory_writer_spec
from project_memory.models import (
    FactMutation,
    FactMutationAction,
    FactMutationApplyResult,
    FactRecord,
    RunRecord,
    RunStatus,
)
from project_workspace.chat.models import TurnRecord, TurnStatus

if TYPE_CHECKING:
    from project_memory.storage.repository import MemoryRepository


def commit_facts(
    memory: MemoryRepository,
    facts: list[FactRecord],
    *,
    expected_versions: dict[tuple[str, str], int | None],
    writer: str,
    skip_unchanged: bool = False,
) -> list[FactRecord]:
    """Validate a registered deterministic batch, then commit it once atomically.

    ``expected_versions`` is mandatory, including for inserts. A conflict is
    returned to the caller unchanged so its domain policy can decide whether a
    fresh operation is valid. The shared writer never replays an old approval.
    """
    spec = memory_writer_spec(writer)
    if spec.mutation_mode != "cas":
        raise MemoryPolicyError(f"Memory writer {writer!r} requires evidence-validated extraction")
    keys = [(fact.kind, fact.lookup_key) for fact in facts]
    if len(set(keys)) != len(keys) or set(keys) != set(expected_versions):
        raise MemoryPolicyError("expected_versions must cover each distinct fact key exactly")
    source_runs: dict[str, RunRecord] = {}
    for fact in facts:
        if fact.kind not in spec.fact_kinds:
            raise MemoryPolicyError(f"Memory writer {writer!r} cannot write {fact.kind!r}")
        if fact.evidence_kind.value not in spec.evidence_kinds:
            raise MemoryPolicyError(f"Memory writer {writer!r} cannot use {fact.evidence_kind.value!r} evidence")
        if not fact.fact_id or not fact.lookup_key or len(fact.lookup_key) > 240:
            raise MemoryPolicyError("Memory facts require a stable ID and lookup key of at most 240 characters")
        # Domain values may be large, but must remain finite JSON data.
        json.dumps(fact.value, ensure_ascii=False, allow_nan=False)
        expected = expected_versions[(fact.kind, fact.lookup_key)]
        if expected is not None and (type(expected) is not int or expected < 1):
            raise MemoryPolicyError("Expected fact versions must be positive integers or None")
        if writer == "run_publication":
            if not fact.source_run_id:
                raise MemoryPolicyError("Run publication requires a source run for every fact")
            run = source_runs.get(fact.source_run_id)
            if run is None:
                run = memory.get_run(fact.source_run_id)
                source_runs[run.run_id] = run
            if run.status is not RunStatus.COMPLETED:
                raise MemoryPolicyError("Run publication requires a completed source run")
    for run in source_runs.values():
        try:
            validate_source_publication(run, run.result,
                                        [fact for fact in facts if fact.source_run_id == run.run_id])
        except ValueError as exc:
            raise MemoryPolicyError(str(exc)) from exc
    return memory.compare_and_swap_facts(
        facts, expected_versions=expected_versions, skip_unchanged=skip_unchanged,
    )


def apply_extraction(
    memory: MemoryRepository,
    mutations: list[FactMutation],
    *,
    user_turn: TurnRecord,
    assistant_turn: TurnRecord,
    allowed_runs: dict[str, RunRecord],
    existing_facts: dict[tuple[str, str], FactRecord],
    omitted_fact_keys: set[tuple[str, str]] | None = None,
    writer: str = "conversation_extraction",
    now: str | None = None,
) -> FactMutationApplyResult:
    """Validate user evidence and current versions, then apply the whole exchange.

    Exact lookups cover targets omitted from bounded recall. The existing chat
    policy remains authoritative for canonical keys, retractions, completed-run
    evidence, and the prohibition on overwriting deterministic workflow facts.
    """
    spec = memory_writer_spec(writer)
    if spec.mutation_mode != "extraction":
        raise MemoryPolicyError(f"Memory writer {writer!r} requires a versioned fact batch")
    if (
        user_turn.status is not TurnStatus.COMPLETED
        or assistant_turn.status is not TurnStatus.COMPLETED
        or user_turn.thread_id != assistant_turn.thread_id
        or assistant_turn.sequence != user_turn.sequence + 1
    ):
        raise MemoryPolicyError("Memory extraction requires one completed, consecutive exchange")
    current_facts = dict(existing_facts)
    for mutation in mutations:
        if mutation.action is FactMutationAction.NOOP:
            continue
        if mutation.kind not in spec.fact_kinds:
            raise MemoryPolicyError(f"Memory writer {writer!r} cannot write {mutation.kind!r}")
        evidence = mutation.evidence_kind.value if mutation.evidence_kind else None
        if evidence not in spec.evidence_kinds:
            raise MemoryPolicyError(f"Memory writer {writer!r} cannot use {evidence!r} evidence")
        if not mutation.lookup_key:
            continue
        key = (mutation.kind, mutation.lookup_key.strip())
        if key not in current_facts:
            rows = memory.query_facts(
                kind=mutation.kind, lookup_key=key[1], include_retracted=True, limit=1,
            )
            if rows:
                current_facts[key] = rows[0]
    chat = ChatRepository(memory.project_root)
    source_order = chat.get_turn_order(assistant_turn.turn_id)
    if source_order is None:
        raise MemoryPolicyError("Memory source turn has no durable project order")
    fresh_mutations = []
    for mutation in mutations:
        current = current_facts.get((mutation.kind, (mutation.lookup_key or "").strip()))
        current_order = chat.get_turn_order(current.source_turn_id) if current and current.source_turn_id else None
        if current_order is not None and current_order > source_order:
            continue
        fresh_mutations.append(mutation)
    validated = validate_mutations(
        mutations=fresh_mutations,
        user_turn=user_turn,
        assistant_turn=assistant_turn,
        allowed_runs=allowed_runs,
        existing_facts=current_facts,
        omitted_fact_keys=omitted_fact_keys,
    )
    if not validated:
        revision = memory.latest_semantic_revision()
        return FactMutationApplyResult(
            noop_count=len(mutations),
            semantic_revision_before=revision,
            semantic_revision_after=revision,
        )
    result = memory.apply_fact_mutations(
        validated, source_turn_id=assistant_turn.turn_id, at=now or utc_now_iso(),
    )
    return result.model_copy(update={
        "noop_count": result.noop_count + len(mutations) - len(validated),
    })
