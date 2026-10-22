"""LangChain tools wrapping csl_generator.py for use by the Structure worker.

list_csl_sigmas — list Sigma and Theta for a rotation axis;
list_csl_gb_planes — list symmetry-filtered GB planes for one rotation branch;
list_csl_gb_planes_batch — same for many rotations in one parallelized call.
analyze_tilt_geometry — derive and validate the common axis of two tilt planes.
score_csl_tilt_plane_pairs — deterministically score supplied exact CSL plane
pairs against a general tilt boundary without performing CSL enumeration.
match_csl_tilt_references — reverse-map a supplied general tilt pair to
nearby exact CSL rotations and their closest exact plane-pair representatives.
suggest_low_index_near_csl_planes — approximate exact plane pairs by low-index
tilt planes without claiming that the original Sigma is retained.
"""

from __future__ import annotations

import json
from itertools import permutations, product
import re
from math import acos, degrees
from typing import Any, Literal

import numpy as np
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from domains.structure.csl import generator as csl
from core.execution.process_pool import parallel_map

VALID_BASIS = ("fcc", "bcc", "diamond", "sc")
MAX_BATCH_SIGMAS = 25
MAX_NEAR_CSL_SOURCE_PAIRS = 50
# Miller-index search bound for GB plane listing; larger values explode combinatorially.
MAX_GB_PLANE_LIMIT = 6
MAX_LOW_INDEX_LIMIT = 6
MAX_REVERSE_CSL_SIGMA_LIMIT = 500
MAX_REVERSE_CSL_PLANE_MATCHES = 20
MAX_CSL_SCORE_SOURCE_PAIRS = 200
MAX_CSL_SCORE_RESULTS = 100


class NearCslSourcePair(BaseModel):
    """One exact CSL plane pair used as a geometric near-CSL reference."""

    sigma: int = Field(
        gt=1,
        description=(
            "Required exact source Sigma provenance; it is not assigned to snapped planes."
        ),
    )
    plane1: list[int] = Field(description="First exact CSL GB plane normal.")
    plane2: list[int] = Field(description="Second exact CSL GB plane normal.")
    rotation_m: int | None = Field(
        default=None, gt=0, description="Optional exact CSL rotation branch m."
    )
    rotation_n: int | None = Field(
        default=None, gt=0, description="Optional exact CSL rotation branch n."
    )


class CslRotationSelection(BaseModel):
    """One exact CSL rotation branch returned by ``list_csl_sigmas``."""

    sigma: int = Field(gt=1, description="Basis-valid coincidence index.")
    rotation_m: int = Field(
        gt=0, description="Positive primitive quaternion scalar parameter m."
    )
    rotation_n: int = Field(
        gt=0, description="Positive primitive quaternion axis multiplier n."
    )


class CslTiltPlanePair(BaseModel):
    """One basis-valid exact CSL tilt-plane pair to score geometrically."""

    sigma: int = Field(
        gt=1,
        description="Exact source Sigma from a basis-aware CSL plane lookup.",
    )
    plane1: list[int] = Field(description="Exact ordered grain-1 GB plane normal.")
    plane2: list[int] = Field(description="Exact ordered grain-2 GB plane normal.")
    rotation_m: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Exact rotation branch m from list_csl_sigmas; required when Sigma "
            "has more than one branch on the requested axis."
        ),
    )
    rotation_n: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Exact rotation branch n from list_csl_sigmas; required together with m."
        ),
    )
    boundary_type: str = Field(
        default="Tilt",
        description="Exact table boundary class, normally Tilt or Symmetric Tilt.",
    )
    reference_atom_count: int | None = Field(
        default=None,
        gt=0,
        description="Optional reference atom count reported by the plane table.",
    )


def _clamp_gb_plane_limit(limit: int) -> int:
    """Clamp plane-index limit to ``[1, MAX_GB_PLANE_LIMIT]``."""
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return 2
    return max(1, min(value, MAX_GB_PLANE_LIMIT))


def _miller_vector(name: str, values: list[int]) -> np.ndarray:
    if len(values) != 3:
        raise ValueError(f"{name} must contain exactly three integers")
    try:
        vector = np.array([int(value) for value in values], dtype=int)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain exactly three integers") from exc
    if not np.any(vector):
        raise ValueError(f"{name} must not be [0, 0, 0]")
    return vector


def _reduced_miller(vector: np.ndarray) -> list[int]:
    return [int(value) for value in csl.reduce_miller_indices(vector)]


def _plane_angle_degrees(left: np.ndarray, right: np.ndarray) -> tuple[float, float]:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    cosine = float(np.dot(left, right)) / denominator
    oriented = degrees(acos(max(-1.0, min(1.0, cosine))))
    return oriented, min(oriented, 180.0 - oriented)


def _resolve_plane_pair_rotation(
    axis: np.ndarray,
    basis: str,
    source: CslTiltPlanePair,
    plane1: np.ndarray,
    plane2: np.ndarray,
) -> tuple[float, int, int]:
    """Resolve an exact source row to its CSL branch.

    New plane-table rows carry ``rotation_m``/``rotation_n`` explicitly.  For
    older saved rows, the exact ordered plane-pair angle can safely recover the
    branch when it has a unique match.
    """

    if (source.rotation_m is None) != (source.rotation_n is None):
        raise ValueError("rotation_m and rotation_n must be supplied together")
    if source.rotation_m is not None:
        return csl.resolve_csl_rotation(
            axis,
            basis,
            int(source.sigma),
            rotation_m=source.rotation_m,
            rotation_n=source.rotation_n,
        )

    rotations = csl.get_fundamental_theta_m_n_list(axis, basis, int(source.sigma))
    if len(rotations) == 1:
        return rotations[0]
    _oriented, pair_angle = _plane_angle_degrees(plane1, plane2)
    matching = [
        row for row in rotations if abs(degrees(row[0]) - pair_angle) <= 1.0e-6
    ]
    if len(matching) == 1:
        return matching[0]
    choices = ", ".join(
        f"(m={m}, n={n}, theta={degrees(theta):.8f} deg)"
        for theta, m, n in rotations
    )
    raise ValueError(
        f"Sigma {int(source.sigma)} has multiple rotation branches and the "
        f"plane pair does not select one uniquely; supply rotation_m and "
        f"rotation_n. Choices: {choices}."
    )


def _cubic_family_members(reference: np.ndarray) -> list[np.ndarray]:
    members: set[tuple[int, int, int]] = set()
    for permutation in set(permutations(abs(int(value)) for value in reference)):
        for signs in product((-1, 1), repeat=3):
            candidate = tuple(
                int(value) * int(sign)
                for value, sign in zip(permutation, signs, strict=True)
            )
            reduced = tuple(_reduced_miller(np.array(candidate, dtype=int)))
            members.add(reduced)
    return [np.array(member, dtype=int) for member in sorted(members)]


def _canonical_unoriented_miller(vector: np.ndarray) -> tuple[int, int, int]:
    """Return a primitive representative with a stable sign for an unoriented plane."""
    reduced = tuple(_reduced_miller(vector))
    first_nonzero = next(value for value in reduced if value != 0)
    if first_nonzero < 0:
        return tuple(-value for value in reduced)
    return reduced


def _signed_like(candidate: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Choose the plane-normal sign closest to the reference's displayed direction."""
    return -candidate if int(np.dot(candidate, reference)) < 0 else candidate


def _family_label(vector: np.ndarray) -> str:
    signature = sorted((abs(value) for value in _reduced_miller(vector)), reverse=True)
    return "{" + "".join(str(value) for value in signature) + "}"


def _cubic_family_signature(vector: np.ndarray) -> tuple[int, int, int]:
    """Return the permutation/sign-invariant signature of a cubic family."""
    return tuple(
        sorted((abs(value) for value in _reduced_miller(vector)), reverse=True)
    )


def _ordered_unoriented_plane_pair_orbit_key(
    plane1: np.ndarray,
    plane2: np.ndarray,
    *,
    fixed_axis: np.ndarray | None = None,
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Canonicalize an ordered plane pair under proper cubic symmetry.

    A cubic plane is unoriented, so each transformed normal is independently
    equivalent to its negative. The pair remains ordered because the two
    entries belong to different grains. For a nonparallel pair the tilt line
    is fixed by the cross product, and its sign changes only with the arbitrary
    signs chosen for the two normals; no separate directed-axis key is needed.
    If an exact axis is supplied, only its line-preserving subgroup is allowed.
    """

    symmetries = (
        csl._axis_preserving_symmetries(fixed_axis)
        if fixed_axis is not None
        else csl._cubic_symmetry_matrices()
    )
    equivalents: list[
        tuple[tuple[int, int, int], tuple[int, int, int]]
    ] = []
    for raw_symmetry in symmetries:
        symmetry = np.rint(raw_symmetry).astype(int)
        equivalents.append(
            (
                _canonical_unoriented_miller(symmetry @ plane1),
                _canonical_unoriented_miller(symmetry @ plane2),
            )
        )
    return min(equivalents)


def _grain_local_axis_family_candidates(
    plane1: np.ndarray,
    plane2: np.ndarray,
    axis_family: np.ndarray,
) -> tuple[list[dict[str, Any]], list[list[int]], list[list[int]]]:
    """Resolve a cubic tilt-axis family independently in two grain bases.

    ``plane1`` and ``plane2`` are Miller normals in their respective grain-local
    crystal coordinates.  A physical tilt line may therefore have different
    signed/permuted Miller representatives in the two grains.  Proper cubic
    symmetries provide the coordinate relabeling needed to compare the ordered
    pair in one reference frame.
    """
    family_members = _cubic_family_members(axis_family)
    plane1_axis_lines = sorted(
        {
            _canonical_unoriented_miller(member)
            for member in family_members
            if int(np.dot(plane1, member)) == 0
        }
    )
    plane2_axis_lines = sorted(
        {
            _canonical_unoriented_miller(member)
            for member in family_members
            if int(np.dot(plane2, member)) == 0
        }
    )

    ranked: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    seen: set[
        tuple[
            tuple[int, int, int],
            tuple[int, int, int],
            tuple[int, int, int],
        ]
    ] = set()
    for plane1_axis_line in plane1_axis_lines:
        left_axis = np.array(plane1_axis_line, dtype=int)
        for plane2_axis_line in plane2_axis_lines:
            right_axis_line = np.array(plane2_axis_line, dtype=int)
            for raw_symmetry in csl._cubic_symmetry_matrices():
                symmetry = np.rint(raw_symmetry).astype(int)
                mapped_axis = symmetry @ left_axis
                if np.any(np.cross(mapped_axis, right_axis_line)):
                    continue

                raw_plane2_in_plane1 = symmetry.T @ plane2
                plane2_in_plane1 = _signed_like(raw_plane2_in_plane1, plane1)
                intersection = np.cross(plane1, plane2_in_plane1)
                if not np.any(intersection):
                    continue
                plane1_axis = np.array(_reduced_miller(intersection), dtype=int)
                if (
                    _cubic_family_signature(plane1_axis)
                    != _cubic_family_signature(axis_family)
                    or np.any(np.cross(plane1_axis, left_axis))
                ):
                    continue
                plane2_axis = np.array(
                    _reduced_miller(symmetry @ plane1_axis), dtype=int
                )
                if int(np.dot(plane1, plane1_axis)) != 0:
                    continue
                if int(np.dot(plane2, plane2_axis)) != 0:
                    continue

                _, angle = _plane_angle_degrees(plane1, plane2_in_plane1)
                key = (
                    tuple(int(value) for value in plane1_axis),
                    tuple(int(value) for value in plane2_axis),
                    tuple(int(value) for value in plane2_in_plane1),
                )
                if key in seen:
                    continue
                seen.add(key)
                mapped_back = symmetry @ plane2_in_plane1
                normal_sign = 1 if int(np.dot(mapped_back, plane2)) > 0 else -1
                row = {
                    "plane1_axis": plane1_axis.tolist(),
                    "plane2_axis": plane2_axis.tolist(),
                    "plane2_in_plane1_frame": np.array(
                        _reduced_miller(plane2_in_plane1), dtype=int
                    ).tolist(),
                    "plane2_normal_sign": normal_sign,
                    "tilt_misorientation_degrees": round(angle, 8),
                    "symmetry_matrix_plane1_to_plane2": symmetry.tolist(),
                }
                rank_key = (
                    round(angle, 12),
                    tuple(plane1_axis.tolist()),
                    tuple(plane2_axis.tolist()),
                    tuple(plane2_in_plane1.tolist()),
                )
                ranked.append((rank_key, row))

    ranked.sort(key=lambda item: item[0])
    return (
        [item[1] for item in ranked],
        [list(item) for item in plane1_axis_lines],
        [list(item) for item in plane2_axis_lines],
    )


def _low_index_tilt_planes(axis: np.ndarray, max_index: int) -> list[np.ndarray]:
    """Enumerate unique primitive low-index plane normals perpendicular to axis."""
    members: set[tuple[int, int, int]] = set()
    values = range(-max_index, max_index + 1)
    for candidate in product(values, repeat=3):
        if candidate == (0, 0, 0):
            continue
        vector = np.array(candidate, dtype=int)
        if int(np.dot(vector, axis)) != 0:
            continue
        members.add(_canonical_unoriented_miller(vector))
    return [np.array(member, dtype=int) for member in sorted(members)]


def _near_csl_pair_suggestions(
    source: NearCslSourcePair,
    axis: np.ndarray,
    low_index_planes: list[np.ndarray],
    *,
    max_plane_deviation_degrees: float,
    max_misorientation_delta_degrees: float,
    max_suggestions: int,
) -> dict[str, Any]:
    """Rank joint low-index approximations for one exact reference pair."""
    try:
        plane1 = _miller_vector("plane1", source.plane1)
        plane2 = _miller_vector("plane2", source.plane2)
    except ValueError as exc:
        return {"sigma_reference": source.sigma, "error": str(exc)}

    dot1 = int(np.dot(plane1, axis))
    dot2 = int(np.dot(plane2, axis))
    if dot1 != 0 or dot2 != 0:
        return {
            "sigma_reference": source.sigma,
            "source_plane1": _reduced_miller(plane1),
            "source_plane2": _reduced_miller(plane2),
            "error": (
                "near-CSL low-index snapping currently supports tilt pairs only; "
                f"axis dots are plane1={dot1}, plane2={dot2}"
            ),
        }

    _, source_angle = _plane_angle_degrees(plane1, plane2)
    side1: list[tuple[np.ndarray, float]] = []
    side2: list[tuple[np.ndarray, float]] = []
    for candidate in low_index_planes:
        _, error1 = _plane_angle_degrees(plane1, candidate)
        _, error2 = _plane_angle_degrees(plane2, candidate)
        if error1 <= max_plane_deviation_degrees + 1e-10:
            side1.append((candidate, error1))
        if error2 <= max_plane_deviation_degrees + 1e-10:
            side2.append((candidate, error2))

    ranked: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for candidate1, error1 in side1:
        for candidate2, error2 in side2:
            _, candidate_angle = _plane_angle_degrees(candidate1, candidate2)
            angle_delta = abs(candidate_angle - source_angle)
            if angle_delta > max_misorientation_delta_degrees + 1e-10:
                continue
            display1 = _signed_like(candidate1, plane1)
            display2 = _signed_like(candidate2, plane2)
            complexity = (
                max(abs(int(value)) for value in candidate1),
                max(abs(int(value)) for value in candidate2),
                sum(abs(int(value)) for value in candidate1)
                + sum(abs(int(value)) for value in candidate2),
            )
            suggestion = {
                "plane1": {
                    "concrete": display1.tolist(),
                    "family": _family_label(candidate1),
                    "deviation_degrees": round(error1, 8),
                    "unchanged": bool(error1 < 1e-8),
                },
                "plane2": {
                    "concrete": display2.tolist(),
                    "family": _family_label(candidate2),
                    "deviation_degrees": round(error2, 8),
                    "unchanged": bool(error2 < 1e-8),
                },
                "angle_degrees": {
                    "source_pair": round(source_angle, 8),
                    "low_index_pair": round(candidate_angle, 8),
                    "delta": round(angle_delta, 8),
                },
                "classification": (
                    "exact_geometry"
                    if error1 < 1e-8 and error2 < 1e-8 and angle_delta < 1e-8
                    else "low_index_near_csl_candidate"
                ),
            }
            rank_key = (
                round(max(error1, error2), 12),
                round(error1 + error2, 12),
                round(angle_delta, 12),
                *complexity,
                tuple(int(value) for value in candidate1),
                tuple(int(value) for value in candidate2),
            )
            ranked.append((rank_key, suggestion))

    ranked.sort(key=lambda item: item[0])
    return {
        "sigma_reference": source.sigma,
        "rotation_m": source.rotation_m,
        "rotation_n": source.rotation_n,
        "source_plane1": _reduced_miller(plane1),
        "source_plane2": _reduced_miller(plane2),
        "source_pair_angle_degrees": round(source_angle, 8),
        "suggestions": [item[1] for item in ranked[:max_suggestions]],
    }


def _exact_pair_deviation_matches(
    target1: np.ndarray,
    target2: np.ndarray,
    axis: np.ndarray,
    exact1: np.ndarray,
    exact2: np.ndarray,
) -> list[dict[str, Any]]:
    """Compare an ordered target pair with fixed-axis equivalents of a CSL pair."""

    table1 = np.array(_reduced_miller(exact1), dtype=int)
    table2 = np.array(_reduced_miller(exact2), dtype=int)
    ranked: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    seen: set[tuple[tuple[int, ...], tuple[int, ...], str]] = set()

    for symmetry in csl._axis_preserving_symmetries(axis):
        transformed1 = np.array(
            _reduced_miller(np.dot(symmetry, table1)), dtype=int
        )
        transformed2 = np.array(
            _reduced_miller(np.dot(symmetry, table2)), dtype=int
        )
        for assignment, candidate1, candidate2 in (
            ("direct", transformed1, transformed2),
            ("swapped", transformed2, transformed1),
        ):
            display1 = _signed_like(candidate1, target1)
            display2 = _signed_like(candidate2, target2)
            key = (
                tuple(int(value) for value in display1),
                tuple(int(value) for value in display2),
                assignment,
            )
            if key in seen:
                continue
            seen.add(key)
            _, deviation1 = _plane_angle_degrees(target1, display1)
            _, deviation2 = _plane_angle_degrees(target2, display2)
            _, exact_pair_angle = _plane_angle_degrees(display1, display2)
            maximum = max(deviation1, deviation2)
            total = deviation1 + deviation2
            payload = {
                "exact_plane1": display1.tolist(),
                "exact_plane2": display2.tolist(),
                "table_plane1": table1.tolist(),
                "table_plane2": table2.tolist(),
                "grain_assignment": assignment,
                "fixed_axis_symmetry_equivalent": bool(
                    not np.array_equal(display1, table1)
                    or not np.array_equal(display2, table2)
                    or assignment == "swapped"
                ),
                "plane1_deviation_degrees": round(deviation1, 8),
                "plane2_deviation_degrees": round(deviation2, 8),
                "max_plane_deviation_degrees": round(maximum, 8),
                "total_plane_deviation_degrees": round(total, 8),
                "exact_pair_angle_degrees": round(exact_pair_angle, 8),
            }
            rank_key = (
                round(maximum, 12),
                round(total, 12),
                round(deviation1, 12),
                round(deviation2, 12),
                assignment == "swapped",
                key,
            )
            ranked.append((rank_key, payload))

    ranked.sort(key=lambda item: item[0])
    return [item[1] for item in ranked]


@tool
def analyze_tilt_geometry(
    plane1: list[int],
    plane2: list[int],
    axis: list[int] | None = None,
    plane1_is_family: bool = False,
    plane2_is_family: bool = False,
    axis_is_family: bool = False,
) -> str:
    """Derive and validate the common axis and angle of two tilt-plane normals.

    For nonparallel normals already expressed in one shared crystal frame, their
    cross product gives the candidate pure-tilt axis. If an exact ``axis`` is
    supplied, the tool validates it against both normals and the derived direction.
    Set ``plane1_is_family``/``plane2_is_family`` to expand cubic plane families.
    Raw signed/permuted family members are grouped into ordered unoriented
    plane-pair equivalence classes under proper cubic symmetry; ambiguity means
    more than one such physical class, not merely more than one index variant.
    With two concrete ordered planes and ``axis_is_family=True``, the planes are
    treated as grain-local: the tool resolves one signed/permuted axis member per
    grain and uses proper cubic symmetry to obtain a shared-frame comparison. This
    tool performs no CSL enumeration and does not assign Sigma.
    """
    if axis_is_family and axis is None:
        return "axis is required when axis_is_family=true."
    try:
        left_reference = np.array(
            _reduced_miller(_miller_vector("plane1", plane1)), dtype=int
        )
        right_reference = np.array(
            _reduced_miller(_miller_vector("plane2", plane2)), dtype=int
        )
        supplied_axis = (
            np.array(_reduced_miller(_miller_vector("axis", axis)), dtype=int)
            if axis is not None
            else None
        )
    except ValueError as exc:
        return str(exc)

    # Ordered GB planes are normally expressed in their own grain-local crystal
    # bases.  For a direction *family*, resolve one concrete member per grain
    # and use a proper cubic symmetry to compare the pair in grain 1's frame.
    # Requiring one literal [uvw] to be perpendicular to both local normals is
    # incorrect and was the source of spurious cross-product axes such as
    # (100) x (113) = [0,-3,1] for a requested <110> boundary.
    if axis_is_family and not plane1_is_family and not plane2_is_family:
        assert supplied_axis is not None
        candidates, plane1_axis_candidates, plane2_axis_candidates = (
            _grain_local_axis_family_candidates(
                left_reference,
                right_reference,
                supplied_axis,
            )
        )
        distinct_angles = sorted(
            {
                float(candidate["tilt_misorientation_degrees"])
                for candidate in candidates
            }
        )
        ambiguous = len(distinct_angles) > 1
        selected = candidates[0] if candidates and not ambiguous else None
        raw_intersection = np.cross(left_reference, right_reference)
        raw_cross = (
            _reduced_miller(raw_intersection)
            if np.any(raw_intersection)
            else None
        )

        if selected is not None:
            plane1_axis = np.array(selected["plane1_axis"], dtype=int)
            plane2_axis = np.array(selected["plane2_axis"], dtype=int)
            plane2_in_plane1 = np.array(
                selected["plane2_in_plane1_frame"], dtype=int
            )
            oriented_angle, plane_angle = _plane_angle_degrees(
                left_reference, plane2_in_plane1
            )
            classification = "pure_tilt_geometry"
            tilt_compatible = True
            matches_requested_family = True
            resolved_geometry: dict[str, object] = {
                "plane1": left_reference.tolist(),
                "plane2": right_reference.tolist(),
                # There is no single Miller vector valid in both local bases.
                "axis": None,
                "plane1_axis": plane1_axis.tolist(),
                "plane2_axis": plane2_axis.tolist(),
            }
            shared_frame_geometry: dict[str, object] | None = {
                "reference_grain": "plane1",
                "plane1": left_reference.tolist(),
                "plane2": plane2_in_plane1.tolist(),
                "axis": plane1_axis.tolist(),
            }
            derived_axis: list[int] | None = plane1_axis.tolist()
            angle_payload: dict[str, float | None] = {
                "oriented_normal_degrees": round(oriented_angle, 8),
                "unoriented_plane_degrees": round(plane_angle, 8),
                "tilt_misorientation_degrees": round(plane_angle, 8),
            }
            axis_validation: dict[str, object] = {
                "source": "supplied_family_grain_local",
                "axis": None,
                "requested_axis_family": supplied_axis.tolist(),
                "matches_requested_axis_family": True,
                "selected_concrete_axis": None,
                "selected_plane1_axis": plane1_axis.tolist(),
                "selected_plane2_axis": plane2_axis.tolist(),
                "plane1_dot": int(np.dot(left_reference, plane1_axis)),
                "plane2_dot": int(np.dot(right_reference, plane2_axis)),
                "plane1_perpendicular": True,
                "plane2_perpendicular": True,
                "matches_derived_common_axis": True,
                "requires_grain_local_axes": True,
            }
        else:
            classification = (
                "ambiguous_grain_local_axis_family_tilt_geometry"
                if candidates
                else "supplied_axis_family_incompatible_with_grain_local_tilt_geometry"
            )
            tilt_compatible = False
            matches_requested_family = False if not candidates else None
            resolved_geometry = {
                "plane1": left_reference.tolist(),
                "plane2": right_reference.tolist(),
                "axis": None,
                "plane1_axis": None,
                "plane2_axis": None,
            }
            shared_frame_geometry = None
            derived_axis = None
            angle_payload = {
                "oriented_normal_degrees": None,
                "unoriented_plane_degrees": None,
                "tilt_misorientation_degrees": None,
            }
            axis_validation = {
                "source": "supplied_family_grain_local",
                "axis": None,
                "requested_axis_family": supplied_axis.tolist(),
                "matches_requested_axis_family": matches_requested_family,
                "selected_concrete_axis": None,
                "selected_plane1_axis": None,
                "selected_plane2_axis": None,
                "requires_grain_local_axes": True,
            }

        payload = {
            "method": "grain-local cubic-axis-family resolution and symmetry alignment",
            "coordinate_mode": "grain_local_ordered_planes",
            "input": {
                "plane1": left_reference.tolist(),
                "plane2": right_reference.tolist(),
                "axis": supplied_axis.tolist(),
                "plane1_is_family": False,
                "plane2_is_family": False,
                "axis_is_family": True,
            },
            "resolved_geometry": resolved_geometry,
            "shared_frame_geometry": shared_frame_geometry,
            "angles": angle_payload,
            "derived_common_axis": derived_axis,
            "raw_input_index_cross_product": raw_cross,
            "raw_cross_product_is_physical_axis": False,
            "axis_validation": axis_validation,
            "family_resolution": {
                "mode": "grain_local_axis_pair",
                "plane1_axis_candidates": plane1_axis_candidates,
                "plane2_axis_candidates": plane2_axis_candidates,
                "candidate_count": len(candidates),
                "distinct_misorientation_degrees": distinct_angles,
                "ambiguous": ambiguous,
                "selected": selected,
                "candidates": candidates[:24],
                "candidates_truncated": len(candidates) > 24,
            },
            "geometry_classification": classification,
            "tilt_compatible": tilt_compatible,
            "sigma_assigned": False,
            "caveat": (
                "plane1 and plane2 are grain-local Miller normals. Use "
                "resolved_geometry.plane1_axis and plane2_axis independently "
                "for construction. Use shared_frame_geometry only for tools "
                "that require both planes and one axis in a common reference "
                "frame. raw_input_index_cross_product is diagnostic only."
            ),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    left = left_reference
    right = right_reference
    family_candidates: list[dict[str, object]] = []
    family_candidate_count = 0
    raw_family_candidate_count = 0
    family_equivalence_classes: list[dict[str, object]] = []
    if plane1_is_family or plane2_is_family:
        left_members = (
            _cubic_family_members(left_reference)
            if plane1_is_family
            else [left_reference]
        )
        right_members = (
            _cubic_family_members(right_reference)
            if plane2_is_family
            else [right_reference]
        )
        unique_left = {
            _canonical_unoriented_miller(member) for member in left_members
        }
        unique_right = {
            _canonical_unoriented_miller(member) for member in right_members
        }
        ranked: list[
            tuple[tuple[object, ...], np.ndarray, np.ndarray, np.ndarray, dict[str, object]]
        ] = []
        for left_member in sorted(unique_left):
            candidate_left = _signed_like(
                np.array(left_member, dtype=int), left_reference
            )
            for right_member in sorted(unique_right):
                candidate_right = _signed_like(
                    np.array(right_member, dtype=int), right_reference
                )
                cross = np.cross(candidate_left, candidate_right)
                if not np.any(cross):
                    continue
                candidate_axis = np.array(_reduced_miller(cross), dtype=int)
                if supplied_axis is not None:
                    if axis_is_family:
                        axis_matches = (
                            _cubic_family_signature(candidate_axis)
                            == _cubic_family_signature(supplied_axis)
                        )
                    else:
                        axis_matches = bool(
                            np.all(np.cross(candidate_axis, supplied_axis) == 0)
                        )
                    if not axis_matches:
                        continue
                _, candidate_angle = _plane_angle_degrees(
                    candidate_left, candidate_right
                )
                _, left_deviation = _plane_angle_degrees(
                    left_reference, candidate_left
                )
                _, right_deviation = _plane_angle_degrees(
                    right_reference, candidate_right
                )
                row: dict[str, object] = {
                    "plane1": candidate_left.tolist(),
                    "plane2": candidate_right.tolist(),
                    "selected_concrete_axis": candidate_axis.tolist(),
                    "tilt_misorientation_degrees": round(candidate_angle, 8),
                }
                rank_key: tuple[object, ...] = (
                    round(left_deviation + right_deviation, 12),
                    round(max(left_deviation, right_deviation), 12),
                    tuple(candidate_left.tolist()),
                    tuple(candidate_right.tolist()),
                )
                ranked.append(
                    (
                        rank_key,
                        candidate_left,
                        candidate_right,
                        candidate_axis,
                        row,
                    )
                )
        ranked.sort(key=lambda item: item[0])
        if ranked:
            raw_family_candidate_count = len(ranked)
            grouped: dict[
                tuple[tuple[int, int, int], tuple[int, int, int]],
                list[
                    tuple[
                        tuple[object, ...],
                        np.ndarray,
                        np.ndarray,
                        np.ndarray,
                        dict[str, object],
                    ]
                ],
            ] = {}
            for item in ranked:
                orbit_key = _ordered_unoriented_plane_pair_orbit_key(
                    item[1],
                    item[2],
                    fixed_axis=(
                        supplied_axis
                        if supplied_axis is not None and not axis_is_family
                        else None
                    ),
                )
                grouped.setdefault(orbit_key, []).append(item)

            class_items = sorted(
                grouped.items(),
                key=lambda entry: entry[1][0][0],
            )
            representatives = [items[0] for _, items in class_items]
            _, left, right, _, _ = representatives[0]
            family_candidate_count = len(representatives)
            family_candidates = [item[4] for item in representatives[:24]]
            remaining_variant_rows = 24
            for orbit_key, items in class_items[:24]:
                displayed_variants = [
                    item[4] for item in items[:remaining_variant_rows]
                ]
                remaining_variant_rows -= len(displayed_variants)
                family_equivalence_classes.append(
                    {
                        "orbit_key": [list(orbit_key[0]), list(orbit_key[1])],
                        "representative": items[0][4],
                        "variant_count": len(items),
                        "variants": displayed_variants,
                        "variants_truncated": (
                            len(items) > len(displayed_variants)
                        ),
                    }
                )

    oriented_angle, plane_angle = _plane_angle_degrees(left, right)
    intersection = np.cross(left, right)
    has_unique_intersection = bool(np.any(intersection))
    derived_axis = (
        np.array(_reduced_miller(intersection), dtype=int)
        if has_unique_intersection
        else None
    )
    requested_family_matches = (
        _cubic_family_signature(supplied_axis)
        == _cubic_family_signature(derived_axis)
        if axis_is_family
        and supplied_axis is not None
        and derived_axis is not None
        else None
    )
    resolved_axis = (
        derived_axis
        if axis_is_family and derived_axis is not None
        else supplied_axis if supplied_axis is not None else derived_axis
    )

    axis_validation: dict[str, object] | None = None
    tilt_compatible = False
    if resolved_axis is not None:
        dot1 = int(np.dot(left, resolved_axis))
        dot2 = int(np.dot(right, resolved_axis))
        matches_derived = (
            bool(
                np.all(
                    np.cross(resolved_axis, derived_axis)
                    == np.zeros(3, dtype=int)
                )
            )
            if derived_axis is not None
            else None
        )
        tilt_compatible = bool(
            has_unique_intersection
            and dot1 == 0
            and dot2 == 0
            and matches_derived
            and requested_family_matches is not False
        )
        axis_validation = {
            "source": (
                "supplied_family"
                if axis_is_family
                else "supplied" if supplied_axis is not None else "derived"
            ),
            "axis": resolved_axis.tolist(),
            "plane1_dot": dot1,
            "plane2_dot": dot2,
            "plane1_perpendicular": dot1 == 0,
            "plane2_perpendicular": dot2 == 0,
            "matches_derived_common_axis": matches_derived,
        }
        if axis_is_family and supplied_axis is not None:
            axis_validation.update(
                {
                    "requested_axis_family": supplied_axis.tolist(),
                    "matches_requested_axis_family": requested_family_matches,
                    "selected_concrete_axis": resolved_axis.tolist(),
                }
            )

    if not has_unique_intersection:
        classification = "parallel_planes_no_unique_tilt_axis"
    elif tilt_compatible:
        classification = "pure_tilt_geometry"
    elif axis_is_family:
        classification = "supplied_axis_family_incompatible_with_tilt_geometry"
    else:
        classification = "supplied_axis_incompatible_with_tilt_geometry"

    payload = {
        "method": "plane-normal intersection and deterministic axis validation",
        "input": {
            "plane1": left_reference.tolist(),
            "plane2": right_reference.tolist(),
            "axis": supplied_axis.tolist() if supplied_axis is not None else None,
            "plane1_is_family": plane1_is_family,
            "plane2_is_family": plane2_is_family,
            "axis_is_family": axis_is_family,
        },
        "resolved_geometry": {
            "plane1": left.tolist(),
            "plane2": right.tolist(),
            "axis": resolved_axis.tolist() if resolved_axis is not None else None,
        },
        "angles": {
            "oriented_normal_degrees": round(oriented_angle, 8),
            "unoriented_plane_degrees": round(plane_angle, 8),
            "tilt_misorientation_degrees": (
                round(plane_angle, 8) if tilt_compatible else None
            ),
        },
        "derived_common_axis": (
            derived_axis.tolist() if derived_axis is not None else None
        ),
        "axis_validation": axis_validation,
        "family_resolution": (
            {
                "raw_candidate_count": raw_family_candidate_count,
                "candidate_count": family_candidate_count,
                "equivalence_class_count": family_candidate_count,
                "ambiguous": family_candidate_count > 1,
                "selected": family_candidates[0] if family_candidates else None,
                "candidates": family_candidates,
                "candidates_truncated": family_candidate_count > len(family_candidates),
                "equivalence_classes": family_equivalence_classes,
                "equivalence_classes_truncated": (
                    family_candidate_count > len(family_equivalence_classes)
                ),
                "equivalence_convention": (
                    "ordered unoriented plane pairs modulo proper cubic symmetry"
                    + (
                        " preserving the supplied exact axis line; "
                        if supplied_axis is not None and not axis_is_family
                        else "; "
                    )
                    + "the derived tilt line is unoriented"
                ),
            }
            if plane1_is_family or plane2_is_family
            else None
        ),
        "geometry_classification": classification,
        "tilt_compatible": tilt_compatible,
        "sigma_assigned": False,
        "caveat": (
            "The plane-pair angle is a tilt misorientation only for validated "
            "pure-tilt geometry in the stated coordinate convention. Geometry "
            "analysis alone does not establish a CSL Sigma."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


@tool
def compare_miller_planes(
    plane1: list[int],
    plane2: list[int] | None = None,
    axis: list[int] | None = None,
    reference_family: list[int] | None = None,
) -> str:
    """Reduce one or compare two Miller plane normals without CSL enumeration.

    ``plane2`` is optional.  With one plane, the tool can reduce the normal,
    check it against an optional axis, and classify it against an optional
    cubic ``reference_family`` such as ``[3, 3, 1]`` for ``{331}``.  With two
    planes, it additionally returns oriented and crystallographic plane-normal
    angles.  When an axis is supplied, family candidates are restricted to
    normals perpendicular to that axis when possible.
    """
    try:
        left = _miller_vector("plane1", plane1)
        right = _miller_vector("plane2", plane2) if plane2 is not None else None
        axis_vector = _miller_vector("axis", axis) if axis is not None else None
        family = (
            _miller_vector("reference_family", reference_family)
            if reference_family is not None
            else None
        )
    except ValueError as exc:
        return str(exc)

    payload: dict[str, object] = {
        "plane1": {
            "input": left.tolist(),
            "reduced": _reduced_miller(left),
        },
    }

    if right is not None:
        oriented, plane_angle = _plane_angle_degrees(left, right)
        payload["plane2"] = {
            "input": right.tolist(),
            "reduced": _reduced_miller(right),
        }
        payload["angle_degrees"] = {
            "oriented_normals": round(oriented, 8),
            "unoriented_planes": round(plane_angle, 8),
        }

    if axis_vector is not None:
        axis_payload: dict[str, object] = {
            "input": axis_vector.tolist(),
            "reduced": _reduced_miller(axis_vector),
            "plane1_dot": int(np.dot(left, axis_vector)),
            "plane1_perpendicular": bool(np.dot(left, axis_vector) == 0),
        }
        if right is not None:
            axis_payload.update(
                {
                    "plane2_dot": int(np.dot(right, axis_vector)),
                    "plane2_perpendicular": bool(np.dot(right, axis_vector) == 0),
                }
            )
        payload["axis"] = axis_payload

    if family is not None:
        family_signature = sorted(abs(value) for value in _reduced_miller(family))
        family_membership: dict[str, object] = {
            "reference_family": _reduced_miller(family),
            "plane1_exact_member": (
                sorted(abs(value) for value in _reduced_miller(left))
                == family_signature
            ),
        }
        if right is not None:
            family_membership["plane2_exact_member"] = (
                sorted(abs(value) for value in _reduced_miller(right))
                == family_signature
            )
        payload["family_membership"] = family_membership
        candidates = _cubic_family_members(family)
        if axis_vector is not None:
            perpendicular = [
                candidate
                for candidate in candidates
                if int(np.dot(candidate, axis_vector)) == 0
            ]
            if perpendicular:
                candidates = perpendicular
        comparison_target = right if right is not None else left
        comparisons: list[dict[str, object]] = []
        for candidate in candidates:
            candidate_oriented, candidate_plane = _plane_angle_degrees(
                comparison_target,
                candidate,
            )
            comparisons.append(
                {
                    "member": candidate.tolist(),
                    "oriented_angle_degrees": round(candidate_oriented, 8),
                    "unoriented_plane_angle_degrees": round(candidate_plane, 8),
                }
            )
        comparisons.sort(
            key=lambda item: (
                float(item["unoriented_plane_angle_degrees"]),
                item["member"],
            )
        )
        payload["closest_reference_family_target"] = (
            "plane2" if right is not None else "plane1"
        )
        payload["closest_reference_family_members"] = comparisons[:12]

    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


@tool
def score_csl_tilt_plane_pairs(
    axis: list[int],
    basis: str,
    plane1: list[int],
    plane2: list[int],
    candidate_pairs: list[CslTiltPlanePair],
    max_misorientation_delta_degrees: float = 5.0,
    max_plane_deviation_degrees: float = 5.0,
    max_results: int = 20,
) -> str:
    """Score supplied exact CSL tilt pairs against a general ordered pair.

    This is the composable scoring stage below ``match_csl_tilt_references``.
    It does not enumerate Sigma values or generate GB plane tables. Candidate
    pairs should come from basis-aware CSL plane tools. Each candidate Sigma is
    revalidated for ``basis`` and ``axis`` before fixed-axis cubic symmetries,
    grain assignment, angle tolerance, and per-side plane tolerances are scored.
    """
    basis_norm = basis.strip().lower()
    if basis_norm not in VALID_BASIS:
        return f"Invalid basis '{basis}'. Use one of: {', '.join(VALID_BASIS)}."
    try:
        axis_vector = np.array(
            _reduced_miller(_miller_vector("axis", axis)), dtype=int
        )
        target1 = np.array(
            _reduced_miller(_miller_vector("plane1", plane1)), dtype=int
        )
        target2 = np.array(
            _reduced_miller(_miller_vector("plane2", plane2)), dtype=int
        )
        angle_tolerance = float(max_misorientation_delta_degrees)
        plane_tolerance = float(max_plane_deviation_degrees)
        result_limit = int(max_results)
    except (TypeError, ValueError) as exc:
        return f"Invalid CSL tilt-plane scoring input: {exc}"

    if not candidate_pairs:
        return "candidate_pairs must contain at least one exact CSL tilt-plane pair"
    if len(candidate_pairs) > MAX_CSL_SCORE_SOURCE_PAIRS:
        return (
            f"candidate_pairs accepts at most {MAX_CSL_SCORE_SOURCE_PAIRS} rows"
        )
    if not 0.0 <= angle_tolerance <= 45.0:
        return "max_misorientation_delta_degrees must be between 0 and 45"
    if not 0.0 <= plane_tolerance <= 45.0:
        return "max_plane_deviation_degrees must be between 0 and 45"
    if not 1 <= result_limit <= MAX_CSL_SCORE_RESULTS:
        return f"max_results must be between 1 and {MAX_CSL_SCORE_RESULTS}"

    dot1 = int(np.dot(target1, axis_vector))
    dot2 = int(np.dot(target2, axis_vector))
    if dot1 != 0 or dot2 != 0:
        return (
            "CSL tilt-plane scoring supports pure tilt pairs only; both target "
            f"normals must be perpendicular to axis {axis_vector.tolist()} "
            f"(plane1_dot={dot1}, plane2_dot={dot2})"
        )

    oriented_angle, target_angle = _plane_angle_degrees(target1, target2)
    ranked: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    rejected_inputs: list[dict[str, Any]] = []
    eligible: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    for source_index, source in enumerate(candidate_pairs):
        boundary_type = source.boundary_type.strip() or "Tilt"
        if "tilt" not in boundary_type.lower():
            rejected_inputs.append(
                {
                    "source_index": source_index,
                    "sigma": source.sigma,
                    "error": f"boundary_type {boundary_type!r} is not a tilt class",
                }
            )
            continue
        try:
            exact1 = np.array(
                _reduced_miller(_miller_vector("candidate plane1", source.plane1)),
                dtype=int,
            )
            exact2 = np.array(
                _reduced_miller(_miller_vector("candidate plane2", source.plane2)),
                dtype=int,
            )
            theta, rotation_m, rotation_n = _resolve_plane_pair_rotation(
                axis_vector,
                basis_norm,
                source,
                exact1,
                exact2,
            )
        except ValueError as exc:
            rejected_inputs.append(
                {
                    "source_index": source_index,
                    "sigma": source.sigma,
                    "error": str(exc),
                }
            )
            continue
        candidate_dot1 = int(np.dot(exact1, axis_vector))
        candidate_dot2 = int(np.dot(exact2, axis_vector))
        if candidate_dot1 != 0 or candidate_dot2 != 0:
            rejected_inputs.append(
                {
                    "source_index": source_index,
                    "sigma": source.sigma,
                    "error": (
                        "candidate pair is not perpendicular to the tilt axis "
                        f"(plane1_dot={candidate_dot1}, plane2_dot={candidate_dot2})"
                    ),
                }
            )
            continue

        theta_degrees = degrees(theta)
        angle_delta = abs(theta_degrees - target_angle)
        variants = _exact_pair_deviation_matches(
            target1, target2, axis_vector, exact1, exact2
        )
        if not variants:
            continue
        match = dict(variants[0])
        plane_max = float(match["max_plane_deviation_degrees"])
        plane_total = float(match["total_plane_deviation_degrees"])
        within_angle = bool(angle_delta <= angle_tolerance + 1.0e-10)
        within_plane = bool(plane_max <= plane_tolerance + 1.0e-10)
        exact_geometry = bool(angle_delta < 1.0e-8 and plane_max < 1.0e-8)
        if exact_geometry:
            classification = "exact_csl_match"
        elif within_angle and within_plane:
            classification = "near_csl_reference"
        elif within_angle:
            classification = "angle_near_but_planes_outside_tolerance"
        elif within_plane:
            classification = "planes_near_but_angle_outside_tolerance"
        else:
            classification = "outside_angle_and_plane_tolerances"

        result = {
            "source_index": source_index,
            "sigma": int(source.sigma),
            "rotation_m": int(rotation_m),
            "rotation_n": int(rotation_n),
            "theta_degrees": round(theta_degrees, 8),
            "misorientation_delta_degrees": round(angle_delta, 8),
            "boundary_type": boundary_type,
            "reference_atom_count": source.reference_atom_count,
            **match,
            "within_misorientation_tolerance": within_angle,
            "within_plane_tolerance": within_plane,
            "eligible_as_near_csl_reference": bool(within_angle and within_plane),
            "classification": classification,
        }
        rank_key = (
            classification != "exact_csl_match",
            not (within_angle and within_plane),
            round(plane_max, 12),
            round(plane_total, 12),
            round(angle_delta, 12),
            int(source.sigma),
            tuple(match["exact_plane1"]),
            tuple(match["exact_plane2"]),
            source_index,
        )
        ranked.append((rank_key, result))
        if within_angle and within_plane:
            eligible.append((rank_key, result))

    ranked.sort(key=lambda item: item[0])
    eligible.sort(key=lambda item: item[0])
    selected_reference = eligible[0][1] if eligible else None
    if selected_reference is None:
        search_classification = "no_near_csl_reference_in_supplied_pairs"
    elif selected_reference["classification"] == "exact_csl_match":
        search_classification = "exact_csl_match"
    else:
        search_classification = "near_csl_reference_found"

    payload = {
        "method": (
            "score supplied basis-valid exact CSL tilt pairs under fixed-axis "
            "cubic symmetry; no Sigma enumeration or plane-table generation"
        ),
        "scope": "tilt",
        "basis": basis_norm,
        "axis": axis_vector.tolist(),
        "input": {
            "plane1": target1.tolist(),
            "plane2": target2.tolist(),
            "plane1_axis_dot": dot1,
            "plane2_axis_dot": dot2,
            "oriented_normal_angle_degrees": round(oriented_angle, 8),
            "misorientation_degrees": round(target_angle, 8),
            "candidate_pair_count": len(candidate_pairs),
        },
        "criteria": {
            "max_misorientation_delta_degrees": angle_tolerance,
            "max_plane_deviation_degrees": plane_tolerance,
            "max_results": result_limit,
        },
        "reference_selection_rule": (
            "Require both angle and worst-side plane tolerances, then rank by "
            "exactness, worst-side deviation, total deviation, angle delta, and Sigma."
        ),
        "search_classification": search_classification,
        "selected_reference": selected_reference,
        "scored_pairs": [item[1] for item in ranked[:result_limit]],
        "scored_pair_count": len(ranked),
        "omitted_scored_pair_count": max(0, len(ranked) - result_limit),
        "rejected_inputs": rejected_inputs,
        "sigma_caveat": (
            "A nonzero-deviation Sigma is a geometric reference only. This tool "
            "validates supplied Sigma provenance against basis and axis but does "
            "not prove that the supplied candidate list is exhaustive."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


@tool
def match_csl_tilt_references(
    axis: list[int],
    basis: str,
    plane1: list[int],
    plane2: list[int],
    axis_is_family: bool = False,
    sigma_limit: int = 50,
    plane_limit: int = 5,
    max_plane_matches_per_sigma: int = 3,
    max_misorientation_delta_degrees: float = 5.0,
    max_plane_deviation_degrees: float = 5.0,
) -> str:
    """Reverse-map a supplied general tilt GB to nearby exact CSL references.

    The supplied plane normals are treated as an ordered grain-1/grain-2 pair.
    With an exact ``axis``, both normals must already use a shared crystal frame
    and be perpendicular to that axis. With ``axis_is_family=True``, the normals
    are treated as grain-local: the tool resolves one family member per grain and
    applies a proper cubic symmetry before comparing them. The resulting
    plane-normal angle defines the target tilt misorientation. The tool then enumerates exact
    CSL rotations for ``basis`` through ``sigma_limit`` and keeps every rotation
    inside the misorientation tolerance. It then generates each candidate's
    bounded-complexity exact tilt-plane table and compares fixed-axis
    symmetry-equivalent plane pairs side by side. A reference is eligible only
    when its worst per-side plane deviation also satisfies the plane tolerance.

    This is the reverse direction of ``suggest_low_index_near_csl_planes``:
    general/non-Sigma input → nearby exact Sigma references. A nonzero angle or
    plane deviation does not assign that Sigma to the supplied boundary.
    """
    basis_norm = basis.strip().lower()
    if basis_norm not in VALID_BASIS:
        return f"Invalid basis '{basis}'. Use one of: {', '.join(VALID_BASIS)}."
    try:
        axis_reference = np.array(
            _reduced_miller(_miller_vector("axis", axis)), dtype=int
        )
        target1_local = np.array(
            _reduced_miller(_miller_vector("plane1", plane1)), dtype=int
        )
        target2_local = np.array(
            _reduced_miller(_miller_vector("plane2", plane2)), dtype=int
        )
        resolved_sigma_limit = int(sigma_limit)
        resolved_plane_limit = int(plane_limit)
        plane_match_limit = int(max_plane_matches_per_sigma)
        angle_tolerance = float(max_misorientation_delta_degrees)
        plane_tolerance = float(max_plane_deviation_degrees)
    except (TypeError, ValueError) as exc:
        return f"Invalid reverse near-CSL input: {exc}"

    if not 2 <= resolved_sigma_limit <= MAX_REVERSE_CSL_SIGMA_LIMIT:
        return (
            f"sigma_limit must be between 2 and {MAX_REVERSE_CSL_SIGMA_LIMIT}"
        )
    if not 1 <= resolved_plane_limit <= MAX_GB_PLANE_LIMIT:
        return f"plane_limit must be between 1 and {MAX_GB_PLANE_LIMIT}"
    if not 1 <= plane_match_limit <= MAX_REVERSE_CSL_PLANE_MATCHES:
        return (
            "max_plane_matches_per_sigma must be between 1 and "
            f"{MAX_REVERSE_CSL_PLANE_MATCHES}"
        )
    if not 0.0 <= angle_tolerance <= 45.0:
        return "max_misorientation_delta_degrees must be between 0 and 45"
    if not 0.0 <= plane_tolerance <= 45.0:
        return "max_plane_deviation_degrees must be between 0 and 45"

    grain_local_resolution: dict[str, Any] | None = None
    plane2_to_local_symmetry: np.ndarray | None = None
    if axis_is_family:
        family_candidates, plane1_axis_candidates, plane2_axis_candidates = (
            _grain_local_axis_family_candidates(
                target1_local,
                target2_local,
                axis_reference,
            )
        )
        distinct_angles = sorted(
            {
                float(candidate["tilt_misorientation_degrees"])
                for candidate in family_candidates
            }
        )
        if not family_candidates:
            return json.dumps(
                {
                    "search_classification": "axis_family_incompatible",
                    "axis_family": axis_reference.tolist(),
                    "plane1": target1_local.tolist(),
                    "plane2": target2_local.tolist(),
                    "plane1_axis_candidates": plane1_axis_candidates,
                    "plane2_axis_candidates": plane2_axis_candidates,
                    "error": (
                        "No requested-family member lies in each ordered "
                        "grain-local plane under cubic symmetry."
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        if len(distinct_angles) > 1:
            return json.dumps(
                {
                    "search_classification": "ambiguous_axis_family_resolution",
                    "axis_family": axis_reference.tolist(),
                    "plane1": target1_local.tolist(),
                    "plane2": target2_local.tolist(),
                    "distinct_misorientation_degrees": distinct_angles,
                    "candidates": family_candidates[:24],
                    "error": (
                        "The axis family admits multiple physically distinct "
                        "tilt misorientations; select concrete grain-local axes."
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        selected_family = family_candidates[0]
        axis_vector = np.array(selected_family["plane1_axis"], dtype=int)
        target1 = target1_local
        target2 = np.array(
            selected_family["plane2_in_plane1_frame"], dtype=int
        )
        plane2_to_local_symmetry = np.array(
            selected_family["symmetry_matrix_plane1_to_plane2"], dtype=int
        )
        grain_local_resolution = {
            "axis_family": axis_reference.tolist(),
            "plane1_axis": selected_family["plane1_axis"],
            "plane2_axis": selected_family["plane2_axis"],
            "plane2_in_plane1_frame": selected_family[
                "plane2_in_plane1_frame"
            ],
            "symmetry_matrix_plane1_to_plane2": selected_family[
                "symmetry_matrix_plane1_to_plane2"
            ],
            "candidate_count": len(family_candidates),
            "distinct_misorientation_degrees": distinct_angles,
        }
    else:
        axis_vector = axis_reference
        target1 = target1_local
        target2 = target2_local

    dot1 = int(np.dot(target1, axis_vector))
    dot2 = int(np.dot(target2, axis_vector))
    if dot1 != 0 or dot2 != 0:
        return (
            "reverse near-CSL analysis supports pure tilt pairs only; both plane "
            f"normals must be perpendicular to axis {axis_vector.tolist()} "
            f"(plane1_dot={dot1}, plane2_dot={dot2})"
        )

    oriented_angle, target_angle = _plane_angle_degrees(target1, target2)
    sigma_rows: list[tuple[float, int, float, int, int]] = []
    for sigma, theta, m, n in csl.list_csl_rotations_for_axis(
        axis_vector, basis_norm, resolved_sigma_limit
    ):
        theta_degrees = degrees(theta)
        angle_delta = abs(theta_degrees - target_angle)
        if angle_delta <= angle_tolerance + 1.0e-10:
            sigma_rows.append(
                (angle_delta, sigma, theta_degrees, int(m), int(n))
            )
    sigma_rows.sort(key=lambda item: (round(item[0], 8), item[1], item[0]))

    candidates: list[dict[str, Any]] = []
    eligible_reference_matches: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for rank, (angle_delta, sigma, theta_degrees, m, n) in enumerate(
        sigma_rows, start=1
    ):
        exact1, exact2, plane_types, atom_counts = (
            csl.Create_Filtered_GB_Plane_List(
                axis_vector,
                basis_norm,
                m,
                n,
                resolved_plane_limit,
                parallel_filtering=False,
                gb_type="tilt",
            )
        )
        pair_matches: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        for table1, table2, plane_type, atom_count in zip(
            exact1, exact2, plane_types, atom_counts, strict=True
        ):
            variants = _exact_pair_deviation_matches(
                target1,
                target2,
                axis_vector,
                np.asarray(table1),
                np.asarray(table2),
            )
            if not variants:
                continue
            best_variant = dict(variants[0])
            if plane2_to_local_symmetry is not None:
                exact_plane2_shared = np.array(
                    best_variant["exact_plane2"], dtype=int
                )
                exact_plane2_local = np.array(
                    _reduced_miller(
                        plane2_to_local_symmetry @ exact_plane2_shared
                    ),
                    dtype=int,
                )
                exact_plane2_local = _signed_like(
                    exact_plane2_local, target2_local
                )
                best_variant.update(
                    {
                        "coordinate_mode": "grain_local_ordered_planes",
                        "exact_plane1_local": best_variant["exact_plane1"],
                        "exact_plane2_local": exact_plane2_local.tolist(),
                        "exact_plane1_shared": best_variant["exact_plane1"],
                        "exact_plane2_shared": best_variant["exact_plane2"],
                    }
                )
            best_variant.update(
                {
                    "boundary_type": str(plane_type),
                    "reference_atom_count": int(atom_count),
                    "within_plane_tolerance": bool(
                        float(best_variant["max_plane_deviation_degrees"])
                        <= plane_tolerance + 1.0e-10
                    ),
                }
            )
            exact_geometry = bool(
                angle_delta < 1.0e-8
                and float(best_variant["max_plane_deviation_degrees"]) < 1.0e-8
            )
            if exact_geometry:
                classification = "exact_csl_match"
            elif best_variant["within_plane_tolerance"]:
                classification = "near_csl_reference"
            else:
                classification = "angle_near_but_planes_outside_tolerance"
            best_variant["classification"] = classification
            plane_rank = (
                round(float(best_variant["max_plane_deviation_degrees"]), 12),
                round(float(best_variant["total_plane_deviation_degrees"]), 12),
                round(float(best_variant["plane1_deviation_degrees"]), 12),
                round(float(best_variant["plane2_deviation_degrees"]), 12),
                str(best_variant["boundary_type"]),
                tuple(best_variant["exact_plane1"]),
                tuple(best_variant["exact_plane2"]),
            )
            pair_matches.append((plane_rank, best_variant))
        pair_matches.sort(key=lambda item: item[0])
        selected_matches = [
            item[1] for item in pair_matches[:plane_match_limit]
        ]
        candidate = {
            "rotation_angle_rank": rank,
            "sigma": sigma,
            "rotation_m": m,
            "rotation_n": n,
            "theta_degrees": round(theta_degrees, 8),
            "misorientation_delta_degrees": round(angle_delta, 8),
            "exact_tilt_plane_pair_count": len(exact1),
            "plane_limit": resolved_plane_limit,
            "best_plane_matches": selected_matches,
            "eligible_as_near_csl_reference": bool(
                selected_matches
                and str(selected_matches[0]["classification"])
                in {"exact_csl_match", "near_csl_reference"}
            ),
        }
        candidates.append(candidate)
        for plane_rank, match in pair_matches:
            result = {
                "sigma": sigma,
                "rotation_m": m,
                "rotation_n": n,
                "theta_degrees": round(theta_degrees, 8),
                "misorientation_delta_degrees": round(angle_delta, 8),
                **match,
            }
            global_rank = (
                *plane_rank[:2],
                round(angle_delta, 12),
                sigma,
                *plane_rank[2:],
            )
            if str(match["classification"]) in {
                "exact_csl_match",
                "near_csl_reference",
            }:
                eligible_reference_matches.append(
                    (
                        (
                            match["classification"] != "exact_csl_match",
                            *global_rank,
                        ),
                        result,
                    )
                )

    eligible_reference_matches.sort(key=lambda item: item[0])
    selected_reference = (
        eligible_reference_matches[0][1] if eligible_reference_matches else None
    )

    if selected_reference is None:
        search_classification = "no_near_csl_reference_within_search_bounds"
    elif selected_reference["classification"] == "exact_csl_match":
        search_classification = "exact_csl_match"
    else:
        search_classification = "near_csl_reference_found"

    payload = {
        "method": (
            "reverse general-tilt lookup: plane-pair misorientation → exact CSL "
            "rotation candidates → bounded exact tilt-plane tables → joint "
            "rotation/plane-tolerance selection"
        ),
        "scope": "tilt",
        "basis": basis_norm,
        "axis": axis_vector.tolist(),
        "axis_is_family": axis_is_family,
        "grain_local_resolution": grain_local_resolution,
        "input": {
            "plane1": target1_local.tolist(),
            "plane2": target2_local.tolist(),
            "plane1_in_shared_frame": target1.tolist(),
            "plane2_in_shared_frame": target2.tolist(),
            "plane1_axis_dot": dot1,
            "plane2_axis_dot": dot2,
            "oriented_normal_angle_degrees": round(oriented_angle, 8),
            "misorientation_degrees": round(target_angle, 8),
        },
        "criteria": {
            "sigma_limit": resolved_sigma_limit,
            "plane_limit": resolved_plane_limit,
            "max_plane_matches_per_sigma": plane_match_limit,
            "max_misorientation_delta_degrees": angle_tolerance,
            "max_plane_deviation_degrees": plane_tolerance,
        },
        "reference_selection_rule": (
            "Enumerate every basis-validated Sigma rotation inside the "
            "misorientation tolerance, scan each exact tilt-plane table, require "
            "the worst per-side plane deviation to satisfy its tolerance, then "
            "rank by exactness, worst-side deviation, total deviation, and angle delta."
        ),
        "search_classification": search_classification,
        "selected_reference": selected_reference,
        "candidates": candidates,
        "sigma_caveat": (
            "A selected Sigma is a geometric reference, not an assignment. The "
            "supplied boundary retains that Sigma only when both misorientation "
            "and exact plane-pair deviations are zero within numerical tolerance. "
            "The plane_limit bounds exact-plane complexity; search bounds do not "
            "prove absence at higher Sigma or plane complexity."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


# Rows one run-sourced screening reads; far above a full plane table at limit 6.
MAX_RUN_SOURCE_ROWS = 2000
# Compact model-facing views stay inside the model-boundary result budget.
MODEL_VIEW_CHARS = 14_000
_NEAR_CSL_SIGMA_CAVEAT = (
    "Sigma values label only the exact source geometry. Any suggestion with a "
    "nonzero deviation is not proven to retain that Sigma and must be described "
    "as a low-index near-CSL candidate referenced to the source Sigma."
)
_NEAR_CSL_DEDUPLICATION_RULE = (
    "Within one source branch, two suggestions are one candidate when a cubic symmetry "
    "that keeps the tilt-axis line maps one plane pair onto the other, each plane normal "
    "taken without sign, in the same or grain-swapped order (equivalent Miller indices "
    "applied to the pair jointly). Equal family labels alone never merge candidates."
)


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _suggestion_quality(suggestion: dict[str, Any]) -> tuple[float, float, float]:
    first = suggestion["plane1"]["deviation_degrees"]
    second = suggestion["plane2"]["deviation_degrees"]
    return max(first, second), first + second, suggestion["angle_degrees"]["delta"]


def _merge_near_csl_candidates(
    axis: np.ndarray, results: list[dict[str, Any]], *, deduplicate: bool,
) -> list[dict[str, Any]]:
    """Group the per-row suggestions of each source branch into unique candidates."""
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for result in results:
        branch = (result["sigma_reference"], result.get("rotation_m"), result.get("rotation_n"))
        for rank, suggestion in enumerate(result.get("suggestions") or [], 1):
            plane1 = np.array(suggestion["plane1"]["concrete"], dtype=int)
            plane2 = np.array(suggestion["plane2"]["concrete"], dtype=int)
            ordered = _ordered_unoriented_plane_pair_orbit_key(plane1, plane2, fixed_axis=axis)
            identity = (
                min(ordered, _ordered_unoriented_plane_pair_orbit_key(plane2, plane1, fixed_axis=axis))
                if deduplicate else (result["row"], rank)
            )
            groups.setdefault((branch, identity), []).append(
                {"result": result, "rank": rank, "suggestion": suggestion, "ordered": ordered}
            )
    branches_by_identity: dict[Any, list[tuple[Any, ...]]] = {}
    for branch, identity in groups:
        branches_by_identity.setdefault(identity, []).append(branch)

    candidates = []
    for (branch, identity), members in groups.items():
        best = min(members, key=lambda member: _suggestion_quality(member["suggestion"]))
        suggestion = best["suggestion"]
        others = [other for other in branches_by_identity[identity] if other != branch]
        candidates.append({
            "sigma_reference": branch[0],
            "rotation_m": branch[1],
            "rotation_n": branch[2],
            "plane1": suggestion["plane1"],
            "plane2": suggestion["plane2"],
            "angle_degrees": suggestion["angle_degrees"],
            "classification": suggestion["classification"],
            "source_rows": [{
                "row": member["result"]["row"],
                "rank": member["rank"],
                "source_plane1": member["result"]["source_plane1"],
                "source_plane2": member["result"]["source_plane2"],
                "planes": [member["suggestion"]["plane1"]["concrete"],
                           member["suggestion"]["plane2"]["concrete"]],
                "plane1_deviation_degrees": member["suggestion"]["plane1"]["deviation_degrees"],
                "plane2_deviation_degrees": member["suggestion"]["plane2"]["deviation_degrees"],
                "angle_delta_degrees": member["suggestion"]["angle_degrees"]["delta"],
                "grain_swapped": member["ordered"] != best["ordered"],
            } for member in members],
            **({"also_in_branches": [
                {"sigma": other[0], "rotation_m": other[1], "rotation_n": other[2]}
                for other in others
            ]} if deduplicate and others else {}),
        })
    return candidates


def _near_csl_model_view(payload: dict[str, Any]) -> dict[str, Any]:
    """Bounded per-branch table of every unique candidate for the model."""
    summary = payload["summary"]
    source = payload["source"]
    origin = f" of run {source['run_id']}" if source.get("run_id") else ""
    merged = (f"; {summary['unique_candidates']} unique candidates after merging "
              f"{summary['merged_duplicates']} equivalent or grain-swapped duplicates within "
              f"each branch, {summary['candidates_shared_with_other_branches']} of them also "
              f"found in another branch" if payload["deduplication"]["applied"]
              else f"; {summary['unique_candidates']} suggestions, not deduplicated")
    view: dict[str, Any] = {
        "status": "completed",
        "summary": (
            f"Screened {summary['rows_screened']} exact source rows in "
            f"{summary['branches']} branches{origin}: {summary['rows_with_candidates']} rows have "
            f"a low-index pair within the bounds, {summary['rows_without_candidates']} have none"
            f"{merged}."
        ),
        "criteria": payload["criteria"],
        "source": {key: source[key] for key in ("kind", "run_id", "basis", "plane_limit")
                   if source.get(key) is not None},
        "candidate_columns": [
            "plane1", "plane2", "families", "plane1_deviation_deg", "plane2_deviation_deg",
            "low_index_angle_deg", "angle_delta_deg", "exact", "source_rows",
        ],
        "branches": [],
        "sigma_caveat": _NEAR_CSL_SIGMA_CAVEAT,
    }
    used = len(_compact_json(view))
    omitted = 0
    by_branch: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for candidate in payload["candidates"]:
        key = (candidate["sigma_reference"], candidate["rotation_m"], candidate["rotation_n"])
        by_branch.setdefault(key, []).append(candidate)
    for branch in payload["branches"]:
        entry = {
            "sigma": branch["sigma"], "rotation": [branch["rotation_m"], branch["rotation_n"]],
            **({"theta_deg": round(branch["theta_degrees"], 3)}
               if branch.get("theta_degrees") is not None else {}),
            "rows": branch["rows_screened"], "rows_with_candidates": branch["rows_with_candidates"],
            "candidates": [], "rows_without_candidates": branch["rows_without_candidates"],
            **({"rows_with_errors": branch["rows_with_errors"]} if branch["rows_with_errors"] else {}),
        }
        used += len(_compact_json(entry)) + 1
        for candidate in by_branch.get((branch["sigma"], branch["rotation_m"], branch["rotation_n"]), []):
            row = [
                candidate["plane1"]["concrete"], candidate["plane2"]["concrete"],
                f"{candidate['plane1']['family']}/{candidate['plane2']['family']}",
                round(candidate["plane1"]["deviation_degrees"], 3),
                round(candidate["plane2"]["deviation_degrees"], 3),
                round(candidate["angle_degrees"]["low_index_pair"], 3),
                round(candidate["angle_degrees"]["delta"], 3),
                candidate["classification"] == "exact_geometry",
                [source_row["row"] for source_row in candidate["source_rows"]],
            ]
            size = len(_compact_json(row)) + 1
            if omitted or used + size > MODEL_VIEW_CHARS:
                omitted += 1
                continue
            entry["candidates"].append(row)
            used += size
        view["branches"].append(entry)
    if omitted:
        view["omitted_candidates"] = omitted
        view["read_more"] = (
            f"The last {omitted} candidates did not fit; the full output lists every candidate "
            "at /candidates, with all merged source rows and their deviations."
        )
    return view


def screen_near_csl_sources(
    axis: list[int],
    sources: list[dict[str, Any]],
    *,
    max_index: int = 3,
    max_plane_deviation_degrees: float = 5.0,
    max_misorientation_delta_degrees: float = 5.0,
    max_suggestions_per_pair: int = 3,
    deduplicate: bool = True,
    source: dict[str, Any] | None = None,
) -> str:
    """Screen exact source rows and return per-row results, unique candidates and a view.

    Each source row carries ``sigma``, optional ``rotation_m``/``rotation_n``,
    ``plane1``, ``plane2`` and its ``row`` number within its source table.
    """
    try:
        axis_vector = _miller_vector("axis", axis)
    except ValueError as exc:
        return str(exc)
    try:
        index_limit = int(max_index)
        plane_tolerance = float(max_plane_deviation_degrees)
        angle_tolerance = float(max_misorientation_delta_degrees)
        suggestion_limit = int(max_suggestions_per_pair)
    except (TypeError, ValueError):
        return "near-CSL search bounds must be numeric"
    if not 1 <= index_limit <= MAX_LOW_INDEX_LIMIT:
        return f"max_index must be between 1 and {MAX_LOW_INDEX_LIMIT}"
    if not 0.0 <= plane_tolerance <= 45.0:
        return "max_plane_deviation_degrees must be between 0 and 45"
    if not 0.0 <= angle_tolerance <= 45.0:
        return "max_misorientation_delta_degrees must be between 0 and 45"
    if not 1 <= suggestion_limit <= 20:
        return "max_suggestions_per_pair must be between 1 and 20"
    if not sources:
        return "No exact source rows to screen"

    primitive_axis = np.array(_reduced_miller(axis_vector), dtype=int)
    low_index_planes = _low_index_tilt_planes(primitive_axis, index_limit)
    if not low_index_planes:
        return "No low-index plane normal is perpendicular to the supplied tilt axis."

    results = []
    branches: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in sources:
        result = _near_csl_pair_suggestions(
            NearCslSourcePair(
                sigma=row["sigma"], plane1=row["plane1"], plane2=row["plane2"],
                rotation_m=row.get("rotation_m"), rotation_n=row.get("rotation_n"),
            ),
            primitive_axis,
            low_index_planes,
            max_plane_deviation_degrees=plane_tolerance,
            max_misorientation_delta_degrees=angle_tolerance,
            max_suggestions=suggestion_limit,
        )
        result = {"row": int(row["row"]), "rotation_m": row.get("rotation_m"),
                  "rotation_n": row.get("rotation_n"), **result}
        results.append(result)
        key = (row["sigma"], row.get("rotation_m"), row.get("rotation_n"))
        branch = branches.setdefault(key, {
            "sigma": key[0], "rotation_m": key[1], "rotation_n": key[2],
            "theta_degrees": row.get("theta_degrees"), "rows_screened": 0,
            "rows_with_candidates": 0, "rows_without_candidates": [], "rows_with_errors": [],
        })
        branch["rows_screened"] += 1
        if result.get("error"):
            branch["rows_with_errors"].append(result["row"])
        elif result["suggestions"]:
            branch["rows_with_candidates"] += 1
        else:
            branch["rows_without_candidates"].append(result["row"])

    candidates = _merge_near_csl_candidates(primitive_axis, results, deduplicate=deduplicate)
    branch_order = {key: index for index, key in enumerate(branches)}
    candidates.sort(key=lambda item: (
        branch_order[(item["sigma_reference"], item["rotation_m"], item["rotation_n"])],
        item["classification"] != "exact_geometry",
        _suggestion_quality(item),
        item["plane1"]["concrete"], item["plane2"]["concrete"],
    ))
    for branch in branches.values():
        key = (branch["sigma"], branch["rotation_m"], branch["rotation_n"])
        branch["unique_candidates"] = sum(
            1 for item in candidates
            if (item["sigma_reference"], item["rotation_m"], item["rotation_n"]) == key
        )
    suggestion_count = sum(len(result.get("suggestions") or []) for result in results)
    payload = {
        "method": "low-index geometric snapping of exact CSL tilt-plane pairs",
        "gb_type": "tilt",
        "axis": primitive_axis.tolist(),
        "criteria": {
            "max_index": index_limit,
            "max_plane_deviation_degrees": plane_tolerance,
            "max_misorientation_delta_degrees": angle_tolerance,
            "max_suggestions_per_pair": suggestion_limit,
        },
        "candidate_plane_count": len(low_index_planes),
        "source": {**(source or {}), "rows_screened": len(results)},
        "deduplication": {"applied": bool(deduplicate), "rule": _NEAR_CSL_DEDUPLICATION_RULE},
        "summary": {
            "rows_screened": len(results),
            "branches": len(branches),
            "rows_with_candidates": sum(item["rows_with_candidates"] for item in branches.values()),
            "rows_without_candidates": sum(
                len(item["rows_without_candidates"]) for item in branches.values()),
            "rows_with_errors": sum(len(item["rows_with_errors"]) for item in branches.values()),
            "suggestions": suggestion_count,
            "unique_candidates": len(candidates),
            "merged_duplicates": suggestion_count - len(candidates),
            "candidates_shared_with_other_branches": sum(
                1 for item in candidates if item.get("also_in_branches")),
        },
        "branches": list(branches.values()),
        "candidates": candidates,
        "sigma_caveat": _NEAR_CSL_SIGMA_CAVEAT,
        "results": results,
    }
    payload["model_view"] = _near_csl_model_view(payload)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


@tool
def suggest_low_index_near_csl_planes(
    axis: list[int],
    source_pairs: list[NearCslSourcePair] | None = None,
    source_run_id: str | None = None,
    source_branches: list[CslRotationSelection] | None = None,
    max_index: int = 3,
    max_plane_deviation_degrees: float = 5.0,
    max_misorientation_delta_degrees: float = 5.0,
    max_suggestions_per_pair: int = 3,
    deduplicate: bool = True,
    gb_type: Literal["tilt"] = "tilt",
) -> str:
    """Suggest low-index tilt-plane pairs near exact CSL plane-pair geometry.

    This is a deterministic geometric screening tool. It enumerates primitive plane
    normals whose components are no larger than ``max_index`` and which are exactly
    perpendicular to the supplied tilt axis. It jointly approximates both sides of
    every source pair, then reports the per-side plane-normal deviations, the new
    plane-pair angle, and its change from the source pair angle.

    To screen rows of a completed exact enumeration, pass its ``source_run_id``: the
    tool reads every saved ``list_csl_gb_planes``/``list_csl_gb_planes_batch`` row
    for ``axis`` itself, in one call. ``source_branches`` (exact ``sigma``,
    ``rotation_m``, ``rotation_n``) optionally limits it to some branches. Never
    transcribe saved table rows into ``source_pairs``. ``source_pairs`` is for a few
    rows that have no saved run: up to 50 of the form
    ``{"sigma": 9, "plane1": [-1, 1, -1], "plane2": [-11, 11, 1]}``.
    ``sigma`` is required exact-source provenance. A nonzero approximation does not retain
    or prove that Sigma; report the result as a low-index near-CSL candidate referenced to
    the source Sigma. Use ``match_csl_tilt_references`` instead when starting from a
    general/non-Sigma plane pair. Validate physical relevance separately.

    With ``deduplicate`` (default), suggestions of one branch that are the same plane
    pair under a cubic symmetry keeping the tilt-axis line, each normal taken without
    sign and in either grain order, become one candidate listing all its source rows.
    The reply's ``model_view`` lists every unique candidate per branch; the full output
    keeps per-row results (``results``) and candidates (``candidates``).

    ``gb_type`` is intentionally restricted to ``"tilt"``. Twist and mixed
    boundaries require a full misorientation representation; their geometry cannot
    be screened from the angle between two GB plane normals alone.
    """
    if str(gb_type).strip().lower() != "tilt":
        return (
            'suggest_low_index_near_csl_planes supports gb_type="tilt" only; '
            "twist and mixed boundaries require a full misorientation representation"
        )
    if source_run_id and source_pairs:
        return "Pass either source_run_id or source_pairs, not both"
    if source_run_id:
        return (
            "source_run_id is read by the project-aware Structure worker tool; outside a "
            "project, pass the rows as source_pairs"
        )
    if source_branches:
        return "source_branches selects branches of source_run_id; pass both together"
    if not source_pairs:
        return (
            "Pass source_run_id of a completed exact CSL enumeration, or at least one "
            "exact plane pair in source_pairs"
        )
    if len(source_pairs) > MAX_NEAR_CSL_SOURCE_PAIRS:
        return (
            f"Too many source plane pairs ({len(source_pairs)}). Pass at most "
            f"{MAX_NEAR_CSL_SOURCE_PAIRS}, or source_run_id for a saved enumeration."
        )
    return screen_near_csl_sources(
        axis,
        [{**pair.model_dump(), "row": index} for index, pair in enumerate(source_pairs, 1)],
        max_index=max_index,
        max_plane_deviation_degrees=max_plane_deviation_degrees,
        max_misorientation_delta_degrees=max_misorientation_delta_degrees,
        max_suggestions_per_pair=max_suggestions_per_pair,
        deduplicate=deduplicate,
        source={"kind": "source_pairs"},
    )


_PLANE_TABLE_HEADER = re.compile(
    r"^CSL GB planes — axis \[(?P<axis>[-\d, ]+)\], basis=(?P<basis>\w+), "
    r"Sigma=(?P<sigma>\d+), theta=(?P<theta>[-\d.]+) deg, "
    r"rotation=\(m=(?P<m>\d+), n=(?P<n>\d+)\), limit=(?P<limit>\d+):$"
)
_PLANE_TABLE_ROW = re.compile(
    r"^\[(?P<plane1>[-\d\s]+)\]\s+\[(?P<plane2>[-\d\s]+)\]\s+"
    r"(?P<type>Symmetric Tilt|Tilt|Twist|Mixed)\s+(?P<atoms>\d+)$"
)


_BOUNDARY_TYPE_CODES = {"Symmetric Tilt": "ST", "Tilt": "T", "Twist": "TW", "Mixed": "M"}


def plane_table_model_view(text: str) -> dict[str, Any] | None:
    """Every row of a plane-table reply, compactly, for the model.

    The fixed-width text stays the reply, the trace and the saved artifact; this
    view only replaces the generic preview the model would otherwise get.
    """
    notices = []
    body = str(text).strip()
    while body.startswith("Reused ") and "\n" in body:
        notice, body = body.split("\n", 1)
        notices.append(notice)
    rows = parse_gb_plane_tables(body)
    if not rows:
        return None
    first = rows[0]
    branches: dict[tuple[int, int, int], dict[str, Any]] = {}
    for row in rows:
        key = (row["sigma"], row["rotation_m"], row["rotation_n"])
        branches.setdefault(key, {
            "sigma": key[0], "rotation": [key[1], key[2]],
            "theta_deg": round(row["theta_degrees"], 3), "row_count": 0, "rows": [],
        })["row_count"] += 1
    view: dict[str, Any] = {
        "status": "completed",
        "summary": (
            f"{len(rows)} exact GB plane rows in {len(branches)} rotation branches, axis "
            f"{first['axis']}, basis {first['basis']}, plane limit {first['plane_limit']}. "
            "Every row is listed below; the saved artifact keeps the full text table."
        ),
        **({"reused": notices} if notices else {}),
        "row_columns": ["gb1", "gb2", "type", "reference_atoms"],
        "type_codes": {code: label for label, code in _BOUNDARY_TYPE_CODES.items()},
        "branches": list(branches.values()),
    }
    used = len(_compact_json(view))
    omitted = 0
    for row in rows:
        entry = [row["plane1"], row["plane2"],
                 _BOUNDARY_TYPE_CODES.get(row["boundary_type"], row["boundary_type"]),
                 row["reference_atom_count"]]
        size = len(_compact_json(entry)) + 1
        if omitted or used + size > MODEL_VIEW_CHARS:
            omitted += 1
            continue
        branches[(row["sigma"], row["rotation_m"], row["rotation_n"])]["rows"].append(entry)
        used += size
    if omitted:
        view["omitted_rows"] = omitted
        view["summary"] = view["summary"].replace(
            "Every row is listed below;", f"{len(rows) - omitted} of the rows are listed below;")
        view["read_more"] = (
            f"The last {omitted} rows did not fit; read them from full_output with "
            "read_tool_output, or pass gb_type to list fewer rows."
        )
    return view


def parse_gb_plane_tables(text: str) -> list[dict[str, Any]]:
    """Read ``list_csl_gb_planes``/``_batch`` tables back into rows.

    The inverse of ``_format_gb_planes_for_sigma``: each row keeps its branch
    identity and its 1-based position in that branch's table.
    """
    rows: list[dict[str, Any]] = []
    branch: dict[str, Any] | None = None
    for line in text.splitlines():
        line = line.strip()
        header = _PLANE_TABLE_HEADER.match(line)
        if header:
            branch = {
                "axis": [int(value) for value in header["axis"].split(",")],
                "basis": header["basis"],
                "sigma": int(header["sigma"]),
                "theta_degrees": float(header["theta"]),
                "rotation_m": int(header["m"]),
                "rotation_n": int(header["n"]),
                "plane_limit": int(header["limit"]),
                "rows": 0,
            }
            continue
        match = _PLANE_TABLE_ROW.match(line)
        if match and branch is not None:
            branch["rows"] += 1
            rows.append({
                **{key: value for key, value in branch.items() if key != "rows"},
                "row": branch["rows"],
                "plane1": [int(value) for value in match["plane1"].split()],
                "plane2": [int(value) for value in match["plane2"].split()],
                "boundary_type": match["type"],
                "reference_atom_count": int(match["atoms"]),
            })
    return rows


def _format_gb_planes_for_sigma(
    u: int,
    v: int,
    w: int,
    basis: str,
    sigma: int,
    limit: int = 2,
    gb_type: str | None = None,
    *,
    parallel_planes: bool = True,
    rotation_m: int | None = None,
    rotation_n: int | None = None,
) -> str:
    limit = _clamp_gb_plane_limit(limit)
    basis_norm = basis.strip().lower()
    if basis_norm not in VALID_BASIS:
        return f"Invalid basis '{basis}'. Use one of: {', '.join(VALID_BASIS)}."

    if sigma <= 1:
        return (
            "Sigma=1 is the identity orientation (0° misorientation), not a physical "
            "grain boundary. Skip GB plane lookup for Σ1."
        )

    uvw = csl.CommonDivisor(np.array([u, v, w], dtype=int))[0]
    try:
        theta, m, n = csl.resolve_csl_rotation(
            uvw,
            basis_norm,
            sigma,
            rotation_m=rotation_m,
            rotation_n=rotation_n,
        )
    except ValueError as exc:
        return str(exc)
    v1, v2, gb_types, num_atoms = csl.Create_Filtered_GB_Plane_List(
        uvw,
        basis_norm,
        m,
        n,
        limit,
        parallel_filtering=parallel_planes,
        gb_type=gb_type,
    )

    if len(v1) == 0:
        return (
            f"No GB planes found for axis {uvw.tolist()}, basis={basis_norm}, "
            f"Sigma={sigma}, limit={limit}."
        )

    lines = [
        f"CSL GB planes — axis {uvw.tolist()}, basis={basis_norm}, Sigma={sigma}, "
        f"theta={degrees(theta):.8f} deg, rotation=(m={m}, n={n}), limit={limit}:",
        f"{'GB1 (primitive)':<20} {'GB2 (primitive)':<20} {'Type':<20} {'NumAtoms':<10}",
    ]
    type_filter = gb_type.strip().lower() if gb_type else None
    for gb1, gb2, plane_type, atoms in zip(v1, v2, gb_types, num_atoms):
        if type_filter and type_filter not in plane_type.lower():
            continue
        primitive_gb1 = np.array(_reduced_miller(np.asarray(gb1)), dtype=int)
        primitive_gb2 = np.array(_reduced_miller(np.asarray(gb2)), dtype=int)
        lines.append(
            f"{str(primitive_gb1):<20} {str(primitive_gb2):<20} "
            f"{plane_type:<20} {atoms:<10}"
        )

    if len(lines) == 2:
        hint = f" (filter gb_type={gb_type!r})" if gb_type else ""
        return (
            f"No GB planes matched for axis {uvw.tolist()}, basis={basis_norm}, "
            f"Sigma={sigma}, limit={limit}{hint}."
        )
    return "\n".join(lines)


def _gb_planes_batch_worker(args: tuple) -> tuple[tuple[int, int, int], str]:
    u, v, w, basis, sigma, rotation_m, rotation_n, limit, gb_type = args
    return (sigma, rotation_m, rotation_n), _format_gb_planes_for_sigma(
        u,
        v,
        w,
        basis,
        sigma,
        limit,
        gb_type,
        parallel_planes=False,
        rotation_m=rotation_m,
        rotation_n=rotation_n,
    )


@tool
def list_csl_sigmas(basis: str, u: int, v: int, w: int, limit: int = 50) -> str:
    """List every basis-validated CSL rotation branch for axis (u, v, w).

    Use this first to find valid Sigma/rotation_m/rotation_n identities before
    listing grain-boundary planes. One Sigma may appear at multiple angles.
    basis: crystal structure — one of fcc, bcc, diamond, sc.
    limit: maximum Sigma to search (default 50).
    """
    basis_norm = basis.strip().lower()
    if basis_norm not in VALID_BASIS:
        return f"Invalid basis '{basis}'. Use one of: {', '.join(VALID_BASIS)}."
    uvw = csl.CommonDivisor(np.array([u, v, w], dtype=int))[0]
    rotations = csl.list_csl_rotations_for_axis(uvw, basis_norm, limit)
    branch_counts: dict[int, int] = {}
    branch_indices: dict[int, int] = {}
    for sigma, _theta, _m, _n in rotations:
        branch_counts[sigma] = branch_counts.get(sigma, 0) + 1
    lines = []
    for sigma, theta, m, n in rotations:
        branch_indices[sigma] = branch_indices.get(sigma, 0) + 1
        branch = f"{branch_indices[sigma]}/{branch_counts[sigma]}"
        lines.append(
            f"Sigma: {sigma:3d}  Theta: {degrees(theta):5.2f}  "
            f"rotation_m: {m:3d}  rotation_n: {n:3d}  branch: {branch}"
        )
    if not lines:
        return (
            f"No CSL values found for basis={basis_norm}, axis {uvw.tolist()} "
            f"up to Sigma {limit}."
        )
    header = (
        f"CSL candidates for basis={basis_norm}, axis {uvw.tolist()} "
        f"(limit={limit}):\n"
    )
    return header + "\n".join(lines)


@tool
def list_csl_gb_planes(
    u: int,
    v: int,
    w: int,
    basis: str,
    sigma: int,
    limit: int = 2,
    gb_type: str | None = None,
    rotation_m: int | None = None,
    rotation_n: int | None = None,
) -> str:
    """List symmetry-filtered CSL grain-boundary planes for one rotation.

    Use this when the task targets one exact Sigma/m/n branch. For multiple
    rotations, prefer list_csl_gb_planes_batch instead of repeated calls.

    basis: crystal structure — one of fcc, bcc, diamond, sc.
    sigma: target CSL Sigma (from list_csl_sigmas); must be > 1.
    rotation_m/rotation_n: exact branch from list_csl_sigmas. Both are required
      when the selected Sigma has multiple rotations on this axis.
    limit: upper bound on GB plane indices (default 2, **maximum 6**). Larger values
      include more planes but grow combinatorially; values above 6 are clamped to 6.
    Returned GB1/GB2 normals are automatically reduced to primitive Miller
      indices before display (for example, [-11,-11,11] becomes [-1,-1,1]).
    gb_type: optional Type-column filter (case-insensitive substring). Exact labels are
      ``Symmetric Tilt``, ``Tilt``, ``Twist``, ``Mixed``. Prefer ``"tilt"`` for all tilt
      rows (symmetric + asymmetric); use ``"symmetric tilt"`` / ``"twist"`` / ``"mixed"``
      for a single class. Pass this whenever the task specifies a boundary class —
      unfiltered tables at higher limit are dominated by Mixed rows and become huge.
    """
    return _format_gb_planes_for_sigma(
        u,
        v,
        w,
        basis,
        sigma,
        limit,
        gb_type,
        rotation_m=rotation_m,
        rotation_n=rotation_n,
    )


@tool
def list_csl_gb_planes_batch(
    u: int,
    v: int,
    w: int,
    basis: str,
    sigmas: list[int] | None = None,
    limit: int = 2,
    gb_type: str | None = None,
    rotations: list[CslRotationSelection] | None = None,
) -> str:
    """List symmetry-filtered GB planes for many Sigma values in one call.

    Prefer this over repeated list_csl_gb_planes when the task needs plane tables
    for several or all Sigma on an axis. Sigma values are solved in parallel on
    the server. When querying multiple axes, wait for one batch result before calling
    the next batch; the server gives one CSL computation exclusive pool access at a
    time. Skip Σ1 (identity); it is ignored automatically.

    The number of planes per Sigma is determined solely by limit (Miller-index
    search bound) and optional gb_type — there is no separate row cap. Returned
    GB1/GB2 normals are reduced to primitive Miller indices before display.

    sigmas: unambiguous Sigma integers (e.g. [3, 9, 11]).
    rotations: exact branch descriptors for repeated Sigma values, e.g.
      [{"sigma": 33, "rotation_m": 8, "rotation_n": 1},
       {"sigma": 33, "rotation_m": 5, "rotation_n": 2}].
    limit: GB plane index bound (default 2, **maximum 6**; higher values are clamped).
    gb_type: optional Type-column filter (same as list_csl_gb_planes). When the task is
      about tilt GBs, pass ``gb_type="tilt"`` so Twist/Mixed rows are dropped before
      the (often huge) table is returned.
    """
    limit = _clamp_gb_plane_limit(limit)
    basis_norm = basis.strip().lower()
    if basis_norm not in VALID_BASIS:
        return f"Invalid basis '{basis}'. Use one of: {', '.join(VALID_BASIS)}."

    unique_sigmas = []
    seen: set[int] = set()
    for value in sigmas or []:
        sigma = int(value)
        if sigma in seen:
            continue
        seen.add(sigma)
        unique_sigmas.append(sigma)

    unique_sigmas = [sigma for sigma in unique_sigmas if sigma > 1]
    requested_rotations = list(rotations or [])
    if not unique_sigmas and not requested_rotations:
        return (
            "No valid rotations supplied. Pass unambiguous Sigma integers or "
            "exact rotation descriptors from list_csl_sigmas."
        )

    if len(unique_sigmas) + len(requested_rotations) > MAX_BATCH_SIGMAS:
        return (
            f"Too many CSL rotations ({len(unique_sigmas) + len(requested_rotations)}). "
            f"Pass at most {MAX_BATCH_SIGMAS}, or raise the axis limit and batch in chunks."
        )

    uvw = csl.CommonDivisor(np.array([u, v, w], dtype=int))[0]
    resolved: list[tuple[int, int, int]] = []
    explicitly_selected_sigmas = {item.sigma for item in requested_rotations}
    for sigma in unique_sigmas:
        if sigma in explicitly_selected_sigmas:
            continue
        try:
            _theta, m, n = csl.resolve_csl_rotation(uvw, basis_norm, sigma)
        except ValueError as exc:
            return str(exc)
        resolved.append((sigma, m, n))
    for selection in requested_rotations:
        try:
            _theta, m, n = csl.resolve_csl_rotation(
                uvw,
                basis_norm,
                selection.sigma,
                rotation_m=selection.rotation_m,
                rotation_n=selection.rotation_n,
            )
        except ValueError as exc:
            return str(exc)
        resolved.append((selection.sigma, m, n))
    resolved = list(dict.fromkeys(resolved))

    tasks = [
        (u, v, w, basis_norm, sigma, m, n, limit, gb_type)
        for sigma, m, n in resolved
    ]
    sections = parallel_map(_gb_planes_batch_worker, tasks)
    requested_order = {identity: index for index, identity in enumerate(resolved)}
    sections.sort(key=lambda pair: requested_order[pair[0]])

    header = (
        f"Batch CSL GB planes — axis {uvw.tolist()}, basis={basis_norm}, "
        f"limit={limit}, rotations={resolved}\n"
    )
    body = "\n\n".join(section for _, section in sections)
    return header + body


def _format_orientation_block(label: str, grain: dict) -> list[str]:
    ori = grain["orientation"]
    raw = grain["orientation_raw"]
    lines = [
        f"{label} grain:",
        f"  orientation (reduced): x={ori['x']}  y={ori['y']}  z={ori['z']}",
        f"  orientation (raw):       x={raw['x']}  y={raw['y']}  z={raw['z']}",
        f"  x period: {grain['x_period_lattice']:.4f} lattice units "
        f"({grain['x_period_angstrom']:.3f} Å per repeat)",
        f"  y period: {grain['y_period_lattice']:.4f} lattice units "
        f"({grain['y_period_angstrom']:.3f} Å per repeat)",
        f"  z period: {grain['z_period_lattice']:.4f} lattice units "
        f"({grain['z_period_angstrom']:.3f} Å per repeat)",
    ]
    return lines


@tool
def get_tilt_gb_orientation(
    u: int,
    v: int,
    w: int,
    basis: str,
    sigma: int,
    gb_plane: list[int],
    a0: float = 2.8553,
    rotation_m: int | None = None,
    rotation_n: int | None = None,
) -> str:
    """Derive orthogonal grain orientations for a CSL tilt GB (LAMMPS gb_minimize input).

    Call this after choosing axis, Sigma, and a tilt GB plane from list_csl_gb_planes.
    Returns orientation integer triples for top and bottom grains mapped to the
    simulation frame: y = GB normal, z = tilt axis, x = in-plane direction, plus
    period lengths for LAMMPS box setup.

    gb_plane: Miller indices of grain-1 GB plane normal (must be a tilt plane), e.g. [0, 2, 1].
    a0: lattice parameter in Å (default Fe BCC 2.8553).
    rotation_m/rotation_n: exact branch from list_csl_sigmas. Both are required
      when the selected Sigma has multiple rotations on this axis.

    Twist boundaries are not supported — only tilt GBs where the plane normal is
    perpendicular to the rotation axis.
    """
    basis_norm = basis.strip().lower()
    if basis_norm not in VALID_BASIS:
        return f"Invalid basis '{basis}'. Use one of: {', '.join(VALID_BASIS)}."

    try:
        primitive_plane = _reduced_miller(_miller_vector("gb_plane", gb_plane))
        payload = csl.tilt_gb_orthogonal_orientation(
            [u, v, w],
            basis_norm,
            int(sigma),
            primitive_plane,
            a0=float(a0),
            rotation_m=rotation_m,
            rotation_n=rotation_n,
        )
    except ValueError as exc:
        return str(exc)

    lines = [
        "CSL tilt GB orientation for gb_minimize (y=GB normal, z=tilt axis, x=in-plane):",
        f"axis: {payload['axis']}  basis: {payload['basis']}  Sigma: {payload['sigma']}",
        f"rotation: m={payload['m']}  n={payload['n']}",
        f"GB plane (grain 1): {payload['gb_plane']}  theta: {payload['theta_deg']:.2f}°",
        f"internal CSL-scaled plane pair: {payload['gb_plane_csl_scaled']} -> "
        f"{payload['paired_gb_plane_csl_scaled']} "
        f"(scale={payload['gb_plane_csl_scale']})",
        f"orthogonal cell atoms (reference): {payload['orthogonal_cell_atoms']}",
        "",
        *_format_orientation_block("Top", payload["top"]),
        "",
        *_format_orientation_block("Bottom", payload["bottom"]),
        "",
        "Pass the orientation integers and period lengths to delegate_lammps. "
        "LAMMPS must choose nx/ny/nz from its own sizing policy.",
    ]
    return "\n".join(lines)


STRUCTURE_AGENT_TOOLS = [
    analyze_tilt_geometry,
    compare_miller_planes,
    match_csl_tilt_references,
    score_csl_tilt_plane_pairs,
    suggest_low_index_near_csl_planes,
    list_csl_sigmas,
    list_csl_gb_planes,
    list_csl_gb_planes_batch,
    get_tilt_gb_orientation,
]
CSL_TOOL_NAMES = frozenset(item.name for item in STRUCTURE_AGENT_TOOLS)
