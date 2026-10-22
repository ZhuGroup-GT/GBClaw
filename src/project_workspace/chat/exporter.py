"""Demand-driven Markdown exports of authoritative SQLite chat history."""

from __future__ import annotations

from pathlib import Path

from project_workspace.chat.models import TraceEventRecord, TurnRecord
from project_workspace.chat.transcript import render_chat_transcript, write_chat_transcript
from project_workspace.persistence.locks import file_lock
from project_workspace.persistence.sqlite import connect_project_db, transaction
from project_workspace.execution.ownership import project_lock_directory


def export_chat_transcript(db_path: Path, transcript_path: Path, *, force: bool = False) -> Path:
    """Coalesce pending writes into one export, preserving concurrent revisions.

    The process-shared export lock prevents an older snapshot replacing a newer
    export. The SQLite read transaction only spans the snapshot read; rendering
    and file I/O do not block a chat writer. A write during rendering remains
    dirty and is picked up by the next request.
    """
    project_root = db_path.parent.parent.resolve()
    # File reads can request exports without an execution lease. Coordinate with
    # ProjectStore.delete before any helper can create a directory or SQLite DB.
    with file_lock(project_lock_directory(project_root) / "metadata.lock"):
        if not project_root.is_dir() or not db_path.is_file():
            raise ValueError("Project chat history not found")
        return _export_snapshot(db_path, transcript_path, force=force)


def _export_snapshot(db_path: Path, transcript_path: Path, *, force: bool) -> Path:
    """Export while holding the project metadata lock, then the export lock."""
    with file_lock(transcript_path.with_name(".transcript.lock")):
        conn = connect_project_db(db_path)
        try:
            conn.execute("BEGIN")
            state = conn.execute(
                "SELECT revision, exported_revision FROM transcript_export WHERE singleton = 1"
            ).fetchone()
            revision = int(state["revision"])
            if not force and revision == state["exported_revision"] and transcript_path.is_file():
                return transcript_path
            turn_rows = conn.execute(
                "SELECT * FROM turns ORDER BY created_at, thread_id, sequence"
            ).fetchall()
            trace_rows = conn.execute(
                "SELECT trace_events.* FROM trace_events JOIN turns USING (turn_id) "
                "ORDER BY turns.created_at, turns.thread_id, turns.sequence, trace_events.sequence"
            ).fetchall()
        finally:
            conn.close()

        turns = [TurnRecord.model_validate(dict(row)) for row in turn_rows]
        trace_by_turn: dict[str, list[TraceEventRecord]] = {}
        for row in trace_rows:
            event = TraceEventRecord.model_validate(dict(row))
            trace_by_turn.setdefault(event.turn_id, []).append(event)
        write_chat_transcript(transcript_path, render_chat_transcript(turns, trace_by_turn))

        conn = connect_project_db(db_path)
        try:
            with transaction(conn):
                conn.execute(
                    "UPDATE transcript_export SET exported_revision = ? WHERE singleton = 1",
                    (revision,),
                )
        finally:
            conn.close()
    return transcript_path
