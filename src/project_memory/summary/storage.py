"""Atomic current-summary storage and lossless historical summary archives.

The SQLite archive is authoritative for historical text; Markdown views are
rebuildable exports. Archiving a summary never deletes facts, runs, or evidence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from project_memory.models import FactRecord, SemanticSummaryArchiveRecord, SemanticSummaryRecord
from project_workspace.persistence.sqlite import connect_project_db, transaction
from .contracts import SummaryRefreshConflict
from .navigation import summary_archive_id


SUMMARY_SCHEMA = """
CREATE TABLE IF NOT EXISTS semantic_summary (
    singleton_id    INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    content         TEXT NOT NULL,
    format_version  INTEGER NOT NULL,
    source_digest   TEXT NOT NULL,
    source_revision INTEGER NOT NULL DEFAULT 0,
    generated_at    TEXT,
    fact_count      INTEGER NOT NULL,
    run_count       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS semantic_summary_archives (
    archive_id      TEXT PRIMARY KEY,
    content         TEXT NOT NULL,
    format_version  INTEGER NOT NULL,
    source_digest   TEXT NOT NULL,
    source_revision INTEGER NOT NULL,
    generated_at    TEXT,
    fact_count      INTEGER NOT NULL,
    run_count       INTEGER NOT NULL,
    archived_at     TEXT NOT NULL,
    archive_reason TEXT NOT NULL CHECK (archive_reason IN ('replaced', 'format_reset'))
);
CREATE INDEX IF NOT EXISTS idx_summary_archives_revision
    ON semantic_summary_archives(source_revision DESC, archived_at DESC, archive_id);
CREATE TRIGGER IF NOT EXISTS summary_archive_no_update
BEFORE UPDATE ON semantic_summary_archives
BEGIN
    SELECT RAISE(ABORT, 'Historical summary archives are immutable');
END;
CREATE TRIGGER IF NOT EXISTS summary_archive_no_delete
BEFORE DELETE ON semantic_summary_archives
BEGIN
    SELECT RAISE(ABORT, 'Historical summary archives are immutable');
END;
"""

_SUMMARY_COLUMNS = (
    "content, format_version, source_digest, source_revision, generated_at, fact_count, run_count"
)


def _read_summary(conn: sqlite3.Connection) -> SemanticSummaryRecord | None:
    row = conn.execute(
        f"SELECT {_SUMMARY_COLUMNS} FROM semantic_summary WHERE singleton_id = 1"
    ).fetchone()
    return SemanticSummaryRecord.model_validate(dict(row)) if row is not None else None


def _archive_summary(
    conn: sqlite3.Connection, current: SemanticSummaryRecord | None, *, reason: str,
) -> None:
    if current is None or not current.content.strip():
        return
    archive_id = summary_archive_id(current)
    conn.execute(
        "INSERT OR IGNORE INTO semantic_summary_archives "
        f"(archive_id, {_SUMMARY_COLUMNS}, archived_at, archive_reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            archive_id, current.content, current.format_version, current.source_digest,
            current.source_revision, current.generated_at, current.fact_count, current.run_count,
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), reason,
        ),
    )


class SummaryStorageMixin:
    """Summary persistence methods shared by the project memory repository."""

    db_path: Path

    def get_summary(self) -> SemanticSummaryRecord:
        conn = connect_project_db(self.db_path)
        try:
            return _read_summary(conn) or SemanticSummaryRecord()
        finally:
            conn.close()

    def save_summary(
        self, summary: SemanticSummaryRecord, *, prune_changes_through: int | None = None,
        expected_summary: SemanticSummaryRecord | None = None,
    ) -> None:
        if prune_changes_through is not None and prune_changes_through < 0:
            raise ValueError("prune_changes_through must be non-negative")
        normalized = summary.model_copy(update={"content": summary.content.strip()})
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                current = _read_summary(conn)
                if expected_summary is not None and (current or SemanticSummaryRecord()) != expected_summary:
                    raise SummaryRefreshConflict(
                        "Project summary changed during generation; newer summary and pending changes preserved"
                    )
                if current != normalized:
                    _archive_summary(conn, current, reason="replaced")
                conn.execute(
                    "INSERT INTO semantic_summary "
                    f"(singleton_id, {_SUMMARY_COLUMNS}) VALUES (1, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(singleton_id) DO UPDATE SET "
                    "content = excluded.content, format_version = excluded.format_version, "
                    "source_digest = excluded.source_digest, source_revision = excluded.source_revision, "
                    "generated_at = excluded.generated_at, fact_count = excluded.fact_count, "
                    "run_count = excluded.run_count",
                    (
                        normalized.content, normalized.format_version, normalized.source_digest,
                        normalized.source_revision, normalized.generated_at,
                        normalized.fact_count, normalized.run_count,
                    ),
                )
                if prune_changes_through is not None:
                    conn.execute(
                        "DELETE FROM semantic_changes WHERE revision <= ?", (prune_changes_through,),
                    )
        finally:
            conn.close()

    def reset_summary_sources(self, *, format_version: int) -> SemanticSummaryRecord:
        """Archive an obsolete summary and replay sources without changing evidence."""
        # Kept local because the repository owns source revision bookkeeping and
        # inherits this mixin; importing it at module load would form a cycle.
        from project_memory.storage.changes import _mark_semantic_change

        empty = SemanticSummaryRecord(format_version=format_version)
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                current = _read_summary(conn)
                if current is not None and current.format_version == format_version:
                    return current
                _archive_summary(conn, current, reason="format_reset")
                conn.execute(
                    "INSERT INTO semantic_summary (singleton_id, content, format_version, "
                    "source_digest, source_revision, generated_at, fact_count, run_count) "
                    "VALUES (1, '', ?, '', 0, NULL, 0, 0) "
                    "ON CONFLICT(singleton_id) DO UPDATE SET content = '', "
                    "format_version = excluded.format_version, source_digest = '', "
                    "source_revision = 0, generated_at = NULL, fact_count = 0, run_count = 0",
                    (empty.format_version,),
                )
                for entity_type, rows in (
                    ("fact", conn.execute(
                        "SELECT fact_id AS entity_id, updated_at FROM facts ORDER BY updated_at, fact_id"
                    ).fetchall()),
                    ("run", conn.execute(
                        "SELECT run_id AS entity_id, updated_at FROM runs ORDER BY updated_at, run_id"
                    ).fetchall()),
                ):
                    for row in rows:
                        _mark_semantic_change(
                            conn, entity_type=entity_type, entity_id=row["entity_id"],
                            changed_at=row["updated_at"],
                        )
            return empty
        finally:
            conn.close()

    def summary_archive_count(self) -> int:
        conn = connect_project_db(self.db_path)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM semantic_summary_archives").fetchone()[0])
        finally:
            conn.close()

    def query_summary_open_facts(self, *, limit: int = 128) -> list[FactRecord]:
        """Recall live status rows before pagination, even behind completed history."""
        if not 1 <= limit <= 1000:
            raise ValueError("Open fact limit must be 1..1000")
        # The repository owns fact deserialization and source-origin derivation.
        from project_memory.storage.repository import MemoryRepository

        statuses = (
            "running", "configured", "pending", "pending_approval", "awaiting_user", "partial",
            "failed", "blocked", "interrupted", "publication_error", "approved", "active",
        )
        conn = connect_project_db(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM facts WHERE status = 'active' AND "
                "LOWER(COALESCE(NULLIF(json_extract(value_json, '$.status'), ''), "
                "json_extract(value_json, '$.outcome'), '')) IN ("
                + ",".join("?" for _ in statuses)
                + ") ORDER BY updated_at DESC, fact_id DESC LIMIT ?",
                (*statuses, limit),
            ).fetchall()
            return [MemoryRepository._fact_from_row(row) for row in rows]
        finally:
            conn.close()

    def query_summary_archives(
        self, *, archive_id: str | None = None, query: str | None = None,
        limit: int = 20, offset: int = 0,
    ) -> list[SemanticSummaryArchiveRecord]:
        """Read historical snapshots; text search matches literal substrings."""
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("Archive limit must be 1..1000 and offset must be non-negative")
        if query is not None and len(query) > 500:
            raise ValueError("Archive search query must be at most 500 characters")
        clauses: list[str] = []
        params: list[object] = []
        if archive_id is not None:
            clauses.append("archive_id = ?")
            params.append(archive_id)
        if query and query.strip():
            clauses.append("content LIKE ? ESCAPE '\\'")
            escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{escaped}%")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        conn = connect_project_db(self.db_path)
        try:
            rows = conn.execute(
                f"SELECT archive_id, {_SUMMARY_COLUMNS}, archived_at, archive_reason "
                "FROM semantic_summary_archives" + where
                + " ORDER BY source_revision DESC, archived_at DESC, archive_id LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            return [SemanticSummaryArchiveRecord.model_validate(dict(row)) for row in rows]
        finally:
            conn.close()
