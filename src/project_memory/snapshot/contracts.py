"""Pure data contracts for project-memory context, independent of Agent runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from project_memory.models import SemanticSummaryRecord


@dataclass(frozen=True)
class ProjectMemorySnapshot:
    """Persisted project data, independent of any Agent runtime or thread.

    This is project memory, not the conversation-continuity Session summary
    maintained by ``agents.middleware.session`` in an individual thread checkpoint.
    """

    project_id: str
    summary: SemanticSummaryRecord
    summary_valid: bool
    latest_revision: int
    active_project_plan: dict[str, Any] | None
    pending_project_plan: dict[str, Any] | None
    extraction_counts: dict[str, int]
    publication_counts: dict[str, int]

    @property
    def project_summary(self) -> str:
        """Expose only the supported source-separated project-summary format."""
        return self.summary.content if self.summary_valid else ""

    @property
    def semantic_digest(self) -> str:
        return self.summary.source_digest

    @property
    def health(self) -> dict[str, Any]:
        return {
            "summary_source_revision": self.summary.source_revision,
            "latest_semantic_revision": self.latest_revision,
            "summary_stale": (
                not self.summary_valid
                or self.summary.source_revision < self.latest_revision
            ),
            "summary_needs_rebuild": not self.summary_valid,
            "extraction": dict(self.extraction_counts),
            "publication": dict(self.publication_counts),
        }
