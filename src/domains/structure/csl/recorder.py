"""Persist durable scientific outputs produced by the Structure worker."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from project_workspace.models import utc_now_iso
from project_memory.publication.service import RunService
from project_workspace.artifacts.models import ArtifactRecord, ProvenanceClass
from project_memory.models import FactEvidenceKind, FactRecord, RunStatus, Workflow
from domains.structure.csl import generator as csl
from domains.structure.csl.tools import CSL_TOOL_NAMES


_GB_CRYSTALLOGRAPHY_TOOLS = frozenset(
    {"match_csl_tilt_references", "score_csl_tilt_plane_pairs"}
)
_GB_CRYSTALLOGRAPHY_TOOL_PRIORITY = {
    "score_csl_tilt_plane_pairs": 1,
    "match_csl_tilt_references": 2,
}
_CSL_TOOL_SEMANTIC_VERSIONS = {
    # v3 also reduces raw plane-family variants to proper-cubic physical orbits.
    "analyze_tilt_geometry": 3,
    # Rotation branch identity (Sigma, m, n) became explicit in v2/v3.
    "list_csl_sigmas": 2,
    "list_csl_gb_planes": 2,
    "list_csl_gb_planes_batch": 2,
    "get_tilt_gb_orientation": 2,
    "score_csl_tilt_plane_pairs": 2,
    # v3 reads saved run rows, merges equivalent candidates and adds model_view.
    "suggest_low_index_near_csl_planes": 3,
    "match_csl_tilt_references": 3,
}


def normalized_csl_tool_input(
    input_payload: dict[str, Any] | str | None,
    *,
    name: str = "",
) -> dict[str, Any]:
    """Return a stable JSON-compatible tool input for indexing and reuse."""
    if isinstance(input_payload, str):
        try:
            values = json.loads(input_payload)
        except json.JSONDecodeError:
            return {"raw": input_payload}
    else:
        values = dict(input_payload or {})

    defaults: dict[str, dict[str, Any]] = {
        "analyze_tilt_geometry": {
            "axis": None,
            "plane1_is_family": False,
            "plane2_is_family": False,
            "axis_is_family": False,
        },
        "compare_miller_planes": {
            "plane2": None,
            "axis": None,
            "reference_family": None,
        },
        "suggest_low_index_near_csl_planes": {
            "source_pairs": None,
            "source_run_id": None,
            "source_branches": None,
            "max_index": 3,
            "max_plane_deviation_degrees": 5.0,
            "max_misorientation_delta_degrees": 5.0,
            "max_suggestions_per_pair": 3,
            "deduplicate": True,
            "gb_type": "tilt",
        },
        "match_csl_tilt_references": {
            "axis_is_family": False,
            "sigma_limit": 50,
            "plane_limit": 5,
            "max_plane_matches_per_sigma": 3,
            "max_misorientation_delta_degrees": 5.0,
            "max_plane_deviation_degrees": 5.0,
        },
        "score_csl_tilt_plane_pairs": {
            "max_misorientation_delta_degrees": 5.0,
            "max_plane_deviation_degrees": 5.0,
            "max_results": 20,
        },
        "list_csl_sigmas": {"limit": 50},
        "list_csl_gb_planes": {
            "limit": 2,
            "gb_type": None,
            "rotation_m": None,
            "rotation_n": None,
        },
        "list_csl_gb_planes_batch": {
            "limit": 2,
            "gb_type": None,
            "sigmas": None,
            "rotations": None,
        },
        "get_tilt_gb_orientation": {
            "a0": 2.8553,
            "rotation_m": None,
            "rotation_n": None,
        },
    }
    normalized = {**defaults.get(name, {}), **values}
    if "basis" in normalized:
        normalized["basis"] = str(normalized["basis"]).strip().lower()
    if normalized.get("gb_type") is not None:
        normalized["gb_type"] = str(normalized["gb_type"]).strip().lower() or None
    for key in (
        "u",
        "v",
        "w",
        "sigma",
        "rotation_m",
        "rotation_n",
        "limit",
        "max_index",
        "max_suggestions_per_pair",
        "sigma_limit",
        "plane_limit",
        "max_plane_matches_per_sigma",
        "max_results",
    ):
        if key in normalized and normalized[key] is not None:
            normalized[key] = int(normalized[key])
    for key in ("plane1", "plane2", "axis", "reference_family", "gb_plane", "sigmas"):
        if key in normalized and normalized[key] is not None:
            normalized[key] = [int(value) for value in normalized[key]]
    if normalized.get("source_pairs") is not None:
        normalized_pairs = []
        for source in normalized["source_pairs"]:
            row = dict(source)
            if row.get("sigma") is not None:
                row["sigma"] = int(row["sigma"])
            for key in ("rotation_m", "rotation_n"):
                if row.get(key) is not None:
                    row[key] = int(row[key])
                else:
                    row[key] = None
            for key in ("plane1", "plane2"):
                if row.get(key) is not None:
                    row[key] = [int(value) for value in row[key]]
            normalized_pairs.append(row)
        normalized["source_pairs"] = normalized_pairs
    if normalized.get("candidate_pairs") is not None:
        normalized_pairs = []
        for source in normalized["candidate_pairs"]:
            row = dict(source)
            row["sigma"] = int(row["sigma"])
            for key in ("rotation_m", "rotation_n"):
                if row.get(key) is not None:
                    row[key] = int(row[key])
                else:
                    row[key] = None
            for key in ("plane1", "plane2"):
                row[key] = [int(value) for value in row[key]]
            if row.get("reference_atom_count") is not None:
                row["reference_atom_count"] = int(row["reference_atom_count"])
            else:
                row["reference_atom_count"] = None
            row["boundary_type"] = str(row.get("boundary_type", "Tilt")).strip()
            normalized_pairs.append(row)
        normalized["candidate_pairs"] = normalized_pairs
    for field in ("rotations", "source_branches"):
        if normalized.get(field) is not None:
            normalized_rotations = []
            for selection in normalized[field]:
                row = dict(selection)
                for key in ("sigma", "rotation_m", "rotation_n"):
                    row[key] = int(row[key])
                normalized_rotations.append(row)
            normalized[field] = normalized_rotations
    if normalized.get("source_branches") == []:
        normalized["source_branches"] = None
    if normalized.get("sigmas") == []:
        normalized["sigmas"] = None
    if normalized.get("rotations") == []:
        normalized["rotations"] = None
    if all(key in normalized for key in ("u", "v", "w")):
        reduced_axis = csl.reduce_miller_indices(
            [normalized["u"], normalized["v"], normalized["w"]]
        )
        normalized.update(
            {"u": int(reduced_axis[0]), "v": int(reduced_axis[1]), "w": int(reduced_axis[2])}
        )
    if name in {
        "analyze_tilt_geometry",
        "match_csl_tilt_references",
        "score_csl_tilt_plane_pairs",
    }:
        for key in ("axis", "plane1", "plane2"):
            if normalized.get(key) is not None:
                normalized[key] = [
                    int(value)
                    for value in csl.reduce_miller_indices(normalized[key])
                ]
    if name == "score_csl_tilt_plane_pairs":
        for row in normalized.get("candidate_pairs", []):
            for key in ("plane1", "plane2"):
                row[key] = [
                    int(value)
                    for value in csl.reduce_miller_indices(row[key])
                ]
    if name == "get_tilt_gb_orientation" and normalized.get("gb_plane") is not None:
        normalized["gb_plane"] = [
            int(value) for value in csl.reduce_miller_indices(normalized["gb_plane"])
        ]
    if "a0" in normalized and normalized["a0"] is not None:
        normalized["a0"] = float(normalized["a0"])
    for key in (
        "max_plane_deviation_degrees",
        "max_misorientation_delta_degrees",
    ):
        if key in normalized and normalized[key] is not None:
            normalized[key] = float(normalized[key])
    return json.loads(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str)
    )


def csl_tool_input_fingerprint(
    name: str,
    input_payload: dict[str, Any] | str | None,
) -> str:
    payload = {
        "tool": str(name),
        "semantic_version": _CSL_TOOL_SEMANTIC_VERSIONS.get(str(name), 1),
        "input": normalized_csl_tool_input(input_payload, name=name),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def csl_orientation_lookup_key(
    *,
    basis: str,
    axis: list[int] | np.ndarray,
    sigma: int,
    plane: list[int] | np.ndarray,
    rotation_m: int | None = None,
    rotation_n: int | None = None,
) -> str:
    """Build the deterministic lookup key used by orientation facts."""
    if (rotation_m is None) != (rotation_n is None):
        raise ValueError("rotation_m and rotation_n must be supplied together")
    normalized_axis = csl.reduce_miller_indices(axis)
    normalized_plane = csl.reduce_miller_indices(plane)
    rotation = (
        f":{int(rotation_m)},{int(rotation_n)}"
        if rotation_m is not None
        else ""
    )
    return (
        f"{str(basis).strip().lower()}:"
        f"{','.join(str(int(value)) for value in normalized_axis)}:{int(sigma)}"
        f"{rotation}:"
        f"{','.join(str(int(value)) for value in normalized_plane)}"
    )


def gb_crystallography_lookup_key(
    *,
    basis: str,
    axis: list[int] | np.ndarray,
    plane1: list[int] | np.ndarray,
    plane2: list[int] | np.ndarray,
) -> str:
    """Build a stable per-geometry key for descriptive GB crystallography."""
    identity = {
        "basis": str(basis).strip().lower(),
        "axis": [int(value) for value in csl.reduce_miller_indices(axis)],
        "plane1": [int(value) for value in csl.reduce_miller_indices(plane1)],
        "plane2": [int(value) for value in csl.reduce_miller_indices(plane2)],
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    geometry_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"gb:crystallography:{geometry_id}"


def _json_tool_payload(output_text: str) -> dict[str, Any] | None:
    """Decode direct JSON output or a cached-result prefix followed by JSON."""
    text = str(output_text).strip()
    starts = [0] if text.startswith("{") else []
    starts.extend(match.start() + 1 for match in re.finditer(r"\n\{", text))
    for start in starts:
        try:
            payload = json.loads(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def csl_result_text(output_text: str) -> str:
    """Remove reuse notices without changing the saved scientific result."""
    text = str(output_text).strip()
    while text.startswith("Reused ") and "\n" in text:
        text = text.split("\n", 1)[1].lstrip()
    payload = _json_object(text)
    if payload is not None and "reused" in payload:
        payload.pop("reused")
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return text


def _json_object(text: str) -> dict[str, Any] | None:
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def csl_reuse_reply(result_text: str, notice: dict[str, Any]) -> str:
    """Attach a reuse notice; a JSON result stays a single JSON object.

    ``notice["message"]`` is the human-readable sentence; text results keep it as
    their first line, as before.
    """
    text = str(result_text).strip()
    payload = _json_object(text)
    if payload is None:
        return f"{notice['message']}\n{text}"
    earlier = payload.get("reused")
    reply = {**payload, "reused": {**notice, **({"earlier": earlier} if earlier else {})}}
    return json.dumps(reply, ensure_ascii=False, sort_keys=True)


def _reused_from(output_text: str) -> dict[str, Any] | None:
    payload = _json_object(str(output_text).strip())
    notice = payload.get("reused") if payload is not None else None
    if isinstance(notice, dict) and notice.get("scope") == "completed_run":
        return {key: notice.get(key) for key in ("run_id", "artifact_id", "input_fingerprint")}
    reused = re.search(
        r"Reused completed CSL result; no computation was run\. "
        r"run_id=([^;\s]+); artifact_id=([^;\s]+); input_fingerprint=([0-9a-f]+)",
        str(output_text),
    )
    return ({"run_id": reused.group(1), "artifact_id": reused.group(2),
             "input_fingerprint": reused.group(3)} if reused else None)


def csl_tool_result_is_successful(name: str, output_text: str) -> bool:
    """Recognize scientific outputs, including valid empty searches, not errors.

    Several legacy tools report validation errors as ordinary strings. A successful
    LangChain tool call alone therefore does not establish scientific evidence.
    """
    text = csl_result_text(output_text)
    if name in {
        "analyze_tilt_geometry", "compare_miller_planes", "match_csl_tilt_references",
        "score_csl_tilt_plane_pairs", "suggest_low_index_near_csl_planes",
    }:
        payload = _json_tool_payload(text)
        if not payload or payload.get("error"):
            return False
        required = {
            "analyze_tilt_geometry": ("input", "geometry_classification"),
            "compare_miller_planes": ("plane1",),
            "match_csl_tilt_references": ("scope", "input", "search_classification"),
            "score_csl_tilt_plane_pairs": ("scope", "input", "search_classification"),
            "suggest_low_index_near_csl_planes": ("axis", "criteria", "results"),
        }
        return all(key in payload for key in required[name])
    prefixes = {
        "list_csl_sigmas": ("CSL candidates for ", "No CSL values found for "),
        "list_csl_gb_planes": ("CSL GB planes — ", "No GB planes found for ", "No GB planes matched for "),
        "list_csl_gb_planes_batch": ("Batch CSL GB planes — ",),
        "get_tilt_gb_orientation": ("CSL tilt GB orientation for gb_minimize ",),
    }
    return bool(prefixes.get(name)) and text.startswith(prefixes[name])


def _compact_csl_reference(reference: object) -> dict[str, Any] | None:
    if not isinstance(reference, dict):
        return None
    keys = (
        "sigma",
        "rotation_m",
        "rotation_n",
        "theta_degrees",
        "misorientation_delta_degrees",
        "classification",
        "boundary_type",
        "exact_plane1",
        "exact_plane2",
        "plane1_deviation_degrees",
        "plane2_deviation_degrees",
        "max_plane_deviation_degrees",
        "total_plane_deviation_degrees",
        "reference_atom_count",
    )
    return {key: reference.get(key) for key in keys if key in reference}


class CslRunRecorder:
    """Store scientific CSL results without copying worker reasoning or summaries."""

    def __init__(self, project_root: Path) -> None:
        self.runs = RunService(project_root)
        self._run_id: str | None = None
        self._crystallography_evidence: list[dict[str, Any]] = []
        self._orientation_evidence: list[dict[str, Any]] = []
        self._memory_facts: list[FactRecord] | None = None

    def _bind_running_run(self, run_id: str) -> None:
        if self._run_id is not None and self._run_id != run_id:
            raise ValueError("A CSL recorder cannot combine evidence from different runs")
        run = self.runs.memory.get_run(run_id)
        if run.workflow is not Workflow.CSL or run.status is not RunStatus.RUNNING:
            raise ValueError("CSL evidence must be staged on a running CSL run")
        self._run_id = run_id

    def persist_tool_result(
        self,
        run_id: str,
        name: str,
        input_payload: dict[str, Any] | str | None,
        output_text: str,
        sequence: int,
        *,
        is_error: bool = False,
    ) -> bool:
        if (
            name not in CSL_TOOL_NAMES
            or is_error
            or not csl_tool_result_is_successful(name, output_text)
        ):
            return False
        self._bind_running_run(run_id)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "tool"
        relative = f"results/{sequence:02d}_{safe_name}.txt"
        path = self.runs.artifacts.run_directory(run_id) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(output_text), encoding="utf-8")
        reused = _reused_from(output_text)
        reuse_metadata = {"reused_from": reused} if reused else {}
        artifact = self.runs.register_artifact(
            run_id,
            "csl_result",
            relative,
            ProvenanceClass.PRIMARY,
            media_type="text/plain",
            metadata={
                "tool": name,
                "sequence": sequence,
                "input": normalized_csl_tool_input(input_payload, name=name),
                "input_fingerprint": csl_tool_input_fingerprint(name, input_payload),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                **reuse_metadata,
            },
        )
        self._memory_facts = None
        self._stage_crystallography_evidence(
            name=name,
            output_text=output_text,
            sequence=sequence,
            artifact=artifact,
        )
        if name == "get_tilt_gb_orientation":
            self._orientation_evidence.append({
                "input": normalized_csl_tool_input(input_payload, name=name),
                "artifact_id": artifact.artifact_id,
                "artifact_path": artifact.path,
                "sequence": sequence,
            })
        return True

    def _stage_crystallography_evidence(
        self,
        *,
        name: str,
        output_text: str,
        sequence: int,
        artifact: ArtifactRecord,
    ) -> None:
        if name not in _GB_CRYSTALLOGRAPHY_TOOLS:
            return
        payload = _json_tool_payload(output_text)
        if payload is None or payload.get("scope") != "tilt":
            return
        required = ("basis", "axis", "input", "search_classification")
        if any(key not in payload for key in required):
            return
        input_geometry = payload.get("input")
        if not isinstance(input_geometry, dict) or any(
            key not in input_geometry for key in ("plane1", "plane2")
        ):
            return
        self._crystallography_evidence.append(
            {
                "name": name,
                "sequence": int(sequence),
                "artifact_id": artifact.artifact_id,
                "artifact_path": artifact.path,
                "payload": payload,
            }
        )

    def persist_crystallography_fact(self, run_id: str) -> FactRecord | None:
        """Compatibility entry point for repairing a previously staged batch."""
        facts = self.persist_crystallography_facts(run_id)
        return facts[-1] if facts else None

    def persist_crystallography_facts(self, run_id: str) -> list[FactRecord]:
        """Repair the durable batch and return this run's current geometry facts.

        New callers build the facts while the run is running and pass them to
        ``RunService.complete_run(memory_tool="csl", memory_facts=...)``. This
        compatibility method never constructs or writes facts after completion.
        """
        run = self.runs.memory.get_run(run_id)
        if run.status is not RunStatus.COMPLETED:
            raise ValueError(
                "CSL facts require a completed supporting CSL run"
            )
        if run.workflow is not Workflow.CSL:
            raise ValueError("CSL facts require a supporting CSL run")
        publication = run.result.get("memory_publication")
        if not isinstance(publication, dict) or publication.get("memory_tool") != "csl":
            raise ValueError(
                "CSL run has no staged memory batch; build_memory_facts must be "
                "passed to complete_run before completing the run"
            )
        repaired = self.runs.repair_run_memory(run_id)
        if repaired.result["memory_publication"].get("status") != "completed":
            raise RuntimeError(
                repaired.result.get("fact_indexing_error")
                or "CSL memory publication is still pending"
            )
        # Shared geometry keys may since have been updated by another run. Only
        # return facts still supported by this run, never claim the later result.
        facts = []
        for reference in repaired.result.get("memory_fact_refs", []):
            if reference.get("kind") != "gb_crystallography":
                continue
            try:
                fact = self.runs.memory.get_fact(reference["fact_id"])
            except ValueError:
                continue
            if fact.source_run_id == run_id and fact.status.value == "active":
                facts.append(fact)
        return facts

    def build_memory_facts(self, run_id: str) -> list[FactRecord]:
        """Build typed CSL facts without publishing them or completing the run.

        Preserve the best crystallography evidence for each geometry and the
        last orientation result for each canonical orientation key. Publishing,
        version checks, search indexing, and recovery belong to RunService.
        """
        self._bind_running_run(run_id)
        if self._memory_facts is not None:
            return [fact.model_copy(deep=True) for fact in self._memory_facts]
        selected: dict[str, dict[str, Any]] = {}
        for evidence in sorted(
            self._crystallography_evidence,
            key=lambda item: (
                _GB_CRYSTALLOGRAPHY_TOOL_PRIORITY[str(item["name"])],
                int(item["sequence"]),
            ),
        ):
            payload = evidence["payload"]
            geometry = payload["input"]
            key = gb_crystallography_lookup_key(
                basis=payload["basis"], axis=payload["axis"],
                plane1=geometry["plane1"], plane2=geometry["plane2"],
            )
            selected[key] = evidence
        orientation_facts: dict[str, FactRecord] = {}
        # Reused identical orientation calls have the same scientific payload.
        # Compute it once, while retaining the final artifact as its provenance.
        orientation_payloads: dict[str, dict[str, Any] | None] = {}
        for evidence in self._orientation_evidence:
            fingerprint = csl_tool_input_fingerprint(
                "get_tilt_gb_orientation", evidence["input"],
            )
            if fingerprint not in orientation_payloads:
                orientation_payloads[fingerprint] = self._orientation_payload(evidence["input"])
            payload = orientation_payloads[fingerprint]
            if payload is None:
                continue
            fact = self._build_orientation_fact(run_id, evidence, payload)
            orientation_facts[fact.lookup_key] = fact
        crystallography_facts = [
            self._build_crystallography_fact(run_id, evidence)
            for evidence in selected.values()
        ]
        self._memory_facts = [*orientation_facts.values(), *crystallography_facts]
        return [fact.model_copy(deep=True) for fact in self._memory_facts]

    def _build_crystallography_fact(
        self, run_id: str, evidence: dict[str, Any],
    ) -> FactRecord:
        name = str(evidence["name"])
        payload = dict(evidence["payload"])
        input_geometry = dict(payload["input"])
        basis = str(payload["basis"]).strip().lower()
        axis = [int(value) for value in payload["axis"]]
        plane1 = [int(value) for value in input_geometry["plane1"]]
        plane2 = [int(value) for value in input_geometry["plane2"]]
        selected = _compact_csl_reference(payload.get("selected_reference"))
        exact_match = bool(
            selected is not None
            and selected.get("classification") == "exact_csl_match"
        )
        lookup_key = gb_crystallography_lookup_key(
            basis=basis,
            axis=axis,
            plane1=plane1,
            plane2=plane2,
        )
        candidate_count = (
            len(payload.get("candidates") or [])
            if name == "match_csl_tilt_references"
            else int(payload.get("scored_pair_count") or 0)
        )
        value = {
            "fact_scope": "descriptive_general_gb_crystallography",
            "basis": basis,
            "actual_boundary": {
                "ordered_plane1": plane1,
                "ordered_plane2": plane2,
                "tilt_axis": axis,
                "geometry_classification": "pure_tilt_geometry",
                "misorientation_degrees": input_geometry.get(
                    "misorientation_degrees"
                ),
                "oriented_normal_angle_degrees": input_geometry.get(
                    "oriented_normal_angle_degrees"
                ),
            },
            "csl_assessment": {
                "search_classification": payload.get("search_classification"),
                "exact_sigma_assigned": (
                    selected.get("sigma") if exact_match and selected else None
                ),
                "exact_rotation_assigned": (
                    {
                        "sigma": selected.get("sigma"),
                        "rotation_m": selected.get("rotation_m"),
                        "rotation_n": selected.get("rotation_n"),
                        "theta_degrees": selected.get("theta_degrees"),
                    }
                    if exact_match and selected
                    else None
                ),
                "selected_reference": selected,
                "selected_reference_is_descriptive_only": bool(
                    selected is not None and not exact_match
                ),
                "criteria": payload.get("criteria") or {},
                "candidate_count": candidate_count,
                "candidate_set_exhaustive_within_reported_bounds": bool(
                    name == "match_csl_tilt_references"
                ),
            },
            "downstream_policy": {
                "informational_only": True,
                "authoritative_for_structure_construction": False,
                "requires_user_or_task_confirmation_for_reuse": True,
                "near_csl_reference_must_not_replace_actual_boundary": True,
                "must_not_supply_orientations_periods_or_repeat_counts": True,
            },
            "provenance": {
                "source_run_id": run_id,
                "source_tool": name,
                "artifact_id": evidence["artifact_id"],
                "artifact_path": evidence["artifact_path"],
                "tool_sequence": evidence["sequence"],
            },
        }
        timestamp = utc_now_iso()
        return FactRecord(
            fact_id=str(uuid.uuid5(uuid.NAMESPACE_URL, lookup_key)),
            kind="gb_crystallography",
            lookup_key=lookup_key,
            value=value,
            source_run_id=run_id,
            evidence_kind=FactEvidenceKind.DETERMINISTIC_WORKFLOW,
            created_at=timestamp,
            updated_at=timestamp,
        )

    @staticmethod
    def _orientation_payload(
        input_payload: dict[str, Any] | str | None,
    ) -> dict[str, Any] | None:
        if isinstance(input_payload, str):
            try:
                values = json.loads(input_payload)
            except json.JSONDecodeError:
                return None
        else:
            values = dict(input_payload or {})
        try:
            axis = csl.CommonDivisor(
                np.array(
                    [int(values["u"]), int(values["v"]), int(values["w"])],
                    dtype=int,
                )
            )[0]
            basis = str(values["basis"]).strip().lower()
            sigma = int(values["sigma"])
            plane = [int(value) for value in values["gb_plane"]]
            return csl.tilt_gb_orthogonal_orientation(
                axis,
                basis,
                sigma,
                plane,
                a0=float(values.get("a0", 2.8553)),
                rotation_m=values.get("rotation_m"),
                rotation_n=values.get("rotation_n"),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _build_orientation_fact(
        run_id: str,
        evidence: dict[str, Any],
        payload: dict[str, Any],
    ) -> FactRecord:
        values = evidence["input"]
        lookup_key = csl_orientation_lookup_key(
            basis=values["basis"],
            axis=[values["u"], values["v"], values["w"]],
            sigma=values["sigma"],
            plane=values["gb_plane"],
            rotation_m=payload["m"],
            rotation_n=payload["n"],
        )
        now = utc_now_iso()
        return FactRecord(
            fact_id=str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"csl_orientation:{lookup_key}")
            ),
            kind="csl_orientation",
            lookup_key=lookup_key,
            value={**payload, "provenance": {
                "source_run_id": run_id,
                "source_tool": "get_tilt_gb_orientation",
                "artifact_id": evidence["artifact_id"],
                "artifact_path": evidence["artifact_path"],
                "tool_sequence": evidence["sequence"],
            }},
            source_run_id=run_id,
            evidence_kind=FactEvidenceKind.DETERMINISTIC_WORKFLOW,
            created_at=now,
            updated_at=now,
        )
