"""Structured semantic run, fact, and artifact indexes for a project."""

from __future__ import annotations

from pathlib import Path

from project_memory.storage.artifact_index import ArtifactIndexMixin
from project_memory.storage.changes import ChangeLogMixin
from project_memory.storage.facts import FactStoreMixin
from project_memory.storage.retry_queue import RetryQueueMixin
from project_memory.storage.runs import RunStoreMixin
from project_memory.storage.schema import _SCHEMA, SchemaMixin
from project_memory.storage.search import _SEARCH_INDEX_VERSION, SearchIndexMixin
from project_memory.summary.storage import SUMMARY_SCHEMA, SummaryStorageMixin
from project_workspace.persistence.sqlite import connect_project_db, transaction


class MemoryRepository(
    ChangeLogMixin,
    SchemaMixin,
    RunStoreMixin,
    FactStoreMixin,
    RetryQueueMixin,
    ArtifactIndexMixin,
    SearchIndexMixin,
    SummaryStorageMixin,
):
    """One project's memory database (``memory/index.sqlite``).

    Each mixin owns one group of tables: runs and their sources, versioned
    facts, the extraction retry queue, the artifact index, FTS search, the
    semantic change log and project summaries. All of them share this
    database path and its transaction helpers; opening a repository validates
    or migrates the schema once.
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.db_path = self.project_root / "memory" / "index.sqlite"
        conn = connect_project_db(self.db_path)
        try:
            self._validate_or_prepare_schema(conn)
            conn.executescript(_SCHEMA)
            conn.executescript(SUMMARY_SCHEMA)
            conn.commit()
            initialized = bool(
                conn.execute(
                    "SELECT initialized FROM semantic_revision "
                    "WHERE singleton_id = 1"
                ).fetchone()[0]
            )
            if not initialized:
                # Recheck under the write lock inside the helper. Two processes
                # may discover an old project at the same time.
                with transaction(conn):
                    self._bootstrap_semantic_changes(conn)
            search_version = int(
                conn.execute(
                    "SELECT index_version FROM memory_search_state "
                    "WHERE singleton_id = 1"
                ).fetchone()[0]
            )
            if search_version != _SEARCH_INDEX_VERSION:
                # Search is a derived index. Existing projects are rebuilt once
                # under the same write lock used by normal memory updates.
                with transaction(conn):
                    self._backfill_canonical_configs(conn)
                    self._rebuild_search_index(conn)
        finally:
            conn.close()

