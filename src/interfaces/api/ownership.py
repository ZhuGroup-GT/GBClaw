"""Admission for project writes; request-scope dependencies cover SSE cleanup."""
from fastapi import HTTPException, Request

from interfaces.api import deps
from project_workspace.execution.ownership import ChatBusyError, ProjectRequestLease

FRONTEND_TOKEN_HEADER = "X-GBclaw-Project-Token"


async def protect_project_write(request: Request = None):
    # APIRouter dependencies also run on WebSocket routes.
    if request is None:
        yield
        return
    lease = None
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        project_id = request.path_params.get("project_id")
        route_path = getattr(request.scope.get("route"), "path", request.url.path)
        if not project_id and route_path in {"/api/chat", "/api/chat/stream", "/api/chat/stop"}:
            try:
                body = await request.json()
            except ValueError:
                body = None
            if isinstance(body, dict):
                project_id = body.get("project_id")
        if isinstance(project_id, str) and project_id:
            store = deps.project_store(request)
            try:
                lease = ProjectRequestLease(store.project_path(project_id),
                    request.headers.get(FRONTEND_TOKEN_HEADER), deleting=request.method == "DELETE")
            except ChatBusyError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        yield
    finally:
        if lease is not None:
            lease.close()
