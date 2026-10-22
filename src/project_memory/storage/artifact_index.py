"""Index of manifested run artifacts for lookup and exact reuse."""

from __future__ import annotations

import json
import sqlite3

from project_memory.models import (
    RunStatus,
    Workflow,
)
from project_memory.storage.encoding import _json_dump
from project_workspace.artifacts.models import ArtifactRecord
from project_workspace.persistence.sqlite import connect_project_db, transaction


class ArtifactIndexMixin:
    def register_artifact(self, artifact: ArtifactRecord) -> None:
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                artifact_id = self._insert_artifact(conn, artifact)
                self._upsert_artifact_search_document(conn, artifact_id)
        finally:
            conn.close()

    def replace_run_artifacts(
        self,
        run_id: str,
        artifacts: list[ArtifactRecord],
    ) -> None:
        if any(artifact.run_id != run_id for artifact in artifacts):
            raise ValueError("Every artifact must belong to the requested run")
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                if conn.execute(
                    "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone() is None:
                    raise ValueError(f"Run not found: {run_id}")
                previous_ids = [
                    str(row["artifact_id"])
                    for row in conn.execute(
                        "SELECT artifact_id FROM artifacts WHERE run_id = ?",
                        (run_id,),
                    )
                ]
                for artifact_id in previous_ids:
                    self._delete_search_document(conn, "artifact", artifact_id)
                conn.execute("DELETE FROM artifacts WHERE run_id = ?", (run_id,))
                for artifact in artifacts:
                    artifact_id = self._insert_artifact(conn, artifact)
                    self._upsert_artifact_search_document(conn, artifact_id)
        finally:
            conn.close()

    def query_artifacts(
        self,
        *,
        run_id: str,
        role: str | None = None,
    ) -> list[ArtifactRecord]:
        conn = connect_project_db(self.db_path)
        try:
            if role is None:
                rows = conn.execute(
                    "SELECT * FROM artifacts WHERE run_id = ? "
                    "ORDER BY role, path",
                    (run_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM artifacts WHERE run_id = ? AND role = ? "
                    "ORDER BY path",
                    (run_id, role),
                ).fetchall()
            return [self._artifact_from_row(row) for row in rows]
        finally:
            conn.close()

    def get_artifact(self, artifact_id: str) -> ArtifactRecord:
        conn = connect_project_db(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Artifact not found: {artifact_id}")
            return self._artifact_from_row(row)
        finally:
            conn.close()

    def query_completed_tool_artifacts(
        self,
        *,
        workflow: Workflow,
        role: str,
        input_fingerprint: str,
    ) -> list[ArtifactRecord]:
        """Find exact tool inputs across all completed runs, newest first.

        Callers must still verify the artifact file and its scientific payload
        before reuse. Unlike a recent-run preview, this lookup has no history cap.
        """
        conn = connect_project_db(self.db_path)
        try:
            rows = conn.execute(
                """
                SELECT artifacts.* FROM artifacts
                JOIN runs ON runs.run_id = artifacts.run_id
                WHERE runs.workflow = ? AND runs.status = ?
                  AND artifacts.role = ?
                  AND json_extract(artifacts.metadata_json, '$.input_fingerprint') = ?
                ORDER BY runs.updated_at DESC, artifacts.created_at DESC,
                         artifacts.artifact_id
                """,
                (workflow.value, RunStatus.COMPLETED.value, role, input_fingerprint),
            ).fetchall()
            return [self._artifact_from_row(row) for row in rows]
        finally:
            conn.close()

    @staticmethod
    def _insert_artifact(
        conn: sqlite3.Connection,
        artifact: ArtifactRecord,
    ) -> str:
        conn.execute(
            """
            INSERT INTO artifacts (
                artifact_id, run_id, role, path, provenance_class, media_type,
                size_bytes, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, role, path) DO UPDATE SET
                provenance_class = excluded.provenance_class,
                media_type = excluded.media_type,
                size_bytes = excluded.size_bytes,
                metadata_json = excluded.metadata_json
            """,
            (
                artifact.artifact_id,
                artifact.run_id,
                artifact.role,
                artifact.path,
                artifact.provenance_class.value,
                artifact.media_type,
                artifact.size_bytes,
                _json_dump(artifact.metadata),
                artifact.created_at,
            ),
        )
        row = conn.execute(
            "SELECT artifact_id FROM artifacts "
            "WHERE run_id = ? AND role = ? AND path = ?",
            (artifact.run_id, artifact.role, artifact.path),
        ).fetchone()
        if row is None:
            raise RuntimeError("Artifact upsert did not produce a row")
        return str(row["artifact_id"])

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> ArtifactRecord:
        values = dict(row)
        values["metadata"] = json.loads(values.pop("metadata_json"))
        return ArtifactRecord.model_validate(values)
