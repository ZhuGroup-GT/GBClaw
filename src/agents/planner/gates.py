"""Authoritative plan gates, independent of conversational summaries."""
from __future__ import annotations
import json
from typing import TYPE_CHECKING
from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import AnyMessage, HumanMessage, ToolMessage
from project_memory.identifiers import PENDING_PLAN_LOOKUP_KEY
from planning.mode import PlanningMode
from planning.authorization import explicit_plan_decision
from extensions import tool_capability, worker_definition_for_delegate
from agents.middleware.contracts import _context_planning_mode
if TYPE_CHECKING:
    from project_workspace.store import ProjectStore


def _tool_name(tool) -> str:
    if isinstance(tool, dict):
        return str((tool.get("function") or tool).get("name") or "")
    return str(getattr(tool, "name", "") or "")


def _tool_policy(name: str, tool=None) -> tuple[str, str]:
    """Resolve host declarations, including a newly assembled worker's metadata."""
    worker = worker_definition_for_delegate(name)
    capability = tool_capability(name)
    metadata = getattr(tool, "metadata", None) or {}
    execution = worker.execution_kind if worker else metadata.get("execution_kind", "read")
    if (not worker and capability and capability.plan_effect == "none"
            and capability.operation in {"scientific", "configuration", "interaction"}):
        execution = "scientific"
    plan_effect = capability.plan_effect if capability else "none"
    if worker and worker.execution_kind == "planning":
        plan_effect = "draft"
    return execution, plan_effect


def _blocked_by_plan_mode(name: str, mode: str, tool=None) -> bool:
    execution, effect = _tool_policy(name, tool)
    if mode == "force":
        return execution == "scientific" or effect in {"update", "approve"}
    if mode == "skip":
        return execution == "planning" or effect != "none"
    if mode == "pending":
        return execution in {"scientific", "planning"} or effect in {"draft", "update"}
    return False


class ForcePlanGateMiddleware(AgentMiddleware):
    """When the user forced planning, block scientific work for this turn."""

    @staticmethod
    def _is_force_mode(request) -> bool:
        runtime = getattr(request, "runtime", None)
        context = getattr(runtime, "context", None)
        return _context_planning_mode(context) is PlanningMode.FORCE

    def _blocked_message(self, request) -> ToolMessage:
        return ToolMessage(
            content=(
                "This turn was started with a forced-planning request (/plan). "
                "Call delegate_plan with the user's full objective, then summarize "
                "the draft and end the turn. Do not run scientific delegates or "
                "approve a plan in the same turn."
            ),
            tool_call_id=str(request.tool_call.get("id") or ""),
            name=str(request.tool_call.get("name") or ""),
            status="error",
        )

    def _intercept(self, request) -> ToolMessage | None:
        if not self._is_force_mode(request):
            return None
        tool_name = str(request.tool_call.get("name") or "")
        if _blocked_by_plan_mode(tool_name, "force", getattr(request, "tool", None)):
            return self._blocked_message(request)
        return None

    _model_tool_name = staticmethod(_tool_name)

    def _planning_only_request(self, request: ModelRequest):
        if not self._is_force_mode(request):
            return request
        visible_tools = [
            tool
            for tool in request.tools
            if not _blocked_by_plan_mode(self._model_tool_name(tool), "force", tool)
        ]
        return request.override(tools=visible_tools)

    def wrap_model_call(self, request, handler):
        return handler(self._planning_only_request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._planning_only_request(request))

    def wrap_tool_call(self, request, handler):
        blocked = self._intercept(request)
        if blocked is not None:
            return blocked
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        blocked = self._intercept(request)
        if blocked is not None:
            return blocked
        return await handler(request)


class SkipPlanGateMiddleware(AgentMiddleware):
    """Keep direct-execution turns out of the durable planning workflow."""

    @staticmethod
    def _skip_plan(request) -> bool:
        runtime = getattr(request, "runtime", None)
        context = getattr(runtime, "context", None)
        return _context_planning_mode(context) is PlanningMode.SKIP

    _model_tool_name = staticmethod(_tool_name)

    def _blocked_message(self, request) -> ToolMessage:
        return ToolMessage(
            content=(
                "This turn was started with /noplan. Execute the user's bounded "
                "request directly with the scientific delegates; do not create, "
                "approve, reject, or update a durable project plan."
            ),
            tool_call_id=str(request.tool_call.get("id") or ""),
            name=str(request.tool_call.get("name") or ""),
            status="error",
        )

    def _intercept(self, request) -> ToolMessage | None:
        if not self._skip_plan(request):
            return None
        tool_name = str(request.tool_call.get("name") or "")
        if _blocked_by_plan_mode(tool_name, "skip", getattr(request, "tool", None)):
            return self._blocked_message(request)
        return None

    def _direct_execution_request(self, request: ModelRequest):
        if not self._skip_plan(request):
            return request
        visible_tools = [
            tool
            for tool in request.tools
            if not _blocked_by_plan_mode(self._model_tool_name(tool), "skip", tool)
        ]
        return request.override(tools=visible_tools)

    def wrap_model_call(self, request, handler):
        return handler(self._direct_execution_request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._direct_execution_request(request))

    def wrap_tool_call(self, request, handler):
        blocked = self._intercept(request)
        if blocked is not None:
            return blocked
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        blocked = self._intercept(request)
        if blocked is not None:
            return blocked
        return await handler(request)


class PlanApprovalGateMiddleware(AgentMiddleware):
    """Hard-block planned execution while a project-plan draft awaits approval."""

    def __init__(self, project_store: ProjectStore) -> None:
        self.project_store = project_store

    @staticmethod
    def _project_id(request) -> str:
        runtime = getattr(request, "runtime", None)
        context = getattr(runtime, "context", None)
        if context is None:
            return ""
        if isinstance(context, dict):
            return str(context.get("project_id") or "").strip()
        return str(getattr(context, "project_id", "") or "").strip()

    def _pending_pointer(self, request):
        project_id = self._project_id(request)
        if not project_id:
            return None
        rows = self.project_store.memory(project_id).query_facts(
            kind="project_plan",
            lookup_key=PENDING_PLAN_LOOKUP_KEY,
            limit=1,
        )
        if not rows:
            return None
        return rows[0]

    @staticmethod
    def _matches_turn_snapshot(request, pointer) -> bool:
        """A decision applies only to the draft visible when this turn began."""
        context = getattr(getattr(request, "runtime", None), "context", None)
        snapshot = (context.get("pending_project_plan", "") if isinstance(context, dict)
                    else getattr(context, "pending_project_plan", ""))
        try:
            value = json.loads(snapshot)
            return (value["fact_id"] == pointer.fact_id
                    and value["memory_version"] == pointer.version
                    and value["value"]["draft_id"] == pointer.value.get("draft_id"))
        except (ValueError, KeyError, TypeError):
            return False

    def _blocked_message(self, request, draft_id: str) -> ToolMessage:
        return ToolMessage(
            content=(
                f"Execution blocked: project plan draft {draft_id} awaits explicit user "
                "approval. Ask the user to approve it, reject it, or provide revision "
                "guidance. Do not execute planned scientific work in this turn."
            ),
            tool_call_id=str(request.tool_call.get("id") or ""),
            name=str(request.tool_call.get("name") or ""),
            status="error",
        )

    @staticmethod
    def _message_text(message: AnyMessage) -> str:
        content = message.content
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            return "\n".join(parts).strip()
        return str(content or "").strip()

    def _latest_user_text(self, request) -> str:
        state = request.state
        if isinstance(state, dict):
            messages = state.get("messages") or []
        elif isinstance(state, list):
            messages = state
        else:
            messages = getattr(state, "messages", []) or []
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") in {"user", "human"}:
                return str(message.get("content") or "").strip()
            if isinstance(message, HumanMessage) or getattr(message, "type", "") == "human":
                return self._message_text(message)
        return ""

    @classmethod
    def _is_explicit_approval(cls, text: str, draft_id: str) -> bool:
        return explicit_plan_decision(text, draft_id, approve=True)

    @classmethod
    def _is_explicit_rejection(cls, text: str, draft_id: str) -> bool:
        return explicit_plan_decision(text, draft_id, approve=False)

    def _authorization_error(self, request, draft_id: str, action: str) -> ToolMessage:
        return ToolMessage(
            content=(
                f"{action} blocked: the latest user message must explicitly authorize "
                f"pending draft {draft_id}, with the same version visible at turn start. Ask the user to approve, "
                "reject, or describe requested changes in their own message."
            ),
            tool_call_id=str(request.tool_call.get("id") or ""),
            name=str(request.tool_call.get("name") or ""),
            status="error",
        )

    def _intercept(self, request) -> ToolMessage | None:
        tool_name = str(request.tool_call.get("name") or "")
        pointer = self._pending_pointer(request)
        draft_id = str(pointer.value.get("draft_id") or "").strip() if pointer else ""
        if not draft_id:
            return None
        latest_user_text = self._latest_user_text(request)
        if tool_name == "approve_project_plan_draft":
            if (not self._matches_turn_snapshot(request, pointer)
                    or not self._is_explicit_approval(latest_user_text, draft_id)):
                return self._authorization_error(request, draft_id, "Approval")
            requested_draft_id = str(
                (request.tool_call.get("args") or {}).get("draft_id") or ""
            ).strip()
            if requested_draft_id and requested_draft_id != draft_id:
                return self._authorization_error(request, draft_id, "Approval")
        elif tool_name == "reject_project_plan_draft":
            if (not self._matches_turn_snapshot(request, pointer)
                    or not self._is_explicit_rejection(latest_user_text, draft_id)):
                return self._authorization_error(request, draft_id, "Rejection")
            requested_draft_id = str(
                (request.tool_call.get("args") or {}).get("draft_id") or ""
            ).strip()
            if requested_draft_id and requested_draft_id != draft_id:
                return self._authorization_error(request, draft_id, "Rejection")
        elif _blocked_by_plan_mode(tool_name, "pending", getattr(request, "tool", None)):
            return self._blocked_message(request, draft_id)
        return None

    def wrap_tool_call(self, request, handler):
        blocked = self._intercept(request)
        if blocked is not None:
            return blocked
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        blocked = self._intercept(request)
        if blocked is not None:
            return blocked
        return await handler(request)
