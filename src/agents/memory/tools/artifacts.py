"""Manifest-bounded tools for listing and reading scientific artifacts."""

from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

from core.util.graph_config import runtime_project_id
from project_workspace.artifacts.files import TEXT_EXTENSIONS
from project_memory.models import WorkflowFilter
from extensions import resolve_workflow_filter, workflow_query_help
from project_workspace.store import ProjectStore


DEFAULT_ARTIFACT_READ_CHARS = 12_000
MAX_ARTIFACT_READ_CHARS = 24_000

# These are text-encoded machine data, not language-model-readable scientific text.
# They remain permanent artifacts and can be passed to LAMMPS/OVITO by run ID and role.
_ATOMIC_DATA_EXTENSIONS = frozenset(
    {
        ".cfg",
        ".cif",
        ".data",
        ".lammpstrj",
        ".lmp",
        ".poscar",
        ".vasp",
        ".xyz",
    }
)
_ATOMIC_DATA_ROLES = frozenset(
    {
        "source_cfg",
        "converted_lmp",
        "initial_cfg",
        "intermediate_structure",
        "minimized_cfg",
        "minimized_lmp",
        "sheared_cfg",
        "sheared_lmp",
        "trajectory",
    }
)


def make_project_artifact_tools(store: ProjectStore) -> list[BaseTool]:
    def context():
        project_id = runtime_project_id()
        if not project_id:
            raise ValueError("No active project_id in run config.")
        return project_id, store.project_path(project_id), store.runs(project_id)

    def verified(run_id: str, role: str | None = None):
        _project_id, project_root, service = context()
        rows = service.memory.query_artifacts(run_id=run_id, role=role)
        for row in rows:
            service.resolve_artifact(row, expected_role=role)
        return project_root, service, rows

    @tool
    def list_run_artifacts(run_id: str, role: str | None = None, offset: int = 0,
                           limit: int = 20, offset_chars: int = 0, max_chars: int = 12_000,
                           snapshot_id: str | None = None) -> str:
        """List manifest-verified artifacts in pages; continue with next_offset.

        Large metadata is returned in lossless JSON text pages, with a stable
        full_output snapshot reference. Use snapshot_id for character continuation.
        """
        from agents.memory.tools.output import paged_json_result
        if snapshot_id:
            try:
                return paged_json_result({}, store=store, project_id=runtime_project_id(),
                                         tool_name="list_run_artifacts", snapshot_id=snapshot_id,
                                         offset_chars=offset_chars, max_chars=max_chars)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)
        try:
            _root, _service, rows = verified(run_id, role)
        except ValueError as exc:
            return str(exc)
        if offset < 0:
            return "offset must be non-negative"
        end = min(len(rows), offset + min(100, max(1, limit)))
        from agents.memory.tools.output import paged_json_result
        try:
            response = paged_json_result({
                "run_id": run_id,
                "presentation_policy": "references_only",
                "artifacts": [item.model_dump(mode="json") for item in rows[offset:end]],
                "total": len(rows), "offset": offset, "next_offset": end if end < len(rows) else None,
            }, store=store, project_id=runtime_project_id(), tool_name="list_run_artifacts",
                offset_chars=offset_chars, max_chars=max_chars)
            payload = json.loads(response)
            # Artifact discovery is an index lookup, not a request to display
            # every image in the inventory (including paged inventories).
            payload["presentation_policy"] = "references_only"
            return json.dumps(payload, ensure_ascii=False)
        except ValueError as exc:
            return str(exc)

    @tool
    def read_run_artifact(
        run_id: str,
        role: str,
        artifact_id: str | None = None,
        max_chars: int = DEFAULT_ARTIFACT_READ_CHARS,
        offset_chars: int = 0,
    ) -> str:
        """Read human-readable scientific text, not atomic structures or trajectories.

        Structure/trajectory artifacts must be passed by run ID and role to LAMMPS or analysis
        tools instead of loading their coordinates into model context. Human-readable files up
        to 12,000 characters are returned completely by default (24,000 maximum per call).
        Prefer the scientific tool's compact conclusions for a normal answer.
        For larger files, use offsets to read only sections needed for a concrete
        unanswered question; do not automatically consume every chunk.
        """
        try:
            project_root, _service, rows = verified(run_id, role)
        except ValueError as exc:
            return str(exc)
        if artifact_id:
            rows = [item for item in rows if item.artifact_id == artifact_id]
        if not rows:
            return f"No artifact found for run={run_id!r}, role={role!r}"
        if len(rows) > 1:
            return "multiple artifacts match this role; pass artifact_id"
        artifact = rows[0]
        path = (project_root / artifact.path).resolve()
        if role in _ATOMIC_DATA_ROLES or path.suffix.lower() in _ATOMIC_DATA_EXTENSIONS:
            return (
                f"artifact is atomic structure/trajectory data and is not readable by the "
                f"language model: {artifact.path}. Pass run_id={run_id!r} and role={role!r} "
                f"to the appropriate LAMMPS or analysis tool instead."
            )
        if path.suffix.lower() not in TEXT_EXTENSIONS and not str(
            artifact.media_type or ""
        ).startswith("text/"):
            return f"artifact is not text-readable: {artifact.path}"
        if not path.is_file():
            return f"artifact file is missing: {artifact.path}"
        try:
            offset = int(offset_chars)
            requested_limit = int(max_chars)
        except (TypeError, ValueError):
            return "offset_chars and max_chars must be integers"
        if offset < 0:
            return "offset_chars must be non-negative"
        limit = min(MAX_ARTIFACT_READ_CHARS, max(1, requested_limit))
        text = path.read_text(encoding="utf-8", errors="replace")
        total = len(text)
        if offset > total:
            return f"offset_chars={offset} is beyond the artifact length of {total} characters"
        end = min(total, offset + limit)
        if offset == 0 and end == total:
            return text
        chunk = text[offset:end]
        prefix = (
            ""
            if offset == 0
            else f"… artifact chunk [{offset}:{end}] of {total} characters …\n"
        )
        if end < total:
            suffix = (
                f"\n… artifact is partial; returned [{offset}:{end}] of {total} characters. "
                f"Continue with offset_chars={end} …"
            )
        else:
            suffix = (
                f"\n… artifact complete; returned final chunk "
                f"[{offset}:{end}] of {total} characters …"
            )
        return prefix + chunk + suffix

    @tool
    def report_artifact_usage(workflow: WorkflowFilter | None = None) -> str:
        """Report artifact bytes by provenance without exposing deletion operations."""
        try:
            _project_id, _root, service = context()
            report = service.artifacts.report_usage(
                workflow=resolve_workflow_filter(workflow) if workflow is not None else None
            )
        except ValueError as exc:
            return str(exc)
        return json.dumps(report, ensure_ascii=False, sort_keys=True)

    report_artifact_usage.description += "\n\n" + workflow_query_help()
    return [list_run_artifacts, read_run_artifact, report_artifact_usage]


list_run_artifacts, read_run_artifact, report_artifact_usage = make_project_artifact_tools(
    ProjectStore()
)
