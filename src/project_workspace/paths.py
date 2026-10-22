"""Project locations and identifiers, independent of storage services."""

import os
from pathlib import Path
import re

from core.paths import REPO_ROOT

_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def get_default_projects_root() -> Path:
    """`<repo>/projects`, or ``GBCLAW_PROJECTS_ROOT`` when set (e.g. isolated test runs)."""
    override = os.environ.get("GBCLAW_PROJECTS_ROOT", "").strip()
    return Path(override).expanduser() if override else REPO_ROOT / "projects"


def validate_project_id(project_id: str) -> str:
    value = project_id.strip()
    if not value or not _PROJECT_ID_RE.fullmatch(value):
        raise ValueError(f"Invalid project_id: {project_id!r}")
    if ".." in value or "/" in value or "\\" in value:
        raise ValueError(f"Invalid project_id: {project_id!r}")
    return value
