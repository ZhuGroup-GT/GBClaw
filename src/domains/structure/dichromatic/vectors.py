"""Measure complete selected-cell boundaries using original atom identities.

This analysis is independent of the displayed viewport. Each boundary contains
only atoms exactly on the selected finite segment. Every axial layer is searched
independently, then the shortest same-layer pairs across all layers are retained.
A geometric inter-grain displacement is a *candidate* Burgers vector; it does
not identify a dislocation without further structural evidence.
"""
from __future__ import annotations

from fractions import Fraction
from math import gcd, lcm
from typing import Any

import numpy as np


GEOMETRY_TOLERANCE_A0 = 1e-8
RATIONAL_TOLERANCE = 1e-10
MAX_EDGE_ANNOTATIONS = 2_000
MAX_TOTAL_ANNOTATIONS = 4_000


def _plane_coordinates(identities: np.ndarray, geometry: Any, layer: int) -> list[list[int]]:
    """Invert two integer rows exactly, then verify all three cubic indices."""
    basis = geometry.basis_half_indices.tolist()
    offset = geometry.layer_offsets_half_indices[layer].tolist()
    for first in range(3):
        for second in range(first + 1, 3):
            det = basis[first][0] * basis[second][1] - basis[first][1] * basis[second][0]
            if det:
                break
        if det:
            break
    result = []
    for identity in identities.tolist():
        relative = [int(value) - int(origin) for value, origin in zip(identity, offset)]
        u, rem_u = divmod(relative[first] * basis[second][1] - relative[second] * basis[first][1], det)
        v, rem_v = divmod(basis[first][0] * relative[second] - basis[second][0] * relative[first], det)
        if rem_u or rem_v or any(basis[row][0] * u + basis[row][1] * v != relative[row] for row in range(3)):
            raise ValueError("Selected atom identities do not belong to the declared axial layer")
        result.append([u, v])
    return result


def _minimum_pair_ranges(m: int, n: int) -> list[tuple[int, int, int, int, int, int]]:
    """All solutions of b*m-a*n=+/-gcd(m,n), with finite-edge bounds.

    Atoms occur at a/m and b/n along their aligned segments. Bezout solutions
    give every shortest nonzero pair in O(number of ties), without a point
    mesh or dense pair-distance matrix. Equal fractions are coincidences.
    """
    common = gcd(m, n)
    reduced_m, reduced_n = m // common, n // common
    result = []
    for sign in (1, -1):
        b0 = (sign * pow(reduced_m, -1, reduced_n)) % reduced_n if reduced_n > 1 else 0
        a0 = (b0 * reduced_m - sign) // reduced_n
        # ceil(-a0 / reduced_m) == -(a0 // reduced_m), using integer arithmetic.
        low = max(-(a0 // reduced_m), -(b0 // reduced_n))
        high = min((m - a0) // reduced_m, (n - b0) // reduced_n)
        if low <= high:
            result.append((a0, b0, reduced_m, reduced_n, low, high))
    return result


def _layer_edge_row(geometry: Any, selected_layer: int, layer: int,
                    start: list[int], delta: list[int]) -> dict[str, Any] | None:
    """Exact projected lattice row in one axial phase, without a 2-D mesh.

    Project the layer-offset difference into the integer planar basis using
    its integer Gram matrix. A row intersects this layer iff its transverse
    offset is integral. Along the row, atoms occur at (phase+k)/intervals.
    """
    from dichromatic_map.crystal import extended_gcd

    basis = geometry.basis_half_indices.astype(object)
    offset = geometry.layer_offsets_half_indices[layer].astype(object)
    relative = offset - geometry.layer_offsets_half_indices[selected_layer]
    gram, dots = basis.T @ basis, basis.T @ relative
    determinant = int(gram[0, 0] * gram[1, 1] - gram[0, 1] * gram[1, 0])
    q = [Fraction(int(gram[1, 1] * dots[0] - gram[0, 1] * dots[1]), determinant),
         Fraction(int(gram[0, 0] * dots[1] - gram[1, 0] * dots[0]), determinant)]
    intervals = gcd(*delta)
    primitive = [int(value) // intervals for value in delta]
    if (primitive[0] * q[1] - primitive[1] * q[0]).denominator != 1:
        return None
    _, u, v = extended_gcd(*primitive)
    phase = (u * q[0] + v * q[1]) % 1
    initial = [Fraction(start[index]) + phase * primitive[index] - q[index] for index in range(2)]
    if any(value.denominator != 1 for value in initial):
        raise ValueError("Inconsistent integer layer-row reconstruction")
    identity = offset + basis @ [int(value) for value in initial]
    step = basis @ primitive
    return {"intervals": intervals, "phase": phase, "last": intervals - bool(phase),
            "identity": np.array(identity, dtype=np.int64), "step": np.array(step, dtype=np.int64)}


def _pair_range_for_difference(m: int, n: int, last_a: int, last_b: int,
                              difference: int) -> tuple[int, int, int, int, int, int] | None:
    """All bounded solutions to m*b-n*a=difference, using integer arithmetic."""
    common = gcd(m, n)
    if difference % common:
        return None
    reduced_m, reduced_n = m // common, n // common
    target = difference // common
    b0 = (target * pow(reduced_m, -1, reduced_n)) % reduced_n if reduced_n > 1 else 0
    a0 = (b0 * reduced_m - target) // reduced_n
    low = max(-(a0 // reduced_m), -(b0 // reduced_n))
    high = min((last_a - a0) // reduced_m, (last_b - b0) // reduced_n)
    return (a0, b0, reduced_m, reduced_n, low, high) if low <= high else None


def _shifted_minimum_pair_ranges(first: dict, second: dict) -> tuple[Fraction | None, list, int]:
    """Nearest nonzero differences between two finite, phase-shifted rows.

    Candidate differences are a shifted integer lattice. Visit that lattice
    in increasing absolute distance and solve its bounded Diophantine equation
    exactly. This preserves all ties without generating every row atom or an
    inter-grain distance matrix. Identical singleton rows have no nonzero pair.
    """
    m, n = first["intervals"], second["intervals"]
    common = gcd(m, n)
    shift = m * second["phase"] - n * first["phase"]
    last_a, last_b = first["last"], second["last"]
    coincident = None
    if shift.denominator == 1:
        coincident = _pair_range_for_difference(m, n, last_a, last_b, -int(shift))
    coincidence_count = coincident[-1] - coincident[-2] + 1 if coincident else 0
    minimum_key, maximum_key = -n * last_a // common, m * last_b // common
    pivot = (-shift) // common
    left, right = min(pivot, maximum_key), max(pivot + 1, minimum_key)
    minimum = None
    ranges = []
    while left >= minimum_key or right <= maximum_key:
        choices = []
        if left >= minimum_key:
            choices.append((abs(shift + common * left), left, False))
        if right <= maximum_key:
            choices.append((abs(shift + common * right), right, True))
        distance, key, advance_right = min(choices)
        if minimum is not None and distance > minimum:
            break
        if advance_right:
            right += 1
        else:
            left -= 1
        if not distance:  # Same-layer coincidences are deliberately excluded.
            continue
        pair_range = _pair_range_for_difference(m, n, last_a, last_b, common * key)
        if pair_range:
            minimum = distance
            ranges.append(pair_range)
    return (minimum / (m * n) if minimum is not None else None), ranges, coincidence_count


def _notation(components: np.ndarray, basis: np.ndarray, displacement: np.ndarray, *, unit: str) -> dict[str, Any]:
    """Match the installed measure-vector formatter and quantify its precision."""
    from dichromatic_map.crystal import format_direction_components

    fractions = [Fraction(float(value)).limit_denominator(48) for value in components]
    denominator = lcm(*(value.denominator for value in fractions))
    integers = [value.numerator * (denominator // value.denominator) for value in fractions]
    common = gcd(*integers) or 1
    indices = [value // common for value in integers]
    accepted = (denominator <= 48 and max(map(abs, indices)) <= 256
                and all(abs(float(value) - actual) <= RATIONAL_TOLERANCE
                        for value, actual in zip(fractions, components)))
    if accepted:
        factor = Fraction(common, denominator)
        displayed = np.array([float(value) for value in fractions])
        rational = {"is_rational": True, "numerator": factor.numerator,
                    "denominator": factor.denominator, "indices": indices}
    else:
        displayed = np.array([0.0 if abs(value) < 1e-12 else float(f"{value:.6g}") for value in components])
        rational = {"is_rational": False, "numerator": None, "denominator": None, "indices": None}
    return {"notation": format_direction_components(components, unit=unit),
            "rational": rational, "notation_components": displayed.tolist(),
            "reconstruction_error_a0": float(np.linalg.norm(basis @ displayed - displacement))}


def measure_grain_vector(
    displacement_a0: list[float] | np.ndarray, *, angle_deg: float,
    deformation_gradients: list | np.ndarray, lattice: str, axis: str,
) -> list[dict[str, Any]]:
    """Use the desktop's Measure vector semantics for the same G1→G2 vector.

    Lattice coefficients refer to each grain's actual deformed conventional
    basis. Current components refer to polar-rotated orthonormal cubic axes.
    The input is already deformed; it must not be deformed a second time.
    """
    from dichromatic_map.crystal import crystal_vector_coordinates

    displacement = np.asarray(displacement_a0, dtype=float)
    result = []
    for index, sign in enumerate((1, -1)):
        coordinates = crystal_vector_coordinates(displacement, sign * angle_deg / 2,
                                                deformation_gradients[index], lattice, axis)
        lattice_notation = _notation(coordinates.lattice, coordinates.lattice_basis, displacement, unit="")
        current_notation = _notation(coordinates.current, coordinates.current_frame, displacement, unit="a₀")
        result.append({
            "grain": index + 1, "lattice_components": coordinates.lattice.tolist(),
            "lattice_notation": lattice_notation["notation"], "rational": lattice_notation["rational"],
            "notation_components": lattice_notation["notation_components"],
            "reconstruction_error_a0": lattice_notation["reconstruction_error_a0"],
            "current_components_a0": coordinates.current.tolist(), "current_notation": current_notation["notation"],
            "current_rational": current_notation["rational"],
            "current_reconstruction_error_a0": current_notation["reconstruction_error_a0"],
            "lattice_basis_analysis": coordinates.lattice_basis.tolist(),
            "basis_reconstruction_error_a0": float(np.linalg.norm(coordinates.lattice_basis @ coordinates.lattice - displacement)),
        })
    return result


def analyze_dichromatic_edge_vectors(
    parameters: dict[str, Any], strain: dict[str, Any], selected_cell: dict[str, Any],
) -> dict[str, Any]:
    """Find all shortest nonzero inter-grain pairs on each selected boundary.

    The four saved selected corners, not a reduced CSL basis, define the edges.
    Only confirmed saved transformations are inputs; no strain solve occurs.
    Limits return an explicit incomplete edge rather than a truncated set of
    ties or a possibly false claimed minimum.
    """
    from dichromatic_map.cells import validate_cell_vertices
    from dichromatic_map.crystal import get_geometry, layer_name, rotation_matrix_2d

    lattice, axis = parameters.get("lattice", "FCC"), parameters.get("axis", "110")
    geometry = get_geometry(lattice, axis)
    layer = selected_cell.get("layer")
    if isinstance(layer, bool) or not isinstance(layer, (int, np.integer)) or not 0 <= layer < geometry.layer_count:
        raise ValueError("Selected cell requires a valid axial layer")
    identities = np.asarray(selected_cell.get("grain_half_indices"))
    vertices = np.asarray(selected_cell.get("transformed_grain_vertices_a0"), dtype=float)
    deformation = np.asarray(strain.get("deformation_gradients"), dtype=float)
    shifts = np.asarray(strain.get("translations_a0"), dtype=float)
    if (identities.shape != (2, 4, 3) or not np.issubdtype(identities.dtype, np.integer)
            or vertices.shape != (2, 4, 2) or deformation.shape != (2, 2, 2) or shifts.shape != (2, 2)
            or any(not np.all(np.isfinite(value)) for value in (vertices, deformation, shifts))
            or np.any(np.linalg.det(deformation) <= 0)):
        raise ValueError("Selected cell requires valid original atom identities and finite saved transformations")
    angle = float(strain.get("reference_angle_deg", selected_cell.get("reference_angle_deg", parameters.get("angle_deg", 0))))
    a0 = float(parameters.get("lattice_constant", 1.0))
    if not np.isfinite(angle) or not np.isfinite(a0) or a0 <= 0:
        raise ValueError("Reference angle and positive lattice constant must be finite")
    if parameters.get("angle_deg") is not None and abs(float(parameters["angle_deg"]) - angle) > 1e-12:
        raise ValueError("Saved strain reference angle does not match the requested geometry")
    transforms = [deformation[index] @ rotation_matrix_2d(sign * angle / 2) for index, sign in enumerate((1, -1))]
    plane_coordinates = [_plane_coordinates(identities[index], geometry, layer) for index in range(2)]
    reconstructed = np.array([(identities[index] @ geometry.frame[:, :2] / 2) @ transforms[index].T + shifts[index]
                              for index in range(2)])
    if (np.max(np.linalg.norm(reconstructed - vertices, axis=-1)) > GEOMETRY_TOLERANCE_A0
            or np.max(np.linalg.norm(vertices[0] - vertices[1], axis=-1)) > GEOMETRY_TOLERANCE_A0):
        raise ValueError("Saved selected vertices do not match aligned original atom identities")
    for polygon in vertices:
        validate_cell_vertices(polygon)
    common_vertices = vertices.mean(axis=0)
    alignment_residual = float(np.max(np.linalg.norm(vertices[0] - vertices[1], axis=-1)))
    # Distances can differ from their common-segment value by the alignment
    # residual. Resolve the first spacing only if it cannot exchange order
    # with the next spacing under this saved-geometry uncertainty.
    spacing_resolution = max(GEOMETRY_TOLERANCE_A0, 2 * alignment_residual)
    report: dict[str, Any] = {
        "method": "exact_all_layers_finite_edge_integer_lattices", "analysis_version": 2,
        "status": "complete", "layer": int(layer), "edges": [], "annotation_count": 0,
        "selected_layer": int(layer), "layer_count": geometry.layer_count,
        "analyzed_layers": list(range(geometry.layer_count)),
        "layer_policy": "Search G1/G2 pairs within each axial layer separately, then retain all globally shortest nonzero pairs per edge across every layer. Never pair atoms from different layers.",
        "convention": "Directed displacement G1→G2 in unrotated analysis coordinates; lengths in a0 and Angstrom. Lattice [uvw] coefficients use each grain's deformed conventional basis.",
        "edge_membership": "Projected atoms exactly on each selected finite segment in every axial layer; no nearby band, visibility filter or viewport clipping.",
        "tolerances": {"geometry_a0": GEOMETRY_TOLERANCE_A0, "rational_components": RATIONAL_TOLERANCE},
        "alignment_residual_a0": alignment_residual,
        "spacing_resolution_a0": spacing_resolution,
        "zero_pairs_policy": "Equal rational edge fractions are coincident and excluded, including the selected corners.",
        "tie_policy": "All exact minimum nonzero differences of rational edge fractions are retained, with their actual saved-transform displacements.",
        "limits": {"annotations_per_edge": MAX_EDGE_ANNOTATIONS, "annotations_total": MAX_TOTAL_ANNOTATIONS,
                   "rational_denominator": 48, "rational_direction_index": 256},
        "interpretation": "These geometric displacements are candidate secondary misfit Burgers vectors, not proof of a dislocation or a Burgers-circuit measurement.",
    }
    for edge_index in range(4):
        following = (edge_index + 1) % 4
        edge_length = float(np.linalg.norm(common_vertices[following] - common_vertices[edge_index]))
        edge: dict[str, Any] = {
            "edge_index": edge_index, "edge_label": f"C{edge_index + 1}–C{following + 1}",
            "vertices_a0": common_vertices[[edge_index, following]].tolist(), "length_a0": edge_length,
            "atom_counts": [0, 0], "coincident_pair_count": 0, "layer_results": [], "minimum_layers": [],
            "nominal_minimum_distance_a0": None,
            "minimum_distance_a0": None, "minimum_distance_angstrom": None,
            "candidate_secondary_misfit": False, "status": "ok", "vectors": [],
            "expected_annotation_count": 0, "warnings": [],
        }
        available = []
        for axial_layer in range(geometry.layer_count):
            rows = []
            for grain in range(2):
                delta = [plane_coordinates[grain][following][index] - plane_coordinates[grain][edge_index][index]
                         for index in range(2)]
                rows.append(_layer_edge_row(geometry, int(layer), axial_layer,
                                            plane_coordinates[grain][edge_index], delta))
            counts = [row["last"] + 1 if row else 0 for row in rows]
            edge["atom_counts"] = [total + count for total, count in zip(edge["atom_counts"], counts)]
            item = {"layer": axial_layer, "layer_name": layer_name(axial_layer), "atom_counts": counts,
                    "coincident_pair_count": 0, "minimum_distance_a0": None,
                    "nominal_minimum_distance_a0": None, "minimum_distance_angstrom": None,
                    "candidate_secondary_misfit": False, "expected_annotation_count": 0,
                    "status": "empty" if not all(rows) else "ok"}
            edge["layer_results"].append(item)
            if not all(rows):
                continue
            fraction, ranges, coincidences = _shifted_minimum_pair_ranges(*rows)
            item["coincident_pair_count"] = coincidences
            edge["coincident_pair_count"] += coincidences
            if fraction is None:
                item["status"] = "only_coincident_pairs"
                continue
            item["expected_annotation_count"] = sum(high - low + 1 for _, _, _, _, low, high in ranges)
            nominal_distance = edge_length * float(fraction)
            item["nominal_minimum_distance_a0"] = nominal_distance
            item["candidate_secondary_misfit"] = not (
                rows[0]["intervals"] == rows[1]["intervals"] and rows[0]["phase"] == rows[1]["phase"])
            if nominal_distance <= spacing_resolution:
                item.update(status="below_numerical_resolution", candidate_secondary_misfit=False)
            else:
                item.update(minimum_distance_a0=nominal_distance,
                            minimum_distance_angstrom=nominal_distance * a0)
            available.append((fraction, axial_layer, rows, ranges, item))
        if not available:
            edge.update(status="no_nonzero_pairs")
            edge["warnings"].append("No same-layer noncoincident atom pair exists on this finite edge in any axial layer.")
            report["edges"].append(edge)
            continue
        minimum_fraction = min(item[0] for item in available)
        minima = [item for item in available if item[0] == minimum_fraction]
        edge["minimum_layers"] = [item[1] for item in minima]
        edge["nominal_minimum_distance_a0"] = edge_length * float(minimum_fraction)
        expected_count = sum(item[-1]["expected_annotation_count"] for item in minima)
        edge["expected_annotation_count"] = expected_count
        edge["candidate_secondary_misfit"] = any(item[-1]["candidate_secondary_misfit"] for item in minima)
        if all(item[2][0]["intervals"] == item[2][1]["intervals"] and
               item[2][0]["phase"] == item[2][1]["phase"] for item in minima):
            edge["warnings"].append("All edge sites in the minimum-attaining layer(s) coincide between grains; their shortest nonzero pair is an inter-site translation, not evidence of secondary misfit.")
            if all(item[1] == layer and all(row["last"] == 1 and not row["phase"] for row in item[2])
                   for item in minima):
                edge["warnings"].append("Only the two selected coincident vertices lie on this edge; no interior edge atoms are present.")
        if expected_count > MAX_EDGE_ANNOTATIONS or report["annotation_count"] + expected_count > MAX_TOTAL_ANNOTATIONS:
            edge.update(status="limit_exceeded", candidate_secondary_misfit=False)
            edge["warnings"].append("All tied minima exceed the annotation budget; this edge is incomplete and no minimum is claimed.")
            report["status"] = "incomplete"
            report["edges"].append(edge)
            continue
        if edge["nominal_minimum_distance_a0"] <= spacing_resolution:
            edge.update(status="below_numerical_resolution", candidate_secondary_misfit=False)
            edge["warnings"].append("The expected nonzero spacing is below the saved geometry tolerance; no resolved minimum is claimed.")
            report["status"] = "incomplete"
            report["edges"].append(edge)
            continue
        for _, axial_layer, rows, ranges, layer_result in minima:
            pairs = []
            for a_start, b_start, a_step, b_step, low, high in ranges:
                pairs.extend((a_start + step * a_step, b_start + step * b_step) for step in range(low, high + 1))
            actual_lengths = []
            for a, b in sorted(pairs):
                atom_indices = [row["identity"] + row["step"] * count for row, count in zip(rows, (a, b))]
                endpoints = np.array([(identity @ geometry.frame[:, :2] / 2) @ transforms[grain].T + shifts[grain]
                                      for grain, identity in enumerate(atom_indices)])
                vector = np.r_[endpoints[1] - endpoints[0], 0.0]
                length = float(np.linalg.norm(vector))
                actual_lengths.append(length)
                fractions = [(row["phase"] + count) / row["intervals"] for row, count in zip(rows, (a, b))]
                edge["vectors"].append({
                    "id": f"edge-{edge_index + 1}-vector-{len(edge['vectors']) + 1}", "edge_index": edge_index,
                    "layer": axial_layer, "layer_name": layer_name(axial_layer),
                    "grain_half_indices": [identity.tolist() for identity in atom_indices],
                    "edge_fractions": [[value.numerator, value.denominator] for value in fractions],
                    "endpoints_a0": endpoints.tolist(),
                    "displacement_a0": vector.tolist(), "length_a0": length, "length_angstrom": length * a0,
                    "candidate_secondary_misfit": layer_result["candidate_secondary_misfit"],
                    "grain_coordinates": measure_grain_vector(vector, angle_deg=angle, deformation_gradients=deformation,
                                                               lattice=lattice, axis=axis),
                })
            layer_result.update(minimum_distance_a0=min(actual_lengths),
                                minimum_distance_angstrom=min(actual_lengths) * a0)
        lengths = [item["length_a0"] for item in edge["vectors"]]
        edge.update(minimum_distance_a0=min(lengths), minimum_distance_angstrom=min(lengths) * a0,
                    distance_spread_a0=max(lengths) - min(lengths))
        report["annotation_count"] += len(edge["vectors"])
        report["edges"].append(edge)
    return report
