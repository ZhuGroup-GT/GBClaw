"""Auditable signed-strain spectra for the  periodicity prototype.

An actual local maximum, a repeating local interval and a translation recurrence
are separate observations. No proposed coordinate length can manufacture a peak.
Absolute strain units are retained so a nearly zero far field is not magnified
into a strong reference by independent maximum normalization.
"""
from __future__ import annotations

import numpy as np

from .structure_spectrum import _maxima_indices, _peak_record


REVIEW_POLICY = {
    "minimum_detrended_strain_std": 1e-7,
    "minimum_peak_amplitude_strain": 1e-7,
    "minimum_local_prominence_ratio": .5,
    "minimum_prominence_over_local_floor": 2.,
    "matching_frequency_tolerance_native_bins": .75,
    "variant_amplitude_ratio_interval": [.25, 4.],
    "required_numerical_variant_fraction": 1.,
    "minimum_variant_local_prominence_ratio": .2,
    "window_sensitivity_check": "Hann versus Blackman; matched actual peak coherent amplitude ratio must be in [0.5, 2] and exceed the absolute floor",
    "minimum_smoothed_coverage_fraction": .95,
    "maximum_interpolated_gap_bins": 3,
    "far_field_enhancement_power_ratio": 3.,
    "far_field_peak_overlap_is_veto": False,
}
REVIEW_METHOD = (
    "Retain every resolution-separated actual maximum, without a maximum-relative "
    "power cutoff or a fixed peak count. Review local prominence and absolute "
    "strain amplitude, then require consistent peak frequency/amplitude under "
    "a Blackman-window comparison to flag Hann leakage, and actual atom rebinning with a half-x-bin origin shift and neighboring Gaussian "
    "smoothing widths. Same-grain far-field signed strain uses the same x window, "
    "binning, baseline, z channel and physical power units. Shared far-field peaks "
    "are descriptive evidence and never a veto: elastic fields may extend into "
    "the far field. These are fixed review heuristics, not significance tests."
)


def spectral_profile(x, values, min_period, max_period):
    """Hann spectrum with all actual maxima and physically scaled power.

This low-level function cannot test atom binning robustness. Peaks passing only
signal checks are explicitly marked ``signal_only_numerics_untested``; production
profiles subsequently call :func:`review_spectrum` with rebuilt atom profiles.
    """
    x, values = np.asarray(x, dtype=float), np.asarray(values, dtype=float)
    ok = np.isfinite(values)
    unresolved = {"peaks": [], "local_peaks": [], "review_peaks": [],
                  "review_method": REVIEW_METHOD, "review_policy": REVIEW_POLICY}
    if len(x) < 32 or len(x) != len(values) or ok.mean() < .95 or max_period <= min_period:
        return {**unresolved, "status": "insufficient_coverage_or_domain"}
    if not np.all(np.isfinite(x)) or not np.allclose(np.diff(x), np.median(np.diff(x)), rtol=1e-6, atol=1e-10):
        return {**unresolved, "status": "nonuniform_or_invalid_x_grid"}
    gaps = np.split(np.flatnonzero(~ok), np.flatnonzero(np.diff(np.flatnonzero(~ok)) > 1)+1)
    if not ok[0] or not ok[-1] or any(len(gap) > 3 for gap in gaps):
        return {**unresolved, "status": "unresolved_missing_data_gaps",
                "longest_gap_bins": max(map(len, gaps), default=0)}
    filled = np.interp(x, x[ok], values[ok])
    residual = filled - np.polyval(np.polyfit(x-x.mean(), filled, 1), x-x.mean())
    amplitude = float(np.std(residual))
    dx = float(np.median(np.diff(x)))
    extent = len(x)*dx
    if amplitude < REVIEW_POLICY["minimum_detrended_strain_std"]:
        return {**unresolved, "status": "no_resolved_strain_variation", "detrended_std": amplitude}
    window = np.hanning(len(x))
    nfft = 2**int(np.ceil(np.log2(len(x)*8)))
    frequency = np.fft.rfftfreq(nfft, dx)
    raw = np.abs(np.fft.rfft(residual*window, n=nfft))**2
    # One-sided Parseval normalization: the sum gives the window-weighted mean
    # squared residual, regardless of zero padding. Units remain strain^2.
    absolute = 2*raw/(nfft*np.sum(window**2))
    absolute[0] *= .5
    absolute[-1] *= .5
    coherent_amplitude = 2*np.sqrt(raw)/np.sum(window)
    alternate_window = np.blackman(len(x))
    alternate_raw = np.abs(np.fft.rfft(residual*alternate_window, n=nfft))**2
    alternate_amplitude = 2*np.sqrt(alternate_raw)/np.sum(alternate_window)
    alternate_maxima = _maxima_indices(frequency, alternate_raw, extent)
    total = float(absolute[1:].sum())
    band = (frequency >= 1/max_period) & (frequency <= 1/min_period)
    band_maximum = float(raw[band].max()) if band.any() else 0.
    global_maximum = max(float(raw[1:].max()), 1e-300)
    peaks = []
    for i in _maxima_indices(frequency, raw, extent):
        if not band[i]:
            continue
        peak = _peak_record(i, frequency, raw, extent)
        reasons = []
        if peak["local_prominence_ratio"] < .5 or peak["prominence_over_local_floor"] < 2:
            reasons.append("insufficient_local_prominence")
        if coherent_amplitude[i] < REVIEW_POLICY["minimum_peak_amplitude_strain"]:
            reasons.append("below_absolute_strain_amplitude_floor")
        matches = [j for j in alternate_maxima if abs(frequency[j]-frequency[i]) <= .75/extent]
        j = max(matches, key=lambda j: alternate_amplitude[j], default=None)
        window_ratio = float(alternate_amplitude[j]/max(coherent_amplitude[i], 1e-300)) if j is not None else None
        window_stable = j is not None and .5 <= window_ratio <= 2. and alternate_amplitude[j] >= REVIEW_POLICY["minimum_peak_amplitude_strain"]
        if not window_stable:
            reasons.append("window_sensitive_possible_leakage")
        peak.update(
            relative_power=float(raw[i]/max(band_maximum, 1e-300)),
            power_over_global_peak=float(raw[i]/global_maximum),
            absolute_power_strain_squared=float(absolute[i]),
            normalized_power=float(absolute[i]/max(total, 1e-300)),
            amplitude_strain=float(coherent_amplitude[i]),
            review_eligible=not reasons, rejection_reasons=reasons,
            review_scope="signal_only_numerics_untested",
            numerical_stability_status="untested",
            far_field_relation="not_compared",
            taper_stability={"status": "passed" if window_stable else "failed",
                             "reference_window": "Hann", "comparison_window": "Blackman",
                             "amplitude_ratio": window_ratio,
                             "matched_period_angstrom": float(1/frequency[j]) if j is not None else None},
        )
        peaks.append(peak)
    return {"status": "resolved_profile", "detrended_std": amplitude,
            "native_frequency_resolution_inverse_angstrom": 1/extent,
            "window_angstrom": extent, "search_period_interval_angstrom": [min_period, max_period],
            "band_power_fraction": float(absolute[band].sum()/max(total, 1e-300)),
            "band_peak_over_global_peak": band_maximum/global_maximum,
            "frequency_inverse_angstrom": frequency[band].tolist(),
            "relative_power": (raw[band]/max(band_maximum, 1e-300)).tolist(),
            "raw_power": raw[band].tolist(),
            "absolute_power_strain_squared": absolute[band].tolist(),
            "amplitude_strain": coherent_amplitude[band].tolist(),
            "normalized_power": (absolute[band]/max(total, 1e-300)).tolist(),
            "total_positive_frequency_power_strain_squared": total,
            "absolute_power_definition": "One-sided |FFT(w*residual)|^2/(Nfft*sum(w^2)); sum equals Hann-weighted residual mean square; DC excluded from reported total.",
            "amplitude_definition": "2*|FFT|/sum(Hann); coherent single-sinusoid amplitude estimate, not a confidence bound.",
            "review_method": REVIEW_METHOD, "review_policy": REVIEW_POLICY,
            "peaks": peaks, "local_peaks": peaks,
            "review_peaks": [p for p in peaks if p["review_eligible"]]}


def review_spectrum(spectrum, variants, far_field=None):
    """Attach numerical review and amplitude-preserving far-field comparisons.

``variants`` are independently rebuilt spectra, not resampled copies of the
original signal. A missing or unresolved requested variant fails that check.
    """
    if spectrum.get("status") != "resolved_profile":
        return spectrum
    tolerance = .75*spectrum["native_frequency_resolution_inverse_angstrom"]
    for peak in spectrum["local_peaks"]:
        reasons = [r for r in peak["rejection_reasons"] if r != "numerical_variant_inconsistent"]
        checks = {}
        for name, variant in variants.items():
            matches = [p for p in variant.get("local_peaks", [])
                       if abs(p["frequency_inverse_angstrom"]-peak["frequency_inverse_angstrom"]) <= tolerance]
            match = max(matches, key=lambda p:p["amplitude_strain"], default=None)
            ratio = match["amplitude_strain"]/peak["amplitude_strain"] if match else None
            stable = match is not None and .25 <= ratio <= 4. and match["local_prominence_ratio"] >= .2
            checks[name] = {"status": "consistent" if stable else "unresolved_or_inconsistent",
                            "profile_status": variant.get("status"),
                            "matched_period_angstrom": match["period_angstrom"] if match else None,
                            "amplitude_ratio": ratio,
                            "matched_amplitude_strain": match["amplitude_strain"] if match else None}
        if checks and not all(c["status"] == "consistent" for c in checks.values()):
            reasons.append("numerical_variant_inconsistent")
        peak["numerical_stability"] = checks
        peak["numerical_stability_status"] = ("passed" if all(c["status"] == "consistent" for c in checks.values())
                                               else "failed") if checks else "untested"
        peak["review_scope"] = "profile_and_numerical_variants" if checks else "signal_only_numerics_untested"
        peak["rejection_reasons"] = reasons
        peak["review_eligible"] = not reasons
        peak["far_field_relation"] = "not_compared"
        peak["far_field_comparison"] = None
        if far_field is not None:
            if far_field.get("status") != "resolved_profile":
                peak["far_field_relation"] = "far_field_unresolved_or_below_floor"
                peak["far_field_comparison"] = {"status": far_field.get("status"), "used_as_veto": False}
                continue
            f = np.asarray(far_field["frequency_inverse_angstrom"])
            absolute = np.asarray(far_field["absolute_power_strain_squared"])
            close = np.abs(f-peak["frequency_inverse_angstrom"]) <= tolerance
            value = float(absolute[close].max()) if close.any() else None
            matches = [p for p in far_field.get("local_peaks", []) if p["review_eligible"]
                       and abs(p["frequency_inverse_angstrom"]-peak["frequency_inverse_angstrom"]) <= tolerance]
            match = max(matches, key=lambda p:p["amplitude_strain"], default=None)
            ratio = peak["absolute_power_strain_squared"]/value if value is not None and value > 1e-300 else None
            relation = "shared_with_far_field" if match else "no_resolved_far_field_peak"
            if ratio is not None and ratio >= REVIEW_POLICY["far_field_enhancement_power_ratio"]:
                relation = "enhanced_near_gb_and_shared" if match else "enhanced_near_gb"
            peak["far_field_relation"] = relation
            peak["far_field_comparison"] = {
                "absolute_power_neighborhood_max_strain_squared": value,
                "absolute_power_ratio": ratio,
                "matched_period_angstrom": match["period_angstrom"] if match else None,
                "matched_amplitude_strain": match["amplitude_strain"] if match else None,
                "shared_actual_peak": bool(match), "used_as_veto": False,
            }
    spectrum["peaks"] = spectrum["local_peaks"]
    spectrum["review_peaks"] = [p for p in spectrum["local_peaks"] if p["review_eligible"]]
    spectrum["numerical_variant_names"] = list(variants)
    return spectrum


def match_strain_peak(spectrum, period):
    """Query existing actual maxima only, including ineligible nearby peaks."""
    if not np.isfinite(period) or period <= 0 or spectrum.get("status") != "resolved_profile":
        return {"status": "unresolved_channel", "matched_peak": None}
    low, high = spectrum["search_period_interval_angstrom"]
    if not low <= period <= high:
        return {"status": "outside_observable_band", "matched_peak": None,
                "review_eligible": False, "search_period_interval_angstrom": [low, high]}
    native = spectrum["native_frequency_resolution_inverse_angstrom"]
    peaks = spectrum.get("local_peaks", spectrum.get("peaks", []))
    near = [p for p in peaks if abs(1/p["period_angstrom"]-1/period) <= .75*native]
    best = min(near, key=lambda p:abs(1/p["period_angstrom"]-1/period), default=None)
    return {"status": "review_peak_match" if best and best.get("review_eligible") else
                      "actual_peak_fails_review" if best else "no_actual_peak_within_resolution",
            "matched_peak": best, "period_angstrom": float(period),
            "review_eligible": bool(best and best.get("review_eligible")),
            "frequency_tolerance_inverse_angstrom": .75*native,
            "frequency_error_native_bins": abs(1/best["period_angstrom"]-1/period)/native if best else None,
            "candidate_period_is_not_inserted_as_peak": True}
