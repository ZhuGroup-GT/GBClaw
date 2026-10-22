"""Deterministic validation for LLM-proposed Memory V2 fact mutations."""

from __future__ import annotations

import json
from typing import Any

from project_memory.extraction.keys import canonical_lookup_key
from extensions import fact_memory_spec
from project_memory.publication.contracts import validate_source_publication
from project_memory.models import (
    FACT_KINDS,
    FactEvidenceKind,
    FactMutation,
    FactMutationAction,
    FactRecord,
    FactStatus,
    RunRecord,
    RunStatus,
)
from project_workspace.chat.models import TurnRecord


MAX_FACT_VALUE_CHARS = 16_000
MAX_FACT_VALUE_DEPTH = 8


class MemoryPolicyError(ValueError):
    """Raised when an extracted mutation is unsafe or unsupported."""


def _normalized_evidence(value: str) -> str:
    return " ".join(value.split()).casefold()


def _json_depth(value: Any, *, depth: int = 0) -> int:
    if depth > MAX_FACT_VALUE_DEPTH:
        return depth
    if isinstance(value, dict):
        if not value:
            return depth + 1
        return max(_json_depth(item, depth=depth + 1) for item in value.values())
    if isinstance(value, list):
        if not value:
            return depth + 1
        return max(_json_depth(item, depth=depth + 1) for item in value)
    return depth + 1


def _validate_value(value: dict[str, Any]) -> None:
    try:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise MemoryPolicyError("Fact value must contain finite JSON data") from exc
    if len(serialized) > MAX_FACT_VALUE_CHARS:
        raise MemoryPolicyError(
            f"fact value exceeds {MAX_FACT_VALUE_CHARS} serialized characters"
        )
    if _json_depth(value) > MAX_FACT_VALUE_DEPTH:
        raise MemoryPolicyError(
            f"fact value exceeds maximum nesting depth {MAX_FACT_VALUE_DEPTH}"
        )


def _validate_user_evidence(mutation: FactMutation, user_turn: TurnRecord) -> None:
    if mutation.evidence_kind is not FactEvidenceKind.USER_EXPLICIT:
        raise MemoryPolicyError(
            f"{mutation.kind} requires explicit evidence from the user"
        )
    excerpt = _normalized_evidence(mutation.evidence_excerpt)
    if not excerpt:
        raise MemoryPolicyError("user-explicit memory requires a non-empty evidence excerpt")
    if excerpt not in _normalized_evidence(user_turn.content):
        raise MemoryPolicyError(
            "memory evidence excerpt is absent from the final user content"
        )


def validate_mutations(
    *,
    mutations: list[FactMutation],
    user_turn: TurnRecord,
    assistant_turn: TurnRecord,
    allowed_runs: dict[str, RunRecord],
    existing_facts: dict[tuple[str, str], FactRecord],
    omitted_fact_keys: set[tuple[str, str]] | None = None,
) -> list[FactMutation]:
    """Validate a complete mutation batch or raise without partial acceptance."""
    if assistant_turn.role != "assistant" or user_turn.role != "user":
        raise MemoryPolicyError("memory extraction requires one user/assistant exchange")
    if len(mutations) > 20:
        raise MemoryPolicyError("memory extraction produced more than 20 mutations")

    omitted = omitted_fact_keys or set()
    validated: list[FactMutation] = []
    touched: set[tuple[str, str]] = set()

    for mutation in mutations:
        if mutation.action is FactMutationAction.NOOP:
            continue
        if mutation.kind is None or mutation.kind not in FACT_KINDS:
            raise MemoryPolicyError("non-noop mutation requires a supported fact kind")
        if not mutation.lookup_key:
            raise MemoryPolicyError("non-noop mutation requires lookup_key")

        try:
            requested_key = mutation.lookup_key.strip()
            saved = existing_facts.get((mutation.kind, requested_key))
            if (
                mutation.action is FactMutationAction.RETRACT
                and fact_memory_spec(mutation.kind).writer == "run_publication"
                and saved is not None
                and saved.kind == mutation.kind
                and saved.lookup_key == requested_key
            ):
                # A registered tool owns its key vocabulary, including batch
                # items and shared geometry slots. Explicit user retraction
                # targets an existing identity; it never creates a new slot or
                # grants chat extraction the tool's upsert permissions.
                lookup_key = requested_key
            else:
                lookup_key = canonical_lookup_key(
                    kind=mutation.kind,
                    requested_key=mutation.lookup_key,
                    source_run_id=mutation.source_run_id,
                )
        except ValueError as exc:
            raise MemoryPolicyError(str(exc)) from exc
        key = (mutation.kind, lookup_key)
        if key in touched:
            raise MemoryPolicyError(
                f"multiple mutations target the same fact slot: {lookup_key}"
            )
        touched.add(key)

        if mutation.source_run_id is not None:
            if mutation.source_run_id not in allowed_runs:
                raise MemoryPolicyError(
                    f"mutation references an unprovided run: {mutation.source_run_id}"
                )

        current = existing_facts.get(key)
        if current is not None and key in omitted:
            raise MemoryPolicyError(
                f"cannot modify omitted fact candidate without its full value: {lookup_key}"
            )

        if mutation.action is FactMutationAction.RETRACT:
            if current is None or current.status is not FactStatus.ACTIVE:
                raise MemoryPolicyError(
                    f"cannot retract a missing or inactive fact: {lookup_key}"
                )
            if mutation.expected_version != current.version:
                raise MemoryPolicyError(
                    f"fact version mismatch for {lookup_key}: "
                    f"expected {current.version}, got {mutation.expected_version}"
                )
            _validate_user_evidence(mutation, user_turn)
            validated.append(mutation.model_copy(update={"lookup_key": lookup_key}))
            continue

        if mutation.value is None:
            raise MemoryPolicyError("upsert mutation requires a fact value")
        _validate_value(mutation.value)

        source_run = allowed_runs.get(str(mutation.source_run_id or ""))
        if source_run is not None and source_run.result.get("memory_publication"):
            reserved = {
                (item.get("kind"), item.get("lookup_key"))
                for item in source_run.result.get("memory_fact_refs", [])
                if isinstance(item, dict)
            }
            if source_run.result.get("conclusion_lookup_key"):
                reserved.add(("scientific_conclusion", source_run.result["conclusion_lookup_key"]))
            if key in reserved:
                raise MemoryPolicyError(
                    f"{lookup_key} is reserved for deterministic run publication; "
                    "chat extraction cannot replace a pending or published tool fact"
                )

        resolved_mutation = mutation
        if current is None:
            if mutation.expected_version is not None:
                raise MemoryPolicyError(
                    f"new fact {lookup_key} must not specify expected_version"
                )
        elif (
            current.status is FactStatus.RETRACTED
            and mutation.expected_version is None
        ):
            # Retracted facts are absent from normal extractor search. An
            # explicit, policy-valid upsert can reactivate the stable slot
            # without requiring the model to guess a hidden tombstone version.
            resolved_mutation = mutation.model_copy(
                update={"expected_version": current.version}
            )
        elif mutation.expected_version != current.version:
            raise MemoryPolicyError(
                f"fact version mismatch for {lookup_key}: "
                f"expected {current.version}, got {mutation.expected_version}"
            )
        elif current.evidence_kind is FactEvidenceKind.DETERMINISTIC_WORKFLOW:
            raise MemoryPolicyError(
                f"chat extraction cannot overwrite deterministic fact {lookup_key}"
            )

        policy = fact_memory_spec(mutation.kind).extraction_policy
        if policy == "user_explicit":
            _validate_user_evidence(resolved_mutation, user_turn)
        elif policy == "deterministic":
            raise MemoryPolicyError(
                f"{mutation.kind} must be written by a deterministic workflow"
            )
        elif policy == "completed_run":
            if mutation.evidence_kind is not FactEvidenceKind.COMPLETED_RUN:
                raise MemoryPolicyError(
                    "scientific_conclusion requires completed_run evidence"
                )
            run = allowed_runs.get(str(mutation.source_run_id or ""))
            if run is None or run.status is not RunStatus.COMPLETED:
                raise MemoryPolicyError(
                    "scientific_conclusion requires a completed source run"
                )
            try:
                validate_source_publication(run, run.result, [])
            except ValueError as exc:
                raise MemoryPolicyError(str(exc)) from exc
        else:
            raise MemoryPolicyError(
                f"unsupported chat-extracted fact kind: {mutation.kind}"
            )

        if (
            current is not None
            and current.status is FactStatus.ACTIVE
            and current.value == resolved_mutation.value
            and current.source_run_id == resolved_mutation.source_run_id
            and current.evidence_kind == resolved_mutation.evidence_kind
            and current.source_turn_id == assistant_turn.turn_id
        ):
            continue
        validated.append(
            resolved_mutation.model_copy(update={"lookup_key": lookup_key})
        )

    return validated
