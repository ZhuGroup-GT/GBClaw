"""CLI startup: model configuration, orchestrator construction and shutdown."""

from __future__ import annotations

import logging
import os
import sys

from interfaces.lifecycle import start_extensions, stop_extensions
import core.settings.langsmith as langsmith_config
from agents.orchestrator.graph import create_orchestrator_agent
from core.execution.sessions import cancel_all_sessions
from core.llm.config import ModelRouter, load_llm_config
from core.settings.runtime_context import load_runtime_context_config
from interfaces.cli.chat import chat_loop
from interfaces.cli.projects import select_or_create_project
from interfaces.turn import TurnServices
from project_workspace.execution.checkpoint import ProjectCheckpointRouter
from project_workspace.store import ProjectStore

LOGGER = logging.getLogger(__name__)


def command_loop() -> None:
    """Entry point used by `python -m interfaces.cli` and `start_cli.sh`."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        sys.stderr.write("GBclaw CLI requires an interactive terminal.\n")
        sys.exit(3)
    if not os.path.isfile(".key"):
        sys.stderr.write("Error: .key not found. Run start_cli.sh from the GBclaw root.\n")
        sys.exit(2)
    # Library warnings go to stderr in one line each; the chat stream owns stdout.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")

    agent = None
    try:
        print("GBclaw CLI — loading models and extensions…", flush=True)
        model_router = ModelRouter(load_llm_config())
        memory_endpoint = model_router.for_role("memory")
        runtime_context_config = load_runtime_context_config()
        try:
            langsmith_config.configure_langsmith()
        except Exception as exc:
            LOGGER.warning("LangSmith configuration failed: %s", exc)
        start_extensions()
        store = ProjectStore()
        agent = create_orchestrator_agent(
            model_router, project_store=store, runtime_context_config=runtime_context_config,
        )
        services = TurnServices(agent, store, memory_endpoint, runtime_context_config)
        meta = select_or_create_project(store)
        if meta is None:
            print("Bye.")
            return
        chat_loop(services, meta)
    except KeyboardInterrupt:
        print("\nBye.")
    except SystemExit:
        raise
    except Exception:
        LOGGER.exception("GBclaw CLI failed")
        sys.exit(1)
    finally:
        for step in (cancel_all_sessions, lambda: _close_checkpointer(agent), stop_extensions):
            try:
                step()
            except Exception:
                LOGGER.debug("CLI shutdown step failed", exc_info=True)


def _close_checkpointer(agent) -> None:
    checkpointer = getattr(agent, "checkpointer", None)
    if isinstance(checkpointer, ProjectCheckpointRouter):
        checkpointer.close()


__all__ = ["command_loop"]
