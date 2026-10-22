"""Typed records owned by project chat."""

from __future__ import annotations

from enum import StrEnum
from pydantic import BaseModel, Field


class TurnStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class TurnRecord(BaseModel):
    turn_id: str
    thread_id: str
    sequence: int = Field(ge=0)
    role: str
    content: str = ""
    reasoning: str = ""
    status: TurnStatus
    created_at: str
    completed_at: str | None = None


class TraceEventRecord(BaseModel):
    event_id: str
    turn_id: str
    parent_event_id: str | None = None
    tool_call_id: str | None = None
    sequence: int = Field(ge=0)
    event_type: str
    tool_name: str | None = None
    label: str | None = None
    input_json: str | None = None
    output_text: str | None = None
    status: str
    started_at: str | None = None
    completed_at: str | None = None
