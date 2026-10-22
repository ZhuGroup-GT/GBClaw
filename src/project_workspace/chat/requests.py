"""Durable API request admission and results, separate from semantic facts."""
from __future__ import annotations

import hashlib
import json

from project_workspace.chat.repository import ChatRepository
from project_workspace.models import utc_now_iso
from project_workspace.persistence.sqlite import connect_project_db, transaction


def request_fingerprint(thread_id: str, message: str, planning_mode: str) -> str:
    encoded = json.dumps({"thread_id": thread_id, "message": message, "planning_mode": planning_mode},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


class ChatRequestRepository:
    def __init__(self, chat: ChatRepository):
        self.chat = chat
        self.db_path = chat.db_path

    def get(self, request_id: str) -> dict | None:
        conn = connect_project_db(self.db_path)
        try:
            row = conn.execute("SELECT * FROM chat_requests WHERE request_id = ?", (request_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            raw = result.pop("response_json")
            result["response"] = json.loads(raw) if raw is not None else None
            return result
        finally:
            conn.close()

    def admit(self, request_id: str, thread_id: str, fingerprint: str, owner: str) -> None:
        """Caller holds the project gate and execution lease until final cleanup."""
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                now = utc_now_iso()
                conn.execute(
                    "INSERT INTO chat_requests (request_id,thread_id,fingerprint,owner,status,created_at,updated_at) "
                    "VALUES (?,?,?,?,'running',?,?)", (request_id, thread_id, fingerprint, owner, now, now),
                )
        finally:
            conn.close()

    def finish(self, request_id: str, owner: str, *, response: dict | None = None, error: str = "") -> bool:
        """A late producer cannot replace an already terminal request result."""
        status = str(response["status"]) if response is not None else "failed"
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                changed = conn.execute(
                    "UPDATE chat_requests SET status=?,response_json=?,error=?,updated_at=? "
                    "WHERE request_id=? AND owner=? AND status='running'",
                    (status, json.dumps(response, ensure_ascii=False) if response is not None else None,
                     error, utc_now_iso(), request_id, owner),
                ).rowcount
            return bool(changed)
        finally:
            conn.close()

    def stop_before_start(self, request_id: str, thread_id: str, response: dict) -> None:
        """Persist a tombstone under the stop lease; never modify an existing ID."""
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                now = utc_now_iso()
                conn.execute(
                    "INSERT OR IGNORE INTO chat_requests "
                    "(request_id,thread_id,status,response_json,created_at,updated_at) "
                    "VALUES (?,?,'interrupted',?,?,?)",
                    (request_id, thread_id, json.dumps(response, ensure_ascii=False), now, now),
                )
        finally:
            conn.close()
