"""Coordination between semantic run rows and artifact manifests."""

from __future__ import annotations

import hashlib
import asyncio
import json
import uuid
from contextlib import contextmanager
from pathlib import Path

from core.util.graph_config import runtime_ids
from core.util.io import sha256_file, write_json_atomic
from project_memory.run_artifacts.store import ArtifactStore
from project_memory.storage.repository import MemoryRepository
from extensions import tool_memory_spec, workflow_memory_spec
from project_memory.publication.writes import commit_facts
from project_memory.publication.contracts import (
    contract_reference, load_hook, preflight_tool, resolve_publication_spec,
    validate_source_publication, validate_tool_fact_batch,
)
from project_memory.publication.session import ToolRunSession
from core.identifiers import new_run_id
from project_workspace.models import utc_now_iso
from project_workspace.artifacts.models import ArtifactRecord, ProvenanceClass
from project_memory.models import (
    FactEvidenceKind,
    FactRecord,
    FactStatus,
    RunRecord,
    RunSourceRecord,
    RunStatus,
    Workflow,
)


def _parameters_with_runtime_context(parameters: dict) -> dict:
    """Attach the active chat thread so cancelled turns can finalize their runs."""
    enriched = dict(parameters)
    project_id, chat_thread_id = runtime_ids()
    if chat_thread_id or project_id:
        enriched["_gbclaw_context"] = {
            "chat_thread_id": str(chat_thread_id or ""),
            "project_id": str(project_id or ""),
        }
    return enriched


class RunService:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.memory = MemoryRepository(self.project_root)
        self.artifacts = ArtifactStore(self.project_root)

    def create_tool_run(
        self, memory_tool: str, *, parameters: dict,
        source_run_id: str | None = None, source_fingerprint: str | None = None,
        sources: list[RunSourceRecord] | None = None, now: str | None = None,
    ) -> RunRecord:
        """Start a registered tool without repeating workflow memory wiring."""
        spec = tool_memory_spec(memory_tool)
        return self.create_running_run(
            workflow=Workflow(spec.workflow), parameters=parameters, memory_tool=memory_tool,
            source_run_id=source_run_id, source_fingerprint=source_fingerprint,
            sources=sources, now=now,
        )

    @contextmanager
    def tool_run(self, memory_tool: str, *, parameters: dict, **source_options):
        """Own a tool lifecycle; finish with session.complete and a scientific result.

        Exceptions and accidentally omitted completion produce a failed run with
        its already registered artifacts retained. This wrapper never computes or
        retries a scientific operation.
        """
        run = self.create_tool_run(memory_tool, parameters=parameters, **source_options)
        try:
            yield ToolRunSession(self, run, memory_tool)
            if self.memory.get_run(run.run_id).status is RunStatus.RUNNING:
                raise ValueError(f"Tool {memory_tool} exited without completing run {run.run_id}")
        except BaseException as exc:
            if self.memory.get_run(run.run_id).status is RunStatus.RUNNING:
                transition = (self.cancel_run if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, GeneratorExit))
                              else self.fail_run)
                transition(run.run_id, error=str(exc) or type(exc).__name__)
            raise

    @staticmethod
    def _bound_tool_parameters(workflow: Workflow, parameters: dict, memory_tool: str | None) -> dict:
        """Bind registered configured/running lifecycles before any artifact IO."""
        memory_tool = memory_tool or parameters.get("memory_tool")
        if "_memory_contract" in parameters:
            raise ValueError("parameters._memory_contract is reserved for the publication service")
        if memory_tool is None:
            return parameters
        if parameters.get("memory_tool", memory_tool) != memory_tool:
            raise ValueError("parameters.memory_tool conflicts with the selected tool")
        spec = tool_memory_spec(memory_tool)
        if str(workflow) != spec.workflow:
            raise ValueError("Registered memory tool does not match the run workflow")
        preflight_tool(spec)
        return {**parameters, "memory_tool": memory_tool, "_memory_contract": contract_reference(spec)}

    def create_configured_run(
        self,
        *,
        workflow: Workflow,
        input_payload: dict,
        parameters: dict,
        source_run_id: str | None = None,
        source_fingerprint: str | None = None,
        sources: list[RunSourceRecord] | None = None,
        now: str | None = None,
        memory_tool: str | None = None,
    ) -> RunRecord:
        parameters = self._bound_tool_parameters(workflow, parameters, memory_tool)
        if workflow_memory_spec(workflow).canonical_config:
            if "canonical_config" in parameters and parameters["canonical_config"] != input_payload:
                raise ValueError("canonical_config must match the authoritative input payload")
            parameters = {**parameters, "canonical_config": input_payload}
        timestamp = now or utc_now_iso()
        run_id = self._new_run_id(workflow, timestamp)
        self.artifacts.create_run_directory(workflow, run_id)
        config_path = self.artifacts.write_input_json(run_id, input_payload)
        run = RunRecord(
            run_id=run_id,
            workflow=workflow,
            status=RunStatus.CONFIGURED,
            source_run_id=source_run_id,
            source_fingerprint=source_fingerprint,
            sources=self._normalized_sources(
                source_run_id=source_run_id,
                source_fingerprint=source_fingerprint,
                sources=sources,
            ),
            config_path=config_path,
            manifest_path=self._project_relative(
                self.artifacts.manifest_path(run_id)
            ),
            parameters=_parameters_with_runtime_context(parameters),
            created_at=timestamp,
            updated_at=timestamp,
        )
        try:
            self.memory.create_run(run)
            self.reindex_artifacts(run_id)
        except Exception as exc:
            raise RuntimeError(
                f"index_sync_failed while creating run {run_id}: {exc}"
            ) from exc
        return self.memory.get_run(run_id)

    def create_running_run(
        self,
        *,
        workflow: Workflow,
        parameters: dict,
        source_run_id: str | None = None,
        source_fingerprint: str | None = None,
        sources: list[RunSourceRecord] | None = None,
        now: str | None = None,
        memory_tool: str | None = None,
    ) -> RunRecord:
        parameters = self._bound_tool_parameters(workflow, parameters, memory_tool)
        timestamp = now or utc_now_iso()
        run_id = self._new_run_id(workflow, timestamp)
        self.artifacts.create_run_directory(workflow, run_id)
        run = RunRecord(
            run_id=run_id,
            workflow=workflow,
            status=RunStatus.CONFIGURED,
            source_run_id=source_run_id,
            source_fingerprint=source_fingerprint,
            sources=self._normalized_sources(
                source_run_id=source_run_id,
                source_fingerprint=source_fingerprint,
                sources=sources,
            ),
            manifest_path=self._project_relative(
                self.artifacts.manifest_path(run_id)
            ),
            parameters=_parameters_with_runtime_context(parameters),
            created_at=timestamp,
            updated_at=timestamp,
        )
        try:
            self.memory.create_run(run)
            return self.start_run(run_id, now=timestamp)
        except Exception as exc:
            raise RuntimeError(
                f"index_sync_failed while creating run {run_id}: {exc}"
            ) from exc

    def start_run(
        self,
        run_id: str,
        *,
        progress: dict | None = None,
        now: str | None = None,
    ) -> RunRecord:
        return self.memory.transition_run(
            run_id,
            RunStatus.RUNNING,
            at=now or utc_now_iso(),
            progress=progress,
        )

    def update_progress(
        self,
        run_id: str,
        *,
        progress: dict,
        now: str | None = None,
    ) -> RunRecord:
        return self.memory.update_run_progress(
            run_id,
            progress=progress,
            at=now or utc_now_iso(),
        )

    def complete_run(
        self,
        run_id: str,
        *,
        result: dict,
        progress: dict | None = None,
        now: str | None = None,
        memory_tool: str | None = None,
        conclusion: dict | None = None,
        memory_facts: list[FactRecord] | None = None,
        publication_mode: str | None = None,
    ) -> RunRecord:
        try:
            manifest = self.artifacts.read_manifest(run_id)
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"Cannot complete run {run_id}: manifest is unavailable: {exc}"
            ) from exc
        current = self.memory.get_run(run_id)
        if current.status is not RunStatus.RUNNING:
            raise ValueError(f"Run {run_id} must be running to complete")
        registered_tool = memory_tool or current.parameters.get("memory_tool")
        if conclusion is not None and memory_facts is not None:
            raise ValueError("Supply conclusion or memory_facts, not both")
        if publication_mode not in {None, "facts", "artifacts_only"}:
            raise ValueError("publication_mode must be facts or artifacts_only")
        if any(key in result for key in ("memory_publication", "memory_fact_refs", "memory_result_ref")):
            raise ValueError("Memory publication fields are reserved for the shared publication service")
        if registered_tool is not None:
            spec = tool_memory_spec(registered_tool)
            preflight_tool(spec)
            reference = current.parameters.get("_memory_contract")
            if isinstance(reference, dict):
                saved_spec = resolve_publication_spec(registered_tool, reference)
                if contract_reference(saved_spec) != contract_reference(spec):
                    raise ValueError("Tool contract changed while the run was active")
            if current.workflow.value != spec.workflow:
                raise ValueError("Registered memory tool does not match the run workflow")
            if current.parameters.get("memory_tool", registered_tool) != registered_tool:
                raise ValueError("Cannot change a run's registered memory tool")
            verified_roles = {artifact.role for artifact in self.verified_artifacts(run_id)}
            missing = set(spec.required_artifact_roles) - verified_roles
            if missing:
                raise ValueError(f"Missing required tool artifacts: {', '.join(sorted(missing))}")
            if publication_mode == "artifacts_only":
                if conclusion is not None or memory_facts is not None:
                    raise ValueError("artifacts_only mode cannot publish facts")
                if spec.conclusion_lookup_key is not None or spec.fact_builder is not None:
                    raise ValueError("Tool policy requires facts; artifacts_only mode is not allowed")
            elif conclusion is None and memory_facts is None and spec.fact_builder is not None:
                builder = load_hook(spec.fact_builder, positional_arguments=2, label="fact builder")
                memory_facts = builder(current.model_copy(deep=True), json.loads(json.dumps(result)))
                if not isinstance(memory_facts, list):
                    raise ValueError(f"Tool {registered_tool} fact builder must return a list")
            if publication_mode == "facts" and conclusion is None and not memory_facts:
                raise ValueError("facts mode requires a non-empty fact batch or conclusion")
            if spec.conclusion_lookup_key is not None and conclusion is None and memory_facts is None:
                raise ValueError(f"Tool {registered_tool} requires a scientific conclusion")
        elif conclusion is not None or memory_facts is not None or publication_mode is not None:
            raise ValueError("Register memory_tool before publishing facts")
        if conclusion is not None:
            result = self._stage_conclusion(current, registered_tool, result, conclusion)
        elif memory_facts is not None:
            result = self._stage_memory_facts(current, registered_tool, result, memory_facts)
        if registered_tool is not None:
            result = {**result, "memory_result_ref": {
                "run_id": run_id, "memory_tool": registered_tool,
                "project_root": str(self.project_root), "workflow": current.workflow.value,
                "manifest_path": current.manifest_path, "result_location": "runs.result_json",
                "publication_mode": publication_mode or ("facts" if "memory_publication" in result else "artifacts_only"),
                **contract_reference(spec),
            }}
        terminal = self.memory.transition_run(
            run_id,
            RunStatus.COMPLETED,
            at=now or utc_now_iso(),
            progress=progress,
            result=result,
        )
        if "memory_publication" in result:
            try:
                return self.repair_run_memory(run_id)
            except Exception as exc:
                # The durable pending pointer was committed with completion.
                # A later database outage must not turn successful science into
                # a failed computation or trigger a second scientific run.
                return terminal.model_copy(update={"result": {
                    **terminal.result, "fact_indexing_error": str(exc)[:1600],
                }})
        return terminal

    def _stage_conclusion(self, run: RunRecord, memory_tool: str, result: dict, value: dict) -> dict:
        """Make the exact fact payload durable before marking computation completed."""
        spec = tool_memory_spec(memory_tool)
        if spec.conclusion_lookup_key is None:
            raise ValueError(f"Tool {memory_tool} does not declare a conclusion")
        if not isinstance(value, dict):
            raise ValueError("conclusion must be an object")
        # Validate serialization before writing any file or success reference.
        json.dumps(value, ensure_ascii=False, allow_nan=False)
        lookup_key = spec.conclusion_lookup_key.format(run_id=run.run_id)
        timestamp = utc_now_iso()
        fact = FactRecord(
            fact_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"scientific_conclusion:{lookup_key}")),
            kind="scientific_conclusion", lookup_key=lookup_key, value=value,
            source_run_id=run.run_id, evidence_kind=FactEvidenceKind.DETERMINISTIC_WORKFLOW,
            created_at=timestamp, updated_at=timestamp,
        )
        self._validate_tool_facts(run, memory_tool, result, [fact])
        path = self.artifacts.run_directory(run.run_id) / "memory_conclusion.json"
        write_json_atomic(path, {
            "memory_tool": memory_tool, **contract_reference(spec), "fact": fact.model_dump(mode="json"),
        })
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        artifact = self.register_artifact(
            run.run_id, "memory_conclusion", path.name, ProvenanceClass.PRIMARY,
            media_type="application/json", metadata={"memory_tool": memory_tool, "sha256": digest},
        )
        return {
            **result, "conclusion_fact_id": fact.fact_id, "conclusion_lookup_key": fact.lookup_key,
            "memory_fact_refs": self._fact_refs([fact]),
            "memory_publication": {
                "status": "pending", "memory_tool": memory_tool, "artifact_id": artifact.artifact_id,
                "path": artifact.path, "sha256": digest, "attempts": 0,
                **contract_reference(spec),
            },
        }

    @staticmethod
    def _fact_refs(facts: list[FactRecord]) -> list[dict]:
        return [{"fact_id": fact.fact_id, "kind": fact.kind, "lookup_key": fact.lookup_key,
                 "source_run_id": fact.source_run_id} for fact in facts]

    @staticmethod
    def _same_fact(left: FactRecord, right: FactRecord) -> bool:
        """Semantic identity for retry; timestamps and versions are checked separately."""
        return all(getattr(left, field) == getattr(right, field) for field in (
            "fact_id", "kind", "lookup_key", "value", "status", "source_turn_id",
            "source_run_id", "evidence_kind",
        ))

    def _validate_tool_facts(
        self, run: RunRecord, memory_tool: str, result: dict, facts: list[FactRecord], *, spec=None,
    ) -> None:
        spec = spec or tool_memory_spec(memory_tool)
        if spec.workflow != run.workflow.value:
            raise ValueError("Publication tool/workflow identity mismatch")
        validate_tool_fact_batch(run, spec, facts, require_conclusion=True)
        validate_source_publication(run, result, facts, spec=spec)

    def _stage_memory_facts(
        self, run: RunRecord, memory_tool: str, result: dict, facts: list[FactRecord],
    ) -> dict:
        """Stage all tool-specific entries and update preconditions before completion."""
        self._validate_tool_facts(run, memory_tool, result, facts)
        spec = tool_memory_spec(memory_tool)
        staged = []
        for fact in facts:
            expected = None
            if spec.shared_fact_keys:
                current = self.memory.query_facts(
                    kind=fact.kind, lookup_key=fact.lookup_key, include_retracted=True, limit=1,
                )
                if current:
                    previous = current[0]
                    if (previous.status is not FactStatus.ACTIVE
                            or previous.evidence_kind is not FactEvidenceKind.DETERMINISTIC_WORKFLOW
                            or previous.fact_id != fact.fact_id):
                        raise ValueError("Tool publication cannot replace retracted or differently owned facts")
                    expected = previous.version
            # New rows always begin at version 1; repository assigns update versions.
            staged.append({"fact": fact.model_copy(update={"version": 1}).model_dump(mode="json"),
                           "expected_version": expected})
        path = self.artifacts.run_directory(run.run_id) / "memory_facts.json"
        write_json_atomic(path, {
            "memory_tool": memory_tool, **contract_reference(spec), "entries": staged,
        })
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        artifact = self.register_artifact(
            run.run_id, "memory_facts", path.name, ProvenanceClass.PRIMARY,
            media_type="application/json", metadata={"memory_tool": memory_tool, "sha256": digest},
        )
        refs = self._fact_refs(facts)
        staged_result = {
            **result, "memory_fact_refs": refs,
            "memory_publication": {
                "status": "pending", "memory_tool": memory_tool, "artifact_id": artifact.artifact_id,
                "path": artifact.path, "sha256": digest, "attempts": 0, "format": "fact_batch",
                **contract_reference(spec),
            },
        }
        main_key = spec.conclusion_lookup_key and spec.conclusion_lookup_key.format(run_id=run.run_id)
        for fact in facts:
            if fact.kind == "scientific_conclusion" and fact.lookup_key == main_key:
                staged_result.update(conclusion_fact_id=fact.fact_id, conclusion_lookup_key=fact.lookup_key)
        return staged_result

    def _commit_staged_facts(self, entries: list[dict]) -> None:
        """Atomically apply the snapshot or acknowledge its exact previous commit."""
        facts, versions = [], {}
        for entry in entries:
            fact = FactRecord.model_validate(entry["fact"])
            expected = entry.get("expected_version")
            if expected is not None and (type(expected) is not int or expected < 1):
                raise ValueError("Invalid staged fact version")
            existing = self.memory.query_facts(
                kind=fact.kind, lookup_key=fact.lookup_key, include_retracted=True, limit=1,
            )
            version = expected
            if existing:
                current = existing[0]
                if self._same_fact(current, fact) and current.version == (expected or 0) + 1:
                    version = current.version
                elif (expected is None or current.version != expected
                      or current.status is not FactStatus.ACTIVE
                      or current.fact_id != fact.fact_id
                      or current.evidence_kind is not FactEvidenceKind.DETERMINISTIC_WORKFLOW):
                    raise ValueError("Saved fact changed; recovery must not overwrite newer evidence")
            elif expected is not None:
                raise ValueError("Saved fact disappeared; recovery cannot recreate newer evidence")
            facts.append(fact)
            versions[(fact.kind, fact.lookup_key)] = version
        commit_facts(self.memory, facts, expected_versions=versions,
                     writer="run_publication", skip_unchanged=True)

    def repair_run_memory(self, run_id: str) -> RunRecord:
        """Publish a staged fact batch idempotently, without rerunning its tool.

        Failed attempts remain visible in the completed run. Automatic recovery
        stops after three attempts; explicit calls may retry after the cause is fixed.
        """
        run = self.memory.get_run(run_id)
        publication = run.result.get("memory_publication")
        if run.status is not RunStatus.COMPLETED or not isinstance(publication, dict):
            return run
        if publication.get("status") == "completed":
            return run
        next_publication = {**publication, "attempts": int(publication.get("attempts", 0)) + 1}
        try:
            spec = resolve_publication_spec(publication["memory_tool"], publication)
            is_batch = publication.get("format") == "fact_batch"
            if spec.workflow != run.workflow.value or (not is_batch and spec.conclusion_lookup_key is None):
                raise ValueError("Publication tool/workflow identity mismatch")
            artifact = self.memory.get_artifact(publication["artifact_id"])
            path = (self.project_root / artifact.path).resolve()
            run_root = self.artifacts.run_directory(run_id).resolve()
            manifest = self.artifacts.read_manifest(run_id)
            entry = next((item for item in manifest["files"] if item["artifact_id"] == artifact.artifact_id), None)
            if (artifact.run_id != run_id or artifact.role != ("memory_facts" if is_batch else "memory_conclusion")
                    or artifact.path != publication["path"] or not path.is_relative_to(run_root)
                    or entry is None or (run_root / entry["path"]).resolve() != path
                    or entry.get("role") != artifact.role):
                raise ValueError("Publication artifact does not match its run manifest")
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != publication["sha256"]:
                raise ValueError("Publication artifact content hash changed")
            payload = json.loads(raw)
            if payload["memory_tool"] != spec.key:
                raise ValueError("Publication fact identity or evidence mismatch")
            if any(payload.get(key) != publication.get(key)
                   for key in ("contract_version", "contract_sha256")):
                raise ValueError("Publication artifact contract differs from its pointer")
            if is_batch:
                staged_entries = payload["entries"]
                facts = [FactRecord.model_validate(item["fact"]) for item in staged_entries]
                if self._fact_refs(facts) != run.result.get("memory_fact_refs"):
                    raise ValueError("Publication fact references changed")
                if not spec.shared_fact_keys and any(item.get("expected_version") is not None for item in staged_entries):
                    raise ValueError("Run-scoped facts cannot overwrite an existing slot")
            else:
                fact = FactRecord.model_validate(payload["fact"])
                if (fact.kind != "scientific_conclusion"
                        or fact.lookup_key != spec.conclusion_lookup_key.format(run_id=run_id)
                        or fact.fact_id != run.result["conclusion_fact_id"]):
                    raise ValueError("Publication fact identity or evidence mismatch")
                facts = [fact]
                staged_entries = [{"fact": payload["fact"], "expected_version": None}]
            self._validate_tool_facts(run, spec.key, run.result, facts, spec=spec)
            self._commit_staged_facts(staged_entries)
            next_publication.update(status="completed")
            next_publication.pop("error", None)
        except Exception as exc:
            next_publication.update(
                status="blocked" if next_publication["attempts"] >= 3 else "pending",
                error=str(exc)[:1600],
            )
        return self.memory.update_run_publication(
            run_id, next_publication, expected_artifact_id=publication.get("artifact_id"), at=utc_now_iso(),
        )

    def repair_pending_memory(self, *, limit: int = 8) -> list[RunRecord]:
        """Retry a bounded set of pending publications at the next project request."""
        return [self.repair_run_memory(run_id) for run_id in self.memory.pending_publication_run_ids(limit=limit)]

    def fail_run(
        self,
        run_id: str,
        *,
        error: str,
        result: dict | None = None,
        progress: dict | None = None,
        now: str | None = None,
    ) -> RunRecord:
        return self.memory.transition_run(
            run_id,
            RunStatus.FAILED,
            at=now or utc_now_iso(),
            progress=progress,
            result=result,
            error=error,
        )

    def cancel_run(
        self,
        run_id: str,
        *,
        error: str,
        result: dict | None = None,
        progress: dict | None = None,
        now: str | None = None,
    ) -> RunRecord:
        return self.memory.transition_run(
            run_id,
            RunStatus.CANCELLED,
            at=now or utc_now_iso(),
            progress=progress,
            result=result,
            error=error,
        )

    def register_artifact(
        self,
        run_id: str,
        role: str,
        relative_path: str,
        provenance_class: ProvenanceClass,
        *,
        media_type: str | None = None,
        metadata: dict | None = None,
    ) -> ArtifactRecord:
        with self.artifacts.manifest_lock(run_id):
            return self._register_artifact(run_id, role, relative_path, provenance_class,
                                           media_type=media_type, metadata=metadata)

    def _register_artifact(self, run_id, role, relative_path, provenance_class,
                           *, media_type=None, metadata=None) -> ArtifactRecord:
        current = self.memory.get_run(run_id)
        if current.status is RunStatus.COMPLETED:
            path = (self.artifacts.run_directory(run_id) / relative_path).relative_to(self.project_root).as_posix()
            for previous in self.memory.query_artifacts(run_id=run_id):
                if previous.path == path and previous.provenance_class.permanent:
                    # Do not overwrite the original digest by registering tampered
                    # completed evidence again under the same or a different role.
                    self.resolve_artifact(previous, require_completed=True)
                    if role != previous.role or not provenance_class.permanent:
                        raise ValueError("Completed permanent artifact identity cannot be changed")
        entry = self.artifacts.register_file(
            run_id,
            role,
            relative_path,
            provenance_class,
            media_type=media_type,
            metadata=metadata,
        )
        artifact = self._artifact_from_manifest(run_id, entry)
        try:
            self.memory.register_artifact(artifact)
        except Exception as exc:
            raise RuntimeError(
                f"index_sync_failed for artifact {artifact.artifact_id}: {exc}"
            ) from exc
        return artifact

    def resolve_artifact(self, artifact: ArtifactRecord, *, expected_role: str | None = None,
                         require_completed: bool = False) -> Path:
        """Verify file identity once for readers, staging and completion checks."""
        run = self.memory.get_run(artifact.run_id)
        if require_completed and run.status is not RunStatus.COMPLETED:
            raise ValueError(f"Source run must be completed: {run.run_id}")
        manifest = self.artifacts.read_manifest(run.run_id)
        if manifest.get("run_id") != run.run_id or manifest.get("workflow") != run.workflow.value:
            raise ValueError(f"Manifest identity mismatch for run {run.run_id}")
        entry = next((item for item in manifest["files"]
                      if item.get("artifact_id") == artifact.artifact_id), None)
        if entry is None or entry.get("role") != artifact.role or (
            expected_role is not None and artifact.role != expected_role
        ):
            raise ValueError(f"Artifact role/identity disagrees with manifest: {artifact.artifact_id}")
        relative = Path(artifact.path)
        manifest_relative = Path(str(entry.get("path") or ""))
        if any(p.is_absolute() or not p.parts or ".." in p.parts for p in (relative, manifest_relative)):
            raise ValueError(f"Invalid artifact path: {artifact.artifact_id}")
        root = self.artifacts.run_directory(run.run_id).resolve()
        indexed = self.project_root / relative
        expected = root / manifest_relative
        if indexed != expected or not indexed.resolve().is_relative_to(root):
            raise ValueError(f"Artifact path disagrees with manifest: {artifact.artifact_id}")
        current = self.project_root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"Artifact symlink is not permitted: {artifact.artifact_id}")
        if not indexed.is_file():
            raise ValueError(f"Artifact file is missing: {artifact.path}")
        for size in (artifact.size_bytes, entry.get("size_bytes")):
            if run.status is RunStatus.COMPLETED and size is not None and indexed.stat().st_size != size:
                raise ValueError(f"Artifact size changed: {artifact.artifact_id}")
        hashes = {value for value in (artifact.metadata.get("sha256"),
                                      (entry.get("metadata") or {}).get("sha256")) if value}
        if len(hashes) > 1:
            raise ValueError(f"Artifact hash index disagrees with manifest: {artifact.artifact_id}")
        if hashes:
            if sha256_file(indexed) not in hashes:
                raise ValueError(f"Artifact content hash changed: {artifact.artifact_id}")
        return indexed

    def verified_artifacts(self, run_id: str) -> list[ArtifactRecord]:
        """Return indexed files whose identity and path agree with the run manifest."""
        verified = []
        for artifact in self.memory.query_artifacts(run_id=run_id):
            try:
                self.resolve_artifact(artifact)
            except (ValueError, OSError):
                continue
            verified.append(artifact)
        return verified

    def reindex_artifacts(self, run_id: str | None = None) -> int:
        if run_id is None:
            run_ids = self.artifacts.run_ids()
        else:
            run_ids = [run_id]
        count = 0
        for current_run_id in run_ids:
            with self.artifacts.manifest_lock(current_run_id):
                manifest = self.artifacts.read_manifest(current_run_id)
                artifacts = [
                    self._artifact_from_manifest(current_run_id, entry)
                    for entry in manifest.get("files") or []
                ]
                self.memory.replace_run_artifacts(current_run_id, artifacts)
                count += len(artifacts)
        return count

    def _artifact_from_manifest(self, run_id: str, entry: dict) -> ArtifactRecord:
        relative = (
            self.artifacts.run_directory(run_id).relative_to(self.project_root)
            / str(entry["path"])
        ).as_posix()
        return ArtifactRecord(
            artifact_id=str(entry["artifact_id"]),
            run_id=run_id,
            role=str(entry["role"]),
            path=relative,
            provenance_class=ProvenanceClass(entry["provenance_class"]),
            media_type=entry.get("media_type"),
            size_bytes=entry.get("size_bytes"),
            metadata=dict(entry.get("metadata") or {}),
            created_at=str(entry["created_at"]),
        )

    def _project_relative(self, path: Path) -> str:
        return path.relative_to(self.project_root).as_posix()

    @staticmethod
    def _normalized_sources(
        *,
        source_run_id: str | None,
        source_fingerprint: str | None,
        sources: list[RunSourceRecord] | None,
    ) -> list[RunSourceRecord]:
        normalized = [
            item
            if isinstance(item, RunSourceRecord)
            else RunSourceRecord.model_validate(item)
            for item in (sources or [])
        ]
        if source_run_id and all(
            item.source_run_id != source_run_id for item in normalized
        ):
            normalized.append(
                RunSourceRecord(
                    source_run_id=source_run_id,
                    source_fingerprint=source_fingerprint,
                )
            )
        identities = [
            (item.relation, item.ordinal, item.source_run_id)
            for item in normalized
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("run sources must have unique relation/ordinal/source tuples")
        slots = [(item.relation, item.ordinal) for item in normalized]
        if len(slots) != len(set(slots)):
            raise ValueError("run sources must have unique ordinal values within each relation")
        return sorted(
            normalized,
            key=lambda item: (item.relation, item.ordinal, item.source_run_id),
        )

    @staticmethod
    def _new_run_id(workflow: Workflow, timestamp: str) -> str:
        return new_run_id(workflow_memory_spec(workflow).run_prefix, timestamp)
