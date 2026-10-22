"""Model-facing views of stress-strain replies: every event in one row, every curve in one line.

The complete analysis stays in the run's JSON artifact and the archived reply;
these views give the worker what the handoff needs without paging through them.
"""

from __future__ import annotations

import json
import math
from typing import Any

MODEL_VIEW_MAX_CHARS = 15_000

EVENT_ROW_KEYS = (
    "event_index", "support", "segment_index", "loading_direction", "loading_half_cycle",
    "pre_peak_step", "pre_peak_strain", "pre_peak_stress_mpa", "event_step", "event_end_step",
    "event_end_strain", "stress_after_mpa", "stress_drop_mpa", "drop_amplitude_basis",
    "post_minimum_confirmed", "threshold_event_indices",
)
_DETECTION_KEYS = (
    "method", "threshold_mpa", "sensitivity", "robust_sigma_mpa", "mad_mpa",
    "signal_range_mpa", "nonconverged_points_included",
)
_SEGMENT_KEYS = (
    "segment_index", "loading_direction", "loading_half_cycle", "loading_cycle",
    "complete_event_count", "first_complete_event", "first_complete_is_first_observed_event",
    "first_to_subsequent_median_drop_ratio", "first_drop_larger_than_all_subsequent",
    "incomplete_event_indices", "threshold_only_event_indices",
)
_ENVELOPE_KEYS = ("run_id", "workflow", "status", "manifest", "error")


def _number(value: Any, digits: int) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return round(value, digits) if isinstance(value, float) else value


def _rounded(value: Any, *, strain_digits: int = 6, stress_digits: int = 3) -> Any:
    """Round floats by their unit; shear-strain steps are 1e-4 apart, stresses are in MPa."""
    if isinstance(value, dict):
        return {key: (_number(item, strain_digits) if "strain" in key else _number(item, stress_digits))
                if not isinstance(item, (dict, list)) else _rounded(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_rounded(item) for item in value]
    return value


def _frame(frame: Any) -> dict[str, Any] | None:
    if not isinstance(frame, dict):
        return None
    return {key: frame.get(key) for key in ("step", "exact", "artifact_id") if key in frame}


def _event_row(event: dict[str, Any]) -> dict[str, Any]:
    row = {key: event.get(key) for key in EVENT_ROW_KEYS if key in event}
    row["post_event_frame"] = _frame(event.get("post_event_frame"))
    return _rounded(row)


def _extremum_row(item: dict[str, Any]) -> dict[str, Any]:
    return _rounded({key: item.get(key) for key in (
        "extremum_index", "step", "strain", "stress_mpa", "plateau", "loading_direction",
    ) if key in item})


def _artifacts(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    return [{key: item[key] for key in ("role", "path") if key in item}
            for item in envelope.get("artifacts") or [] if isinstance(item, dict)]


def _fit(view: dict[str, Any], *, rows_key: str, detail_key: str | None, read_from: str) -> dict[str, Any]:
    """Keep the view whole when it fits; otherwise drop the redundant detail, then trailing rows."""
    encoded = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    if encoded(view) <= MODEL_VIEW_MAX_CHARS:
        return view
    if detail_key and detail_key in view:
        view = {**view, detail_key: {"omitted": True, "read_from": read_from}}
        if encoded(view) <= MODEL_VIEW_MAX_CHARS:
            return view
    rows = list(view.get(rows_key) or [])
    kept = len(rows)
    while kept > 1:
        kept = kept * 3 // 4
        candidate = {**view, rows_key: [*rows[:kept], {
            "omitted_events": len(rows) - kept, "total_events": len(rows), "read_from": read_from}]}
        if encoded(candidate) <= MODEL_VIEW_MAX_CHARS:
            return candidate
    return candidate


def stress_strain_model_view(envelope: dict[str, Any]) -> dict[str, Any]:
    """One row per combined event, the detection parameters and the curve summary."""
    result = envelope.get("result") or {}
    detection = result.get("detection") or {}
    threshold = result.get("threshold_detection") or detection.get("threshold_detection") or {}
    extrema = result.get("extrema_detection") or detection.get("extrema_detection") or {}
    curve = result.get("curve_summary") or {}
    local = result.get("local_extrema") or {}
    events = result.get("combined_events") or result.get("events") or []
    view = {
        **{key: envelope.get(key) for key in _ENVELOPE_KEYS if key in envelope},
        "artifacts": _artifacts(envelope),
        **{key: result.get(key) for key in (
            "source_run_id", "source_status", "point_count", "nonconverged_point_count",
            "stress_field", "stress_signal", "summary", "conclusion_lookup_key", "data", "plot",
        ) if key in result},
        "detection": _rounded({
            **{key: threshold.get(key) for key in _DETECTION_KEYS if key in threshold},
            "extrema_tolerance_mpa": extrema.get("tolerance_mpa"),
            "combined_method": detection.get("method"),
        }),
        "event_counts": {
            "combined": result.get("combined_event_count", len(events)),
            "threshold": result.get("threshold_event_count"),
            "extrema": result.get("extrema_event_count"),
            "support_counts": curve.get("support_counts"),
        },
        "events_note": ("One row per combined event, in loading order; a row with "
                        "post_minimum_confirmed=false is an unfinished descent."),
        "events": [_event_row(event) for event in events],
        "local_extrema": {kind: [_extremum_row(item) for item in local.get(kind) or []]
                          for kind in ("maxima", "minima")},
        "curve_summary": {
            "summary": curve.get("summary"),
            "segments": [_rounded({**{key: segment.get(key) for key in _SEGMENT_KEYS if key in segment},
                                   "subsequent_events": {
                                       key: (segment.get("subsequent_events") or {}).get(key)
                                       for key in ("count", "event_indices", "drop_mpa")}})
                         for segment in curve.get("segments") or []],
            "interpretation_notes": curve.get("interpretation_notes"),
        },
        "details_elsewhere": ("Frames with paths, segment bounds, single-step drops and detection "
                              "definitions: stress_strain_data artifact or full_output."),
    }
    return _fit(view, rows_key="events", detail_key="local_extrema", read_from="stress_strain_data")


def _finite_points(arrays: dict[str, list[Any]], stress_field: str) -> list[tuple[int, float, float]]:
    points = []
    for step, strain, stress in zip(arrays.get("step") or [], arrays.get("actual_shear_strain") or [],
                                    arrays.get(stress_field) or []):
        if isinstance(strain, (int, float)) and isinstance(stress, (int, float)) \
                and math.isfinite(strain) and math.isfinite(stress):
            points.append((int(step), float(strain), float(stress)))
    return points


def _series_line(item: dict[str, Any], *, stress_field: str) -> dict[str, Any]:
    arrays = item.get("arrays") or {}
    points = _finite_points(arrays, stress_field)
    line = {key: item.get(key) for key in (
        "source_run_id", "label", "point_count", "nonconverged_point_count") if key in item}
    directions = [value for value in arrays.get("loading_direction") or [] if value]
    line["loading_directions"] = list(dict.fromkeys(directions))
    line["half_cycles"] = len(set(value for value in arrays.get("loading_half_cycle") or [] if value is not None))
    if not points:
        line["finite_points"] = 0
        return line
    first, last = points[0], points[-1]
    peak = max(points, key=lambda point: point[2])
    low = min(points, key=lambda point: point[2])
    line["finite_points"] = len(points)
    line["strain"] = {"first": first[1], "last": last[1],
                      "min": min(point[1] for point in points), "max": max(point[1] for point in points)}
    line[stress_field] = {
        "first": first[2], "last": last[2], "first_step": first[0], "last_step": last[0],
        "max": peak[2], "max_step": peak[0], "max_strain": peak[1],
        "min": low[2], "min_step": low[0], "min_strain": low[1],
    }
    return _rounded(line)


def stress_strain_comparison_model_view(envelope: dict[str, Any], series: list[dict[str, Any]], *,
                                        stress_field: str, use_absolute_strain: bool,
                                        use_absolute_stress: bool) -> dict[str, Any]:
    """Per-curve statistics so peak, residual and hysteresis questions need no data file."""
    result = envelope.get("result") or {}
    view = {
        **{key: envelope.get(key) for key in _ENVELOPE_KEYS if key in envelope},
        "artifacts": _artifacts(envelope),
        "source_run_ids": result.get("source_run_ids"),
        "plotted_axes": {"x": "actual_shear_strain", "y": stress_field,
                         "use_absolute_strain": use_absolute_strain,
                         "use_absolute_stress": use_absolute_stress},
        "series_note": ("Statistics are of the signed source values in loading order; "
                        "'first'/'last' are the curve's end points, not extrema."),
        "series": [_series_line(item, stress_field=stress_field) for item in series],
        "data": result.get("data"),
        "plot": result.get("plot"),
        "details_elsewhere": "Per-point arrays: stress_strain_comparison_data artifact.",
    }
    return _fit(view, rows_key="series", detail_key=None, read_from="stress_strain_comparison_data")


__all__ = ["EVENT_ROW_KEYS", "MODEL_VIEW_MAX_CHARS", "stress_strain_comparison_model_view",
           "stress_strain_model_view"]
