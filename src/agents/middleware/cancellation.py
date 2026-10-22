"""Cooperative admission checks inherited by temporary worker graphs."""
from langchain.agents.middleware import AgentMiddleware

from core.execution.sessions import ensure_execution_active


class ExecutionCancellationMiddleware(AgentMiddleware):
    def before_model(self, state, runtime):
        ensure_execution_active()

    async def abefore_model(self, state, runtime):
        ensure_execution_active()

    def wrap_model_call(self, request, handler):
        ensure_execution_active()
        response = handler(request)
        ensure_execution_active()
        return response

    async def awrap_model_call(self, request, handler):
        ensure_execution_active()
        response = await handler(request)
        ensure_execution_active()
        return response

    def wrap_tool_call(self, request, handler):
        ensure_execution_active()
        response = handler(request)
        ensure_execution_active()
        return response

    async def awrap_tool_call(self, request, handler):
        ensure_execution_active()
        response = await handler(request)
        ensure_execution_active()
        return response
