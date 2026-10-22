"""Infer cubic grain orientations and lattice periods from cached OVITO PTM.

OVITO quaternions are xyzw and rotate crystal Cartesian coordinates into lab.
The raw signed Miller indices depend on PTM's arbitrary cubic symmetry branch;
direction families are invariant, but original builder labels are unrecoverable
without an external orientation convention.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path

import numpy as np


def cubic_rotations() -> np.ndarray:
    result = []
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((-1, 1), repeat=3):
            matrix = np.eye(3)[:, perm] * np.asarray(signs)
            if np.linalg.det(matrix) > 0:
                result.append(matrix)
    return np.asarray(result)


def mean_cubic_orientation(quaternions: np.ndarray, rmsd: np.ndarray) -> dict:
    qx, qy, qz, qw = quaternions.T
    rotations = np.empty((len(quaternions), 3, 3))
    rotations[:, 0, 0] = 1 - 2 * (qy*qy + qz*qz)
    rotations[:, 0, 1] = 2 * (qx*qy - qw*qz)
    rotations[:, 0, 2] = 2 * (qx*qz + qw*qy)
    rotations[:, 1, 0] = 2 * (qx*qy + qw*qz)
    rotations[:, 1, 1] = 1 - 2 * (qx*qx + qz*qz)
    rotations[:, 1, 2] = 2 * (qy*qz - qw*qx)
    rotations[:, 2, 0] = 2 * (qx*qz - qw*qy)
    rotations[:, 2, 1] = 2 * (qy*qz + qw*qx)
    rotations[:, 2, 2] = 1 - 2 * (qx*qx + qy*qy)
    reference = rotations[np.argmin(rmsd)]
    symmetries = cubic_rotations()
    # Maximize trace(R_ref.T R_i S) over the 24 proper cubic symmetries.
    relative = np.einsum("ji,njk->nik", reference, rotations)
    scores = np.einsum("nij,sji->ns", relative, symmetries)
    aligned = np.einsum("nij,njk->nik", rotations, symmetries[np.argmax(scores, axis=1)])
    u, _, vh = np.linalg.svd(np.mean(aligned, axis=0))
    mean = u @ np.diag([1, 1, np.linalg.det(u @ vh)]) @ vh
    relative_mean = np.einsum("ji,njk->nik", mean, aligned)
    deviations = np.arccos(np.clip((np.trace(relative_mean, axis1=1, axis2=2) - 1) / 2, -1, 1))
    return {
        "rotation_crystal_to_lab": mean.tolist(),
        "misorientation_median_deg": float(np.degrees(np.median(deviations))),
        "misorientation_p95_deg": float(np.degrees(np.quantile(deviations, 0.95))),
    }


def nearest_low_index(vector: np.ndarray, maximum_index: int = 12,
                      angular_tolerance_deg: float = 0.25) -> dict:
    unit = np.asarray(vector) / np.linalg.norm(vector)
    candidates = []
    for hkl in itertools.product(range(-maximum_index, maximum_index + 1), repeat=3):
        if hkl == (0, 0, 0) or math.gcd(*hkl) != 1:
            continue
        n = np.linalg.norm(hkl)
        cosine = float(np.dot(unit, hkl) / n)
        candidates.append((cosine, n, hkl))
    cosine, _, indices = max(candidates, key=lambda item: (item[0], -item[1]))
    residual = float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))))
    return {
        "crystal_unit_components": unit.tolist(),
        "signed_indices_in_ptm_branch": list(indices),
        "cubic_family": sorted(abs(x) for x in indices),
        "angular_residual_deg": residual,
        "angular_tolerance_deg": angular_tolerance_deg,
        "accepted": bool(residual <= angular_tolerance_deg),
        "status": "resolved_low_index_direction" if residual <= angular_tolerance_deg else "no_accepted_low_index_direction",
        "search_maximum_index": maximum_index,
    }


def translation_count_step(lattice: str, indices: list[int]) -> float:
    """Smallest positive c for the real-space translation c*a*[uvw].

    For reduced uvw, FCC permits halves iff u+v+w is even; BCC permits
    halves iff all three indices are odd. Zero is even. Nonprimitive indices
    divide the coefficient step by their gcd. These are translation rules,
    not reciprocal-space reflection conditions, and assume an undecorated
    FCC/BCC Bravais lattice (chemical ordering may impose a larger repeat).

    Primitive-vector references: lampz.tugraz.at/~hadley/ss1/crystalstructure/
    structures/{fcc/fcc,bcc/bcc}.php
    """
    if len(indices) != 3 or not all(math.isfinite(v) and float(v).is_integer() for v in indices):
        raise ValueError("Direction requires three finite integer indices")
    direction = [int(v) for v in indices]
    divisor = math.gcd(*direction)
    if not divisor:
        raise ValueError("Direction must be nonzero")
    primitive = [v // divisor for v in direction]
    if lattice.upper() == "FCC":
        return (0.5 if sum(primitive) % 2 == 0 else 1.0) / divisor
    if lattice.upper() == "BCC":
        return (0.5 if all(value % 2 for value in primitive) else 1.0) / divisor
    raise ValueError("Only FCC/BCC translation rules are implemented")


def is_allowed_translation_count(lattice: str, indices: list[int], count: float) -> bool:
    """Check a nominal coefficient; never use this exact test on relaxed lengths."""
    step = translation_count_step(lattice, indices)
    return (math.isfinite(count) and count > 0
            and abs(count / step - round(count / step)) <= 1e-7)


def shortest_translation_factor(lattice: str, indices: list[int]) -> float:
    """Return s in t=s*a*[hkl] for primitive integer hkl."""
    if math.gcd(*indices) != 1:
        raise ValueError("Expected relatively prime integer direction indices")
    return translation_count_step(lattice, indices)


def _nearest_actual_translation(positions: np.ndarray, source_indices: np.ndarray,
                                grain_mask: np.ndarray, predicted: np.ndarray,
                                z_period: float) -> dict:
    targets = positions[grain_mask]
    images = np.concatenate([targets + (0, 0, shift) for shift in (-z_period, 0, z_period)])
    starts = positions[source_indices]
    tolerance = 0.25 * np.linalg.norm(predicted)
    bins = {}
    for index, address in enumerate(np.floor(images / tolerance).astype(int)):
        bins.setdefault(tuple(address), []).append(index)
    differences, distances = [], []
    offsets = list(itertools.product((-1, 0, 1), repeat=3))
    for start in starts:
        query = start + predicted
        address = np.floor(query / tolerance).astype(int)
        candidates = [index for offset in offsets for index in bins.get(tuple(address + offset), ())]
        if not candidates:
            continue
        local_distances = np.linalg.norm(images[candidates] - query, axis=1)
        nearest = int(np.argmin(local_distances))
        if local_distances[nearest] < tolerance:
            differences.append(images[candidates[nearest]] - start)
            distances.append(local_distances[nearest])
    differences, distances = np.asarray(differences), np.asarray(distances)
    if not len(differences):
        return {"matched_pairs": 0}
    median = np.median(differences, axis=0)
    return {
        "matched_pairs": int(len(differences)),
        "median_translation_lab_angstrom": median.tolist(),
        "median_x_translation_angstrom": float(median[0]),
        "x_translation_p05_p95_angstrom": np.quantile(differences[:, 0], (0.05, 0.95)).tolist(),
        "median_prediction_residual_angstrom": float(np.median(distances)),
        "median_transverse_distance_angstrom": float(np.median(np.linalg.norm(differences[:, 1:], axis=1))),
    }


def analyze_crystal_mapping(npz_path: str | Path, gb_y: float | None = None,
                            period_angstrom: float | None = None) -> dict:
    data = np.load(npz_path)
    positions = data["positions"]
    structure_types = data["types"]
    orientations = data["orientations"]
    rmsd = data["rmsd"]
    nn_distance = data["interatomic_distance"]
    cell = data["cell"]
    z_period = float(np.linalg.norm(cell[:3, 2]))
    bounds = np.quantile(positions, [0.005, 0.995], axis=0)
    if gb_y is None:
        gb_y = float((bounds[0, 1] + bounds[1, 1]) / 2)
    width = bounds[1] - bounds[0]
    # Broad bulk bands keep free surfaces and interface out of orientation fit.
    interior_x = (positions[:, 0] > bounds[0, 0] + 0.15 * width[0]) & (positions[:, 0] < bounds[1, 0] - 0.15 * width[0])
    output = {
        "input_ptm_cache": str(npz_path), "gb_y_angstrom": gb_y,
        "orientation_convention": "OVITO xyzw quaternion; cubic symmetry aligned before averaging",
        "index_ambiguity": "Signed axis labels depend on the cubic symmetry branch; families are invariant. Original builder labels require its orientation metadata.",
        "grains": {},
    }
    for label, ymin, ymax in (
        ("lower", bounds[0, 1] + 0.2 * (gb_y - bounds[0, 1]), gb_y - 0.2 * (gb_y - bounds[0, 1])),
        ("upper", gb_y + 0.2 * (bounds[1, 1] - gb_y), bounds[1, 1] - 0.2 * (bounds[1, 1] - gb_y)),
    ):
        region = interior_x & (positions[:, 1] > ymin) & (positions[:, 1] < ymax)
        choices, counts = np.unique(structure_types[region], return_counts=True)
        majority_type = int(choices[np.argmax(counts)])
        if majority_type not in (1, 3):
            raise ValueError(f"{label}: dominant structure {majority_type} is not FCC/BCC")
        lattice = {1: "FCC", 3: "BCC"}[majority_type]
        valid = region & (structure_types == majority_type) & (np.linalg.norm(orientations, axis=1) > 0)
        result = mean_cubic_orientation(orientations[valid], rmsd[valid])
        rotation = np.asarray(result["rotation_crystal_to_lab"])
        x_direction = nearest_low_index(rotation.T @ (1, 0, 0))
        gb_plane = nearest_low_index(rotation.T @ (0, 1, 0))
        a_values = nn_distance[valid] / {"FCC": math.sqrt(0.5), "BCC": math.sqrt(0.75)}[lattice]
        a = float(np.median(a_values))
        if not x_direction["accepted"]:
            result.update({
                "lattice": lattice, "bulk_atom_count": int(np.count_nonzero(valid)),
                "local_a_median_angstrom": a,
                "x_direction": x_direction, "gb_plane_normal": gb_plane,
                "period_mapping": None,
                "warning": "No low-index x direction within the angular tolerance; no lattice-period claim is made.",
            })
            output["grains"][label] = result
            continue
        hkl = x_direction["signed_indices_in_ptm_branch"]
        factor = shortest_translation_factor(lattice, hkl)
        full_length = a * np.linalg.norm(hkl)
        predicted = a * rotation @ (factor * np.asarray(hkl))
        grain_mask = (structure_types == majority_type) & ((positions[:, 1] < gb_y) if label == "lower" else (positions[:, 1] > gb_y))
        measured = _nearest_actual_translation(positions, np.flatnonzero(valid), grain_mask, predicted, z_period)
        result.update({
            "lattice": lattice, "bulk_atom_count": int(np.count_nonzero(valid)),
            "bulk_y_interval_angstrom": [float(ymin), float(ymax)],
            "local_a_median_angstrom": a,
            "local_a_p05_p95_angstrom": np.quantile(a_values, [0.05, 0.95]).tolist(),
            "x_direction": x_direction, "gb_plane_normal": gb_plane,
            "shortest_translation_coefficient": factor,
            "full_a_hkl_length_angstrom": float(full_length),
            "shortest_translation_length_angstrom": float(factor * full_length),
            "predicted_translation_lab_angstrom": predicted.tolist(),
            "coordinate_translation_check": measured,
            "conversion_assumptions": "Local FCC/BCC conventional cubic cell with isotropic PTM scale; anisotropic strain is checked by measured actual atom translations rather than assumed absent.",
        })
        if period_angstrom is not None:
            measured_x = measured["median_x_translation_angstrom"]
            result["period_mapping"] = {
                "period_angstrom": period_angstrom,
                "multiple_of_a_hkl_from_local_scale": float(period_angstrom / full_length),
                "number_of_shortest_translations_from_local_scale": float(period_angstrom / (factor * full_length)),
                "multiple_of_a_hkl_from_coordinate_translation": float(period_angstrom / measured_x * factor),
                "number_of_shortest_translations_from_coordinate_translation": float(period_angstrom / measured_x),
            }
        output["grains"][label] = result
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("npz", type=Path)
    parser.add_argument("--gb-y", type=float)
    parser.add_argument("--period", type=float)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = analyze_crystal_mapping(args.npz, args.gb_y, args.period)
    serialized = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(serialized + "\n")
    print(serialized)
