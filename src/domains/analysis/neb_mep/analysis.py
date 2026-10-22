"""Deterministic analysis of discrete LAMMPS NEB energy profiles."""

from __future__ import annotations

import math
from typing import Any


def analyze_neb_mep_payload(
    payload: dict[str, Any],
    *,
    derivative_tolerance_eV_per_coordinate: float = 0.0,
) -> dict[str, Any]:
    """Validate an MEP profile and find every positive-to-negative slope change.

    Zero-slope intervals are treated as a plateau. A ``+ ... 0 ... -`` transition
    produces one saddle candidate at the highest, most central replica on that
    plateau. Endpoints are not saddle candidates because they do not have slopes
    on both sides.
    """
    tolerance = float(derivative_tolerance_eV_per_coordinate)
    if not math.isfinite(tolerance) or tolerance < 0.0:
        raise ValueError(
            "derivative_tolerance_eV_per_coordinate must be finite and non-negative"
        )
    if not isinstance(payload, dict):
        raise ValueError("mep_json must contain a JSON object")
    source_version = payload.get("version")
    if source_version not in {1, 2}:
        raise ValueError(
            f"unsupported mep_json version {source_version!r}; expected 1 or 2"
        )
    if payload.get("status") != "completed":
        raise ValueError(
            f"mep_json status must be completed, got {payload.get('status')!r}"
        )
    raw_profile = payload.get("profile")
    if not isinstance(raw_profile, list) or len(raw_profile) < 2:
        raise ValueError("mep_json profile must contain at least two replicas")
    declared_replicas = payload.get("replicas")
    if (
        isinstance(declared_replicas, bool)
        or not isinstance(declared_replicas, int)
        or declared_replicas != len(raw_profile)
    ):
        raise ValueError("mep_json replicas must equal the number of profile points")

    profile: list[dict[str, float | int]] = []
    seen_replicas: set[int] = set()
    previous_coordinate: float | None = None
    initial_energy: float | None = None
    for index, raw_point in enumerate(raw_profile):
        if not isinstance(raw_point, dict):
            raise ValueError(f"mep_json profile point {index} must be an object")
        replica_raw = raw_point.get("replica")
        if isinstance(replica_raw, bool) or not isinstance(replica_raw, int):
            raise ValueError(f"mep_json profile point {index} has invalid replica")
        replica = int(replica_raw)
        if replica in seen_replicas:
            raise ValueError(f"mep_json contains duplicate replica {replica}")
        seen_replicas.add(replica)
        try:
            coordinate = float(raw_point["reaction_coordinate"])
            potential_energy = float(raw_point["potential_energy_eV"])
            relative_energy = float(raw_point["relative_energy_eV"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"mep_json profile point {index} has invalid numeric fields"
            ) from exc
        if not all(
            math.isfinite(value)
            for value in (coordinate, potential_energy, relative_energy)
        ):
            raise ValueError(
                f"mep_json profile point {index} contains a non-finite value"
            )
        if previous_coordinate is not None and coordinate <= previous_coordinate:
            raise ValueError("mep_json reaction coordinates must be strictly increasing")
        if initial_energy is None:
            initial_energy = potential_energy
        expected_relative = potential_energy - initial_energy
        if not math.isclose(
            relative_energy,
            expected_relative,
            rel_tol=1.0e-9,
            abs_tol=1.0e-8,
        ):
            raise ValueError(
                f"mep_json profile point {index} has inconsistent relative energy"
            )
        previous_coordinate = coordinate
        profile.append(
            {
                "replica": replica,
                "reaction_coordinate": coordinate,
                "potential_energy_eV": potential_energy,
                "relative_energy_eV": relative_energy,
            }
        )

    derivatives: list[dict[str, float | int | str]] = []
    derivative_values: list[float] = []
    for left_index in range(len(profile) - 1):
        right_index = left_index + 1
        left = profile[left_index]
        right = profile[right_index]
        derivative = (
            float(right["relative_energy_eV"])
            - float(left["relative_energy_eV"])
        ) / (
            float(right["reaction_coordinate"])
            - float(left["reaction_coordinate"])
        )
        derivative_values.append(derivative)
        sign = (
            "positive"
            if derivative > tolerance
            else "negative" if derivative < -tolerance else "zero"
        )
        derivatives.append(
            {
                "left_replica": int(left["replica"]),
                "right_replica": int(right["replica"]),
                "left_reaction_coordinate": float(left["reaction_coordinate"]),
                "right_reaction_coordinate": float(right["reaction_coordinate"]),
                "derivative_eV_per_coordinate": derivative,
                "sign": sign,
            }
        )

    final_relative_energy = float(profile[-1]["relative_energy_eV"])
    saddles: list[dict[str, Any]] = []
    previous_nonzero_sign: int | None = None
    previous_nonzero_index: int | None = None
    for derivative_index, derivative in enumerate(derivative_values):
        sign = 1 if derivative > tolerance else -1 if derivative < -tolerance else 0
        if sign == 0:
            continue
        if (
            previous_nonzero_sign == 1
            and sign == -1
            and previous_nonzero_index is not None
        ):
            plateau_start_index = previous_nonzero_index + 1
            plateau_end_index = derivative_index
            midpoint = (plateau_start_index + plateau_end_index) / 2.0
            saddle_index = max(
                range(plateau_start_index, plateau_end_index + 1),
                key=lambda position: (
                    float(profile[position]["relative_energy_eV"]),
                    -abs(position - midpoint),
                    -position,
                ),
            )
            point = profile[saddle_index]
            saddle_number = len(saddles) + 1
            saddles.append(
                {
                    "saddle_number": saddle_number,
                    "profile_index": saddle_index,
                    "replica": int(point["replica"]),
                    "reaction_coordinate": float(point["reaction_coordinate"]),
                    "potential_energy_eV": float(point["potential_energy_eV"]),
                    "relative_energy_eV": float(point["relative_energy_eV"]),
                    "left_derivative_eV_per_coordinate": derivative_values[
                        previous_nonzero_index
                    ],
                    "right_derivative_eV_per_coordinate": derivative,
                    "plateau_start_replica": int(
                        profile[plateau_start_index]["replica"]
                    ),
                    "plateau_end_replica": int(
                        profile[plateau_end_index]["replica"]
                    ),
                    "plateau_start_reaction_coordinate": float(
                        profile[plateau_start_index]["reaction_coordinate"]
                    ),
                    "plateau_end_reaction_coordinate": float(
                        profile[plateau_end_index]["reaction_coordinate"]
                    ),
                }
            )
        previous_nonzero_sign = sign
        previous_nonzero_index = derivative_index

    for saddle_position, saddle in enumerate(saddles):
        saddle_index = int(saddle["profile_index"])
        left_bound = (
            int(saddles[saddle_position - 1]["profile_index"])
            if saddle_position > 0
            else 0
        )
        right_bound = (
            int(saddles[saddle_position + 1]["profile_index"])
            if saddle_position + 1 < len(saddles)
            else len(profile) - 1
        )
        left_minimum = min(
            profile[left_bound : saddle_index + 1],
            key=lambda item: float(item["relative_energy_eV"]),
        )
        right_minimum = min(
            profile[saddle_index : right_bound + 1],
            key=lambda item: float(item["relative_energy_eV"]),
        )
        saddle_energy = float(saddle["relative_energy_eV"])
        saddle.update(
            {
                "barrier_from_initial_eV": saddle_energy,
                "barrier_from_final_eV": saddle_energy - final_relative_energy,
                "left_minimum": dict(left_minimum),
                "right_minimum": dict(right_minimum),
                "forward_barrier_from_left_minimum_eV": (
                    saddle_energy - float(left_minimum["relative_energy_eV"])
                ),
                "reverse_barrier_from_right_minimum_eV": (
                    saddle_energy - float(right_minimum["relative_energy_eV"])
                ),
            }
        )

    highest_saddle = (
        max(saddles, key=lambda item: float(item["relative_energy_eV"]))
        if saddles
        else None
    )
    warnings = []
    if not saddles:
        warnings.append(
            "No interior positive-to-negative derivative transition was found."
        )
    return {
        "version": 1,
        "source_mep_version": source_version,
        "definition": (
            "Interior discrete local maxima where the reaction-coordinate energy "
            "derivative changes from positive to negative; zero-slope plateaus "
            "between those signs produce one saddle candidate."
        ),
        "derivative_tolerance_eV_per_coordinate": tolerance,
        "replica_count": len(profile),
        "profile": profile,
        "derivatives": derivatives,
        "initial_energy_eV": float(profile[0]["potential_energy_eV"]),
        "final_energy_eV": float(profile[-1]["potential_energy_eV"]),
        "final_relative_energy_eV": final_relative_energy,
        "saddle_count": len(saddles),
        "saddles": saddles,
        "highest_saddle": highest_saddle,
        "warnings": warnings,
    }
