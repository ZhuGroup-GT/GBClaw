"""Explicit paged access to complete tool invocation outputs."""
from __future__ import annotations

import json

from langchain_core.tools import tool

from project_workspace.store import ProjectStore
from project_memory.storage.tool_outputs import ToolOutputStore
from core.util.graph_config import runtime_project_id, graph_thread_id

DEFAULT_DETAIL_CHARS = 12_000
MAX_DETAIL_CHARS = 24_000


def _agent_visible_content(original: dict):
    """Legacy delegate archives may contain human-only conversation traces.

    Keep their full final conclusion and scientific references readable, never
    their conversation. This changes only the reader's view, not stored bytes.
    """
    content = original["content"]
    try:
        value = json.loads(content) if isinstance(content, str) else content
    except (ValueError, TypeError):
        if str(original.get("tool_name", "")).startswith("delegate_"):
            raise ValueError("Unstructured delegate archive is human-only; query project memory conclusions instead")
        return content
    is_worker = isinstance(value, dict) and isinstance(value.get("summary"), str) and isinstance(value.get("trace"), list)
    if not (is_worker or str(original.get("tool_name", "")).startswith("delegate_")):
        return content
    if not isinstance(value, dict):
        raise ValueError("Delegate conversation archives are human-only")
    if is_worker:
        from dataclasses import asdict, fields
        from agents.workers.result import WorkerResult, compact_worker_result
        worker = WorkerResult(**{f.name: value[f.name] for f in fields(WorkerResult) if f.name in value})
        value = {**asdict(compact_worker_result(worker)), "summary": value["summary"]}

    def without_conversation(item):
        if isinstance(item, dict):
            return {key: without_conversation(part) for key, part in item.items()
                    if key not in {"trace", "messages", "history", "reasoning", "chat_history"}}
        if isinstance(item, list):
            return [without_conversation(part) for part in item]
        return item

    return without_conversation(value)


def text_page(text: str, *, offset_chars: int = 0, max_chars: int = DEFAULT_DETAIL_CHARS) -> dict:
    if offset_chars < 0 or offset_chars > len(text):
        raise ValueError(f"offset_chars must be between 0 and {len(text)}")
    limit = min(MAX_DETAIL_CHARS, max(1, max_chars))
    end = min(len(text), offset_chars + limit)
    return {"content": text[offset_chars:end], "offset_chars": offset_chars,
            "end_chars": end, "total_chars": len(text), "complete": end == len(text),
            "next_offset_chars": end if end < len(text) else None}


def paged_json_result(payload: dict, *, store: ProjectStore, project_id: str,
                      tool_name: str, offset_chars: int = 0,
                      max_chars: int = DEFAULT_DETAIL_CHARS,
                      snapshot_id: str | None = None) -> str:
    """Lossless JSON paging for explicit memory/index queries, with a snapshot."""
    outputs = ToolOutputStore(store.project_path(project_id))
    if snapshot_id:
        original = outputs.read(snapshot_id)
        if original["tool_name"] != tool_name or original.get("scope_thread_id") != invocation_thread_id():
            raise ValueError("Snapshot belongs to a different tool or invocation")
        serialized = original["content"]
        payload = json.loads(serialized)
    else:
        if offset_chars:
            raise ValueError("Character continuation requires snapshot_id from full_output.output_id")
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    page = text_page(serialized, offset_chars=offset_chars, max_chars=max_chars)
    if offset_chars == 0 and page["complete"]:
        return serialized
    reference = outputs.write(
        serialized, tool_name=tool_name, scope_thread_id=invocation_thread_id())
    return json.dumps({"format": "serialized_json_page", **page,
                       "full_output": reference,
                       "snapshot_id": reference["output_id"],
                       **{key: payload[key] for key in (
                           "entity", "has_more", "next_cursor", "offset_records", "source_revision",
                           "presentation_policy", "next_offset", "total"
                       ) if key in payload}}, ensure_ascii=False)


def invocation_thread_id() -> str | None:
    return graph_thread_id()


def make_read_tool_output_tool(store: ProjectStore, *, current_invocation_only: bool = False):
    @tool
    def read_tool_output(output_id: str, offset_chars: int = 0,
                         max_chars: int = DEFAULT_DETAIL_CHARS,
                         json_pointer: str | None = None) -> str:
        """Read archived scientific tool output in bounded pages, on demand.

        Use the output_id from a tool result's full_output reference. Optional
        RFC6901 json_pointer selects a specific JSON field, e.g. /result/candidates/0,
        before paging. Continue with next_offset_chars until complete=true.
        Originals belong to the active project; this does not rerun any tool.
        Chat history and delegated conversation traces are never available here.
        Use read_agent_conclusion for a saved worker conclusion.
        """
        project_id = runtime_project_id()
        if not project_id:
            return "No active project_id in run config."
        try:
            original = ToolOutputStore(store.project_path(project_id)).read(output_id)
            if current_invocation_only and (not invocation_thread_id() or original.get("scope_thread_id") != invocation_thread_id()):
                raise ValueError("This worker may only read outputs from its current invocation")
            content = _agent_visible_content(original)
            if json_pointer is not None:
                value = json.loads(content) if isinstance(content, str) else content
                if json_pointer and not json_pointer.startswith("/"):
                    raise ValueError("json_pointer must be empty or begin with /")
                for segment in json_pointer.split("/")[1:]:
                    key = segment.replace("~1", "/").replace("~0", "~")
                    if isinstance(value, list):
                        if not key.isdecimal():
                            raise ValueError("JSON array pointer must use a nonnegative index")
                        value = value[int(key)]
                    else:
                        value = value[key]
                content = json.dumps(value, ensure_ascii=False, indent=2)
            elif not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            return json.dumps({"output_id": output_id, "tool_name": original["tool_name"],
                               "json_pointer": json_pointer,
                               **text_page(content, offset_chars=offset_chars, max_chars=max_chars)}, ensure_ascii=False)
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
    read_tool_output.metadata = {"gbclaw_projects_root": str(store.root)}
    return read_tool_output
