"""Create, list, and load user project workspaces on disk."""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from project_workspace.execution.ownership import project_lock_directory as lease_directory
from project_workspace.persistence.locks import file_lock
from core.util.io import write_json_atomic
from project_workspace.models import ProjectMeta, utc_now_iso
from project_workspace.paths import get_default_projects_root, validate_project_id

if TYPE_CHECKING:
    from project_memory.run_artifacts.store import ArtifactStore
    from project_workspace.chat.repository import ChatRepository
    from project_memory.storage.repository import MemoryRepository
    from project_memory.publication.service import RunService
    from project_workspace.execution.store import RuntimeStore


class ProjectStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or get_default_projects_root()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def list_projects(self) -> list[ProjectMeta]:
        projects: list[ProjectMeta] = []
        for path in sorted(self.root.iterdir()):
            if not path.is_dir() or path.name.startswith("."):
                continue
            try:
                projects.append(self.get(path.name))
            except ValueError:
                continue
        # Sort by created_at descending so the most recently created project
        # is first. project_id is the tiebreaker for stable ordering when
        # two projects share a created_at (e.g. created in the same wall
        # clock second).
        return sorted(projects, key=lambda meta: (meta.created_at, meta.project_id), reverse=True)

    def create(self, *, name: str | None = None) -> ProjectMeta:
        project_id = self._new_project_id()
        display_name = (name or "").strip() or project_id
        now = utc_now_iso()
        meta = ProjectMeta(
            project_id=project_id,
            name=display_name,
            created_at=now,
            updated_at=now,
            goal="",
            thread_id=str(uuid.uuid4()),
        )
        project_root = self._project_dir(project_id)
        with file_lock(lease_directory(project_root) / "metadata.lock"):
            if project_root.exists():
                raise ValueError(f"Project directory already exists: {project_id}")
            self._ensure_layout(project_root)
            self._write_project(project_root, meta)
        return meta

    def get(self, project_id: str) -> ProjectMeta:
        pid = self._validate_project_id(project_id)
        project_root = self._project_dir(pid)
        if not project_root.is_dir():
            raise ValueError(f"Project not found: {pid}")

        meta_path = project_root / "project.json"
        try:
            meta = ProjectMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"Project metadata missing: {pid}; initialize it explicitly") from exc
        return meta if meta.project_id == pid else meta.model_copy(update={"project_id": pid})

    def initialize(self, project_id: str) -> ProjectMeta:
        """Explicitly migrate a workspace; ordinary reads never create files."""
        pid = self._validate_project_id(project_id)
        root = self._project_dir(pid)
        with file_lock(lease_directory(root) / "metadata.lock"):
            if not root.is_dir():
                raise ValueError(f"Project not found: {pid}")
            meta_path = root / "project.json"
            if meta_path.exists():
                meta = self.get(pid)  # Preserve malformed metadata for diagnosis.
            else:
                now = utc_now_iso()
                meta = ProjectMeta(project_id=pid, name=pid, created_at=now,
                                   updated_at=now, thread_id=str(uuid.uuid4()))
            self._ensure_layout(root)
            self._write_project(root, meta)
            return meta

    def project_path(self, project_id: str) -> Path:
        pid = self._validate_project_id(project_id)
        project_root = self._project_dir(pid)
        if not project_root.is_dir():
            raise ValueError(f"Project not found: {pid}")
        return project_root

    def touch_project(self, project_id: str) -> ProjectMeta:
        root = self._project_dir(self._validate_project_id(project_id))
        with file_lock(lease_directory(root) / "metadata.lock"):
            meta = self.get(project_id)
            meta.touch()
            self._write_project(root, meta)
            return meta

    def chat(self, project_id: str) -> ChatRepository:
        from project_workspace.chat.repository import ChatRepository

        return ChatRepository(self.project_path(project_id))

    def memory(self, project_id: str) -> MemoryRepository:
        from project_memory.storage.repository import MemoryRepository

        return MemoryRepository(self.project_path(project_id))

    def artifacts(self, project_id: str) -> ArtifactStore:
        from project_memory.run_artifacts.store import ArtifactStore

        return ArtifactStore(self.project_path(project_id))

    def runtime(self, project_id: str) -> RuntimeStore:
        from project_workspace.execution.store import RuntimeStore

        return RuntimeStore(self.project_path(project_id))

    def runs(self, project_id: str) -> RunService:
        from project_memory.publication.service import RunService

        return RunService(self.project_path(project_id))

    def delete(self, project_id: str) -> None:
        """Remove a project workspace directory from disk."""
        pid = self._validate_project_id(project_id)
        project_root = self._project_dir(pid)
        with file_lock(lease_directory(project_root) / "metadata.lock"):
            if not project_root.is_dir():
                raise ValueError(f"Project not found: {pid}")
            shutil.rmtree(project_root)

    def _project_dir(self, project_id: str) -> Path:
        return self.root / project_id

    def _new_project_id(self) -> str:
        date = datetime.now(timezone.utc).strftime("%Y%m%d")
        suffix = uuid.uuid4().hex[:6]
        return f"proj-{date}-{suffix}"

    def _validate_project_id(self, project_id: str) -> str:
        return validate_project_id(project_id)

    def _write_project(self, project_root: Path, meta: ProjectMeta) -> None:
        # All callers hold the external metadata lock; this method must never
        # recreate a deleted project or initialize databases on an update.
        if not project_root.is_dir():
            raise ValueError(f"Project not found: {meta.project_id}")
        write_json_atomic(project_root / "project.json", meta.model_dump())

    def _ensure_layout(self, project_root: Path) -> None:
        from project_workspace.chat.repository import ChatRepository
        from project_memory.storage.repository import MemoryRepository
        from project_memory.summary.views import save_summary_view

        for path in (
            project_root / "chat",
            project_root / "memory",
            project_root / "artifacts",
            project_root / "runtime",
        ):
            path.mkdir(parents=True, exist_ok=True)

        ChatRepository(project_root)
        memory = MemoryRepository(project_root)
        summary = project_root / "memory" / "summary.md"
        if not summary.exists():
            save_summary_view(project_root, memory.get_summary().content)
