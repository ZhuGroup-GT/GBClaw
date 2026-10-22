""" layered coordinate spectra for comparison with elastic strain.

The x FFT is evaluated independently in transverse y/z density channels.
Adding powers preserves a wavelength when different rows have opposite phases;
it is a pure-x spectral-power marginal, not proof of a 3D translation.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import numpy as np


METHOD = (
    "Count atoms in separate y/z density channels; remove each channel's linear "
    "x trend, apply a Hann window, and sum squared x-FFT magnitudes. Do not sum "
    "complex amplitudes or project atoms onto x before the transform. This is a "
    "pure-x spectral-power marginal, not a full xyz translation validation."
)

PEAK_REVIEW_METHOD = (
    "Retain actual local maxima separated by 0.75 native frequency bins without "
    "a global-relative-power threshold. Review uses local prominence, agreement "
    "under a half-bin transverse-origin shift, and spectral-shape contrast against "
    "each available far-field grain on the same x window/grid. Normalize each "
    "spectrum by its total positive-frequency power; this compares power fractions, "
    "not absolute scattering intensity, and no spectrum is subtracted. Background "
    "is the largest reference power fraction within half a native bin, regularized "
    "by max(reference median power fraction, 1e-6 of its largest fraction). These "
    "are review heuristics, not statistical significance or evidence of a dislocation."
)

PEAK_REVIEW_POLICY = {
    "local_prominence_ratio_minimum": .5,
    "prominence_over_local_floor_minimum": 2.,
    "background_shape_ratio_minimum": 3.,
    "reference_strong_peak_fraction_of_reference_maximum": .01,
    "reference_regularization_fraction_of_reference_maximum": 1e-6,
    "reference_regularization_empirical_floor": "median of all positive-frequency power fractions",
    "reference_and_query_frequency_tolerance_native_bins": .5,
    "local_maximum_separation_native_bins": .75,
    "transverse_grid_required_fraction": 1.,
    "transverse_fractional_origins": [[0., 0.], [.5, .5]],
    "shifted_grid_local_prominence_ratio_minimum": .2,
    "shifted_grid_normalized_peak_power_ratio_interval": [.25, 4.],
    "interpretation": "Fixed review heuristics, not a detection probability; every resolution-separated local maximum remains in local_peaks regardless of review eligibility.",
}


def _normalized_power(frequency, power):
    total = float(np.sum(power[frequency > 0]))
    return power/total if total > 1e-20 else np.zeros_like(power)


def _maxima_indices(frequency, power, extent):
    """Actual maxima only; zero padding never supplies additional resolution."""
    maxima = np.flatnonzero((power[1:-1] > power[:-2]) & (power[1:-1] >= power[2:]))+1
    chosen = []
    occupied = np.zeros(len(frequency), dtype=bool)
    radius = int(np.ceil((.75/extent)/(frequency[1]-frequency[0])))-1
    for i in maxima[np.argsort(-power[maxima], kind="stable")]:
        if power[i] > 0 and not occupied[i]:
            chosen.append(int(i))
            occupied[max(0, i-radius):i+radius+1] = True
    return chosen


def _peak_record(i, frequency, power, extent):
    native = 1/extent
    radius = max(1, int(np.ceil(2*native/(frequency[1]-frequency[0]))))
    left, right = power[max(0, i-radius):i], power[i+1:min(len(power), i+radius+1)]
    base = max(float(left.min()), float(right.min())) if len(left) and len(right) else float(power[i])
    side = np.r_[left[:max(1, len(left)//2)], right[len(right)//2:]]
    local_floor = float(np.median(side)) if len(side) else float(power[i])
    peak = float(power[i])
    return {
        "period_angstrom": float(1/frequency[i]),
        "frequency_inverse_angstrom": float(frequency[i]),
        "raw_power": peak,
        "observable_cycles": float(extent*frequency[i]),
        "resolution_interval_angstrom": [float(1/(frequency[i]+.5*native)),
            float(1/(frequency[i]-.5*native)) if frequency[i] > .5*native else None],
        "local_prominence_ratio": max(0., (peak-base)/max(peak, 1e-300)),
        "prominence_over_local_floor": max(0., peak-base)/max(local_floor, peak*1e-12, 1e-300),
    }


def _review_maxima(frequency, power, extent, references, shifted_power, period_interval=None):
    """Build a band-independent audit; no expected structural periods enter it."""
    normalized = _normalized_power(frequency, power)
    native = 1/extent
    shifted_maxima = np.sort(np.asarray(_maxima_indices(frequency, shifted_power, extent), dtype=int)) if shifted_power is not None else []
    shifted_normalized = _normalized_power(frequency, shifted_power) if shifted_power is not None else None
    refs = {}
    for name, ref in references.items():
        if ref["status"] != "resolved_spectrum":
            continue
        norm = _normalized_power(frequency, ref["power"])
        floor = max(float(np.median(norm[frequency > 0])), 1e-6*float(norm.max()), 1e-300)
        prominent = [j for j in _maxima_indices(frequency, ref["power"], extent)
                     if norm[j] >= .01*norm.max()]
        refs[name] = (norm, floor, np.sort(frequency[prominent]))
    audit = []
    for i in _maxima_indices(frequency, power, extent):
        if period_interval is not None and not period_interval[0] <= 1/frequency[i] <= period_interval[1]:
            continue
        peak = _peak_record(i, frequency, power, extent)
        peak["normalized_power"] = float(normalized[i])
        support = 1
        peak["shifted_grid_peak_period_angstrom"] = None
        peak["shifted_grid_normalized_power_ratio"] = None
        if shifted_power is not None:
            start_shift = np.searchsorted(frequency[shifted_maxima], frequency[i]-.5*native)
            end_shift = np.searchsorted(frequency[shifted_maxima], frequency[i]+.5*native, side="right")
            compatible = shifted_maxima[start_shift:end_shift]
            if len(compatible):
                j = max(compatible, key=lambda j: shifted_power[j])
                ratio = float(shifted_normalized[j]/max(normalized[i], 1e-300))
                peak["shifted_grid_peak_period_angstrom"] = float(1/frequency[j])
                peak["shifted_grid_normalized_power_ratio"] = ratio
                if .25 <= ratio <= 4. and _peak_record(j, frequency, shifted_power, extent)["local_prominence_ratio"] >= .2:
                    support += 1
        peak["transverse_grid_support"] = support
        peak["transverse_grid_count"] = 2 if shifted_power is not None else 1
        backgrounds = {}
        start = np.searchsorted(frequency, frequency[i]-.5*native)
        end = np.searchsorted(frequency, frequency[i]+.5*native, side="right")
        for name, (norm, floor, prominent) in refs.items():
            value = float(norm[start:max(end, start+1)].max())
            backgrounds[name] = {
                "normalized_power_neighborhood_max": value,
                "regularization_floor": floor,
                "shape_power_ratio": float((normalized[i]+floor)/(value+floor)),
                "bounded_shape_contrast": float((normalized[i]-value)/(normalized[i]+value+floor)),
                "strong_peak_overlap": bool(np.searchsorted(prominent, frequency[i]+.5*native, side="right") >
                                            np.searchsorted(prominent, frequency[i]-.5*native)),
            }
        peak["background_comparison"] = backgrounds
        peak["background_contrast"] = min((v["shape_power_ratio"] for v in backgrounds.values()), default=None)
        peak["bounded_background_contrast"] = min((v["bounded_shape_contrast"] for v in backgrounds.values()), default=None)
        peak["background_peak_overlap"] = any(v["strong_peak_overlap"] for v in backgrounds.values())
        locally_resolved = peak["local_prominence_ratio"] >= .5 and peak["prominence_over_local_floor"] >= 2
        stable = support == peak["transverse_grid_count"]
        enhanced = peak["background_contrast"] is not None and peak["background_contrast"] >= 3
        peak["review_eligible"] = bool(locally_resolved and stable and (enhanced or not refs))
        if not locally_resolved or not stable:
            classification = "weak_or_grid_sensitive"
        elif not refs:
            classification = "unreferenced"
        elif enhanced:
            classification = "gb_enhanced"
        else:
            classification = "bulk_like" if peak["background_peak_overlap"] else "not_background_enhanced"
        peak["classification"] = classification
        audit.append(peak)
    return audit


def _selection_masks(arrays, boundary, selection):
    p = np.asarray(arrays["positions"], dtype=float)
    bounds = np.asarray(boundary["occupied_bounds_angstrom"], dtype=float)
    lo, hi = boundary["interior_x_interval_angstrom"]
    margin = boundary.get("normal_exclusion_margin_angstrom", 3*boundary["nearest_neighbor_angstrom"])
    core = (p[:, 0] >= lo) & (p[:, 0] <= hi)
    core &= (p[:, 1] > bounds[0, 1]+margin) & (p[:, 1] < bounds[1, 1]-margin)
    for axis, name in ((0, "x"), (2, "z")):
        lateral = boundary.get("lateral_exclusion_margins_angstrom", {}).get(name)
        if lateral is not None:
            core &= (p[:, axis] > bounds[0, axis]+lateral) & (p[:, axis] < bounds[1, axis]-lateral)
    if selection is not None:
        if isinstance(selection, (str, Path)):
            with np.load(selection, allow_pickle=False) as packed:
                selection = {name: packed[name] for name in ("near", "defects")}
        if not isinstance(selection, Mapping):
            raise TypeError("selection must be a mask mapping or a boundary_selection.npz path")
        masks = {}
        for name in ("near", "defects"):
            mask = np.asarray(selection[name])
            if mask.shape != (len(p),) or mask.dtype.kind != "b":
                raise ValueError(f"{name} selection must be a per-atom boolean mask")
            masks[name] = mask & core
        return masks, "saved_boundary_selection_with_surface_exclusion_reapplied"
    near_lo, near_hi = boundary["near_boundary_y_angstrom"]
    defect_lo, defect_hi = boundary["defect_band_y_angstrom"]
    types = np.asarray(arrays["types"])
    if types.shape != (len(p),):
        raise ValueError("PTM structure types must correspond to input atoms")
    return {
        "near": core & (p[:, 1] >= near_lo) & (p[:, 1] <= near_hi),
        "defects": core & (types != boundary["dominant_bulk_structure_type"])
                   & (p[:, 1] >= defect_lo) & (p[:, 1] <= defect_hi),
    }, "reconstructed_from_detected_boundary_and_ptm_types"


def _band_spectrum(frequency, power, minimum, maximum, extent, *, audit=None, references=None):
    band = (frequency >= 1/maximum) & (frequency <= 1/minimum)
    total = float(np.sum(power[frequency > 0]))
    band_power = float(np.sum(power[band]))
    result = {
        "status": "resolved_spectrum" if total > 1e-20 and band_power > 1e-20 else "no_resolved_density_variation",
        "frequency_inverse_angstrom": frequency[band].tolist(),
        "relative_power": [], "peaks": [],
        "raw_power": power[band].tolist(),
        "normalized_power": _normalized_power(frequency, power)[band].tolist(),
        "total_positive_frequency_power": total,
        "band_integrated_power": band_power,
        "band_power_fraction": band_power/total if total > 0 else 0.,
        "native_frequency_resolution_inverse_angstrom": 1/extent,
        "window_angstrom": extent, "search_period_interval_angstrom": [minimum, maximum],
        "normalization": "relative_power is normalized to the maximum within this displayed band",
        "band_power_fraction_definition": "Band-integrated power divided by all positive-frequency power through the density-grid Nyquist frequency.",
        "normalized_power_definition": "Power fraction in each Fourier sample divided by all positive-frequency power; spectral shape only, not absolute intensity. The full and long bands share the same denominator.",
    }
    maximum_power = float(np.max(power[band])) if band.any() else 0.
    relative = power/max(maximum_power, 1e-30)
    result["relative_power"] = relative[band].tolist()
    if audit is not None:
        result["local_peaks"] = [dict(peak, relative_power=peak["raw_power"]/max(maximum_power, 1e-30))
                                 for peak in audit if minimum <= peak["period_angstrom"] <= maximum]
        result["review_peaks"] = [peak for peak in result["local_peaks"] if peak["review_eligible"]]
        result["peak_review_method"] = PEAK_REVIEW_METHOD
        result["peak_review_policy"] = PEAK_REVIEW_POLICY
        valid = [name for name, ref in (references or {}).items() if ref["status"] == "resolved_spectrum"]
        result["peak_review_status"] = "bulk_referenced" if valid else "unreferenced_local_prominence_fallback"
        result["background_references"] = {}
        for name, ref in (references or {}).items():
            spectrum = _band_spectrum(frequency, ref["power"], minimum, maximum, extent)
            spectrum.update(ref["details"], selected_atom_count=ref["atom_count"])
            if ref["status"] != "resolved_spectrum":
                spectrum["status"] = ref["status"]
            result["background_references"][name] = spectrum
    if result["status"] != "resolved_spectrum":
        return result
    local_maxima = np.flatnonzero((power[1:-1] > power[:-2]) & (power[1:-1] >= power[2:]))+1
    maxima = [i for i in local_maxima if band[i] and relative[i] >= .05]
    maxima.sort(key=lambda i: -power[i])
    chosen = []
    for i in maxima:
        if all(abs(frequency[i]-frequency[j]) >= .75/extent for j in chosen):
            chosen.append(i)
        if len(chosen) == 12:
            break
    result["peaks"] = [{
        "period_angstrom": float(1/frequency[i]), "relative_power": float(relative[i]),
        "observable_cycles": float(extent*frequency[i]),
        "resolution_interval_angstrom": [float(1/(frequency[i]+.5/extent)),
            float(1/(frequency[i]-.5/extent)) if frequency[i] > .5/extent else None],
    } for i in chosen]
    return result


def match_structure_peak(channel, period_angstrom):
    """Audit a proposed length against an actual maximum, without inventing one.

    Match tolerance is half a *native* frequency bin, not a fraction of length
    or the finer zero-padded bin width. Review eligibility is reported separately.
    """
    period = float(period_angstrom)
    if not np.isfinite(period) or period <= 0:
        raise ValueError("The queried period must be positive and finite")
    native = float(channel["native_frequency_resolution_inverse_angstrom"])
    interval = channel["search_period_interval_angstrom"]
    result = {"queried_period_angstrom": period, "status": "no_resolved_peak",
              "matched_peak": None, "frequency_error_native_bins": None,
              "matching_tolerance_native_bins": .5,
              "interpretation": "A match associates a proposed length with an actual density-spectrum maximum; it does not establish a full xyz repeat."}
    if not interval[0] <= period <= interval[1]:
        result["status"] = "outside_observable_band"
        return result
    peaks = channel.get("local_peaks", [])
    if not peaks:
        return result
    closest = min(peaks, key=lambda peak: abs(1/peak["period_angstrom"]-1/period))
    offset = abs(1/closest["period_angstrom"]-1/period)/native
    result["frequency_error_native_bins"] = float(offset)
    if offset <= .5:
        result["status"] = "matched_actual_peak"
        result["matched_peak"] = dict(closest)
    return result


def _layered_power(positions, cell, interval, spacing, *, transverse_origin=(0., 0.)):
    """Accumulate a bounded batch of occupied y/z channels at a time."""
    lo, hi = interval
    nx = max(32, int(np.ceil((hi-lo)/spacing[0])))
    dx = (hi-lo)/nx
    length_z = float(cell[2, 2])
    nz = max(1, int(np.ceil(length_z/spacing[2])))
    dz = length_z/nz
    nfft = 2**int(np.ceil(np.log2(4*nx)))
    frequency = np.fft.rfftfreq(nfft, dx)
    power = np.zeros(len(frequency))
    details = {"x_bin_count": nx, "z_bin_count": nz, "fft_sample_count": nfft,
               "grid_spacing_angstrom": [dx, spacing[1], dz], "transverse_channel_count": 0,
               "x_padding_note": "Zero padding samples the spectrum more finely and adds no physical resolution."}
    if not len(positions):
        return frequency, power, details
    ix = np.minimum(np.floor((positions[:, 0]-lo)/dx).astype(int), nx-1)
    iy = np.floor(positions[:, 1]/spacing[1]-transverse_origin[0]).astype(np.int64)
    iz = np.floor(((positions[:, 2]-cell[2, 3]-transverse_origin[1]*dz) % length_z)/dz).astype(int)
    iz = np.minimum(iz, nz-1)
    channel = (iy-iy.min())*nz+iz
    occupied, inverse = np.unique(channel, return_inverse=True)
    details["transverse_channel_count"] = len(occupied)
    order = np.argsort(inverse, kind="stable")
    ordered_channels = inverse[order]
    centered_x = (np.arange(nx)-(nx-1)/2)*dx
    x_energy = np.dot(centered_x, centered_x)
    window = np.hanning(nx)
    # The dense allocation is only batch_size*nx, independent of all y/z bins.
    batch_size = max(1, min(32, 2_000_000//nfft))
    for start in range(0, len(occupied), batch_size):
        end = min(start+batch_size, len(occupied))
        first, last = np.searchsorted(ordered_channels, (start, end))
        atoms = order[first:last]
        density = np.zeros((end-start, nx))
        np.add.at(density, (inverse[atoms]-start, ix[atoms]), 1.)
        density -= density.mean(axis=1, keepdims=True)
        slopes = density @ centered_x/x_energy
        density -= slopes[:, None]*centered_x[None, :]
        transformed = np.fft.rfft(density*window, n=nfft, axis=1)
        power += np.sum(np.abs(transformed)**2, axis=0)
    return frequency, power, details


def coordinate_spectra(arrays, boundary, min_period, max_period, *, long_min_period=None, selection=None,
                       reference_masks=None):
    """Return JSON-serializable near/defect density spectra without ratio priors.

    ``arrays`` is the existing PTM-cache mapping; ``boundary`` is boundary.json.
    ``selection`` optionally supplies saved near/defects boolean masks or their
    boundary_selection.npz path. If omitted, these masks are reconstructed.
    ``long_min_period`` adds a separately normalized ``long_band`` per channel,
    using the same power and full-positive-frequency denominator. The specified
    period ranges never seed a chosen physical period or dislocation spacing.

    ``reference_masks`` contains optional per-grain far-field boolean masks from
    contiguous geometric slabs. Residual/strain-quantile atom filtering must not
    be used to define these masks: spatial holes can create artificial spectra.
    """
    min_period, max_period = float(min_period), float(max_period)
    if not np.all(np.isfinite([min_period, max_period])) or not 0 < min_period < max_period:
        raise ValueError("Require finite 0 < min_period < max_period")
    if long_min_period is not None:
        long_min_period = float(long_min_period)
        if not np.isfinite(long_min_period) or long_min_period < min_period:
            raise ValueError("long_min_period must be finite and at least min_period")
    p = np.asarray(arrays["positions"], dtype=float)
    cell = np.asarray(arrays["cell"], dtype=float)
    if p.ndim != 2 or p.shape[1] != 3 or not np.isfinite(p).all():
        raise ValueError("Coordinates must be finite Nx3 Cartesian positions")
    if cell.shape != (3, 4) or not np.isfinite(cell).all():
        raise ValueError("An OVITO 3x4 simulation cell is required")
    if not np.allclose(cell[:, :3], np.diag(np.diag(cell[:, :3])), atol=1e-8) or np.any(np.diag(cell[:, :3]) <= 0):
        raise ValueError("Layered coordinate spectra require a positive axis-aligned orthogonal cell")
    if not bool(arrays["pbc"][2]) or "z" in boundary.get("vacuum_axes", []):
        raise ValueError("Layered coordinate spectra require a periodic vacuum-free z direction")
    interval = np.asarray(boundary["interior_x_interval_angstrom"], dtype=float)
    if interval.shape != (2,) or not np.isfinite(interval).all() or interval[1] <= interval[0]:
        raise ValueError("Require a finite increasing interior x interval")
    nn = float(boundary["nearest_neighbor_angstrom"])
    if not np.isfinite(nn) or nn <= 0:
        raise ValueError("Require a positive nearest-neighbor distance")
    spacing = [min(.06*nn, min_period/8), .12*nn, .06*nn]
    masks, selection_source = _selection_masks(arrays, boundary, selection)
    channels = {}
    extent = float(interval[1]-interval[0])
    references = {}
    if reference_masks is not None and not isinstance(reference_masks, Mapping):
        raise TypeError("reference_masks must be a mapping of per-grain boolean masks")
    for name, raw_mask in (reference_masks or {}).items():
        mask = np.asarray(raw_mask)
        if mask.shape != (len(p),) or mask.dtype.kind != "b":
            raise ValueError(f"{name} reference selection must be a per-atom boolean mask")
        mask = mask & (p[:, 0] >= interval[0]) & (p[:, 0] <= interval[1])
        frequency, power, details = _layered_power(p[mask], cell, interval, spacing)
        references[name] = {"power": power, "details": details, "atom_count": int(mask.sum()),
                            "status": "resolved_spectrum" if mask.any() and power.sum() > 1e-20
                                      else "insufficient_reference_variation"}
    for name, mask in masks.items():
        frequency, power, details = _layered_power(p[mask], cell, interval, spacing)
        _, shifted_power, _ = _layered_power(p[mask], cell, interval, spacing, transverse_origin=(.5, .5))
        audit = _review_maxima(frequency, power, extent, references, shifted_power, (min_period, max_period))
        result = _band_spectrum(frequency, power, min_period, max_period, extent, audit=audit, references=references)
        result.update(details, selected_atom_count=int(mask.sum()), method=METHOD)
        result["transverse_grid_check"] = {"fractional_origins": [[0., 0.], [.5, .5]],
                                            "interpretation": "Binning sensitivity check; not independent statistical replicates."}
        if not mask.any():
            result["status"] = "insufficient_selected_atoms"
        if long_min_period is not None:
            result["long_band"] = _band_spectrum(frequency, power, long_min_period, max_period, extent,
                                                 audit=audit, references=references)
            if long_min_period >= max_period:
                result["long_band"].update(status="insufficient_long_window", frequency_inverse_angstrom=[],
                    relative_power=[], raw_power=[], normalized_power=[], peaks=[], local_peaks=[], review_peaks=[], band_power_fraction=0.,
                    reason="The requested long-scale minimum reaches or exceeds the observable period maximum.")
        channels[name] = result
    return {"method": METHOD, "peak_review_method": PEAK_REVIEW_METHOD, "peak_review_policy": PEAK_REVIEW_POLICY,
            "selection_source": selection_source, "channels": channels,
            "interior_x_interval_angstrom": interval.tolist(), "expected_ratio_used": False,
            "interpretation_limits": [
                "Layer-resolved power prevents phase cancellation but discards relative phases; shared spectral peaks do not establish a full xyz translation.",
                "No x/y periodic wrapping or artificial atom replication is used; z positions wrap into the original physical cell.",
                "Band power is grid- and selection-dependent; a separately normalized long-band peak can carry little total power.",
                "Harmonics, finite-window leakage and density-bin boundary sensitivity remain possible; peak resolution ranges are not confidence intervals.",
                "The legacy peaks list retains the former 5%-of-band-maximum display rule for compatibility; local_peaks and review_peaks do not use that global gate.",
                "Far-field comparison measures spectral-shape redistribution, not absolute density/scattering amplification; disorder can change its positive-frequency normalization denominator.",
                "No wavelength multiple or harmonic is deleted; a strong bulk-reference peak overlap is a flag, not a veto on structural evidence.",
                "A lattice peak shifted by local homogeneous strain beyond the half-native-bin background tolerance may appear enhanced; enhancement does not certify a non-lattice origin.",
            ]}
