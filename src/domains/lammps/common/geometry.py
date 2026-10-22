"""Bicrystal box geometry, x-repeat matching and the agent box-size policy."""

from __future__ import annotations

import json
import math
from math import gcd

from domains.lammps.common.defaults import (
    BOX_DIM_FACTOR,
    BOX_VOLUME_FACTOR,
    MIN_Y_PER_GRAIN_NM,
    MIN_Z_NM,
    PBC_X_PADDING_EPS_FACTOR,
    TARGET_BOX_NM,
    TARGET_X_NM,
    X_MATCH_CANDIDATE_LIMIT,
    X_MISMATCH_MAX_PERCENT,
)
from domains.lammps.minimize.config import (
    RepeatCount,
    RepeatTriplet,
    SimulationConfig,
)


def _box_dims_nm(geometry: dict) -> tuple[float, float, float]:
    """Material extents in nm: lx = max grain, ly = both grains stacked, lz = max grain."""
    lx_nm = max(geometry["lx_top_angstrom"], geometry["lx_bot_angstrom"]) / 10.0
    ly_nm = (geometry["ly_top_angstrom"] + geometry["ly_bot_angstrom"]) / 10.0
    lz_nm = max(geometry["lz_top_angstrom"], geometry["lz_bot_angstrom"]) / 10.0
    return lx_nm, ly_nm, lz_nm


def box_size_violations(geometry: dict) -> list[str]:
    """Return policy violations for agent-selected construction sizes."""
    dims = _box_dims_nm(geometry)
    violations: list[str] = []

    mismatch = float(geometry["x_mismatch_percent"])
    if mismatch >= X_MISMATCH_MAX_PERCENT:
        violations.append(
            f"x repeat-length mismatch {mismatch:.3f}% does not satisfy the "
            f"agent policy <{X_MISMATCH_MAX_PERCENT:.3f}% limit; choose a "
            "better-matched nx_top:nx_bottom pair from the reported candidates."
        )

    for grain, key in (("top", "top"), ("bottom", "bot")):
        y_nm = float(geometry[f"ly_{key}_angstrom"]) / 10.0
        z_nm = float(geometry[f"lz_{key}_angstrom"]) / 10.0
        if y_nm + 1.0e-9 < MIN_Y_PER_GRAIN_NM:
            violations.append(
                f"{grain} grain y thickness {y_nm:.2f} nm is below the "
                f"agent policy {MIN_Y_PER_GRAIN_NM:.0f} nm per-grain minimum."
            )
        if z_nm + 1.0e-9 < MIN_Z_NM:
            violations.append(
                f"{grain} grain z extent {z_nm:.3f} nm is below the "
                f"agent policy {MIN_Z_NM:.0f} nm minimum."
            )

    for axis, value, target in zip("xyz", dims, TARGET_BOX_NM):
        limit = BOX_DIM_FACTOR * target
        if value > limit:
            violations.append(
                f"{axis} extent {value:.1f} nm exceeds {BOX_DIM_FACTOR:.0f}× the ~{target:.0f} nm "
                f"target (limit {limit:.0f} nm)."
            )
    volume = dims[0] * dims[1] * dims[2]
    target_volume = TARGET_BOX_NM[0] * TARGET_BOX_NM[1] * TARGET_BOX_NM[2]
    limit_volume = BOX_VOLUME_FACTOR * target_volume
    if volume > limit_volume:
        violations.append(
            f"total volume {volume:.0f} nm³ exceeds {BOX_VOLUME_FACTOR:.0f}× the "
            f"~{target_volume:.0f} nm³ target (limit {limit_volume:.0f} nm³)."
        )
    return violations


_ATOMS_PER_CELL = {"bcc": 2, "fcc": 4, "sc": 1, "hcp": 2}


def _vector_norm(vec: list[int] | tuple[int, ...]) -> float:
    return math.sqrt(sum(int(component) ** 2 for component in vec))


def _x_match_candidates(
    period_top: float,
    period_bot: float,
    *,
    target_nm: float = TARGET_X_NM,
    a0: float = 2.8553,
    limit: int = X_MATCH_CANDIDATE_LIMIT,
) -> list[dict]:
    """Find bounded integer repeat pairs for crystallography-dependent x lengths.

    The periods may be irrational relative to one another, so decimal-to-integer
    GCD conversion is not physically meaningful. Search nearby integer ratios
    directly, retain the smallest feasible cell, the target-sized cell, and
    progressively more accurate larger approximants.
    """

    if period_top <= 0 or period_bot <= 0 or a0 <= 0 or target_nm <= 0 or limit <= 0:
        return []

    max_nm = BOX_DIM_FACTOR * TARGET_X_NM
    max_top = max(1, int(math.floor(max_nm * 10.0 / (period_top * a0))))
    max_bot = max(1, int(math.floor(max_nm * 10.0 / (period_bot * a0))))
    rows: dict[tuple[int, int], dict] = {}

    for nx_top in range(1, max_top + 1):
        ideal_bottom = nx_top * period_top / period_bot
        center = int(round(ideal_bottom))
        for nx_bottom in range(max(1, center - 2), min(max_bot, center + 2) + 1):
            lx_top_nm = nx_top * period_top * a0 / 10.0
            lx_bottom_nm = nx_bottom * period_bot * a0 / 10.0
            if max(lx_top_nm, lx_bottom_nm) > max_nm + 1.0e-9:
                continue
            mismatch = (
                abs(lx_top_nm - lx_bottom_nm)
                / max(lx_top_nm, lx_bottom_nm)
                * 100.0
            )
            common = gcd(nx_top, nx_bottom)
            rows[(nx_top, nx_bottom)] = {
                "nx_top": nx_top,
                "nx_bottom": nx_bottom,
                "primitive_ratio": [nx_top // common, nx_bottom // common],
                "scale": common,
                "lx_top_lattice_units": nx_top * period_top,
                "lx_bottom_lattice_units": nx_bottom * period_bot,
                "lx_top_nm": lx_top_nm,
                "lx_bottom_nm": lx_bottom_nm,
                "mean_lx_nm": 0.5 * (lx_top_nm + lx_bottom_nm),
                "mismatch_percent": mismatch,
                "within_tolerance": mismatch < X_MISMATCH_MAX_PERCENT,
                "roles": [],
            }

    if not rows:
        return []
    all_rows = list(rows.values())
    pool = [item for item in all_rows if item["within_tolerance"]]
    if not pool:
        return []

    shortest = min(
        pool,
        key=lambda item: (item["mean_lx_nm"], item["mismatch_percent"]),
    )
    near_target = min(
        pool,
        key=lambda item: (
            abs(item["mean_lx_nm"] - target_nm),
            item["mismatch_percent"],
        ),
    )
    lowest_mismatch = min(
        pool,
        key=lambda item: (
            item["mismatch_percent"],
            abs(item["mean_lx_nm"] - target_nm),
        ),
    )

    # Pareto frontier: as the cell grows, retain only pairs that improve the
    # mismatch. These expose non-multiple rational approximants when useful.
    frontier: list[dict] = []
    best_mismatch = math.inf
    for item in sorted(pool, key=lambda row: row["mean_lx_nm"]):
        if item["mismatch_percent"] < best_mismatch - 1.0e-12:
            frontier.append(item)
            best_mismatch = item["mismatch_percent"]

    selected: dict[tuple[int, int], dict] = {}

    def add(item: dict, role: str) -> None:
        key = (int(item["nx_top"]), int(item["nx_bottom"]))
        if key not in selected:
            selected[key] = {**item, "roles": []}
        if role not in selected[key]["roles"]:
            selected[key]["roles"].append(role)

    add(shortest, "smallest")
    add(near_target, "near_target")
    if frontier:
        remaining_slots = max(0, limit - len(selected) - 1)
        if remaining_slots:
            for index in range(1, remaining_slots + 1):
                position = round(index * (len(frontier) - 1) / (remaining_slots + 1))
                add(frontier[position], "improved_match")
    add(lowest_mismatch, "lowest_mismatch")

    result = sorted(selected.values(), key=lambda item: item["mean_lx_nm"])
    if len(result) <= limit:
        return result

    # Keep the target-sized choice first when a caller requests an unusually
    # small result limit, then preserve the size and accuracy anchors.
    trimmed: list[dict] = []
    for anchor in (near_target, shortest, lowest_mismatch):
        key = (int(anchor["nx_top"]), int(anchor["nx_bottom"]))
        selected_anchor = selected[key]
        if selected_anchor not in trimmed:
            trimmed.append(selected_anchor)
        if len(trimmed) == limit:
            break
    if len(trimmed) < limit:
        for item in result:
            if item not in trimmed:
                trimmed.append(item)
            if len(trimmed) == limit:
                break
    return sorted(trimmed, key=lambda item: item["mean_lx_nm"])


def _minimum_repeat_count(target_nm: float, period: float, a0: float) -> int:
    if target_nm <= 0 or period <= 0 or a0 <= 0:
        return 1
    return max(1, int(math.ceil(target_nm * 10.0 / (period * a0))))


def _complete_x_match_options(
    candidates: list[dict],
    *,
    top_y_period: float,
    top_z_period: float,
    bottom_y_period: float,
    bottom_z_period: float,
    a0: float,
) -> list[dict]:
    """Attach policy-compliant y/z repeats and physical dimensions to x matches."""

    top_ny = _minimum_repeat_count(MIN_Y_PER_GRAIN_NM, top_y_period, a0)
    bottom_ny = _minimum_repeat_count(
        MIN_Y_PER_GRAIN_NM, bottom_y_period, a0
    )
    top_nz = _minimum_repeat_count(MIN_Z_NM, top_z_period, a0)
    bottom_nz = _minimum_repeat_count(MIN_Z_NM, bottom_z_period, a0)

    top_y_nm = top_ny * top_y_period * a0 / 10.0
    bottom_y_nm = bottom_ny * bottom_y_period * a0 / 10.0
    top_z_nm = top_nz * top_z_period * a0 / 10.0
    bottom_z_nm = bottom_nz * bottom_z_period * a0 / 10.0

    options: list[dict] = []
    for candidate in candidates:
        nx_top = int(candidate["nx_top"])
        nx_bottom = int(candidate["nx_bottom"])
        lx_top_nm = float(candidate["lx_top_nm"])
        lx_bottom_nm = float(candidate["lx_bottom_nm"])
        options.append(
            {
                **candidate,
                "top_size": [nx_top, top_ny, top_nz],
                "bottom_size": [nx_bottom, bottom_ny, bottom_nz],
                "material_dimensions_nm": {
                    "x": max(lx_top_nm, lx_bottom_nm),
                    "y": top_y_nm + bottom_y_nm,
                    "z": max(top_z_nm, bottom_z_nm),
                },
                "grain_dimensions_nm": {
                    "top": {
                        "x": lx_top_nm,
                        "y": top_y_nm,
                        "z": top_z_nm,
                    },
                    "bottom": {
                        "x": lx_bottom_nm,
                        "y": bottom_y_nm,
                        "z": bottom_z_nm,
                    },
                },
            }
        )
    return options


def _gb_size_recommendations(
    *,
    top_x: tuple[int, int, int],
    top_y: tuple[int, int, int],
    top_z: tuple[int, int, int],
    bottom_x: tuple[int, int, int],
    bottom_y: tuple[int, int, int],
    bottom_z: tuple[int, int, int],
    a0: float,
    target_x_nm: float,
) -> dict:
    """Return deterministic repeat recommendations without requiring seed sizes."""

    if not math.isfinite(a0) or a0 <= 0:
        raise ValueError("a0 must be a finite number greater than zero")
    if not math.isfinite(target_x_nm) or target_x_nm <= 0:
        raise ValueError("target_x_nm must be a finite number greater than zero")

    x_period_top = _vector_norm(top_x)
    x_period_bottom = _vector_norm(bottom_x)
    x_match_candidates = _complete_x_match_options(
        _x_match_candidates(
            x_period_top,
            x_period_bottom,
            target_nm=target_x_nm,
            a0=a0,
        ),
        top_y_period=_vector_norm(top_y),
        top_z_period=_vector_norm(top_z),
        bottom_y_period=_vector_norm(bottom_y),
        bottom_z_period=_vector_norm(bottom_z),
        a0=a0,
    )
    if not x_match_candidates:
        raise ValueError(
            "No bounded integer x-repeat match satisfying the configured "
            f"<{X_MISMATCH_MAX_PERCENT:.3f}% mismatch limit could be generated "
            "for the supplied orientations."
        )

    target_candidate = min(
        x_match_candidates,
        key=lambda item: (
            abs(float(item["mean_lx_nm"]) - target_x_nm),
            float(item["mismatch_percent"]),
        ),
    )
    recommended_top_size = list(target_candidate["top_size"])
    recommended_bottom_size = list(target_candidate["bottom_size"])
    suggested_nx_top = int(target_candidate["nx_top"])
    suggested_nx_bottom = int(target_candidate["nx_bottom"])
    suggested_lx_nm = float(target_candidate["mean_lx_nm"])

    return {
        "x_period_top": x_period_top,
        "x_period_bottom": x_period_bottom,
        "x_match_candidates": x_match_candidates,
        "recommended_top_size": recommended_top_size,
        "recommended_bottom_size": recommended_bottom_size,
        "suggested_nx_top": suggested_nx_top,
        "suggested_nx_bottom": suggested_nx_bottom,
        "suggested_lx_nm": suggested_lx_nm,
    }


def parse_orientation_triplet(value: list[int], *, label: str) -> tuple[int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{label} must be a list of three integers")
    parsed: list[int] = []
    try:
        for component in value:
            if isinstance(component, bool):
                raise ValueError
            numeric = float(component)
            if not math.isfinite(numeric) or not numeric.is_integer():
                raise ValueError
            parsed.append(int(numeric))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a list of three integers") from exc
    return tuple(parsed)  # type: ignore[return-value]


def _parse_size_triplet(
    value: list[RepeatCount], *, label: str
) -> RepeatTriplet:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{label} must be a list of three positive numbers")
    parsed: list[RepeatCount] = []
    try:
        for component in value:
            if isinstance(component, bool):
                raise ValueError
            numeric = float(component)
            if not math.isfinite(numeric) or numeric <= 0.0:
                raise ValueError
            parsed.append(int(numeric) if numeric.is_integer() else numeric)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{label} must be a list of three positive finite numbers"
        ) from exc
    return tuple(parsed)  # type: ignore[return-value]


def fractional_repeat_warnings(geometry: dict) -> list[str]:
    warnings: list[str] = []
    for grain, key in (
        ("top", "resolved_top_size"),
        ("bottom", "resolved_bottom_size"),
    ):
        size = list(geometry.get(key) or [])
        fractional = [
            float(value)
            for value in size
            if not math.isclose(float(value), round(float(value)), abs_tol=1.0e-12)
        ]
        if fractional:
            warnings.append(
                f"{grain} repeat counts {size} contain fractional value(s) {fractional}; "
                "the exact values are preserved. Integer repeats are recommended for "
                "simple periodic commensurability, so inspect the periodic seam and "
                "relaxed structure when using this explicit user choice."
            )
    return warnings


def _grain_extents(
    orientation_x: tuple[int, int, int],
    orientation_y: tuple[int, int, int],
    orientation_z: tuple[int, int, int],
    size: RepeatTriplet,
    a0: float,
) -> tuple[float, float, float]:
    nx, ny, nz = size
    lx = nx * _vector_norm(orientation_x) * a0
    ly = ny * _vector_norm(orientation_y) * a0
    lz = nz * _vector_norm(orientation_z) * a0
    return lx, ly, lz


def gb_box_geometry(
    *,
    top_x: list[int],
    top_y: list[int],
    top_z: list[int],
    top_size: list[RepeatCount] | None = None,
    bottom_x: list[int],
    bottom_y: list[int],
    bottom_z: list[int],
    bottom_size: list[RepeatCount] | None = None,
    a0: float | None = None,
    lattice: str = "bcc",
    y_vacuum: float | None = None,
    y_eps: float | None = None,
    x_pbc: bool = False,
    target_x_nm: float | None = None,
) -> dict:
    sim_defaults = SimulationConfig()
    resolved_a0 = float(a0 if a0 is not None else sim_defaults.a0)
    resolved_y_vacuum = float(
        y_vacuum if y_vacuum is not None else sim_defaults.y_vacuum
    )
    resolved_y_eps = float(y_eps if y_eps is not None else sim_defaults.y_eps)
    resolved_target_x_nm = float(
        target_x_nm if target_x_nm is not None else TARGET_X_NM
    )
    lattice_norm = lattice.strip().lower()
    atoms_per_cell = _ATOMS_PER_CELL.get(lattice_norm, 2)

    top_x_t = parse_orientation_triplet(top_x, label="top_x")
    top_y_t = parse_orientation_triplet(top_y, label="top_y")
    top_z_t = parse_orientation_triplet(top_z, label="top_z")
    bot_x_t = parse_orientation_triplet(bottom_x, label="bottom_x")
    bot_y_t = parse_orientation_triplet(bottom_y, label="bottom_y")
    bot_z_t = parse_orientation_triplet(bottom_z, label="bottom_z")
    sizing = _gb_size_recommendations(
        top_x=top_x_t,
        top_y=top_y_t,
        top_z=top_z_t,
        bottom_x=bot_x_t,
        bottom_y=bot_y_t,
        bottom_z=bot_z_t,
        a0=resolved_a0,
        target_x_nm=resolved_target_x_nm,
    )
    top_size_t = (
        _parse_size_triplet(top_size, label="top_size")
        if top_size is not None
        else tuple(sizing["recommended_top_size"])
    )
    bot_size_t = (
        _parse_size_triplet(bottom_size, label="bottom_size")
        if bottom_size is not None
        else tuple(sizing["recommended_bottom_size"])
    )

    lx_top, ly_top, lz_top = _grain_extents(
        top_x_t, top_y_t, top_z_t, top_size_t, resolved_a0
    )
    lx_bot, ly_bot, lz_bot = _grain_extents(
        bot_x_t, bot_y_t, bot_z_t, bot_size_t, resolved_a0
    )

    lx_match = max(lx_top, lx_bot)
    x_extent_difference_pct = (
        abs(lx_top - lx_bot) / lx_match * 100.0 if lx_match > 0 else 0.0
    )
    x_mismatch_pct = x_extent_difference_pct
    # Keep this identical to gb_minimize.x_box_length(). The extra half-epsilon
    # in PBC mode safely contains the shifted atom-creation regions.
    x_padding = (
        PBC_X_PADDING_EPS_FACTOR * resolved_y_eps
        if x_pbc
        else resolved_y_vacuum
    )
    lz_box = max(lz_top, lz_bot) + 2.0 * resolved_y_eps
    lx_box = lx_match + 2.0 * x_padding
    ly_box = ly_top + ly_bot + 2.0 * resolved_y_vacuum

    cell_volume = resolved_a0 ** 3 / atoms_per_cell
    approx_atoms = int((lx_top * ly_top * lz_top + lx_bot * ly_bot * lz_bot) / cell_volume)

    return {
        "a0": resolved_a0,
        "lattice": lattice_norm,
        "lx_top_angstrom": lx_top,
        "lx_bot_angstrom": lx_bot,
        "lx_top_nm": lx_top / 10.0,
        "lx_bot_nm": lx_bot / 10.0,
        "x_mismatch_percent": x_mismatch_pct,
        "x_extent_difference_percent": x_extent_difference_pct,
        "x_pbc": x_pbc,
        "target_x_nm": resolved_target_x_nm,
        "target_x_overridden": target_x_nm is not None,
        "x_padding_angstrom": x_padding,
        "ly_top_angstrom": ly_top,
        "ly_bot_angstrom": ly_bot,
        "lz_top_angstrom": lz_top,
        "lz_bot_angstrom": lz_bot,
        "lz_box_angstrom": lz_box,
        "lx_box_angstrom": lx_box,
        "ly_box_angstrom": ly_box,
        "approx_atoms": approx_atoms,
        "x_period_top": sizing["x_period_top"],
        "x_period_bot": sizing["x_period_bottom"],
        "z_period_top": _vector_norm(top_z_t),
        "z_period_bot": _vector_norm(bot_z_t),
        "nx_top": top_size_t[0],
        "nx_bot": bot_size_t[0],
        "resolved_top_size": list(top_size_t),
        "resolved_bottom_size": list(bot_size_t),
        "auto_sized_top": top_size is None,
        "auto_sized_bottom": bottom_size is None,
        "suggested_nx_top": sizing["suggested_nx_top"],
        "suggested_nx_bot": sizing["suggested_nx_bottom"],
        "suggested_lx_nm": sizing["suggested_lx_nm"],
        "x_match_candidates": sizing["x_match_candidates"],
        "recommended_top_size": sizing["recommended_top_size"],
        "recommended_bottom_size": sizing["recommended_bottom_size"],
    }


def format_gb_box_geometry_report(geometry: dict) -> str:
    lines = [
        "Grain box geometry:",
        f"  lx top: {geometry['lx_top_angstrom']:.2f} Å ({geometry['lx_top_nm']:.2f} nm)",
        f"  lx bottom: {geometry['lx_bot_angstrom']:.2f} Å ({geometry['lx_bot_nm']:.2f} nm)",
    ]
    lines.append(
        f"  x repeat-length mismatch: {geometry['x_mismatch_percent']:.2f}% "
        f"(required <{X_MISMATCH_MAX_PERCENT:.2f}%)"
    )
    lines.append(
        f"  preferred x target: {geometry['target_x_nm']:g} nm "
        "(ranking only; not a minimum)"
    )
    lines.extend(
        [
            f"  x padding mode: {'PBC/epsilon' if geometry['x_pbc'] else 'vacuum'} "
            f"({geometry['x_padding_angstrom']:.3f} Å per side)",
            f"  ly top / bottom: {geometry['ly_top_angstrom']:.2f} / "
            f"{geometry['ly_bot_angstrom']:.2f} Å",
            f"  lz top / bottom: {geometry['lz_top_angstrom']:.2f} / "
            f"{geometry['lz_bot_angstrom']:.2f} Å",
            f"  approx simulation box: {geometry['lx_box_angstrom']:.1f} × "
            f"{geometry['ly_box_angstrom']:.1f} × {geometry['lz_box_angstrom']:.1f} Å",
            f"  approx atoms (both grains): {geometry['approx_atoms']}",
            f"  x period (lattice units): top={geometry['x_period_top']:.4f}  "
            f"bottom={geometry['x_period_bot']:.4f}",
            f"  z period (lattice units): top={geometry['z_period_top']:.4f}  "
            f"bottom={geometry['z_period_bot']:.4f}",
        ]
    )

    lines.append(
        f"  X repeat-match candidates near target {geometry['target_x_nm']:.1f} nm "
        f"(required mismatch <{X_MISMATCH_MAX_PERCENT:.2f}%):"
    )
    for candidate in geometry["x_match_candidates"]:
        roles = ",".join(candidate["roles"]) or "alternative"
        dimensions = candidate["material_dimensions_nm"]
        top_dimensions = candidate["grain_dimensions_nm"]["top"]
        bottom_dimensions = candidate["grain_dimensions_nm"]["bottom"]
        lines.append(
            f"     - size top={candidate['top_size'][0]}×"
            f"{candidate['top_size'][1]}×{candidate['top_size'][2]}  "
            f"bottom={candidate['bottom_size'][0]}×"
            f"{candidate['bottom_size'][1]}×{candidate['bottom_size'][2]}; "
            f"material x×y×z={dimensions['x']:.2f}×"
            f"{dimensions['y']:.2f}×{dimensions['z']:.2f} nm "
            f"(y top/bottom={top_dimensions['y']:.2f}/"
            f"{bottom_dimensions['y']:.2f} nm); "
            f"x top:bottom={candidate['nx_top']}:{candidate['nx_bottom']} "
            f"(primitive {candidate['primitive_ratio'][0]}:"
            f"{candidate['primitive_ratio'][1]}, Lx/a0="
            f"{candidate['lx_top_lattice_units']:.3f}/"
            f"{candidate['lx_bottom_lattice_units']:.3f}, "
            f"lx={candidate['mean_lx_nm']:.2f} nm, "
            f"mismatch={candidate['mismatch_percent']:.3f}%, {roles})"
        )
    lines.append(
        "  Candidate lengths below the preferred target remain valid when "
        f"mismatch <{X_MISMATCH_MAX_PERCENT:.2f}%."
    )

    if geometry["auto_sized_top"] or geometry["auto_sized_bottom"]:
        lines.append(
            "  Auto-sized omitted repeat counts: "
            f"top={geometry['resolved_top_size']}  "
            f"bottom={geometry['resolved_bottom_size']}"
        )

    lines.append(
        f"  Recommended matched sizes: top={geometry['recommended_top_size']}  "
        f"bottom={geometry['recommended_bottom_size']}"
    )

    violations = box_size_violations(geometry)
    if violations:
        tx, ty, tz = TARGET_BOX_NM
        lines.append(
            f"  ⚠ geometry is outside the agent sizing policy around "
            f"~{tx:.0f}×{ty:.0f}×{tz:.0f} nm "
            "(x=in-plane, y=both grains, z=tilt axis). Agent-selected sizes "
            "must be revised; an exact user-supplied size may be written only "
            "with user_confirmed_size_override=true:"
        )
        lines.extend(f"     - {reason}" for reason in violations)
    lines.extend(
        f"  ⚠ {warning}" for warning in fractional_repeat_warnings(geometry)
    )
    return "\n".join(lines)


def invalid_geometry_result(geometry: dict, violations: list[str]) -> str:
    """Return geometry violations and deterministic sizing recommendations."""
    x_nm, y_nm, z_nm = _box_dims_nm(geometry)
    payload = {
        "status": "invalid_geometry",
        "written": False,
        "violations": violations,
        "override": {
            "available_for_explicit_user_sizes": True,
            "writer_parameter": "user_confirmed_size_override",
            "instruction": (
                "Use only when the user explicitly supplied or confirmed both "
                "repeat-count triplets and asked to proceed; never use it for "
                "agent-selected sizes."
            ),
        },
        "geometry": {
            "material_extents_nm": {"x": x_nm, "y": y_nm, "z": z_nm},
            "grain_extents_angstrom": {
                "top": {
                    "x": geometry["lx_top_angstrom"],
                    "y": geometry["ly_top_angstrom"],
                    "z": geometry["lz_top_angstrom"],
                },
                "bottom": {
                    "x": geometry["lx_bot_angstrom"],
                    "y": geometry["ly_bot_angstrom"],
                    "z": geometry["lz_bot_angstrom"],
                },
            },
            "simulation_box_angstrom": {
                "x": geometry["lx_box_angstrom"],
                "y": geometry["ly_box_angstrom"],
                "z": geometry["lz_box_angstrom"],
            },
            "x_mismatch_percent": geometry["x_mismatch_percent"],
            "x_extent_difference_percent": geometry[
                "x_extent_difference_percent"
            ],
            "x_pbc": geometry["x_pbc"],
            "target_x_nm": geometry["target_x_nm"],
            "target_x_overridden": geometry["target_x_overridden"],
            "approx_atoms": geometry["approx_atoms"],
            "resolved_top_size": geometry["resolved_top_size"],
            "resolved_bottom_size": geometry["resolved_bottom_size"],
            "auto_sized_top": geometry["auto_sized_top"],
            "auto_sized_bottom": geometry["auto_sized_bottom"],
            "recommended_top_size": geometry["recommended_top_size"],
            "recommended_bottom_size": geometry["recommended_bottom_size"],
            "x_match_candidates": geometry["x_match_candidates"],
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
