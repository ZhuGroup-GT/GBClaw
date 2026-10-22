"""Permanent project chat and trace persistence."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from project_workspace.chat.exporter import export_chat_transcript
from project_workspace.chat.schema import initialize_chat_schema, mark_transcript_dirty
from project_workspace.artifacts.presentation import extract_artifact_image_paths
from project_workspace.persistence.sqlite import connect_project_db, transaction
from project_workspace.chat.models import TraceEventRecord, TurnRecord, TurnStatus


class ChatRepository:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.db_path = self.project_root / "chat" / "history.sqlite"
        self.transcript_path = self.project_root / "chat" / "transcript.md"
        conn = connect_project_db(self.db_path)
        try:
            initialize_chat_schema(conn)
        finally:
            conn.close()

    def start_exchange(
        self,
        thread_id: str,
        user_content: str,
        created_at: str,
        *,
        request_id: str | None = None,
        request_owner: str | None = None,
    ) -> tuple[TurnRecord, TurnRecord]:
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                if conn.execute(
                    "SELECT 1 FROM turns WHERE thread_id = ? AND role = 'assistant' "
                    "AND status = 'running' LIMIT 1", (thread_id,),
                ).fetchone():
                    raise ValueError("This conversation already has a running assistant turn")
                row = conn.execute(
                    "SELECT COALESCE(MAX(sequence), -1) AS last_sequence "
                    "FROM turns WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
                user_sequence = int(row["last_sequence"]) + 1
                user = TurnRecord(
                    turn_id=str(uuid.uuid4()),
                    thread_id=thread_id,
                    sequence=user_sequence,
                    role="user",
                    content=user_content,
                    status=TurnStatus.COMPLETED,
                    created_at=created_at,
                    completed_at=created_at,
                )
                assistant = TurnRecord(
                    turn_id=str(uuid.uuid4()),
                    thread_id=thread_id,
                    sequence=user_sequence + 1,
                    role="assistant",
                    status=TurnStatus.RUNNING,
                    created_at=created_at,
                )
                self._insert_turn(conn, user)
                self._insert_turn(conn, assistant)
                if request_id is not None:
                    changed = conn.execute(
                        "UPDATE chat_requests SET assistant_turn_id = ? "
                        "WHERE request_id = ? AND owner = ? AND thread_id = ? AND status = 'running' "
                        "AND assistant_turn_id IS NULL",
                        (assistant.turn_id, request_id, request_owner, thread_id),
                    ).rowcount
                    if changed != 1:
                        raise ValueError("Chat request admission no longer permits a new exchange")
                mark_transcript_dirty(conn)
        finally:
            conn.close()
        return user, assistant

    def complete_turn(
        self,
        turn_id: str,
        *,
        content: str,
        reasoning: str,
        completed_at: str,
    ) -> TurnRecord:
        return self._finish_turn(
            turn_id,
            status=TurnStatus.COMPLETED,
            content=content,
            reasoning=reasoning,
            completed_at=completed_at,
        )

    def get_turn_order(self, turn_id: str) -> int | None:
        """Return immutable project-wide insertion order, independent of clocks."""
        conn = connect_project_db(self.db_path)
        try:
            row = conn.execute(
                "SELECT position FROM turn_order WHERE turn_id = ?", (turn_id,)
            ).fetchone()
            return int(row[0]) if row is not None else None
        finally:
            conn.close()

    def fail_turn(
        self,
        turn_id: str,
        *,
        content: str,
        reasoning: str = "",
        completed_at: str,
    ) -> TurnRecord:
        return self._finish_turn(
            turn_id,
            status=TurnStatus.FAILED,
            content=content,
            reasoning=reasoning,
            completed_at=completed_at,
        )

    def interrupt_turn(
        self,
        turn_id: str,
        *,
        content: str,
        reasoning: str,
        completed_at: str,
    ) -> TurnRecord:
        return self._finish_turn(
            turn_id,
            status=TurnStatus.INTERRUPTED,
            content=content,
            reasoning=reasoning,
            completed_at=completed_at,
        )

    def append_trace_event(self, event: TraceEventRecord) -> None:
        self.append_trace_events([event])

    def append_trace_events(self, events: list[TraceEventRecord]) -> None:
        """Append trace events in one transaction, preserving their order."""
        if not events:
            return
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                conn.executemany(
                    """
                    INSERT INTO trace_events (
                        event_id, turn_id, parent_event_id, tool_call_id,
                        sequence, event_type,
                        tool_name, label, input_json, output_text, status,
                        started_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            event.event_id,
                            event.turn_id,
                            event.parent_event_id,
                            event.tool_call_id,
                            event.sequence,
                            event.event_type,
                            event.tool_name,
                            event.label,
                            event.input_json,
                            event.output_text,
                            event.status,
                            event.started_at,
                            event.completed_at,
                        )
                        for event in events
                    ],
                )
                mark_transcript_dirty(conn)
        finally:
            conn.close()

    def list_turns(
        self,
        thread_id: str,
        *,
        offset: int,
        limit: int,
        include_reasoning: bool,
    ) -> list[TurnRecord]:
        if offset < 0 or limit < 1:
            raise ValueError("offset must be non-negative and limit must be positive")
        reasoning = "reasoning" if include_reasoning else "'' AS reasoning"
        conn = connect_project_db(self.db_path)
        try:
            rows = conn.execute(
                f"""
                SELECT turn_id, thread_id, sequence, role, content, {reasoning},
                       status, created_at, completed_at
                FROM turns
                WHERE thread_id = ?
                ORDER BY sequence
                LIMIT ? OFFSET ?
                """,
                (thread_id, limit, offset),
            ).fetchall()
            return [self._turn_from_row(row) for row in rows]
        finally:
            conn.close()

    def count_turns(self, thread_id: str) -> int:
        conn = connect_project_db(self.db_path)
        try:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM turns WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()[0]
            )
        finally:
            conn.close()

    def get_turn(self, turn_id: str) -> TurnRecord:
        conn = connect_project_db(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM turns WHERE turn_id = ?", (turn_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Turn not found: {turn_id}")
            return self._turn_from_row(row)
        finally:
            conn.close()

    def get_active_assistant_turn(self, thread_id: str) -> TurnRecord | None:
        conn = connect_project_db(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT * FROM turns
                WHERE thread_id = ? AND role = 'assistant' AND status = 'running'
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (thread_id,),
            ).fetchone()
            return self._turn_from_row(row) if row is not None else None
        finally:
            conn.close()

    def get_active_or_latest_assistant_turn(
        self, thread_id: str
    ) -> TurnRecord | None:
        active = self.get_active_assistant_turn(thread_id)
        if active is not None:
            return active
        conn = connect_project_db(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT * FROM turns
                WHERE thread_id = ? AND role = 'assistant'
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (thread_id,),
            ).fetchone()
            return self._turn_from_row(row) if row is not None else None
        finally:
            conn.close()

    def list_trace_events(self, turn_id: str) -> list[TraceEventRecord]:
        conn = connect_project_db(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM trace_events WHERE turn_id = ? ORDER BY sequence",
                (turn_id,),
            ).fetchall()
            return [TraceEventRecord.model_validate(dict(row)) for row in rows]
        finally:
            conn.close()

    def list_turn_image_paths(self, turn_ids: list[str], project_id: str) -> dict[str, list[str]]:
        """Read only image-bearing tool outputs for a history page, without expanding its trace."""
        paths: dict[str, dict[str, None]] = {turn_id: {} for turn_id in turn_ids}
        if not turn_ids:
            return {}
        conn = connect_project_db(self.db_path)
        try:
            # Stay below SQLite's parameter limit even at the history route's maximum page size.
            for offset in range(0, len(turn_ids), 500):
                batch = turn_ids[offset : offset + 500]
                placeholders = ",".join("?" for _ in batch)
                rows = conn.execute(
                    "SELECT turn_id, output_text FROM trace_events "
                    f"WHERE turn_id IN ({placeholders}) AND event_type = 'tool_end' "
                    "AND output_text LIKE '%artifacts%' ORDER BY turn_id, sequence",
                    batch,
                )
                for row in rows:
                    for path in extract_artifact_image_paths(row["output_text"], project_id):
                        paths[row["turn_id"]][path] = None
        finally:
            conn.close()
        return {turn_id: list(images) for turn_id, images in paths.items()}

    def ensure_transcript(self) -> Path:
        """Refresh the human-readable view on demand if its revision is stale."""
        return export_chat_transcript(self.db_path, self.transcript_path)

    def rebuild_transcript(self) -> Path:
        """Force a complete export from authoritative SQLite history."""
        return export_chat_transcript(self.db_path, self.transcript_path, force=True)

    @staticmethod
    def _insert_turn(conn: sqlite3.Connection, turn: TurnRecord) -> None:
        conn.execute(
            """
            INSERT INTO turns (
                turn_id, thread_id, sequence, role, content, reasoning,
                status, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn.turn_id,
                turn.thread_id,
                turn.sequence,
                turn.role,
                turn.content,
                turn.reasoning,
                turn.status.value,
                turn.created_at,
                turn.completed_at,
            ),
        )
        conn.execute("INSERT INTO turn_order (turn_id) VALUES (?)", (turn.turn_id,))

    def _finish_turn(
        self,
        turn_id: str,
        *,
        status: TurnStatus,
        content: str,
        reasoning: str,
        completed_at: str,
    ) -> TurnRecord:
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                existing = conn.execute(
                    "SELECT * FROM turns WHERE turn_id = ?", (turn_id,)
                ).fetchone()
                if existing is None:
                    raise ValueError(f"Turn not found: {turn_id}")
                if (status is TurnStatus.INTERRUPTED and existing["role"] == "assistant"
                        and existing["status"] != "running"):
                    # Stop cleanup can race with worker completion or another
                    # interruption. Terminal history must remain immutable.
                    return self._turn_from_row(existing)
                if existing["role"] != "assistant" or existing["status"] != "running":
                    raise ValueError(
                        f"Turn {turn_id} cannot transition from {existing['status']} "
                        f"to {status.value}"
                    )
                conn.execute(
                    """
                    UPDATE turns
                    SET content = ?, reasoning = ?, status = ?, completed_at = ?
                    WHERE turn_id = ?
                    """,
                    (content, reasoning, status.value, completed_at, turn_id),
                )
                mark_transcript_dirty(conn)
        finally:
            conn.close()
        finished = self.get_turn(turn_id)
        return finished

    @staticmethod
    def _turn_from_row(row: sqlite3.Row) -> TurnRecord:
        return TurnRecord.model_validate(dict(row))
