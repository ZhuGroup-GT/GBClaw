"""Typed records owned by project artifacts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from pydantic import BaseModel, Field


class ProvenanceClass(StrEnum):
    PRIMARY = "primary"
    DERIVED = "derived"
    DEBUG = "debug"

    @property
    def permanent(self) -> bool:
        return self is not ProvenanceClass.DEBUG


class ArtifactRecord(BaseModel):
    artifact_id: str
    run_id: str
    role: str
    path: str
    provenance_class: ProvenanceClass
    media_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
