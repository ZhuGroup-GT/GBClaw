"""Render the complete validated summary without discarding alternative scales."""
from __future__ import annotations

import json
from pathlib import Path
from textwrap import fill

import numpy as np
from .io import ptm_cache_path


GRAIN_COLORS = {"lower": "#2868ac", "upper": "#d3651c"}
BAND_STYLES = {"near": "-", "middle": "--", "outer": ":"}
STRUCTURE_COLORS = {"near": "#447c42", "defects": "#9c498b"}
GOLD = "#b27700"
SHORT_COLOR = "#3858b0"
LONG_COLOR = "#147e80"


def _period_notes(annotations):
    return [(f"S{i}", note, SHORT_COLOR) for i, note in
            enumerate(annotations.get("short_period_annotations", []), 1)] + [
        (f"L{i}", note, LONG_COLOR if note["kind"] != "longer_field_interval" else "#ac437b")
        for i, note in enumerate(annotations.get("long_period_annotations", []), 1)]


def _period_guides(ax, annotations):
    """Overlay non-spectral evidence without pretending that it is an FFT peak."""
    lo, hi = ax.get_xlim()
    notes = [(label, note, color) for label, note, color in _period_notes(annotations)
             if lo <= note["period_nm"] <= hi]
    for i, (label, note, color) in enumerate(notes):
        period = note["period_nm"]
        ax.axvline(period, color=color, ls="--", lw=1.25, alpha=.85, zorder=3)
        origin = " row" if note.get("association_kind") == "nearby_candidate" else ""
        ax.text(period, .97 - .23 * (i % 2), f"{label} {period:.3f} nm{origin}",
                color=color, rotation=90, va="top", ha="right", fontsize=9,
                weight="bold", transform=ax.get_xaxis_transform(), zorder=6,
                bbox={"facecolor": "white", "edgecolor": "none", "pad": 1, "alpha": .85})
    return [note["period_nm"] * 10 for _, note, _ in notes]


def _short_motif_description(label, note):
    """Keep a nearby lattice candidate distinct from the measured row motif."""
    lattice = note.get("display_lattice_label", "lattice correspondence unresolved")
    row_period = note.get("row_period_nm", note["period_nm"])
    coordinate_period = note.get("coordinate_period_nm")
    if note.get("association_kind") == "nearby_candidate" and coordinate_period is not None:
        main = f"{label}  row {row_period:.3f} nm → nearby {coordinate_period:.3f} nm | {lattice}"
        difference = 100 * note.get("association_relative_difference", abs(coordinate_period / row_period - 1))
        detail = f"Row spacing and coordinate candidate differ by {difference:.2f}%; association is not equality or FFT confirmation."
    elif note.get("candidate_id"):
        main = f"{label}  {note['period_nm']:.3f} nm | {lattice}"
        detail = f"Row estimate {row_period:.3f} nm; coordinate match within the spatial tolerance."
    else:
        main = f"{label}  row {row_period:.3f} nm | {lattice}"
        detail = "No suitable nearby coordinate candidate; the measured row spacing is retained."
    return main, detail


def _hierarchy_overview(ax, annotations):
    """Show both observed scales and full lattice notation in one readable place."""
    ax.axis("off")
    ax.text(.005, .98, "Structural motif and longer candidate scales (upper : lower)",
            fontsize=12, weight="bold", va="top", transform=ax.transAxes)
    groups = [("Short structural motif", annotations.get("short_period_annotations", []), "S", SHORT_COLOR),
              ("Longer-scale candidates", annotations.get("long_period_annotations", []), "L", LONG_COLOR)]
    kinds = {"longer_local_repeat": "local interval + coordinate match", "longer_coordinate_recurrence": "coordinate recurrence",
             "longer_field_interval": "local interval; no pure-x match"}
    nrows = max(3, *(len(notes) for _, notes, _, _ in groups))
    step = .58 / nrows
    for col, (title, notes, prefix, color) in enumerate(groups):
        start = .01 + .5 * col
        ax.text(start, .74, title, color=color, fontsize=11, weight="bold", transform=ax.transAxes)
        if not notes:
            ax.text(start, .51, "Unresolved in the available window", fontsize=10, transform=ax.transAxes)
        for i, note in enumerate(notes, 1):
            lattice = note.get("display_lattice_label", "lattice correspondence unresolved")
            description = f"{prefix}{i}  {note['period_nm']:.3f} nm  |  {lattice}"
            kind = kinds.get(note.get("kind"), "structural motif / coordinate candidate")
            if prefix == "S":
                description, kind = _short_motif_description(f"S{i}", note)
            ax.text(start, .72 - step * i, description, color=color, fontsize=10,
                    weight="bold", transform=ax.transAxes)
            ax.text(start, .72 - step * i - .075, kind, fontsize=8.4, color=".35", transform=ax.transAxes)
    ax.text(.01, -.04, "Colored dashed FFT guides show these candidate lengths, not assigned spectral peaks. Nominal pairs use symmetry-equivalent directions; measured counts remain in JSON/CSV.",
            fontsize=8.6, transform=ax.transAxes)


def candidate_scale_tracks(rows, elastic, events, row_features):
    """Every saved candidate/peak is represented; coincident marks may overlap."""
    structure, bulk_like = [], []
    for data in elastic["structure_fft"]["channels"].values():
        for spectrum in (data, data.get("long_band", {})):
            structure.extend(p["period_angstrom"] for p in spectrum.get("review_peaks", spectrum.get("peaks", [])))
            bulk_like.extend(p["period_angstrom"] for p in spectrum.get("local_peaks", [])
                             if p.get("classification") == "bulk_like")
    strain = [p["period_angstrom"] for key in ("strain_proposals", "long_band_strain_proposals")
              for p in elastic.get(key, [])]
    other_strain = []
    for profile in elastic["profiles"].values():
        if profile.get("distance_band") == "far_field":
            continue
        for key in ("spectrum", "long_spectrum"):
            spectrum = profile.get(key, {})
            strain.extend(p["period_angstrom"] for p in spectrum.get("review_peaks", spectrum.get("peaks", [])))
            other_strain.extend(p["period_angstrom"] for p in spectrum.get("local_peaks", [])
                                if p.get("review_eligible") is False)
    local_strain = [m["center_angstrom"] for m in events.get("spacing_candidates", [])]
    channels = events.get("channels", [])
    if isinstance(channels, dict):
        channels = channels.values()
    for channel in channels:
        local_strain.extend(m["center_angstrom"] for m in channel.get("spacing_modes", []))
    atomic_rows = []
    for row in row_features.get("rows", []):
        if row.get("sampling_status") != "approximately_uniform_atom_row":
            continue
        atomic_rows.extend(p["median_angstrom"] for p in row.get("spacing_groups", []))
        seq = row.get("sequence", {})
        if seq.get("status") == "predominantly_alternating_two_spacing_modes":
            atomic_rows.append(seq["ordinary_pair_median_angstrom"])
            atomic_rows.extend(seq["event_spacings_angstrom"])
    return [
        ("Atomic translations", [r["translation_angstrom"] for r in rows], "#2868ac"),
        ("Structure FFT review peaks", structure, "#447c42"),
        ("Bulk-like structure FFT peaks", bulk_like, "#909090"),
        ("Strain FFT review peaks", strain, "#d3651c"),
        ("Other strain FFT maxima (*)", other_strain, "#ab9889"),
        ("Spatial strain modes", local_strain, "#ad487c"),
        ("Valid atomic-row scales", atomic_rows, "#6d57a0"),
    ]


def _spectrum(ax, data, *, power_key="relative_power", **style):
    frequency = np.asarray(data.get("frequency_inverse_angstrom", []), dtype=float)
    power = np.asarray(data.get(power_key, []), dtype=float)
    if len(frequency) and len(power) == len(frequency):
        valid = np.isfinite(frequency) & (frequency > 0) & np.isfinite(power)
        if power_key != "relative_power":
            valid &= power > 0
        # NaNs preserve gaps instead of drawing a line across invalid samples.
        wavelengths = np.divide(1., frequency, out=np.full_like(frequency, np.nan), where=frequency > 0) / 10
        ax.plot(wavelengths, np.where(valid, power, np.nan), **style)


def _structure_label_periods(channels, annotations, *, long_band=False):
    """Prioritize genuine maxima near motif guides without inventing FFT peaks.

    A matching maximum only receives display priority, not a new eligibility or
    confidence score. Frequency tolerance is half the native (unpadded) bin.
    All other review peaks are retained in the scale strip and numerical report.
    """
    supported, alternatives = [], []
    for data in channels.values():
        spectrum = data.get("long_band", {}) if long_band else data
        peaks = spectrum.get("local_peaks", spectrum.get("peaks", []))
        resolution = spectrum.get("native_frequency_resolution_inverse_angstrom",
                                  data.get("native_frequency_resolution_inverse_angstrom", 0))
        if resolution and peaks:
            for _, note, _ in _period_notes(annotations):
                target = 1 / (note["period_nm"] * 10)
                close = [p for p in peaks if p.get("relative_power", 0) > 0
                         and abs(1 / p["period_angstrom"] - target) <= .5 * resolution]
                if close:
                    supported.append(min(close, key=lambda p: abs(1 / p["period_angstrom"] - target)))
        alternatives.extend(spectrum.get("review_peaks", spectrum.get("peaks", [])))
    alternatives.sort(key=lambda p: (p.get("classification") == "bulk_like",
                                      -p.get("local_prominence_ratio", 0), -p.get("relative_power", 0)))
    return [p["period_angstrom"] for p in supported + alternatives]


def _strain_review_channels(elastic):
    """Review interface signals without treating reference-only peaks as GB peaks."""
    return {
        name: {**profile.get("spectrum", {}), "long_band": profile.get("long_spectrum", {})}
        for name, profile in elastic.get("profiles", {}).items()
        if profile.get("z_channel") == "all" and profile.get("distance_band") != "far_field"
    }


def _strain_reference_profiles(elastic):
    """Resolve the shared far-field profiles, without copying or rescaling powers."""
    references = elastic.get("strain_fft_review", {}).get("background_references", {})
    return {grain: elastic["profiles"][name] for grain, name in references.items()
            if isinstance(name, str) and name in elastic.get("profiles", {})}


def _log_power_axis(ax, *, ylabel="Power fraction / bin (log scale)"):
    """Expose weak peaks while retaining unmodified saved powers."""
    lo, hi = ax.get_xlim()
    values = []
    for line in ax.lines:
        x, y = np.asarray(line.get_xdata()), np.asarray(line.get_ydata())
        values.extend(y[(x >= lo) & (x <= hi) & np.isfinite(y) & (y > 0)])
    if not values:
        ax.set_yscale("linear")
        ax.set_ylim(0, 1)
        ax.set_yticks([])
        ax.set_ylabel("Spectral power unavailable")
        ax.text(.5, .5, "No positive spectral power in this window", ha="center",
                transform=ax.transAxes, fontsize=10)
        return
    ax.set_yscale("log")
    top = max(values)
    ax.set_ylim(max(min(values) * .5, top * 1e-7), top * 2)
    ax.set_ylabel(ylabel)


def _unconfirmed_structure_periods(channels, *, long_band=False):
    """An actual maximum need not pass the independent review checks."""
    status = {}
    for data in channels.values():
        spectrum = data.get("long_band", {}) if long_band else data
        for peak in spectrum.get("local_peaks", []):
            if "review_eligible" in peak:
                period = peak["period_angstrom"]
                status[period] = status.get(period, False) or peak["review_eligible"]
    return {period for period, eligible in status.items() if not eligible}


def _peak_labels(ax, periods, *, count=5, candidate_periods=(), unconfirmed_periods=()):
    """Only text is thinned; complete peaks also appear in the candidate strip."""
    selected = []
    lo, hi = ax.get_xlim()
    # Label separation must follow the displayed axis, not linear wavelength:
    # otherwise short-wavelength labels are unnecessarily removed on a log axis.
    transform = np.log10 if ax.get_xscale() == "log" else lambda value: value
    extent = transform(hi) - transform(lo)
    for period in periods:
        if not lo <= period / 10 <= hi:
            continue
        if all(abs(transform(period / 10) - transform(old / 10)) > .055 * extent for old in selected):
            selected.append(period)
        if len(selected) == count:
            break
    for i, period in enumerate(selected):
        ax.axvline(period / 10, ls=":", color=".45", lw=.65)
        near_left = transform(period / 10) < transform(lo) + .035 * extent
        near_candidate = any(abs(transform(period / 10) - transform(old / 10)) < .035 * extent
                             for old in candidate_periods)
        y = .75 - .12 * (i % 2) if near_candidate else .96 - .19 * (i % 3)
        flag = "*" if period in unconfirmed_periods else ""
        ax.text(period / 10, y, f"FFT {period / 10:.2f} nm{flag}",
                rotation=90, ha="left" if near_left or near_candidate else "right", va="top", fontsize=8,
                color=".3", transform=ax.get_xaxis_transform(),
                bbox={"facecolor": "white", "edgecolor": "none", "pad": .3, "alpha": .8})


def _spatial_features(ax, events):
    channels = events.get("channels", [])
    if isinstance(channels, dict):
        channels = [{"id": key, **value} for key, value in channels.items()]
    by_id = {c["id"]: c for c in channels}
    chosen = [by_id[item] if isinstance(item, str) else item
              for item in events.get("summary_display_channels", [])]
    if not chosen:
        chosen = [c for c in channels if len(c.get("successive_spacing_angstrom", [])) >= 3][:6]
    nn_nm = events["nearest_neighbor_angstrom"] / 10
    for c in chosen:
        positions = np.asarray(c.get("extrema_x_angstrom", []))
        spacing = np.asarray(c.get("successive_spacing_angstrom", []))
        if len(spacing) and len(positions) == len(spacing) + 1:
            polarity = "maxima" if c["polarity"] == "positive" else "minima"
            label = (f"{c['grain']}: depth {c['depth_nn'] * nn_nm:.2f} nm, "
                     f"smooth {c['sigma_nn'] * nn_nm:.2f} nm, {polarity}")
            ax.plot((positions[:-1] + positions[1:]) / 20, spacing / 10,
                    ".-", lw=.7, ms=4, label=label)
    ax.set(xlabel="Interval midpoint along GB / nm", ylabel="Consecutive spacing / nm",
           title="Spatial strain features · local intervals can exist without a matching FFT peak")
    if ax.lines:
        ax.legend(fontsize=8, ncol=2, loc="upper center", bbox_to_anchor=(.5, -.23), framealpha=.95)
    ax.grid(alpha=.2)


def _coordinate_candidates(ax, rows, annotations):
    x = np.asarray([r["translation_nm"] for r in rows])
    near = np.asarray([r["near_rms_over_nn"] for r in rows])
    core = np.asarray([r["defect_rms_over_nn"] for r in rows])
    ax.plot(x, near, "o-", ms=4, lw=.65, label="near-GB atoms")
    ax.plot(x, core, "s-", ms=3.5, lw=.65, label="non-bulk core atoms")
    by_id = {r["candidate_id"]: r for r in annotations["records"]}
    excluded = np.asarray([not by_id[r["candidate_id"]]["lattice_repeat_check"]["eligible"] for r in rows])
    if excluded.any():
        ax.scatter(x[excluded], near[excluded], marker="x", color=".55", s=38, zorder=4,
                   label="Pure-x lattice closure not validated")
    highlighted = annotations.get("highlighted_id")
    top = max(float(np.nanmax(near)), float(np.nanmax(core)), .01)
    xmin, xmax = 0, max(x) * 1.05
    # Vertical labels consume little horizontal space. Spread only their text
    # anchors within dense groups; leader lines preserve exact x coordinates.
    anchors = x.copy()
    gap = (xmax - xmin) / 95
    for i in range(1, len(anchors)):
        anchors[i] = max(anchors[i], anchors[i - 1] + gap)
    for i in range(len(anchors) - 2, -1, -1):
        anchors[i] = min(anchors[i], anchors[i + 1] - gap)
    for i, row in enumerate(rows):
        record = by_id[row["candidate_id"]]
        special = row["candidate_id"] == highlighted
        ax.annotate(record.get("display_lattice_label", record["ratio_label"]), (x[i], near[i]),
                    xytext=(anchors[i], max(near[i], core[i]) + .055 * top),
                    textcoords="data", fontsize=7.5, rotation=90,
                    ha="center", va="bottom", color=GOLD if special else ".55" if excluded[i] else ".2",
                    weight="bold" if special else "normal",
                    arrowprops={"arrowstyle": "-", "lw": .45, "color": ".7"},
                    bbox={"facecolor": "white", "edgecolor": "none", "pad": .2, "alpha": .85})
        if special:
            ax.axvspan(x[i] - .006 * xmax, x[i] + .006 * xmax, color="#f4d477", alpha=.35)
            ax.scatter([x[i]], [near[i]], marker="*", s=160, color=GOLD, zorder=5,
                       edgecolors="white", linewidths=.7,
                       label=f"Review priority {row['candidate_id']}: {x[i]:.3f} nm")
    directions = sorted({r.get("display_direction_ratio_label", r["direction_ratio_label"])
                         for r in annotations["records"]})
    direction = directions[0] if len(directions) == 1 else "mixed/unresolved direction bases: see catalog"
    basis = "cubic-symmetry equivalents" if "PTM_cubic_symmetry_branch" in annotations["direction_bases"] else "validated source basis"
    long_notes = annotations.get("long_period_annotations", [])
    _long_period_marks(ax, rows, long_notes)
    indexed_rows = {row["candidate_id"]: row for row in rows}
    for i, note in enumerate(annotations.get("short_period_annotations", []), 1):
        row = indexed_rows.get(note.get("candidate_id"))
        if row is None:
            ax.axvline(note["period_nm"], color=SHORT_COLOR, ls="--", lw=1,
                       label="S: row motif (no coordinate match)" if i == 1 else None)
            ax.text(.015, .97 - .055 * (i - 1), f"S{i} · {note['period_nm']:.3f} nm (no suitable nearby coordinate candidate)",
                    color=SHORT_COLOR, fontsize=8, ha="left", va="top", transform=ax.transAxes)
            continue
        px, py = row["translation_nm"], row["near_rms_over_nn"]
        nearby = note.get("association_kind") == "nearby_candidate"
        if nearby:
            observed = note.get("row_period_nm", note["period_nm"])
            ax.axvline(observed, color=SHORT_COLOR, ls="--", lw=.8, alpha=.7)
            ax.text(.015, .97 - .055 * (i - 1),
                    f"S{i}: row {observed:.3f} nm → nearby coordinate {px:.3f} nm; values remain distinct",
                    color=SHORT_COLOR, fontsize=8, ha="left", va="top", transform=ax.transAxes)
        ax.axvspan(px - .005 * xmax, px + .005 * xmax, color=SHORT_COLOR, alpha=.10)
        ax.scatter([px], [py], marker="s", s=70, facecolors="white", edgecolors=SHORT_COLOR,
                   linewidths=1.8, zorder=6, label=("S: nearby lattice candidate" if nearby else "S: short structural motif") if i == 1 else None)
        ax.annotate(f"S{i} nearby" if nearby else f"S{i}", (px, py), xytext=(6, 4),
                    textcoords="offset points", ha="left", va="bottom", color=SHORT_COLOR, fontsize=8.5, weight="bold",
                    bbox={"facecolor": "white", "edgecolor": "none", "pad": .4, "alpha": .9})
    ax.set(xlim=(xmin, max(xmax, anchors[-1] + gap)), ylim=(0, top * 1.95),
           xlabel="Atomic translation along x / nm", ylabel="XYZ mismatch RMS / NN",
           title=f"All {len(rows)} coordinate hypotheses · {int((~excluded).sum())} pass lattice closure · {direction} · {basis}\nPoint labels: nominal lattice pair or ≈ measured counts; S/L observations need not be valid PBC repeats")
    ax.legend(fontsize=8, loc="upper right", framealpha=.95)
    ax.grid(alpha=.2)


def _long_period_marks(ax, rows, notes):
    """Separate long local/field scales from the single gold review priority."""
    by_id = {row["candidate_id"]: row for row in rows}
    styles = {
        "longer_local_repeat": ("D", "#147e80", "#147e80", "Long: local interval + coordinate match"),
        "longer_coordinate_recurrence": ("D", "none", "#147e80", "Long: coordinate recurrence"),
        "longer_field_interval": ("v", "none", "#ac437b", "Long: local interval only"),
    }
    seen = set()
    for i, note in enumerate(notes, 1):
        row = by_id.get(note["candidate_id"])
        if row is None:
            continue
        marker, face, edge, label = styles[note["kind"]]
        x, y = row["translation_nm"], row["near_rms_over_nn"]
        ax.scatter([x], [y], marker=marker, s=60, facecolors=face, edgecolors=edge,
                   linewidths=1.2, zorder=4, label=label if note["kind"] not in seen else None)
        ax.annotate(f"L{i}", (x, y), xytext=(6, 4), textcoords="offset points",
                    ha="left", va="bottom", color=edge, fontsize=8, weight="bold",
                    bbox={"facecolor": "white", "edgecolor": "none", "pad": .4, "alpha": .8})
        seen.add(note["kind"])


def _glossary(ax, boundary, elastic, annotations):
    ax.axis("off")
    nn = boundary["nearest_neighbor_angstrom"]
    entries = [
        ("near-GB atoms (GB = grain boundary)", "Defect band plus neighboring atoms within ~1 NN on each side. External surfaces are excluded."),
        ("non-bulk core atoms", "Interface atoms not assigned to the main FCC/BCC structure by PTM. These are seeds, not identified dislocation cores."),
        (f"NN = nearest-neighbor distance: {nn:.3f} Å ({nn / 10:.4f} nm)", "RMS/NN is the 3D nearest-atom matching error after x translation, divided by NN. Lower is better; this is not lattice mismatch."),
        ("Strain near / middle / outer", "Valid crystal atoms 0–3 / 3–6 / 6–10 NN from the defect-band edges. These sets differ from the coordinate near-GB set."),
        ("Upper : lower lattice correspondence", "Counts multiply full a[u v w] vectors. ≈ denotes measured counts, not allowed PBC coefficients. Nominal pairs match both grain lengths within 0.3 NN; raw feature lengths remain separate."),
        ("FFT wavelength / spatial interval / atomic translation", "Global Fourier oscillation / spacing of successive like extrema / approximate atomic-pattern match. Intervals need not be global periods."),
        ("FCC/BCC lattice closure (reduced [uvw])", "FCC: half-integer steps iff u+v+w is even. BCC: iff all three are odd. Otherwise integer steps. Divide by gcd for unreduced indices. Closure is necessary, not sufficient for a PBC cell."),
        ("Structure FFT and bulk references", "Log power exposes weak peaks. Dashed far-field curves share total-power normalization; contrast compares spectral shapes, not absolute scattering intensity."),
        ("Depth / smooth", "Distance from the GB reference plane to the sampling-layer center / Gaussian standard deviation. Maxima/minima are local extrema."),
        ("Reviewed FFT peaks / starred maxima", "Review uses prominence, sampling stability and evidence. * means an actual maximum fails checks, not that a nearby S/L guide was measured by FFT."),
        ("Strain FFT and far-field references", "Powers retain absolute strain amplitudes on one scale. Far-field peaks describe depth persistence, not automatic rejection. Grey dotted lines mark actual maxima."),
        ("Short row motif → nearby lattice candidate", "For a nearby association, the S guide keeps the row measurement. Its distinct coordinate candidate supplies a possible lattice correspondence; both lengths remain explicit."),
    ]
    best = next((r for r in annotations["records"] if r["highlighted"]), None)
    if best:
        highlight = (f"★ {best['candidate_id']} · {best['translation_nm']:.3f} nm · {best.get('display_lattice_label', best['ratio_label'])}  |  "
                     f"Review score {best['highlight_score']:.3f} = 0.4×row {best['row_support']['score']:.3f} "
                     f"+ 0.3×strain {best['strain_support']['score']:.3f} + 0.3×geometry {best['geometry_score']:.3f}")
    else:
        highlight = "No candidate passes lattice closure plus local-repeat and geometry requirements. Raw observations remain available."
    ax.text(.005, 1.13, highlight, fontsize=10, color=GOLD, transform=ax.transAxes)
    ax.text(.005, 1.045, "Gold = review priority, not a probability or unique period. Correlated channels do not add votes. Rules/evidence: multiscale_review.json.",
            fontsize=9, transform=ax.transAxes)
    for i, (heading, meaning) in enumerate(entries):
        col, row = i % 2, i // 2
        ax.text(.005 + .51 * col, .97 - .145 * row, heading,
                fontsize=10, weight="bold", va="top", transform=ax.transAxes)
        # Avoid a single long line exceeding the figure's right edge.
        meaning = fill(meaning, width=96)
        ax.text(.005 + .51 * col, .92 - .145 * row, meaning,
                fontsize=9, va="top", linespacing=1.4, transform=ax.transAxes)
    refs = " · ".join(f"{side}: {r['lattice']}, a_far = {r['a_reference_angstrom']:.5f} Å"
                      for side, r in elastic["references"].items())
    ax.text(.005, .015, refs, fontsize=9, transform=ax.transAxes)
    ax.text(.005, -.07, "S/L guides are motif / interval / recurrence hypotheses, not confirmed global periods; they do not identify dislocations or select PBC dimensions.",
            fontsize=8.5, transform=ax.transAxes)


# One summary height-ratio unit is about this many inches of the 16-inch-wide figure.
_INCHES_PER_RATIO = 1.5


def _atom_view_height_ratio(view_positions, interior, nn):
    """Size the 1:1 atom row so the view can span the figure width.

    A fixed aspect in a row that is too short shrinks the axes into a small box
    beside its colorbar. About 13.5 inches of width remain for the axes; the
    row adds title and label space and is capped for very narrow boundaries.
    """
    if view_positions is not None and len(view_positions):
        x_span, y_span = np.ptp(view_positions[:, 0]), np.ptp(view_positions[:, 1])
    else:
        x_span, y_span = interior[1] - interior[0], 20 * nn
    axes_inches = 13.5 * y_span / max(x_span, 1e-9)
    return float(np.clip(axes_inches + .9, 1.3, 5.5)) / _INCHES_PER_RATIO


def render_summary(case, out, rows, elastic, events, row_features, annotations, *, coordinate_dir=None):
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, LogLocator
    coordinate_dir = coordinate_dir if coordinate_dir is not None else case
    boundary = json.loads((coordinate_dir / "boundary.json").read_text())
    with np.load(ptm_cache_path(coordinate_dir) / "ptm_atoms.npz") as arrays:
        positions = arrays["positions"]
    with np.load(case / "elastic/elastic_atoms.npz") as arrays:
        valid, strain = arrays["valid"], arrays["volumetric_strain"]
    hierarchy_rows = max(3, len(annotations.get("short_period_annotations", [])),
                         len(annotations.get("long_period_annotations", [])))
    hierarchy_height = 1.3 + .25 * (hierarchy_rows - 3)
    nn = boundary["nearest_neighbor_angstrom"]
    interior = boundary["interior_x_interval_angstrom"]
    view = ((abs(positions[:, 1] - boundary["gb_y_angstrom"]) < 10 * nn)
            & (positions[:, 0] >= interior[0]) & (positions[:, 0] <= interior[1]))
    measured = view & valid & np.isfinite(strain)
    unmeasured = view & ~measured
    atom_ratio = _atom_view_height_ratio(positions[view] if np.any(view) else None, interior, nn)
    fig = plt.figure(figsize=(16, 28 + .3 * (hierarchy_rows - 3) + _INCHES_PER_RATIO * (atom_ratio - .85)),
                     layout="constrained")
    gs = fig.add_gridspec(9, 2, height_ratios=[atom_ratio, 1.5, 2.1, 2.1, 1.6, 1.9, hierarchy_height, 3.6, 3.7])
    atom_ax, profile_ax = fig.add_subplot(gs[0, :]), fig.add_subplot(gs[1, :])
    structure_ax, strain_ax = fig.add_subplot(gs[2, 0]), fig.add_subplot(gs[2, 1])
    structure_long_ax, strain_long_ax = fig.add_subplot(gs[3, 0]), fig.add_subplot(gs[3, 1])
    scale_ax, spatial_ax = fig.add_subplot(gs[4, :]), fig.add_subplot(gs[5, :])
    hierarchy_ax = fig.add_subplot(gs[6, :])
    candidates_ax, glossary_ax = fig.add_subplot(gs[7, :]), fig.add_subplot(gs[8, :])
    if np.any(measured):
        vmax = max(float(np.quantile(abs(strain[measured]), .98)), 1e-5) * 100
        dots = atom_ax.scatter(positions[measured, 0] / 10, positions[measured, 1] / 10,
                              c=100 * strain[measured], s=2, cmap="coolwarm", vmin=-vmax, vmax=vmax, rasterized=True)
        # Keep the fixed-aspect view centered; a right colorbar otherwise pulls it right.
        fig.colorbar(dots, ax=atom_ax, label="OVITO tr(e)/3 / %", shrink=.85, pad=.01, panchor=False)
    if np.any(unmeasured):
        # Atoms without a valid strain (mostly the GB core) stay visible.
        atom_ax.scatter(positions[unmeasured, 0] / 10, positions[unmeasured, 1] / 10,
                        c="black", s=2, rasterized=True)
    if not np.any(view):
        atom_ax.text(.5, .5, "No atoms in this view", transform=atom_ax.transAxes, ha="center")
    atom_ax.set_aspect("equal", adjustable="box")
    atom_ax.set(xlabel="x / nm", ylabel="y / nm",
                title="Atoms near GB · z projection · x/y scale 1:1 · black: strain not computable")
    x = np.asarray(elastic["x_angstrom"], dtype=float)
    strain_review_channels = _strain_review_channels(elastic)
    strain_reviewed = any("absolute_power_strain_squared" in data for data in strain_review_channels.values())
    strain_power_key = "absolute_power_strain_squared" if strain_reviewed else "relative_power"
    for profile in elastic["profiles"].values():
        if profile["z_channel"] != "all" or profile["distance_band"] == "far_field":
            continue
        label = f"{profile['grain']} {profile['distance_band']}"
        style = dict(color=GRAIN_COLORS[profile["grain"]], ls=BAND_STYLES[profile["distance_band"]], lw=.9)
        profile_ax.plot(x / 10, 100 * np.asarray(profile["signed_mean"], dtype=float), label=label, **style)
        _spectrum(strain_ax, profile["spectrum"], power_key=strain_power_key, **style)
        _spectrum(strain_long_ax, profile["long_spectrum"], power_key=strain_power_key, **style)
    if strain_reviewed:
        for grain, profile in _strain_reference_profiles(elastic).items():
            style = dict(color=GRAIN_COLORS[grain], ls=(0, (6, 2)), lw=1.2, alpha=.65,
                         label=f"{grain} far-field strain")
            _spectrum(strain_ax, profile.get("spectrum", {}), power_key=strain_power_key, **style)
            _spectrum(strain_long_ax, profile.get("long_spectrum", {}), power_key=strain_power_key, **style)
    profile_ax.set(xlabel="x / nm", ylabel="Signed strain / %",
                   title="Signed volumetric strain · per-grain, per-distance profiles relative to each far-field baseline")
    profile_ax.legend(fontsize=8, ncol=3, loc="lower left"); profile_ax.grid(alpha=.2)
    channels = elastic["structure_fft"]["channels"]
    bulk_aware = any("normalized_power" in data for data in channels.values())
    structure_power_key = "normalized_power" if bulk_aware else "relative_power"
    references_drawn = set()
    for name, data in channels.items():
        label = "near-GB atoms" if name == "near" else "non-bulk core atoms"
        style = dict(color=STRUCTURE_COLORS[name], lw=1)
        _spectrum(structure_ax, data, power_key=structure_power_key, label=label, **style)
        long = data.get("long_band", {})
        _spectrum(structure_long_ax, long, power_key=structure_power_key,
                  label=f"{label}: {100 * long.get('band_power_fraction', 0):.3g}% power", **style)
        if bulk_aware:
            for grain, reference in data.get("background_references", {}).items():
                if grain in references_drawn:
                    continue
                references_drawn.add(grain)
                reference_style = dict(color=GRAIN_COLORS[grain], ls="--", lw=.8, alpha=.75,
                                       label=f"{grain} far-field crystal")
                _spectrum(structure_ax, reference, power_key="normalized_power", **reference_style)
                _spectrum(structure_long_ax, reference, power_key="normalized_power", **reference_style)
    structure_ax.set_title("Structure FFT · weak peaks and far-field crystal background")
    strain_ax.set_title("Signed-strain FFT · weak peaks, absolute amplitude retained" if strain_reviewed else
                        "Signed-strain FFT · grain / distance channels kept separate")
    structure_long_ax.set_title("Structure FFT · long-wave detail, same power normalization")
    strain_long_ax.set_title("Signed-strain FFT · long-wave detail, same absolute powers" if strain_reviewed else
                             "Signed-strain FFT · long-wave detail, normalized within band")
    structure_ax.legend(fontsize=7.5, ncol=2); structure_long_ax.legend(fontsize=7.5, ncol=2, loc="lower left")
    if strain_reviewed and _strain_reference_profiles(elastic):
        strain_ax.legend(fontsize=7.5, ncol=2)
        if strain_long_ax.get_legend_handles_labels()[0]:
            strain_long_ax.legend(fontsize=7.5, ncol=2, loc="upper right")
    for ax in (structure_ax, strain_ax, structure_long_ax, strain_long_ax):
        ax.set(xlabel="Fourier wavelength / nm (log scale)", xscale="log",
               ylabel="Power / band maximum", ylim=(0, 1.08))
        ax.xaxis.set_major_locator(LogLocator(base=10, numticks=12))
        ax.xaxis.set_minor_locator(LogLocator(base=10, subs=(2, 3, 5), numticks=24))
        ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
        ax.xaxis.set_minor_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
        ax.tick_params(axis="x", which="both", labelsize=9)
        ax.grid(which="both", alpha=.2)
    maximum = elastic["long_band_policy"]["max_period_angstrom"] / 10
    for ax in (structure_ax, strain_ax):
        ax.set_xlim(2.5 * nn / 10, maximum)
    for ax in (structure_long_ax, strain_long_ax):
        ax.set_xlim(elastic["long_band_policy"]["min_period_angstrom"] / 10, maximum)
    if bulk_aware:
        for ax in (structure_ax, structure_long_ax):
            _log_power_axis(ax)
    if strain_reviewed:
        for ax in (strain_ax, strain_long_ax):
            _log_power_axis(ax, ylabel="Strain² power / bin (log scale)")
    guide_periods = {}
    for ax in (structure_ax, strain_ax, structure_long_ax, strain_long_ax):
        guide_periods[ax] = _period_guides(ax, annotations)
        ax.set_xlabel("Fourier wavelength / nm (log scale)\nDashed S/L: motif / interval hypotheses; dotted: actual FFT maxima", fontsize=9)
    if bulk_aware:
        for ax in (structure_ax, structure_long_ax):
            ax.set_xlabel("Fourier wavelength / nm (log scale)\nDotted maxima: * fails review checks; dashed S/L: independent hypotheses", fontsize=8.5)
    if strain_reviewed:
        for ax in (strain_ax, strain_long_ax):
            ax.set_xlabel("Fourier wavelength / nm (log scale)\nDotted maxima: * fails review checks; far-field presence alone does not reject", fontsize=8.5)
    _peak_labels(structure_ax, _structure_label_periods(channels, annotations),
                 count=5, candidate_periods=guide_periods[structure_ax],
                 unconfirmed_periods=_unconfirmed_structure_periods(channels))
    _peak_labels(strain_ax, _structure_label_periods(strain_review_channels, annotations),
                 count=5, candidate_periods=guide_periods[strain_ax],
                 unconfirmed_periods=_unconfirmed_structure_periods(strain_review_channels))
    _peak_labels(structure_long_ax, _structure_label_periods(channels, annotations, long_band=True),
                 candidate_periods=guide_periods[structure_long_ax],
                 unconfirmed_periods=_unconfirmed_structure_periods(channels, long_band=True))
    _peak_labels(strain_long_ax, _structure_label_periods(strain_review_channels, annotations, long_band=True),
                 candidate_periods=guide_periods[strain_long_ax],
                 unconfirmed_periods=_unconfirmed_structure_periods(strain_review_channels, long_band=True))
    fractions = [p["long_spectrum"].get("band_power_fraction", 0) for p in elastic["profiles"].values()
                 if p["z_channel"] == "all" and p["distance_band"] != "far_field"]
    if fractions:
        strain_long_ax.text(.98, .04, f"Band power: {100 * min(fractions):.3g}%–{100 * max(fractions):.3g}% of each profile",
                            transform=strain_long_ax.transAxes, ha="right", fontsize=8)
    minimum_long = elastic["long_band_policy"]["min_period_angstrom"] / 10
    if minimum_long >= maximum:
        # A short specimen may support the main spectrum but no separate long
        # band. Show that limit explicitly without fabricating power or peaks.
        for ax, title in ((structure_long_ax, "Structure FFT · long-wave detail"),
                          (strain_long_ax, "Signed-strain FFT · long-wave detail")):
            ax.clear()
            ax.set_axis_off()
            ax.set_title(title)
            ax.text(.5, .5, "Long-wave band unresolved in this observation window\n"
                    f"Requested lower bound {minimum_long:.3f} nm; available maximum {maximum:.3f} nm\n"
                    "This does not establish absence of a longer period.",
                    ha="center", va="center", fontsize=11, transform=ax.transAxes)
    tracks = candidate_scale_tracks(rows, elastic, events, row_features)
    for i, (label, periods, color) in enumerate(tracks):
        values = sorted(set(float(t) / 10 for t in periods if np.isfinite(t) and t > 0))
        scale_ax.scatter(values, np.full(len(values), i), marker="|", s=130, color=color, linewidths=1.2)
    scale_ax.set_yticks(range(len(tracks)), [label for label, _, _ in tracks], fontsize=9)
    scale_ax.set(xscale="log", xlabel="Candidate length scale / nm (log scale; marks can overlap)", ylim=(len(tracks) - .5, -.5),
                 title="All reviewed candidates + bulk-like FFT peaks · marks do not imply equal evidence")
    scale_ax.grid(axis="x", alpha=.2)
    _spatial_features(spatial_ax, events)
    _hierarchy_overview(hierarchy_ax, annotations)
    _coordinate_candidates(candidates_ax, rows, annotations)
    _glossary(glossary_ax, boundary, elastic, annotations)
    fig.suptitle(f"Structure + elastic-strain GB periodicity · {Path(elastic['source']).stem}", fontsize=17, weight="bold")
    fig.savefig(out / "multiscale_summary.png", dpi=170)
    plt.close(fig)
