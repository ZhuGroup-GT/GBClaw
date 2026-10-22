"""Persistent retry orchestration for the synchronous Memory V2 pipeline."""

from __future__ import annotations

import logging
import json
import uuid
from datetime import datetime, timedelta, timezone

from core.llm.config import LLMEndpoint
from core.settings.runtime_context import RuntimeContextConfig
from project_memory.extraction.extractor import (
    MemorySourceContextError,
    extract_and_apply_turn,
    MemoryExtractionPipelineError,
)
from project_memory.extraction.policy import MemoryPolicyError
from project_memory.storage.facts import FactVersionConflict
from project_memory.storage.schema import MemorySchemaVersionError
from project_workspace.models import utc_now_iso
from project_memory.models import (
    MemoryExtraction,
    MemoryRetryJobRecord,
    MemoryRetryJobStage,
    MemoryRetryJobStatus,
)
from project_workspace.store import ProjectStore
from project_memory.summary.contracts import (
    ProjectSummaryTokenLimitError,
    WorkingSummaryInputLimitError,
)
from project_memory.summary.service import maybe_refresh_summary


LOGGER = logging.getLogger(__name__)
MAX_RETRY_ATTEMPTS = 5
PROCESSING_STALE_SECONDS = 600
MAX_ERROR_CHARS = 4_000
_RETRY_DELAYS_SECONDS = (5, 30, 120, 600, 3_600)


class MemoryRetryPayloadError(ValueError):
    """Raised when a persisted retry payload is missing or invalid."""


def requeue_legacy_extraction_budget_failure(
    store: ProjectStore,
    project_id: str,
    assistant_turn_id: str,
    *,
    dry_run: bool = True,
) -> bool:
    """Explicitly recover one failure from the former independent recall budget.

    By default only report eligibility. With ``dry_run=False``, enqueue at most
    the named turn for the normal bounded retry worker; never invoke a model or
    reset attempts here. This is deliberately not called during app startup.
    Genuine source-only overflow and other permanent failures remain untouched.
    """
    memory = store.memory(project_id)
    job = memory.get_retry_job_for_turn(assistant_turn_id)
    if (job is None or job.status is not MemoryRetryJobStatus.DEAD_LETTER
            or job.stage is not MemoryRetryJobStage.EXTRACT
            or job.attempts >= MAX_RETRY_ATTEMPTS):
        return False
    legacy_reason = (
        "Memory extraction input exceeds its budget; exchange preserved for review"
    )
    try:
        error = json.loads(job.last_error)
    except (TypeError, ValueError):
        matches = job.last_error == f"MemorySourceContextError: {legacy_reason}"
    else:
        matches = (isinstance(error, dict)
                   and error.get("error_type") == "MemorySourceContextError"
                   and error.get("reason") == legacy_reason)
    if not matches:
        return False
    if dry_run:
        return True
    changed = memory.requeue_dead_letter_retry_job(
        job.job_id, expected_last_error=job.last_error, now=utc_now_iso(),
        max_attempts=MAX_RETRY_ATTEMPTS,
    )
    if changed:
        LOGGER.info("Requeued legacy extraction-budget failure for project=%s turn=%s job=%s",
                    project_id, assistant_turn_id, job.job_id)
    return changed


def requeue_dead_letter_extraction_failures(
    store: ProjectStore,
    project_id: str,
    *,
    error_type: str,
    dry_run: bool = True,
) -> list[str]:
    """Requeue every dead-lettered extraction whose recorded error class a host fix resolved.

    This is for failures caused by host or provider configuration, such as a
    request parameter the model rejected, never for source overflow or policy
    failures. Attempts are reset because none of them exercised the source.
    Returns the affected assistant turn IDs; with ``dry_run`` only the eligible ones.
    """
    memory = store.memory(project_id)
    affected: list[str] = []
    for job in memory.list_retry_jobs(status=MemoryRetryJobStatus.DEAD_LETTER):
        if job.stage is not MemoryRetryJobStage.EXTRACT:
            continue
        try:
            recorded = json.loads(job.last_error or "")
        except (TypeError, ValueError):
            continue
        if not isinstance(recorded, dict) or recorded.get("error_type") != error_type:
            continue
        if dry_run or memory.requeue_dead_letter_retry_job(
                job.job_id, expected_last_error=job.last_error, now=utc_now_iso(),
                max_attempts=MAX_RETRY_ATTEMPTS, reset_attempts=True):
            affected.append(job.assistant_turn_id)
    if affected and not dry_run:
        LOGGER.info("Requeued %d dead-lettered %s extraction failures for project=%s",
                    len(affected), error_type, project_id)
    return affected


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def _iso_after(seconds: int, *, now: str) -> str:
    return (_parse_iso(now) + timedelta(seconds=seconds)).replace(
        microsecond=0
    ).isoformat()


def _job_id(assistant_turn_id: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"gbclaw-memory-retry:{assistant_turn_id}",
        )
    )


def _error_text(exc: BaseException) -> str:
    value = (json.dumps(exc.as_dict(), ensure_ascii=False) if hasattr(exc, "as_dict")
             else f"{type(exc).__name__}: {exc}".strip())
    return value[:MAX_ERROR_CHARS]


def _is_permanent(exc: BaseException) -> bool:
    if isinstance(exc, MemoryExtractionPipelineError):
        exc = exc.cause
    return isinstance(
        exc,
        (
            MemoryPolicyError,
            MemoryRetryPayloadError,
            MemorySchemaVersionError,
            MemorySourceContextError,
            ProjectSummaryTokenLimitError,
            WorkingSummaryInputLimitError,
            TypeError,
        ),
    )


def _next_retry_at(attempts: int, *, now: str) -> str:
    # ``attempts`` counts background retry executions, not the initial
    # synchronous attempt. The initial failure therefore uses delay index 0.
    index = min(max(attempts, 0), len(_RETRY_DELAYS_SECONDS) - 1)
    return _iso_after(_RETRY_DELAYS_SECONDS[index], now=now)


def _record_failure(
    store: ProjectStore,
    project_id: str,
    *,
    assistant_turn_id: str,
    referenced_run_ids: list[str],
    stage: MemoryRetryJobStage,
    exc: BaseException,
    extraction: MemoryExtraction | None,
    attempts: int | None = None,
) -> MemoryRetryJobRecord | None:
    """Persist a retry or dead-letter record; never hide the original failure."""
    try:
        memory = store.memory(project_id)
        current = memory.get_retry_job_for_turn(assistant_turn_id)
        now = utc_now_iso()
        resolved_attempts = (
            attempts
            if attempts is not None
            else (current.attempts if current is not None else 0)
        )
        permanent = _is_permanent(exc)
        exhausted = resolved_attempts >= MAX_RETRY_ATTEMPTS
        status = (
            MemoryRetryJobStatus.DEAD_LETTER
            if permanent or exhausted
            else MemoryRetryJobStatus.RETRY_WAIT
        )
        job = MemoryRetryJobRecord(
            job_id=current.job_id if current is not None else _job_id(assistant_turn_id),
            assistant_turn_id=assistant_turn_id,
            referenced_run_ids=list(dict.fromkeys(referenced_run_ids)),
            stage=stage,
            status=status,
            attempts=resolved_attempts,
            next_attempt_at=(
                None
                if status is MemoryRetryJobStatus.DEAD_LETTER
                else _next_retry_at(resolved_attempts, now=now)
            ),
            extraction=(
                extraction.model_dump(mode="json")
                if extraction is not None
                else None
            ),
            last_error=_error_text(exc),
            created_at=current.created_at if current is not None else now,
            updated_at=now,
        )
        saved = memory.save_retry_job(job)
        LOGGER.warning(
            "Memory V2 %s failed for project=%s turn=%s; job=%s status=%s",
            stage.value,
            project_id,
            assistant_turn_id,
            saved.job_id,
            saved.status.value,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return saved
    except Exception:
        LOGGER.exception(
            "Failed to persist Memory V2 retry job for project=%s turn=%s",
            project_id,
            assistant_turn_id,
        )
        return None


def _refresh_summary_after_fact_failure(
    store: ProjectStore,
    endpoint: LLMEndpoint,
    project_id: str,
    runtime_context_config: RuntimeContextConfig,
) -> None:
    """Preserve the old behavior that run changes can still reach the summary."""
    try:
        maybe_refresh_summary(
            store,
            endpoint,
            project_id,
            runtime_context_config,
        )
    except Exception:
        # The primary fact retry will reach the summarize stage after it
        # succeeds. Pending semantic changes also remain durable in SQLite.
        LOGGER.warning(
            "Summary refresh after a Memory V2 fact-stage failure also failed "
            "for project=%s; pending semantic changes were preserved.",
            project_id,
            exc_info=True,
        )


def process_turn_memory_with_retry(
    store: ProjectStore,
    endpoint: LLMEndpoint,
    project_id: str,
    assistant_turn_id: str,
    referenced_run_ids: list[str],
    runtime_context_config: RuntimeContextConfig,
) -> bool:
    """Run Memory V2 synchronously and persist the failed stage for retry."""
    extraction: MemoryExtraction | None = None
    try:
        extraction, _result = extract_and_apply_turn(
            store,
            endpoint,
            project_id,
            assistant_turn_id,
            referenced_run_ids,
        )
    except Exception as exc:
        _record_failure(
            store,
            project_id,
            assistant_turn_id=assistant_turn_id,
            referenced_run_ids=referenced_run_ids,
            stage=MemoryRetryJobStage(getattr(exc, "stage", "extract")),
            exc=exc,
            extraction=getattr(exc, "extraction", None),
        )
        _refresh_summary_after_fact_failure(
            store, endpoint, project_id, runtime_context_config
        )
        return False

    try:
        maybe_refresh_summary(
            store,
            endpoint,
            project_id,
            runtime_context_config,
        )
    except Exception as exc:
        _record_failure(
            store,
            project_id,
            assistant_turn_id=assistant_turn_id,
            referenced_run_ids=referenced_run_ids,
            stage=MemoryRetryJobStage.SUMMARIZE,
            exc=exc,
            extraction=extraction,
        )
        return False
    return True


def _save_processing_stage(
    store: ProjectStore,
    project_id: str,
    job: MemoryRetryJobRecord,
    *,
    stage: MemoryRetryJobStage,
    extraction: MemoryExtraction | None,
) -> MemoryRetryJobRecord:
    now = utc_now_iso()
    return store.memory(project_id).save_retry_job(
        job.model_copy(
            update={
                "stage": stage,
                "status": MemoryRetryJobStatus.PROCESSING,
                "next_attempt_at": None,
                "extraction": (
                    extraction.model_dump(mode="json")
                    if extraction is not None
                    else None
                ),
                "last_error": "",
                "updated_at": now,
            }
        )
    )


def drain_one_retry_job(
    store: ProjectStore,
    endpoint: LLMEndpoint,
    project_id: str,
    runtime_context_config: RuntimeContextConfig,
) -> bool:
    """Process at most one ready retry job for one project."""
    now = utc_now_iso()
    stale_before = (
        _parse_iso(now) - timedelta(seconds=PROCESSING_STALE_SECONDS)
    ).replace(microsecond=0).isoformat()
    memory = store.memory(project_id)
    job = memory.claim_ready_retry_job(now=now, stale_before=stale_before)
    if job is None:
        return False
    if job.attempts > MAX_RETRY_ATTEMPTS:
        _record_failure(
            store,
            project_id,
            assistant_turn_id=job.assistant_turn_id,
            referenced_run_ids=job.referenced_run_ids,
            stage=job.stage,
            exc=RuntimeError("memory retry attempts exhausted"),
            extraction=None,
            attempts=job.attempts,
        )
        return True

    extraction: MemoryExtraction | None = None
    try:
        if job.extraction is not None:
            try:
                extraction = MemoryExtraction.model_validate(job.extraction)
            except (TypeError, ValueError) as exc:
                raise MemoryRetryPayloadError(
                    "retry job has invalid extraction payload"
                ) from exc

        if job.stage in {MemoryRetryJobStage.EXTRACT, MemoryRetryJobStage.APPLY}:
            if job.stage is MemoryRetryJobStage.APPLY and extraction is None:
                raise MemoryRetryPayloadError(
                    "apply-stage retry job has no extraction payload"
                )
            extraction, _result = extract_and_apply_turn(store, endpoint, project_id,
                job.assistant_turn_id, job.referenced_run_ids,
                initial_extraction=extraction if job.stage is MemoryRetryJobStage.APPLY else None)
            job = _save_processing_stage(
                store,
                project_id,
                job,
                stage=MemoryRetryJobStage.SUMMARIZE,
                extraction=extraction,
            )

        if job.stage is MemoryRetryJobStage.SUMMARIZE:
            maybe_refresh_summary(
                store,
                endpoint,
                project_id,
                runtime_context_config,
            )
            completed = job.model_copy(
                update={
                    "status": MemoryRetryJobStatus.COMPLETED,
                    "next_attempt_at": None,
                    "last_error": "",
                    "updated_at": utc_now_iso(),
                }
            )
            memory.save_retry_job(completed)
        return True
    except FactVersionConflict as exc:
        _record_failure(
            store,
            project_id,
            assistant_turn_id=job.assistant_turn_id,
            referenced_run_ids=job.referenced_run_ids,
            stage=MemoryRetryJobStage.EXTRACT,
            exc=exc,
            extraction=None,
            attempts=job.attempts,
        )
    except Exception as exc:
        _record_failure(
            store,
            project_id,
            assistant_turn_id=job.assistant_turn_id,
            referenced_run_ids=job.referenced_run_ids,
            stage=MemoryRetryJobStage(getattr(exc, "stage", job.stage.value)),
            exc=exc,
            extraction=getattr(exc, "extraction", extraction),
            attempts=job.attempts,
        )
    return True


def drain_ready_retry_jobs(
    store: ProjectStore,
    endpoint: LLMEndpoint,
    runtime_context_config: RuntimeContextConfig,
    *,
    max_jobs: int = 10,
) -> int:
    """Best-effort drain across current V2 projects without opening old projects."""
    processed = 0
    if max_jobs < 1:
        return 0
    for project_path in sorted(store.root.iterdir()):
        if processed >= max_jobs:
            break
        if not project_path.is_dir() or project_path.name.startswith("."):
            continue
        try:
            if drain_one_retry_job(
                store,
                endpoint,
                project_path.name,
                runtime_context_config,
            ):
                processed += 1
        except MemorySchemaVersionError:
            continue
        except Exception:
            LOGGER.exception(
                "Failed to drain Memory V2 retry job for project=%s",
                project_path.name,
            )
    return processed
