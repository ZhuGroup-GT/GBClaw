"""Interactive grain diagrams and project exports, independent of an LLM call."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from interfaces.api import deps
from interfaces.api.ownership import protect_project_write

from project_memory.models import RunStatus
from domains.structure.dichromatic.adapter import (
    DichromaticMapRequest, build_dichromatic_map, dichromatic_options,
)
from domains.structure.dichromatic.tools import export_project_dichromatic_map
from domains.structure.dichromatic.interactions import (
    DichromaticInteractionService, InteractionConflict, InteractionNotFound,
)

router = APIRouter(prefix="/api", tags=["dichromatic-map"],
                   dependencies=[Depends(protect_project_write, scope="request")])


class DichromaticMapExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parameters: DichromaticMapRequest
    purpose: str = Field(default="Explore a grain-boundary dichromatic pattern in the browser.",
                         min_length=1, max_length=2000)
    source_run_id: str | None = Field(default=None, max_length=120)


class CellSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_ids: list[str] = Field(min_length=4, max_length=4)
    expected_revision: StrictInt = Field(ge=1)


class CellPreparationRequest(DichromaticMapExportRequest):
    parameters: DichromaticMapRequest | None = None


class CellRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=1)


class CellViewportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    center: tuple[float, float]
    width: float = Field(ge=0.1, le=100)
    height: float = Field(ge=0.1, le=100)


class CellStrainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    strain_limit_percent: float = Field(default=2.0, gt=0, le=10)
    max_rotation_deg: float = Field(default=1.0, ge=0, le=5)


def _interactions(request: Request, project_id: str) -> DichromaticInteractionService:
    store = deps.project_store(request)
    try:
        return DichromaticInteractionService(store, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _interaction_call(function, *args, **kwargs):
    try:
        return await run_in_threadpool(function, *args, **kwargs)
    except InteractionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InteractionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Install dichromatic-map in the GBclaw environment.") from exc


@router.post("/projects/{project_id}/dichromatic-map/requests")
async def create_cell_request(project_id: str, payload: CellPreparationRequest, request: Request):
    return await _interaction_call(_interactions(request, project_id).create,
        payload.parameters.model_dump(mode="json") if payload.parameters is not None else None,
        purpose=payload.purpose, source_run_id=payload.source_run_id)


@router.get("/projects/{project_id}/dichromatic-map/requests")
async def list_cell_requests(project_id: str, request: Request, status: str = "pending"):
    return {"requests": await _interaction_call(_interactions(request, project_id).list_requests, status)}


@router.get("/projects/{project_id}/dichromatic-map/requests/{request_id}")
async def get_cell_request(project_id: str, request_id: str, request: Request):
    return await _interaction_call(_interactions(request, project_id).get, request_id)


@router.post("/projects/{project_id}/dichromatic-map/requests/{request_id}/preview")
async def preview_cell_request(project_id: str, request_id: str, payload: CellViewportRequest, request: Request):
    return await _interaction_call(_interactions(request, project_id).preview, request_id, payload.model_dump(mode="json"))


@router.post("/projects/{project_id}/dichromatic-map/requests/{request_id}/selection")
async def select_cell(project_id: str, request_id: str, payload: CellSelectionRequest, request: Request):
    return await _interaction_call(_interactions(request, project_id).select, request_id,
                                  payload.candidate_ids, expected_revision=payload.expected_revision)


@router.post("/projects/{project_id}/dichromatic-map/requests/{request_id}/cancel")
async def cancel_cell_request(project_id: str, request_id: str, payload: CellRevisionRequest, request: Request):
    return await _interaction_call(_interactions(request, project_id).cancel, request_id,
                                  expected_revision=payload.expected_revision)


@router.post("/projects/{project_id}/dichromatic-map/requests/{request_id}/apply")
async def apply_cell_request(project_id: str, request_id: str, payload: CellStrainRequest, request: Request):
    result = await _interaction_call(_interactions(request, project_id).apply, request_id, **payload.model_dump())
    return result.model_dump(mode="json")


@router.post("/projects/{project_id}/dichromatic-map/requests/{request_id}/edge-analysis")
async def analyze_cell_edges(project_id: str, request_id: str, request: Request):
    result = await _interaction_call(_interactions(request, project_id).analyze_edges, request_id)
    return result.model_dump(mode="json")


@router.get("/dichromatic-map/options")
async def options(lattice: str = Query(default="FCC", max_length=12),
                  axis: str = Query(default="110", max_length=80)) -> dict:
    try:
        return await run_in_threadpool(dichromatic_options, lattice, axis)
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Install dichromatic-map in the GBclaw environment.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/dichromatic-map/preview")
async def preview(payload: DichromaticMapRequest) -> dict:
    try:
        return await run_in_threadpool(build_dichromatic_map, payload.model_dump(mode="json"))
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Install dichromatic-map in the GBclaw environment.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/dichromatic-map/export")
async def export(project_id: str, payload: DichromaticMapExportRequest, request: Request) -> dict:
    store = deps.project_store(request)
    try:
        store.project_path(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        result = await run_in_threadpool(
            export_project_dichromatic_map, store, project_id,
            payload.parameters.model_dump(mode="json"), purpose=payload.purpose,
            source_run_id=payload.source_run_id, origin="browser_widget",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.status is not RunStatus.COMPLETED:
        raise HTTPException(status_code=502, detail=result.model_dump(mode="json"))
    return result.model_dump(mode="json")
