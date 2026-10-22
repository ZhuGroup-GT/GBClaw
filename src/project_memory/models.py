"""Typed run, fact, extraction and query records owned by project memory."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, computed_field

from extensions import FACT_MEMORY_SPECS, Workflow


# Publish the registry vocabulary to model schemas while retaining a readable
# error and explicit query-alias compatibility at the tool boundary.
WorkflowFilter = Annotated[str, Field(json_schema_extra={"enum": [item.value for item in Workflow]})]


class RunStatus(StrEnum):
    CONFIGURED = "configured"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FactStatus(StrEnum):
    ACTIVE = "active"
    RETRACTED = "retracted"


class FactEvidenceKind(StrEnum):
    USER_EXPLICIT = "user_explicit"
    COMPLETED_RUN = "completed_run"
    ASSISTANT_REPORT = "assistant_report"
    DETERMINISTIC_WORKFLOW = "deterministic_workflow"


class FactSourceType(StrEnum):
    TOOL_RESULT = "tool_result"
    CONVERSATION = "conversation"
    AGENT_REPORT = "agent_report"
    WORKFLOW_STATE = "workflow_state"


class FactMutationAction(StrEnum):
    UPSERT = "upsert"
    RETRACT = "retract"
    NOOP = "noop"


class MemoryRetryJobStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    DEAD_LETTER = "dead_letter"


class MemoryRetryJobStage(StrEnum):
    EXTRACT = "extract"
    APPLY = "apply"
    SUMMARIZE = "summarize"


# The registry also owns the query/extraction schema vocabulary. Registering a
# deterministic kind makes it queryable without making it writable by an LLM.
FactKind = Literal[tuple(FACT_MEMORY_SPECS)]
FACT_KINDS: frozenset[str] = frozenset(FACT_MEMORY_SPECS)


class RunSourceRecord(BaseModel):
    source_run_id: str = Field(min_length=1, max_length=128)
    relation: str = Field(
        default="primary_input",
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    ordinal: int = Field(default=0, ge=0)
    source_fingerprint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunRecord(BaseModel):
    run_id: str
    workflow: Workflow
    status: RunStatus
    source_run_id: str | None = None
    source_fingerprint: str | None = None
    sources: list[RunSourceRecord] = Field(default_factory=list)
    config_path: str | None = None
    manifest_path: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    progress: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str


class FactRecord(BaseModel):
    fact_id: str
    kind: str
    lookup_key: str
    value: dict[str, Any]
    status: FactStatus = FactStatus.ACTIVE
    version: int = Field(default=1, ge=1)
    source_turn_id: str | None = None
    source_run_id: str | None = None
    evidence_kind: FactEvidenceKind = FactEvidenceKind.DETERMINISTIC_WORKFLOW
    created_at: str
    updated_at: str

    @computed_field
    @property
    def source_type(self) -> FactSourceType:
        """Describe provenance from stored evidence, never a caller's label."""
        if self.source_turn_id or self.evidence_kind in {
            FactEvidenceKind.USER_EXPLICIT, FactEvidenceKind.COMPLETED_RUN,
        }:
            return FactSourceType.CONVERSATION
        if self.evidence_kind is FactEvidenceKind.ASSISTANT_REPORT or self.kind == "agent_conclusion":
            return FactSourceType.AGENT_REPORT
        if self.kind in {"task_state", "project_plan"}:
            return FactSourceType.WORKFLOW_STATE
        return FactSourceType.TOOL_RESULT


class FactMutation(BaseModel):
    action: FactMutationAction
    kind: FactKind | None = None
    lookup_key: str | None = Field(default=None, min_length=1, max_length=240)
    value: dict[str, Any] | None = None
    expected_version: int | None = Field(default=None, ge=1)
    source_run_id: str | None = None
    evidence_kind: FactEvidenceKind | None = None
    evidence_excerpt: str = Field(default="", max_length=500)


class MemoryExtraction(BaseModel):
    mutations: list[FactMutation] = Field(default_factory=list, max_length=20)


class FactMutationApplyResult(BaseModel):
    changed_facts: list[FactRecord] = Field(default_factory=list)
    noop_count: int = Field(default=0, ge=0)
    semantic_revision_before: int = Field(ge=0)
    semantic_revision_after: int = Field(ge=0)


class MemoryRetryJobRecord(BaseModel):
    job_id: str
    assistant_turn_id: str
    referenced_run_ids: list[str] = Field(default_factory=list)
    stage: MemoryRetryJobStage
    status: MemoryRetryJobStatus
    attempts: int = Field(default=0, ge=0)
    next_attempt_at: str | None = None
    extraction: dict[str, Any] | None = None
    last_error: str = ""
    created_at: str
    updated_at: str


class SemanticSummaryRecord(BaseModel):
    content: str = ""
    format_version: int = Field(default=1, ge=1)
    source_digest: str = ""
    source_revision: int = Field(default=0, ge=0)
    generated_at: str | None = None
    fact_count: int = Field(default=0, ge=0)
    run_count: int = Field(default=0, ge=0)


class SemanticSummaryArchiveRecord(SemanticSummaryRecord):
    """An immutable historical summary, never a replacement for current evidence."""

    archive_id: str
    archived_at: str
    archive_reason: Literal["replaced", "format_reset"]


class SemanticChangeRecord(BaseModel):
    entity_type: Literal["fact", "run"]
    entity_id: str
    revision: int = Field(ge=1)
    changed_at: str


class MemorySearchResult(BaseModel):
    entity_type: Literal["fact", "run", "artifact"]
    entity_id: str
    title: str
    snippet: str
    rank: float
    updated_at: str
    reference: dict[str, Any] = Field(default_factory=dict)


class MemorySearchPage(BaseModel):
    query: str
    entity_types: list[Literal["fact", "run", "artifact"]]
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
    has_more: bool = False
    next_offset: int | None = Field(default=None, ge=0)
    results: list[MemorySearchResult] = Field(default_factory=list)
