"""Describe measured scale ratios without imposing an integer hierarchy.

The caller supplies a small, data-derived candidate list and an absolute length
tolerance. This helper neither selects a physical fundamental nor interprets
arithmetic relationships as evidence of a dislocation mechanism.
"""
from __future__ import annotations

import math
from itertools import combinations


def _scales(items, label):
    result = []
    for item in items:
        if not all(key in item for key in ("source", "id", "period_angstrom")):
            raise ValueError(f"Each {label} scale needs source, id, and period_angstrom")
        period = float(item["period_angstrom"])
        if not math.isfinite(period) or period <= 0:
            raise ValueError(f"Each {label} period must be finite and positive")
        result.append({"source": item["source"], "id": item["id"], "period_angstrom": period})
    return result


def _within(residual, tolerance):
    magnitude = abs(residual)
    return magnitude <= tolerance or math.isclose(magnitude, tolerance, rel_tol=1e-12)


def _ratio(short, long, tolerance):
    ratio = long["period_angstrom"] / short["period_angstrom"]
    nearest = max(2, math.floor(ratio + 0.5))
    residual = long["period_angstrom"] - nearest * short["period_angstrom"]
    return {
        "short_scale": short,
        "long_scale": long,
        "ratio": ratio,
        "fractional_offset": ratio - math.floor(ratio),
        "nearest_integer_multiple": nearest,
        "signed_offset_from_integer_multiple": ratio - nearest,
        "residual_angstrom": residual,
        "absolute_relative_error": abs(residual) / long["period_angstrom"],
        "within_tolerance": _within(residual, tolerance),
    }


def describe_scale_relations(short_scales, long_scales, tolerance_angstrom):
    """Report L≈nS and L2−L1≈qS independently, retaining noninteger L/S.

    Input scale records require ``source``, ``id``, and ``period_angstrom``.
    All lengths and the tolerance use the same unit (normally Angstrom). Every
    long/short ratio is retained. Difference relations are reported only within
    the caller's tolerance, with q>=1 and distinct positive long lengths. Their
    fractional offsets make clear that ΔL≈S does not require either L/S to be
    an integer. No candidate is changed, rounded to an integer, or selected.
    """
    tolerance = float(tolerance_angstrom)
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("tolerance_angstrom must be finite and nonnegative")
    short = _scales(short_scales, "short")
    long = _scales(long_scales, "long")
    ratios = [_ratio(s, length, tolerance) for s in short for length in long]
    differences = []
    evaluated = 0
    for first, second in combinations(sorted(long, key=lambda row: row["period_angstrom"]), 2):
        delta = second["period_angstrom"] - first["period_angstrom"]
        if delta <= 0:
            continue
        for s in short:
            evaluated += 1
            q = max(1, math.floor(delta / s["period_angstrom"] + 0.5))
            residual = delta - q * s["period_angstrom"]
            if not _within(residual, tolerance):
                continue
            endpoints = [_ratio(s, length, tolerance) for length in (first, second)]
            differences.append({
                "short_scale": s,
                "lower_long_scale": first,
                "upper_long_scale": second,
                "difference_angstrom": delta,
                "nearest_short_multiple": q,
                "residual_angstrom": residual,
                "absolute_relative_error": abs(residual) / delta,
                "within_tolerance": True,
                "long_to_short_ratios": [row["ratio"] for row in endpoints],
                "fractional_offsets": [row["fractional_offset"] for row in endpoints],
                "individual_integer_multiple_support": [row["within_tolerance"] for row in endpoints],
            })
    return {
        "tolerance_angstrom": tolerance,
        "long_to_short_ratios": ratios,
        "long_difference_relations": differences,
        "evaluated_long_difference_checks": evaluated,
        "policy": {
            "single_ratios": "Retain every L/S, including failed integer checks; compare the nearest permitted n>=2.",
            "difference_filter": "Only distinct long lengths with |(L2-L1)-q*S| <= tolerance, q>=1, are listed.",
            "fractional_offset": "ratio - floor(ratio), without snapping the measured ratio to an integer.",
            "relative_errors": "Absolute residual divided by the observed L or observed positive L2-L1.",
            "tolerance": "Caller-supplied absolute length tolerance; not a confidence interval or a probability.",
        },
        "interpretation": "A supported difference relation does not imply integer individual ratios or prove a physical period or dislocation mechanism.",
        "selected_candidate_id": None,
    }
