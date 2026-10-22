"""One schema owner for chat turns, traces, request admission, and exports."""

from __future__ import annotations

import sqlite3

from project_workspace.persistence.sqlite import transaction


CHAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    turn_id        TEXT PRIMARY KEY,
    thread_id      TEXT NOT NULL,
    sequence       INTEGER NOT NULL,
    role           TEXT NOT NULL,
    content        TEXT NOT NULL DEFAULT '',
    reasoning      TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    completed_at   TEXT,
    UNIQUE(thread_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_turns_thread_sequence
    ON turns(thread_id, sequence);

CREATE TABLE IF NOT EXISTS turn_order (
    position INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id TEXT NOT NULL UNIQUE REFERENCES turns(turn_id)
);
CREATE TABLE IF NOT EXISTS chat_migrations (
    name TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS trace_events (
    event_id        TEXT PRIMARY KEY,
    turn_id         TEXT NOT NULL REFERENCES turns(turn_id),
    parent_event_id TEXT REFERENCES trace_events(event_id),
    tool_call_id    TEXT,
    sequence        INTEGER NOT NULL,
    event_type      TEXT NOT NULL,
    tool_name       TEXT,
    label           TEXT,
    input_json      TEXT,
    output_text     TEXT,
    status          TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_trace_turn_sequence
    ON trace_events(turn_id, sequence);

CREATE TABLE IF NOT EXISTS chat_requests (
    request_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    fingerprint TEXT,
    owner TEXT,
    status TEXT NOT NULL CHECK (status IN ('running','completed','paused','interrupted','failed')),
    assistant_turn_id TEXT REFERENCES turns(turn_id),
    response_json TEXT,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcript_export (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    revision INTEGER NOT NULL DEFAULT 0,
    exported_revision INTEGER NOT NULL DEFAULT -1
);
INSERT OR IGNORE INTO transcript_export (singleton) VALUES (1);
"""


def initialize_chat_schema(conn: sqlite3.Connection) -> None:
    """Create request and history tables together before either repository is used."""
    conn.executescript(CHAT_SCHEMA)
    with transaction(conn):
        trace_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(trace_events)")
        }
        if "tool_call_id" not in trace_columns:
            conn.execute("ALTER TABLE trace_events ADD COLUMN tool_call_id TEXT")
        if conn.execute(
            "SELECT 1 FROM chat_migrations WHERE name = 'turn_order'"
        ).fetchone() is None:
            conn.execute(
                "INSERT INTO turn_order (turn_id) SELECT turn_id FROM turns "
                "WHERE turn_id NOT IN (SELECT turn_id FROM turn_order) ORDER BY rowid"
            )
            conn.execute("INSERT INTO chat_migrations (name) VALUES ('turn_order')")


def mark_transcript_dirty(conn: sqlite3.Connection) -> None:
    """Advance the export revision in the same transaction as each chat write."""
    conn.execute("UPDATE transcript_export SET revision = revision + 1 WHERE singleton = 1")
