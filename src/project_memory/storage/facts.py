"""Versioned research facts with compare-and-swap writes and validated mutations."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Iterable

from project_memory.models import (
    FactEvidenceKind,
    FactMutation,
    FactMutationAction,
    FactMutationApplyResult,
    FactRecord,
    FactSourceType,
    FactStatus,
)
from project_memory.storage.changes import _mark_semantic_change
from project_memory.storage.encoding import _json_dump
from project_workspace.persistence.sqlite import connect_project_db, transaction


class FactVersionConflict(RuntimeError):
    """Raised when a fact changed after mutation extraction."""


def _fact_source_sql(prefix: str = "") -> str:
    """SQL equivalent of FactRecord.source_type, evaluated before pagination."""
    return (
        "CASE "
        f"WHEN COALESCE({prefix}source_turn_id, '') != '' "
        f"OR {prefix}evidence_kind IN ('user_explicit', 'completed_run') THEN 'conversation' "
        f"WHEN {prefix}evidence_kind = 'assistant_report' "
        f"OR {prefix}kind = 'agent_conclusion' THEN 'agent_report' "
        f"WHEN {prefix}kind IN ('task_state', 'project_plan') THEN 'workflow_state' "
        "ELSE 'tool_result' END"
    )


class FactStoreMixin:
    def upsert_fact(self, fact: FactRecord) -> FactRecord:
        """Low-level storage upsert for fixtures and explicit maintenance.

        Tool and agent integrations use RunService or memory_writes so ownership,
        source evidence and optimistic versions are validated before committing.
        """
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                value_json = _json_dump(fact.value)
                existing = conn.execute(
                    "SELECT * FROM facts "
                    "WHERE kind = ? AND lookup_key = ?",
                    (fact.kind, fact.lookup_key),
                ).fetchone()
                changed = (
                    existing is None
                    or existing["value_json"] != value_json
                    or existing["status"] != fact.status.value
                    or existing["source_run_id"] != fact.source_run_id
                    or existing["evidence_kind"] != fact.evidence_kind.value
                )
                if changed:
                    if existing is None:
                        actual_fact_id = fact.fact_id
                        conn.execute(
                            """
                            INSERT INTO facts (
                                fact_id, kind, lookup_key, value_json, status,
                                version, source_turn_id, source_run_id,
                                evidence_kind, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                fact.fact_id,
                                fact.kind,
                                fact.lookup_key,
                                value_json,
                                fact.status.value,
                                fact.version,
                                fact.source_turn_id,
                                fact.source_run_id,
                                fact.evidence_kind.value,
                                fact.created_at,
                                fact.updated_at,
                            ),
                        )
                    else:
                        actual_fact_id = str(existing["fact_id"])
                        conn.execute(
                            """
                            UPDATE facts
                            SET value_json = ?, status = ?, version = version + 1,
                                source_turn_id = ?, source_run_id = ?,
                                evidence_kind = ?, updated_at = ?
                            WHERE fact_id = ?
                            """,
                            (
                                value_json,
                                fact.status.value,
                                fact.source_turn_id,
                                fact.source_run_id,
                                fact.evidence_kind.value,
                                fact.updated_at,
                                actual_fact_id,
                            ),
                        )
                    self._upsert_fact_search_document(conn, actual_fact_id)
                    _mark_semantic_change(
                        conn,
                        entity_type="fact",
                        entity_id=actual_fact_id,
                        changed_at=fact.updated_at,
                    )
            rows = self.query_facts(
                kind=fact.kind,
                lookup_key=fact.lookup_key,
                include_retracted=True,
                limit=1,
            )
            if not rows:
                raise RuntimeError("Fact upsert did not produce a row")
            return rows[0]
        finally:
            conn.close()

    def compare_and_swap_facts(
        self,
        facts: list[FactRecord],
        *,
        expected_versions: dict[tuple[str, str], int | None],
        skip_unchanged: bool = False,
    ) -> list[FactRecord]:
        """Atomically replace deterministic facts using optimistic versions.

        An expected version of ``None`` means that the row must not exist. This
        is used by project-plan updates so the full plan and active-plan pointer
        cannot diverge under concurrent delegate completions.
        ``skip_unchanged`` checks every supplied version inside the transaction
        but keeps exact publication retries from creating new fact versions.
        """
        if not facts:
            return []
        keys = [(fact.kind, fact.lookup_key) for fact in facts]
        if len(set(keys)) != len(keys):
            raise ValueError("compare_and_swap_facts received duplicate fact keys")
        if set(keys) != set(expected_versions):
            raise ValueError("expected_versions must cover every fact key exactly")

        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                rows: dict[tuple[str, str], sqlite3.Row | None] = {}
                for key in keys:
                    row = conn.execute(
                        "SELECT * FROM facts WHERE kind = ? AND lookup_key = ?",
                        key,
                    ).fetchone()
                    rows[key] = row
                    expected = expected_versions[key]
                    if expected is None:
                        if row is not None:
                            raise FactVersionConflict(
                                f"Fact was created concurrently: {key[1]}"
                            )
                    elif row is None or int(row["version"]) != expected:
                        actual = "missing" if row is None else str(row["version"])
                        raise FactVersionConflict(
                            f"Fact version changed for {key[1]}: "
                            f"expected {expected}, got {actual}"
                        )

                for fact in facts:
                    key = (fact.kind, fact.lookup_key)
                    existing = rows[key]
                    value_json = _json_dump(fact.value)
                    if skip_unchanged and existing is not None and (
                        str(existing["fact_id"]) == fact.fact_id
                        and json.loads(existing["value_json"]) == fact.value
                        and existing["status"] == fact.status.value
                        and existing["source_turn_id"] == fact.source_turn_id
                        and existing["source_run_id"] == fact.source_run_id
                        and existing["evidence_kind"] == fact.evidence_kind.value
                    ):
                        continue
                    if existing is None:
                        actual_fact_id = fact.fact_id
                        conn.execute(
                            """
                            INSERT INTO facts (
                                fact_id, kind, lookup_key, value_json, status,
                                version, source_turn_id, source_run_id,
                                evidence_kind, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                fact.fact_id,
                                fact.kind,
                                fact.lookup_key,
                                value_json,
                                fact.status.value,
                                fact.version,
                                fact.source_turn_id,
                                fact.source_run_id,
                                fact.evidence_kind.value,
                                fact.created_at,
                                fact.updated_at,
                            ),
                        )
                    else:
                        actual_fact_id = str(existing["fact_id"])
                        conn.execute(
                            """
                            UPDATE facts
                            SET value_json = ?, status = ?, version = version + 1,
                                source_turn_id = ?, source_run_id = ?,
                                evidence_kind = ?, updated_at = ?
                            WHERE fact_id = ?
                            """,
                            (
                                value_json,
                                fact.status.value,
                                fact.source_turn_id,
                                fact.source_run_id,
                                fact.evidence_kind.value,
                                fact.updated_at,
                                actual_fact_id,
                            ),
                        )
                    self._upsert_fact_search_document(conn, actual_fact_id)
                    _mark_semantic_change(
                        conn,
                        entity_type="fact",
                        entity_id=actual_fact_id,
                        changed_at=fact.updated_at,
                    )

            resolved: list[FactRecord] = []
            for fact in facts:
                rows = self.query_facts(
                    kind=fact.kind,
                    lookup_key=fact.lookup_key,
                    include_retracted=True,
                    limit=1,
                )
                if not rows:
                    raise RuntimeError(
                        f"Fact compare-and-swap did not produce {fact.lookup_key}"
                    )
                resolved.append(rows[0])
            return resolved
        finally:
            conn.close()

    def apply_fact_mutations(
        self,
        mutations: list[FactMutation],
        *,
        source_turn_id: str,
        at: str,
    ) -> FactMutationApplyResult:
        """Apply one validated mutation batch atomically."""
        from project_workspace.chat.repository import ChatRepository

        chat = ChatRepository(self.project_root)
        source_order = chat.get_turn_order(source_turn_id)
        if source_order is None:
            raise ValueError("Memory source turn has no durable project order")
        conn = connect_project_db(self.db_path)
        changed_ids: list[str] = []
        noop_count = 0
        try:
            with transaction(conn):
                revision_before = int(
                    conn.execute(
                        "SELECT current_revision FROM semantic_revision "
                        "WHERE singleton_id = 1"
                    ).fetchone()[0]
                )
                for mutation in mutations:
                    if mutation.action is FactMutationAction.NOOP:
                        noop_count += 1
                        continue
                    if mutation.kind is None or not mutation.lookup_key:
                        raise ValueError("validated mutation is missing kind or lookup_key")
                    current = conn.execute(
                        "SELECT * FROM facts WHERE kind = ? AND lookup_key = ?",
                        (mutation.kind, mutation.lookup_key),
                    ).fetchone()

                    # Recheck against the actual row under the memory write
                    # lock. A newer writer may have committed since validation.
                    current_order = (chat.get_turn_order(current["source_turn_id"])
                                     if current is not None and current["source_turn_id"] else None)
                    if current_order is not None and current_order > source_order:
                        noop_count += 1
                        continue

                    if mutation.action is FactMutationAction.RETRACT:
                        if current is None:
                            raise FactVersionConflict(
                                f"Fact disappeared before retract: {mutation.lookup_key}"
                            )
                        current_version = int(current["version"])
                        if mutation.expected_version != current_version:
                            raise FactVersionConflict(
                                f"Fact version changed before retract: "
                                f"{mutation.lookup_key}"
                            )
                        if current["status"] == FactStatus.RETRACTED.value:
                            noop_count += 1
                            continue
                        conn.execute(
                            """
                            UPDATE facts
                            SET status = ?, version = version + 1,
                                source_turn_id = ?, evidence_kind = ?, updated_at = ?
                            WHERE fact_id = ?
                            """,
                            (
                                FactStatus.RETRACTED.value,
                                source_turn_id,
                                (mutation.evidence_kind or FactEvidenceKind.USER_EXPLICIT).value,
                                at,
                                str(current["fact_id"]),
                            ),
                        )
                        fact_id = str(current["fact_id"])
                        self._delete_search_document(conn, "fact", fact_id)
                        _mark_semantic_change(
                            conn,
                            entity_type="fact",
                            entity_id=fact_id,
                            changed_at=at,
                        )
                        changed_ids.append(fact_id)
                        continue

                    if mutation.value is None or mutation.evidence_kind is None:
                        raise ValueError(
                            "validated upsert requires value and evidence_kind"
                        )
                    value_json = _json_dump(mutation.value)
                    if current is None:
                        if mutation.expected_version is not None:
                            raise FactVersionConflict(
                                f"Fact appeared before insert: {mutation.lookup_key}"
                            )
                        fact_id = str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"semantic-fact:{mutation.kind}:{mutation.lookup_key}",
                            )
                        )
                        conn.execute(
                            """
                            INSERT INTO facts (
                                fact_id, kind, lookup_key, value_json, status,
                                version, source_turn_id, source_run_id,
                                evidence_kind, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                            """,
                            (
                                fact_id,
                                mutation.kind,
                                mutation.lookup_key,
                                value_json,
                                FactStatus.ACTIVE.value,
                                source_turn_id,
                                mutation.source_run_id,
                                mutation.evidence_kind.value,
                                at,
                                at,
                            ),
                        )
                    else:
                        fact_id = str(current["fact_id"])
                        current_version = int(current["version"])
                        if mutation.expected_version != current_version:
                            raise FactVersionConflict(
                                f"Fact version changed before update: "
                                f"{mutation.lookup_key}"
                            )
                        unchanged = (
                            current["value_json"] == value_json
                            and current["status"] == FactStatus.ACTIVE.value
                            and current["source_run_id"] == mutation.source_run_id
                            and current["evidence_kind"] == mutation.evidence_kind.value
                            and current["source_turn_id"] == source_turn_id
                        )
                        if unchanged:
                            noop_count += 1
                            continue
                        conn.execute(
                            """
                            UPDATE facts
                            SET value_json = ?, status = ?, version = version + 1,
                                source_turn_id = ?, source_run_id = ?,
                                evidence_kind = ?, updated_at = ?
                            WHERE fact_id = ?
                            """,
                            (
                                value_json,
                                FactStatus.ACTIVE.value,
                                source_turn_id,
                                mutation.source_run_id,
                                mutation.evidence_kind.value,
                                at,
                                fact_id,
                            ),
                        )

                    self._upsert_fact_search_document(conn, fact_id)
                    _mark_semantic_change(
                        conn,
                        entity_type="fact",
                        entity_id=fact_id,
                        changed_at=at,
                    )
                    changed_ids.append(fact_id)

                revision_after = int(
                    conn.execute(
                        "SELECT current_revision FROM semantic_revision "
                        "WHERE singleton_id = 1"
                    ).fetchone()[0]
                )

            changed = [self.get_fact(fact_id) for fact_id in changed_ids]
            return FactMutationApplyResult(
                changed_facts=changed,
                noop_count=noop_count,
                semantic_revision_before=revision_before,
                semantic_revision_after=revision_after,
            )
        finally:
            conn.close()

    def query_facts(
        self,
        *,
        kind: str | None = None,
        lookup_key: str | None = None,
        source_run_id: str | None = None,
        source_type: FactSourceType | str | None = None,
        include_retracted: bool = False,
        excluded_fact_kinds: Iterable[str] = (),
        limit: int = 20,
        offset: int = 0,
    ) -> list[FactRecord]:
        if limit < 1:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        clauses: list[str] = []
        values: list[Any] = []
        if kind is not None:
            clauses.append("kind = ?")
            values.append(kind)
        if lookup_key is not None:
            clauses.append("lookup_key = ?")
            values.append(lookup_key)
        if source_run_id is not None:
            clauses.append("source_run_id = ?")
            values.append(source_run_id)
        if source_type is not None:
            clauses.append(f"({_fact_source_sql()}) = ?")
            values.append(FactSourceType(source_type).value)
        if not include_retracted:
            clauses.append("status = ?")
            values.append(FactStatus.ACTIVE.value)
        excluded_kinds = tuple(dict.fromkeys(excluded_fact_kinds))
        if excluded_kinds:
            clauses.append(f"kind NOT IN ({','.join('?' for _ in excluded_kinds)})")
            values.extend(excluded_kinds)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        conn = connect_project_db(self.db_path)
        try:
            rows = conn.execute(
                f"SELECT * FROM facts {where} ORDER BY updated_at DESC, fact_id DESC LIMIT ? OFFSET ?",
                (*values, limit, offset),
            ).fetchall()
            return [self._fact_from_row(row) for row in rows]
        finally:
            conn.close()

    def get_fact(self, fact_id: str) -> FactRecord:
        conn = connect_project_db(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Fact not found: {fact_id}")
            return self._fact_from_row(row)
        finally:
            conn.close()

    def count_facts(self, *, include_retracted: bool = False) -> int:
        conn = connect_project_db(self.db_path)
        try:
            if include_retracted:
                row = conn.execute("SELECT COUNT(*) FROM facts").fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM facts WHERE status = ?",
                    (FactStatus.ACTIVE.value,),
                ).fetchone()
            return int(row[0])
        finally:
            conn.close()

    def existing_fact_lookup_keys(self, lookup_keys: set[str]) -> set[str]:
        if not lookup_keys:
            return set()
        found: set[str] = set()
        values = sorted(lookup_keys)
        conn = connect_project_db(self.db_path)
        try:
            for start in range(0, len(values), 500):
                chunk = values[start : start + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT lookup_key FROM facts "
                    f"WHERE status = ? AND lookup_key IN ({placeholders})",
                    (FactStatus.ACTIVE.value, *chunk),
                ).fetchall()
                found.update(str(row["lookup_key"]) for row in rows)
            return found
        finally:
            conn.close()

    @staticmethod
    def _fact_from_row(row: sqlite3.Row) -> FactRecord:
        values = dict(row)
        values["value"] = json.loads(values.pop("value_json"))
        return FactRecord.model_validate(values)
