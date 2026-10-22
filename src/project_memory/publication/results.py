"""Run references that scientific tools return across the agent/tool boundary."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from project_memory.models import RunStatus, Workflow


class RunToolResult(BaseModel):
    """Stable workflow identity returned across the agent/tool boundary."""

    run_id: str
    workflow: Workflow
    status: RunStatus
    manifest: str
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    # A tool's bounded view for the model boundary; the full result stays beside it.
    model_view: dict[str, Any] | None = None

    def to_json(self) -> str:
        payload = self.model_dump(mode="json")
        if payload.get("model_view") is None:
            payload.pop("model_view", None)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


