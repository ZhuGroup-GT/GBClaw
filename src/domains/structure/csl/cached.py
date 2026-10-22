"""Project-aware CSL tools with deterministic exact-input result reuse."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from langgraph.config import get_config
from pydantic import create_model

from core.util.graph_config import runtime_project_id
from project_memory.models import Workflow
from project_workspace.store import ProjectStore
from domains.structure.csl.recorder import (
    csl_reuse_reply,
    csl_tool_input_fingerprint,
    csl_tool_result_is_successful,
)
from domains.structure.csl.sources import exact_plane_rows_from_run, verified_csl_result_text
from domains.structure.csl.tools import STRUCTURE_AGENT_TOOLS, screen_near_csl_sources

# Appended to each base tool's own guidance in the reuse wrapper.
_REUSE_NOTE = (
    "Within a project, a completed result for identical inputs is reused instead of "
    "recomputed, and the reply says so."
)


def make_project_csl_tools(store: ProjectStore) -> list[BaseTool]:
    """Return CSL tools that reuse completed artifacts for identical inputs."""
    local_results: dict[tuple[str | None, str, str], str] = {}

    def local_key(fingerprint: str) -> tuple[str | None, str, str] | None:
        try:
            config = get_config()
        except Exception:
            return None
        thread_id = str((config.get("configurable") or {}).get("thread_id") or "")
        return (runtime_project_id(), thread_id, fingerprint) if thread_id else None

    def remember(fingerprint: str, output: str) -> None:
        key = local_key(fingerprint)
        if key is not None:
            local_results[key] = output
            if len(local_results) > 128:
                local_results.pop(next(iter(local_results)))

    def cached_result(name: str, args: dict[str, Any]) -> str | None:
        fingerprint = csl_tool_input_fingerprint(name, args)
        key = local_key(fingerprint)
        if key is not None and key in local_results:
            return csl_reuse_reply(local_results[key], {
                "message": (f"Reused identical {name} result from this worker invocation "
                            f"(input_fingerprint={fingerprint})."),
                "scope": "this_worker_invocation",
                "input_fingerprint": fingerprint,
            })

        project_id = runtime_project_id()
        if not project_id:
            return None
        try:
            project_root = store.project_path(project_id).resolve()
            memory = store.memory(project_id)
            artifacts = memory.query_completed_tool_artifacts(
                workflow=Workflow.CSL,
                role="csl_result",
                input_fingerprint=fingerprint,
            )
        except (OSError, ValueError):
            return None
        for artifact in artifacts:
            try:
                run = memory.get_run(artifact.run_id)
            except ValueError:
                continue
            text = verified_csl_result_text(project_root, run, artifact)
            if text is None or not csl_tool_result_is_successful(name, text):
                continue
            reused = csl_reuse_reply(text, {
                "message": (f"Reused completed CSL result; no computation was run. "
                            f"run_id={run.run_id}; artifact_id={artifact.artifact_id}; "
                            f"input_fingerprint={fingerprint}."),
                "scope": "completed_run",
                "run_id": run.run_id,
                "artifact_id": artifact.artifact_id,
                "input_fingerprint": fingerprint,
            })
            remember(fingerprint, reused)
            return reused
        return None

    def near_csl_from_run(args: dict[str, Any]) -> str:
        """Screen every saved row of a completed exact enumeration in one call."""
        if args.get("source_pairs"):
            return "Pass either source_run_id or source_pairs, not both"
        project_id = runtime_project_id()
        if not project_id:
            return "source_run_id needs a project; pass the rows as source_pairs"
        try:
            rows, source = exact_plane_rows_from_run(
                store, project_id, str(args["source_run_id"]), axis=args["axis"],
                branches=args.get("source_branches"),
            )
        except (OSError, ValueError) as exc:
            return str(exc)
        return screen_near_csl_sources(
            args["axis"], rows,
            max_index=args["max_index"],
            max_plane_deviation_degrees=args["max_plane_deviation_degrees"],
            max_misorientation_delta_degrees=args["max_misorientation_delta_degrees"],
            max_suggestions_per_pair=args["max_suggestions_per_pair"],
            deduplicate=args["deduplicate"],
            source=source,
        )

    def execute(
        name: str,
        args: dict[str, Any],
        implementation: BaseTool,
    ) -> str:
        fingerprint = csl_tool_input_fingerprint(name, args)
        reused = cached_result(name, args)
        if reused is not None:
            return reused
        if name == "suggest_low_index_near_csl_planes" and args.get("source_run_id"):
            output = near_csl_from_run(args)
        else:
            output = str(implementation.invoke(args))
        if csl_tool_result_is_successful(name, output):
            remember(fingerprint, output)
        return output

    def cached(base: BaseTool) -> BaseTool:
        description = f"{base.description.rstrip()}\n\n{_REUSE_NOTE}"
        # Same fields and title as the base tool; the schema's own description
        # follows the wrapper (it also enters context-budget accounting).
        schema = create_model(base.args_schema.__name__, __base__=base.args_schema, __doc__=description)

        def run(**kwargs: Any) -> str:
            # Validate against the base tool so omitted defaults enter the
            # input fingerprint exactly as the base tool would apply them.
            return execute(base.name, schema.model_validate(kwargs).model_dump(), base)

        return StructuredTool.from_function(
            func=run, name=base.name, description=description, args_schema=schema,
        )

    return [cached(base) for base in STRUCTURE_AGENT_TOOLS]

__all__ = ["make_project_csl_tools"]
