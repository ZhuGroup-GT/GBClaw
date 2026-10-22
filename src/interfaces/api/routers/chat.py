"""HTTP routes for GBclaw chat API."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from core.stream_events import StreamEventType
from interfaces.api import deps
from interfaces.api.ownership import FRONTEND_TOKEN_HEADER, protect_project_write
from interfaces.api.schemas import ChatRequest, ChatResponse, ChatStopRequest, ChatStopResponse, HealthResponse
from core.execution.sessions import cancel_session, make_session_key, signal_session_cancel
from interfaces.turn import (
    TurnInput,
    TurnInputError,
    TurnOwnership,
    TurnServices,
    parse_turn_input,
    repair_project_checkpoint,
    run_turn,
)
from project_workspace.artifacts.presentation import extract_artifact_image_paths
from project_workspace.chat.models import TurnStatus
from project_workspace.chat.requests import ChatRequestRepository, request_fingerprint
from project_workspace.models import utc_now_iso
from project_workspace.persistence.locks import file_lock
from project_memory.run_artifacts.cleanup import cancel_running_runs_for_chat
from project_workspace.execution.ownership import ChatBusyError, ProjectStopLease, project_lock_directory
from project_workspace.store import ProjectStore

router = APIRouter(prefix="/api", tags=["gbclaw"],
                   dependencies=[Depends(protect_project_write, scope="request")])
LOGGER = logging.getLogger(__name__)
_settling_executions: set[asyncio.Task] = set()


def _resolve_thread_id(thread_id: str | None) -> str:
    if thread_id:
        value = thread_id.strip()
        if value:
            return value
    return str(uuid.uuid4())


def _resolve_project_context(
    payload: ChatRequest,
    request: Request,
) -> tuple[str, str]:
    store = deps.project_store(request)
    try:
        meta = store.get(payload.project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    thread_id = _resolve_thread_id(payload.thread_id or meta.thread_id)
    return meta.project_id, thread_id


def _services(request: Request) -> TurnServices:
    return TurnServices(
        agent=deps.agent(request),
        store=deps.project_store(request),
        memory_endpoint=deps.memory_endpoint(request),
        runtime_context_config=deps.runtime_context_config(request),
    )


def _open_ownership(store: ProjectStore, project_id: str, thread_id: str, request: Request,
                    request_id: str | None = None) -> TurnOwnership:
    try:
        return TurnOwnership(store, project_id, thread_id, request_id=request_id,
                             frontend_token=request.headers.get(FRONTEND_TOKEN_HEADER))
    except ChatBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _result_response(result, project_id, thread_id, request_id):
    return ChatResponse(reply=result.reply, project_id=project_id, thread_id=thread_id,
                        request_id=request_id, image_paths=result.image_paths,
                        status=result.status, recovery=result.recovery)


def _interrupted_response(project_id, thread_id, request_id, *, before_start=False):
    return ChatResponse(reply="Request was stopped before admission." if before_start else
                        "Agent execution was cancelled.", status="interrupted",
                        project_id=project_id, thread_id=thread_id, request_id=request_id)


def _cached_response(record):
    if record["status"] == "failed":
        raise HTTPException(status_code=500, detail=record["error"])
    return ChatResponse.model_validate(record["response"]).model_copy(update={"replayed": True})


def _recover_request(repository, record, project_id, store):
    """Execution lease is free: recover the receipt without replaying any tools."""
    cancel_running_runs_for_chat(store, project_id, record["thread_id"],
                                reason="Previous execution owner ended before request completion.")
    response = _interrupted_response(project_id, record["thread_id"], record["request_id"])
    if record["assistant_turn_id"]:
        turn = repository.chat.get_turn(record["assistant_turn_id"])
        if turn.status is TurnStatus.COMPLETED:
            images = extract_artifact_image_paths(
                [turn.content, *[event.output_text for event in repository.chat.list_trace_events(turn.turn_id)]],
                project_id,
            )
            response = ChatResponse(reply=turn.content, status="completed", image_paths=images,
                                    project_id=project_id, thread_id=turn.thread_id, request_id=record["request_id"])
        elif turn.status is TurnStatus.FAILED:
            repository.finish(record["request_id"], record["owner"], error=turn.content)
            return _cached_response(repository.get(record["request_id"]))
        elif turn.status is TurnStatus.RUNNING:
            repository.chat.interrupt_turn(turn.turn_id, content="Previous execution owner ended before completion.",
                                           reasoning=turn.reasoning, completed_at=utc_now_iso())
    repository.finish(record["request_id"], record["owner"], response=response.model_dump())
    return _cached_response(repository.get(record["request_id"]))


def _admit_chat_request(store, project_id, thread_id, request, request_id, agent_message, planning_mode):
    request_id = request_id or uuid.uuid4().hex
    fingerprint = request_fingerprint(thread_id, agent_message, planning_mode.value)
    with file_lock(project_lock_directory(store.project_path(project_id)) / "gate.lock"):
        repository = ChatRequestRepository(store.chat(project_id))
        record = repository.get(request_id)
        if record is not None:
            if record["thread_id"] != thread_id or record["fingerprint"] not in {None, fingerprint}:
                raise HTTPException(status_code=409, detail="Request ID already belongs to a different task or thread.")
            if record["status"] != "running":
                return _cached_response(record)
        # A healthy execution (including another worker) causes 409 here.
        ownership = _open_ownership(store, project_id, thread_id, request, request_id)
        if record is not None:
            try:
                return _recover_request(repository, record, project_id, store)
            finally:
                ownership.close()
        try:
            repository.admit(request_id, thread_id, fingerprint, ownership.owner)
        except BaseException:
            ownership.close()
            raise
        return ownership, repository


async def _settle_disconnected_execution(execution, ownership, store, project_id, thread_id):
    """Keep ownership until the graph and its synchronous delegates return."""
    try:
        await run_in_threadpool(ownership.cancel)
        await execution
    except asyncio.CancelledError:
        pass
    except Exception:
        LOGGER.info("Disconnected project execution finished with an error.", exc_info=True)
    finally:
        try:
            await run_in_threadpool(cancel_running_runs_for_chat, store, project_id, thread_id,
                                    reason="Streaming chat disconnected or was cancelled.")
        finally:
            ownership.close()


def _defer_execution_cleanup(execution, ownership, store, project_id, thread_id):
    settling = asyncio.create_task(_settle_disconnected_execution(
        execution, ownership, store, project_id, thread_id))
    _settling_executions.add(settling)
    def settled(task):
        _settling_executions.discard(task)
        if not task.cancelled() and task.exception() is not None:
            LOGGER.error("Disconnected execution cleanup failed.", exc_info=task.exception())
    settling.add_done_callback(settled)


def _partial_reasoning(payload: ChatStopRequest) -> str:
    parts = [
        str(segment.get("text") or "")
        for segment in payload.partial_trace
        if isinstance(segment, dict) and segment.get("kind") == "reasoning"
    ]
    return "".join(parts).strip() or payload.reasoning


def _parse_turn(payload: ChatRequest) -> TurnInput:
    try:
        return parse_turn_input(payload.message, planning_mode=payload.planning_mode)
    except TurnInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _execute_owned_turn(services: TurnServices, ownership: TurnOwnership, turn: TurnInput, *,
                              project_id: str, thread_id: str, request_repository,
                              stream: bool = False, on_event=None):
    """Run the shared turn and settle the idempotent request receipt."""
    request_id, owner = ownership.request_id, ownership.owner
    try:
        result = await run_turn(
            services, ownership, turn, project_id=project_id, thread_id=thread_id,
            source="fastapi", run_name="gbclaw_api_chat",
            tags=["gbclaw", "api", *(["stream"] if stream else [])],
            stream=stream, on_event=on_event, request_id=request_id, request_owner=owner,
        )
    except asyncio.CancelledError:
        response = _interrupted_response(project_id, thread_id, request_id)
        request_repository.finish(request_id, owner, response=response.model_dump())
        raise
    except Exception as exc:
        request_repository.finish(request_id, owner, error=str(exc))
        raise
    response = _result_response(result, project_id, thread_id, request_id)
    request_repository.finish(request_id, owner, response=response.model_dump())
    return result


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    turn = _parse_turn(payload)

    project_id, thread_id = _resolve_project_context(payload, request)
    services = _services(request)
    store = services.store
    admission = _admit_chat_request(store, project_id, thread_id, request, payload.request_id,
                                   turn.agent_message, turn.planning_mode)
    if isinstance(admission, ChatResponse):
        return admission
    ownership, request_repository = admission
    execution = None
    cleanup_deferred = False

    try:
        execution = asyncio.create_task(_execute_owned_turn(
            services, ownership, turn, project_id=project_id, thread_id=thread_id,
            request_repository=request_repository))
        result = await asyncio.shield(execution)
    except asyncio.CancelledError:
        ownership.cancel_event.set()
        if (execution is not None and execution.cancelled()
                and not asyncio.current_task().cancelling()):
            return ChatResponse(reply="Agent execution was cancelled.", status="interrupted",
                project_id=project_id, thread_id=thread_id, request_id=ownership.request_id)
        if execution is not None and not execution.done():
            cleanup_deferred = True
            _defer_execution_cleanup(execution, ownership, store, project_id, thread_id)
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        if not cleanup_deferred:
            ownership.close()

    return _result_response(result, project_id, thread_id, ownership.request_id)


@router.post("/chat/stop", response_model=ChatStopResponse)
async def chat_stop(payload: ChatStopRequest, request: Request) -> ChatStopResponse:
    store = deps.project_store(request)
    try:
        lease = ProjectStopLease(store.project_path(payload.project_id),
            request.headers.get(FRONTEND_TOKEN_HEADER), payload.thread_id, request_id=payload.request_id)
    except ChatBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    with lease:
        if not lease.execution_active and payload.request_id is not None:
            with file_lock(project_lock_directory(store.project_path(payload.project_id)) / "gate.lock"):
                repository = ChatRequestRepository(store.chat(payload.project_id))
                record = repository.get(payload.request_id)
                if record is not None and record["thread_id"] != payload.thread_id:
                    raise HTTPException(status_code=409, detail="Request ID belongs to a different thread.")
                response = _interrupted_response(payload.project_id, payload.thread_id,
                                                 payload.request_id, before_start=True)
                repository.stop_before_start(payload.request_id, payload.thread_id, response.model_dump())
            return ChatStopResponse(status="not_running", project_id=payload.project_id,
                                    thread_id=payload.thread_id, request_id=payload.request_id)
        lease.request_cancel(payload.reason)
        return await _stop_owned_chat(payload, request, execution_active=lease.execution_active,
                                      execution_owner=lease.execution_owner)


async def _stop_owned_chat(payload: ChatStopRequest, request: Request, *, execution_active: bool,
                           execution_owner: str | None) -> ChatStopResponse:
    store = deps.project_store(request)
    try:
        store.get(payload.project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    session_key = make_session_key(payload.project_id, payload.thread_id, execution_owner=execution_owner)
    if execution_active:
        signal_session_cancel(session_key)
    else:
        await run_in_threadpool(cancel_session, session_key)
    await run_in_threadpool(
        cancel_running_runs_for_chat,
        store,
        payload.project_id,
        payload.thread_id,
        reason="User stopped the in-flight agent run.",
        cleanup_ephemeral=not execution_active,
    )

    agent = getattr(request.app.state, "agent", None)
    # A live worker owns its checkpoint. Recovery after its exit happens during
    # normal execution cleanup or preparation of the next turn.
    if agent is not None and not execution_active:
        try:
            await run_in_threadpool(
                repair_project_checkpoint,
                agent,
                payload.project_id,
                payload.thread_id,
            )
        except Exception:
            pass

    chat_repository = store.chat(payload.project_id)
    active = await run_in_threadpool(
        chat_repository.get_active_assistant_turn,
        payload.thread_id,
    )
    if active is not None:
        persisted_reasoning = "".join(
            event.output_text or ""
            for event in await run_in_threadpool(
                chat_repository.list_trace_events,
                active.turn_id,
            )
            if event.event_type == "reasoning"
        )
        await run_in_threadpool(
            chat_repository.interrupt_turn,
            active.turn_id,
            content="User stopped the in-flight agent run.",
            reasoning=_partial_reasoning(payload) or persisted_reasoning,
            completed_at=utc_now_iso(),
        )
    return ChatStopResponse(
        status="stop_requested" if execution_active else "stopped",
        project_id=payload.project_id,
        thread_id=payload.thread_id,
        request_id=payload.request_id,
    )


@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    turn = _parse_turn(payload)

    project_id, thread_id = _resolve_project_context(payload, request)
    services = _services(request)
    store = services.store
    admission = _admit_chat_request(store, project_id, thread_id, request, payload.request_id,
                                   turn.agent_message, turn.planning_mode)
    if isinstance(admission, ChatResponse):
        async def replay_events():
            yield f"data: {json.dumps({'type': StreamEventType.STATUS, 'message': 'Returning the existing request result.', 'request_id': admission.request_id, 'replayed': True}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': StreamEventType.DONE, **admission.model_dump()}, ensure_ascii=False)}\n\n"
        return StreamingResponse(replay_events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    ownership, request_repository = admission
    cancel_event = ownership.cancel_event
    cleanup_deferred = False
    stream_finished = False

    def finish_stream_session():
        nonlocal stream_finished
        if not cleanup_deferred and not stream_finished:
            stream_finished = True
            try:
                response = _interrupted_response(project_id, thread_id, ownership.request_id)
                request_repository.finish(ownership.request_id, ownership.owner, response=response.model_dump())
            finally:
                ownership.close()

    async def settle_stream_session():
        if cleanup_deferred or stream_finished:
            return
        try:
            if cancel_event.is_set():
                await run_in_threadpool(
                    cancel_running_runs_for_chat,
                    store,
                    project_id,
                    thread_id,
                    reason="Streaming chat disconnected or was cancelled.",
                )
        finally:
            finish_stream_session()

    async def event_generator():
        nonlocal cleanup_deferred
        execution: asyncio.Task | None = None
        disconnected = False
        try:
            preparing = {
                "type": StreamEventType.STATUS,
                "message": "Preparing project context…",
                "request_id": ownership.request_id,
            }
            yield f"data: {json.dumps(preparing, ensure_ascii=False)}\n\n"
            # Keep the producer coupled to the SSE response. An unbounded queue
            # lets a run enqueue many ready chunks before the response task gets
            # scheduled, which makes the browser receive them in visible bursts.
            queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1)

            async def emit(event: dict) -> None:
                if event.get("type") == StreamEventType.DONE:
                    # The graph has an answer, but the request still owns the
                    # project while its chat and memory updates are committed.
                    # Only this transport emits terminal `done`, after release.
                    event = {
                        "type": StreamEventType.STATUS, "phase": "finalizing",
                        "message": ("Finishing this response…" if event.get("status") == "paused"
                                    else "Saving project memory…"),
                        "request_id": ownership.request_id,
                    }
                while not cancel_event.is_set():
                    try:
                        await asyncio.wait_for(queue.put(event), timeout=0.1)
                        return
                    except TimeoutError:
                        continue

            execution = asyncio.create_task(_execute_owned_turn(
                services, ownership, turn, project_id=project_id, thread_id=thread_id,
                request_repository=request_repository, stream=True, on_event=emit))
            while not execution.done() or not queue.empty():
                if await request.is_disconnected():
                    disconnected = True
                    ownership.cancel()
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.1)
                except TimeoutError:
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if disconnected:
                return
            if execution.cancelled():
                response = _interrupted_response(project_id, thread_id, ownership.request_id)
            else:
                result = await execution
                response = _result_response(result, project_id, thread_id, ownership.request_id)
            # A browser may immediately submit another turn upon receiving
            # `done`. Its receipt must already be durable and execution free.
            await settle_stream_session()
            yield f"data: {json.dumps({'type': StreamEventType.DONE, **response.model_dump()}, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            ownership.cancel()
            raise
        except Exception as exc:
            if execution is None or execution.done():
                await settle_stream_session()
            yield f"data: {json.dumps({'type': StreamEventType.ERROR, 'message': str(exc)}, ensure_ascii=False)}\n\n"
        finally:
            if execution is not None and not execution.done():
                cancel_event.set()
                cleanup_deferred = True
                # Cancel the session cooperatively, drain the graph in the
                # background and retain the project execution lock meanwhile.
                _defer_execution_cleanup(execution, ownership, store, project_id, thread_id)
            await settle_stream_session()

    return StreamingResponse(
        event_generator(),
        background=BackgroundTask(finish_stream_session),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
