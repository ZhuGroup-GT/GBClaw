"""Durable queue of conversation-extraction jobs awaiting retry."""

from __future__ import annotations

import json
import sqlite3

from project_memory.models import (
    MemoryRetryJobRecord,
    MemoryRetryJobStage,
    MemoryRetryJobStatus,
)
from project_workspace.persistence.sqlite import connect_project_db, transaction


class RetryQueueMixin:
    def save_retry_job(self, job: MemoryRetryJobRecord) -> MemoryRetryJobRecord:
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                conn.execute(
                    """
                    INSERT INTO memory_retry_jobs (
                        job_id, assistant_turn_id, referenced_run_ids_json,
                        stage, status, attempts, next_attempt_at, extraction_json,
                        last_error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        referenced_run_ids_json = excluded.referenced_run_ids_json,
                        stage = excluded.stage,
                        status = excluded.status,
                        attempts = excluded.attempts,
                        next_attempt_at = excluded.next_attempt_at,
                        extraction_json = excluded.extraction_json,
                        last_error = excluded.last_error,
                        updated_at = excluded.updated_at
                    """,
                    (
                        job.job_id,
                        job.assistant_turn_id,
                        json.dumps(
                            job.referenced_run_ids,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        job.stage.value,
                        job.status.value,
                        job.attempts,
                        job.next_attempt_at,
                        (
                            json.dumps(
                                job.extraction,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            )
                            if job.extraction is not None
                            else None
                        ),
                        job.last_error,
                        job.created_at,
                        job.updated_at,
                    ),
                )
            return self.get_retry_job(job.job_id)
        finally:
            conn.close()

    def get_retry_job(self, job_id: str) -> MemoryRetryJobRecord:
        conn = connect_project_db(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM memory_retry_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Memory retry job not found: {job_id}")
            return self._retry_job_from_row(row)
        finally:
            conn.close()

    def get_retry_job_for_turn(
        self, assistant_turn_id: str
    ) -> MemoryRetryJobRecord | None:
        conn = connect_project_db(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM memory_retry_jobs WHERE assistant_turn_id = ?",
                (assistant_turn_id,),
            ).fetchone()
            return self._retry_job_from_row(row) if row is not None else None
        finally:
            conn.close()

    def list_retry_jobs(self, *, status: MemoryRetryJobStatus | None = None) -> list[MemoryRetryJobRecord]:
        conn = connect_project_db(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM memory_retry_jobs WHERE (? IS NULL OR status = ?) ORDER BY created_at",
                (status.value if status else None, status.value if status else None),
            ).fetchall()
            return [self._retry_job_from_row(row) for row in rows]
        finally:
            conn.close()

    def requeue_dead_letter_retry_job(
        self, job_id: str, *, expected_last_error: str, now: str, max_attempts: int,
        reset_attempts: bool = False,
    ) -> bool:
        """Requeue one inspected extraction failure.

        The attempt count stays unless ``reset_attempts`` is set, which is for a
        failure caused by host configuration that has since been fixed: those
        attempts never exercised the source. The compare-and-swap guard prevents
        a concurrent recovery command from resetting a job already claimed by
        the retry worker.
        """
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                changed = conn.execute(
                    "UPDATE memory_retry_jobs SET status = ?, next_attempt_at = ?, updated_at = ?, "
                    "attempts = CASE WHEN ? THEN 0 ELSE attempts END "
                    "WHERE job_id = ? AND status = ? AND stage = ? "
                    "AND last_error = ? AND (attempts < ? OR ?)",
                    (MemoryRetryJobStatus.RETRY_WAIT.value, now, now, int(reset_attempts), job_id,
                     MemoryRetryJobStatus.DEAD_LETTER.value, MemoryRetryJobStage.EXTRACT.value,
                     expected_last_error, max_attempts, int(reset_attempts)),
                )
                return changed.rowcount == 1
        finally:
            conn.close()

    def claim_ready_retry_job(
        self,
        *,
        now: str,
        stale_before: str,
    ) -> MemoryRetryJobRecord | None:
        """Claim one ready job and recover abandoned processing rows."""
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                conn.execute(
                    """
                    UPDATE memory_retry_jobs
                    SET status = ?, next_attempt_at = ?, updated_at = ?
                    WHERE status = ? AND updated_at <= ?
                    """,
                    (
                        MemoryRetryJobStatus.RETRY_WAIT.value,
                        now,
                        now,
                        MemoryRetryJobStatus.PROCESSING.value,
                        stale_before,
                    ),
                )
                row = conn.execute(
                    """
                    SELECT * FROM memory_retry_jobs
                    WHERE status IN (?, ?)
                      AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                    ORDER BY created_at, job_id
                    LIMIT 1
                    """,
                    (
                        MemoryRetryJobStatus.PENDING.value,
                        MemoryRetryJobStatus.RETRY_WAIT.value,
                        now,
                    ),
                ).fetchone()
                if row is None:
                    return None
                job_id = str(row["job_id"])
                conn.execute(
                    """
                    UPDATE memory_retry_jobs
                    SET status = ?, attempts = attempts + 1, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (MemoryRetryJobStatus.PROCESSING.value, now, job_id),
                )
                claimed = conn.execute(
                    "SELECT * FROM memory_retry_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                if claimed is None:
                    raise RuntimeError("Claimed memory retry job disappeared")
                return self._retry_job_from_row(claimed)
        finally:
            conn.close()

    def retry_job_counts(self) -> dict[str, int]:
        conn = connect_project_db(self.db_path)
        try:
            counts = {
                status.value: 0
                for status in (
                    MemoryRetryJobStatus.PENDING,
                    MemoryRetryJobStatus.PROCESSING,
                    MemoryRetryJobStatus.RETRY_WAIT,
                    MemoryRetryJobStatus.DEAD_LETTER,
                )
            }
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM memory_retry_jobs "
                "WHERE status != ? GROUP BY status",
                (MemoryRetryJobStatus.COMPLETED.value,),
            ):
                counts[str(row["status"])] = int(row["count"])
            return counts
        finally:
            conn.close()

    @staticmethod
    def _retry_job_from_row(row: sqlite3.Row) -> MemoryRetryJobRecord:
        values = dict(row)
        values["referenced_run_ids"] = json.loads(
            values.pop("referenced_run_ids_json")
        )
        extraction_json = values.pop("extraction_json")
        values["extraction"] = (
            json.loads(extraction_json) if extraction_json is not None else None
        )
        return MemoryRetryJobRecord.model_validate(values)
