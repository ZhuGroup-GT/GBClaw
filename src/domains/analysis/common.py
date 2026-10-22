"""Artifact resolution shared by analysis tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from project_workspace.store import ProjectStore


def verified_artifact_path(
    *,
    store: ProjectStore,
    service: Any,
    project_id: str,
    run_id: str,
    artifact: Any,
) -> Path:
    """Resolve an indexed artifact only when its run manifest agrees."""
    if artifact.run_id != run_id or service.project_root != store.project_path(project_id):
        raise ValueError("Artifact source identity does not match the active project/run")
    return service.resolve_artifact(artifact)
