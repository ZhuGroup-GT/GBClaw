"""Project metadata models."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class ProjectMeta(BaseModel):
    project_id: str
    name: str
    created_at: str
    updated_at: str
    goal: str = ""
    thread_id: str

    def touch(self) -> None:
        self.updated_at = utc_now_iso()
