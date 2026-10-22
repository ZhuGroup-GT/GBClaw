"""Produce detailed evidence on disk and a bounded handoff for agents."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from domains.analysis.periodicity_artifacts import (
    MULTISCALE_FILES,
    MULTISCALE_STATUS_FILE,
    OPTIONAL_MULTISCALE_FILES,
)

from .errors import MultiscaleUnavailable


_SEMANTICS = {
    "primary_output": "Present the summary image first, with the review-priority candidate and a few distinct alternatives. Read the linked detailed reports only when needed.",
    "priority": "Highest declared local-evidence review score among eligible candidates; a heuristic, not a probability or a confirmed period. If none is eligible, no priority is assigned.",
    "short_and_long": "Short atomic-row motifs, local strain intervals and full-coordinate recurrences are separate observations. A nearby lattice pair retains its own coordinate length; it does not replace the measured motif length.",
    "spectra": "Colored S/L guides are hypotheses, not inserted Fourier maxima. Actual structure and strain peaks retain review outcomes; far-field strain overlap never automatically rejects a peak.",
    "decision": "Candidates do not establish secondary dislocations or automatically select PBC dimensions. The user makes the final selection.",
}


def _compact_record(record, short_notes, long_notes):
    result = {key: record.get(key) for key in (
        "candidate_id", "translation_angstrom", "translation_nm", "display_lattice_label",
        "ratio_label", "display_direction_ratio_label", "near_rms_over_nn", "defect_rms_over_nn",
        "observable_cycles", "highlight_score", "highlight_eligible", "roles")}
    check = record["lattice_repeat_check"]
    result.update(lattice_repeat_status=check["status"], pure_x_repeat_eligible=check["eligible"],
                  nominal_counts=check["nominal_counts"])
    result["evidence_scores"] = {
        "coordinate_rows": record["row_support"]["score"],
        "local_strain": record["strain_support"]["score"],
        "coordinate_geometry": record["geometry_score"],
    }
    ident = record["candidate_id"]
    result["short_motif"] = short_notes.get(ident)
    result["long_scale"] = long_notes.get(ident)
    return result


def candidate_handoff(report):
    """Keep scientific ranking explicit and include distinct short/long alternatives."""
    annotations = report["candidate_annotations"]
    short = [{key: note.get(key) for key in (
        "candidate_id", "row_period_nm", "period_nm", "coordinate_period_nm", "association_kind",
        "association_relative_difference", "association_difference_angstrom", "display_lattice_label", "reason",
        "lattice_repeat_status", "pure_x_repeat_eligible")}
        for note in annotations["short_period_annotations"]]
    longer = [{key: note.get(key) for key in (
        "candidate_id", "period_nm", "kind", "display_lattice_label", "reason",
        "lattice_repeat_status", "pure_x_repeat_eligible")}
        for note in annotations["long_period_annotations"]]
    short_by_id = {note["candidate_id"]: note for note in short if note["candidate_id"] is not None}
    long_by_id = {note["candidate_id"]: note for note in longer if note["candidate_id"] is not None}
    ranked = sorted((r for r in annotations["records"] if r["lattice_repeat_check"]["eligible"]), key=lambda r: (
        not r["highlight_eligible"], -round(r["highlight_score"], 8), r["translation_angstrom"], r["candidate_id"]))
    priority = next((record for record in ranked if record["candidate_id"] == annotations["highlighted_id"]), None)
    chosen = []
    seen = {priority["candidate_id"]} if priority else set()

    def pair_key(record):
        check = record["lattice_repeat_check"]
        return tuple((tuple(check["grains"][side]["direction"]), check["nominal_counts"][side])
                     for side in ("upper", "lower"))

    seen_pairs = {pair_key(priority)} if priority else set()

    def append(record):
        # Multiple relaxed minima can map to the same nominal construction.
        # Retain them in detailed evidence, not duplicate the default choices.
        if record["candidate_id"] not in seen and pair_key(record) not in seen_pairs and len(chosen) < 4:
            chosen.append(record)
            seen.add(record["candidate_id"])
            seen_pairs.add(pair_key(record))
            return True
        return False

    # First reserve one place for each observed scale family, then score-order.
    for family in (short_by_id, long_by_id):
        for record in ranked:
            if record["candidate_id"] in family and append(record):
                break
    for record in ranked:
        append(record)
    return {
        "review_priority_candidate": _compact_record(priority, short_by_id, long_by_id) if priority else None,
        "alternative_candidates": [_compact_record(record, short_by_id, long_by_id) for record in chosen],
        "short_motif_candidates": short[:4], "longer_candidates": longer[:4],
        "short_motif_candidate_count": len(short), "longer_candidate_count": len(longer),
        "total_candidate_count": len(annotations["records"]),
        "lattice_filter": annotations["lattice_filter"],
        "priority_reason": annotations["reason"], "selection_policy": annotations["policy"],
        "long_band_observability":report.get("long_band_observability"),
        "evidence_semantics": _SEMANTICS,
        "selected_candidate_id": None, "requires_user_decision": True,
    }


def analyze_multiscale(source, coordinate_dir, output_dir=None):
    """Analyze an existing coordinate run; expected scientific limits are explicit.

    ``coordinate_dir`` contains ``period_candidates.json`` and boundary files;
    the baseline report selects the PTM cache, including any external cache.
    Detailed numerical outputs are written below ``output_dir``; none are
    returned inline. Programming errors and inconsistent provenance propagate.
    """
    from .analyze_elastic_periodicity import analyze as analyze_elastic
    from .coordinate_rows import analyze as analyze_rows
    from .local_strain_events import analyze_case
    from .multiscale_review import review

    source = Path(source).resolve()
    coordinate_dir = Path(coordinate_dir).resolve()
    out = Path(output_dir).resolve() if output_dir is not None else coordinate_dir
    baseline = json.loads((coordinate_dir / "period_candidates.json").read_text())
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if baseline.get("source", {}).get("sha256") != digest:
        raise ValueError("Coordinate report fingerprint does not match the source input")
    status_file = out / MULTISCALE_STATUS_FILE
    status_file.parent.mkdir(parents=True, exist_ok=True)
    stage = "coordinate_validation"
    try:
        if baseline.get("status") != "candidates_reported" or not baseline.get("candidates"):
            raise MultiscaleUnavailable(baseline.get("reason") or f"Coordinate analysis status: {baseline.get('status')}")
        stage = "elastic_analysis"
        analyze_elastic(source, coordinate_dir, out / "elastic")
        stage = "spatial_strain"
        analyze_case(out, coordinate_dir)
        stage = "coordinate_rows"
        analyze_rows(coordinate_dir, out)
        stage = "multiscale_review_and_summary"
        report = review(out, coordinate_dir)
    except MultiscaleUnavailable as exc:
        result = {
            "status": "unavailable", "reason": str(exc), "source_sha256": digest,
            "coordinate_report_path": str(coordinate_dir / "period_candidates.json"),
            "summary_image_path": None, "detailed_report_path": str(status_file),
            "artifact_paths": {"analysis_status": str(status_file)},
            "review_priority_candidate": None, "alternative_candidates": [],
            "short_motif_candidates": [], "longer_candidates": [],
            "total_candidate_count": len(baseline.get("candidates", [])),
            "evidence_semantics": _SEMANTICS, "requires_user_decision": True,
        }
    except Exception as exc:
        status_file.write_text(json.dumps({
            "status":"failed", "stage":stage, "reason":f"{type(exc).__name__}: {exc}",
            "source_sha256":digest, "summary_image_path":None, "image_paths":[],
            "presentation_policy":"summary_only", "retry_without_changes":False,
        }, indent=2, allow_nan=False) + "\n")
        raise
    else:
        paths = {name: str(out / relative) for name, relative in MULTISCALE_FILES.items()
                 if name not in OPTIONAL_MULTISCALE_FILES or (out / relative).is_file()}
        missing = [path for path in paths.values() if not Path(path).is_file()]
        if missing:
            raise RuntimeError(f"Multiscale analysis did not write required artifacts: {missing}")
        paths["analysis_status"] = str(status_file)
        result = {
            "status": "completed", "source_sha256": digest,
            "lattice_repeat_checks": {r["candidate_id"]: r["lattice_repeat_check"]
                                      for r in report["candidate_annotations"]["records"]},
            "summary_image_path": paths["summary_image"], "detailed_report_path": paths["multiscale_report"],
            "artifact_paths": paths, **candidate_handoff(report),
        }
    status_file.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result
