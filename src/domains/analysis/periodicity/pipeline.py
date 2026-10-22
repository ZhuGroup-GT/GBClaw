"""Coordinate, strain and real-space evidence in a single analysis run."""
from __future__ import annotations

import json
from pathlib import Path
import time

from .candidates import analyze_coordinates


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def analyze_periodicity(input_path, output_dir, *, source_metadata=None, cache_dir=None, policy=None):
    """Preserve detailed evidence and lead the handoff with the joint summary.

    No candidate is selected for construction. The complete multiscale workflow
    produces the only summary. Unsupported inputs retain diagnostics and an
    explicit incomplete outcome, never a substitute coordinate-only summary.
    """
    started = time.monotonic()
    out = Path(output_dir).resolve()
    report = analyze_coordinates(input_path, out, source_metadata=source_metadata,
                                 cache_dir=cache_dir, policy=policy)
    from .multiscale import analyze_multiscale
    joint = analyze_multiscale(Path(input_path).resolve(), out)
    for candidate in report.get("candidates", []):
        check = joint.get("lattice_repeat_checks", {}).get(candidate["id"])
        if check is not None:
            candidate["lattice_repeat_check"] = check
    summary_path = out / "agent_summary.json"
    summary = {
        "status": report["status"], "source": report["source"]["path"],
        "source_sha256": report["source"]["sha256"],
        "source_metadata": report.get("source_metadata", {}),
        "eligibility": report.get("eligibility", {}), "boundary": report.get("boundary"),
        "crystal_mapping": report.get("crystal_mapping"), "limitations": report.get("limitations", []),
        "expected_repeat_ratio_used": False,
    }
    primary_image = joint.get("summary_image_path")
    if joint["status"] == "completed" and (not primary_image or not Path(primary_image).is_file()):
        raise ValueError("Periodicity analysis did not produce its advertised summary image")
    artifacts = {**report.get("artifacts", {}), **joint.get("artifact_paths", {})}
    images = [primary_image] if joint["status"] == "completed" else []
    if joint["status"] == "completed":
        overview = {
            "image_path": primary_image, "layout": "structure_and_elastic_strain_multiscale",
            "review_priority_candidate": joint.get("review_priority_candidate"),
            "total_candidate_count": joint.get("total_candidate_count"),
            "selected_candidate_id": None, "requires_user_decision": True, "ptm_xy_equal_scale": True,
        }
        report["overview"] = summary["overview"] = overview
    else:
        primary_image = None
        report.update(coordinate_analysis_status=report["status"], status="analysis_unavailable", reason=joint["reason"])
        summary.update(status=report["status"], reason=joint["reason"])
    report.update(multiscale_analysis=joint, artifacts=artifacts,
                  summary_image_path=primary_image, image_paths=images,
                  elapsed_seconds=time.monotonic()-started)
    summary.update(
        multiscale_analysis_status=joint["status"],
        multiscale_analysis_reason=joint.get("reason"),
        summary_image_path=primary_image, image_paths=images,
        presentation_policy="summary_only",
        detailed_report_path=joint["detailed_report_path"],
        review_priority_candidate=joint.get("review_priority_candidate"),
        alternative_candidates=joint.get("alternative_candidates", []),
        short_motif_candidates=joint.get("short_motif_candidates", []),
        longer_candidates=joint.get("longer_candidates", []),
        total_candidate_count=joint.get("total_candidate_count", len(report.get("candidates", []))),
        selected_candidate_id=None, requires_user_decision=True,
        artifacts=artifacts, elapsed_seconds=report["elapsed_seconds"],
        presentation_guidance=(
            "Show only summary_image_path by default, with the review-priority candidate and a few alternatives. "
            "Report short motifs and longer candidates separately, retaining nearby row and coordinate lengths. "
            "Priority is a transparent review heuristic, not a probability or a chosen PBC cell. "
            "This summary suffices for a normal answer. Read a specific registered artifact only for a concrete question it does not answer."
        ),
    )
    for key in ("selection_semantics", "evidence_semantics", "priority_reason", "selection_policy",
                "short_motif_candidate_count", "longer_candidate_count", "long_band_observability", "lattice_filter"):
        if key in joint:
            summary[key] = joint[key]
    # The joint review determines the handoff. Exact coordinate records support
    # memory facts and PBC sizing; the full candidate set stays in the report.
    requested = [summary["review_priority_candidate"], *summary["alternative_candidates"],
                 *summary["short_motif_candidates"], *summary["longer_candidates"]]
    ids = {row.get("candidate_id", row.get("id")) for row in requested if isinstance(row, dict)}
    fields = ("id", "x_translation_angstrom", "roles", "geometry", "joint_xz_check", "joint_xz_defects",
              "lattice_pairs", "crystal_counts", "observable_cycles", "supporting_evidence",
              "conflicting_evidence", "limitations", "lattice_repeat_check")
    summary["candidates"] = [
        {key: candidate[key] for key in fields if key in candidate}
        for candidate in report.get("candidates", []) if candidate["id"] in ids
    ]
    if joint["status"] != "completed":
        summary["presentation_guidance"] = "Joint analysis did not complete. Report its reason and available diagnostics."
    summary["diagnostic_image_paths"] = list(dict.fromkeys(
        path for path in artifacts.values()
        if isinstance(path, str) and path.endswith(".png") and path != primary_image and Path(path).is_file()
    ))
    _write(out / "period_candidates.json", report)
    _write(summary_path, summary)
    return report
