"""Pure project-summary format and refresh contracts."""
from dataclasses import dataclass
from typing import Any

SUMMARY_FORMAT_VERSION = 4

class ProjectSummaryTokenLimitError(ValueError):
    """Generated output and its safe fallback cannot fit the configured budget."""

class WorkingSummaryInputLimitError(ValueError):
    """The source request cannot fit the configured input budget."""

class SummaryRefreshConflict(RuntimeError):
    """Another refresh replaced the summary while this writer was generating."""

@dataclass(frozen=True)
class WorkingSummaryBatch:
    payload: dict[str, Any]
    source_revision: int
    latest_revision: int
    input_tokens: int
