"""Locate a planar, y-normal grain boundary and render its two facing layers.

Run this module in the OVITO environment, in an isolated process. CNA is
computed on the complete structure before any slice is applied. Automatic
location is a geometric estimate, not a crystallographic grain assignment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any

import numpy as np

if __package__:
    from .ovito_rendering import crop_to_content
else:  # Executed as a script beside ovito_rendering.py.
    from ovito_rendering import crop_to_content

if __package__:
    from .ovito_rendering import (
        summarize_displacements, add_displacement_modifier,
        configure_displacement_display, validate_arrow_policy, ARROW_SCALE_MODES,
    )
else:
    from ovito_rendering import (
        summarize_displacements, add_displacement_modifier,
        configure_displacement_display, validate_arrow_policy, ARROW_SCALE_MODES,
    )


class BoundaryDetectionError(ValueError):
    """Automatic selection cannot justify one boundary/two facing layers."""

    def __init__(self, message: str, diagnostics: dict[str, Any] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


def _positive(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def _layers(values: np.ndarray, gap: float) -> list[dict[str, Any]]:
    values = np.sort(values)
    if not len(values):
        return []
    groups = np.split(values, np.flatnonzero(np.diff(values) > gap) + 1)
    return [dict(y=float(np.median(g)), lower_y=float(g[0]), upper_y=float(g[-1]),
                 count=int(len(g))) for g in groups]


def _spacing(layers: list[dict[str, Any]]) -> float | None:
    if len(layers) < 3:
        return None
    return float(np.median(np.diff([layer["y"] for layer in layers])))


def analyze_planar_boundary(
    positions: np.ndarray,
    structure_types: np.ndarray,
    cell: np.ndarray,
    nearest_neighbor_distance: float,
    *,
    gb_y: float | None = None,
    slab_thickness: float | None = None,
) -> dict[str, Any]:
    """Analyze imported Cartesian coordinates (lengths in the input's units).

    Surface exclusion is only used for locating the boundary. The rendered
    slab keeps its complete lateral extent, including free edges. Numerical
    thresholds scale with the median nearest-neighbor distance and are
    recorded in the report. No fixture-specific coordinates are used.
    """
    pos = np.asarray(positions, dtype=float)
    types = np.asarray(structure_types)
    matrix = np.asarray(cell, dtype=float)
    nn = _positive(nearest_neighbor_distance, "nearest_neighbor_distance")
    if pos.ndim != 2 or pos.shape[1] != 3 or len(pos) < 4 or not np.isfinite(pos).all():
        raise ValueError("At least four finite 3-D particle positions are required")
    if types.shape != (len(pos),):
        raise ValueError("One CNA structure type is required per particle")
    if matrix.shape != (3, 4) or not np.isfinite(matrix).all():
        raise ValueError("cell must be a finite OVITO 3 x 4 cell matrix")
    lengths = np.diag(matrix[:, :3])
    if np.any(lengths <= 0) or not np.allclose(matrix[:, :3], np.diag(lengths), atol=1e-8):
        raise ValueError("Automatic plan view currently requires an axis-aligned orthogonal cell")
    if gb_y is not None and not math.isfinite(float(gb_y)):
        raise ValueError("gb_y must be finite")
    if slab_thickness is not None:
        slab_thickness = _positive(slab_thickness, "slab_thickness")

    occupied_min, occupied_max = pos.min(axis=0), pos.max(axis=0)
    extent = occupied_max - occupied_min
    vacuum = lengths - extent > 3 * nn
    nonbulk = ~np.isin(types, (1, 3))  # FCC and BCC are not interface seeds.
    core = np.ones(len(pos), dtype=bool)
    trims: dict[str, Any] = {}
    for axis in (0, 2):
        if vacuum[axis]:
            margin = max(3 * nn, 0.1 * extent[axis])
            if 2 * margin >= extent[axis]:
                raise BoundaryDetectionError("Insufficient interior width after removing lateral surfaces")
            lo, hi = occupied_min[axis] + margin, occupied_max[axis] - margin
            core &= (pos[:, axis] >= lo) & (pos[:, axis] <= hi)
            trims["xyz"[axis]] = dict(lower=float(lo), upper=float(hi), margin=float(margin))
    interior = core & (pos[:, 1] > occupied_min[1] + 3 * nn) & (pos[:, 1] < occupied_max[1] - 3 * nn)
    seeds = pos[interior & nonbulk, 1]
    bin_width = 0.1 * nn
    edges = np.arange(occupied_min[1] - bin_width / 2, occupied_max[1] + 1.5 * bin_width, bin_width)
    all_hist, _ = np.histogram(pos[:, 1], edges)
    defect_hist, _ = np.histogram(pos[nonbulk, 1], edges)
    core_hist, _ = np.histogram(seeds, edges)
    threshold = max(4, int(math.ceil(0.15 * int(core_hist.max(initial=0)))))
    hot = np.flatnonzero(core_hist >= threshold)
    groups = np.split(hot, np.flatnonzero(np.diff(hot) * bin_width > 2 * nn) + 1) if len(hot) else []
    candidates = []
    for group in groups:
        lo, hi = float(edges[group[0]]), float(edges[group[-1] + 1])
        # Require lateral support in both in-plane directions. A localized
        # defect should not become a planar interface just because it is dense.
        mask = interior & nonbulk & (pos[:, 1] >= lo) & (pos[:, 1] <= hi)
        coverage = []
        for axis in (0, 2):
            lower = float(pos[core, axis].min())
            upper = float(pos[core, axis].max())
            h, _ = np.histogram(pos[mask, axis], np.linspace(lower - 1e-8, upper + 1e-8, 9))
            coverage.append(float(np.count_nonzero(h) / 8))
        candidates.append(dict(lower_y=lo, upper_y=hi, count=int(mask.sum()),
                               lateral_coverage=coverage, supported=min(coverage) >= 0.5))
    diagnostics = dict(
        nearest_neighbor_distance=nn, vacuum_axes=["xyz"[i] for i in range(3) if vacuum[i]],
        occupied_bounds=np.stack((occupied_min, occupied_max)).tolist(),
        lateral_detection_crops=trims,
        normal_detection_bounds=[float(occupied_min[1] + 3 * nn), float(occupied_max[1] - 3 * nn)],
        histogram_bin_width=bin_width, dense_bin_min_count=threshold,
        dense_bin_peak_fraction=0.15, candidate_join_distance=2 * nn,
        layer_join_distance=0.25 * nn, candidates=candidates,
        profile=dict(y=((edges[:-1] + edges[1:]) / 2).tolist(), all_atoms=all_hist.tolist(),
                     non_fcc_bcc=defect_hist.tolist(), interior_non_fcc_bcc=core_hist.tolist()),
    )
    warnings = [
        "Boundary y is a geometric estimate for one approximately planar y-normal interface; "
        "CNA and plane spacings do not prove crystallographic grain membership.",
        "Black and milk white label the lower and upper y sides in the plan view, respectively; they are not CNA colors.",
    ]
    supported = [candidate for candidate in candidates if candidate["supported"]]
    if gb_y is None:
        if len(supported) != 1:
            raise BoundaryDetectionError(
                f"Found {len(supported)} supported interior defect bands; supply gb_y to select a boundary", diagnostics)
        candidate = supported[0]
        if candidate["upper_y"] - candidate["lower_y"] > 5 * nn:
            raise BoundaryDetectionError("Defect band is too broad for a two-layer automatic slice; supply gb_y and slab_thickness", diagnostics)
        band_seeds = np.sort(seeds[(seeds >= candidate["lower_y"]) & (seeds <= candidate["upper_y"])])
        gaps = np.diff(band_seeds)
        if not len(gaps) or gaps.max() < 0.25 * nn:
            raise BoundaryDetectionError("No resolved interlayer gap in the defect band; supply gb_y and slab_thickness", diagnostics)
        split = int(np.argmax(gaps))
        estimated_y = float((band_seeds[split] + band_seeds[split + 1]) / 2)
        method = "cna_interior_band_interlayer_gap"
    else:
        estimated_y = float(gb_y)
        method = "user_supplied"

    all_layers = _layers(pos[core, 1], 0.25 * nn)
    lower_indices = [i for i, layer in enumerate(all_layers) if layer["upper_y"] < estimated_y]
    upper_indices = [i for i, layer in enumerate(all_layers) if layer["lower_y"] > estimated_y]
    lower_i = lower_indices[-1] if lower_indices else None
    upper_i = upper_indices[0] if upper_indices else None
    pair_resolved = lower_i is not None and upper_i == lower_i + 1
    if not pair_resolved and slab_thickness is None:
        raise BoundaryDetectionError("Boundary is not in a resolved gap between adjacent layers; supply gb_y and slab_thickness", diagnostics)
    lower_layer = all_layers[lower_i] if lower_i is not None else None
    upper_layer = all_layers[upper_i] if upper_i is not None else None
    if method != "user_supplied":
        if not pair_resolved:
            raise BoundaryDetectionError("CNA gap is bridged by bulk atoms; supply gb_y and slab_thickness", diagnostics)
        estimated_y = (lower_layer["upper_y"] + upper_layer["lower_y"]) / 2
    full_layer_bounds = None
    if slab_thickness is None:
        # Extend the interior layer pair to free lateral edges. Midplanes to
        # the next interior planes define conservative geometric membership;
        # the final symmetric slice must still exclude every neighboring atom.
        outer_lo, outer_hi = -math.inf, math.inf
        if lower_i > 0:
            outer_lo = (all_layers[lower_i - 1]["y"] + lower_layer["y"]) / 2
        if upper_i + 1 < len(all_layers):
            outer_hi = (all_layers[upper_i + 1]["y"] + upper_layer["y"]) / 2
        full_pair = (pos[:, 1] > outer_lo) & (pos[:, 1] < outer_hi)
        full_lower = pos[full_pair & (pos[:, 1] < estimated_y), 1]
        full_upper = pos[full_pair & (pos[:, 1] >= estimated_y), 1]
        required = max(estimated_y - float(full_lower.min()), float(full_upper.max()) - estimated_y)
        excluded = np.abs(pos[~full_pair, 1] - estimated_y)
        limits = [float(excluded.min())] if len(excluded) else []
        available = min(limits) if limits else required + nn
        if available <= required:
            raise BoundaryDetectionError("A symmetric slab cannot isolate both full facing layers at lateral edges; supply slab_thickness", diagnostics)
        half_width = required + min(0.05 * nn, 0.25 * (available - required))
        slab_thickness = 2 * half_width
        slab_method = "one_facing_layer_per_side_with_lateral_extension"
        full_layer_bounds = {
            "method": "interior_plane_midpoint_assignment",
            "lower": dict(lower_y=float(full_lower.min()), upper_y=float(full_lower.max()), count=len(full_lower)),
            "upper": dict(lower_y=float(full_upper.min()), upper_y=float(full_upper.max()), count=len(full_upper)),
        }
    else:
        half_width = slab_thickness / 2
        slab_method = "user_supplied"
        warnings.append("User-supplied thickness can contain partial or multiple layers per side.")
    lo, hi = estimated_y - half_width, estimated_y + half_width
    selected = (pos[:, 1] >= lo) & (pos[:, 1] <= hi)
    lower_count = int(np.count_nonzero(selected & (pos[:, 1] < estimated_y)))
    upper_count = int(np.count_nonzero(selected & (pos[:, 1] >= estimated_y)))
    if not lower_count or not upper_count:
        raise BoundaryDetectionError("Selected slab must contain atoms on both sides of gb_y", diagnostics)
    if trims:
        warnings.append("Lateral surfaces were excluded only during detection; full-width layer assignment uses interior-plane midpoints and remains approximate at distorted edges.")
    # Exclude the two most distorted planes adjoining the interface when
    # estimating bulk plane spacing. Report null when too few remain.
    lower_spacing = _spacing(all_layers[max(0, lower_i - 8):max(0, lower_i - 1)]) if lower_i is not None else None
    upper_spacing = _spacing(all_layers[upper_i + 2:upper_i + 9]) if upper_i is not None else None
    if lower_spacing is not None and upper_spacing is not None:
        if abs(lower_spacing - upper_spacing) < 0.05 * max(lower_spacing, upper_spacing):
            warnings.append("The two side spacings are similar; spacing does not independently distinguish the grains.")
    return dict(
        status="completed", coordinate_system="OVITO imported Cartesian positions; inspect cell origin before comparing with raw CFG fractions",
        boundary=dict(y=float(estimated_y), method=method),
        slab=dict(lower_y=float(lo), upper_y=float(hi), thickness=float(slab_thickness), method=slab_method,
                  full_lateral_layers=full_layer_bounds,
                  counts=dict(lower=lower_count, upper=upper_count, total=lower_count + upper_count),
                  interior_counts=dict(lower=int(np.count_nonzero(selected & core & (pos[:, 1] < estimated_y))),
                                       upper=int(np.count_nonzero(selected & core & (pos[:, 1] >= estimated_y))))),
        layers=dict(lower=lower_layer, upper=upper_layer, pair_resolved=pair_resolved,
                    lower_spacing=lower_spacing, upper_spacing=upper_spacing,
                    nearby=[layer for layer in all_layers if abs(layer["y"] - estimated_y) <= 5 * nn]),
        detection=diagnostics, warnings=warnings,
    )


def _plot_profile(report: dict[str, Any], destination: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    detection = report["detection"]
    profile = detection["profile"]
    y = np.asarray(profile["y"])
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), constrained_layout=True)
    for ax in axes:
        ax.plot(y, profile["non_fcc_bcc"], color="0.7", label="All non-FCC/BCC")
        ax.plot(y, profile["interior_non_fcc_bcc"], color="#bb5500", label="Interior non-FCC/BCC")
        ax.axvline(report["boundary"]["y"], color="#9944aa", ls="--", label="Estimated interface")
        ax.axvspan(report["slab"]["lower_y"], report["slab"]["upper_y"], color="#3388bb", alpha=0.16, label="Rendered slab")
        ax.set(xlabel="Imported y coordinate (input length units)", ylabel="Atoms per y bin")
        ax.grid(alpha=0.2)
    for edge in detection["normal_detection_bounds"]:
        axes[0].axvline(edge, color="0.5", ls=":")
    axes[0].set_title("CNA profile and surface exclusion")
    axes[0].legend(loc="upper right", fontsize=8)
    axes[1].plot(y, profile["all_atoms"], color="#226688", alpha=0.6, label="All atoms")
    axes[1].set_xlim(report["boundary"]["y"] - 4 * detection["nearest_neighbor_distance"],
                     report["boundary"]["y"] + 4 * detection["nearest_neighbor_distance"])
    for side, color in (("lower", "black"), ("upper", "#297ddd")):
        layer = report["layers"][side]
        if layer:
            axes[1].axvspan(layer["lower_y"], layer["upper_y"], color=color, alpha=0.3)
    axes[1].set_title("Facing layers (black: lower y; blue: upper y)")
    axes[1].legend(loc="upper right", fontsize=8)
    fig.savefig(destination, dpi=170, transparent=True)
    plt.close(fig)


def _render(pipeline: Any, report: dict[str, Any], destination: Path,
            image_width: int, view_width: float | None, atom_scale: float) -> dict[str, Any]:
    from ovito.modifiers import AssignColorModifier, ClearSelectionModifier, ExpressionSelectionModifier, SliceModifier
    from ovito.vis import TachyonRenderer, Viewport

    boundary_y = report["boundary"]["y"]
    upper_color = (1.0, 0.98, 0.94)  # Warm ivory / milk white.
    pipeline.modifiers.append(AssignColorModifier(color=upper_color))
    pipeline.modifiers.append(ExpressionSelectionModifier(expression=f"Position.Y < {boundary_y:.17g}"))
    pipeline.modifiers.append(AssignColorModifier(color=(0, 0, 0)))
    pipeline.modifiers.append(ClearSelectionModifier())
    pipeline.modifiers.append(SliceModifier(normal=(0, 1, 0), distance=boundary_y,
                                           slab_width=report["slab"]["thickness"], inverse=False))

    def set_display(frame, data):
        # This is OVITO's GUI "Radius scaling": preserve imported per-type or
        # per-particle radii and scale them globally (70% by default).
        data.particles_.vis.scaling = atom_scale
        data.cell_.vis.enabled = False
    pipeline.modifiers.append(set_display)
    data = pipeline.compute()
    pos = np.asarray(data.particles.positions)
    if len(pos) != report["slab"]["counts"]["total"]:
        raise RuntimeError("OVITO slice count differs from the recorded numerical selection")
    radii = np.full(len(pos), data.particles.vis.radius, dtype=float)
    if "Particle Type" in data.particles:
        for particle_type in data.particles.particle_types.types:
            if particle_type.radius > 0:
                radii[np.asarray(data.particles.particle_types) == particle_type.id] = particle_type.radius
    if "Radius" in data.particles:
        radii = np.asarray(data.particles["Radius"], dtype=float)
    radii = radii * atom_scale
    radius = float(radii.max())
    cell = np.asarray(data.cell)
    center = cell[:, 3] + 0.5 * np.sum(cell[:, :3], axis=1)
    x_half = float(np.max(np.abs(pos[:, 0] - center[0]) + radii))
    z_half = float(np.max(np.abs(pos[:, 2] - center[2]) + radii))
    if report.get("displacement"):
        style = report["displacement"]["render"]
        vectors = np.asarray(data.particles["Displacement"]) * style["scaling"]
        # Center-aligned arrow tips can extend beyond the atoms at free edges.
        # Include their projected extent when fitting the complete view.
        x_half = max(x_half, float(np.max(np.abs(pos[:, 0] - center[0]) + np.abs(vectors[:, 0]) / 2)) + style["width"])
        z_half = max(z_half, float(np.max(np.abs(pos[:, 2] - center[2]) + np.abs(vectors[:, 2]) / 2)) + style["width"])
    x_half *= 1.08
    z_half *= 1.08
    if view_width is not None:
        x_half = _positive(view_width, "view_width") / 2
    image_height = max(160, min(image_width, round(image_width * z_half / x_half)))
    field_half_height = (x_half * image_height / image_width if view_width is not None
                         else max(z_half, x_half * image_height / image_width))
    distance = float(np.linalg.norm(cell[:, :3], axis=0).max()) + 10
    viewport = Viewport(type=Viewport.Type.Ortho, camera_dir=(0, -1, 0), camera_up=(0, 0, -1),
                        camera_pos=(center[0], boundary_y + distance, center[2]), fov=field_half_height)
    pipeline.add_to_scene()
    try:
        viewport.render_image(filename=str(destination), size=(image_width, image_height),
                              alpha=True, renderer=TachyonRenderer(ambient_occlusion=False, shadows=False))
    finally:
        pipeline.remove_from_scene()
    crop = crop_to_content(destination)
    return dict(direction=[0, -1, 0], up=[0, 0, -1], roll_degrees=180,
                right=[1, 0, 0], box_center=center.tolist(), target=[float(center[0]), boundary_y, float(center[2])],
                fov_half_height=float(viewport.fov), **crop,
                visible_width=2 * field_half_height * image_width / image_height,
                atom_scale=atom_scale, atom_radius=radius,
                atom_radius_range=[float(radii.min()), radius], radius_source="OVITO imported radii",
                lower_color=[0, 0, 0], upper_color=list(upper_color), transparent_background=True)


def run_plan_view(source: Path | str, output_dir: Path | str, *, gb_y: float | None = None,
                  slab_thickness: float | None = None, pbc: tuple[bool, bool, bool] | None = None,
                  image_width: int = 3200, view_width: float | None = None,
                  reference: Path | str | None = None, atom_scale: float = 0.7,
                  arrow_width: float = 0.1, arrow_offset_y: float = 1.0,
                  arrow_target_length: float = 1.5,
                  arrow_scale_mode: str = "physical_capped", arrow_max_length: float = 2.0,
                  minimum_image_convention: bool = True) -> dict[str, Any]:
    """Compute CNA, write diagnostic evidence and render full/detail plan views."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("XDG_CACHE_HOME", str(Path(tempfile.gettempdir()) / "gbclaw-plan-view-cache"))
    os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "gbclaw-plan-view-matplotlib"))
    from ovito.data import NearestNeighborFinder
    from ovito.io import import_file
    from ovito.modifiers import CommonNeighborAnalysisModifier

    source = Path(source).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    if source.suffix.lower() != ".cfg" or not source.is_file():
        raise ValueError("source must be an existing CFG file")
    if not isinstance(image_width, int) or not 256 <= image_width <= 8192:
        raise ValueError("image_width must be an integer from 256 to 8192")
    if view_width is not None:
        _positive(view_width, "view_width")
    atom_scale = _positive(atom_scale, "atom_scale")
    _positive(arrow_width, "arrow_width")
    _positive(arrow_target_length, "arrow_target_length")
    validate_arrow_policy(arrow_scale_mode, arrow_max_length)
    if not math.isfinite(arrow_offset_y):
        raise ValueError("arrow_offset_y must be finite")
    if not isinstance(minimum_image_convention, bool):
        raise ValueError("minimum_image_convention must be a boolean")
    if pbc is not None and (len(pbc) != 3 or any(not isinstance(value, (bool, np.bool_)) for value in pbc)):
        raise ValueError("pbc must contain three booleans")
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "gb_plan_view.json"
    pipeline = import_file(str(source))
    if pipeline.source.num_frames != 1:
        raise ValueError("Select one CFG frame before running plan view")
    imported = pipeline.compute()
    imported_pbc = list(imported.cell.pbc)
    if pbc is not None:
        def set_pbc(frame, data):
            data.cell_.pbc = tuple(pbc)
        pipeline.modifiers.append(set_pbc)
    pipeline.modifiers.append(CommonNeighborAnalysisModifier(mode=CommonNeighborAnalysisModifier.Mode.AdaptiveCutoff))
    data = pipeline.compute()
    positions = np.asarray(data.particles.positions)
    sample = np.linspace(0, len(positions) - 1, min(len(positions), 2048), dtype=int)
    _, vectors = NearestNeighborFinder(1, data).find_all(sample)
    nn = float(np.median(np.linalg.norm(vectors[:, 0], axis=1)))
    source_info = dict(path=str(source), sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                       particle_count=len(positions), cell=np.asarray(data.cell).tolist(),
                       imported_pbc=imported_pbc, effective_pbc=list(data.cell.pbc),
                       pbc_source="user_supplied" if pbc is not None else "OVITO_import")
    try:
        report = analyze_planar_boundary(positions, np.asarray(data.particles["Structure Type"]),
                                         np.asarray(data.cell), nn, gb_y=gb_y, slab_thickness=slab_thickness)
    except BoundaryDetectionError as exc:
        failure = dict(status="needs_parameters", error=str(exc), source=source_info, detection=exc.diagnostics)
        report_path.write_text(json.dumps(failure, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        raise
    report["source"] = source_info
    report["warnings"].append("CFG periodicity follows OVITO import unless pbc is supplied; vacuum detection does not change boundary conditions.")
    mask = (positions[:, 1] >= report["slab"]["lower_y"]) & (positions[:, 1] <= report["slab"]["upper_y"])
    if "Particle Identifier" in data.particles:
        report["slab"]["particle_ids"] = np.asarray(data.particles["Particle Identifier"])[mask].tolist()
    report["cna_counts"] = {name: int(np.count_nonzero(np.asarray(data.particles["Structure Type"]) == value))
                            for value, name in enumerate(("Other", "FCC", "HCP", "BCC", "ICO"))}
    report["displacement"] = None
    if reference is not None:
        try:
            displacement_modifier, reference_info, displacement_warnings = add_displacement_modifier(
                pipeline, reference, pbc=pbc, minimum_image_convention=minimum_image_convention)
            displaced = pipeline.compute()
            report["displacement"] = summarize_displacements(
                np.asarray(displaced.particles["Displacement"]), mask,
                np.asarray(displaced.particles["Particle Identifier"]),
                target_length=arrow_target_length, zero_tolerance=1e-8 * nn,
                arrow_width=arrow_width, arrow_offset_y=arrow_offset_y,
                scale_mode=arrow_scale_mode, max_length=arrow_max_length)
            displacement = report["displacement"]
            displacement["minimum_image_convention"] = minimum_image_convention
            displacement["reference"] = reference_info
            style = displacement["render"]
            configure_displacement_display(displacement_modifier, displacement)
            report["warnings"].extend(displacement_warnings)
            if not style["nonzero_projected_count"]:
                report["warnings"].append("No nonzero XZ displacement is visible in the slab. Pure Y displacement is along the viewing direction; the 3D display limit still applies.")
        except Exception as exc:
            report.update(status="failed", error=str(exc))
            report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            raise
    _plot_profile(report, output_dir / "y_profile.png")
    # Use the same CNA pipeline for all renders; remove view modifiers before
    # rendering a detail so the scientific data remain identical.
    modifier_count = len(pipeline.modifiers)
    report["camera"] = _render(pipeline, report, output_dir / "plan_view.png", image_width, view_width, atom_scale)
    report["outputs"] = dict(plan_view="plan_view.png", profile="y_profile.png", data="gb_plan_view.json")
    if view_width is None and report["camera"]["visible_width"] > 24 * nn:
        while len(pipeline.modifiers) > modifier_count:
            del pipeline.modifiers[-1]
        report["detail_camera"] = _render(pipeline, report, output_dir / "plan_view_detail.png",
                                          min(image_width, 1800), 20 * nn, atom_scale)
        report["outputs"]["detail"] = "plan_view_detail.png"
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--gb-y", type=float)
    parser.add_argument("--slab-thickness", type=float)
    parser.add_argument("--pbc", help="Three boundary flags, for example 'p f p'; default is OVITO import")
    parser.add_argument("--image-width", type=int, default=3200)
    parser.add_argument("--view-width", type=float, help="Horizontal field width in input length units")
    parser.add_argument("--reference", type=Path, help="Reference CFG for ID-matched displacement arrows")
    parser.add_argument("--atom-scale", type=float, default=0.7, help="OVITO global radius scaling (default: 0.7 = 70%%)")
    parser.add_argument("--arrow-width", type=float, default=0.1)
    parser.add_argument("--arrow-offset-y", type=float, default=1.0)
    parser.add_argument("--arrow-target-length", type=float, default=1.5,
                        help="Preferred median projection length in median_capped mode")
    parser.add_argument("--arrow-scale-mode", choices=ARROW_SCALE_MODES, default="physical_capped")
    parser.add_argument("--arrow-max-length", type=float, default=2.0,
                        help="Maximum 3D arrow length in input units; cannot exceed 2")
    parser.add_argument("--no-minimum-image", action="store_true", help="Use unwrapped Cartesian displacement differences")
    args = parser.parse_args()
    pbc = None
    if args.pbc:
        tokens = args.pbc.lower().replace(",", " ").split()
        if len(tokens) != 3 or any(token not in ("p", "f") for token in tokens):
            parser.error("--pbc must have three p/f tokens, e.g. 'p f p'")
        pbc = tuple(token == "p" for token in tokens)
    try:
        report = run_plan_view(args.source, args.output_dir, gb_y=args.gb_y,
                               slab_thickness=args.slab_thickness, pbc=pbc,
                               image_width=args.image_width, view_width=args.view_width,
                               reference=args.reference, atom_scale=args.atom_scale,
                               arrow_width=args.arrow_width, arrow_offset_y=args.arrow_offset_y,
                               arrow_target_length=args.arrow_target_length,
                               arrow_scale_mode=args.arrow_scale_mode, arrow_max_length=args.arrow_max_length,
                               minimum_image_convention=not args.no_minimum_image)
    except Exception as exc:
        print(json.dumps(dict(status="error", error=str(exc))))
        return 1
    print(json.dumps({key: report[key] for key in ("status", "boundary", "slab", "layers", "outputs")}, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
