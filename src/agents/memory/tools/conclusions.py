"""Bounded access to final agent conclusions indexed in project memory."""

from __future__ import annotations

import json

from langchain_core.tools import tool

from project_memory.storage.conclusions import AgentConclusionService
from project_workspace.store import ProjectStore
from core.util.graph_config import runtime_project_id
from agents.memory.tools.output import text_page


def make_agent_conclusion_tools(store: ProjectStore):
    @tool
    def read_agent_conclusion(conclusion_id: str, offset_chars: int = 0,
                              max_chars: int = 12_000, section: str = "report") -> str:
        """Read an indexed final agent conclusion in verified, bounded pages.

        Get conclusion_id from query_project_memory(entity='facts',
        fact_kind='agent_conclusion') or search_project_memory. Read the report
        for the full final findings; section='metadata' provides complete source
        references and purpose. Continue with next_offset_chars until complete.
        This reads conclusion memory only, never chat history or execution traces.
        """
        project_id = runtime_project_id()
        if not project_id:
            return json.dumps({"error": "No active project_id in run config."})
        try:
            result = AgentConclusionService(store, project_id).read(conclusion_id, section=section)
            content = result.pop("content")
            return json.dumps({**result, **text_page(content, offset_chars=offset_chars, max_chars=max_chars)},
                              ensure_ascii=False)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    read_agent_conclusion.metadata = {"gbclaw_projects_root": str(store.root)}
    return [read_agent_conclusion]


__all__ = ["make_agent_conclusion_tools"]
