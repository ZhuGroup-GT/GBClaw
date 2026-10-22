"""Elastic-strain evidence using the coordinate analysis PTM cache and provenance."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import numpy as np
from ..modulation import _smooth
from .io import ptm_cache_path
from .errors import MultiscaleUnavailable
from .strain_spectrum import (
    REVIEW_METHOD as STRAIN_REVIEW_METHOD, REVIEW_POLICY as STRAIN_REVIEW_POLICY,
    spectral_profile, review_spectrum, match_strain_peak,
)


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False)+"\n")


INTERPRETATION_LIMITS = [
    "All audited actual maxima remain in strain_fft_peaks.csv and the profile local_peaks arrays; review eligibility is a heuristic, not statistical significance.",
    "No expected orientation ratio or dislocation spacing enters this calculation.",
    "Far-field a is an effective reference and includes pre-existing homogeneous strain; a scalar cannot remove anisotropy.",
    "PTM and elastic strain share atomic coordinates and are not independent statistical measurements.",
    "No strain signal is not evidence that a coordinate period or a dislocation is absent.",
    "Signed strain is primary: mixing grains can cancel opposite signs; absolute/squared strain can introduce half-periods.",
    "The two z half-channels test transverse consistency, not a full 3D elastic-field translation proof.",
    "Spectral resolution ranges are not confidence intervals; finite window, free surfaces and smoothing can shift peaks.",
    "Local extrema intervals, FFT wavelengths and whole-pattern recurrence are distinct observations; none automatically establishes a dislocation identity.",
    "Summary strain panels use common absolute strain-squared power units, including far-field curves; separately normalized relative_power arrays are supplemental shape evidence only.",
    "Same-grain far-field overlap is descriptive and never a rejection condition; a real elastic field may extend into the far field.",
]


def finalize_strain_metadata(report, out):
    report["interpretation_limits"] = INTERPRETATION_LIMITS
    report["long_band_policy"]["meaning"] = (
        "Exploratory zoom interval, not a proven division between short motifs and dislocation scales. "
        "Summary uses the same absolute strain power for full and long bands; independently band-normalized arrays remain supplemental only.")
    review = report["strain_fft_review"]
    review.update(source_sha256=report["source_sha256"], elastic_atoms_sha256=report["elastic_atoms_sha256"])
    save_json(out / "strain_fft_review.json", review)


def infer_far_field_reference(arrays, boundary):
    """Estimate an effective reference independently for each grain.

    Neither chemical labels nor file names supply the lattice type or a0.
    Reference bands span 40–75% of each grain's distance from the GB to its
    outer surface. Low PTM residual suppresses non-affine/noisy environments.
    """
    p, types = arrays["positions"], arrays["types"]
    nn = boundary["nearest_neighbor_angstrom"]
    gb = boundary["gb_y_angstrom"]
    lo, hi = np.asarray(boundary["occupied_bounds_angstrom"])
    xlo, xhi = boundary["interior_x_interval_angstrom"]
    interior = (p[:, 0] >= xlo) & (p[:, 0] <= xhi)
    if "z" in boundary.get("vacuum_axes", []):
        margin = max(3*nn, .05*(hi[2]-lo[2]))
        interior &= (p[:, 2] > lo[2]+margin) & (p[:, 2] < hi[2]-margin)
    references, masks = {}, {}
    for side, sign, extent in (("lower", -1, gb-lo[1]), ("upper", 1, hi[1]-gb)):
        distance = sign*(p[:, 1]-gb)
        near, far = max(6*nn, .40*extent), min(extent-4*nn, .75*extent)
        region = interior & (distance >= near) & (distance <= far)
        if region.sum() < 100:
            raise MultiscaleUnavailable(f"{side}: insufficient far-field reference atoms")
        counts = {name: int(np.sum(region & (types == value))) for name, value in (("FCC", 1), ("BCC", 3))}
        lattice = max(counts, key=counts.get)
        purity = counts[lattice]/int(region.sum())
        if purity < .8:
            raise MultiscaleUnavailable(f"{side}: no dominant FCC/BCC far field (best fraction {purity:.3f})")
        structure_type = {"FCC": 1, "BCC": 3}[lattice]
        good = region & (types == structure_type) & np.isfinite(arrays["rmsd"])
        if good.sum() < 100:
            raise MultiscaleUnavailable(f"{side}: insufficient finite PTM reference residuals")
        rms_limit = min(.05, float(np.quantile(arrays["rmsd"][good], .8))+1e-10)
        good &= arrays["rmsd"] <= rms_limit
        good &= np.isfinite(arrays["interatomic_distance"]) & (arrays["interatomic_distance"] > 0)
        if good.sum() < 100:
            raise MultiscaleUnavailable(f"{side}: too few low-residual crystalline reference atoms")
        factor = math.sqrt(.5 if lattice == "FCC" else .75)
        values = arrays["interatomic_distance"]/factor
        local_a = values[good]
        a_ref = float(np.median(local_a))
        bins = np.linspace(xlo, xhi, 9)
        spatial_medians = [float(np.median(values[mask])) for left, right in zip(bins[:-1], bins[1:])
                           for mask in [good & (p[:, 0] >= left) & (p[:, 0] < right)] if mask.sum() >= 20]
        references[side] = {
            "lattice": lattice, "ptm_structure_type": structure_type,
            "a_reference_angstrom": a_ref, "atom_count": int(good.sum()),
            "far_field_crystalline_fraction": purity, "region_type_counts": counts,
            "distance_from_gb_interval_angstrom": [float(near), float(far)],
            "ptm_rmsd_max": rms_limit, "a_p05_p95_angstrom": np.quantile(local_a, [.05, .95]).tolist(),
            "x_block_reference_medians_angstrom": spatial_medians,
            "x_block_relative_range": float(np.ptp(spatial_medians)/a_ref) if spatial_medians else None,
            "reference_kind": "effective_far_field_cubic_reference_not_proven_stress_free",
            "conversion": "a=d_PTM/sqrt(1/2)" if lattice == "FCC" else "a=d_PTM/sqrt(3/4)",
        }
        masks[side] = good
    return references, masks


def structure_reference_masks(arrays, boundary, references):
    """Use complete far-field bands, not the RMSD-filtered a0 fitting samples.

    Removing individual high-residual atoms would turn their missing positions
    into a second artificial density signal. PTM purity validates a region but
    never thins the atoms within a usable region for the reference FFT.
    """
    p = np.asarray(arrays["positions"])
    types = np.asarray(arrays["types"])
    bounds = np.asarray(boundary["occupied_bounds_angstrom"])
    lo, hi = boundary["interior_x_interval_angstrom"]
    interior = (p[:, 0] >= lo) & (p[:, 0] <= hi)
    margin = boundary.get("normal_exclusion_margin_angstrom", 3 * boundary["nearest_neighbor_angstrom"])
    interior &= (p[:, 1] > bounds[0, 1] + margin) & (p[:, 1] < bounds[1, 1] - margin)
    for axis, name in ((0, "x"), (2, "z")):
        lateral = boundary.get("lateral_exclusion_margins_angstrom", {}).get(name)
        if lateral is not None:
            interior &= (p[:, axis] > bounds[0, axis] + lateral) & (p[:, axis] < bounds[1, axis] - lateral)
    masks, metadata = {}, {}
    for side, sign in (("lower", -1), ("upper", 1)):
        ref = references.get(side)
        if ref is None:
            metadata[side] = {"status": "missing_far_field_reference"}
            continue
        near, far = ref["distance_from_gb_interval_angstrom"]
        distance = sign * (p[:, 1] - boundary["gb_y_angstrom"])
        region = interior & (distance >= near) & (distance <= far)
        count = int(region.sum())
        purity = float(np.mean(types[region] == ref["ptm_structure_type"])) if count else 0.
        blocks = np.histogram(p[region, 0], np.linspace(lo, hi, 9))[0]
        usable = count >= 100 and purity >= .8 and bool(np.all(blocks > 0))
        metadata[side] = {
            "status": "complete_geometric_far_field_band" if usable else "insufficient_or_noncrystalline_reference_band",
            "atom_count": count, "dominant_crystal_fraction": purity,
            "distance_from_gb_interval_angstrom": [near, far],
            "x_block_atom_counts": blocks.tolist(), "lattice": ref["lattice"],
            "individual_atoms_removed_by_ptm_or_rmsd": False,
        }
        if usable:
            masks[side] = region
    return masks, metadata


def build_structure_spectra(arrays, boundary, references, coordinate_dir, long_min, max_period):
    from .structure_spectrum import coordinate_spectra
    masks, metadata = structure_reference_masks(arrays, boundary, references)
    structure = coordinate_spectra(
        arrays, boundary, 2.5 * boundary["nearest_neighbor_angstrom"], max_period,
        long_min_period=long_min, selection=coordinate_dir / "boundary_selection.npz",
        reference_masks=masks)
    structure["reference_selection"] = metadata
    structure["reference_selection_policy"] = (
        "Complete contiguous far-field bands validated by PTM purity, with the same x window and free-edge exclusions. "
        "No RMSD-quantile thinning: masks used for lattice-constant fitting are intentionally not reused.")
    return structure


def write_structure_peak_review(out, structure):
    """Export every audited maximum; display selection never deletes evidence."""
    records = [{"channel": name, **peak}
               for name, channel in structure["channels"].items()
               for peak in channel.get("local_peaks", [])]
    keys = list(dict.fromkeys(key for row in records for key in row))
    target = out / "structure_fft_peaks.csv"
    with target.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys or ["channel", "period_angstrom"])
        writer.writeheader()
        for row in records:
            writer.writerow({k: json.dumps(v, ensure_ascii=False, allow_nan=False)
                             if isinstance(v, (list, dict)) else v for k, v in row.items()})
    return {"peak_table": str(target.resolve()),
            "method": structure.get("peak_review_method"),
            "counts": {name: {"legacy_display_peaks": len(ch.get("peaks", [])),
                              "audited_local_maxima": len(ch.get("local_peaks", [])),
                              "review_peaks": len(ch.get("review_peaks", []))}
                       for name, ch in structure["channels"].items()},
            "meaning": "Background-aware review is heuristic evidence, not a statistical significance test or an identified dislocation period."}


def calculate_strain(source, arrays, boundary, references, reference_masks):
    from ovito.io import import_file
    from ovito.modifiers import ElasticStrainModifier, ReplicateModifier
    import ovito

    pipeline = import_file(str(source))
    def set_pbc(frame, data):
        data.cell_.pbc = tuple(bool(x) for x in arrays["pbc"])
    pipeline.modifiers.append(set_pbc)
    base = pipeline.compute()
    if not np.allclose(base.particles.positions, arrays["positions"], atol=1e-9, rtol=0):
        raise ValueError("PTM cache coordinates do not match the source input")
    if not np.allclose(np.asarray(base.cell), arrays["cell"], atol=1e-9, rtol=0):
        raise ValueError("PTM cache cell does not match the source input")
    identifiers = (np.asarray(base.particles["Particle Identifier"])
                   if "Particle Identifier" in base.particles else np.arange(1, base.particles.count + 1))
    if "ids" in arrays and not np.array_equal(identifiers, arrays["ids"]):
        raise ValueError("PTM cache atom IDs/order do not match the source input")
    n = len(arrays["positions"])
    def remember_indices(frame, data):
        data.particles_.create_property("AnalysisOriginalIndex", data=np.arange(n, dtype=np.int64))
    pipeline.modifiers.append(remember_indices)
    lz = float(np.linalg.norm(arrays["cell"][:, 2]))
    repeats = max(1, int(np.ceil(4*max(r["a_reference_angstrom"] for r in references.values())/lz)))
    if repeats > 1:
        if not arrays["pbc"][2] or "z" in boundary.get("vacuum_axes", []):
            raise MultiscaleUnavailable("Elastic strain needs a thicker periodic vacuum-free z cell")
        pipeline.modifiers.append(ReplicateModifier(num_x=1, num_y=1, num_z=repeats, adjust_box=True, unique_ids=True))
    replicated = pipeline.compute()
    original_indices = np.asarray(replicated.particles["AnalysisOriginalIndex"])
    difference = np.asarray(replicated.particles.positions)-arrays["positions"][original_indices]
    keep = np.flatnonzero(np.linalg.norm(difference, axis=1) < 1e-7)
    keep = keep[np.argsort(original_indices[keep])]
    if not np.array_equal(original_indices[keep], np.arange(n)):
        raise ValueError("Cannot map replicated strain atoms back to the unique source atoms")
    volumetric = np.full(n, np.nan)
    volume_change = np.full(n, np.nan)
    elastic_types = np.zeros(n, dtype=np.int32)
    valid = np.zeros(n, dtype=bool)
    grain = np.where(arrays["positions"][:, 1] < boundary["gb_y_angstrom"], -1, 1)
    for side, sign in (("lower", -1), ("upper", 1)):
        ref = references[side]
        modifier = ElasticStrainModifier(
            input_crystal_structure=getattr(ElasticStrainModifier.Lattice, ref["lattice"]),
            lattice_constant=ref["a_reference_angstrom"], calculate_deformation_gradients=True,
            calculate_strain_tensors=True, push_strain_tensors_forward=True, only_selected=False)
        pipeline.modifiers.append(modifier)
        data = pipeline.compute()
        t = np.asarray(data.particles["Structure Type"])[keep]
        v = np.asarray(data.particles["Volumetric Strain"])[keep]
        strain = np.asarray(data.particles["Elastic Strain"])[keep]
        # OVITO matrices use column-major components. Determinants are unchanged
        # by transpose; abs(det(F)) also handles crystal orientation reflections.
        f = np.asarray(data.particles["Elastic Deformation Gradient"])[keep].reshape(-1, 3, 3)
        jacobian = np.abs(np.linalg.det(f))
        selected = grain == sign
        good = selected & (arrays["types"] == ref["ptm_structure_type"]) & (t == ref["ptm_structure_type"])
        good &= np.isfinite(v) & np.isfinite(jacobian) & (jacobian > 0)
        if not np.allclose(v[good], strain[good, :3].mean(axis=1), atol=1e-10, rtol=1e-8):
            raise ValueError("Unexpected OVITO volumetric strain convention")
        elastic_types[selected] = t[selected]
        volumetric[good] = v[good]
        volume_change[good] = jacobian[good]-1
        valid |= good
        far = good & reference_masks[side]
        if far.sum() < 100:
            raise MultiscaleUnavailable(f"{side}: insufficient elastic-valid far-field reference atoms")
        ref.update(elastic_valid_far_field_count=int(far.sum()), valid_bulk_count=int(good.sum()),
                   analysis_only_periodic_z_replication=repeats, reported_atom_count=n,
                   far_field_volumetric_median=float(np.median(v[far])),
                   far_field_volumetric_std=float(np.std(v[far])),
                   far_field_volume_change_median=float(np.median(jacobian[far]-1)),
                   far_field_mean_normal_strains=np.mean(strain[far, :3], axis=0).tolist())
        pipeline.modifiers.pop()
    return {"volumetric_strain": volumetric, "volume_change": volume_change,
            "elastic_types": elastic_types, "valid": valid, "grain": grain}, ovito.version_string


def period_evidence(x, values, spectrum, period):
    if spectrum.get("status") != "resolved_profile":
        return {"status": "unresolved_channel", "signed_correlation": None, "spectral_peak_match": False}
    x, values = np.asarray(x), np.asarray(values)
    ok = np.isfinite(values)
    filled = np.interp(x, x[ok], values[ok])
    y = filled-np.polyval(np.polyfit(x-x.mean(), filled, 1), x-x.mean())
    inside = x+period <= x[-1]
    a, b = y[inside], np.interp(x[inside]+period, x, y)
    cycles = spectrum["window_angstrom"]/period
    if len(a) < 20 or cycles < 3:
        return {"status": "insufficient_cycles", "signed_correlation": None,
                "spectral_peak_match": False, "observable_cycles": cycles}
    a, b = a-a.mean(), b-b.mean()
    norm = np.linalg.norm(a)*np.linalg.norm(b)
    correlation = float(np.dot(a, b)/norm) if norm > 1e-30 else None
    df = spectrum["native_frequency_resolution_inverse_angstrom"]
    matches = [p for p in spectrum.get("local_peaks", spectrum.get("peaks", []))
               if abs(1/p["period_angstrom"]-1/period) <= .75*df]
    # A query cannot skip a closer ineligible maximum to claim a different,
    # farther peak. Use the same actual-maximum query as the candidate catalog.
    strongest = match_strain_peak(spectrum, period)["matched_peak"]
    peak_match = strongest is not None and strongest.get("review_eligible", False)
    support = peak_match and correlation is not None and correlation >= .5
    recurs = correlation is not None and correlation >= .5
    global_ratio = strongest.get("power_over_global_peak") if strongest else None
    return {"status": "strain_period_supported" if support else "not_confirmed_by_this_strain_channel",
            "signed_correlation": correlation, "spectral_peak_match": bool(peak_match),
            "raw_spectral_peak_match": bool(matches),
            "spectral_review_scope": strongest.get("review_scope") if strongest else None,
            "recurrence_supported":bool(recurs),
            "recurs_without_resolved_fundamental_peak":bool(recurs and not peak_match),
            "matched_peak": strongest, "observable_cycles": cycles,
            "matched_peak_power_over_global_peak":global_ratio,
            "band_power_fraction":spectrum.get("band_power_fraction"),
            "spectral_strength":"unknown" if global_ratio is None else
                                "weak_relative_to_full_spectrum" if global_ratio < .1 else "visible_in_full_spectrum",
            "opposite_phase_at_translation": bool(correlation is not None and correlation <= -.5)}


def binned_profile(positions, values, mask, edges, sigma_bins):
    mask = mask & np.isfinite(values)
    counts = np.histogram(positions[mask, 0], edges)[0].astype(float)
    numerator = np.histogram(positions[mask, 0], edges, weights=values[mask])[0]
    smooth_count = _smooth(counts, sigma_bins)
    smooth_sum = _smooth(numerator, sigma_bins)
    median_count = float(np.median(smooth_count[smooth_count > 0])) if np.any(smooth_count > 0) else 0
    covered = smooth_count >= max(.25*median_count, 1.)
    mean = np.full(len(counts), np.nan)
    mean[covered] = smooth_sum[covered]/smooth_count[covered]
    return mean, {"atom_count": int(mask.sum()), "occupied_bin_fraction": float(np.mean(counts > 0)),
                  "smoothed_coverage_fraction": float(covered.mean()), "smoothed_count_median": median_count}


def build_profiles(arrays, strain, boundary, references, long_min_period=None):
    """Build atom-binned strain and independent numerical/reference checks."""
    p = arrays["positions"]
    nn = boundary["nearest_neighbor_angstrom"]
    lo, hi = boundary["interior_x_interval_angstrom"]
    edges = np.linspace(lo, hi, max(32, int(np.ceil((hi-lo)/(.5*nn))))+1)
    x = .5*(edges[:-1]+edges[1:]); dx = x[1]-x[0]
    maximum = (hi-lo)/3
    profiles = {}
    z0 = arrays["cell"][2, 3]
    lz = np.linalg.norm(arrays["cell"][:, 2])
    phases = ((p[:, 2]-z0)/lz) % 1
    bounds = np.asarray(boundary["occupied_bounds_angstrom"])
    geometric = (p[:, 0] >= lo) & (p[:, 0] <= hi)
    geometric &= (p[:, 1] > bounds[0, 1]+3*nn) & (p[:, 1] < bounds[1, 1]-3*nn)
    if "z" in boundary.get("vacuum_axes", []):
        zmargin = boundary.get("lateral_exclusion_margins_angstrom", {}).get("z", 3*nn)
        geometric &= (p[:, 2] > bounds[0, 2]+zmargin) & (p[:, 2] < bounds[1, 2]-zmargin)
    for side, sign in (("lower", -1), ("upper", 1)):
        edge_y = boundary["defect_band_y_angstrom"][0 if sign < 0 else 1]
        distance = sign*(p[:, 1]-edge_y)
        signed_values = strain["volumetric_strain"]-references[side]["far_field_volumetric_median"]
        valid = strain["valid"] & (strain["grain"] == sign) & geometric
        far_lo, far_hi = references[side]["distance_from_gb_interval_angstrom"]
        gb_distance = sign*(p[:, 1]-boundary["gb_y_angstrom"])
        bands = [("far_field", valid & (gb_distance >= far_lo) & (gb_distance <= far_hi), None)]
        bands += [(name, valid & (distance >= inner*nn) & (distance < outer*nn), [inner, outer])
                  for name, inner, outer in (("near", 0, 3), ("middle", 3, 6), ("outer", 6, 10))]
        for band_name, base, bounds_nn in bands:
            for phase, zmask in (("all", np.ones(len(p), dtype=bool)), ("z0", phases < .5), ("z1", phases >= .5)):
                mask = base & zmask
                mean, coverage = binned_profile(p, signed_values, mask, edges, sigma_bins=.65*nn/dx)
                absolute, _ = binned_profile(p, np.abs(signed_values), mask, edges, sigma_bins=.65*nn/dx)
                numerical = {}
                variant_spectra = {"spectrum": {}, "long_spectrum": {}}
                # A half-bin origin shift changes which actual atoms enter each
                # bin. Merely shifting interpolated profile x values is not a test.
                for variant, offset, sigma in (("half_x_bin_origin", .5*dx, .65*nn),
                                                ("smoothing_0.5_nn", 0., .5*nn),
                                                ("smoothing_0.8_nn", 0., .8*nn)):
                    shifted_edges = edges + offset
                    vx = .5*(shifted_edges[:-1]+shifted_edges[1:])
                    vm, cov = binned_profile(p, signed_values, mask, shifted_edges, sigma_bins=sigma/dx)
                    numerical[variant] = {"x_origin_shift_angstrom": float(offset),
                                          "smoothing_sigma_angstrom": float(sigma),
                                          "atom_rebinned": True, "coverage": cov,
                                          "signed_mean": [float(v) if np.isfinite(v) else None for v in vm]}
                    variant_spectra["spectrum"][variant] = spectral_profile(vx, vm, 2.5*nn, maximum)
                    if long_min_period is not None:
                        variant_spectra["long_spectrum"][variant] = spectral_profile(vx, vm, long_min_period, maximum)
                profile = {"grain": side, "distance_band": band_name, "z_channel": phase,
                           "distance_from_defect_band_nn": bounds_nn, "coverage": coverage,
                           "signed_mean": mean, "absolute_mean": absolute,
                           "spectrum": spectral_profile(x, mean, 2.5*nn, maximum),
                           "absolute_spectrum": spectral_profile(x, absolute, 2.5*nn, maximum),
                           "numerical_profile_variants": numerical,
                           "base_smoothing_sigma_angstrom": float(.65*nn),
                           "x_bin_width_angstrom": float(dx),
                           "far_field_reference_profile": None if band_name == "far_field" else f"{side}_far_field_{phase}"}
                if long_min_period is not None:
                    profile.update(long_spectrum=spectral_profile(x, mean, long_min_period, maximum),
                                   long_absolute_spectrum=spectral_profile(x, absolute, long_min_period, maximum))
                if band_name == "far_field":
                    profile["distance_from_gb_interval_angstrom"] = [far_lo, far_hi]
                    profile["reference_selection"] = "All valid crystalline atoms in the contiguous geometric band; no RMSD-quantile thinning."
                for key in ("spectrum", "long_spectrum"):
                    if key not in profile:
                        continue
                    far = profiles.get(f"{side}_far_field_{phase}", {}).get(key) if band_name != "far_field" else None
                    review_spectrum(profile[key], variant_spectra[key], far_field=far)
                profiles[f"{side}_{band_name}_{phase}"] = profile
    return x, profiles

def compare_periods(coordinate, x, profiles, spectrum_key="spectrum"):
    profiles = {k:v for k,v in profiles.items() if v.get("distance_band") != "far_field"}
    primary = {k:v for k,v in profiles.items() if v["z_channel"] == "all"}
    proposals = sorted([p for v in profiles.values() for p in v[spectrum_key].get("local_peaks", v[spectrum_key].get("peaks", []))
                        if p.get("review_eligible", False)],
                       key=lambda p:-p["relative_power"])
    periods = []
    extent = x[-1]-x[0]
    for proposal in proposals:
        t = proposal["period_angstrom"]
        if not any(abs(1/t-1/p) <= .75/extent for p in periods):
            periods.append(t)
    def compare(period):
        channels = {name: period_evidence(x, values["signed_mean"], values[spectrum_key], period)
                    for name, values in profiles.items()}
        consistent = [name for name in primary
                      if all(channels[name[:-3]+phase]["status"] == "strain_period_supported" for phase in ("z0", "z1"))]
        supporters = [name for name in primary if channels[name]["status"] == "strain_period_supported" or name in consistent]
        recurrence_consistent = [name for name in primary if all(
            channels[name[:-3]+phase].get("recurrence_supported", False) for phase in ("z0", "z1"))]
        recurrence_supporters = [name for name in primary if channels[name].get("recurrence_supported", False) or name in recurrence_consistent]
        projection_loss = [name for name in consistent if channels[name]["status"] != "strain_period_supported"]
        assessment = ("corroborated_by_layered_strain" if len(consistent) >= 2 else
                      "limited_strain_support" if supporters else "not_resolved_by_strain")
        if spectrum_key == "long_spectrum":
            assessment = ("long_band_candidate_with_recurrence" if len(consistent) >= 2 else
                          "limited_long_band_support" if supporters else "unconfirmed_long_band_candidate")
        return {"period_angstrom": period, "spectrum_key": spectrum_key, "supporting_channels": supporters,
                "recurrence_supporting_channels":recurrence_supporters,
                "z_consistent_recurrence_channels":recurrence_consistent,
                "recurrence_assessment":"multi_stratum_recurrence" if len(recurrence_consistent) >= 2 else
                                        "limited_recurrence" if recurrence_supporters else "recurrence_not_resolved",
                "z_consistent_supporting_channels": consistent, "supporting_grains": sorted({profiles[n]["grain"] for n in supporters}),
                "z_average_loses_support_channels":projection_loss,
                "assessment":assessment,
                "weak_global_power_channels":[name for name in supporters if channels[name].get("spectral_strength") == "weak_relative_to_full_spectrum"],
                "weak_global_power_z_channels":[name for name, item in channels.items() if
                    profiles[name]["z_channel"] != "all" and item["status"] == "strain_period_supported" and
                    item.get("spectral_strength") == "weak_relative_to_full_spectrum"],
                "evidence_note":"The assessment combines a review-eligible actual spectral maximum AND recurrence and is not a geometry candidate eligibility gate. All rejected actual maxima remain separately auditable; far-field peak overlap is never a strain veto. Recurrence is also reported independently: nonuniform local intervals and supercells need not have their own FFT peak. Band strength, local spacing, and full xyz matching describe different evidence.",
                "channels": channels}
    strain_proposals = [compare(t) for t in periods]
    strain_proposals.sort(key=lambda p: (-len(p["z_consistent_supporting_channels"]), -len(p["supporting_channels"]), p["period_angstrom"]))
    for p in strain_proposals:
        p["coordinate_neighbors"] = [{"id":c["id"], "period_angstrom":c["x_translation_angstrom"], "roles":c.get("roles", [])}
                                     for c in coordinate.get("candidates", []) if abs(1/c["x_translation_angstrom"]-1/p["period_angstrom"]) <= .75/extent]
        p["spectral_peak_sources"] = [{"channel":name,"z_channel":profile["z_channel"],**peak}
                                     for name, profile in profiles.items() for peak in profile[spectrum_key].get("local_peaks", profile[spectrum_key].get("peaks", []))
                                     if abs(1/peak["period_angstrom"]-1/p["period_angstrom"]) <= .75/extent]
        p["max_peak_power_over_global_peak"] = max((peak.get("power_over_global_peak", 0)
                                                   for peak in p["spectral_peak_sources"]),default=0)
    coordinate_comparisons = []
    for c in coordinate.get("candidates", []):
        row = compare(c["x_translation_angstrom"])
        row.update(candidate_id=c["id"], coordinate_roles=c.get("roles", []),
                   coordinate_near_rms=c.get("geometry", {}).get("near", {}).get("rms"),
                   coordinate_defect_rms=c.get("geometry", {}).get("defects", {}).get("rms"),
                   coordinate_status=c.get("status"),
                   coordinate_near_rms_over_nn=c.get("geometry", {}).get("near", {}).get("rms_over_nn"),
                   coordinate_near_coverage=c.get("geometry", {}).get("near", {}).get("coverage_fraction"),
                   coordinate_conflicting_evidence=c.get("conflicting_evidence", []),
                   coordinate_crystal_counts=c.get("crystal_counts", {}))
        coordinate_comparisons.append(row)
    strong = [p for p in strain_proposals if p["assessment"] == "corroborated_by_layered_strain"]
    for row in [*strain_proposals, *coordinate_comparisons]:
        row["possible_harmonic_relations"] = []
        for other in strong:
            ratio = row["period_angstrom"]/other["period_angstrom"]
            multiple = round(ratio)
            if multiple >= 2 and abs(multiple/row["period_angstrom"]-1/other["period_angstrom"]) <= .75/extent:
                row["possible_harmonic_relations"].append({"shorter_strain_wavelength_angstrom":other["period_angstrom"],
                    "approximate_multiple":multiple, "meaning":"An allowed supercell can recur without a spectral peak at its own fundamental frequency."})
    return strain_proposals, coordinate_comparisons


def write_strain_peak_review(out, profiles):
    records = [{"profile": name, "grain": profile["grain"], "distance_band": profile["distance_band"],
                "z_channel": profile["z_channel"], "spectrum": key, **peak}
               for name, profile in profiles.items()
               for key in ("spectrum", "long_spectrum")
               for peak in profile.get(key, {}).get("local_peaks", [])]
    keys = list(dict.fromkeys(key for row in records for key in row))
    target = out / "strain_fft_peaks.csv"
    with target.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys or ["profile", "period_angstrom"])
        writer.writeheader()
        for row in records:
            writer.writerow({k: json.dumps(v, ensure_ascii=False, allow_nan=False)
                             if isinstance(v, (dict, list)) else v for k, v in row.items()})
    review = {"peak_table": str(target.resolve()), "method": STRAIN_REVIEW_METHOD,
              "policy": STRAIN_REVIEW_POLICY,
              "background_references": {side: f"{side}_far_field_all" for side in ("lower", "upper")},
              "counts": {name: {key: {"audited_local_maxima": len(profile.get(key, {}).get("local_peaks", [])),
                                       "review_peaks": len(profile.get(key, {}).get("review_peaks", []))}
                                 for key in ("spectrum", "long_spectrum")}
                         for name, profile in profiles.items()},
              "meaning": "Absolute signed-strain power is comparable across depth and far-field channels; shared far-field peaks are descriptive, never rejected solely for overlap. Local extrema intervals, FFT wavelengths and spatial recurrence remain distinct."}
    save_json(out / "strain_fft_review.json", review)
    review["review_index"] = str((out / "strain_fft_review.json").resolve())
    return review


def strain_report_fields(coordinate, arrays, strain, boundary, references, long_min, out):
    x, profiles = build_profiles(arrays, strain, boundary, references, long_min)
    usable = {"resolved_profile", "no_resolved_strain_variation"}
    if not any(profile["spectrum"]["status"] in usable
               for profile in profiles.values() if profile["distance_band"] != "far_field"):
        raise MultiscaleUnavailable("No near-GB strain channel has sufficient domain and coverage for spectral review")
    proposals, comparisons = compare_periods(coordinate, x, profiles)
    long_proposals, long_comparisons = compare_periods(coordinate, x, profiles, "long_spectrum")
    long_comparisons = [p for p in long_comparisons if p["period_angstrom"] >= long_min]
    strong_short = [p for p in proposals if p["assessment"] == "corroborated_by_layered_strain"]
    for row in [*long_proposals, *long_comparisons]:
        row["possible_harmonic_relations"] = []
        for short in strong_short:
            multiple = round(row["period_angstrom"]/short["period_angstrom"])
            if multiple >= 2 and abs(multiple/row["period_angstrom"]-1/short["period_angstrom"]) <= .75/(x[-1]-x[0]):
                row["possible_harmonic_relations"].append({"shorter_strain_wavelength_angstrom":short["period_angstrom"],
                    "approximate_multiple":multiple, "meaning":"Numerical wavelength relation only; not proof of equal event spacing or a dislocation array."})
    sensitivity = {}
    for name, profile in profiles.items():
        if profile["z_channel"] != "all" or profile["distance_band"] == "far_field" or profile["spectrum"]["status"] != "resolved_profile":
            continue
        variants = {}
        for scale in (.995, 1.005):
            changed = .5-scale**2*(.5-profile["signed_mean"])
            spectral = spectral_profile(x, changed, *profile["spectrum"]["search_period_interval_angstrom"])
            variants[str(scale)] = spectral["peaks"][:3]
        sensitivity[name] = {"original_peaks": profile["spectrum"]["peaks"][:3],
                             "reference_scale_variants": variants,
                             "meaning": "Scalar-reference convention check; not a substitute for actual atom rebinning tests."}
    return {
        "strain_proposals": proposals, "coordinate_candidates_with_strain": comparisons,
        "long_band_strain_proposals": long_proposals, "long_band_coordinate_comparisons": long_comparisons,
        "x_angstrom": x.tolist(), "profiles": {name: {**profile,
            "signed_mean": [float(v) if np.isfinite(v) else None for v in profile["signed_mean"]],
            "absolute_mean": [float(v) if np.isfinite(v) else None for v in profile["absolute_mean"]]}
            for name, profile in profiles.items()},
        "reference_sensitivity": sensitivity, "strain_fft_review": write_strain_peak_review(out, profiles),
        "gates": {"minimum_cycles": 3, "minimum_signed_correlation": .5,
                  "spectral_match_native_frequency_bins": .75, "minimum_z_consistent_strata": 2,
                  "minimum_detrended_strain_std": 1e-7, "minimum_smoothed_coverage": .95,
                  "strain_peak_review": STRAIN_REVIEW_POLICY,
                  "global_relative_peak_power_gate": None, "fixed_peak_count_limit": None},
    }


def write_review_index(out, report):
    """Write a compact current index into the full numerical evidence."""
    index = {"status": report["status"], "source": report["source"], "source_sha256": report["source_sha256"],
             "full_report": str((out / "elastic_periodicity.json").resolve()),
             "coordinate_report": report["coordinate_report"],
             "references": {side: {k: r[k] for k in ("lattice", "a_reference_angstrom", "reference_kind")}
                            for side, r in report["references"].items()},
             "structure_fft_review": report["structure_fft_review"],
             "strain_fft_review": report["strain_fft_review"],
             "selected_candidate_id": None, "requires_user_decision": True,
             "summary_generation": "multiscale_review.py generates the current combined summary after local strain and coordinate-row analyses."}
    summary = out.parent / "multiscale_review" / "multiscale_summary.png"
    if summary.exists():
        index["summary_image"] = str(summary.resolve())
    save_json(out / "review_index.json", index)


def analyze(source, coordinate_dir, out):
    started = time.monotonic()
    out.mkdir(parents=True, exist_ok=True)
    cache = ptm_cache_path(coordinate_dir)
    metadata = json.loads((cache/"ptm_metadata.json").read_text())
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    if metadata["source_sha256"] != sha:
        raise ValueError("Baseline PTM fingerprint does not match input CFG")
    with np.load(cache/"ptm_atoms.npz", allow_pickle=False) as packed:
        arrays = {k: packed[k] for k in packed.files}
    boundary = json.loads((coordinate_dir/"boundary.json").read_text())
    coordinate = json.loads((coordinate_dir/"period_candidates.json").read_text())
    references, reference_masks = infer_far_field_reference(arrays, boundary)
    strain, version = calculate_strain(source, arrays, boundary, references, reference_masks)
    np.savez_compressed(out/"elastic_atoms.npz", **strain,
                        lower_reference_mask=reference_masks["lower"], upper_reference_mask=reference_masks["upper"])
    nn = boundary["nearest_neighbor_angstrom"]
    short_scale = coordinate.get("scale_partition", {}).get("data_derived_short_scale_angstrom")
    long_min = max(8*nn, 3*short_scale) if short_scale else 12*nn
    max_period = np.ptp(boundary["interior_x_interval_angstrom"])/3
    structure = build_structure_spectra(arrays, boundary, references, coordinate_dir, long_min, max_period)
    strain_fields = strain_report_fields(coordinate, arrays, strain, boundary, references, long_min, out)
    report = {
        "status":"completed", "source":str(source.resolve()), "source_sha256":sha,
        "coordinate_report":str((coordinate_dir/"period_candidates.json").resolve()), "ovito_version":version,
        "references":references, "valid_crystalline_atom_count":int(strain["valid"].sum()),
        "strain_definition":"Volumetric Strain = tr(Eulerian-Almansi elastic strain)/3; distinct from abs(det(F))-1",
        "strain_map_projection":"All valid near-interface atoms projected along z; no median-z slab selection. Quantitative z channels remain separate.",
        "reference_policy":"Separate effective far-field FCC/BCC references; full-system neighbor context, then valid bulk mask",
        **strain_fields,
        "structure_fft":structure,
        "structure_fft_review":write_structure_peak_review(out, structure),
        "long_band_policy":{"min_period_angstrom":long_min,"max_period_angstrom":float(max_period),
                            "coordinate_derived_short_scale_angstrom":short_scale,
                            "definition":"max(8 nearest-neighbor distances, 3 times the baseline data-derived short scale); 12 NN fallback",
                            "meaning":"Exploratory zoom interval; summary uses common absolute strain power, with separately normalized arrays retained only as supplemental shape evidence."},
        "coordinate_candidate_groups":coordinate.get("candidate_groups",{}),
        "elastic_atoms_sha256":hashlib.sha256((out/"elastic_atoms.npz").read_bytes()).hexdigest(),
        "selected_candidate_id":None,"requires_user_decision":True,
        "elapsed_seconds":time.monotonic()-started,
    }
    finalize_strain_metadata(report, out)
    save_json(out/"elastic_periodicity.json", report)
    write_review_index(out, report)
    return report
