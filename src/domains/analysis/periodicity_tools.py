"""Project-owned GB period candidates, construction comparison and memory evidence.

OVITO executes in a child GBclaw process. Construction metadata names measured
directions and evaluates possible PBC sizes; it never supplies a target period.
"""
from __future__ import annotations

from dataclasses import asdict
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from langchain_core.tools import tool

from domains.analysis.periodicity_artifacts import (
    ARTIFACTS as _ARTIFACTS,
    MULTISCALE_FILES,
    MULTISCALE_STATUS_FILE,
)
from core.paths import SRC_ROOT
from core.execution.process import run_cancellable
from core.util.graph_config import runtime_project_id
from core.util.io import write_json_atomic
from core.settings.env import get_env_int
from extensions import tool_memory_spec
from project_memory.models import FactRecord, RunRecord, RunSourceRecord, RunStatus, Workflow
from project_workspace.artifacts.models import ProvenanceClass
from project_workspace.store import ProjectStore
from project_memory.publication.facts import make_run_fact
from project_memory.publication.results import RunToolResult


PERIODICITY_TIMEOUT_SEC = get_env_int("GBCLAW_PERIODICITY_TIMEOUT_SEC", 1200, minimum=30)
LONGER_PERIOD_SIZE_HINT = (
    "No longer period length was obtained; the GB may be too short along x (the boundary "
    "direction) to show one. A longer x extent is needed to look for it."
)


class _JointAnalysisIncomplete(ValueError):
    """The joint structure/strain stage reported a scientific limit, not a crash."""

    def __init__(self, status: str | None, reason: str | None):
        self.status, self.reason = status, reason
        super().__init__(f"GB periodicity joint analysis could not complete: {reason or status}")
def _write_json(path: Path, value: Any) -> None:
    write_json_atomic(path, value, sort_keys=False, allow_nan=False)


def _public_paths(value: Any, output_dir: Path, project_root: Path) -> Any:
    """Translate generated absolute paths without touching scientific strings."""
    if isinstance(value, dict):
        return {key: _public_paths(item, output_dir, project_root) for key, item in value.items()}
    if isinstance(value, list):
        return [_public_paths(item, output_dir, project_root) for item in value]
    if isinstance(value, str) and value.startswith(str(output_dir) + os.sep):
        path = Path(value)
        try:
            return path.relative_to(project_root).as_posix()
        except ValueError:
            return value
    return value


def _candidate_references(compact: dict, *, analysis_run_id: str, source_run_id: str,
                          source_artifact_id: str, detail_path: str) -> None:
    """Attach persistent recall handles to the displayed scientific conclusions."""
    records = [compact.get("review_priority_candidate"), *compact.get("alternative_candidates", []),
               *compact.get("short_motif_candidates", []), *compact.get("longer_candidates", []),
               *compact.get("candidates", [])]
    known_ids = {item["id"] for item in compact.get("candidates", []) if item.get("id")}
    for candidate in records:
        if not isinstance(candidate, dict):
            continue
        ident = _candidate_id(candidate)
        suffix = f"candidate:{ident}" if ident in known_ids else "main_conclusion"
        candidate.update(lookup_key=f"run:{analysis_run_id}:{suffix}", source_run_id=source_run_id,
                         source_artifact_id=source_artifact_id, detailed_report_path=detail_path,
                         decision_status="candidate_not_selected")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _recipe_digest() -> str:
    root = Path(__file__).resolve().parent
    paths = [Path(__file__).resolve(), root/"periodicity_artifacts.py",
             *sorted((root/"periodicity").rglob("*.py")),
             root.parent/"lammps"/"common"/"geometry.py"]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(root.parent).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _size_policy() -> dict:
    from domains.lammps.common import defaults
    return {key:getattr(defaults,key) for key in (
        "X_MISMATCH_MAX_PERCENT", "TARGET_X_NM", "MIN_Y_PER_GRAIN_NM", "MIN_Z_NM",
        "BOX_DIM_FACTOR", "BOX_VOLUME_FACTOR", "TARGET_BOX_NM", "PBC_X_PADDING_EPS_FACTOR", "X_MATCH_CANDIDATE_LIMIT",
    )}


def _run_periodicity_subprocess(cfg_path: Path, output_dir: Path, metadata_path: Path, policy_path: Path):
    source_root = SRC_ROOT
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(source_root), env.get("PYTHONPATH", "")])
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["MPLBACKEND"] = "Agg"
    env["MPLCONFIGDIR"] = str(output_dir/".mpl_cache")
    env.setdefault("OMP_NUM_THREADS", "4")
    return run_cancellable(
        [sys.executable, "-m", "domains.analysis.periodicity", str(cfg_path),
         "--output", str(output_dir), "--metadata", str(metadata_path), "--policy", str(policy_path)],
        env=env, timeout=PERIODICITY_TIMEOUT_SEC, label="GB periodicity analysis",
    )


def build_pbc_size_comparison(report: dict, canonical: dict, *, target_x_nm: float | None = None) -> dict:
    """Read-only comparison using exactly the constructor's sizing equations.

    Measured primitive counts must first be mapped to the validated original
    orientation convention; that convention may use nonprimitive Miller vectors.
    """
    from domains.lammps.common.geometry import gb_box_geometry, box_size_violations
    from domains.analysis.periodicity.crystal_mapping import is_allowed_translation_count

    top, bottom = canonical["grains"]["top"], canonical["grains"]["bottom"]
    sim, material = canonical["simulation"], canonical["material"]
    common = {"top_x":top["orientation"]["x"], "top_y":top["orientation"]["y"], "top_z":top["orientation"]["z"],
              "bottom_x":bottom["orientation"]["x"], "bottom_y":bottom["orientation"]["y"], "bottom_z":bottom["orientation"]["z"],
              "a0":sim["a0"], "lattice":material["lattice"], "y_vacuum":sim["y_vacuum"],
              "y_eps":sim["y_eps"], "x_pbc":True, "target_x_nm":target_x_nm}
    reference = gb_box_geometry(**common)
    options = []
    excluded = []
    seen = {}
    for candidate in report.get("candidates", []):
        check = candidate.get("lattice_repeat_check", {})
        if not check.get("eligible"):
            excluded.append({"candidate_id":candidate["id"], "reason":check.get("status", "unresolved")})
            continue
        # Use the pair validated at the final coordinate translation, not an
        # earlier Pareto seed that may have refined to a different minimum.
        checked_grains = check["grains"]
        if not all(checked_grains[side]["direction_basis"] == "validated_source_direction"
                   and checked_grains[side]["direction"] == grain["orientation"]["x"]
                   for side, grain in (("upper", top), ("lower", bottom))):
            excluded.append({"candidate_id":candidate["id"], "reason":"source_direction_basis_unresolved"})
            continue
        counts = check["nominal_counts"]
        if not all(is_allowed_translation_count(material["lattice"], grain["orientation"]["x"], counts[side])
                   for side, grain in (("upper", top), ("lower", bottom))):
            excluded.append({"candidate_id":candidate["id"], "reason":"source_lattice_translation_incompatible"})
            continue
        lengths = [counts[s] * checked_grains[s]["full_vector_length_angstrom"] for s in ("upper", "lower")]
        pair_mismatch = abs(lengths[0]-lengths[1]) / ((lengths[0]+lengths[1])/2)
        nx_top, nx_bottom = float(counts["upper"]), float(counts["lower"])
        if not all(math.isfinite(x) and x > 0 for x in (nx_top,nx_bottom)):
            continue
        key = (nx_top,nx_bottom)
        if key in seen:
            seen[key]["candidate_ids"].append(candidate["id"])
            seen[key]["roles"] = sorted(set(seen[key]["roles"] + candidate.get("roles", [])))
            seen[key]["measured_evidence"].append({
                "candidate_id":candidate["id"], "roles":candidate.get("roles", []),
                "x_translation_angstrom":candidate["x_translation_angstrom"],
                "pair_mismatch_percent":100*pair_mismatch,
            })
            continue
        geometry = gb_box_geometry(**common, top_size=[nx_top,*top["size"][1:]], bottom_size=[nx_bottom,*bottom["size"][1:]])
        item = {
            "candidate_ids":[candidate["id"]], "roles":candidate.get("roles", []),
            "measured_x_translation_angstrom":candidate["x_translation_angstrom"],
            "measured_pair_mismatch_percent":100*pair_mismatch,
            "measured_evidence":[{
                "candidate_id":candidate["id"], "roles":candidate.get("roles", []),
                "x_translation_angstrom":candidate["x_translation_angstrom"],
                "pair_mismatch_percent":100*pair_mismatch,
            }],
            "top_size":geometry["resolved_top_size"], "bottom_size":geometry["resolved_bottom_size"],
            "top_orientation":top["orientation"], "bottom_orientation":bottom["orientation"],
            "nominal_top_lx_angstrom":geometry["lx_top_angstrom"],
            "nominal_bottom_lx_angstrom":geometry["lx_bot_angstrom"],
            "nominal_pbc_box_lx_angstrom":geometry["lx_box_angstrom"],
            "construction_mismatch_percent":geometry["x_mismatch_percent"],
            "approx_atoms":geometry["approx_atoms"],
            "agent_size_policy_violations":box_size_violations(geometry),
            "fractional_x_repeats":not nx_top.is_integer() or not nx_bottom.is_integer(),
            "status":"candidate_not_selected",
            "lattice_repeat_status":"compatible",
        }
        options.append(item); seen[key] = item
    return {
        "status":"candidates_compared" if options else "no_validated_measured_direction_counts",
        "selected_option":None, "requires_user_decision":True, "x_pbc":True,
        "measured_pair_mismatch_definition":"100*abs(L_top-L_bottom)/mean(L_top,L_bottom), using relaxed bulk translations",
        "construction_mismatch_definition":"100*abs(L_top-L_bottom)/max(L_top,L_bottom), using original a0 and orientation vectors",
        "source_material":material, "source_a0_angstrom":sim["a0"],
        "size_policy":_size_policy(), "options":options,
        "excluded_candidates":excluded,
        "construction_only_x_match_candidates":reference["x_match_candidates"],
        "construction_only_recommended_top_size":reference["recommended_top_size"],
        "construction_only_recommended_bottom_size":reference["recommended_bottom_size"],
        "notes":[
            "Measured motifs and construction-only rational matches are separate evidence; constructor candidates are not measured structural repeats.",
            "Candidate options retain source ny/nz; review listed size-policy violations and periodic seams before choosing a new model.",
            "Only validated FCC/BCC pure-x lattice-repeat coefficients enter measured options; measured feature lengths are not arbitrary construction counts.",
            "No model was rebuilt, strained, or selected. Allowed fractional repeats are preserved for explicit user review.",
        ],
    }


def gb_periodicity_facts(run: RunRecord, result: dict[str, Any]) -> list[FactRecord]:
    """Publish the analysis and its displayed candidates without making a decision."""
    candidates = result.get("candidates") or []
    facts = [make_run_fact(run, {
        **gb_periodicity_model_result(result),
        "conclusion_type":"gb_periodicity_candidates", "selected_candidate_id":None,
        "requires_user_decision":True,
        "candidate_lookup_keys":[f"run:{run.run_id}:candidate:{c['id']}" for c in candidates],
        "parameters":{key:value for key,value in run.parameters.items()
                      if key in ("purpose", "source_artifact_id", "source_config_artifact_id", "analysis_identity", "policy")},
    })]
    reviewed = {_candidate_id(c): c for c in [result.get("review_priority_candidate"),
                 *(result.get("alternative_candidates") or [])] if isinstance(c, dict)}
    for candidate in candidates:
        closure = candidate.get("lattice_repeat_check", {}).get("status", "unresolved")
        facts.append(make_run_fact(run, {
            **candidate, "conclusion_type":"gb_periodicity_candidate",
            "multiscale_review":reviewed.get(candidate["id"]),
            "short_motif_associations":[note for note in result.get("short_motif_candidates", [])
                                         if _candidate_id(note) == candidate["id"]],
            "long_period_associations":[note for note in result.get("longer_candidates", [])
                                         if _candidate_id(note) == candidate["id"]],
            "purpose":result.get("purpose"),
            "source_artifact_id":result.get("source_artifact_id"),
            "summary":f"Observed GB scale {candidate.get('x_translation_angstrom')} Angstrom; candidate {candidate['id']}; pure-x lattice closure {closure}; user has not selected it",
            "decision_status":"candidate_not_selected", "data":result.get("data"),
            "pbc_size_options":[option for option in result.get("pbc_size_comparison", {}).get("options", [])
                                if candidate["id"] in option.get("candidate_ids", [])],
            "pbc_size_comparison_path":result.get("pbc_size_comparison_path"),
            "detailed_report_path":result.get("detailed_report_path"),
            "candidate_table_path":result.get("candidate_table_path"),
            "summary_image_path":result.get("summary_image_path"),
            "analysis_lookup_key":f"run:{run.run_id}:main_conclusion",
        }, suffix=f"candidate:{candidate['id']}"))
    return facts


def _result_response(run, artifacts, *, reused=False, requested_purpose=None):
    # Every artifact remains indexed in the run manifest. The normal handoff
    # needs the verified summary and conclusions, not the diagnostic inventory.
    presentation_roles = {"gb_periodicity_overview", "gb_periodicity_summary"} if run.status is RunStatus.COMPLETED else {"log"}
    return RunToolResult(
        run_id=run.run_id, workflow=run.workflow, status=run.status, manifest=run.manifest_path,
        artifacts=[{"role":a.role,"path":a.path} for a in artifacts if a.role in presentation_roles],
        result={**gb_periodicity_model_result(run.result), "reused":reused,
                "artifact_count":len(artifacts), "presentation_policy":"summary_only",
                **({"requested_purpose":requested_purpose} if requested_purpose else {})},
        error=run.error or "",
    ).to_json()


def _candidate_id(candidate: dict) -> str | None:
    return candidate.get("candidate_id") or candidate.get("id")


def _compact_review_candidate(candidate: dict | None) -> dict | None:
    if not isinstance(candidate, dict):
        return None
    keys = ("id", "candidate_id", "translation_nm", "period_nm", "row_period_nm", "coordinate_period_nm",
            "display_lattice_label", "kind", "reason", "highlight_score", "evidence_scores", "observable_cycles",
            "near_rms_over_nn", "defect_rms_over_nn",
            "association_kind", "association_difference_angstrom", "association_relative_difference",
            "lattice_repeat_status", "pure_x_repeat_eligible", "nominal_counts",
            "lookup_key")
    return {key: candidate[key] for key in keys if candidate.get(key) is not None}


def gb_periodicity_model_result(result: dict) -> dict:
    """Return the main figure, review priority and a bounded set of alternatives.

    All candidate rows, FFT channels, local events and plots remain indexed on
    disk and in per-candidate facts. A display priority is never a user selection.
    """
    keep = ("summary", "status", "analysis_status", "source_run_id", "source_artifact_id",
            "purpose", "requires_user_decision", "selected_candidate_id", "warnings",
            "limitations", "summary_image_path", "image_paths", "presentation_policy",
            "data", "summary_data", "pbc_size_comparison_path", "reused", "requested_purpose",
            "multiscale_analysis_status", "multiscale_analysis_reason", "total_candidate_count",
            "detailed_report_path", "candidate_table_path",
            "priority_reason", "presentation_guidance", "short_motif_candidate_count", "longer_candidate_count", "long_band_observability", "lattice_filter",
            "required_action", "retry_without_changes", "artifact_count",
            "conclusion_lookup_key", "conclusion_fact_id", "memory_publication")
    compact = {key: result[key] for key in keep if key in result}
    if result.get("analysis_status") == "failed":
        return compact
    compact["review_priority_candidate"] = _compact_review_candidate(result.get("review_priority_candidate"))
    compact["alternative_candidates"] = [_compact_review_candidate(c) for c in result.get("alternative_candidates", [])[:4]]
    compact["short_motif_candidates"] = [_compact_review_candidate(c) for c in result.get("short_motif_candidates", [])[:4]]
    compact["longer_candidates"] = [_compact_review_candidate(c) for c in result.get("longer_candidates", [])[:4]]
    compact["total_candidate_count"] = result.get("total_candidate_count", len(result.get("candidates", [])))
    display_ids = {_candidate_id(c) for c in [compact["review_priority_candidate"], *compact["alternative_candidates"]]
                   if isinstance(c, dict)}
    comparison = result.get("pbc_size_comparison") or {}
    compact["pbc_size_comparison"] = {
        **{key: comparison[key] for key in ("status", "requires_user_decision", "selected_option",
             "construction_only_recommended_top_size", "construction_only_recommended_bottom_size", "notes") if key in comparison},
        "options": [{key: option[key] for key in (
            "candidate_ids", "status", "top_size", "bottom_size", "construction_mismatch_percent",
            "measured_pair_mismatch_percent", "agent_size_policy_violations", "nominal_pbc_box_lx_angstrom") if key in option}
            for option in comparison.get("options", []) if display_ids.intersection(option.get("candidate_ids", []))][:8],
        "total_option_count":comparison.get("total_option_count", len(comparison.get("options", []))),
    }
    compact["selected_candidate_id"] = None
    compact["requires_user_decision"] = True
    return compact


@tool
def analyze_gb_periodicity(
    source_run_id: str,
    source_artifact_role: str = "minimized_cfg",
    source_artifact_id: str | None = None,
    purpose: str = "Compare relaxed GB periodicity and mismatch to choose the x size of a later PBC model",
    policy_overrides: dict[str, Any] | None = None,
    force_recompute: bool = False,
) -> str:
    """Analyze short/long period candidates of a completed X-vacuum gb_minimize.

    Default follow-up for successful gb_minimize with simulation.x_pbc=false and
    positive x-side vacuum padding. This is not the LAMMPS boundary='p p p' flag.
    The tool reads the exact indexed minimized_cfg and authoritative construction
    config, checks physical vacuum, runs full-system PTM before filtering, and
    evaluates layered correlations and xyz matches without any expected repeat
    ratio. Original orientation labels and a0 permit a separate construction
    mismatch/size comparison for PBC choices; no structure is rebuilt or chosen.

    Lead with summary_image_path: multiscale_review/multiscale_summary.png,
    the full joint structure/elastic-strain summary
    with short/long candidates and crystal direction ratios, keeping PTM x/y
    scales equal. Explain review_priority_candidate and alternative_candidates
    (at most four alternatives); review priority is not a selected physical
    period or an identified dislocation. Row motifs retain their measured
    length even if pure_x_repeat_eligible=false. Only lattice-compatible pure-x
    candidates enter the ranked alternatives and PBC size comparison; lattice_filter
    reports exclusions. For reduced [uvw], FCC allows half-integer steps when
    u+v+w is even; BCC when all indices are odd; otherwise integer steps.
    Nonprimitive direction coefficients must account for their gcd.
    Nearby row motifs retain their measured
    length separately from the associated lattice/coordinate repeat. image_paths
    contains only this overview. The returned conclusions suffice for a normal
    answer: do not reread numerical artifacts merely to reconstruct or verify
    the summary. Complete JSON/curves, source metadata and diagnostics stay
    indexed in the manifest. Read a specific artifact only for a concrete
    question not answered by the summary. Show additional plots only on request.
    If joint analysis cannot complete, report its reason and saved diagnostics;
    no coordinate-only summary substitutes for the complete workflow. Do not
    retry an unchanged deterministic failure or browse all partial data to
    rebuild its missing conclusion.
    Small inputs may return insufficient
    domain, which is not absence of long periodicity. Candidate decisions stay
    with the user. Matching completed analyses are reused by source, configuration,
    policy and recipe fingerprints unless force_recompute=true.
    Stored workflow is gb_periodicity (query entity=runs), not the tool name.
    Delegate task_state records do not prove this analysis is already running.

    policy_overrides optionally changes the recorded CandidatePolicy fields, e.g.
    shortlist_pair_mismatch, minimum_repeat_cycles, minimum_long_scale_ratio or
    x_window. It never accepts an expected ratio or expected period.
    """
    # Reuse project identity and manifest verification without importing OVITO
    # into the server, or duplicating the existing artifact access rules.
    from domains.analysis.common import verified_artifact_path
    from domains.analysis.periodicity import CandidatePolicy
    from domains.lammps.minimize.config import load_gb_minimize_config, config_to_dict

    project_id = runtime_project_id()
    if not project_id:
        return "No active project_id in run config. Periodicity analysis must belong to a project."
    if not source_run_id.strip() or not purpose.strip():
        return "source_run_id and purpose must not be empty"
    store = ProjectStore(); service = store.runs(project_id)
    run = None; artifacts = []; lock = None; out = None
    try:
        policy = CandidatePolicy(**(policy_overrides or {})); policy.validate()
        policy_payload = asdict(policy)
        source_run = service.memory.get_run(source_run_id)
        if source_run.workflow is not Workflow.GB_MINIMIZE or source_run.status is not RunStatus.COMPLETED:
            raise ValueError("source_run_id must identify a completed gb_minimize run")
        matches = service.memory.query_artifacts(run_id=source_run_id, role=source_artifact_role)
        if source_artifact_role != "minimized_cfg":
            raise ValueError("This workflow analyzes the final minimized_cfg, not an initial or loading frame")
        if source_artifact_id:
            matches = [a for a in matches if a.artifact_id == source_artifact_id]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one minimized CFG; found {len(matches)}")
        selected = matches[0]
        cfg_path = verified_artifact_path(store=store,service=service,project_id=project_id,run_id=source_run_id,artifact=selected)
        configs = service.memory.query_artifacts(run_id=source_run_id,role="config")
        if len(configs) != 1:
            raise ValueError("The source must have one indexed authoritative construction config")
        config_path = verified_artifact_path(store=store,service=service,project_id=project_id,run_id=source_run_id,artifact=configs[0])
        canonical = config_to_dict(load_gb_minimize_config(config_path))
        sim = canonical["simulation"]
        if sim["x_pbc"] is not False or not math.isfinite(sim["y_vacuum"]) or sim["y_vacuum"] <= 0:
            raise ValueError("Automatic GB periodicity analysis requires x_pbc=false and positive x-side vacuum padding")
        source_sha = hashlib.sha256(cfg_path.read_bytes()).hexdigest()
        config_sha = hashlib.sha256(config_path.read_bytes()).hexdigest()
        fingerprint = "sha256:"+source_sha
        identity = _digest({"source":source_sha,"config":config_sha,"policy":policy_payload,"recipe":_recipe_digest(),"size_policy":_size_policy()})
        source_metadata = {
            "source_run_id":source_run_id, "source_artifact_id":selected.artifact_id,
            "source_artifact_role":selected.role, "source_path":selected.path,
            "source_sha256":source_sha, "source_config_artifact_id":configs[0].artifact_id,
            "construction_config_sha256":config_sha, "canonical_config":canonical,
            "purpose":purpose, "analysis_identity":identity,
            "source_convergence":{key:source_run.result.get(key) for key in (
                "all_minimizations_converged","final_minimization_converged","warnings")},
            "metadata_basis":"registered canonical construction config; not a repeat-count prior",
        }
        # Avoid duplicate simultaneous analyses, including different server workers.
        lock_root = Path(tempfile.gettempdir())/"gbclaw-periodicity-locks"
        lock_root.mkdir(exist_ok=True)
        lock = (lock_root/(_digest([str(store.project_path(project_id)),identity])+".lock")).open("a")
        try:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return "An identical GB periodicity analysis is already running; query its run before retrying."
        if not force_recompute:
            previous = service.memory.query_runs(workflow=Workflow.GB_PERIODICITY,source_run_id=source_run_id,source_fingerprint=fingerprint,limit=100)
            for item in previous:
                if item.parameters.get("analysis_identity") != identity or item.status not in (RunStatus.COMPLETED,RunStatus.RUNNING):
                    continue
                indexed = service.memory.query_artifacts(run_id=item.run_id)
                if item.status is RunStatus.COMPLETED:
                    required = set(tool_memory_spec("analyze_gb_periodicity").required_artifact_roles)
                    if not required.issubset({a.role for a in indexed}):
                        continue
                    try:
                        for a in indexed:
                            if a.provenance_class is ProvenanceClass.DEBUG:
                                continue
                            path = verified_artifact_path(store=store,service=service,project_id=project_id,run_id=item.run_id,artifact=a)
                            if a.metadata.get("sha256") and hashlib.sha256(path.read_bytes()).hexdigest() != a.metadata["sha256"]:
                                raise ValueError("Cached analysis artifact content changed")
                    except (ValueError,FileNotFoundError):
                        continue
                return _result_response(item,indexed,reused=True,requested_purpose=purpose)
        run = service.create_tool_run("analyze_gb_periodicity",source_run_id=source_run_id,source_fingerprint=fingerprint,
            sources=[RunSourceRecord(source_run_id=source_run_id,relation="relaxed_gb_structure",ordinal=0,
                                     source_fingerprint=fingerprint,metadata=source_metadata)],
            parameters={"purpose":purpose,"source_artifact_id":selected.artifact_id,"source_config_artifact_id":configs[0].artifact_id,
                        "analysis_identity":identity,"policy":policy_payload,"source_metadata":source_metadata})
        out = service.artifacts.run_directory(run.run_id)
        metadata_path, policy_path = out/"source_metadata.json",out/"analysis_policy.json"
        _write_json(metadata_path,source_metadata); _write_json(policy_path,policy_payload)
        for role,path in (("source_metadata_snapshot",metadata_path),("analysis_policy",policy_path)):
            artifacts.append(service.register_artifact(run.run_id,role,path.name,ProvenanceClass.PRIMARY,media_type="application/json",
                metadata={"source_run_id":source_run_id,"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}))
        completed = _run_periodicity_subprocess(cfg_path,out,metadata_path,policy_path)
        (out/"analysis.log").write_text((completed.stdout or "")+"\n"+(completed.stderr or ""),encoding="utf-8")
        artifacts.append(service.register_artifact(run.run_id,"log","analysis.log",ProvenanceClass.DEBUG,media_type="text/plain"))
        report_path, summary_path = out/"period_candidates.json",out/"agent_summary.json"
        if completed.returncode != 0:
            status_path = out/MULTISCALE_STATUS_FILE
            if status_path.is_file():
                failure = json.loads(status_path.read_text())
                if failure.get("status") == "failed":
                    raise ValueError(f"GB periodicity failed at {failure.get('stage')}: {failure.get('reason')}")
            raise ValueError(f"GB periodicity subprocess failed ({completed.returncode}): {(completed.stderr or completed.stdout or 'no diagnostic')[-1500:]}")
        report, compact = json.loads(report_path.read_text()),json.loads(summary_path.read_text())
        if json.loads(metadata_path.read_text()) != source_metadata:
            raise ValueError("The analysis changed its authoritative source metadata")
        # Core CLI may serialize the same snapshot with different whitespace or
        # Unicode escaping. Preserve the exact bytes registered before execution.
        _write_json(metadata_path,source_metadata)
        if not isinstance(report,dict) or not isinstance(compact,dict):
            raise ValueError("Periodicity diagnostics must be JSON objects")
        if compact.get("multiscale_analysis_status") != "completed":
            raise _JointAnalysisIncomplete(
                compact.get("multiscale_analysis_status"),
                compact.get("multiscale_analysis_reason") or report.get("reason") or report.get("status"))
        if not (out/MULTISCALE_FILES["summary_image"]).is_file():
            raise ValueError("Completed multiscale analysis did not produce its summary image")
        if hashlib.sha256(cfg_path.read_bytes()).hexdigest() != source_sha or hashlib.sha256(config_path.read_bytes()).hexdigest() != config_sha:
            raise ValueError("Source structure/config changed during analysis; results cannot be published against the previous fingerprint")
        if report.get("selected_candidate_id") is not None or report.get("expected_repeat_ratio_used") is not False:
            raise ValueError("Periodicity output violated the candidate-only, no-reference-ratio contract")
        comparison = build_pbc_size_comparison(report,canonical,target_x_nm=source_run.parameters.get("sizing_target_x_nm"))
        comparison.update(source_run_id=source_run_id,analysis_run_id=run.run_id)
        _write_json(out/"pbc_size_comparison.json",comparison)
        # Rewrite public artifact paths to indexed project-relative paths. The
        # numerical report retains exact source/cache paths as provenance only.
        root = store.project_path(project_id)
        specs = _ARTIFACTS
        paths = {role:(out/filename).relative_to(root).as_posix() for role,filename,_ in specs if (out/filename).is_file()}
        summary_image = paths["gb_periodicity_overview"]
        diagnostics = [paths[role] for role,_,media in specs
                       if media == "image/png" and role != "gb_periodicity_overview" and role in paths]
        compact = _public_paths(compact, out, root)
        detail_path = paths["gb_periodicity_multiscale_data"]
        _candidate_references(compact, analysis_run_id=run.run_id, source_run_id=source_run_id,
                              source_artifact_id=selected.artifact_id, detail_path=detail_path)
        overview = {**compact.get("overview", {}), "image_path":summary_image}
        compact.update(source_run_id=source_run_id,source_artifact_id=selected.artifact_id,source_metadata=source_metadata,
                       purpose=purpose,analysis_status=report.get("status"),selected_candidate_id=None,requires_user_decision=True,
                       artifacts=paths,summary_image_path=summary_image,image_paths=[summary_image],presentation_policy="summary_only",
                       diagnostic_image_paths=diagnostics,overview=overview,pbc_size_comparison=comparison,
                       detailed_report_path=detail_path,candidate_table_path=paths.get("gb_periodicity_candidate_table"),
                       conclusion_lookup_key=f"run:{run.run_id}:main_conclusion")
        report.update(source_run_id=source_run_id,analysis_run_id=run.run_id,pbc_size_comparison=comparison,
                      summary_image_path=summary_image,overview=overview,artifacts=paths,artifact_paths=paths)
        _write_json(report_path,report); _write_json(summary_path,compact)
        for role,filename,media in specs:
            if (out/filename).is_file():
                artifacts.append(service.register_artifact(run.run_id,role,filename,ProvenanceClass.DERIVED,media_type=media,
                    metadata={"source_run_id":source_run_id,"source_artifact_id":selected.artifact_id,"analysis_status":report.get("status"),
                              "sha256":hashlib.sha256((out/filename).read_bytes()).hexdigest()}))
        if compact.get("longer_candidate_count") == 0:
            compact["warnings"] = [*(compact.get("warnings") or []), LONGER_PERIOD_SIZE_HINT]
        priority = compact.get("review_priority_candidate")
        priority_text = (f"Review priority {_candidate_id(priority)}: "
                         f"{priority.get('display_lattice_label', 'see candidate evidence')}; "
                         if isinstance(priority, dict) else "No supported joint review priority; ")
        result = {
            **compact, "data":paths["gb_periodicity_data"], "summary_data":paths["gb_periodicity_summary"],
            "pbc_size_comparison_path":paths["pbc_size_comparison"],
            "summary":f"GB periodicity: {report.get('status')}; joint structure/strain analysis={compact.get('multiscale_analysis_status', 'unavailable')}. "
                      + priority_text
                      + f"{len(compact.get('alternative_candidates', []))} alternatives displayed; "
                      + f"{compact.get('total_candidate_count', len(compact.get('candidates', [])))} coordinate candidates retained. "
                      + f"{compact.get('lattice_filter', {}).get('compatible_count', 0)} pass the two-grain pure-x lattice check. "
                      + "Show only summary_image_path by default, with the review priority and alternatives. "
                      + "These conclusions suffice for a normal answer. Read indexed artifacts only for a concrete unanswered question. PBC x size remains for user selection.",
        }
        terminal = service.complete_run(run.run_id,result=result,memory_tool="analyze_gb_periodicity")
        return _result_response(terminal,artifacts)
    except Exception as exc:
        if run is None:
            return str(exc)
        # Preserve available diagnostics even when numerical analysis fails.
        known = {a.role for a in artifacts}
        for role,filename,media in _ARTIFACTS:
            if out is not None and role not in known and (out/filename).is_file():
                try:
                    artifacts.append(service.register_artifact(run.run_id,role,filename,ProvenanceClass.DEBUG,media_type=media,
                        metadata={"incomplete_analysis":True,"source_run_id":source_run_id}))
                except (OSError,ValueError):
                    pass
        error = f"GB periodicity timed out after {PERIODICITY_TIMEOUT_SEC}s" if isinstance(exc,subprocess.TimeoutExpired) else str(exc)
        summary = "GB periodicity analysis did not complete; no verified summary image or final candidate conclusion is available."
        limit = {}
        if isinstance(exc, _JointAnalysisIncomplete):
            summary += " " + LONGER_PERIOD_SIZE_HINT
            limit = {"multiscale_analysis_status": exc.status, "multiscale_analysis_reason": exc.reason,
                     "warnings": [LONGER_PERIOD_SIZE_HINT]}
        terminal = service.fail_run(run.run_id,error=error[:2000],result={
            "analysis_status":"failed", "source_run_id":source_run_id,
            "summary":summary, **limit,
            "summary_image_path":None, "image_paths":[], "presentation_policy":"summary_only",
            "retry_without_changes":False,
            "required_action":"Report this failure and its run ID. Fix or change the cause before retrying; do not reconstruct the missing summary by reading all partial artifacts.",
            "presentation_guidance":"Do not display diagnostic images as a substitute summary. Partial artifacts remain indexed in the manifest for targeted investigation when needed.",
        })
        return _result_response(terminal,artifacts)
    finally:
        if lock is not None:
            lock.close()
