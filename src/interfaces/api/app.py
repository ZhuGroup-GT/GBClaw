"""FastAPI application entry point for GBclaw."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from interfaces.lifecycle import extension_routers, start_extensions, stop_extensions
from core.paths import REPO_ROOT
from core.settings.env import load_dotenv

# Some settings read os.environ at import time; load the repository .env first.
load_dotenv()

from agents.orchestrator.graph import create_orchestrator_agent
from interfaces.api.routers.projects import router as project_router
from interfaces.api.routers.chat import router
from interfaces.api.routers.registry import router as registry_router
from core.llm.config import ModelRouter, load_llm_config
from core.settings.runtime_context import load_runtime_context_config
from project_workspace.execution.checkpoint import ProjectCheckpointRouter
from project_memory.extraction.retry import drain_ready_retry_jobs
from project_workspace.store import ProjectStore
from project_workspace.paths import get_default_projects_root
from core.execution.sessions import cancel_all_sessions

WEB_DIR = REPO_ROOT / "web"
BRAND_ASSETS_DIR = REPO_ROOT / "docs" / "assets"
LOGGER = logging.getLogger(__name__)


async def _memory_retry_loop(app: FastAPI, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            processed = await asyncio.to_thread(
                drain_ready_retry_jobs,
                app.state.project_store,
                app.state.memory_endpoint,
                app.state.runtime_context_config,
                max_jobs=10,
            )
        except Exception:
            LOGGER.exception("Memory V2 retry loop iteration failed.")
            processed = 0
        delay = 1.0 if processed else 10.0
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=delay)


@asynccontextmanager
async def lifespan(app: FastAPI):
    projects_root = get_default_projects_root()
    app.state.project_store = ProjectStore(projects_root)
    app.state.model_router = ModelRouter(load_llm_config())
    app.state.memory_endpoint = app.state.model_router.for_role("memory")
    app.state.runtime_context_config = load_runtime_context_config()
    start_extensions()
    app.state.agent = create_orchestrator_agent(
        app.state.model_router,
        project_store=app.state.project_store,
        runtime_context_config=app.state.runtime_context_config,
    )
    retry_stop = asyncio.Event()
    retry_task = asyncio.create_task(
        _memory_retry_loop(app, retry_stop),
        name="gbclaw-memory-v2-retry",
    )
    try:
        yield
    finally:
        retry_stop.set()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(retry_task, timeout=5.0)
        if not retry_task.done():
            retry_task.cancel()
        cancel_all_sessions()
        checkpointer = getattr(app.state.agent, "checkpointer", None)
        if isinstance(checkpointer, ProjectCheckpointRouter):
            checkpointer.close()
        stop_extensions()


app = FastAPI(
    title="GBclaw API",
    description="HTTP API for the GBclaw grain-boundary research agent",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(project_router)
app.include_router(registry_router)
for extension_router in extension_routers():
    app.include_router(extension_router)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(BRAND_ASSETS_DIR / "GBClaw-icon.svg", media_type="image/svg+xml")


# Documentation and the Web UI share the same canonical brand files.
if BRAND_ASSETS_DIR.is_dir():
    app.mount("/brand-assets", StaticFiles(directory=BRAND_ASSETS_DIR), name="brand-assets")


if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="frontend")
