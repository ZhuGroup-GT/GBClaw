"""Reviewable diagnostic figures; plots never silently select a candidate."""
from __future__ import annotations
import numpy as np


def plot_selection(out, arrays, boundary, masks):
    """Project all z layers; a thin fixed-z cut can miss relaxed atomic planes."""
    import matplotlib.pyplot as plt
    p, types = arrays["positions"], arrays["types"]
    fig, axes = plt.subplots(2, 1, figsize=(13, 8), layout="constrained")
    bulk = boundary["dominant_bulk_structure_type"] if boundary else 1
    axes[0].scatter(p[:, 0], p[:, 1], s=2, c="#aabcca", rasterized=True)
    nonbulk = types != bulk
    axes[0].scatter(p[nonbulk, 0], p[nonbulk, 1], s=2, c="#b5503e", rasterized=True)
    axes[0].set(xlabel="x / Å", ylabel="y / Å",
                title="Full-system PTM: crystalline bulk and nonbulk; all atoms projected along z; x/y scale 1:1")
    if boundary:
        lo, hi = boundary["interior_x_interval_angstrom"]
        for x in (lo, hi):
            axes[0].axvline(x, color="black", ls="--", lw=.8)
        axes[0].axhspan(*boundary["defect_band_y_angstrom"], color="#b5503e", alpha=.2)
        near = masks["near"]
        axes[1].scatter(p[near, 0], p[near, 1], s=6,
                        c=np.where(p[near, 1] < boundary["gb_y_angstrom"], "#222222", "#ddd7c8"),
                        edgecolors="#777777", linewidths=.2, rasterized=True)
        defect = masks["defects"]
        axes[1].scatter(p[defect, 0], p[defect, 1], s=15, facecolors="none", edgecolors="#b5503e", linewidths=.5)
        axes[1].set(xlim=(lo, hi), ylim=boundary["near_boundary_y_angstrom"],
                    title="Selected interface neighborhood: all z layers projected; PTM nonbulk atoms outlined")
    else:
        axes[1].text(.03, .5, "Boundary selection has not resolved a supported interface yet.", transform=axes[1].transAxes)
    axes[1].set(xlabel="x / Å", ylabel="y / Å")
    for ax in axes:
        ax.set_aspect("equal", adjustable="box")
    fig.savefig(out/"periodicity_diagnostics.png", dpi=150)
    plt.close(fig)


def plot_in_plane(out, report, acfs):
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), layout="constrained")
    for row, (name, acf) in enumerate(acfs.items()):
        x, z, corr = acf["x_lag"], acf["z_lag"], acf["correlation"]
        axes[row, 0].imshow(corr.T, origin="lower", aspect="auto", extent=[x[0], x[-1], z[0], z[-1]],
                            vmin=-1, vmax=1, cmap="coolwarm")
        axes[row, 0].set(xlabel="x translation / Å", ylabel="z translation / Å", title=f"{name}: x-z correlation with separate y channels")
        j = np.argmin(abs(z))
        axes[row, 1].plot(x, corr[:, j], label="Pure x")
        axes[row, 1].plot(x, corr.max(axis=1), label="Best z phase", alpha=.8)
        axes[row, 1].set(xlabel="x translation / Å", ylabel="Normalized correlation", title="Transverse phase is retained; peaks are proposals")
        axes[row, 1].legend()
    fig.savefig(out/"in_plane_periodicity.png", dpi=150)
    plt.close(fig)


def plot_modulation(out, report, profiles):
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(3, 1, figsize=(13, 10), layout="constrained")
    modulation = report["long_modulation"]
    x = np.asarray(profiles.get("x_angstrom", []))
    for name, curves in profiles.items():
        if name == "x_angstrom":
            continue
        raw, envelope = np.asarray(curves["raw"]), np.asarray(curves["envelope"])
        scale = max(float(raw.std()), 1e-15)
        axes[0].plot(x, (envelope-raw.mean())/scale, label=name)
        freq = np.asarray(curves["frequency_inverse_angstrom"])
        axes[1].plot(1/freq, curves["relative_power"], label=name)
    rows = 0
    for hypothesis in modulation["hypotheses"]:
        if hypothesis["status"] != "candidate":
            continue
        for signal, features in hypothesis["local_features_by_signal"].items():
            positions = features.get("positions_x_angstrom", [])
            if not positions:
                continue
            axes[2].scatter(positions, np.full(len(positions), rows), s=18)
            for a, b in zip(positions[:-1], positions[1:]):
                axes[2].plot([a, b], [rows, rows], lw=.6, c="gray")
                axes[2].annotate(f"{b-a:.1f}", ((a+b)/2, rows), xytext=(0, 4), textcoords="offset points", ha="center", fontsize=7)
            axes[2].annotate(f"{hypothesis['id']} {signal}", (0, rows), xycoords=("axes fraction", "data"), xytext=(0, -11), textcoords="offset points", fontsize=7)
            rows += 1
    if len(x):
        axes[0].legend(fontsize=8); axes[1].legend(fontsize=8)
    else:
        axes[0].text(.03, .5, modulation.get("reason", modulation["status"]), transform=axes[0].transAxes)
    if not rows:
        axes[2].text(.03, .5, "No resolved local features with supported long-scale evidence.", transform=axes[2].transAxes)
    axes[0].set(xlabel="Position x / Å", ylabel="Envelope / raw standard deviation", title=f"Long modulation: {modulation['status']}")
    axes[1].set(xlabel="Wavelength / Å", ylabel="Relative spectral power", title="Finite spectral peaks require nonbulk xyz validation")
    axes[2].set(xlabel="Position x / Å", yticks=[], ylim=(-.8, max(1, rows)), title="Field-dependent local crests and spacings / Å; not identified dislocation cores")
    fig.savefig(out/"long_period_modulation.png", dpi=150)
    plt.close(fig)
