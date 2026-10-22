"""Per-project SQLite checkpointers routed by project_id in run config."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.sqlite import SqliteSaver

from project_workspace.paths import get_default_projects_root
from project_workspace.execution.store import RuntimeStore


def _project_id_from_config(config: RunnableConfig) -> str:
    configurable = config.get("configurable") or {}
    project_id = configurable.get("project_id")
    if project_id:
        return str(project_id).strip()

    metadata = config.get("metadata") or {}
    project_id = metadata.get("project_id")
    if project_id:
        return str(project_id).strip()

    raise ValueError("project_id is required in run config (configurable or metadata)")


class ProjectCheckpointRouter(BaseCheckpointSaver[str]):
    """Route checkpoint IO to ``projects/<id>/runtime/checkpoints.sqlite``."""

    def __init__(self, projects_root: Path | None = None) -> None:
        super().__init__()
        self._root = (projects_root or get_default_projects_root()).resolve()
        self._savers: dict[str, SqliteSaver] = {}
        self._lock = threading.Lock()

    def _checkpoint_db(self, project_id: str) -> Path:
        return RuntimeStore(self._root / project_id).checkpoint_path

    def _saver_for(self, project_id: str) -> SqliteSaver:
        with self._lock:
            saver = self._savers.get(project_id)
            if saver is not None:
                return saver

            db_path = self._checkpoint_db(project_id)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            saver = SqliteSaver(conn)
            self._savers[project_id] = saver
            return saver

    def close(self) -> None:
        with self._lock:
            for saver in self._savers.values():
                try:
                    saver.conn.close()
                except Exception:
                    pass
            self._savers.clear()

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self._saver_for(_project_id_from_config(config)).get_tuple(config)

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self._saver_for(_project_id_from_config(config)).put(
            config,
            checkpoint,
            metadata,
            new_versions,
        )

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self._saver_for(_project_id_from_config(config)).put_writes(
            config,
            writes,
            task_id,
            task_path,
        )

    def delete_thread(self, config: RunnableConfig) -> None:
        """Delete one thread's checkpoints and pending writes in its project."""
        thread_id = (config.get("configurable") or {}).get("thread_id")
        if thread_id is None or not str(thread_id).strip():
            raise ValueError("thread_id is required in run config (configurable)")
        self._saver_for(_project_id_from_config(config)).delete_thread(str(thread_id))

    def get(self, config: RunnableConfig) -> Checkpoint | None:
        return self._saver_for(_project_id_from_config(config)).get(config)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        if config is None:
            return iter(())
        return self._saver_for(_project_id_from_config(config)).list(
            config,
            filter=filter,
            before=before,
            limit=limit,
        )

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self.get_tuple(config)

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self.put_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, config: RunnableConfig) -> None:
        self.delete_thread(config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        for item in self.list(config, filter=filter, before=before, limit=limit):
            yield item
