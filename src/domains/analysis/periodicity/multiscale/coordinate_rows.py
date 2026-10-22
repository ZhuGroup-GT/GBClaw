""" real-space interface-row diagnostics, without target periods.

The row feature sequence is an additional observation, not a dislocation
classifier. All lengths used in extraction come from the measured nearest
neighbour distance or atom spacing; expected GB periods are never inputs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")
import numpy as np
from .io import ptm_cache_path
from ..modulation import _smooth, _maxima


def spacing_groups(spacings, resolution):
    """Identify visibly separated spacing modes, retaining their full spread."""
    values = np.asarray(spacings, float)
    if not len(values):
        return [], np.empty(0, int)
    order = np.argsort(values)
    cuts = np.flatnonzero(np.diff(values[order]) > resolution)+1
    groups = np.split(order, cuts)
    labels = np.full(len(values), -1, int)
    report = []
    for group in groups:
        if len(group) < 3:
            continue
        labels[group] = len(report)
        report.append({"label": len(report), "count": len(group),
                       "median_angstrom": float(np.median(values[group])),
                       "range_angstrom": [float(values[group].min()), float(values[group].max())],
                       "standard_deviation_angstrom": float(np.std(values[group]))})
    return report, labels


def alternating_sequence(crest_x, groups, labels):
    """Describe exceptional equal neighbours only when a two-mode alternation exists."""
    result = {"status": "no_simple_two_mode_alternation", "event_positions_x_angstrom": [],
              "event_spacings_angstrom": [], "ordinary_pair_lengths_angstrom": [],
              "meaning": "Changes in a measured crest-spacing sequence; not identified dislocations."}
    if len(groups) != 2 or len(labels) < 8 or np.mean(labels >= 0) < .95:
        return result
    usable = (labels[:-1] >= 0) & (labels[1:] >= 0)
    alternating = (labels[:-1] != labels[1:]) & usable
    fraction = float(alternating.sum()/max(1, usable.sum()))
    result["alternating_transition_fraction"] = fraction
    if fraction < .65:
        return result
    x = np.asarray(crest_x)
    same = np.flatnonzero((labels[:-1] == labels[1:]) & usable)
    event_x = x[same+1]
    pair_lengths = (x[2:]-x[:-2])[alternating]
    result.update(status="predominantly_alternating_two_spacing_modes",
                  event_positions_x_angstrom=event_x.tolist(),
                  event_spacings_angstrom=np.diff(event_x).tolist(),
                  event_repeated_spacing_labels=labels[same].tolist(),
                  ordinary_pair_lengths_angstrom=pair_lengths.tolist(),
                  ordinary_pair_median_angstrom=float(np.median(pair_lengths)))
    return result


def row_features(x, y, nn):
    """Keep native atom samples and estimate crests with local quadratic fits."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    order = np.argsort(x)
    x, y = x[order], y[order]
    gaps = np.diff(x)
    if len(x) < MINIMUM_ROW_ATOMS or np.any(gaps <= .02*nn):
        return None
    trend = np.polyval(np.polyfit(x-x.mean(), y, 1), x-x.mean())
    raw = y-trend
    # Weak smoothing removes roundoff, not a user-specified physical scale.
    smooth = _smooth(raw, .25)/_smooth(np.ones(len(raw)), .25)
    peaks = _maxima(smooth)
    prominence = []
    for i in peaks:
        left, right = i, i
        while left > 0 and smooth[left-1] <= smooth[i]:
            left -= 1
        while right < len(x)-1 and smooth[right+1] <= smooth[i]:
            right += 1
        prominence.append(float(smooth[i]-max(smooth[left:i+1].min(), smooth[i:right+1].min())))
    scale = float(np.quantile(smooth, .75)-np.quantile(smooth, .25))
    keep = np.asarray(prominence) >= .15*scale
    numerical_tolerance = max(1e-12, 1e-10*nn, 16*np.spacing(max(1., float(np.max(abs(y))))))
    if np.std(raw) <= numerical_tolerance:
        keep[:] = False
    peaks = peaks[keep]
    crest_x = []
    for i in peaks:
        a, b, _ = np.polyfit(x[i-1:i+2]-x[i], smooth[i-1:i+2], 2)
        off = -b/(2*a) if a < 0 else 0.
        off = float(np.clip(off, x[i-1]-x[i], x[i+1]-x[i]))
        crest_x.append(float(x[i]+off))
    spacing = np.diff(crest_x)
    groups, labels = spacing_groups(spacing, .3*nn)
    median_gap = float(np.median(gaps))
    unreliable = np.max(gaps) > 2*median_gap or np.quantile(gaps, .1) < .4*median_gap
    sequence = alternating_sequence(crest_x, groups, labels)
    if unreliable:
        sequence = unresolved_sequence()
    return {"x_angstrom": x.tolist(), "y_corrugation_angstrom": raw.tolist(),
            "atom_count": len(x), "median_atom_spacing_angstrom": median_gap,
            "sampling_gap_p10_p90_angstrom": np.quantile(gaps, [.1, .9]).tolist(),
            "sampling_max_gap_angstrom": float(gaps.max()),
            "sampling_status": "irregular_or_incomplete_ptm_row" if unreliable else "approximately_uniform_atom_row",
            "corrugation_standard_deviation_angstrom": float(np.std(raw)),
            "corrugation_status": "no_resolved_corrugation" if np.std(raw) <= numerical_tolerance else "resolved_corrugation",
            "crest_positions_x_angstrom": crest_x,
            "crest_spacing_angstrom": spacing.tolist(),
            "crest_prominence_angstrom": np.asarray(prominence)[keep].tolist(),
            "crest_sampling_note": "Quadratic interpolation of native atom coordinates; no sub-atom precision guarantee.",
            "spacing_groups": groups, "spacing_labels": labels.tolist(),
            "sequence": sequence}


def unresolved_sequence():
    return {"status": "unresolved_due_to_sampling_gaps", "event_positions_x_angstrom": [],
            "event_spacings_angstrom": [], "ordinary_pair_lengths_angstrom": [],
            "meaning": "Missing or sparse PTM samples can create artificial crest-sequence interruptions; no interruption conclusion is issued."}


MINIMUM_ROW_ATOMS = 30


def extract_rows(arrays, boundary, diagnostics=None):
    """Return qualifying rows; ``diagnostics`` (a dict) receives candidate-row counts."""
    diagnostics = {} if diagnostics is None else diagnostics
    diagnostics.update(candidate_rows=0, longest_candidate_row_atoms=0)
    p = np.asarray(arrays["positions"])
    nn, gb = boundary["nearest_neighbor_angstrom"], boundary["gb_y_angstrom"]
    xlo, xhi = boundary["interior_x_interval_angstrom"]
    core = ((p[:, 0] >= xlo) & (p[:, 0] <= xhi)
            & (arrays["types"] == boundary["dominant_bulk_structure_type"])
            & (abs(p[:, 1]-gb) < 12*nn))
    if "z" in boundary.get("vacuum_axes", []) or not arrays["pbc"][2]:
        raise ValueError("This row prototype requires periodic, vacuum-free z")
    edges = np.arange(gb-12*nn, gb+12*nn, .04*nn)
    centers = .5*(edges[:-1]+edges[1:])
    density = _smooth(np.histogram(p[core, 1], edges)[0], 1.2)
    peaks = _maxima(density)
    peaks = peaks[density[peaks] > .1*density.max()]
    layer_centers = centers[peaks]
    lz, zorigin = float(arrays["cell"][2, 2]), float(arrays["cell"][2, 3])
    rows = []
    for grain, sign in (("lower", -1), ("upper", 1)):
        near = sorted(layer_centers[sign*(layer_centers-gb) > 0], key=lambda v: abs(v-gb))[:4]
        for layer, center in enumerate(near):
            i = np.flatnonzero(layer_centers == center)[0]
            ylo = .5*(center+layer_centers[i-1]) if i else center-.5*nn
            yhi = .5*(center+layer_centers[i+1]) if i < len(layer_centers)-1 else center+.5*nn
            q = p[core & (p[:, 1] >= ylo) & (p[:, 1] < yhi)]
            phase = (q[:, 2]-zorigin) % lz
            ze = np.linspace(0, lz, max(16, int(lz/(.04*nn)))+1)
            zc = .5*(ze[:-1]+ze[1:])
            count = np.histogram(phase, ze)[0]
            zh = _smooth(np.tile(count, 3), 1.3)[len(count):2*len(count)]
            zp = np.flatnonzero((zh > np.roll(zh, 1)) & (zh >= np.roll(zh, -1)) & (zh > .15*zh.max()))
            if not len(zp):
                continue
            distance = abs(((phase[:, None]-zc[zp]+.5*lz) % lz)-.5*lz)
            labels = distance.argmin(axis=1)
            for channel, zpeak in enumerate(zp):
                channel_atoms = q[labels == channel]
                diagnostics["candidate_rows"] += 1
                diagnostics["longest_candidate_row_atoms"] = max(
                    diagnostics["longest_candidate_row_atoms"], len(channel_atoms))
                features = row_features(channel_atoms[:, 0], channel_atoms[:, 1], nn)
                if features is None:
                    continue
                features.update(row_id=f"{grain}_y{layer}_z{channel}", grain=grain,
                                y_layer_index=layer, y_layer_center_angstrom=float(center),
                                y_selection_interval_angstrom=[float(ylo), float(yhi)],
                                z_channel_index=channel, z_phase_center_angstrom=float(zc[zpeak]))
                rows.append(features)
    for grain in ("lower", "upper"):
        grain_rows = [row for row in rows if row["grain"] == grain]
        if not grain_rows:
            continue
        dense_spacing = float(np.quantile([row["median_atom_spacing_angstrom"] for row in grain_rows], .2))
        for row in grain_rows:
            row["dense_row_reference_spacing_angstrom"] = dense_spacing
            if row["median_atom_spacing_angstrom"] > 2*dense_spacing:
                row["sampling_status"] = "sparse_relative_to_other_layers"
                row["sequence"] = unresolved_sequence()
    return rows


def plot_rows(rows, path, stem):
    import matplotlib.pyplot as plt
    selected = []
    for grain in ("lower", "upper"):
        subset = [row for row in rows if row["grain"] == grain]
        # Prefer a clear feature sequence if present; no particular length wins.
        score = lambda row: (row["sampling_status"] == "approximately_uniform_atom_row",
                             row["sequence"]["status"] == "predominantly_alternating_two_spacing_modes",
                             -row["y_layer_index"], -row["z_channel_index"])
        if subset:
            selected.append(max(subset, key=score))
    if not selected:
        return []
    fig, axes = plt.subplots(len(selected)*2, 1, figsize=(15, 3.2*len(selected)*2), squeeze=False)
    for i, row in enumerate(selected):
        x = np.asarray(row["x_angstrom"])/10
        y = np.asarray(row["y_corrugation_angstrom"])
        ax = axes[2*i, 0]
        ax.plot(x, y, linewidth=.7, color="#1565ad")
        cx = np.asarray(row["crest_positions_x_angstrom"])/10
        ax.plot(cx, np.interp(cx, x, y), ".", ms=3, color="#d62828")
        ax.set_title(f"{row['row_id']}: native y(x) corrugation; {row['sampling_status']}", fontsize=11)
        ax.set_ylabel("Corrugation y / Å")
        bottom = axes[2*i+1, 0]
        spacing = np.asarray(row["crest_spacing_angstrom"])/10
        midpoint = .5*(cx[1:]+cx[:-1])
        labels = np.asarray(row["spacing_labels"])
        for label in sorted(set(labels)):
            sel = labels == label
            bottom.scatter(midpoint[sel], spacing[sel], s=14,
                           label=(f"mode {label}: {np.median(spacing[sel]):.3f} nm" if label >= 0 else "ungrouped"))
        seq = row["sequence"]
        for pos in seq["event_positions_x_angstrom"]:
            for panel in (ax, bottom):
                panel.axvline(pos/10, ls="--", color=".5", lw=.8)
        if seq["ordinary_pair_lengths_angstrom"]:
            text = (f"Alternating-pair sum: {seq['ordinary_pair_median_angstrom']/10:.3f} nm; "
                    "dashed: equal-neighbour interruptions")
            ds = seq["event_spacings_angstrom"]
            if ds:
                text += "\nInterruption spacings (nm): "+", ".join(f"{v/10:.2f}" for v in ds)
            bottom.set_title(text, fontsize=10)
        bottom.set_ylabel("Successive crest spacing / nm")
        bottom.legend(loc="upper right", fontsize=8)
        for panel in (ax, bottom):
            panel.set_xlabel("x / nm")
            panel.grid(alpha=.15)
    fig.suptitle(f"{stem}: real-space row features, with z channels retained separately", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, .975))
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return [row["row_id"] for row in selected]


def analyze(coordinate_dir, output_dir):
    source = Path(coordinate_dir)
    root = Path(output_dir)
    cache = ptm_cache_path(source)
    stem = root.name
    with np.load(cache/"ptm_atoms.npz") as packed:
        arrays = dict(packed)
    boundary = json.loads((source/"boundary.json").read_text())
    ptm_metadata = json.loads((cache/"ptm_metadata.json").read_text())
    stem = Path(ptm_metadata.get("source", stem)).stem
    diagnostics = {}
    rows = extract_rows(arrays, boundary, diagnostics)
    out = root/"coordinate_rows"
    out.mkdir(exist_ok=True)
    selected = plot_rows(rows, out/"coordinate_rows.png", stem)
    status = {"status": "rows_reported"} if rows else {
        "status": "no_qualifying_rows",
        "reason": (f"No interior atom row has at least {MINIMUM_ROW_ATOMS} atoms (longest of "
                   f"{diagnostics['candidate_rows']} candidate rows: {diagnostics['longest_candidate_row_atoms']} atoms); "
                   "the GB may be too short along x to resolve row motifs, so no row figure was drawn."),
    }
    report = {**status, "provider": "native_interface_row_corrugation", "source": ptm_metadata.get("source", stem),
              "ptm_source_provenance": ptm_metadata,
              "method": "Detect y lattice-density peaks in surface-excluded PTM bulk atoms; select four layers per grain; cluster periodic z rows; preserve native y(x), detect prominent crests, and describe their spacing sequence.",
              "parameters": {"y_histogram_spacing_nn": .04, "y_density_smoothing_bins": 1.2,
                             "maximum_layers_per_grain": 4, "minimum_row_atoms": MINIMUM_ROW_ATOMS,
                             "native_y_smoothing_atoms": .25, "crest_prominence_fraction_iqr": .15,
                             "spacing_mode_gap_resolution_nn": .3,
                             "two_mode_minimum_alternating_transition_fraction": .65},
              "limitations": ["PTM removes noncrystalline atoms, so rows may be incomplete; these are explicitly flagged.",
                              "Fixed y intervals may mix strongly bent layers; inspect the row samples before assigning meaning.",
                              "A crest-spacing mode, an alternating-pair sum, an interruption spacing and an FFT wavelength are different observations.",
                              "No event is automatically identified as a dislocation, and no feature is assigned a Burgers vector.",
                              "Periodic z copies are not statistically independent corroboration.",
                              "The two-mode alternation diagnostic is conditional; complex feature sequences remain unclassified."],
              "plotted_row_ids": selected, "row_diagnostics": diagnostics, "rows": rows}
    (out/"coordinate_rows.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    return report
