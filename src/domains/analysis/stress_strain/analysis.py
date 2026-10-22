"""Deterministic stress--strain analysis for ``gb_shear`` metadata.

The agent-facing wrapper lives in :mod:`tools.analysis.analysis_tools`. This module keeps
the numerical work independent of LangChain and project storage so the event
detection policy can be tested directly.
"""

from __future__ import annotations

import math
import statistics
from pathlib import Path
from typing import Any


STRESS_FIELDS = frozenset(
    {
        "top_shear_stress_mpa",
        "bottom_shear_stress_mpa",
        "sigma_xx_mpa",
        "sigma_yy_mpa",
        "sigma_zz_mpa",
        "sigma_xy_mpa",
        "sigma_xz_mpa",
        "sigma_yz_mpa",
    }
)

POINT_ARRAY_FIELDS = (
    "step",
    "increment_stage",
    "loading_direction",
    "loading_half_cycle",
    "loading_cycle",
    "step_in_loading_half_cycle",
    "relative_target_shear_strain",
    "target_shear_strain",
    "relative_shear_strain",
    "actual_shear_strain",
    "current_slab_spacing_angstrom",
    "top_shear_stress_mpa",
    "bottom_shear_stress_mpa",
    "sigma_xx_mpa",
    "sigma_yy_mpa",
    "sigma_zz_mpa",
    "sigma_xy_mpa",
    "sigma_xz_mpa",
    "sigma_yz_mpa",
    "converged",
)


def _finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json_scalar(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def metadata_points_to_arrays(metadata: dict[str, Any]) -> dict[str, list[Any]]:
    """Convert the point-oriented schema-v1 shear metadata into column arrays."""
    if metadata.get("version") != 1:
        raise ValueError(
            f"unsupported shear metadata version {metadata.get('version')!r}; expected 1"
        )
    raw_points = metadata.get("points")
    if not isinstance(raw_points, list) or not raw_points:
        raise ValueError("shear metadata contains no completed points")

    points: list[dict[str, Any]] = []
    seen_steps: set[int] = set()
    for offset, raw in enumerate(raw_points):
        if not isinstance(raw, dict):
            raise ValueError(f"shear point at index {offset} is not an object")
        try:
            step = int(raw["step"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"shear point at index {offset} has no valid step") from exc
        if step in seen_steps:
            raise ValueError(f"duplicate shear point step {step}")
        seen_steps.add(step)
        points.append({**raw, "step": step})
    points.sort(key=lambda item: int(item["step"]))

    arrays = {
        field: [_json_scalar(point.get(field)) for point in points]
        for field in POINT_ARRAY_FIELDS
    }
    for prefix in ("top_com", "bottom_com"):
        for axis in ("x", "y"):
            arrays[f"{prefix}_{axis}_angstrom"] = [
                _json_scalar((point.get(prefix) or {}).get(axis))
                if isinstance(point.get(prefix), dict)
                else None
                for point in points
            ]
    return arrays


def _frame_index(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    frames_by_step: dict[int, str] = {}
    for raw in metadata.get("frames") or []:
        if not isinstance(raw, dict):
            continue
        try:
            step = int(raw["step"])
        except (KeyError, TypeError, ValueError):
            continue
        path = str(raw.get("path") or "").strip()
        if path:
            frames_by_step[step] = path
    return [
        {"step": step, "path": frames_by_step[step]}
        for step in sorted(frames_by_step)
    ]


def _nearest_frame(
    frames: list[dict[str, Any]], requested_step: int
) -> dict[str, Any] | None:
    if not frames:
        return None
    selected = min(
        frames,
        key=lambda item: (
            abs(int(item["step"]) - requested_step),
            int(item["step"]) > requested_step,
            int(item["step"]),
        ),
    )
    selected_step = int(selected["step"])
    return {
        "requested_step": requested_step,
        "step": selected_step,
        "path": selected["path"],
        "exact": selected_step == requested_step,
        "step_offset": selected_step - requested_step,
    }


def _segments(
    arrays: dict[str, list[Any]], signal: list[float | None]
) -> list[list[int]]:
    """Return contiguous, finite point positions without crossing load reversals."""
    steps = arrays["step"]
    half_cycles = arrays["loading_half_cycle"]
    directions = arrays["loading_direction"]
    segments: list[list[int]] = []
    active: list[int] = []
    active_key: tuple[Any, Any] | None = None
    previous_step: int | None = None

    for position, value in enumerate(signal):
        try:
            step = int(steps[position])
        except (TypeError, ValueError):
            value = None
            step = -1
        half_cycle = half_cycles[position]
        key = (half_cycle, directions[position])
        contiguous = previous_step is not None and step == previous_step + 1
        if value is None or (active and (key != active_key or not contiguous)):
            if active:
                segments.append(active)
            active = []
            active_key = None
        if value is not None:
            if not active:
                active_key = key
            active.append(position)
            previous_step = step
        else:
            previous_step = None
    if active:
        segments.append(active)
    return segments


def _automatic_drop_threshold(
    signal: list[float | None],
    segments: list[list[int]],
    *,
    sensitivity: float,
) -> dict[str, Any]:
    magnitudes = [
        abs(float(signal[right]) - float(signal[left]))
        for segment in segments
        for left, right in zip(segment, segment[1:])
    ]
    finite_signal = [float(value) for value in signal if value is not None]
    if not magnitudes or len(finite_signal) < 2:
        return {
            "method": "automatic_mad",
            "threshold_mpa": None,
            "median_absolute_step_change_mpa": None,
            "mad_mpa": None,
            "robust_sigma_mpa": None,
            "signal_range_mpa": None,
            "sensitivity": sensitivity,
        }

    center = statistics.median(magnitudes)
    mad = statistics.median(abs(value - center) for value in magnitudes)
    robust_sigma = 1.4826 * mad
    signal_range = max(finite_signal) - min(finite_signal)
    threshold = max(
        center + sensitivity * robust_sigma,
        0.02 * signal_range,
        1.0e-12,
    )
    return {
        "method": "automatic_mad",
        "threshold_mpa": threshold,
        "median_absolute_step_change_mpa": center,
        "mad_mpa": mad,
        "robust_sigma_mpa": robust_sigma,
        "signal_range_mpa": signal_range,
        "sensitivity": sensitivity,
    }


def _segment_frame(
    frames: list[dict[str, Any]],
    requested_step: int,
    segment_start_step: int,
    segment_end_step: int,
    *,
    after: bool = False,
) -> dict[str, Any] | None:
    """Select within a continuous loading segment; post frames never precede it."""
    available = [
        frame
        for frame in frames
        if segment_start_step <= int(frame["step"]) <= segment_end_step
        and (not after or int(frame["step"]) >= requested_step)
    ]
    selected = _nearest_frame(available, requested_step)
    if selected is not None:
        selected["selection"] = (
            "at_or_after_within_segment" if after else "nearest_within_segment"
        )
    return selected


def _detect_local_extrema(
    arrays: dict[str, list[Any]],
    raw_stress: list[float | None],
    signal: list[float | None],
    segments: list[list[int]],
    frames: list[dict[str, Any]],
    *,
    tolerance_mpa: float,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Find sign reversals along loading order, retaining every peak/valley pair."""
    for field in (
        "progression_slope_mpa",
        "strain_slope_mpa",
        "slope_sign",
        "slope_basis",
        "extrema_slope_sign",
    ):
        arrays[field] = [None] * len(signal)
    extrema: dict[str, list[dict[str, Any]]] = {"maxima": [], "minima": []}
    events: list[dict[str, Any]] = []
    for segment_index, segment in enumerate(segments, start=1):
        segment_start_step = int(arrays["step"][segment[0]])
        segment_end_step = int(arrays["step"][segment[-1]])
        for local_position in range(1, len(segment)):
            left, right = segment[local_position - 1 : local_position + 1]
            difference = float(signal[right]) - float(signal[left])
            sign = 1 if difference > tolerance_mpa else (
                -1 if difference < -tolerance_mpa else 0
            )
            arrays["slope_sign"][right] = sign
            strain_left = _finite_float(arrays["actual_shear_strain"][left])
            strain_right = _finite_float(arrays["actual_shear_strain"][right])
            if strain_left is None or strain_right is None:
                arrays["slope_basis"][right] = "missing_strain"
            elif strain_right == strain_left:
                arrays["slope_basis"][right] = "zero_strain_increment"
            else:
                strain_difference = strain_right - strain_left
                arrays["progression_slope_mpa"][right] = _finite_float(
                    difference / abs(strain_difference)
                )
                arrays["strain_slope_mpa"][right] = _finite_float(
                    difference / strain_difference
                )
                arrays["slope_basis"][right] = "actual_shear_strain"

        # A plateau is bounded by its *total* stress range, not each adjacent
        # difference. Otherwise arbitrarily long small-step drift can disappear,
        # pairing an earlier low "maximum" with a later higher "minimum".
        # At tolerance zero these blocks are simply runs of exactly equal values.
        nonzero_edges: list[tuple[int, int]] = []
        plateau_minimum = plateau_maximum = float(signal[segment[0]])
        for local_position in range(1, len(segment)):
            position = segment[local_position]
            value = float(signal[position])
            extended_minimum = min(plateau_minimum, value)
            extended_maximum = max(plateau_maximum, value)
            if extended_maximum - extended_minimum <= tolerance_mpa:
                arrays["extrema_slope_sign"][position] = 0
                plateau_minimum = extended_minimum
                plateau_maximum = extended_maximum
            else:
                sign = 1 if value > plateau_maximum else -1
                arrays["extrema_slope_sign"][position] = sign
                nonzero_edges.append((local_position, sign))
                plateau_minimum = plateau_maximum = value

        segment_extrema: list[tuple[str, int, int, dict[str, Any]]] = []
        for (before_local, before_sign), (after_local, after_sign) in zip(
            nonzero_edges, nonzero_edges[1:]
        ):
            if before_sign == after_sign:
                continue
            kind = "maxima" if before_sign > 0 else "minima"
            plateau_positions = segment[before_local:after_local]
            representative = max(
                plateau_positions,
                key=lambda position: (
                    float(signal[position]) * (1 if kind == "maxima" else -1),
                    position,
                ),
            )
            step = int(arrays["step"][representative])
            item = {
                "extremum_index": len(extrema[kind]) + 1,
                "kind": "maximum" if kind == "maxima" else "minimum",
                "segment_index": segment_index,
                "segment_start_step": segment_start_step,
                "segment_end_step": segment_end_step,
                "step": step,
                "start_step": int(arrays["step"][plateau_positions[0]]),
                "end_step": int(arrays["step"][plateau_positions[-1]]),
                "plateau": len(plateau_positions) > 1,
                "plateau_signal_range_mpa": max(
                    float(signal[position]) for position in plateau_positions
                ) - min(float(signal[position]) for position in plateau_positions),
                "strain": _finite_float(arrays["actual_shear_strain"][representative]),
                "stress_mpa": raw_stress[representative],
                "signal_mpa": signal[representative],
                "loading_direction": arrays["loading_direction"][representative],
                "loading_half_cycle": arrays["loading_half_cycle"][representative],
                "loading_cycle": arrays["loading_cycle"][representative],
                "slope_before_sign": before_sign,
                "slope_after_sign": after_sign,
                "frame": _segment_frame(
                    frames, step, segment_start_step, segment_end_step,
                    after=kind == "minima",
                ),
            }
            extrema[kind].append(item)
            segment_extrema.append((kind, representative, after_local, item))

        for extremum_position, (kind, peak_position, descent_local, peak) in enumerate(
            segment_extrema
        ):
            if kind != "maxima":
                continue
            # Sign reversals alternate, so the next extremum is necessarily a valley.
            valley = (
                segment_extrema[extremum_position + 1]
                if extremum_position + 1 < len(segment_extrema)
                else None
            )
            confirmed = valley is not None
            end_position = valley[1] if valley is not None else segment[-1]
            onset_position = segment[descent_local]
            # Include every edge from the representative peak to the observed end.
            descent_positions = segment[
                peak_position - segment[0] : end_position - segment[0] + 1
            ]
            largest_before, largest_after = max(
                zip(descent_positions, descent_positions[1:]),
                key=lambda pair: float(signal[pair[0]]) - float(signal[pair[1]]),
            )
            end_step = int(arrays["step"][end_position])
            peak_step = int(arrays["step"][peak_position])
            events.append({
                "event_index": len(events) + 1,
                "extrema_event_index": len(events) + 1,
                "threshold_event_indices": [],
                "support": "extrema_only",
                "segment_index": segment_index,
                "segment_start_step": segment_start_step,
                "segment_end_step": segment_end_step,
                "event_step": int(arrays["step"][onset_position]),
                "event_end_step": end_step,
                "descent_start_step": int(arrays["step"][segment[descent_local - 1]]),
                "descent_end_step": end_step,
                "event_strain": _finite_float(arrays["actual_shear_strain"][onset_position]),
                "event_end_strain": _finite_float(arrays["actual_shear_strain"][end_position]),
                "loading_direction": arrays["loading_direction"][onset_position],
                "loading_half_cycle": arrays["loading_half_cycle"][onset_position],
                "loading_cycle": arrays["loading_cycle"][onset_position],
                "stress_before_mpa": raw_stress[peak_position],
                "stress_after_mpa": raw_stress[end_position],
                "stress_drop_mpa": float(signal[peak_position]) - float(signal[end_position]),
                "drop_amplitude_basis": "peak_to_valley" if confirmed else "peak_to_last_observed",
                "largest_single_step_drop_mpa": float(signal[largest_before]) - float(signal[largest_after]),
                "largest_single_step_drop_at_step": int(arrays["step"][largest_after]),
                "pre_peak_step": peak_step,
                "pre_peak_strain": _finite_float(arrays["actual_shear_strain"][peak_position]),
                "pre_peak_stress_mpa": raw_stress[peak_position],
                "pre_peak_abs_stress_mpa": abs(float(raw_stress[peak_position])),
                "pre_peak_frame": peak["frame"],
                "post_event_frame": _segment_frame(
                    frames, end_step, segment_start_step, segment_end_step, after=True,
                ),
                "maximum_index": peak["extremum_index"],
                "minimum_index": valley[3]["extremum_index"] if valley is not None else None,
                "post_minimum_step": end_step if confirmed else None,
                "post_minimum_confirmed": confirmed,
                "event_complete": confirmed,
            })
    return extrema, events


def _combine_events(
    threshold_events: list[dict[str, Any]],
    extrema_events: list[dict[str, Any]],
    frames: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Union detections by overlapping descent intervals, never by stress height."""
    combined = [dict(event) for event in extrema_events]
    for event in combined:
        event["threshold_event_indices"] = []
    for threshold_event in threshold_events:
        matches = [
            event for event in combined
            if event["extrema_event_index"] is not None
            and event["segment_index"] == threshold_event["segment_index"]
            and event["descent_start_step"] < threshold_event["event_step"]
            and threshold_event["event_end_step"] <= event["descent_end_step"]
        ]
        if matches:
            matched = matches[0]
            matched["threshold_event_indices"].append(threshold_event["event_index"])
            matched["support"] = "both"
            continue
        # Preserve unresolved boundary events and threshold-only descending phases.
        combined.append({
            **threshold_event,
            "extrema_event_index": None,
            "threshold_event_indices": [threshold_event["event_index"]],
            "support": "threshold_only",
            "drop_amplitude_basis": "threshold_cluster",
            "post_minimum_step": None,
            "post_minimum_confirmed": False,
            "event_complete": False,
            "post_event_frame": _segment_frame(
                frames,
                threshold_event["event_end_step"],
                threshold_event["segment_start_step"],
                threshold_event["segment_end_step"],
                after=True,
            ),
            "pre_peak_frame": _segment_frame(
                frames,
                threshold_event["pre_peak_step"],
                threshold_event["segment_start_step"],
                threshold_event["segment_end_step"],
            ),
        })
    combined.sort(key=lambda event: (event["event_step"], event["event_end_step"]))
    for event_index, event in enumerate(combined, start=1):
        event["event_index"] = event_index
    return combined


def _event_distribution(values: list[float]) -> dict[str, Any]:
    """Describe a series without classifying a mechanical regime from its scale."""
    if not values:
        return {
            "count": 0, "min": None, "max": None, "mean": None,
            "median": None, "mad": None, "coefficient_of_variation": None,
        }
    center = statistics.median(values)
    mean = statistics.fmean(values)
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": mean,
        "median": center,
        "mad": statistics.median(abs(value - center) for value in values),
        "coefficient_of_variation": (
            statistics.pstdev(values) / abs(mean) if mean else None
        ),
    }


def _summarize_curve_events(
    arrays: dict[str, list[Any]],
    segments: list[list[int]],
    combined_events: list[dict[str, Any]],
    *,
    threshold_count: int,
    extrema_count: int,
) -> dict[str, Any]:
    """Keep the first and recurring complete drops distinct within each segment."""
    signal_by_step = dict(zip(arrays["step"], arrays["analysis_signal_mpa"]))
    events_by_segment: dict[int, list[dict[str, Any]]] = {}
    for event in combined_events:
        events_by_segment.setdefault(event["segment_index"], []).append(event)
    summaries = []
    descriptions = [
        f"Threshold: {threshold_count}; extrema: {extrema_count}; "
        f"combined: {len(combined_events)} candidate drops."
    ]
    for segment_index, segment in enumerate(segments, start=1):
        observed = events_by_segment.get(segment_index, [])
        complete = [
            event for event in observed
            if event.get("event_complete") and event.get("post_minimum_confirmed")
        ]
        first = complete[0] if complete else None
        later = complete[1:]
        drop_stats = _event_distribution([event["stress_drop_mpa"] for event in later])
        median_drop = drop_stats["median"]
        representative_indices = {"smallest_drop": None, "median_drop": None, "largest_drop": None}
        if later:
            representative_indices = {
                "smallest_drop": min(later, key=lambda event: event["stress_drop_mpa"])["event_index"],
                "median_drop": min(
                    later, key=lambda event: abs(event["stress_drop_mpa"] - median_drop)
                )["event_index"],
                "largest_drop": max(later, key=lambda event: event["stress_drop_mpa"])["event_index"],
            }
        incomplete = [event["event_index"] for event in observed if not event.get("event_complete")]
        strain_spacings = []
        for left, right in zip(complete, complete[1:]):
            left_strain = _finite_float(left.get("pre_peak_strain"))
            right_strain = _finite_float(right.get("pre_peak_strain"))
            if left_strain is not None and right_strain is not None:
                strain_spacings.append(abs(right_strain - left_strain))
        summary = {
            "segment_index": segment_index,
            "segment_start_step": arrays["step"][segment[0]],
            "segment_end_step": arrays["step"][segment[-1]],
            "loading_half_cycle": arrays["loading_half_cycle"][segment[0]],
            "loading_direction": arrays["loading_direction"][segment[0]],
            "loading_cycle": arrays["loading_cycle"][segment[0]],
            "complete_event_count": len(complete),
            "first_complete_event": {
                "event_index": first["event_index"],
                "peak_step": first["pre_peak_step"],
                "minimum_step": first["post_minimum_step"],
                "drop_mpa": first["stress_drop_mpa"],
                "peak_signal_mpa": signal_by_step[first["pre_peak_step"]],
                "minimum_signal_mpa": signal_by_step[first["post_minimum_step"]],
                "support": first["support"],
            } if first else None,
            "first_complete_is_first_observed_event": (
                first["event_index"] == observed[0]["event_index"] if first else None
            ),
            "subsequent_events": {
                "count": len(later),
                "event_indices": [event["event_index"] for event in later],
                "drop_mpa": drop_stats,
                "peak_signal_mpa": _event_distribution([
                    signal_by_step[event["pre_peak_step"]] for event in later
                ]),
                "minimum_signal_mpa": _event_distribution([
                    signal_by_step[event["post_minimum_step"]] for event in later
                ]),
                "representative_event_indices": representative_indices,
            },
            "first_to_subsequent_median_drop_ratio": (
                first["stress_drop_mpa"] / median_drop if first and median_drop else None
            ),
            "first_drop_larger_than_all_subsequent": (
                first["stress_drop_mpa"] > drop_stats["max"] if first and later else None
            ),
            "peak_spacing_steps": _event_distribution([
                right["pre_peak_step"] - left["pre_peak_step"]
                for left, right in zip(complete, complete[1:])
            ]),
            "absolute_peak_spacing_strain": _event_distribution(strain_spacings),
            "incomplete_event_indices": incomplete,
            "threshold_only_event_indices": [
                event["event_index"] for event in observed if event["support"] == "threshold_only"
            ],
        }
        summaries.append(summary)
        if segment_index <= 5:
            detail = f"Segment {segment_index}: {len(complete)} complete, {len(incomplete)} unresolved"
            if first:
                detail += f"; first complete drop {first['stress_drop_mpa']:.6g} MPa"
            if later:
                detail += (
                    f"; {len(later)} subsequent drops, median {median_drop:.6g} MPa"
                    f" (range {drop_stats['min']:.6g} to {drop_stats['max']:.6g})"
                )
            descriptions.append(detail + ".")
    if len(summaries) > 5:
        descriptions.append(f"Details for all {len(summaries)} segments are in curve_summary.segments.")
    return {
        "event_basis": "combined_events",
        "descriptive_only": True,
        "summary": " ".join(descriptions),
        "support_counts": {
            support: sum(event["support"] == support for event in combined_events)
            for support in ("both", "extrema_only", "threshold_only")
        },
        "interpretation_notes": [
            "Complete-event statistics require a confirmed maximum and subsequent minimum in the same loading segment.",
            "The first complete event may follow an unresolved initial descent; inspect first_complete_is_first_observed_event.",
            "Unresolved and threshold-only events remain in the event lists but are excluded from complete-event distributions.",
            "Similar peak or minimum values do not merge events at different steps.",
            "Small local extrema may reflect numerical fluctuations; compare both detectors and the saved structures.",
            "A larger first drop or narrowly distributed later drops alone does not establish structural change or a stable structural state.",
        ],
        "segments": summaries,
    }


def analyze_stress_strain_metadata(
    metadata: dict[str, Any],
    *,
    stress_field: str = "top_shear_stress_mpa",
    minimum_drop_mpa: float | None = None,
    sensitivity: float = 3.5,
    use_absolute_stress: bool = True,
    extrema_tolerance_mpa: float = 0.0,
) -> dict[str, Any]:
    """Compare abrupt drops, slope-sign extrema, and their chronological union.

    Automatic detection uses a robust threshold based on the median absolute
    point-to-point change plus ``sensitivity`` times its MAD-derived robust sigma.
    Two percent of the observed signal range is also used as a small floor.  Events
    are never compared across loading half-cycle boundaries, and consecutive
    above-threshold drops are merged into one event. Independent extrema detection
    pairs positive-to-negative reversals with the next negative-to-positive reversal
    along loading order. It applies no amplitude cutoff; an optional tolerance
    coalesces points into plateaus of bounded total stress range. Top-level events
    are the combined union.
    """
    if stress_field not in STRESS_FIELDS:
        raise ValueError(
            f"stress_field must be one of: {', '.join(sorted(STRESS_FIELDS))}"
        )
    if sensitivity <= 0 or not math.isfinite(float(sensitivity)):
        raise ValueError("sensitivity must be a finite positive number")
    if minimum_drop_mpa is not None:
        minimum_drop_mpa = float(minimum_drop_mpa)
        if not math.isfinite(minimum_drop_mpa) or minimum_drop_mpa <= 0:
            raise ValueError("minimum_drop_mpa must be a finite positive number")
    extrema_tolerance_mpa = float(extrema_tolerance_mpa)
    if not math.isfinite(extrema_tolerance_mpa) or extrema_tolerance_mpa < 0:
        raise ValueError("extrema_tolerance_mpa must be a finite nonnegative number")

    arrays = metadata_points_to_arrays(metadata)
    raw_stress = [_finite_float(value) for value in arrays[stress_field]]
    signal = [
        abs(value) if use_absolute_stress and value is not None else value
        for value in raw_stress
    ]
    finite_signal = [value for value in signal if value is not None]
    if len(finite_signal) < 2:
        raise ValueError(
            f"stress field {stress_field!r} contains fewer than two usable finite points"
        )
    arrays["analysis_signal_mpa"] = signal
    segments = _segments(arrays, signal)
    frames = _frame_index(metadata)

    if minimum_drop_mpa is None:
        detection = _automatic_drop_threshold(
            signal, segments, sensitivity=float(sensitivity)
        )
        threshold = detection["threshold_mpa"]
        inclusive = False
    else:
        threshold = minimum_drop_mpa
        detection = {
            "method": "explicit_minimum_drop",
            "threshold_mpa": threshold,
            "median_absolute_step_change_mpa": None,
            "mad_mpa": None,
            "robust_sigma_mpa": None,
            "signal_range_mpa": max(finite_signal) - min(finite_signal),
            "sensitivity": None,
        }
        inclusive = True

    events: list[dict[str, Any]] = []
    if threshold is not None:
        for segment_index, segment in enumerate(segments, start=1):
            candidates: list[tuple[int, float]] = []
            for local_position in range(1, len(segment)):
                before_pos = segment[local_position - 1]
                after_pos = segment[local_position]
                drop = float(signal[before_pos]) - float(signal[after_pos])
                significant = drop >= threshold if inclusive else drop > threshold
                if drop > 0 and significant:
                    candidates.append((local_position, drop))

            clusters: list[list[tuple[int, float]]] = []
            for candidate in candidates:
                if clusters and candidate[0] == clusters[-1][-1][0] + 1:
                    clusters[-1].append(candidate)
                else:
                    clusters.append([candidate])

            peak_window_start = 0
            for cluster in clusters:
                start_local = cluster[0][0]
                end_local = cluster[-1][0]
                peak_positions = segment[peak_window_start:start_local]
                if not peak_positions:
                    peak_positions = [segment[start_local - 1]]
                peak_position = max(
                    peak_positions,
                    key=lambda position: float(signal[position]),
                )
                onset_position = segment[start_local]
                end_position = segment[end_local]
                before_position = segment[start_local - 1]
                largest_local, largest_drop = max(cluster, key=lambda item: item[1])
                largest_position = segment[largest_local]

                onset_step = int(arrays["step"][onset_position])
                end_step = int(arrays["step"][end_position])
                peak_step = int(arrays["step"][peak_position])
                event = {
                    "event_index": len(events) + 1,
                    "segment_index": segment_index,
                    "segment_start_step": int(arrays["step"][segment[0]]),
                    "segment_end_step": int(arrays["step"][segment[-1]]),
                    "descent_start_step": int(arrays["step"][before_position]),
                    "descent_end_step": end_step,
                    "event_step": onset_step,
                    "event_end_step": end_step,
                    "event_strain": _finite_float(
                        arrays["actual_shear_strain"][onset_position]
                    ),
                    "event_end_strain": _finite_float(
                        arrays["actual_shear_strain"][end_position]
                    ),
                    "loading_direction": arrays["loading_direction"][onset_position],
                    "loading_half_cycle": arrays["loading_half_cycle"][onset_position],
                    "loading_cycle": arrays["loading_cycle"][onset_position],
                    "stress_before_mpa": raw_stress[before_position],
                    "stress_after_mpa": raw_stress[end_position],
                    "stress_drop_mpa": (
                        float(signal[before_position]) - float(signal[end_position])
                    ),
                    "largest_single_step_drop_mpa": largest_drop,
                    "largest_single_step_drop_at_step": int(
                        arrays["step"][largest_position]
                    ),
                    "pre_peak_step": peak_step,
                    "pre_peak_strain": _finite_float(
                        arrays["actual_shear_strain"][peak_position]
                    ),
                    "pre_peak_stress_mpa": raw_stress[peak_position],
                    "pre_peak_abs_stress_mpa": abs(float(raw_stress[peak_position])),
                    "pre_peak_frame": _nearest_frame(frames, peak_step),
                    "post_event_frame": _nearest_frame(frames, end_step),
                }
                events.append(event)
                peak_window_start = end_local

    threshold_detection = {
        **detection,
        "definition": (
            "abrupt decrease in absolute stress between adjacent points"
            if use_absolute_stress
            else "abrupt signed-stress decrease between adjacent points"
        ),
        "half_cycle_boundaries_compared": False,
        "direction_boundaries_compared": False,
        "consecutive_drops_merged": True,
        "nonconverged_points_included": True,
        "convergence_affects_analysis": False,
        "frame_selection": "legacy_nearest_frame_may_precede_target",
    }
    extrema, extrema_events = _detect_local_extrema(
        arrays, raw_stress, signal, segments, frames,
        tolerance_mpa=extrema_tolerance_mpa,
    )
    extrema_detection = {
        "method": "local_extrema",
        "definition": "positive-to-negative progression slope maximum paired with next negative-to-positive minimum",
        "tolerance_mpa": extrema_tolerance_mpa,
        "amplitude_cutoff_mpa": None,
        "smoothing_applied": False,
        "slope_definition": "signal difference / absolute actual-strain difference along point order",
        "flat_definition": "consecutive samples coalesced while total signal range <= tolerance_mpa; restart block when range is exceeded",
        "tolerance_scope": "bounded plateau coalescing; may combine small wiggles inside a band, never hides unbounded cumulative drift",
        "adjacent_slope_sign": "informational sign of adjacent difference with tolerance_mpa deadband",
        "extrema_slope_sign": "direction between bounded plateau blocks; zero within a block",
        "missing_or_zero_strain": "use signal difference sign; numerical slopes are null",
        "plateau_representative": "extreme signal value within bounded plateau block, last sample on ties",
        "endpoints_are_extrema": False,
        "trailing_descent": "reported as incomplete without a confirmed minimum",
        "half_cycle_boundaries_compared": False,
        "direction_boundaries_compared": False,
        "nonconverged_points_included": True,
        "post_frame_selection": "at_or_after_target_within_segment_or_null",
    }
    combined_events = _combine_events(events, extrema_events, frames)

    return {
        "version": 1,
        "source_metadata_version": metadata.get("version"),
        "source_status": metadata.get("status"),
        "point_count": len(arrays["step"]),
        "nonconverged_point_count": sum(
            value is False for value in arrays["converged"]
        ),
        "excluded_nonconverged_point_count": 0,
        "stress_field": stress_field,
        "stress_signal": "absolute" if use_absolute_stress else "signed",
        "arrays": arrays,
        "threshold_detection": threshold_detection,
        "threshold_event_count": len(events),
        "threshold_events": events,
        "extrema_detection": extrema_detection,
        "local_extrema": extrema,
        "extrema_event_count": len(extrema_events),
        "extrema_events": extrema_events,
        "detection": {
            "method": "combined",
            "definition": "chronological union of threshold and peak-to-valley detections matched within the same descent",
            "threshold_detection": threshold_detection,
            "extrema_detection": extrema_detection,
            "matching_basis": "same segment; threshold drop interval contained in extrema descent interval",
            "equal_height_events_merged": False,
            "post_frame_selection": "at_or_after_target_within_segment_or_null",
            "half_cycle_boundaries_compared": False,
            "direction_boundaries_compared": False,
            "nonconverged_points_included": True,
            "convergence_affects_analysis": False,
        },
        "combined_event_count": len(combined_events),
        "combined_events": combined_events,
        "event_count": len(combined_events),
        "events": combined_events,
        "curve_summary": _summarize_curve_events(
            arrays, segments, combined_events,
            threshold_count=len(events), extrema_count=len(extrema_events),
        ),
        "frames": frames,
    }


def plot_stress_strain_analysis(
    analysis: dict[str, Any], output_path: Path | str
) -> Path:
    """Compare threshold, extrema, and combined evidence in aligned PNG panels."""
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    arrays = analysis["arrays"]
    stress_field = str(analysis["stress_field"])
    x_values = [_finite_float(value) for value in arrays["actual_shear_strain"]]
    y_values = [_finite_float(value) for value in arrays["analysis_signal_mpa"]]

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    figure, axes = plt.subplots(
        3, 1, figsize=(11, 12), sharex=True, sharey=True, constrained_layout=True
    )
    segments = _segments(arrays, y_values)
    for axis in axes:
        for segment in segments:
            axis.plot(
                [x_values[position] for position in segment],
                [y_values[position] for position in segment],
                color="#2455a4", linewidth=1.5, marker="o", markersize=2.5,
            )

    step_positions = {
        int(step): position for position, step in enumerate(arrays["step"])
    }
    plotted_labels: list[set[str]] = [set(), set(), set()]

    def mark(
        panel: int, step: int, *, label: str, color: str, marker: str,
        annotation: str | None = None,
    ) -> None:
        position = step_positions.get(step)
        if position is None or x_values[position] is None or y_values[position] is None:
            return
        axis = axes[panel]
        axis.scatter(
            [x_values[position]], [y_values[position]], color=color, marker=marker,
            s=42, zorder=5 if marker == "x" else 4,
            label=label if label not in plotted_labels[panel] else None,
        )
        plotted_labels[panel].add(label)
        if annotation:
            lower_x, upper_x = axis.get_xlim()
            near_right = x_values[position] > lower_x + 0.9 * (upper_x - lower_x)
            axis.annotate(
                annotation, (x_values[position], y_values[position]),
                xytext=(-5 if near_right else 5, -14),
                ha="right" if near_right else "left",
                textcoords="offset points", fontsize=8,
                color="#333333",
            )

    for event in analysis.get("threshold_events") or []:
        mark(
            0, int(event["pre_peak_step"]), label="Pre-drop peak",
            color="#e28b20", marker="^",
        )
        mark(
            0, int(event["event_step"]), label="Threshold onset",
            color="#c62f2f", marker="x",
        )
        mark(
            0, int(event["event_end_step"]), label="Threshold cluster end",
            color="#974251", marker="s", annotation=f"T{event['event_index']}",
        )
    extrema = analysis.get("local_extrema") or {}
    for maximum in extrema.get("maxima") or []:
        mark(
            1, int(maximum["step"]), label="Local maximum",
            color="#e28b20", marker="^",
        )
    for minimum in extrema.get("minima") or []:
        mark(
            1, int(minimum["step"]), label="Confirmed local minimum",
            color="#28864b", marker="v",
        )
    for event in analysis.get("extrema_events") or []:
        end_step = int(event["event_end_step"])
        confirmed = bool(event.get("post_minimum_confirmed"))
        mark(
            1, end_step,
            label="Confirmed local minimum" if confirmed else "Unconfirmed descent end",
            color="#28864b" if confirmed else "#777777",
            marker="v" if confirmed else "s",
            annotation=f"X{event['event_index']}",
        )
    support_styles = {
        "both": ("Both methods", "#7b3294", "D"),
        "extrema_only": ("Extrema only", "#28864b", "v"),
        "threshold_only": ("Threshold only", "#c62f2f", "x"),
    }
    for event in analysis.get("combined_events", analysis.get("events")) or []:
        mark(
            2, int(event["pre_peak_step"]), label="Pre-drop peak",
            color="#e28b20", marker="^",
        )
        label, color, marker = support_styles.get(
            event.get("support"), ("Combined event", "#777777", "o")
        )
        confirmed = bool(event.get("post_minimum_confirmed"))
        mark(
            2, int(event["event_end_step"]), label=label, color=color, marker=marker,
            annotation=f"C{event['event_index']}" + ("" if confirmed else " (open)"),
        )

    field_label = stress_field.removesuffix("_mpa").replace("_", " ")
    if analysis.get("stress_signal") == "absolute":
        field_label = f"|{field_label}|"
    titles = (
        f"Threshold method: {analysis.get('threshold_event_count', 0)} event(s)",
        f"Local extrema method: {analysis.get('extrema_event_count', 0)} descent(s)",
        f"Combined evidence: {analysis['event_count']} event(s); open = no confirmed minimum",
    )
    for panel, axis in enumerate(axes):
        axis.set_ylabel(f"{field_label} (MPa)")
        axis.set_title(titles[panel], fontsize=11)
        if plotted_labels[panel]:
            axis.legend(loc="best", fontsize=8)
        axis.grid(True, alpha=0.25)
    axes[-1].set_xlabel("Actual shear strain")
    figure.savefig(target, dpi=180)
    plt.close(figure)
    return target


def plot_stress_strain_comparison(
    series: list[dict[str, Any]],
    output_path: Path | str,
    *,
    stress_field: str,
    use_absolute_strain: bool,
    use_absolute_stress: bool,
    show_legend: bool,
    legend_title: str | None,
    legend_location: str,
    title: str | None,
    x_label: str | None,
    y_label: str | None,
    linewidth: float,
    marker_size: float,
) -> Path:
    """Plot independently styled stress--strain histories on one axis."""
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(10, 6), constrained_layout=True)
    for item in series:
        arrays = item["arrays"]
        raw_strain = [_finite_float(value) for value in arrays["actual_shear_strain"]]
        raw_stress = [_finite_float(value) for value in arrays[stress_field]]
        x_values = [
            abs(value) if use_absolute_strain and value is not None else value
            for value in raw_strain
        ]
        y_values = [
            None
            if value is None
            or x_values[i] is None
            else (abs(value) if use_absolute_stress else value)
            for i, value in enumerate(raw_stress)
        ]
        first_segment = True
        for segment in _segments(arrays, y_values):
            axis.plot(
                [x_values[position] for position in segment],
                [y_values[position] for position in segment],
                color=item["color"],
                linestyle=item["line_style"],
                marker=item["marker"],
                linewidth=linewidth,
                markersize=marker_size,
                label=item["label"] if first_segment else None,
            )
            first_segment = False

    field_label = stress_field.removesuffix("_mpa").replace("_", " ")
    if use_absolute_stress:
        field_label = f"|{field_label}|"
    axis.set_xlabel(
        x_label
        or (
            "|Actual shear strain|"
            if use_absolute_strain
            else "Actual shear strain"
        )
    )
    axis.set_ylabel(y_label or f"{field_label} (MPa)")
    axis.set_title(title or ("Stress--strain curve" if len(series) == 1 else "Stress--strain comparison"))
    axis.grid(True, alpha=0.25)
    if show_legend:
        axis.legend(title=legend_title or None, loc=legend_location)
    figure.savefig(target, dpi=180)
    plt.close(figure)
    return target
