"""Run-bound tool API for artifact registration and explicit publication."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from project_memory.publication.service import RunService
    from project_memory.models import FactRecord, RunRecord


class ToolRunSession:
    """Bind a tool, project and run once; all writes retain that identity.

    Tools use ``complete`` and ``register_artifact`` without passing identity
    fields repeatedly. The current persisted record is available through ``run``.
    """

    def __init__(self, service: RunService, run: RunRecord, memory_tool: str) -> None:
        self._service = service
        self._run_id = run.run_id
        self._memory_tool = memory_tool
        self._project_root = service.project_root

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def memory_tool(self) -> str:
        return self._memory_tool

    @property
    def project_root(self):
        return self._project_root

    @property
    def run(self) -> RunRecord:
        return self._service.memory.get_run(self.run_id)

    @property
    def artifact_directory(self):
        return self._service.artifacts.run_directory(self.run_id)

    @property
    def result_ref(self) -> dict:
        """Durable full-result and evidence coordinates; never a truncated summary."""
        run = self.run
        return {
            "project_root": str(self.project_root), "run_id": self.run_id,
            "memory_tool": self.memory_tool, "workflow": run.workflow.value,
            "manifest_path": run.manifest_path, "result_location": "runs.result_json",
            "artifact_refs": [artifact.model_dump(mode="json")
                              for artifact in self._service.verified_artifacts(self.run_id)],
            "fact_refs": run.result.get("memory_fact_refs", []),
            "contract": run.parameters.get("_memory_contract", {}),
        }

    def register_artifact(self, role, relative_path, provenance_class, **options):
        return self._service.register_artifact(
            self.run_id, role, relative_path, provenance_class, **options,
        )

    def complete(
        self, *, result: dict, mode: Literal["facts", "artifacts_only"] = "facts",
        conclusion: dict | None = None, facts: list[FactRecord] | None = None,
        progress: dict | None = None,
    ) -> RunRecord:
        return self._service.complete_run(
            self.run_id, result=result, memory_tool=self.memory_tool,
            conclusion=conclusion, memory_facts=facts, progress=progress,
            publication_mode=mode,
        )
