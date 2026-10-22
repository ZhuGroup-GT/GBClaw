"""Request and response models for the GBclaw HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from planning.mode import PlanningMode


class HealthResponse(BaseModel):
    status: str = "ok"


class ProjectSummary(BaseModel):
    project_id: str
    name: str
    created_at: str
    updated_at: str
    goal: str = ""


class ProjectResponse(ProjectSummary):
    thread_id: str
    root: str


class ProjectListResponse(BaseModel):
    projects: list[ProjectSummary]


class CreateProjectRequest(BaseModel):
    name: str | None = Field(
        default=None,
        description="Optional display name; defaults to the generated project_id",
    )


class FileEntryResponse(BaseModel):
    name: str
    path: str
    entry_type: str = Field(..., pattern="^(file|dir)$")
    size: int | None = None
    mtime: str | None = None
    is_text: bool = False
    watch: bool = False


class FileListResponse(BaseModel):
    path: str
    entries: list[FileEntryResponse]
    fingerprint: str = ""


class FileStatResponse(BaseModel):
    path: str
    entry_type: str = Field(..., pattern="^(file|dir)$")
    size: int | None = None
    mtime: str
    is_text: bool = False
    watch: bool = False
    fingerprint: str | None = None


class FileContentResponse(BaseModel):
    path: str
    content: str
    size: int
    mtime: str
    truncated: bool = False
    watch: bool = False
    encoding: str = "utf-8"


class ProjectMemoryResponse(BaseModel):
    summary: str = ""
    semantic_digest: str = ""
    source_revision: int = 0
    latest_revision: int = 0
    changes_pending: bool = False
    fact_count: int = 0
    run_count: int = 0
    generated_at: str | None = None
    active_plan: dict | None = None
    pending_plan: dict | None = None
    retry_pending_count: int = 0
    retry_dead_letter_count: int = 0
    publication_pending_count: int = 0
    publication_blocked_count: int = 0


class HistoryTurnSummaryResponse(BaseModel):
    turn_id: str
    thread_id: str
    sequence: int
    role: str = Field(..., description="'user' or 'assistant'")
    content: str
    status: str
    created_at: str
    completed_at: str | None = None
    image_paths: list[str] = Field(default_factory=list)


class TraceEventResponse(BaseModel):
    event_id: str
    turn_id: str
    parent_event_id: str | None = None
    tool_call_id: str | None = None
    sequence: int
    event_type: str
    tool_name: str | None = None
    label: str | None = None
    input_json: str | None = None
    output_text: str | None = None
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    image_paths: list[str] = Field(default_factory=list)


class HistoryTurnDetailResponse(HistoryTurnSummaryResponse):
    reasoning: str = ""
    trace: list[TraceEventResponse] = Field(default_factory=list)


class ProjectHistoryResponse(BaseModel):
    project_id: str
    offset: int
    limit: int
    has_more: bool
    turns: list[HistoryTurnSummaryResponse]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(..., min_length=1, description="User message to send to the agent")
    project_id: str = Field(..., min_length=1, description="Active project workspace ID")
    request_id: str | None = Field(default=None, min_length=1, max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$", description="Unique client task ID for idempotent retries and targeted stop")
    thread_id: str | None = Field(
        default=None,
        description="Conversation thread ID; defaults to the project's stored thread_id",
    )
    planning_mode: PlanningMode = Field(
        default=PlanningMode.AUTO,
        description=(
            "Per-turn durable-planning policy: auto, force (/plan), or skip (/noplan)."
        ),
    )


class ChatResponse(BaseModel):
    reply: str
    project_id: str
    thread_id: str
    request_id: str | None = None
    replayed: bool = False
    image_paths: list[str] = Field(default_factory=list)
    status: str = "completed"
    recovery: dict | None = None


class ChatStopRequest(BaseModel):
    project_id: str = Field(..., min_length=1, description="Active project workspace ID")
    thread_id: str = Field(..., min_length=1, description="Conversation thread ID to stop")
    request_id: str | None = Field(default=None, min_length=1, max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$", description="Stop only the execution started with this request ID")
    message: str = Field(default="", description="Original user message for the stopped run")
    reason: str = Field(default="user_stop", description="Reason the run was stopped")
    partial_trace: list[dict] = Field(
        default_factory=list,
        description=(
            "Ordered trace segments (`kind: reasoning|reasoning_withheld|tool|agent_result`) from the UI; "
            "legacy tool-only lists still accepted"
        ),
    )
    reasoning: str = Field(
        default="",
        description="Deprecated for new clients; legacy flat reasoning used only when normalizing old-style partial_trace",
    )


class ChatStopResponse(BaseModel):
    status: str
    project_id: str
    thread_id: str
    request_id: str | None = None


class ChatCommandRecord(BaseModel):
    command: str
    planning_mode: PlanningMode
    description: str
    missing_task_message: str


class RegistryResponse(BaseModel):
    """Declarations the Web UI reads from the backend instead of copying them."""

    delegate_labels: dict[str, str]
    preserve_final_stream: list[str]
    chat_commands: list[ChatCommandRecord]
