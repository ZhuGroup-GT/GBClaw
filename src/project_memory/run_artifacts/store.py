"""Domain artifact directories and portable per-run manifests."""

from __future__ import annotations

import json
import mimetypes
import uuid
from pathlib import Path
from typing import Any

from core.util.io import sha256_file, write_json_atomic
from extensions import workflow_memory_spec
from project_workspace.models import utc_now_iso
from project_workspace.persistence.locks import file_lock
from project_workspace.artifacts.models import ProvenanceClass
from project_memory.models import Workflow


class ArtifactStore:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.artifacts_root = self.project_root / "artifacts"
        self.artifacts_root.mkdir(parents=True, exist_ok=True)

    def create_run_directory(self, workflow: Workflow, run_id: str) -> Path:
        expected = self._run_path(workflow, run_id)
        # Run IDs are project-wide, including across independent tool services.
        # Serialize lookup + creation so two workflows cannot claim one ID.
        with file_lock(self.artifacts_root / ".runs.lock"):
            located = self._find_run(run_id)
            if located is not None:
                existing, _manifest = located
                if existing != expected:
                    raise ValueError(f"Run ID already belongs to another directory: {run_id}")
                return existing
            expected.mkdir(parents=True, exist_ok=True)
            write_json_atomic(
                expected / "manifest.json",
                {"run_id": run_id, "workflow": workflow.value, "files": []},
            )
        return expected

    def run_directory(self, run_id: str) -> Path:
        # Check only the registered workflow locations for this ID. No full
        # scan or instance-local cache can hide another service's latest writes.
        located = self._find_run(run_id)
        if located is None:
            raise ValueError(f"Artifact run not found: {run_id}")
        return located[0]

    def manifest_lock(self, run_id: str):
        return file_lock(self.run_directory(run_id) / ".manifest.lock")

    def run_ids(self) -> list[str]:
        return sorted(self._scan_manifests())

    def manifest_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "manifest.json"

    def write_input_json(self, run_id: str, payload: dict) -> str:
        path = self.run_directory(run_id) / "input.json"
        write_json_atomic(path, payload)
        self.register_file(
            run_id,
            "config",
            "input.json",
            ProvenanceClass.PRIMARY,
            media_type="application/json",
        )
        return path.relative_to(self.project_root).as_posix()

    def register_file(
        self,
        run_id: str,
        role: str,
        relative_path: str,
        provenance_class: ProvenanceClass,
        *,
        media_type: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        with self.manifest_lock(run_id):
            return self._register_file(run_id, role, relative_path, provenance_class,
                                       media_type=media_type, metadata=metadata)

    def _register_file(self, run_id: str, role: str, relative_path: str,
                       provenance_class: ProvenanceClass, *, media_type=None, metadata=None) -> dict:
        run_root = self.run_directory(run_id)
        normalized, path = self._resolve_run_file(run_root, relative_path)
        if not path.is_file():
            raise ValueError(f"Artifact file does not exist: {relative_path}")
        manifest = self.read_manifest(run_id)
        artifact_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:{role}:{normalized}")
        )
        entry = {
            "artifact_id": artifact_id,
            "role": role,
            "path": normalized,
            "provenance_class": provenance_class.value,
            "permanent": provenance_class.permanent,
            "media_type": media_type or mimetypes.guess_type(path.name)[0],
            "size_bytes": path.stat().st_size,
            "metadata": dict(metadata or {}),
            "created_at": utc_now_iso(),
        }
        if provenance_class.permanent:
            digest = sha256_file(path)
            supplied = entry["metadata"].get("sha256")
            if supplied is not None and supplied != digest:
                raise ValueError(f"Artifact content disagrees with supplied sha256: {relative_path}")
            entry["metadata"]["sha256"] = digest
        files = list(manifest.get("files") or [])
        for index, current in enumerate(files):
            if current.get("artifact_id") == artifact_id:
                entry["created_at"] = current.get("created_at") or entry["created_at"]
                files[index] = entry
                break
        else:
            files.append(entry)
        manifest["files"] = files
        write_json_atomic(self.manifest_path(run_id), manifest)
        return entry

    def read_manifest(self, run_id: str) -> dict:
        located = self._find_run(run_id)
        if located is None:
            raise ValueError(f"Manifest not found for run {run_id}")
        return located[1]

    def report_usage(self, *, workflow: Workflow | None = None) -> dict:
        bytes_by_provenance = {item.value: 0 for item in ProvenanceClass}
        artifact_count = 0
        run_count = 0
        for _directory, manifest in self._scan_manifests().values():
            if workflow is not None and manifest.get("workflow") != workflow.value:
                continue
            run_count += 1
            for item in manifest.get("files") or []:
                provenance = str(item.get("provenance_class") or "debug")
                if provenance not in bytes_by_provenance:
                    continue
                bytes_by_provenance[provenance] += int(item.get("size_bytes") or 0)
                artifact_count += 1
        return {
            "run_count": run_count,
            "artifact_count": artifact_count,
            "bytes_by_provenance": bytes_by_provenance,
            "scientific_bytes": (
                bytes_by_provenance[ProvenanceClass.PRIMARY.value]
                + bytes_by_provenance[ProvenanceClass.DERIVED.value]
            ),
            "total_bytes": sum(bytes_by_provenance.values()),
        }

    def remove_debug_artifact(self, run_id: str, artifact_id: str) -> None:
        with self.manifest_lock(run_id):
            self._remove_debug_artifact(run_id, artifact_id)

    def _remove_debug_artifact(self, run_id: str, artifact_id: str) -> None:
        manifest = self.read_manifest(run_id)
        files = list(manifest.get("files") or [])
        match = next(
            (item for item in files if item.get("artifact_id") == artifact_id), None
        )
        if match is None:
            raise ValueError(f"Artifact not found: {artifact_id}")
        if match.get("provenance_class") != ProvenanceClass.DEBUG.value:
            raise ValueError("Artifact cleanup may remove only debug artifacts")
        _, path = self._resolve_run_file(
            self.run_directory(run_id), str(match.get("path") or "")
        )
        if path.exists():
            path.unlink()
        manifest["files"] = [
            item for item in files if item.get("artifact_id") != artifact_id
        ]
        write_json_atomic(self.manifest_path(run_id), manifest)

    def _run_path(self, workflow: Workflow, run_id: str) -> Path:
        if not run_id or run_id in {".", ".."} or Path(run_id).name != run_id or "\\" in run_id:
            raise ValueError(f"Invalid artifact run ID: {run_id}")
        path = (self.project_root / workflow_memory_spec(workflow).artifact_directory / run_id).resolve()
        if not path.is_relative_to(self.project_root):
            raise ValueError(f"Artifact run escapes project: {run_id}")
        return path

    def _validate_manifest(self, path: Path, workflow: Workflow, run_id: str) -> dict[str, Any]:
        try:
            manifest = self._read_manifest_path(path)
            if manifest.get("run_id") != run_id or manifest.get("workflow") != workflow.value:
                raise ValueError(f"Manifest identity mismatch for run {run_id}")
            if not path.resolve().is_relative_to(self.project_root):
                raise ValueError(f"Manifest escapes project: {run_id}")
        except (OSError, ValueError, AttributeError) as exc:
            raise ValueError(f"Invalid manifest for {run_id}: {exc}") from exc
        return manifest

    def _find_run(self, run_id: str) -> tuple[Path, dict[str, Any]] | None:
        found: tuple[Path, dict[str, Any]] | None = None
        for workflow in Workflow:
            run_root = self._run_path(workflow, run_id)
            manifest_path = run_root / "manifest.json"
            if not manifest_path.exists():
                continue
            manifest = self._validate_manifest(manifest_path, workflow, run_id)
            if found is not None and found[0] != run_root:
                raise ValueError(f"Run ID belongs to multiple artifact directories: {run_id}")
            found = (run_root, manifest)
        return found

    def _scan_manifests(self) -> dict[str, tuple[Path, dict[str, Any]]]:
        """Read a fresh disk inventory only for enumeration/usage requests."""
        directories: dict[str, tuple[Path, dict[str, Any]]] = {}
        for workflow in Workflow:
            spec = workflow_memory_spec(workflow)
            directory = self.project_root / spec.artifact_directory
            for manifest_path in directory.glob("*/manifest.json"):
                run_id = manifest_path.parent.name
                manifest = self._validate_manifest(manifest_path, workflow, run_id)
                run_root = self._run_path(workflow, run_id)
                existing = directories.get(run_id)
                if existing is not None and existing[0] != run_root:
                    raise ValueError(f"Run ID belongs to multiple artifact directories: {run_id}")
                directories[run_id] = (run_root, manifest)
        return directories

    @staticmethod
    def _read_manifest_path(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("files"), list):
            raise ValueError(f"Invalid run manifest: {path}")
        return value

    @staticmethod
    def _resolve_run_file(run_root: Path, relative_path: str) -> tuple[str, Path]:
        candidate = Path(relative_path)
        if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
            raise ValueError(f"Artifact path must be run-relative: {relative_path}")
        normalized = candidate.as_posix()
        resolved = (run_root / candidate).resolve()
        if not resolved.is_relative_to(run_root):
            raise ValueError(f"Artifact path escapes run directory: {relative_path}")
        return normalized, resolved
