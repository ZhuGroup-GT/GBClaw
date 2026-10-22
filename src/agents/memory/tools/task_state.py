"""Explicit bounded read of durable task cursors, including unfinished work."""
from __future__ import annotations

from langchain_core.tools import tool

from core.util.graph_config import runtime_ids
from project_workspace.store import ProjectStore
from project_memory.storage.task_state import TaskStateService
from agents.memory.tools.output import paged_json_result


def make_get_task_state_tool(store: ProjectStore):
    @tool
    def get_task_state(lookup_key: str | None = None, thread_id: str | None = None,
                       offset_chars: int = 0, max_chars: int = 12_000,
                       snapshot_id: str | None = None) -> str:
        """Read an execution cursor, including running/failed/partial invocations.

        With no arguments, read the current chat thread. Pass a prior
        task_state_ref.lookup_key for an exact invocation. This is execution
        state, not a scientific conclusion or approval to run a computation.
        A running cursor is created before the worker starts its tools and may
        describe your own invocation. It is not proof of a submitted scientific
        job. Do not skip assigned work or claim a computation is running from
        this cursor alone; verify actual run records and matching inputs first.
        Large results are lossless JSON pages; continue with
        snapshot_id=full_output.output_id and offset_chars=next_offset_chars.
        """
        project_id, chat_thread_id = runtime_ids()
        if not project_id:
            return "No active project_id in run config."
        selected_thread = thread_id or chat_thread_id
        if not lookup_key and not selected_thread:
            return "An active chat thread or exact task-state lookup key is required."
        try:
            result = TaskStateService(store, project_id).get(thread_id=selected_thread, lookup_key=lookup_key)
            return paged_json_result(result, store=store, project_id=project_id, tool_name="get_task_state",
                                     offset_chars=offset_chars, max_chars=max_chars,
                                     snapshot_id=snapshot_id)
        except (ValueError, OSError) as exc:
            return str(exc)
    get_task_state.metadata = {"gbclaw_projects_root": str(store.root)}
    return get_task_state
