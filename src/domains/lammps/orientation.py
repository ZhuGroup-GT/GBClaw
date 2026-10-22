"""Infer concrete tilt-grain orientations from an axis and plane or plane family."""

from __future__ import annotations

from itertools import permutations, product
from math import gcd

from langchain_core.tools import tool

from domains.lammps.common.geometry import parse_orientation_triplet


def _reduce_int_triplet(vec: tuple[int, int, int]) -> tuple[int, int, int]:
    common = 0
    for component in vec:
        common = gcd(common, abs(int(component)))
    if common > 1:
        vec = tuple(int(component // common) for component in vec)
    return tuple(int(component) for component in vec)


def _dot_triplet(left: tuple[int, int, int], right: tuple[int, int, int]) -> int:
    return sum(left[i] * right[i] for i in range(3))


def _cross_triplet(left: tuple[int, int, int], right: tuple[int, int, int]) -> tuple[int, int, int]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _cubic_family_planes(plane: tuple[int, int, int]) -> list[tuple[int, int, int]]:
    abs_indices = tuple(abs(component) for component in plane)
    seen_perms: set[tuple[int, int, int]] = set()
    candidates: list[tuple[int, int, int]] = []
    for perm in permutations(abs_indices):
        if perm in seen_perms:
            continue
        seen_perms.add(perm)
        for signs in product((-1, 1), repeat=3):
            signed = _reduce_int_triplet(tuple(perm[i] * signs[i] for i in range(3)))
            if signed not in candidates:
                candidates.append(signed)
    return candidates


def _plane_choice_key(plane: tuple[int, int, int]) -> tuple[bool, int, tuple[int, int, int]]:
    first_nonzero_positive = True
    for component in plane:
        if component != 0:
            first_nonzero_positive = component > 0
            break
    negative_count = sum(1 for component in plane if component < 0)
    return first_nonzero_positive, negative_count, plane


def _format_triplet(vec: tuple[int, int, int]) -> str:
    return f"[{vec[0]}, {vec[1]}, {vec[2]}]"


@tool
def infer_tilt_orientation(
    tilt_axis: list[int],
    gb_plane: list[int],
    plane_is_family: bool = False,
    preferred_plane: list[int] | None = None,
) -> str:
    """Infer a LAMMPS orientation from a tilt axis and GB plane normal.

    Simulation frame convention: z = tilt axis, y = GB plane normal, and
    x = y cross z. Parenthesized Miller notation such as (100) is a concrete
    plane and must use plane_is_family=False. For cubic families such as {331},
    set plane_is_family=True to search all permutation/sign variants and select
    one perpendicular to the axis.

    tilt_axis: integer [u, v, w] tilt axis used as z.
    gb_plane: integer [h, k, l] plane normal, or cubic family representative when
      plane_is_family=True.
    plane_is_family: if True, expand gb_plane as a cubic {hkl} family.
    preferred_plane: optional concrete [h, k, l] normal to use from the family.
    """
    try:
        z = _reduce_int_triplet(parse_orientation_triplet(tilt_axis, label="tilt_axis"))
        plane = _reduce_int_triplet(parse_orientation_triplet(gb_plane, label="gb_plane"))
        preferred = (
            _reduce_int_triplet(
                parse_orientation_triplet(preferred_plane, label="preferred_plane")
            )
            if preferred_plane is not None
            else None
        )
    except ValueError as exc:
        return str(exc)

    if z == (0, 0, 0):
        return "tilt_axis must not be [0, 0, 0]."
    if plane == (0, 0, 0):
        return "gb_plane must not be [0, 0, 0]."

    if plane_is_family:
        all_planes = _cubic_family_planes(plane)
        candidates = sorted(
            [candidate for candidate in all_planes if _dot_triplet(candidate, z) == 0],
            key=_plane_choice_key,
        )
        if preferred is not None:
            if preferred not in all_planes:
                return (
                    f"preferred_plane {_format_triplet(preferred)} is not a member of "
                    f"the cubic family {{{plane[0]} {plane[1]} {plane[2]}}}."
                )
            if _dot_triplet(preferred, z) != 0:
                return (
                    f"preferred_plane {_format_triplet(preferred)} is not perpendicular "
                    f"to tilt_axis {_format_triplet(z)}."
                )
            y = preferred
        elif candidates:
            y = candidates[0]
        else:
            return (
                f"No member of cubic plane family {{{plane[0]} {plane[1]} {plane[2]}}} "
                f"is perpendicular to tilt_axis {_format_triplet(z)}."
            )
    else:
        y = preferred if preferred is not None else plane
        if _dot_triplet(y, z) != 0:
            return (
                f"GB plane normal {_format_triplet(y)} is not perpendicular to "
                f"tilt_axis {_format_triplet(z)}; dot={_dot_triplet(y, z)}."
            )
        candidates = [y]

    x_raw = _cross_triplet(y, z)
    x = _reduce_int_triplet(x_raw)
    if x == (0, 0, 0):
        return (
            f"GB plane normal {_format_triplet(y)} is parallel to tilt_axis "
            f"{_format_triplet(z)}; cannot infer an in-plane x direction."
        )

    candidate_text = ", ".join(_format_triplet(candidate) for candidate in candidates[:12])
    if len(candidates) > 12:
        candidate_text += f", ... ({len(candidates)} total)"

    lines = [
        "Inferred tilt GB orientation for gb_minimize:",
        f"  tilt axis z={_format_triplet(z)}",
        f"  selected GB plane normal y={_format_triplet(y)}",
        f"  in-plane direction x={_format_triplet(x)}",
        f"  orientation: x={_format_triplet(x)}  y={_format_triplet(y)}  z={_format_triplet(z)}",
    ]
    if plane_is_family:
        lines.extend(
            [
                f"  candidate concrete GB planes perpendicular to z: {candidate_text}",
                "Use preferred_plane to select a different concrete plane from the family.",
            ]
        )
    lines.append(
        "Next: use this orientation with estimate_gb_box_size, then write_gb_minimize_config."
    )
    return "\n".join(lines)
