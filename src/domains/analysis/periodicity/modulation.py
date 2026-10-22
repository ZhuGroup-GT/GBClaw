"""Long-scale GB hypotheses from PTM fields, without orientation priors.

The caller supplies the already surface-excluded, near-interface mask. Nothing
here identifies a dislocation or a Burgers vector. This is one evidence provider
whose scalar profiles complement, not replace, xyz registration.
"""
from __future__ import annotations

import numpy as np


def _maxima(y):
    return np.flatnonzero((y[1:-1] > y[:-2]) & (y[1:-1] >= y[2:])) + 1


def _smooth(y, sigma_bins):
    radius = max(1, int(np.ceil(4*sigma_bins)))
    offset = np.arange(-radius, radius+1)
    kernel = np.exp(-.5*(offset/sigma_bins)**2)
    kernel /= kernel.sum()
    # np.convolve(mode='same') would change length if the kernel exceeds y.
    padded = np.pad(y, (radius, radius), mode='constant')
    return np.convolve(padded, kernel, mode='valid')


def _detrend(y, x):
    return y-np.polyval(np.polyfit(x-x.mean(), y, 1), x-x.mean())


def _correlation(y, lag):
    if lag < 1 or lag >= len(y)-3:
        return None
    a, b = y[:-lag], y[lag:]
    a, b = a-a.mean(), b-b.mean()
    denominator = np.linalg.norm(a)*np.linalg.norm(b)
    return float(np.dot(a, b)/denominator) if denominator > 1e-20 else None


def _local_features(x, envelope, minimum_period, sigma):
    y = _detrend(envelope, x)
    valid = (x >= x[0]+2.5*sigma) & (x <= x[-1]-2.5*sigma)
    scale = float(np.std(y[valid])) if valid.any() else 0.
    if scale < 1e-14:
        return {"positions_x_angstrom": [], "spacings_angstrom": [],
                "spacing_coefficient_of_variation": None, "status": "no_resolved_features"}
    radius = max(2, int(round(.5*minimum_period/(x[1]-x[0]))))
    peaks = []
    for i in _maxima(y):
        if not valid[i] or y[i] < .2*scale:
            continue
        # Require a resolved crest on both sides, not a monotonic shoulder.
        prominence = y[i]-max(y[max(0, i-radius):i+1].min(),
                              y[i:min(len(y), i+radius+1)].min())
        if prominence >= .25*scale:
            peaks.append(int(i))
    chosen = []
    for i in sorted(peaks, key=lambda k: -y[k]):
        if all(abs(x[i]-x[j]) >= .5*minimum_period for j in chosen):
            chosen.append(i)
    chosen.sort()
    positions = x[chosen]
    spacing = np.diff(positions)
    cv = float(np.std(spacing)/np.mean(spacing)) if len(spacing) >= 2 else None
    return {"positions_x_angstrom": positions.tolist(), "spacings_angstrom": spacing.tolist(),
            "spacing_coefficient_of_variation": cv,
            "status": ("spatially_variable_spacing" if cv is not None and cv > .1 else
                       "approximately_recurrent_features" if len(spacing) >= 2 else
                       "insufficient_feature_intervals"),
            "meaning": "Maxima of a smoothed scalar field, not identified dislocation cores."}


def _signal_evidence(x, raw, envelope, minimum_period, maximum_period):
    dx = float(x[1]-x[0]); extent = len(x)*dx
    detrended = _detrend(raw, x)
    low = _detrend(envelope, x)
    sigma = .18*minimum_period
    nfft = 2**int(np.ceil(np.log2(8*len(x))))
    frequency = np.fft.rfftfreq(nfft, dx)
    power = abs(np.fft.rfft(detrended*np.hanning(len(x)), n=nfft))**2
    long = (frequency >= 1/maximum_period) & (frequency <= 1/minimum_period)
    total_power = float(power[frequency >= 1/extent].sum())
    band_fraction = float(power[long].sum()/total_power) if total_power > 1e-25 else 0.
    raw_scale = float(np.std(detrended))
    low_scale = float(np.std(low))
    amplitude_ratio = low_scale/max(raw_scale, 1e-15)
    active = raw_scale > max(1e-12, 1e-8*abs(float(np.mean(raw))))
    peaks = [int(i) for i in _maxima(power) if long[i]]
    # A search-boundary maximum is censored, not an interior measured peak.
    candidates = []
    peak_power = float(power[long].max()) if long.any() else 0.
    for i in sorted(peaks, key=lambda k: -power[k])[:6]:
        relative = float(power[i]/peak_power) if peak_power > 0 else 0.
        if not active or relative < .12:
            continue
        period = float(1/frequency[i])
        correlation = _correlation(low, int(round(period/dx)))
        spectral_resolved = band_fraction >= .01 and amplitude_ratio >= .04
        feature_sigma = max(sigma, .12*period)
        feature_envelope = _smooth(raw, feature_sigma/dx)/np.maximum(
            _smooth(np.ones(len(raw)), feature_sigma/dx), 1e-30)
        candidates.append({
            "period_angstrom": period, "observed_cycles": extent/period,
            "relative_power_within_long_band": relative,
            "long_band_power_fraction": band_fraction,
            "smoothed_to_raw_std_ratio": amplitude_ratio,
            "envelope_correlation_at_period": correlation,
            "signal_evidence": ("resolved_spectral_modulation" if spectral_resolved else
                                "weak_spectral_peak"),
            "local_features": _local_features(x, feature_envelope, period, feature_sigma),
            "local_feature_smoothing_sigma_angstrom": feature_sigma,
            "local_feature_selection_note": "Smoothing and minimum crest separation use this proposed spectral scale; this is localization, not independent period confirmation.",
            "frequency_resolution_interval_angstrom": [1/(frequency[i]+.5/extent),
                                                         1/(frequency[i]-.5/extent)],
        })
    return {"active": bool(active), "raw_standard_deviation": raw_scale,
            "long_band_power_fraction": band_fraction,
            "smoothed_to_raw_std_ratio": amplitude_ratio,
            "peaks": candidates,
            "local_features": _local_features(x, envelope, minimum_period, sigma)}, {
        "raw": raw.tolist(), "envelope": envelope.tolist(),
        "frequency_inverse_angstrom": frequency[long].tolist(),
        "relative_power": (power[long]/max(peak_power, 1e-30)).tolist(),
    }


def analyze_modulation(arrays, masks, boundary, short_scale_angstrom=None, min_cycles=3.0,
                       *, minimum_scale_ratio=3.0):
    """Return JSON-serializable hypotheses and scalar curves; no target periods.

    ``short_scale_angstrom`` is an optional DATA-DERIVED short candidate from the
    caller, never the expected answer. If ambiguous, call for several candidates.
    Search limits are >=3 times that scale (or 12 NN distances if unavailable)
    and <=finite x support/min_cycles. These are evidence gates, not p-values.
    """
    if not np.isfinite(min_cycles) or min_cycles < 2:
        raise ValueError("min_cycles must be finite and at least 2")
    if not np.isfinite(minimum_scale_ratio) or minimum_scale_ratio <= 1:
        raise ValueError("minimum_scale_ratio must be finite and greater than 1")
    if short_scale_angstrom is not None and (not np.isfinite(short_scale_angstrom) or short_scale_angstrom <= 0):
        raise ValueError("short_scale_angstrom must be positive and finite")
    nn = float(boundary["nearest_neighbor_angstrom"])
    lo, hi = map(float, boundary["interior_x_interval_angstrom"])
    if not np.isfinite(nn) or nn <= 0 or not np.isfinite(hi-lo) or hi <= lo:
        raise ValueError("Need positive NN distance and finite ordered x support")
    extent = hi-lo
    minimum_period = max(minimum_scale_ratio*short_scale_angstrom, 8*nn) if short_scale_angstrom is not None else 12*nn
    maximum_period = extent/min_cycles
    report = {
        "provider": "ptm_scalar_long_modulation",
        "status": "insufficient_domain", "hypotheses": [], "signals": {}, "profiles": {},
        "short_scale_input_angstrom": short_scale_angstrom,
        "short_scale_input_role": "Optional measured candidate; no orientation or repeat-count prior.",
        "window_length_angstrom": extent,
        "search_period_interval_angstrom": [minimum_period, maximum_period],
        "native_frequency_resolution_inverse_angstrom": 1/extent,
        "thresholds": {"minimum_observed_cycles": min_cycles, "minimum_relative_peak_power": .12,
                       "minimum_scale_ratio": minimum_scale_ratio,
                       "minimum_long_band_power_fraction": .01, "minimum_envelope_to_raw_std_ratio": .04,
                       "minimum_envelope_correlation": .35, "variable_spacing_cv": .1},
        "interpretation_limits": [
            "No long candidate means insufficient evidence in this finite snapshot, not proof that no longer structure exists.",
            "PTM fractions and PTM RMSD share the same atoms and are correlated evidence, not independent statistical confirmations.",
            "Spectral-resolution intervals are not confidence intervals; zero padding only interpolates peaks.",
            "Scalar profiles discard transverse phase; validate candidates using layer-resolved geometry and full xyz registration.",
            "These are structural-modulation hypotheses, not identified secondary dislocations or Burgers vectors.",
            "The supplied near-interface mask can omit extended cores; compare wider slabs before physical interpretation.",
            "Input coordinates must already have x along the analyzed interface direction; no crystal Miller-index prior is used.",
            "Different PTM fields can localize different crests; field-specific spacing variation alone does not establish varying defect separation.",
        ],
    }
    if maximum_period <= 1.05*minimum_period:
        report["reason"] = "The finite window does not contain enough cycles above the minimum scale separation."
        return report
    positions = np.asarray(arrays["positions"])
    mask = np.asarray(masks["near"], dtype=bool).copy()
    mask &= (positions[:, 0] >= lo) & (positions[:, 0] <= hi)
    if mask.sum() < 30:
        report["reason"] = "Fewer than 30 near-interface atoms remain."
        return report
    bins = np.linspace(lo, hi, max(32, int(np.ceil(extent/(nn*.3))))+1)
    x = .5*(bins[1:]+bins[:-1]); dx = float(bins[1]-bins[0])
    bulk = int(boundary["dominant_bulk_structure_type"])
    types = np.asarray(arrays["types"])
    rmsd = np.asarray(arrays["rmsd"])
    weights = {"nonbulk_fraction": (types != bulk).astype(float)}
    if bulk != 2 and np.any(types[mask] == 2):
        weights["hcp_fraction"] = (types == 2).astype(float)
    valid_rmsd = np.isfinite(rmsd) & (rmsd > 0)
    if np.any(mask & valid_rmsd):
        scale = float(np.median(rmsd[mask & valid_rmsd]**2))
        weights["normalized_mean_squared_ptm_rmsd"] = np.where(valid_rmsd, rmsd**2/max(scale, 1e-30), 0.)
        report["rmsd_squared_normalization"] = scale
    atomic_sigma = .45*nn
    long_sigma = .18*minimum_period
    report["profile_settings"] = {"bin_width_angstrom": dx, "atomic_smoothing_sigma_angstrom": atomic_sigma,
                                  "envelope_sigma_angstrom": long_sigma,
                                  "rmsd_zero_handling": "Zero/nonfinite RMSD excluded from RMSD numerator and denominator.",
                                  "fraction_definition": "Smoothed weighted count divided by the identically smoothed atom count."}
    report["profiles"]["x_angstrom"] = x.tolist()
    all_peaks = []
    for name, weight in weights.items():
        eligible = mask if name != "normalized_mean_squared_ptm_rmsd" else mask & valid_rmsd
        denominator = np.histogram(positions[eligible, 0], bins)[0].astype(float)
        numerator = np.histogram(positions[eligible, 0], bins, weights=weight[eligible])[0]
        raw = _smooth(numerator, atomic_sigma/dx)/np.maximum(_smooth(denominator, atomic_sigma/dx), 1e-30)
        envelope = _smooth(numerator, long_sigma/dx)/np.maximum(_smooth(denominator, long_sigma/dx), 1e-30)
        evidence, curves = _signal_evidence(x, raw, envelope, minimum_period, maximum_period)
        report["signals"][name] = evidence; report["profiles"][name] = curves
        for peak in evidence["peaks"]:
            all_peaks.append({"signal": name, **peak})
    # Cluster compatible frequencies at half a native bin; retain every source.
    groups = []
    for peak in sorted(all_peaks, key=lambda p: -p["long_band_power_fraction"]*p["relative_power_within_long_band"]):
        frequency = 1/peak["period_angstrom"]
        group = next((g for g in groups if abs(frequency-1/g[0]["period_angstrom"]) <= .5/extent), None)
        if group is None:
            groups.append([peak])
        else:
            group.append(peak)
    for group in groups:
        best = group[0]
        resolved = [p for p in group if p["signal_evidence"] == "resolved_spectral_modulation"]
        recurring = [p for p in resolved if p["envelope_correlation_at_period"] is not None and p["envelope_correlation_at_period"] >= .35]
        local = {p["signal"]: p["local_features"] for p in group}
        reasons = []
        if resolved:
            reasons.append("Long-band power and envelope amplitude exceed the stated numerical evidence gates.")
        if recurring:
            reasons.append("The smoothed profile also recurs at this lag (correlation >= 0.35).")
        if any(v["status"] == "spatially_variable_spacing" for v in local.values()):
            reasons.append("Local feature intervals vary; preserve the interval list instead of imposing an exact period.")
        status = "candidate" if recurring else "weak_candidate"
        if not resolved:
            reasons.append("Small long-band power or envelope amplitude: leakage, noise, or weak modulation remain possible.")
        report["hypotheses"].append({"id": f"modulation_{len(report['hypotheses'])+1}",
            "period_angstrom": best["period_angstrom"], "status": status,
            "observed_cycles": extent/best["period_angstrom"],
            "frequency_resolution_interval_angstrom": best["frequency_resolution_interval_angstrom"],
            "evidence": group, "local_features_by_signal": local, "reasons": reasons,
            "harmonic_relations": []})
    # Harmonic links are alternatives to review, never extra confirmations.
    for i, a in enumerate(report["hypotheses"]):
        for b in report["hypotheses"][i+1:]:
            small, large = sorted((a, b), key=lambda h: h["period_angstrom"])
            ratio = large["period_angstrom"]/small["period_angstrom"]
            order = int(round(ratio))
            if order >= 2 and abs(1/small["period_angstrom"]-order/large["period_angstrom"]) <= .5/extent:
                large["harmonic_relations"].append({"candidate_id": small["id"], "order": order,
                    "meaning": "Possible spectral harmonic, not a separately confirmed structural scale."})
    report["status"] = "candidates_found" if any(h["status"] == "candidate" for h in report["hypotheses"]) else "no_resolved_long_modulation"
    report["reason"] = "Retain multiple hypotheses and local intervals for geometry validation and user interpretation."
    return report
