"""Paths owned by transient project runtime state."""

from __future__ import annotations

from pathlib import Path


class RuntimeStore:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.root = self.project_root / "runtime"
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def checkpoint_path(self) -> Path:
        return self.root / "checkpoints.sqlite"
