"""HTTP routes for GBclaw project workspaces."""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from interfaces.api import deps
from agents.streaming.events import tool_input_preview, tool_output_preview
from interfaces.api.ownership import protect_project_write
from interfaces.api.schemas import (
    CreateProjectRequest,
    FileContentResponse,
    FileEntryResponse,
    FileListResponse,
    FileStatResponse,
    HistoryTurnDetailResponse,
    HistoryTurnSummaryResponse,
    ProjectHistoryResponse,
    ProjectListResponse,
    ProjectMemoryResponse,
    ProjectResponse,
    ProjectSummary,
)
from project_workspace.artifacts.files import (
    directory_fingerprint,
    list_directory,
    read_text_content,
    prepare_project_path,
    stat_path,
)
from project_workspace.artifacts.presentation import extract_artifact_image_paths
from project_workspace.models import ProjectMeta
from project_memory.snapshot.session import build_memory_context_preview
from project_workspace.chat.models import TraceEventRecord
from project_workspace.store import ProjectStore
from project_workspace.execution.ownership import ChatBusyError, ProjectFrontendLease

router = APIRouter(prefix="/api/projects", tags=["projects"],
                   dependencies=[Depends(protect_project_write, scope="request")])


def _trace_event_preview(event: TraceEventRecord, project_id: str | None = None) -> dict:
    """Project a complete stored trace event into the bounded UI response."""
    payload = event.model_dump()
    if event.event_type == "tool_start" and event.input_json is not None:
        payload["input_json"] = tool_input_preview(
            event.input_json,
            tool_name=event.tool_name,
        )
    elif event.event_type == "tool_end" and event.output_text is not None:
        payload["image_paths"] = extract_artifact_image_paths(event.output_text, project_id)
        payload["output_text"] = tool_output_preview(
            event.output_text,
            tool_name=event.tool_name,
        )
    return payload


def _to_summary(meta: ProjectMeta) -> ProjectSummary:
    return ProjectSummary(
        project_id=meta.project_id,
        name=meta.name,
        created_at=meta.created_at,
        updated_at=meta.updated_at,
        goal=meta.goal,
    )


def _to_response(meta: ProjectMeta, store: ProjectStore) -> ProjectResponse:
    return ProjectResponse(
        project_id=meta.project_id,
        name=meta.name,
        created_at=meta.created_at,
        updated_at=meta.updated_at,
        goal=meta.goal,
        thread_id=meta.thread_id,
        root=str(store.project_path(meta.project_id)),
    )


@router.get("", response_model=ProjectListResponse)
async def list_projects(request: Request) -> ProjectListResponse:
    store = deps.project_store(request)
    projects = [_to_summary(meta) for meta in store.list_projects()]
    return ProjectListResponse(projects=projects)


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    payload: CreateProjectRequest,
    request: Request,
) -> ProjectResponse:
    store = deps.project_store(request)
    try:
        meta = store.create(name=payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_response(meta, store)


@router.websocket("/{project_id}/connection")
async def project_connection(project_id: str, websocket: WebSocket):
    """A browser tab owns its project until this connection closes."""
    await websocket.accept()
    lease = None
    try:
        store = getattr(websocket.app.state, "project_store", None)
        if store is None:
            await websocket.send_json({"type": "error", "detail": "Project store is not initialized"})
            await websocket.close(code=1013)
            return
        try:
            root = store.project_path(project_id)
            lease = ProjectFrontendLease(root)
            meta = store.get(project_id)
        except (ChatBusyError, ValueError) as exc:
            await websocket.send_json({"type": "error", "detail": str(exc)})
            await websocket.close(code=1008)
            return
        await websocket.send_json({"type": "claimed", "token": lease.token,
                                   "project": _to_response(meta, store).model_dump()})
        while True:
            # Transport ping/pong detects broken peers; no foreground timer or
            # tab visibility is required to keep an idle project occupied.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if lease is not None:
            lease.close()


@router.get("/{project_id}/files", response_model=FileListResponse)
async def list_project_files(
    project_id: str,
    request: Request,
    path: str = Query(default="", description="Relative directory path inside the project"),
) -> FileListResponse:
    store = deps.project_store(request)
    try:
        project_root = store.project_path(project_id)
        listing = list_directory(project_root, path)
    except ValueError as exc:
        detail = str(exc)
        status = 404 if "not found" in detail.lower() else 400
        raise HTTPException(status_code=status, detail=detail) from exc

    return FileListResponse(
        path=listing.path,
        entries=[FileEntryResponse(**entry.__dict__) for entry in listing.entries],
        fingerprint=directory_fingerprint(listing),
    )


@router.get("/{project_id}/files/stat", response_model=FileStatResponse)
async def stat_project_path(
    project_id: str,
    request: Request,
    path: str = Query(default="", description="Relative file or directory path inside the project"),
) -> FileStatResponse:
    store = deps.project_store(request)
    try:
        project_root = store.project_path(project_id)
        payload = stat_path(project_root, path)
    except ValueError as exc:
        detail = str(exc)
        status = 404 if "not found" in detail.lower() else 400
        raise HTTPException(status_code=status, detail=detail) from exc

    return FileStatResponse(**payload.__dict__)


@router.get("/{project_id}/files/content", response_model=FileContentResponse)
async def read_project_file_content(
    project_id: str,
    request: Request,
    path: str = Query(..., min_length=1, description="Relative file path inside the project"),
) -> FileContentResponse:
    store = deps.project_store(request)
    try:
        project_root = store.project_path(project_id)
        payload = read_text_content(project_root, path)
    except ValueError as exc:
        detail = str(exc)
        status = 404 if "not found" in detail.lower() else 400
        raise HTTPException(status_code=status, detail=detail) from exc

    return FileContentResponse(**payload.__dict__)


@router.get("/{project_id}/files/download")
async def download_project_file(
    project_id: str,
    request: Request,
    path: str = Query(..., min_length=1, description="Relative file path inside the project"),
) -> FileResponse:
    store = deps.project_store(request)
    try:
        project_root = store.project_path(project_id)
        target = prepare_project_path(project_root, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(target, filename=target.name, media_type="application/octet-stream")


@router.get("/{project_id}/files/raw")
async def raw_project_file(
    project_id: str,
    request: Request,
    path: str = Query(..., min_length=1, description="Relative file path inside the project"),
) -> FileResponse:
    """Serve a project file inline with a guessed mime type (for image/text preview)."""
    store = deps.project_store(request)
    try:
        project_root = store.project_path(project_id)
        target = prepare_project_path(project_root, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    media_type, _ = mimetypes.guess_type(target.name)
    return FileResponse(target, media_type=media_type or "application/octet-stream")


@router.get("/{project_id}/memory", response_model=ProjectMemoryResponse)
async def get_project_memory(project_id: str, request: Request) -> ProjectMemoryResponse:
    store = deps.project_store(request)
    try:
        store.get(project_id)
        payload = build_memory_context_preview(store, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProjectMemoryResponse(**payload)


@router.get("/{project_id}/history", response_model=ProjectHistoryResponse)
async def get_project_history(
    project_id: str,
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    thread_id: str | None = Query(default=None),
) -> ProjectHistoryResponse:
    """Return a lightweight page of user/assistant turns for UI replay."""
    store = deps.project_store(request)
    try:
        meta = store.get(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    selected_thread = (thread_id or meta.thread_id).strip()
    chat = store.chat(project_id)
    page = chat.list_turns(
        selected_thread,
        offset=offset,
        limit=limit + 1,
        include_reasoning=False,
    )
    has_more = len(page) > limit
    image_paths = chat.list_turn_image_paths(
        [turn.turn_id for turn in page[:limit] if turn.role == "assistant"],
        project_id,
    )
    turns = [
        HistoryTurnSummaryResponse(
            **turn.model_dump(),
            image_paths=extract_artifact_image_paths(
                [turn.content, image_paths.get(turn.turn_id, [])], project_id
            ) if turn.role == "assistant" else [],
        )
        for turn in page[:limit]
    ]
    return ProjectHistoryResponse(
        project_id=project_id,
        offset=offset,
        limit=limit,
        has_more=has_more,
        turns=turns,
    )


@router.get(
    "/{project_id}/history/{turn_id}",
    response_model=HistoryTurnDetailResponse,
)
async def get_project_history_turn(
    project_id: str,
    turn_id: str,
    request: Request,
) -> HistoryTurnDetailResponse:
    store = deps.project_store(request)
    try:
        chat = store.chat(project_id)
        turn = chat.get_turn(turn_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    trace = chat.list_trace_events(turn_id)
    return HistoryTurnDetailResponse(
        **turn.model_dump(),
        image_paths=extract_artifact_image_paths(
            [turn.content, *[event.output_text for event in trace if event.event_type == "tool_end"]],
            project_id,
        ),
        trace=[
            _trace_event_preview(event, project_id)
            for event in trace
        ],
    )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, request: Request) -> ProjectResponse:
    store = deps.project_store(request)
    try:
        meta = store.get(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_response(meta, store)


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str, request: Request) -> None:
    store = deps.project_store(request)
    try:
        store.delete(project_id)
    except ValueError as exc:
        detail = str(exc)
        status = 404 if "not found" in detail.lower() else 400
        raise HTTPException(status_code=status, detail=detail) from exc
