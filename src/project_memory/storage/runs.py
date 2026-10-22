"""Durable run records: lifecycle transitions, progress, publication state and sources."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any

from project_memory.models import (
    RunRecord,
    RunSourceRecord,
    RunStatus,
    Workflow,
)
from project_memory.storage.changes import _mark_semantic_change
from project_memory.storage.encoding import _json_dump
from project_workspace.persistence.sqlite import connect_project_db, transaction


class RunStoreMixin:
    def create_run(self, run: RunRecord) -> None:
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                conn.execute(
                    """
                    INSERT INTO runs (
                        run_id, workflow, status, source_run_id, source_fingerprint,
                        config_path, manifest_path, parameters_json, progress_json,
                        result_json, error, created_at, started_at, completed_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.run_id,
                        run.workflow.value,
                        run.status.value,
                        run.source_run_id,
                        run.source_fingerprint,
                        run.config_path,
                        run.manifest_path,
                        _json_dump(run.parameters),
                        _json_dump(run.progress),
                        _json_dump(run.result),
                        run.error,
                        run.created_at,
                        run.started_at,
                        run.completed_at,
                        run.updated_at,
                    ),
                )
                sources = list(run.sources)
                if run.source_run_id and all(
                    item.source_run_id != run.source_run_id for item in sources
                ):
                    sources.append(
                        RunSourceRecord(
                            source_run_id=run.source_run_id,
                            source_fingerprint=run.source_fingerprint,
                        )
                    )
                for source in sources:
                    conn.execute(
                        """
                        INSERT INTO run_sources (
                            run_id, source_run_id, relation, ordinal,
                            source_fingerprint, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run.run_id,
                            source.source_run_id,
                            source.relation,
                            source.ordinal,
                            source.source_fingerprint,
                            _json_dump(source.metadata),
                        ),
                    )
                self._upsert_run_search_document(conn, run.run_id)
                _mark_semantic_change(
                    conn,
                    entity_type="run",
                    entity_id=run.run_id,
                    changed_at=run.updated_at,
                )
        finally:
            conn.close()

    def get_run(self, run_id: str) -> RunRecord:
        conn = connect_project_db(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Run not found: {run_id}")
            return self._run_from_row(conn, row)
        finally:
            conn.close()

    def count_runs(self) -> int:
        conn = connect_project_db(self.db_path)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
        finally:
            conn.close()

    def transition_run(
        self,
        run_id: str,
        status: RunStatus,
        *,
        at: str,
        progress: dict | None = None,
        result: dict | None = None,
        error: str = "",
    ) -> RunRecord:
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                row = conn.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    raise ValueError(f"Run not found: {run_id}")
                current = RunStatus(row["status"])
                allowed = (
                    current is RunStatus.CONFIGURED and status is RunStatus.RUNNING
                ) or (
                    current is RunStatus.RUNNING
                    and status
                    in {
                        RunStatus.COMPLETED,
                        RunStatus.FAILED,
                        RunStatus.CANCELLED,
                    }
                )
                if not allowed:
                    raise ValueError(
                        f"Run {run_id} cannot transition from {current.value} "
                        f"to {status.value}"
                    )
                next_progress = (
                    progress if progress is not None else json.loads(row["progress_json"])
                )
                next_result = (
                    result if result is not None else json.loads(row["result_json"])
                )
                started_at = row["started_at"]
                completed_at = row["completed_at"]
                if status is RunStatus.RUNNING:
                    started_at = started_at or at
                else:
                    completed_at = at
                conn.execute(
                    """
                    UPDATE runs
                    SET status = ?, progress_json = ?, result_json = ?, error = ?,
                        started_at = ?, completed_at = ?, updated_at = ?
                    WHERE run_id = ?
                    """,
                    (
                        status.value,
                        _json_dump(next_progress),
                        _json_dump(next_result),
                        error,
                        started_at,
                        completed_at,
                        at,
                        run_id,
                    ),
                )
                self._upsert_run_search_document(conn, run_id)
                _mark_semantic_change(
                    conn,
                    entity_type="run",
                    entity_id=run_id,
                    changed_at=at,
                )
            return self.get_run(run_id)
        finally:
            conn.close()

    def update_run_publication(
        self, run_id: str, publication: dict, *, expected_artifact_id: str, at: str,
    ) -> RunRecord:
        """Update only the staged publication envelope; preserve scientific results."""
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                row = conn.execute("SELECT status, result_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
                if row is None or row["status"] != RunStatus.COMPLETED.value:
                    raise ValueError("Memory publication requires a completed run")
                result = json.loads(row["result_json"])
                current = result.get("memory_publication") or {}
                if current.get("artifact_id") != expected_artifact_id:
                    raise ValueError("Staged publication changed during recovery")
                # A concurrent successful retry must not be replaced by an error.
                if current.get("status") != "completed":
                    publication = {**publication, "attempts": max(
                        int(current.get("attempts", 0)), int(publication.get("attempts", 0)),
                    )}
                    if publication.get("status") == "pending" and publication["attempts"] >= 3:
                        publication["status"] = "blocked"
                    result["memory_publication"] = publication
                    if publication.get("error"):
                        result["fact_indexing_error"] = publication["error"]
                    else:
                        result.pop("fact_indexing_error", None)
                    conn.execute(
                        "UPDATE runs SET result_json = ?, updated_at = ? WHERE run_id = ?",
                        (_json_dump(result), at, run_id),
                    )
                    self._upsert_run_search_document(conn, run_id)
                    _mark_semantic_change(conn, entity_type="run", entity_id=run_id, changed_at=at)
            return self.get_run(run_id)
        finally:
            conn.close()

    def pending_publication_run_ids(self, *, limit: int = 8) -> list[str]:
        conn = connect_project_db(self.db_path)
        try:
            return [str(row[0]) for row in conn.execute(
                "SELECT run_id FROM runs WHERE status = 'completed' "
                "AND json_extract(result_json, '$.memory_publication.status') = 'pending' "
                "ORDER BY updated_at, run_id LIMIT ?", (min(100, max(0, int(limit))),),
            )]
        finally:
            conn.close()

    def publication_counts(self) -> dict[str, int]:
        conn = connect_project_db(self.db_path)
        try:
            return {str(row[0]): int(row[1]) for row in conn.execute(
                "SELECT json_extract(result_json, '$.memory_publication.status') AS publication_status, "
                "COUNT(*) FROM runs WHERE status = 'completed' AND publication_status IS NOT NULL "
                "GROUP BY publication_status"
            )}
        finally:
            conn.close()

    def update_run_progress(
        self,
        run_id: str,
        *,
        progress: dict,
        at: str,
    ) -> RunRecord:
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                row = conn.execute(
                    "SELECT status, progress_json FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    raise ValueError(f"Run not found: {run_id}")
                if row["status"] != RunStatus.RUNNING.value:
                    raise ValueError(
                        f"Run {run_id} must be running to update progress; "
                        f"current status is {row['status']}"
                    )
                progress_json = _json_dump(progress)
                if progress_json != row["progress_json"]:
                    conn.execute(
                        "UPDATE runs SET progress_json = ?, updated_at = ? "
                        "WHERE run_id = ?",
                        (progress_json, at, run_id),
                    )
                    self._upsert_run_search_document(conn, run_id)
                    _mark_semantic_change(
                        conn,
                        entity_type="run",
                        entity_id=run_id,
                        changed_at=at,
                    )
            return self.get_run(run_id)
        finally:
            conn.close()

    def query_runs(
        self,
        *,
        workflow: Workflow | None = None,
        status: RunStatus | None = None,
        source_fingerprint: str | None = None,
        source_run_id: str | None = None,
        source_relation: str | None = None,
        run_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[RunRecord]:
        if limit < 1:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if source_relation is not None:
            source_relation = source_relation.strip()
            if re.fullmatch(r"[a-z][a-z0-9_]*", source_relation) is None:
                raise ValueError(
                    "source_relation must use lowercase letters, digits, and underscores"
                )
        clauses: list[str] = []
        values: list[Any] = []
        for column, value in (
            ("workflow", workflow.value if workflow is not None else None),
            ("status", status.value if status is not None else None),
            ("source_fingerprint", source_fingerprint),
            ("run_id", run_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                values.append(value)
        if source_run_id is not None or source_relation is not None:
            source_clauses = ["run_sources.run_id = runs.run_id"]
            if source_run_id is not None:
                source_clauses.append("run_sources.source_run_id = ?")
                values.append(source_run_id)
            if source_relation is not None:
                source_clauses.append("run_sources.relation = ?")
                values.append(source_relation)
            clauses.append(
                "EXISTS (SELECT 1 FROM run_sources WHERE "
                + " AND ".join(source_clauses)
                + ")"
            )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        conn = connect_project_db(self.db_path)
        try:
            rows = conn.execute(
                f"SELECT * FROM runs {where} ORDER BY updated_at DESC, run_id DESC LIMIT ? OFFSET ?",
                (*values, limit, offset),
            ).fetchall()
            return [self._run_from_row(conn, row) for row in rows]
        finally:
            conn.close()

    def query_running_runs_for_chat(
        self, chat_thread_id: str, *, after_run_id: str = "", limit: int = 200,
    ) -> list[RunRecord]:
        """Filter ownership before paging, with a cursor stable during cancellation."""
        if limit < 1:
            raise ValueError("limit must be positive")
        thread_id = chat_thread_id.strip()
        if not thread_id:
            return []
        conn = connect_project_db(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM runs WHERE status = ? AND run_id > ? "
                "AND trim(CAST(json_extract(parameters_json, "
                "'$._gbclaw_context.chat_thread_id') AS TEXT)) = ? "
                "ORDER BY run_id LIMIT ?",
                (RunStatus.RUNNING.value, after_run_id, thread_id, limit),
            ).fetchall()
            return [self._run_from_row(conn, row) for row in rows]
        finally:
            conn.close()

    def append_coding_inputs(self, run_id: str, sources: list[RunSourceRecord], *,
                             source_count: int, source_bytes: int, at: str) -> None:
        """Host-only append: update source provenance without replacing live run state."""
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
                if row is None or row["workflow"] != Workflow.CODE.value or row["status"] != RunStatus.RUNNING.value:
                    raise ValueError("Additional inputs require a running coding run")
                for source in sources:
                    conn.execute("INSERT INTO run_sources VALUES (?, ?, ?, ?, ?, ?)",
                        (run_id, source.source_run_id, source.relation, source.ordinal,
                         source.source_fingerprint, _json_dump(source.metadata)))
                parameters = json.loads(row["parameters_json"])
                parameters.update(source_count=source_count, source_bytes=source_bytes)
                parameters.setdefault("initial_source_fingerprint", row["source_fingerprint"])
                fingerprint = hashlib.sha256(_json_dump([row["source_fingerprint"],
                    [source.model_dump(mode="json") for source in sources]]).encode()).hexdigest()
                source_ids = {r[0] for r in conn.execute(
                    "SELECT source_run_id FROM run_sources WHERE run_id = ?", (run_id,))}
                shortcut = next(iter(source_ids)) if len(source_ids) == 1 else None
                conn.execute("UPDATE runs SET parameters_json = ?, updated_at = ?, source_fingerprint = ?, "
                             "source_run_id = ? WHERE run_id = ?",
                             (_json_dump(parameters), at, fingerprint, shortcut, run_id))
                self._upsert_run_search_document(conn, run_id)
                _mark_semantic_change(conn, entity_type="run", entity_id=run_id, changed_at=at)
        finally:
            conn.close()

    def query_run_sources(self, run_id: str) -> list[RunSourceRecord]:
        conn = connect_project_db(self.db_path)
        try:
            if conn.execute(
                "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone() is None:
                raise ValueError(f"Run not found: {run_id}")
            return self._run_sources_from_rows(
                conn.execute(
                    "SELECT * FROM run_sources WHERE run_id = ? "
                    "ORDER BY relation, ordinal, source_run_id",
                    (run_id,),
                ).fetchall()
            )
        finally:
            conn.close()

    @classmethod
    def _run_from_row(
        cls,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> RunRecord:
        values = dict(row)
        values["parameters"] = json.loads(values.pop("parameters_json"))
        values["progress"] = json.loads(values.pop("progress_json"))
        values["result"] = json.loads(values.pop("result_json"))
        values["sources"] = cls._run_sources_from_rows(
            conn.execute(
                "SELECT * FROM run_sources WHERE run_id = ? "
                "ORDER BY relation, ordinal, source_run_id",
                (str(row["run_id"]),),
            ).fetchall()
        )
        if values.get("source_run_id") and all(
            item.source_run_id != values["source_run_id"]
            for item in values["sources"]
        ):
            raise ValueError(
                f"run {row['run_id']} has a source_run_id without a run_sources row"
            )
        return RunRecord.model_validate(values)

    @staticmethod
    def _run_sources_from_rows(rows: list[sqlite3.Row]) -> list[RunSourceRecord]:
        sources: list[RunSourceRecord] = []
        for row in rows:
            metadata = json.loads(str(row["metadata_json"]))
            if not isinstance(metadata, dict):
                raise ValueError(
                    f"run source metadata must be an object: {row['source_run_id']}"
                )
            sources.append(
                RunSourceRecord(
                    source_run_id=str(row["source_run_id"]),
                    relation=str(row["relation"]),
                    ordinal=int(row["ordinal"]),
                    source_fingerprint=row["source_fingerprint"],
                    metadata=metadata,
                )
            )
        return sources
