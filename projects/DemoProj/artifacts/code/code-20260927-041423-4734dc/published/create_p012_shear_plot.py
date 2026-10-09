#!/usr/bin/env python3
"""Create a stress-strain plot and tabular data from staged P012 shear metadata."""
import csv
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SOURCE = Path("/inputs/000-shear_run.json")
OUT = Path("/workspace")
SOURCE_RUN_ID = "shear-20260927-040956-e24773"
SOURCE_ARTIFACT_ID = "6579ee6c-35ac-558f-a9c5-fa4a7e5cd41e"
SOURCE_SHA256 = "d1b0ded0342085d3fe348db75c5855399119e77b9d6a97ef8f4e6fb2e21a4468"

with SOURCE.open(encoding="utf-8") as f:
    run = json.load(f)
points = run["points"]
if len(points) != 71:
    raise ValueError(f"Expected 71 recorded points, found {len(points)}")
if [p["step"] for p in points] != list(range(71)):
    raise ValueError("Points must be ordered consecutive reference step 0 through step 70")

rows = []
for p in points:
    strain = float(p["actual_shear_strain"])
    stress = float(p["top_shear_stress_mpa"])
    if not (math.isfinite(strain) and math.isfinite(stress)):
        raise ValueError(f"Non-finite plotting datum at step {p['step']}")
    rows.append({"step": p["step"], "actual_shear_strain": strain,
                 "top_shear_stress_mpa": stress, "converged": bool(p["converged"])})

nonconverged = sum(not row["converged"] for row in rows)
peak = max(rows, key=lambda row: row["top_shear_stress_mpa"])
final = rows[-1]
if not math.isclose(final["actual_shear_strain"], 0.0175, rel_tol=0, abs_tol=1e-12):
    raise ValueError(f"Unexpected final strain: {final['actual_shear_strain']}")
if not math.isclose(peak["top_shear_stress_mpa"], 834.7864243899115, rel_tol=0, abs_tol=1e-6):
    raise ValueError(f"Unexpected peak stress: {peak['top_shear_stress_mpa']}")
if not math.isclose(peak["actual_shear_strain"], 0.009, rel_tol=0, abs_tol=1e-12):
    raise ValueError(f"Unexpected peak strain: {peak['actual_shear_strain']}")
if not math.isclose(final["top_shear_stress_mpa"], 21.758443040431928, rel_tol=0, abs_tol=1e-6):
    raise ValueError(f"Unexpected final stress: {final['top_shear_stress_mpa']}")

csv_path = OUT / "p012_shear_stress_strain.csv"
with csv_path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["step", "actual_shear_strain", "top_shear_stress_mpa", "converged"])
    writer.writeheader()
    writer.writerows(rows)

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.linewidth": 0.9,
                     "axes.labelweight": "medium", "figure.dpi": 160, "savefig.dpi": 300})
fig, ax = plt.subplots(figsize=(7.25, 4.65), constrained_layout=True)
x = [r["actual_shear_strain"] for r in rows]
y = [r["top_shear_stress_mpa"] for r in rows]
ax.plot(x, y, color="#1565A9", lw=2.0, marker="o", ms=2.5, mfc="white", mec="#1565A9", mew=0.65,
        label="Top-boundary shear stress")
ax.scatter([peak["actual_shear_strain"]], [peak["top_shear_stress_mpa"]], s=42, color="#C53A32", zorder=4, label="Peak signed stress")
ax.annotate(f"Peak: {peak['top_shear_stress_mpa']:.3f} MPa\n$\\gamma$ = {peak['actual_shear_strain']:.4f}",
            xy=(peak["actual_shear_strain"], peak["top_shear_stress_mpa"]), xytext=(-92, 30),
            textcoords="offset points", arrowprops={"arrowstyle": "->", "color": "#555555", "lw": 0.9},
            fontsize=9, bbox={"boxstyle": "round,pad=0.25", "fc": "white", "ec": "#999999", "alpha": 0.94})
ax.axhline(0, color="#777777", lw=0.7, zorder=0)
ax.set_xlabel("Actual cumulative shear strain, $\\gamma$")
ax.set_ylabel("Top-boundary shear stress (MPa)")
ax.set_title("P012 FCC Ni — +x displacement shear, Δγ = 2.5×10⁻⁴", pad=10)
ax.set_xlim(-0.00035, 0.0180)
ax.grid(True, color="#D7DCE1", lw=0.65, alpha=0.85)
ax.legend(frameon=False, loc="upper left")
for side in ("top", "right"):
    ax.spines[side].set_visible(False)
fig.savefig(OUT / "p012_shear_stress_strain.png", bbox_inches="tight")
plt.close(fig)

summary = {
    "source": {
        "source_run_id": SOURCE_RUN_ID,
        "source_artifact_id": SOURCE_ARTIFACT_ID,
        "source_role": "run_metadata",
        "staged_source_path": str(SOURCE),
        "source_sha256": SOURCE_SHA256,
        "source_status": run["status"],
        "parent_run_id": run["lineage"]["parent_run_id"],
        "parent_workflow": run["lineage"]["parent_workflow"],
    },
    "curve_definition": {
        "material": "P012 FCC Ni",
        "loading": "+x displacement shear",
        "strain_increment": run["loading"]["initial_strain_increment"],
        "x_quantity": "signed actual/cumulative shear strain",
        "y_quantity": "signed top-boundary shear stress",
        "stress_units": "MPa",
        "included_points": "all finite recorded points; no convergence-status filtering",
    },
    "point_count": len(rows),
    "nonconverged_point_count": nonconverged,
    "peak_signed_top_shear_stress": {"step": peak["step"], "actual_shear_strain": peak["actual_shear_strain"], "stress_mpa": peak["top_shear_stress_mpa"]},
    "final_point": {"step": final["step"], "actual_shear_strain": final["actual_shear_strain"], "stress_mpa": final["top_shear_stress_mpa"], "converged": final["converged"]},
}
with (OUT / "p012_shear_stress_strain_summary.json").open("w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2)
    f.write("\n")
print(json.dumps({"points": len(rows), "nonconverged": nonconverged, "peak": peak, "final": final}))
