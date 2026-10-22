"""Coordinate project-summary refresh; SQLite is the source of truth."""
from __future__ import annotations
import hashlib
import json
import logging
import threading
from pathlib import Path
from core.llm.factory import auxiliary_endpoint, build_llm
from core.llm.config import LLMEndpoint, default_max_input_tokens
from core.settings.runtime_context import RuntimeContextConfig, count_project_summary_tokens, model_max_input_tokens
from project_memory.models import SemanticSummaryRecord
from project_memory.storage.repository import MemoryRepository
from project_workspace.models import utc_now_iso
from project_workspace.store import ProjectStore
from .contracts import SUMMARY_FORMAT_VERSION, ProjectSummaryTokenLimitError, WorkingSummaryBatch, WorkingSummaryInputLimitError
from .batch import _build_working_summary_batch
from .generation import _generate_summary_within_limit
from .prompt import build_memory_summarizer_prompt
from .validation import _validate_fact_references
from .views import save_summary_view, save_archive_views
from .navigation import build_history_navigation, has_history_navigation, strip_history_navigation
from .policy import (
    SUMMARY_MAX_CHARS, SUMMARY_TARGET_CHARS, SUMMARY_RETRY_RESERVE,
    RETRIEVAL_NOTE, build_priority_summary, summary_char_limit, required_summary_keys,
)

_REFRESH_LOCKS: dict[str, threading.Lock] = {}
_REFRESH_LOCKS_GUARD = threading.Lock()
LOGGER = logging.getLogger(__name__)

def _refresh_lock(project_root: Path) -> threading.Lock:
    key = str(project_root.resolve())
    with _REFRESH_LOCKS_GUARD:
        return _REFRESH_LOCKS.setdefault(key, threading.Lock())


def _source_digest(*, revision: int) -> str:
    canonical = json.dumps(
        {
            "source_revision": revision,
            "format_version": SUMMARY_FORMAT_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def semantic_source_digest(memory: MemoryRepository) -> str:
    """Return a constant-size digest of the current semantic revision."""
    return _source_digest(revision=memory.latest_semantic_revision())


def maybe_refresh_summary(
    store: ProjectStore,
    endpoint: LLMEndpoint,
    project_id: str,
    runtime_context_config: RuntimeContextConfig,
) -> bool:
    if not isinstance(endpoint, LLMEndpoint):
        raise TypeError("semantic summary refresh requires a valid LLMEndpoint")
    if not isinstance(runtime_context_config, RuntimeContextConfig):
        raise TypeError(
            "semantic summary refresh requires a valid RuntimeContextConfig"
        )
    runtime_context_config.validate()
    project_summary_max_tokens = runtime_context_config.project_summary_max_tokens
    # Two tokens per character covers CJK summaries at the character cap.
    generation_max_tokens = min(
        project_summary_max_tokens, endpoint.resolved_max_tokens(), 2 * SUMMARY_MAX_CHARS
    )
    project_root = store.project_path(project_id)
    with _refresh_lock(project_root):
        memory = store.memory(project_id)
        state = memory.get_summary()
        if state.format_version != SUMMARY_FORMAT_VERSION:
            # Reclassify from the current records, not the old mixed prose.
            # Previously pruned deltas cannot serve as a complete rebuild source.
            state = memory.reset_summary_sources(format_version=SUMMARY_FORMAT_VERSION)
        summary_endpoint = auxiliary_endpoint(endpoint, answer_tokens=generation_max_tokens)
        summary_model = build_llm(summary_endpoint)
        latest_revision = memory.latest_semantic_revision()
        if (
            state.source_revision == latest_revision
            and state.format_version == SUMMARY_FORMAT_VERSION
            and count_project_summary_tokens(summary_model, state.content)
            <= project_summary_max_tokens
            and len(state.content) <= summary_char_limit(generation_max_tokens)
            and (not state.content or RETRIEVAL_NOTE in state.content)
            and (not state.content or has_history_navigation(state.content))
        ):
            # SQLite is authoritative. Repair the derived Markdown view whenever
            # it is missing or was edited by hand.
            save_summary_view(project_root, state.content)
            save_archive_views(project_root, memory)
            return False
        try:
            history_navigation = build_history_navigation(
                memory, state, max_chars=min(1400, summary_char_limit(generation_max_tokens) // 4),
            )
        except ValueError as exc:
            raise ProjectSummaryTokenLimitError(
                "Project summary budget cannot fit historical navigation; existing summary preserved"
            ) from exc
        system_prompt = build_memory_summarizer_prompt(
            generation_max_tokens, navigation_chars=len(history_navigation),
        )
        model_input_limits = [
            limit
            for limit in (
                endpoint.max_input_tokens,
                model_max_input_tokens(summary_model),
            )
            if limit is not None
        ] or [default_max_input_tokens()]
        summary_model_max_input = min(
            runtime_context_config.total_context_tokens, *model_input_limits
        )
        capacity_scale = (
            summary_model_max_input / runtime_context_config.total_context_tokens
        )
        reserved_tokens = max(
            int(runtime_context_config.safety_margin_tokens * capacity_scale),
            summary_endpoint.resolved_max_tokens(),
        )
        working_set_max_tokens = min(
            runtime_context_config.project_summary_working_set_max_tokens,
            summary_model_max_input - reserved_tokens,
        )
        if working_set_max_tokens <= 0:
            raise WorkingSummaryInputLimitError(
                "memory model capacity leaves no summary input budget after output "
                "and safety reserves; increase its max_input_tokens or reduce the "
                "configured reserve/output cap. Existing summary preserved."
            )
        # Old prose is never recursively fed back without a bound. Rebuild its
        # input seed from exact current records; save_summary archives the FULL
        # persisted predecessor atomically before installing the replacement.
        fallback = build_priority_summary(
            memory, model=summary_model, max_tokens=generation_max_tokens,
            max_chars=min(SUMMARY_TARGET_CHARS, summary_char_limit(generation_max_tokens)),
            history_navigation=history_navigation,
        )
        # Historical navigation is built from SQLite by the host. Feeding old
        # indexes to the summarizer would recursively copy references/topics.
        priority_input = strip_history_navigation(fallback)
        input_content = strip_history_navigation(state.content)
        if len(input_content) > SUMMARY_TARGET_CHARS:
            input_content = priority_input
        input_state = state.model_copy(update={"content": input_content})
        force = (len(state.content) > summary_char_limit(generation_max_tokens)
                 or count_project_summary_tokens(summary_model, state.content) > generation_max_tokens
                 or bool(state.content and RETRIEVAL_NOTE not in state.content)
                 or bool(state.content and not has_history_navigation(state.content)))
        summary = None
        try:
            batch = _build_working_summary_batch(
                memory=memory,
                state=input_state,
                model=summary_model,
                system_prompt=system_prompt,
                max_input_tokens=working_set_max_tokens - SUMMARY_RETRY_RESERVE,
                current_priorities="" if input_state.content == priority_input else priority_input,
                force=force,
            )
        except WorkingSummaryInputLimitError:
            # A small memory endpoint may fit the derived index but not the
            # generation prompt. Rebuild directly from current records rather
            # than retrying an impossible request. This selective index covers
            # the revision observed BEFORE source reads; later changes survive.
            LOGGER.warning("Project-summary input cannot fit; using the bounded database index")
            summary = fallback
            batch = WorkingSummaryBatch(payload={}, source_revision=latest_revision,
                                        latest_revision=latest_revision, input_tokens=0)
        if batch is None:
            # No semantic records exist. Upgrade the empty state without an LLM call.
            if state.content:
                raise WorkingSummaryInputLimitError(
                    "stored working summary has no tracked semantic source changes"
                )
            empty_state = SemanticSummaryRecord(
                format_version=SUMMARY_FORMAT_VERSION,
                source_digest=_source_digest(revision=latest_revision),
                source_revision=latest_revision,
                generated_at=utc_now_iso(),
                fact_count=memory.count_facts(),
                run_count=memory.count_runs(),
            )
            memory.save_summary(empty_state, expected_summary=state)
            save_summary_view(project_root, "")
            save_archive_views(project_root, memory)
            return False
        if summary is None:
            summary = _generate_summary_within_limit(
                summary_model=summary_model,
                system_prompt=system_prompt,
                batch_payload=batch.payload,
                max_tokens=generation_max_tokens,
                max_input_tokens=working_set_max_tokens,
                fallback_summary=fallback,
                required_fact_keys=required_summary_keys(memory, fallback),
                history_navigation=history_navigation,
                validate_citations=lambda draft: _validate_fact_references(draft, memory),
            )
        _validate_fact_references(summary, memory)
        fact_count = memory.count_facts()
        run_count = memory.count_runs()
        next_state = SemanticSummaryRecord(
            content=summary,
            format_version=SUMMARY_FORMAT_VERSION,
            source_digest=_source_digest(revision=batch.source_revision),
            source_revision=batch.source_revision,
            generated_at=utc_now_iso(),
            fact_count=fact_count,
            run_count=run_count,
        )
        memory.save_summary(
            next_state,
            prune_changes_through=batch.source_revision,
            expected_summary=state,
        )
        try:
            save_summary_view(project_root, summary)
            save_archive_views(project_root, memory)
        except Exception:
            # The database write is the authoritative commit. A later refresh or
            # project open can rebuild the human-readable view.
            LOGGER.warning("Failed to refresh summary.md view.", exc_info=True)
    store.touch_project(project_id)
    return True
