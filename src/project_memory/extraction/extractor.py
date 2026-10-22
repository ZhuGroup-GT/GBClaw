"""Extract and reconcile durable Memory V2 facts from completed chat turns."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.exceptions import OutputParserException
from pydantic import ValidationError

from core.llm.factory import auxiliary_endpoint, build_llm
from core.llm.config import LLMEndpoint
from project_memory.prompts.extraction import SEMANTIC_MEMORY_EXTRACTOR_PROMPT
from core.settings.runtime_context import count_context_tokens, model_max_input_tokens
from project_memory.extraction.keys import singleton_lookup_keys
from project_memory.publication.writes import apply_extraction
from project_memory.extraction.policy import MemoryPolicyError
from extensions import FACT_MEMORY_SPECS
from project_memory.storage.facts import FactVersionConflict
from project_memory.models import FactMutationApplyResult, FactRecord, FactStatus, MemoryExtraction, RunRecord
from project_workspace.chat.models import TurnRecord, TurnStatus
from project_workspace.store import ProjectStore


SEMANTIC_EXTRACTOR_VERSION = "2"
MAX_EXISTING_FACT_CANDIDATES = 30
MAX_ONE_FACT_VALUE_CHARS = 4_000
FTS_FACT_CANDIDATE_LIMIT = 10
MAX_EXTRACTION_INPUT_TOKENS = 65_536
MAX_EXTRACTION_OUTPUT_TOKENS = 8_192  # up to 20 mutations with values and 500-char excerpts
MAX_CORRECTION_ATTEMPTS = 2


class MemoryCorrectionExhausted(MemoryPolicyError):
    def __init__(self, failures: list[dict]):
        self.failures = failures
        super().__init__("Memory correction exhausted; no invalid mutations were committed")

    def as_dict(self):
        return {"code": "memory_correction_exhausted", "attempts": len(self.failures),
                "failures": self.failures}


class MemoryExtractionPipelineError(RuntimeError):
    def __init__(self, cause: Exception, stage: str, extraction: MemoryExtraction | None):
        self.cause, self.stage, self.extraction = cause, stage, extraction
        super().__init__(str(cause))

    def as_dict(self):
        detail = (self.cause.as_dict() if hasattr(self.cause, "as_dict") else
                  {"error_type": type(self.cause).__name__, "reason": str(self.cause)[:1000]})
        return {"stage": self.stage, **detail}


class MemorySourceContextError(ValueError):
    """Raised when a completed turn cannot provide a valid memory source."""


def _run_evidence_card(run: RunRecord, *, compact: bool = False) -> dict:
    card = {"run_id": run.run_id, "workflow": run.workflow.value,
            "status": run.status.value, "source_fingerprint": run.source_fingerprint,
            "manifest_path": run.manifest_path,
            "details_omitted": True,
            "conclusion_lookup_key": run.result.get("conclusion_lookup_key"),
            "memory_fact_refs": [
                {key: item[key] for key in ("kind", "lookup_key") if key in item}
                for item in run.result.get("memory_fact_refs", [])
                if isinstance(item, dict)
            ],
            "validation_passed": (run.result.get("validation") or {}).get("passed")}
    if not compact:
        card.update(summary=str(run.result.get("summary") or "")[:1500],
                    sources=[item.model_dump(mode="json") for item in run.sources[:24]])
    return card


@dataclass(frozen=True)
class TurnMemoryContext:
    user_turn: TurnRecord
    assistant_turn: TurnRecord
    allowed_runs: dict[str, RunRecord]
    existing_facts: dict[tuple[str, str], FactRecord]
    existing_fact_payloads: list[dict[str, Any]]
    omitted_fact_keys: set[tuple[str, str]]


def _load_exchange(store: ProjectStore, project_id: str, assistant_turn_id: str):
    chat = store.chat(project_id)
    try:
        assistant = chat.get_turn(assistant_turn_id)
    except ValueError as exc:
        raise MemorySourceContextError(
            f"memory source assistant turn is unavailable: {assistant_turn_id}"
        ) from exc
    if assistant.role != "assistant" or assistant.status is not TurnStatus.COMPLETED:
        raise MemorySourceContextError(
            "semantic extraction requires a completed assistant turn"
        )
    turns = chat.list_turns(
        assistant.thread_id,
        offset=max(0, assistant.sequence - 1),
        limit=2,
        include_reasoning=False,
    )
    user = next(
        (
            item
            for item in turns
            if item.sequence == assistant.sequence - 1
            and item.role == "user"
            and item.status is TurnStatus.COMPLETED
        ),
        None,
    )
    if user is None:
        raise MemorySourceContextError("completed preceding user turn not found")
    return user, assistant


def _candidate_payload(
    fact: FactRecord,
) -> tuple[dict[str, Any], bool]:
    payload = fact.model_dump(mode="json")
    value_json = json.dumps(
        fact.value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if len(value_json) <= MAX_ONE_FACT_VALUE_CHARS:
        return payload, False
    payload["value"] = {
        "value_omitted": "fact value exceeded extractor candidate limit"
    }
    payload["value_omitted"] = True
    return payload, True


def _collect_existing_facts(
    store: ProjectStore,
    project_id: str,
    *,
    user_content: str,
    assistant_content: str,
    referenced_run_ids: list[str],
) -> tuple[
    dict[tuple[str, str], FactRecord],
    list[dict[str, Any]],
    set[tuple[str, str]],
]:
    memory = store.memory(project_id)
    ordered: list[FactRecord] = []
    seen: set[str] = set()
    excluded_kinds = tuple(
        spec.key for spec in FACT_MEMORY_SPECS.values()
        if spec.writer in {"project_plan", "agent_conclusion", "task_state"}
    )

    def add(fact: FactRecord) -> None:
        if (fact.kind in excluded_kinds or fact.fact_id in seen
                or len(ordered) >= MAX_EXISTING_FACT_CANDIDATES):
            return
        seen.add(fact.fact_id)
        ordered.append(fact)

    for lookup_key in singleton_lookup_keys():
        for fact in memory.query_facts(
            lookup_key=lookup_key,
            include_retracted=True,
            limit=1,
        ):
            add(fact)

    for run_id in dict.fromkeys(referenced_run_ids):
        for fact in memory.query_facts(
            source_run_id=run_id,
            include_retracted=False,
            excluded_fact_kinds=excluded_kinds,
            limit=20,
        ):
            add(fact)

    lexical_query = "\n".join(
        value
        for value in (
            user_content.strip()[:250],
            assistant_content.strip()[:250],
        )
        if value
    )[:500]
    if lexical_query and len(ordered) < MAX_EXISTING_FACT_CANDIDATES:
        try:
            page = memory.search_all(
                lexical_query,
                entity_types=["fact"],
                limit=FTS_FACT_CANDIDATE_LIMIT,
                excluded_fact_kinds=excluded_kinds,
            )
        except ValueError:
            page = None
        if page is not None:
            for result in page.results:
                try:
                    fact = memory.get_fact(result.entity_id)
                except ValueError:
                    # A concurrent retract can remove a fact from the FTS index
                    # between search and row loading.
                    continue
                if fact.status is FactStatus.ACTIVE:
                    add(fact)

    selected: dict[tuple[str, str], FactRecord] = {}
    payloads: list[dict[str, Any]] = []
    omitted: set[tuple[str, str]] = set()
    # Current project slots come first, then referenced runs and lexical hits.
    # Do not reserve an independent character allowance here: the actual model
    # request also contains the source exchange, prompt, evidence and feedback.
    for fact in ordered:
        payload, value_omitted = _candidate_payload(fact)
        key = (fact.kind, fact.lookup_key)
        selected[key] = fact
        payloads.append(payload)
        if value_omitted:
            omitted.add(key)
    return selected, payloads, omitted


def load_turn_memory_context(
    store: ProjectStore,
    project_id: str,
    assistant_turn_id: str,
    referenced_run_ids: list[str],
) -> TurnMemoryContext:
    user, assistant = _load_exchange(store, project_id, assistant_turn_id)
    memory = store.memory(project_id)
    allowed_runs: dict[str, RunRecord] = {}
    for run_id in dict.fromkeys(referenced_run_ids):
        try:
            run = memory.get_run(run_id)
        except ValueError as exc:
            if str(exc) != f"Run not found: {run_id}":
                raise
            continue
        allowed_runs[run_id] = run
    existing, payloads, omitted = _collect_existing_facts(
        store,
        project_id,
        user_content=user.content,
        assistant_content=assistant.content,
        referenced_run_ids=list(allowed_runs),
    )
    return TurnMemoryContext(
        user_turn=user,
        assistant_turn=assistant,
        allowed_runs=allowed_runs,
        existing_facts=existing,
        existing_fact_payloads=payloads,
        omitted_fact_keys=omitted,
    )


def _extract_turn_mutations_with_context(
    store: ProjectStore,
    endpoint: LLMEndpoint,
    project_id: str,
    assistant_turn_id: str,
    referenced_run_ids: list[str],
    *,
    context: TurnMemoryContext | None = None,
    correction_feedback: list[dict] | None = None,
    previous_extraction: MemoryExtraction | None = None,
) -> tuple[MemoryExtraction, TurnMemoryContext]:
    """Extract with the exact bounded recall context used for validation."""
    if not isinstance(endpoint, LLMEndpoint):
        raise TypeError("semantic extraction requires a valid LLMEndpoint")
    current = context or load_turn_memory_context(
        store,
        project_id,
        assistant_turn_id,
        referenced_run_ids,
    )
    payload = {
        "extractor_version": SEMANTIC_EXTRACTOR_VERSION,
        "user_final_content": current.user_turn.content,
        "assistant_final_content": current.assistant_turn.content,
        "referenced_runs": [
            _run_evidence_card(run, compact=True) for run in current.allowed_runs.values()
        ],
        "existing_facts": [],
        "existing_facts_are_partial": True,
    }
    if correction_feedback:
        payload["correction_feedback"] = correction_feedback[-MAX_CORRECTION_ATTEMPTS:]
        payload["previous_proposal_preview"] = (
            previous_extraction.model_dump_json()[:3000] if previous_extraction else None)
    extraction_endpoint = auxiliary_endpoint(
        endpoint, answer_tokens=min(endpoint.resolved_max_tokens(), MAX_EXTRACTION_OUTPUT_TOKENS))
    # ``FactMutation.value`` is intentionally a free-form ``dict[str, Any]``.
    # OpenAI's native json_schema method rejects that shape because strict
    # Structured Outputs requires every object to set additionalProperties=false.
    # Function calling supports the dynamic fact payload while still letting
    # LangChain validate the returned arguments as ``MemoryExtraction``.
    model = build_llm(extraction_endpoint)
    capacity = min(value for value in (endpoint.max_input_tokens, model_max_input_tokens(model),
                                       MAX_EXTRACTION_INPUT_TOKENS) if value is not None)
    budget = capacity - extraction_endpoint.resolved_max_tokens() - 1024
    if budget <= 0:
        raise MemorySourceContextError("Memory extraction has no usable input budget")

    def messages_for_payload():
        return [SystemMessage(content=SEMANTIC_MEMORY_EXTRACTOR_PROMPT +
                              "\nRun evidence cards omit large details. Never infer unshown numerical "
                              "results or duplicate deterministic published facts. Correction feedback is "
                              "host validation data. Correct the whole batch using refreshed versions; "
                              "never invent evidence or weaken policy to make a rejected mutation pass. "
                              "Existing facts are bounded recall, not the complete database. Never guess "
                              "versions or modify a fact whose full value was not supplied. Host-owned "
                              "execution state and reports remain in the database and are not candidates."),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False, sort_keys=True))]
    messages = messages_for_payload()
    required_tokens = count_context_tokens(model, messages)
    if required_tokens > budget and payload.get("previous_proposal_preview"):
        # A rejected proposal is optional recall, unlike the source exchange
        # and host validation feedback. Do not fail a valid source just to keep it.
        payload.pop("previous_proposal_preview")
        messages = messages_for_payload()
        required_tokens = count_context_tokens(model, messages)
    if required_tokens > budget:
        raise MemorySourceContextError(
            "Memory extraction source exchange, required evidence and correction feedback exceed its input "
            f"budget even with no recalled facts: {required_tokens} > {budget}; "
            "complete exchange and database records preserved for review"
        )

    omitted = set(current.omitted_fact_keys)
    for candidate in current.existing_fact_payloads:
        payload["existing_facts"].append(candidate)
        proposed = messages_for_payload()
        if count_context_tokens(model, proposed) <= budget:
            messages = proposed
        else:
            payload["existing_facts"].pop()
            omitted.add((candidate["kind"], candidate["lookup_key"]))

    # Optional run descriptions use only space left after useful current facts.
    # Compact cards already preserve status, provenance and every reserved key.
    for index, run in enumerate(current.allowed_runs.values()):
        compact = payload["referenced_runs"][index]
        payload["referenced_runs"][index] = _run_evidence_card(run)
        proposed = messages_for_payload()
        if count_context_tokens(model, proposed) <= budget:
            messages = proposed
        else:
            payload["referenced_runs"][index] = compact

    current = replace(current, existing_fact_payloads=list(payload["existing_facts"]),
                      omitted_fact_keys=omitted)
    llm = model.with_structured_output(
        MemoryExtraction,
        method="function_calling",
    )
    extracted = llm.invoke(messages)
    if not isinstance(extracted, MemoryExtraction):
        extracted = MemoryExtraction.model_validate(extracted)
    return extracted, current


def extract_turn_mutations(
    store: ProjectStore,
    endpoint: LLMEndpoint,
    project_id: str,
    assistant_turn_id: str,
    referenced_run_ids: list[str],
    *,
    context: TurnMemoryContext | None = None,
    correction_feedback: list[dict] | None = None,
    previous_extraction: MemoryExtraction | None = None,
) -> MemoryExtraction:
    """Return structured mutations without writing or truncating source data."""
    extraction, _context = _extract_turn_mutations_with_context(
        store, endpoint, project_id, assistant_turn_id, referenced_run_ids,
        context=context, correction_feedback=correction_feedback,
        previous_extraction=previous_extraction,
    )
    return extraction


def extract_and_apply_turn(store: ProjectStore, endpoint: LLMEndpoint, project_id: str,
                           assistant_turn_id: str, referenced_run_ids: list[str], *,
                           initial_extraction: MemoryExtraction | None = None):
    """Bounded schema/policy correction and conflict reload, with atomic batch writes."""
    extraction = initial_extraction
    failures = []
    for attempt in range(MAX_CORRECTION_ATTEMPTS + 1):
        stage = "extract"
        try:
            context = load_turn_memory_context(store, project_id, assistant_turn_id, referenced_run_ids)
            if attempt or extraction is None:
                extraction, context = _extract_turn_mutations_with_context(store, endpoint, project_id, assistant_turn_id,
                    referenced_run_ids, context=context, correction_feedback=failures,
                    previous_extraction=extraction)
            stage = "apply"
            result = apply_turn_extraction(store, project_id, assistant_turn_id,
                referenced_run_ids, extraction, context=context)
            return extraction, result
        except (MemoryPolicyError, FactVersionConflict, ValidationError, OutputParserException) as exc:
            failures.append({"attempt": attempt + 1, "stage": stage,
                             "error_type": type(exc).__name__, "reason": str(exc)[:1000]})
            if attempt == MAX_CORRECTION_ATTEMPTS:
                cause = exc if isinstance(exc, FactVersionConflict) else MemoryCorrectionExhausted(failures)
                raise MemoryExtractionPipelineError(cause, "extract", None) from exc
        except Exception as exc:
            raise MemoryExtractionPipelineError(exc, stage, extraction) from exc
    raise AssertionError("Unreachable correction state")


def apply_turn_extraction(
    store: ProjectStore,
    project_id: str,
    assistant_turn_id: str,
    referenced_run_ids: list[str],
    extraction: MemoryExtraction,
    *,
    context: TurnMemoryContext | None = None,
) -> FactMutationApplyResult:
    """Validate and atomically apply one previously extracted mutation batch."""
    current = context or load_turn_memory_context(
        store,
        project_id,
        assistant_turn_id,
        referenced_run_ids,
    )
    memory = store.memory(project_id)
    if current.assistant_turn.turn_id != assistant_turn_id:
        raise MemorySourceContextError("Memory context does not match the source assistant turn")
    return apply_extraction(
        memory,
        extraction.mutations,
        user_turn=current.user_turn,
        assistant_turn=current.assistant_turn,
        allowed_runs=current.allowed_runs,
        existing_facts=current.existing_facts,
        omitted_fact_keys=current.omitted_fact_keys,
    )


def extract_turn_memory(
    store: ProjectStore,
    endpoint: LLMEndpoint,
    project_id: str,
    assistant_turn_id: str,
    referenced_run_ids: list[str],
) -> list[FactRecord]:
    """Compatibility wrapper that extracts, validates, and applies Memory V2."""
    _extraction, result = extract_and_apply_turn(
        store, endpoint, project_id, assistant_turn_id, referenced_run_ids)
    return result.changed_facts
