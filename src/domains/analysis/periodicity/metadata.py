"""Use builder metadata for provenance and naming, never for period discovery."""
from __future__ import annotations

import math
import numpy as np

from .crystal_mapping import cubic_rotations, shortest_translation_factor


def normalize_metadata(metadata):
    """Keep the wrapper envelope while exposing canonical construction fields."""
    canonical = metadata.get("canonical_config", {})
    if not canonical:
        return dict(metadata)
    simulation = canonical.get("simulation", {})
    material = canonical.get("material", {})
    grains = {}
    for source, target in (("top", "top"), ("bottom", "bot")):
        grain = canonical.get("grains", {}).get(source, {})
        grains[target] = {"ori":grain.get("orientation", {}), "n":grain.get("size")}
    return {**metadata, **material, **simulation, "grains":grains}


def periodic_flags(metadata, imported):
    """CFG does not encode boundary flags; override only explicitly known axes."""
    flags = list(map(bool, imported))
    origin = ["structure_importer"] * 3
    boundary = metadata.get("boundary") or metadata.get("boundary_flags")
    if isinstance(boundary, str):
        boundary = boundary.split() if " " in boundary else list(boundary)
    explicit = metadata.get("pbc")
    if isinstance(explicit, (list, tuple)) and len(explicit) == 3:
        if not all(isinstance(value, bool) for value in explicit):
            raise ValueError("Metadata pbc must contain three booleans")
        flags, origin = list(explicit), ["source_metadata.pbc"] * 3
    elif isinstance(boundary, (list, tuple)) and len(boundary) == 3:
        styles = [str(value).lower() for value in boundary]
        if any(len(value) not in (1, 2) or any(char not in "pfsm" for char in value) for value in styles):
            raise ValueError("Metadata boundary must contain three LAMMPS p/f/s/m styles")
        flags = [set(value) == {"p"} for value in styles]
        origin = ["source_metadata.boundary"] * 3
    # GB builder x_pbc=False requests vacuum padding. It does NOT change the
    # simulation's physical PPP boundary flags. Do not conflate these concepts.
    return tuple(flags), origin


def _orientation_rows(ori):
    rows = np.array([ori[axis] for axis in "xyz"], dtype=float)
    if rows.shape != (3, 3) or not np.isfinite(rows).all() or np.any(np.linalg.norm(rows, axis=1) == 0):
        raise ValueError("Orientation axes must be nonzero finite three-vectors")
    rows /= np.linalg.norm(rows, axis=1)[:, None]
    if not np.allclose(rows @ rows.T, np.eye(3), atol=1e-5) or np.linalg.det(rows) < .999:
        raise ValueError("Builder orientation must be orthonormal and right-handed")
    return rows


def attach_source_convention(mapping, metadata):
    """Accept signed original labels only after a full cubic-symmetry check."""
    if not mapping:
        return
    grains = metadata.get("grains", {})
    for side, source_side in (("upper", "top"), ("lower", "bot")):
        grain = mapping["grains"].get(side)
        source_grain = grains.get(source_side, grains.get(side, {}))
        if not grain or not source_grain.get("ori"):
            continue
        result = {"source_grain":source_side, "source_orientation":source_grain["ori"],
                  "source_repeat_counts":source_grain.get("n"), "repeat_counts_used_for_discovery":False,
                  "accepted":False}
        try:
            expected = _orientation_rows(source_grain["ori"])
            measured = np.asarray(grain["rotation_crystal_to_lab"])
            alternatives = np.einsum("ij,sjk->sik", measured, cubic_rotations())
            relative = np.einsum("sij,kj->sik", alternatives, expected)
            angles = np.degrees(np.arccos(np.clip((np.trace(relative, axis1=1, axis2=2)-1)/2, -1, 1)))
            angle = float(angles.min())
            result.update(orientation_residual_deg=angle, angular_tolerance_deg=1., accepted=angle <= 1.)
            if angle <= 1.:
                indices = np.asarray(source_grain["ori"]["x"], dtype=float)
                if not np.allclose(indices, np.rint(indices)):
                    raise ValueError("Original x direction is not an integer Miller vector")
                indices = np.rint(indices).astype(int).tolist()
                divisor = math.gcd(*indices)
                primitive = [v//divisor for v in indices]
                result.update(signed_x_indices=indices, primitive_signed_x_indices=primitive,
                              full_direction_translation_coefficient=shortest_translation_factor(grain["lattice"], primitive)/divisor)
        except (ValueError, KeyError, TypeError) as exc:
            result.update(accepted=False, reason=str(exc))
        grain["source_convention"] = result


def annotate_counts_and_mismatch(candidates, mapping, metadata):
    """Separate measured relaxed mismatch from nominal builder-lattice mismatch."""
    if not mapping:
        return
    nominal_a = metadata.get("a0", metadata.get("lattice_constant_angstrom"))
    nominal_a = float(nominal_a) if isinstance(nominal_a, (float, int)) and nominal_a > 0 else None
    for candidate in candidates:
        for side, counts in candidate["crystal_counts"].items():
            convention = mapping["grains"][side].get("source_convention", {})
            if convention.get("accepted"):
                counts.update(signed_indices_from_source=convention["signed_x_indices"],
                              source_full_a_hkl_count=counts["measured_primitive_count"]*convention["full_direction_translation_coefficient"],
                              source_orientation_validated=True)
        for pair in candidate["lattice_pairs"]:
            pair["mismatch_source"] = "actual_relaxed_bulk_coordinate_translations"
            pair["source_direction_counts"] = {}
            pair["source_signed_directions"] = {}
            nominal_lengths = []
            for side in ("upper", "lower"):
                grain = mapping["grains"][side]
                convention = grain.get("source_convention", {})
                count = pair[f"{side}_primitive_count"]
                if convention.get("accepted"):
                    pair["source_direction_counts"][side] = count*convention["full_direction_translation_coefficient"]
                    pair["source_signed_directions"][side] = convention["signed_x_indices"]
                if nominal_a:
                    hkl = grain["x_direction"]["signed_indices_in_ptm_branch"]
                    nominal_lengths.append(float(count*grain["shortest_translation_coefficient"]*nominal_a*np.linalg.norm(hkl)))
            if len(nominal_lengths) == 2:
                pair["nominal_builder_comparison"] = {
                    "a0_angstrom":nominal_a, "grain_lengths_angstrom":nominal_lengths,
                    "relative_mismatch":abs(nominal_lengths[0]-nominal_lengths[1])/np.mean(nominal_lengths),
                    "mismatch_definition":"absolute length difference / mean of the two lengths",
                    "source":"source_metadata.a0 and PTM-indexed crystallographic directions",
                    "note":"Nominal unstrained cubic lengths; distinct from relaxed measured mismatch.",
                }
