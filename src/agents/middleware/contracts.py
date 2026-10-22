"""Shared request context and recoverable errors; no storage or model creation."""
from __future__ import annotations
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from langchain_core.exceptions import ContextOverflowError
from planning.mode import PlanningMode, coerce_planning_mode

if TYPE_CHECKING:
    from project_memory.snapshot.contracts import ProjectMemorySnapshot


@dataclass(frozen=True)
class ProjectRuntimeContext:
    """Agent request adapter for project data plus this turn's execution mode.

    Memory readers return ProjectMemorySnapshot without importing agents. This
    adapter belongs here because request injection and planning mode are agent
    concerns; the snapshot itself neither loads nor compresses thread dialogue.
    """

    project_id: str = ""
    project_summary: str = ""
    active_project_plan: str = ""
    pending_project_plan: str = ""
    semantic_digest: str = ""
    project_memory_health: str = ""
    planning_mode: PlanningMode = PlanningMode.AUTO

    @classmethod
    def from_memory_snapshot(
        cls, snapshot: ProjectMemorySnapshot, *, planning_mode: PlanningMode = PlanningMode.AUTO,
    ) -> ProjectRuntimeContext:
        """Serialize one memory snapshot for the existing runtime/prompt contract."""
        def pointer_json(pointer):
            return json.dumps(pointer, ensure_ascii=False, sort_keys=True) if pointer else ""

        return cls(
            project_id=snapshot.project_id,
            project_summary=snapshot.project_summary,
            active_project_plan=pointer_json(snapshot.active_project_plan),
            pending_project_plan=pointer_json(snapshot.pending_project_plan),
            semantic_digest=snapshot.semantic_digest,
            project_memory_health=json.dumps(snapshot.health, ensure_ascii=False, sort_keys=True),
            planning_mode=planning_mode,
        )


def _context_planning_mode(context) -> PlanningMode:
    if context is None:
        return PlanningMode.AUTO
    if isinstance(context, dict):
        raw_mode = context.get("planning_mode", PlanningMode.AUTO)
    else:
        raw_mode = getattr(context, "planning_mode", PlanningMode.AUTO)
    return coerce_planning_mode(raw_mode)


# Matched only on a 400/413 response, so an output max_tokens rejection does not qualify.
_PROVIDER_OVERFLOW = re.compile(
    r"context_length_exceeded|maximum context length|context window|context size"
    r"|prompt is too long|input tokens exceed|too many input tokens", re.IGNORECASE)


class ContextCompactionError(RuntimeError):
    """Raised without updating state when session compaction is unsafe.

    The message states the cause only; whether a checkpoint survives is a fact
    about the caller (a persistent chat thread or a disposable worker).
    """

    def __init__(self, message, *, code="context_recovery_required", action=None,
                 diagnostics=None):
        super().__init__(message)
        self.code = code
        self.action = action or (
            "Retry if the summary service was unavailable. For oversized input, start a new "
            "thread in this project with a narrower request and read the saved task/conclusion "
            "references before continuing. Existing computations and checkpoint evidence are preserved."
        )
        self.recoverable = True
        # Only host-generated categories/counts belong here, never provider
        # response bodies, exception text, prompts or credentials.
        self.diagnostics = dict(diagnostics or {})

    @classmethod
    def from_exception(cls, exc: BaseException) -> ContextCompactionError | None:
        """Return *exc* as a context-limit error, or None; provider text never enters it."""
        if isinstance(exc, cls):
            return exc
        status = getattr(exc, "status_code", None)
        status = status if isinstance(status, int) and 100 <= status <= 599 else None
        if not isinstance(exc, ContextOverflowError) and not (
                status in {400, 413} and _PROVIDER_OVERFLOW.search(str(exc))):
            return None
        diagnostics = {"error_type": re.sub(r"[^A-Za-z0-9_]", "", type(exc).__name__)[:80]}
        if status is not None:
            diagnostics["http_status"] = status
        return cls("The model provider rejected the request as larger than its context window.",
                   code="provider_context_overflow", diagnostics=diagnostics)

    def as_dict(self, *, checkpoint_preserved: bool = True):
        result = {"code": self.code, "message": str(self), "action": self.action,
                  "recoverable": self.recoverable, "checkpoint_preserved": checkpoint_preserved}
        if self.diagnostics:
            result["diagnostics"] = self.diagnostics
        return result


_MEMORY_BLOCK = "gbclaw_memory_context"


_SUMMARY_BLOCK = "gbclaw_project_summary"


def provider_overflow(exc: BaseException) -> ContextCompactionError | None:
    """A provider rejection for size, as a context error; the host's own errors return None."""
    if isinstance(exc, ContextCompactionError):
        return None
    return ContextCompactionError.from_exception(exc)
