"""Memory database schema, version checks and one-time backfills."""

from __future__ import annotations

import json
import sqlite3

from extensions import canonical_config_workflows
from project_memory.storage.changes import _mark_semantic_change
from project_memory.storage.encoding import _json_dump
from project_workspace.persistence.sqlite import transaction

MEMORY_SCHEMA_VERSION = 4


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_schema (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    version      INTEGER NOT NULL
);
INSERT OR IGNORE INTO memory_schema (singleton_id, version) VALUES (1, 4);

CREATE TABLE IF NOT EXISTS runs (
    run_id              TEXT PRIMARY KEY,
    workflow            TEXT NOT NULL,
    status              TEXT NOT NULL,
    source_run_id       TEXT REFERENCES runs(run_id),
    source_fingerprint  TEXT,
    config_path         TEXT,
    manifest_path       TEXT NOT NULL,
    parameters_json     TEXT NOT NULL DEFAULT '{}',
    progress_json       TEXT NOT NULL DEFAULT '{}',
    result_json         TEXT NOT NULL DEFAULT '{}',
    error               TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    started_at          TEXT,
    completed_at        TEXT,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_workflow_status
    ON runs(workflow, status);
CREATE INDEX IF NOT EXISTS idx_runs_source_fingerprint
    ON runs(source_fingerprint);
CREATE INDEX IF NOT EXISTS idx_runs_source_run
    ON runs(source_run_id);

CREATE TABLE IF NOT EXISTS run_sources (
    run_id              TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    source_run_id       TEXT NOT NULL REFERENCES runs(run_id),
    relation            TEXT NOT NULL,
    ordinal             INTEGER NOT NULL CHECK (ordinal >= 0),
    source_fingerprint  TEXT,
    metadata_json       TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, relation, ordinal),
    CHECK (run_id <> source_run_id)
);
CREATE INDEX IF NOT EXISTS idx_run_sources_source
    ON run_sources(source_run_id, relation, run_id);

CREATE TABLE IF NOT EXISTS facts (
    fact_id          TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,
    lookup_key       TEXT NOT NULL,
    value_json       TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('active', 'retracted')),
    version          INTEGER NOT NULL DEFAULT 1,
    source_turn_id   TEXT,
    source_run_id    TEXT REFERENCES runs(run_id),
    evidence_kind    TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE(kind, lookup_key)
);
CREATE INDEX IF NOT EXISTS idx_facts_status_kind
    ON facts(status, kind);
CREATE INDEX IF NOT EXISTS idx_facts_source_run
    ON facts(source_run_id);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id       TEXT PRIMARY KEY,
    run_id            TEXT NOT NULL REFERENCES runs(run_id),
    role              TEXT NOT NULL,
    path              TEXT NOT NULL,
    provenance_class  TEXT NOT NULL,
    media_type        TEXT,
    size_bytes        INTEGER,
    metadata_json     TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    UNIQUE(run_id, role, path)
);
CREATE INDEX IF NOT EXISTS idx_artifacts_run_role
    ON artifacts(run_id, role);

CREATE TABLE IF NOT EXISTS semantic_revision (
    singleton_id     INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    current_revision INTEGER NOT NULL,
    initialized      INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO semantic_revision (
    singleton_id, current_revision, initialized
) VALUES (1, 0, 0);

CREATE TABLE IF NOT EXISTS semantic_changes (
    entity_type TEXT NOT NULL CHECK (entity_type IN ('fact', 'run')),
    entity_id   TEXT NOT NULL,
    revision    INTEGER NOT NULL UNIQUE,
    changed_at  TEXT NOT NULL,
    PRIMARY KEY (entity_type, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_semantic_changes_revision
    ON semantic_changes(revision);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_search USING fts5(
    entity_type UNINDEXED,
    entity_id UNINDEXED,
    title,
    body,
    updated_at UNINDEXED,
    tokenize='trigram'
);

CREATE TABLE IF NOT EXISTS memory_search_state (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    index_version INTEGER NOT NULL
);
INSERT OR IGNORE INTO memory_search_state (singleton_id, index_version)
VALUES (1, 0);

CREATE TABLE IF NOT EXISTS memory_retry_jobs (
    job_id                  TEXT PRIMARY KEY,
    assistant_turn_id       TEXT NOT NULL UNIQUE,
    referenced_run_ids_json TEXT NOT NULL DEFAULT '[]',
    stage                   TEXT NOT NULL CHECK (
                                stage IN ('extract', 'apply', 'summarize')
                            ),
    status                  TEXT NOT NULL CHECK (
                                status IN (
                                    'pending',
                                    'processing',
                                    'retry_wait',
                                    'completed',
                                    'dead_letter'
                                )
                            ),
    attempts                INTEGER NOT NULL DEFAULT 0,
    next_attempt_at         TEXT,
    extraction_json         TEXT,
    last_error              TEXT NOT NULL DEFAULT '',
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_retry_jobs_ready
    ON memory_retry_jobs(status, next_attempt_at, created_at);
"""


class MemorySchemaVersionError(ValueError):
    """Raised when a project uses an unsupported memory database version."""


class SchemaMixin:
    @staticmethod
    def _validate_or_prepare_schema(conn: sqlite3.Connection) -> None:
        tables = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if not tables:
            return
        if "memory_schema" not in tables:
            raise MemorySchemaVersionError(
                "This project uses a pre-V2 memory database. Memory V4 migrates V2/V3 "
                "projects only; create a new project or back up and rebuild its "
                "memory/index.sqlite explicitly."
            )
        row = conn.execute(
            "SELECT version FROM memory_schema WHERE singleton_id = 1"
        ).fetchone()
        version = int(row["version"]) if row is not None else 0
        if version == 2:
            with transaction(conn):
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS run_sources (
                        run_id TEXT NOT NULL REFERENCES runs(run_id)
                            ON DELETE CASCADE,
                        source_run_id TEXT NOT NULL REFERENCES runs(run_id),
                        relation TEXT NOT NULL,
                        ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
                        source_fingerprint TEXT,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        PRIMARY KEY (run_id, relation, ordinal),
                        UNIQUE (run_id, relation, source_run_id),
                        CHECK (run_id <> source_run_id)
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_run_sources_source "
                    "ON run_sources(source_run_id, relation, run_id)"
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO run_sources (
                        run_id, source_run_id, relation, ordinal,
                        source_fingerprint, metadata_json
                    )
                    SELECT run_id, source_run_id, 'primary_input', 0,
                           source_fingerprint, '{}'
                    FROM runs
                    WHERE source_run_id IS NOT NULL
                    """
                )
                conn.execute(
                    "UPDATE memory_schema SET version = 3 WHERE singleton_id = 1"
                )
            version = 3
        if version == 3:
            # A batch may derive several frames from one run. The ordinal slot
            # is unique; the source run need not be. Rebuild atomically because
            # SQLite cannot drop the old table-level UNIQUE constraint.
            with transaction(conn):
                current = int(conn.execute(
                    "SELECT version FROM memory_schema WHERE singleton_id = 1"
                ).fetchone()[0])
                if current == 3:
                    conn.execute(
                        """
                        CREATE TABLE run_sources_v4 (
                            run_id TEXT NOT NULL REFERENCES runs(run_id)
                                ON DELETE CASCADE,
                            source_run_id TEXT NOT NULL REFERENCES runs(run_id),
                            relation TEXT NOT NULL,
                            ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
                            source_fingerprint TEXT,
                            metadata_json TEXT NOT NULL DEFAULT '{}',
                            PRIMARY KEY (run_id, relation, ordinal),
                            CHECK (run_id <> source_run_id)
                        )
                        """
                    )
                    conn.execute(
                        """
                        INSERT INTO run_sources_v4 (
                            run_id, source_run_id, relation, ordinal,
                            source_fingerprint, metadata_json
                        )
                        SELECT run_id, source_run_id, relation, ordinal,
                               source_fingerprint, metadata_json
                        FROM run_sources
                        """
                    )
                    conn.execute("DROP TABLE run_sources")
                    conn.execute("ALTER TABLE run_sources_v4 RENAME TO run_sources")
                    conn.execute(
                        "CREATE INDEX idx_run_sources_source "
                        "ON run_sources(source_run_id, relation, run_id)"
                    )
                    conn.execute(
                        "UPDATE memory_schema SET version = 4 WHERE singleton_id = 1"
                    )
                elif current != MEMORY_SCHEMA_VERSION:
                    raise MemorySchemaVersionError(
                        f"Memory schema changed unexpectedly during migration: {current}"
                    )
            version = MEMORY_SCHEMA_VERSION
        if version != MEMORY_SCHEMA_VERSION:
            raise MemorySchemaVersionError(
                f"Unsupported memory schema version {version}; "
                f"this build requires version {MEMORY_SCHEMA_VERSION}."
            )

    @staticmethod
    def _bootstrap_semantic_changes(conn: sqlite3.Connection) -> None:
        initialized = bool(
            conn.execute(
                "SELECT initialized FROM semantic_revision WHERE singleton_id = 1"
            ).fetchone()[0]
        )
        if initialized:
            return
        rows = conn.execute(
            """
            SELECT 'fact' AS entity_type, fact_id AS entity_id, updated_at
            FROM facts
            UNION ALL
            SELECT 'run' AS entity_type, run_id AS entity_id, updated_at
            FROM runs
            ORDER BY updated_at, entity_type, entity_id
            """
        ).fetchall()
        for row in rows:
            _mark_semantic_change(
                conn,
                entity_type=str(row["entity_type"]),
                entity_id=str(row["entity_id"]),
                changed_at=str(row["updated_at"]),
            )
        conn.execute(
            "UPDATE semantic_revision SET initialized = 1 WHERE singleton_id = 1"
        )

    def _backfill_canonical_configs(
        self,
        conn: sqlite3.Connection,
    ) -> None:
        """Index canonical input.json for workflows opting into this policy."""
        workflows = tuple(item.value for item in canonical_config_workflows())
        if not workflows:
            return
        placeholders = ", ".join("?" for _ in workflows)
        rows = conn.execute(
            "SELECT run_id, config_path, parameters_json, updated_at "
            f"FROM runs WHERE workflow IN ({placeholders}) AND config_path IS NOT NULL",
            workflows,
        ).fetchall()
        for row in rows:
            try:
                parameters = json.loads(str(row["parameters_json"]))
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(parameters, dict) or "canonical_config" in parameters:
                continue

            config_path = (self.project_root / str(row["config_path"])).resolve()
            if not config_path.is_relative_to(self.project_root) or not config_path.is_file():
                continue
            try:
                payload = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue

            parameters["canonical_config"] = payload
            conn.execute(
                "UPDATE runs SET parameters_json = ? WHERE run_id = ?",
                (_json_dump(parameters), str(row["run_id"])),
            )
            _mark_semantic_change(
                conn,
                entity_type="run",
                entity_id=str(row["run_id"]),
                changed_at=str(row["updated_at"]),
            )
