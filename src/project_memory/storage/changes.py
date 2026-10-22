"""Semantic change log: one revision per committed memory mutation."""

from __future__ import annotations

import sqlite3

from project_memory.models import (
    SemanticChangeRecord,
)
from project_workspace.persistence.sqlite import connect_project_db, transaction


def _mark_semantic_change(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: str,
    changed_at: str,
) -> int:
    conn.execute(
        "UPDATE semantic_revision SET current_revision = current_revision + 1 "
        "WHERE singleton_id = 1"
    )
    revision = int(
        conn.execute(
            "SELECT current_revision FROM semantic_revision WHERE singleton_id = 1"
        ).fetchone()[0]
    )
    conn.execute(
        """
        INSERT INTO semantic_changes (
            entity_type, entity_id, revision, changed_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(entity_type, entity_id) DO UPDATE SET
            revision = excluded.revision,
            changed_at = excluded.changed_at
        """,
        (entity_type, entity_id, revision, changed_at),
    )
    return revision


class ChangeLogMixin:
    def latest_semantic_revision(self) -> int:
        conn = connect_project_db(self.db_path)
        try:
            return int(
                conn.execute(
                    "SELECT current_revision FROM semantic_revision "
                    "WHERE singleton_id = 1"
                ).fetchone()[0]
            )
        finally:
            conn.close()

    def query_semantic_changes(
        self,
        *,
        after_revision: int,
        limit: int,
    ) -> list[SemanticChangeRecord]:
        if after_revision < 0:
            raise ValueError("after_revision must be non-negative")
        if limit < 1:
            raise ValueError("limit must be positive")
        conn = connect_project_db(self.db_path)
        try:
            rows = conn.execute(
                "SELECT entity_type, entity_id, revision, changed_at "
                "FROM semantic_changes WHERE revision > ? "
                "ORDER BY revision LIMIT ?",
                (after_revision, limit),
            ).fetchall()
            return [SemanticChangeRecord.model_validate(dict(row)) for row in rows]
        finally:
            conn.close()

    def prune_semantic_changes(self, *, through_revision: int) -> None:
        if through_revision < 0:
            raise ValueError("through_revision must be non-negative")
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                conn.execute(
                    "DELETE FROM semantic_changes WHERE revision <= ?",
                    (through_revision,),
                )
        finally:
            conn.close()
