"""Revision-bounded source batches for the selective project working summary."""
import json
from typing import Any
from langchain_core.messages import HumanMessage, SystemMessage
from core.settings.runtime_context import count_project_summary_tokens
from project_memory.models import SemanticSummaryRecord
from project_memory.storage.repository import MemoryRepository
from .contracts import WorkingSummaryBatch, WorkingSummaryInputLimitError
from .policy import RECORD_DETAIL_MAX_CHARS

WORKING_SET_CHANGE_SCAN_LIMIT = 512

def _working_set_input_tokens(model, *, system_prompt: str, payload: dict) -> int:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    return _summary_messages_input_tokens(
        model,
        [SystemMessage(content=system_prompt), HumanMessage(content=serialized)],
    )


def _summary_messages_input_tokens(model, messages: list[Any]) -> int:
    """Count the complete summarizer request, including message overhead."""
    return sum(
        count_project_summary_tokens(model, message.content) + 4
        for message in messages
    )


def _minimal_changed_record(change, record) -> dict:
    if change.entity_type == "fact":
        return {
            "revision": change.revision,
            "changed_at": change.changed_at,
            "record": {
                "fact_id": record.fact_id,
                "kind": record.kind,
                "lookup_key": record.lookup_key,
                "status": record.status.value,
                "version": record.version,
                "evidence_kind": record.evidence_kind.value,
                "source_type": record.source_type.value,
                "operation": "retract" if record.status.value == "retracted" else "upsert",
                "source_run_id": record.source_run_id,
                "updated_at": record.updated_at,
                "value_omitted": "record exceeded working-set input budget",
            },
        }
    return {
        "revision": change.revision,
        "changed_at": change.changed_at,
        "record": {
            "run_id": record.run_id,
            "workflow": record.workflow.value,
            "status": record.status.value,
            "source_run_id": record.source_run_id,
            "sources": [{"source_run_id": item.source_run_id, "relation": item.relation,
                         "ordinal": item.ordinal, "source_fingerprint": item.source_fingerprint}
                        for item in record.sources[:8]],
            "source_count": len(record.sources),
            "manifest_path": record.manifest_path,
            "updated_at": record.updated_at,
            "details_omitted": "record exceeded working-set input budget",
        },
    }


def _changed_record(change, record) -> dict:
    return {
        "revision": change.revision,
        "changed_at": change.changed_at,
        "record": record.model_dump(mode="json"),
    }


def _build_working_summary_batch(
    *,
    memory: MemoryRepository,
    state: SemanticSummaryRecord,
    model,
    system_prompt: str,
    max_input_tokens: int,
    current_priorities: str = "",
    force: bool = False,
) -> WorkingSummaryBatch | None:
    latest_revision = memory.latest_semantic_revision()
    changes = memory.query_semantic_changes(
        after_revision=state.source_revision,
        limit=WORKING_SET_CHANGE_SCAN_LIMIT,
    )
    if not changes and not force:
        return None

    payload: dict[str, Any] = {
        "previous_working_summary": state.content,
        "current_priorities": current_priorities,
        "database_totals": {
            "facts": memory.count_facts(),
            "runs": memory.count_runs(),
        },
        "revision_window": {
            "after_revision": state.source_revision,
            "through_revision": state.source_revision,
            "latest_revision": latest_revision,
            "more_changes_pending": True,
        },
        "changed_facts": [],
        "conversation_facts": [],
        "agent_reports": [],
        "workflow_state": [],
        "changed_runs": [],
    }
    base_tokens = _working_set_input_tokens(
        model, system_prompt=system_prompt, payload=payload
    )
    if base_tokens > max_input_tokens:
        raise WorkingSummaryInputLimitError(
            "previous project working summary and prompt exceed the effective "
            f"summary input budget ({base_tokens} > {max_input_tokens} tokens); "
            "increase the memory model capacity or working-set budget. "
            "Existing summary preserved."
        )

    source_revision = state.source_revision
    input_tokens = base_tokens
    for change in changes:
        record = (
            memory.get_fact(change.entity_id)
            if change.entity_type == "fact"
            else memory.get_run(change.entity_id)
        )
        if change.entity_type == "fact":
            bucket = {
                "tool_result": "changed_facts",
                "conversation": "conversation_facts",
                "agent_report": "agent_reports",
                "workflow_state": "workflow_state",
            }[record.source_type.value]
            target = payload[bucket]
        else:
            target = payload["changed_runs"]
        from extensions import fact_memory_spec, workflow_memory_spec

        declaration = (fact_memory_spec(record.kind) if change.entity_type == "fact"
                       else workflow_memory_spec(record.workflow))
        guidance = declaration.summary_guidance
        entry = _changed_record(change, record)
        if len(json.dumps(entry, ensure_ascii=False)) > RECORD_DETAIL_MAX_CHARS:
            entry = _minimal_changed_record(change, record)
        if guidance and len(guidance) <= 500:
            entry["summary_guidance"] = guidance
        target.append(entry)
        previous_window = payload["revision_window"].copy()
        payload["revision_window"]["through_revision"] = change.revision
        payload["revision_window"]["more_changes_pending"] = (
            change.revision < latest_revision
        )
        candidate_tokens = _working_set_input_tokens(
            model, system_prompt=system_prompt, payload=payload
        )
        if candidate_tokens > max_input_tokens:
            target[-1] = _minimal_changed_record(change, record)
            if guidance and len(guidance) <= 500:
                target[-1]["summary_guidance"] = guidance
            candidate_tokens = _working_set_input_tokens(
                model, system_prompt=system_prompt, payload=payload
            )
        if candidate_tokens > max_input_tokens:
            target.pop()
            payload["revision_window"] = previous_window
            break
        source_revision = change.revision
        input_tokens = candidate_tokens

    if source_revision == state.source_revision and changes:
        raise WorkingSummaryInputLimitError(
            "working-summary input budget cannot fit the next semantic change"
        )
    payload["revision_window"]["more_changes_pending"] = (
        source_revision < latest_revision
    )
    input_tokens = _working_set_input_tokens(
        model, system_prompt=system_prompt, payload=payload
    )
    return WorkingSummaryBatch(
        payload=payload,
        source_revision=source_revision,
        latest_revision=latest_revision,
        input_tokens=input_tokens,
    )


