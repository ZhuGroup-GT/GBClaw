"""One model-facing boundary for all tools; original outputs remain durable.

This does not change tool implementations, their API/UI responses, run records,
scientific facts, or the raw checkpoint messages. Explicit detail-reading tools
implement their own paging and are never semantically re-summarized here.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from core.util.graph_config import runtime_project_id
from extensions import TOOL_MEMORY_SPECS, resolve_hook
from agents.memory.decisions import payload_requires_user
from project_workspace.store import ProjectStore
from project_memory.storage.tool_outputs import ToolOutputStore

MODEL_RESULT_CHARS = 16_000
MIN_PROJECTION_CHARS = 4_000
# Plain-text replies get the same model budget as JSON conclusions.
TEXT_RESULT_CHARS = MODEL_RESULT_CHARS - 600
LOGGER = logging.getLogger(__name__)
from extensions import TOOL_CAPABILITIES

DETAIL_READ_TOOLS = frozenset(key for key, spec in TOOL_CAPABILITIES.items() if spec.memory_read)
_DETAIL_KEYS = frozenset({"trace", "messages", "stdout", "stderr", "log", "logs", "history",
                         "minimization_history", "all_minimizations_converged", "coordinates", "positions",
                         "orientations", "rmsd_profile", "profile", "profiles", "curves", "samples",
                         "raw_data", "raw_output", "events", "annotations", "diagnostics", "source_metadata"})
_PRIORITY = (
    "status", "error", "errors", "summary", "conclusion", "conclusions", "answer", "message",
    "final_convergence", "final_minimization_converged", "final_converged", "converged",
    "warnings", "limitations", "analysis_status", "analysis_followups", "required_action",
    "requires_user_decision", "selected_candidate_id", "candidate_burgers_vectors", "burgers_vector_summary",
    "edge_vector_summary", "metrics", "results", "outcomes", "result", "data",
    "request_id", "interaction", "interaction_request", "interaction_requests", "api_path",
    "plan_id", "draft_id", "version", "base_version", "approval_status", "approval_required",
    "run_id", "workflow", "manifest", "source_run_id", "source_artifact_id", "purpose",
    "reused", "memory_fact_refs", "conclusion_lookup_key", "conclusion_fact_id", "conclusion_ref", "conclusion_id", "task_state_ref",
    "presentation_policy", "summary_image_path", "image_paths", "diagnostic_image_paths", "artifacts", "plot_path", "png_path", "data_path",
    "review_priority_candidate", "alternative_candidates", "short_motif_candidates", "longer_candidates", "lattice_filter", "retry_without_changes",
    "short_candidates", "long_candidates", "long_observability_status", "candidates",
    "pbc_size_comparison", "edge_analysis", "id", "label", "roles", "length_angstrom",
    "x_translation_angstrom", "lattice_pairs", "source_direction_counts", "source_signed_directions",
    "grain_notations", "lattice_notation", "candidate_secondary_misfit", "orientation", "orientations",
    "geometry", "relative_mismatch",
    "options", "top_size", "bottom_size", "construction_mismatch_percent", "agent_size_policy_violations",
)


def _encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _reference_marker(value: Any) -> dict:
    result = {"details_omitted": True, "read_from": "full_output"}
    if isinstance(value, (list, dict, str)):
        result["item_count" if not isinstance(value, str) else "character_count"] = len(value)
    return result


def _is_detail(key: str, value: Any) -> bool:
    # A pair of grain bases / a small set of selected vertices are conclusions;
    # per-atom orientations/coordinates are detailed numerical data.
    if key in {"orientations", "coordinates", "positions"}:
        return not isinstance(value, (dict, list)) or len(_encoded(value)) > 1800
    return key in _DETAIL_KEYS


def _project(value: Any, *, key: str = "", depth: int = 0, width: int = 8,
             preferred: tuple[str, ...] = ()) -> Any:
    if key == "worker_outcome":
        # A typed contract is indivisible; projection markers are not evidence.
        return value
    if _is_detail(key, value) and isinstance(value, (dict, list, str)) and len(value):
        # Artifact paths sometimes use names such as "log". Keep references.
        if isinstance(value, str) and len(value) < 300 and "\n" not in value:
            return value
        return _reference_marker(value)
    if isinstance(value, str):
        limit = 1800 if key in {"summary", "conclusion", "error", "message"} else 700
        if len(value) > limit:
            return value[:limit] + f" … [full text in full_output; {len(value)} characters]"
        return value
    if not isinstance(value, (dict, list)):
        return value
    if depth >= 7:
        return _reference_marker(value)
    if isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value) and len(value) <= 12:
            return [_project(item, depth=depth+1, width=width) for item in value]
        # Control-flow follow-ups and image references are few in normal runs and
        # must survive the same boundary as the scientific conclusion.
        count = max(width, 24) if key in {"analysis_followups", "image_paths", "memory_fact_refs"} else width
        rows = [_project(item, key="", depth=depth+1, width=width) for item in value[:count]]
        if len(value) > count:
            rows.append({"omitted_items": len(value)-count, "total_items": len(value), "read_from": "full_output"})
        return rows
    ordered = list(dict.fromkeys((*preferred, *_PRIORITY, *value.keys())))
    # Favor conclusion-bearing fields, then scalars over large incidental data.
    keys = [name for name in ordered if name in value and not _is_detail(name, value[name])]
    limit = max(12, width*5)
    chosen = keys[:limit]
    omitted = [name for name in value if name not in chosen]
    result = {name: _project(value[name], key=name, depth=depth+1, width=width) for name in chosen}
    if omitted:
        result["detail_fields_available"] = omitted
    return result


def _fit_budget(value: dict, limit: int) -> dict:
    """Fit whole fields without ever returning malformed or silently cut JSON."""
    if len(_encoded(value)) <= limit:
        return value
    result = {}
    omitted = []
    priority = list(dict.fromkeys(("worker_outcome", "worker_outcome_preview", "run_id", "workflow", "status", "manifest", "error", "artifacts", "result",
                                   "full_output", "model_output", *_PRIORITY, *value.keys())))
    for key in priority:
        if key not in value:
            continue
        item = value[key]
        available = limit - len(_encoded(result)) - len(key) - 150
        if available < 150:
            omitted.append(key)
            continue
        if len(_encoded(item)) > available:
            if key == "worker_outcome":
                omitted.append(key)
                continue
            elif isinstance(item, dict):
                item = _fit_budget(item, available)
            elif isinstance(item, list):
                kept = []
                for entry in item:
                    if len(_encoded([*kept, entry])) > available - 100:
                        break
                    kept.append(entry)
                item = [*kept, {"omitted_items": len(item)-len(kept), "read_from": "full_output"}]
            elif isinstance(item, str):
                item = item[:max(0, available//2)] + " … [full text in full_output]"
        if len(_encoded({**result, key: item})) <= limit - 80:
            result[key] = item
        else:
            omitted.append(key)
    if omitted:
        marker = {"detail_fields_omitted": len(omitted), "read_from": "full_output"}
        if len(_encoded({**result, "additional_details": marker})) <= limit:
            result["additional_details"] = marker
    return result


def _finish_projection(result: dict, original: dict) -> dict:
    """Keep a valid typed outcome, or an explicitly separate bounded preview."""
    explicit = original.get("worker_outcome")
    if explicit is not None:
        from agents.workers.outcome import WorkerOutcome

        contract = WorkerOutcome.model_validate(explicit).model_dump(mode="json")
        result = {key: value for key, value in result.items() if key != "worker_outcome"}
        if len(_encoded(contract)) <= MODEL_RESULT_CHARS // 2:
            result["worker_outcome"] = contract
        else:
            result["worker_outcome_preview"] = {
                "status": contract["status"],
                "summary": _project(contract["summary"], key="summary"),
                "next_action": _project(contract["next_action"]),
                "evidence_reference_count": len(contract["evidence_refs"]),
                "read_from": "full_output",
            }
    return _fit_budget(result, MODEL_RESULT_CHARS - 600)


def compact_tool_payload(payload: dict, *, tool_name: str = "") -> dict:
    """Pure deterministic projection, shared by tools and worker handoffs.

    New tools use the generic policy automatically; exceptional conclusion keys
    can be prioritized once in ToolMemorySpec.model_result_keys. This function
    does not itself persist: callers retain/archive originals before using it.
    """
    if (isinstance(payload.get("status"), str) and payload["status"] in {"ok", "error"}
            and isinstance(payload.get("trace"), list) and isinstance(payload.get("summary"), str)
            and isinstance(payload.get("data", {}), dict) and isinstance(payload.get("artifacts", []), list)
            and all(isinstance(item, str) for item in payload.get("artifacts", []))):
        # Extract durable run identities before removing an internal trace.
        from agents.workers.result import WorkerResult, compact_worker_result
        worker = WorkerResult(**{field.name: payload[field.name] for field in fields(WorkerResult) if field.name in payload})
        result = asdict(compact_worker_result(worker))
        result["summary"] = _project(result["summary"], key="summary")
        if result.get("error"):
            result["error"] = _project(result["error"], key="error")
        result["model_output"] = {"mode": "conclusions_and_references", "details_available": True}
        return _finish_projection(result, payload)
    view = payload.get("model_view")
    if isinstance(view, dict):
        # The tool already bounded its conclusions for the model; keep that view
        # whole when it fits. The caller archives the full payload beside it.
        result = (json.loads(_encoded(view)) if len(_encoded(view)) <= MODEL_RESULT_CHARS - 600
                  else _project(view))
        result["model_output"] = {"mode": "conclusions_and_references", "details_available": True}
        return _finish_projection(result, payload)
    spec = TOOL_MEMORY_SPECS.get(tool_name)
    preferred = spec.model_result_keys if spec else ()
    envelope = {key: payload[key] for key in ("run_id", "workflow", "status", "manifest", "error") if key in payload}
    value = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    is_envelope = value is not payload
    if spec and spec.model_result_builder:
        value = resolve_hook(spec.model_result_builder)(json.loads(_encoded(value)))
        if not isinstance(value, dict):
            raise ValueError(f"{tool_name} model result builder must return a dict")
    for width in (8, 5, 3, 1):
        projected = _project(value, width=width, preferred=preferred)
        if is_envelope:
            result = {**_project(envelope), "result": projected}
            if "artifacts" in payload:
                result["artifacts"] = [
                    {k: item[k] for k in ("artifact_id", "role", "path", "media_type") if k in item}
                    for item in payload["artifacts"][:40] if isinstance(item, dict)
                ]
                if len(payload["artifacts"]) > 40:
                    result["artifacts_total"] = len(payload["artifacts"])
        else:
            result = projected
        if len(_encoded(result)) <= MODEL_RESULT_CHARS:
            break
    result["model_output"] = {"mode": "conclusions_and_references", "details_available": True}
    return _finish_projection(result, payload)


def _declared_model_view(tool_name: str, raw: str) -> dict | None:
    """The tool's declared compact view of a large reply, or None for the generic one."""
    capability = TOOL_CAPABILITIES.get(tool_name)
    if capability is None or not capability.model_view:
        return None
    try:
        view = resolve_hook(capability.model_view)(raw)
    except Exception:
        # A view is a convenience; never lose the reply because a view failed.
        LOGGER.warning("Model view for %s failed; using the generic preview", tool_name, exc_info=True)
        return None
    return view if isinstance(view, dict) else None


def _text_preview(raw: str) -> str:
    """The leading text that fits the model budget, ending at a line boundary."""
    head = raw[:TEXT_RESULT_CHARS]
    cut = head.rfind("\n")
    if cut > TEXT_RESULT_CHARS * 4 // 5:
        head = head[:cut]
    return f"{head}\n… [{len(raw) - len(head)} more characters in full_output]"


def project_tool_messages(messages, *, store: ProjectStore | None,
                          project_id: str | None):
    """Return model-only copies; checkpoint/raw trace objects are never mutated."""
    call_names = {
        call["id"]: call["name"] for message in messages
        for call in (getattr(message, "tool_calls", None) or [])
        if isinstance(call, dict) and isinstance(call.get("id"), str) and isinstance(call.get("name"), str)
    }
    output = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            output.append(message)
            continue
        tool_name = message.name or call_names.get(message.tool_call_id, "")
        if tool_name in DETAIL_READ_TOOLS:
            output.append(message)
            continue
        content = message.content
        raw = content if isinstance(content, str) else _encoded(content)
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = None
        pending_decision = payload_requires_user(parsed)
        worker_trace = (isinstance(parsed, dict) and isinstance(parsed.get("summary"), str)
                               and isinstance(parsed.get("trace"), list) and bool(parsed["trace"]))
        if isinstance(parsed, dict) and isinstance(parsed.get("model_output"), dict):
            reference = parsed.get("full_output")
            if (parsed["model_output"].get("mode") == "conclusions_and_references"
                    and len(raw) <= MODEL_RESULT_CHARS and isinstance(reference, dict)
                    and reference.get("tool") == "read_tool_output"
                    and isinstance(reference.get("output_id"), str)
                    and len(reference["output_id"]) == 64 and store is not None and project_id):
                try:
                    original = ToolOutputStore(store.project_path(project_id)).read(reference["output_id"])
                except (OSError, ValueError):
                    pass
                else:
                    if original.get("tool_name") == tool_name:
                        archived_content = original["content"]
                        archived_raw = (archived_content if isinstance(archived_content, str)
                                        else _encoded(archived_content))
                        try:
                            archived_payload = json.loads(archived_raw)
                        except (ValueError, TypeError):
                            archived_payload = None
                        if payload_requires_user(archived_payload):
                            from agents.workers.lifecycle import current_worker_invocation
                            from extensions import agent_memory_spec
                            from agents.memory.tools.output import invocation_thread_id

                            invocation = current_worker_invocation(required=False)
                            if (invocation is not None
                                    and agent_memory_spec(invocation.definition.agent).artifact_access != "read"):
                                thread_id = invocation_thread_id()
                                if not thread_id or original.get("scope_thread_id") != thread_id:
                                    output.append(message)
                                    continue
                            try:
                                if not isinstance(archived_payload, dict):
                                    raise ValueError("Projected archive must contain a JSON object")
                                expected = compact_tool_payload(archived_payload, tool_name=tool_name)
                                expected["full_output"] = reference
                                if expected == parsed:
                                    from agents.memory.tools.output import _agent_visible_content

                                    restored = _agent_visible_content(original)
                                    restored = restored if isinstance(restored, str) else _encoded(restored)
                                    output.append(message.model_copy(update={
                                        "content": restored,
                                        "response_metadata": {
                                            **message.response_metadata,
                                            "context_editing": {"strategy": "restored_pending_output"},
                                        },
                                    }))
                                    continue
                            except (ValueError, TypeError, KeyError):
                                pass
                            # A mismatched or inaccessible archive cannot
                            # authorize another lossy pass over a pending view.
                            output.append(message)
                            continue
                        output.append(message)
                        continue
        if pending_decision:
            # All options and constraints belong to the current decision. A
            # width/string/depth preview must not silently remove any of them.
            if (isinstance(parsed, dict) and parsed.get("trace")
                    and (tool_name.startswith("delegate_")
                         or isinstance(parsed.get("data"), dict))):
                from agents.memory.tools.output import _agent_visible_content

                visible = _agent_visible_content({"tool_name": tool_name, "content": content})
                visible = visible if isinstance(visible, str) else _encoded(visible)
                output.append(message.model_copy(update={"content": visible}))
            else:
                output.append(message)
            continue
        tool_view = isinstance(parsed, dict) and isinstance(parsed.get("model_view"), dict)
        if len(raw) <= MIN_PROJECTION_CHARS and not worker_trace and not tool_view:
            output.append(message)
            continue
        declared_view = None if tool_view or worker_trace else _declared_model_view(tool_name, raw)
        if declared_view is not None:
            compact = compact_tool_payload({"model_view": declared_view}, tool_name=tool_name)
        elif isinstance(parsed, dict):
            compact = compact_tool_payload(parsed, tool_name=tool_name)
        elif len(raw) <= TEXT_RESULT_CHARS:
            output.append(message)
            continue
        else:
            compact = {"summary": _text_preview(raw), "original_characters": len(raw),
                       "model_output": {"mode": "conclusions_and_references", "details_available": True}}
        if store is not None and project_id:
            # Refuse to silently lose details if durable storage fails. The raw
            # checkpoint and caller receive an actionable error instead.
            from agents.memory.tools.output import invocation_thread_id
            reference = ToolOutputStore(store.project_path(project_id)).write(
                content, tool_name=tool_name, scope_thread_id=invocation_thread_id())
            compact["full_output"] = reference
        else:
            # Nonproject use has no durable read capability: leave the original
            # intact rather than inventing an inaccessible archive reference.
            output.append(message)
            continue
        output.append(message.model_copy(update={"content": _encoded(compact)}))
    return output


class ModelToolOutputMiddleware(AgentMiddleware):
    """Universal model-only projection, including custom/non-scientific tools."""
    def __init__(self, projects_root: Path | None = None, *, store: ProjectStore | None = None):
        self.store = store if store is not None else ProjectStore(projects_root)

    def _request(self, request):
        context = getattr(getattr(request, "runtime", None), "context", None)
        project_id = getattr(context, "project_id", None)
        if isinstance(context, dict):
            project_id = context.get("project_id")
        if not project_id:
            project_id = runtime_project_id()
        messages = project_tool_messages(request.messages, store=self.store, project_id=project_id)
        return request.override(messages=messages)

    def wrap_model_call(self, request, handler):
        return handler(self._request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._request(request))
