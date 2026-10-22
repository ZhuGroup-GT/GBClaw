"""OVITO-based atomic structure rendering for the Analysis worker."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING, Any, Sequence

import numpy as np

if TYPE_CHECKING:
    from ovito.pipeline import Pipeline

DEFAULT_IMAGE_SIZE = (3840, 2160)
# Top view: camera looks straight down the -Z axis (orthogonal projection set below).
DEFAULT_CAMERA_DIR = (0.0, 0.0, -1.0)
RENDER_METADATA_ATTRIBUTE = "GBclaw.RenderMetadata"
MAX_DISPLACEMENT_ARROW_LENGTH = 2.0
ARROW_SCALE_MODES = ("physical_capped", "median_capped")
# Empty border kept around the rendered content when a PNG is cropped.
CROP_MARGIN_PIXELS = 50


def validate_arrow_policy(scale_mode: str, max_length: float) -> float:
    """Bound display vectors in imported length units (angstroms for GBclaw)."""
    if scale_mode not in ARROW_SCALE_MODES:
        raise ValueError(f"arrow_scale_mode must be one of {ARROW_SCALE_MODES}")
    value = _positive(max_length, "arrow_max_length")
    if value > MAX_DISPLACEMENT_ARROW_LENGTH:
        raise ValueError("arrow_max_length must not exceed 2 imported length units")
    return value


def _positive(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def validate_particle_ids(current_ids: np.ndarray, reference_ids: np.ndarray) -> None:
    """Require an unambiguous correspondence; never fall back to row order."""
    for name, values in (("current", current_ids), ("reference", reference_ids)):
        values = np.asarray(values)
        if values.ndim != 1 or not np.issubdtype(values.dtype, np.integer):
            raise ValueError(f"{name} Particle Identifier must be a one-dimensional integer property")
        if len(np.unique(values)) != len(values):
            raise ValueError(f"{name} Particle Identifier contains duplicate IDs")
    if not np.array_equal(np.sort(current_ids), np.sort(reference_ids)):
        raise ValueError("Current and reference CFG files must contain exactly the same atom IDs")


def summarize_displacements(vectors: np.ndarray, selected: np.ndarray, particle_ids: np.ndarray,
                            *, target_length: float = 1.5, zero_tolerance: float = 1e-8,
                            arrow_width: float = 0.1, arrow_offset_y: float = 1.0,
                            projection_axes: tuple[int, int] = (0, 2), selection_name: str = "slab",
                            arrow_offset: Sequence[float] | None = None,
                            scale_mode: str = "physical_capped", max_length: float = 2.0) -> dict[str, Any]:
    """Record physical vectors and a separate, uniform display scaling factor.

    By default small vectors are not magnified. Both display modes cap the
    three-dimensional length of every displayed vector. Physical vectors are unchanged.
    Defaults preserve the GB plan-view convention (XZ, selected slab, +Y offset).
    """
    vectors = np.asarray(vectors, dtype=float)
    selected = np.asarray(selected, dtype=bool)
    particle_ids = np.asarray(particle_ids)
    target_length = _positive(target_length, "arrow_target_length")
    max_length = validate_arrow_policy(scale_mode, max_length)
    arrow_width = _positive(arrow_width, "arrow_width")
    zero_tolerance = _positive(zero_tolerance, "zero_tolerance")
    if not math.isfinite(arrow_offset_y):
        raise ValueError("arrow_offset_y must be finite")
    if len(projection_axes) != 2 or len(set(projection_axes)) != 2 or any(axis not in (0, 1, 2) for axis in projection_axes):
        raise ValueError("projection_axes must specify two distinct Cartesian axes")
    projection = "".join("xyz"[axis] for axis in projection_axes)
    offset = _normalize_triplet(arrow_offset if arrow_offset is not None else (0, arrow_offset_y, 0), label="arrow_offset")
    if vectors.ndim != 2 or vectors.shape[1] != 3 or not np.isfinite(vectors).all():
        raise ValueError("Displacements must be finite three-dimensional vectors")
    if selected.shape != (len(vectors),) or particle_ids.shape != selected.shape or not selected.any():
        raise ValueError("Displacements require matching IDs and a nonempty slab selection")
    magnitudes = np.linalg.norm(vectors, axis=1)
    projected = np.linalg.norm(vectors[:, projection_axes], axis=1)
    if not np.isfinite(magnitudes).all() or not np.isfinite(projected).all():
        raise ValueError("Displacement magnitudes must be finite")
    visible = projected[selected]
    nonzero = visible[visible > zero_tolerance]
    representative = float(np.median(nonzero)) if len(nonzero) else None
    preferred = (target_length / representative
                 if scale_mode == "median_capped" and representative is not None else 1.0)
    physical_max = float(magnitudes[selected].max())
    scaling = min(preferred, max_length / physical_max) if physical_max > 0 else 1.0
    if not math.isfinite(scaling):
        raise ValueError("Displacement scaling is not finite")
    display_max = physical_max * scaling
    if display_max > max_length + 1e-12:
        raise ValueError("Displacement arrow length exceeds the display limit")

    def statistics(mask):
        norms = magnitudes[mask]
        projected_norms = projected[mask]
        return dict(count=len(norms), magnitude_min=float(norms.min()),
                    magnitude_median=float(np.median(norms)), magnitude_max=float(norms.max()),
                    **{f"projected_{projection}_median": float(np.median(projected_norms)),
                       f"projected_{projection}_max": float(projected_norms.max())})

    return dict(
        matching="particle_identifier", definition="current_minus_reference", affine_mapping="off",
        summary={"all": statistics(np.ones(len(vectors), dtype=bool)), selection_name: statistics(selected)},
        render=dict(scaling=scaling, target_length=target_length,
                    scale_mode=scale_mode, max_length=max_length,
                    hard_max_length=MAX_DISPLACEMENT_ARROW_LENGTH, length_unit="input",
                    physical_max_3d=physical_max, display_max_3d=display_max,
                    cap_applied=scaling < preferred, algorithm_version=2,
                    normalization=scale_mode, representative_projected_length=representative,
                    nonzero_projected_count=len(nonzero), zero_tolerance=zero_tolerance,
                    scaled_projected_median=(representative * scaling if representative is not None else 0.0),
                    scaled_projected_max=float(visible.max() * scaling),
                    width=arrow_width, offset=list(offset), alignment="center",
                    color=[0.85, 0.12, 0.08], vectors_are_normalized_individually=False),
        selected=dict(particle_ids=particle_ids[selected].tolist(), vectors=vectors[selected].tolist(),
                      magnitudes=magnitudes[selected].tolist()),
    )


def _normalize_triplet(value: Sequence[float], *, label: str) -> tuple[float, float, float]:
    if len(value) != 3:
        raise ValueError(f"{label} must have three components")
    result = float(value[0]), float(value[1]), float(value[2])
    if not all(math.isfinite(component) for component in result):
        raise ValueError(f"{label} must be finite")
    return result


def _prepare_ovito() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("XDG_CACHE_HOME", str(Path(tempfile.gettempdir()) / "gbclaw-render-cache"))


def structure_file_info(path: Path, data: Any, imported_pbc: Sequence[bool]) -> dict[str, Any]:
    return dict(path=str(path.resolve()), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                particle_count=data.particles.count, cell=np.asarray(data.cell).tolist(),
                imported_pbc=list(imported_pbc), effective_pbc=list(data.cell.pbc))


def add_displacement_modifier(pipeline: Any, reference: Path | str, *,
                              pbc: tuple[bool, bool, bool] | None = None,
                              minimum_image_convention: bool = True) -> tuple[Any, dict[str, Any], list[str]]:
    """Calculate ID-matched vectors on a complete structure, before hiding atoms."""
    _prepare_ovito()
    from ovito.io import import_file
    from ovito.modifiers import CalculateDisplacementsModifier
    from ovito.pipeline import ReferenceConfigurationModifier, StaticSource

    reference = Path(reference).expanduser().resolve()
    if reference.suffix.lower() != ".cfg" or not reference.is_file():
        raise ValueError("reference must be an existing CFG file")
    reference_pipeline = import_file(str(reference))
    if reference_pipeline.source.num_frames != 1:
        raise ValueError("Select one reference CFG frame before rendering")
    reference_data = reference_pipeline.compute()
    current = pipeline.compute()
    reference_imported_pbc = list(reference_data.cell.pbc)
    if pbc is not None:
        reference_data.cell_.pbc = tuple(pbc)
    elif tuple(reference_data.cell.pbc) != tuple(current.cell.pbc):
        raise ValueError("Current and reference PBC differ; supply explicit pbc flags")
    if "Particle Identifier" not in current.particles or "Particle Identifier" not in reference_data.particles:
        raise ValueError("Both CFG files need Particle Identifier for displacement matching; row-order matching is not used")
    validate_particle_ids(np.asarray(current.particles["Particle Identifier"]),
                          np.asarray(reference_data.particles["Particle Identifier"]))
    modifier = CalculateDisplacementsModifier(
        reference=StaticSource(data=reference_data), minimum_image_convention=minimum_image_convention,
        affine_mapping=ReferenceConfigurationModifier.AffineMapping.Off)
    pipeline.modifiers.append(modifier)
    warnings = ["Displacements are current minus reference, including rigid translation and affine deformation; no drift or strain is subtracted. Display scaling does not change the physical vectors."]
    if minimum_image_convention:
        warnings.append("Minimum-image displacement cannot recover motion exceeding half a periodic box between these frames; use unwrapped coordinates and disable minimum_image_convention for such motion.")
    if not np.allclose(np.asarray(reference_data.cell), np.asarray(current.cell), rtol=1e-8, atol=1e-8):
        warnings.append("Current and reference cell geometry/origin differ; imported Cartesian differences are used without affine cell mapping.")
    return modifier, structure_file_info(reference, reference_data, reference_imported_pbc), warnings


def configure_displacement_display(modifier: Any, displacement: dict[str, Any]) -> None:
    from ovito.vis import VectorVis

    style = displacement["render"]
    modifier.vis.enabled = True
    modifier.vis.alignment = VectorVis.Alignment.Center
    modifier.vis.offset = tuple(style["offset"])
    modifier.vis.width = style["width"]
    modifier.vis.scaling = style["scaling"]
    modifier.vis.color = tuple(style["color"])


def build_render_pipeline(
    cfg_path: Path | str,
    *,
    analysis_mode: str = "CNA",
    hide_structure_types: Sequence[int] | None = None,
    dxa_lattice: str = "bcc",
    reference: Path | str | None = None,
    arrow_width: float = 0.1,
    arrow_offset_z: float = 1.0,
    arrow_target_length: float = 1.5,
    arrow_scale_mode: str = "physical_capped",
    arrow_max_length: float = 2.0,
    minimum_image_convention: bool = True,
    pbc: tuple[bool, bool, bool] | None = None,
) -> Pipeline:
    """Build a top-view pipeline; supplying a reference enables displacement arrows.

    Calculate physical displacements before analysis/hiding, then cap the 3D
    arrows of displayed atoms with one uniform scale.
    Without a reference, the existing structure-only visualization is retained.
    """
    _prepare_ovito()
    from ovito.io import import_file
    from ovito.modifiers import (
        CommonNeighborAnalysisModifier, DeleteSelectedModifier,
        DislocationAnalysisModifier, ExpressionSelectionModifier,
    )
    from ovito.vis import DislocationVis

    path = Path(cfg_path).expanduser().resolve()
    if path.suffix.lower() != ".cfg":
        raise ValueError(f"Expected a .cfg file, got: {path.name}")
    if not path.is_file():
        raise FileNotFoundError(f"Structure file not found: {path}")

    mode = analysis_mode.strip().upper()
    if mode not in {"CNA", "DXA", "NONE"}:
        raise ValueError(f"analysis_mode must be CNA, DXA, or none (got {analysis_mode!r})")
    _positive(arrow_width, "arrow_width")
    _positive(arrow_target_length, "arrow_target_length")
    validate_arrow_policy(arrow_scale_mode, arrow_max_length)
    if not math.isfinite(arrow_offset_z):
        raise ValueError("arrow_offset_z must be finite")
    if not isinstance(minimum_image_convention, bool):
        raise ValueError("minimum_image_convention must be a boolean")
    if pbc is not None and (len(pbc) != 3 or not all(isinstance(flag, (bool, np.bool_)) for flag in pbc)):
        raise ValueError("pbc must contain three booleans")

    pipeline = import_file(str(path))
    if pipeline.source.num_frames != 1:
        raise ValueError("Select one CFG frame before rendering")
    imported = pipeline.compute()
    imported_pbc = list(imported.cell.pbc)
    if pbc is not None:
        def set_pbc(frame, data):
            data.cell_.pbc = tuple(pbc)
        pipeline.modifiers.append(set_pbc)
    source = structure_file_info(path, pipeline.compute(), imported_pbc)
    source["pbc_source"] = "user_supplied" if pbc is not None else "OVITO_import"
    report = dict(status="completed", source=source, analysis_mode=mode,
                  hide_structure_types=list(hide_structure_types or []), dxa_lattice=dxa_lattice,
                  camera=dict(direction=list(DEFAULT_CAMERA_DIR), projection="xy"),
                  displacement=None, warnings=[])
    displacement_modifier = None
    if reference is not None:
        displacement_modifier, reference_info, warnings = add_displacement_modifier(
            pipeline, reference, pbc=pbc, minimum_image_convention=minimum_image_convention)
        full = pipeline.compute()
        full_ids = np.array(full.particles["Particle Identifier"])
        full_vectors = np.array(full.particles["Displacement"])
        report["warnings"].extend(warnings)
        report["warnings"].append("CFG periodicity follows OVITO import unless pbc is supplied; explicit pbc applies to both structures.")

    if mode == "CNA":
        cna_modifier = CommonNeighborAnalysisModifier()
        cna_modifier.mode = CommonNeighborAnalysisModifier.Mode.AdaptiveCutoff
        pipeline.modifiers.append(cna_modifier)
    elif mode == "DXA":
        pipeline.source.data.particles.vis.radius *= 0.5

        disloc_vis = DislocationVis()
        disloc_vis.show_burgers_vectors = True
        disloc_vis.line_width = 1.5
        disloc_vis.burgers_vector_color = (1.0, 0.0, 0.0)
        disloc_vis.burgers_vector_scaling = 6
        disloc_vis.burgers_vector_width = 1.5

        lattice_key = dxa_lattice.strip().lower()
        lattices = {
            "bcc": DislocationAnalysisModifier.Lattice.BCC,
            "fcc": DislocationAnalysisModifier.Lattice.FCC,
            "hcp": DislocationAnalysisModifier.Lattice.HCP,
        }
        if lattice_key not in lattices:
            raise ValueError(f"dxa_lattice must be one of {sorted(lattices)}")
        dxa_modifier = DislocationAnalysisModifier(disloc_vis=disloc_vis)
        dxa_modifier.defect_vis.enabled = False
        dxa_modifier.input_crystal_structure = lattices[lattice_key]
        pipeline.modifiers.append(dxa_modifier)

    if hide_structure_types:
        type_expr = " || ".join(f"StructureType == {value}" for value in hide_structure_types)
        pipeline.modifiers.append(ExpressionSelectionModifier(expression=type_expr))
        pipeline.modifiers.append(DeleteSelectedModifier())

    if displacement_modifier is not None:
        displayed = pipeline.compute()
        if not displayed.particles.count:
            raise ValueError("No atoms remain for displacement rendering after hiding structure types")
        selected = np.isin(full_ids, np.asarray(displayed.particles["Particle Identifier"]))
        displacement = summarize_displacements(
            full_vectors, selected, full_ids, target_length=arrow_target_length,
            arrow_width=arrow_width, arrow_offset=(0, 0, arrow_offset_z),
            projection_axes=(0, 1), selection_name="displayed",
            scale_mode=arrow_scale_mode, max_length=arrow_max_length)
        displacement.pop("selected")  # Full arrays go in the sidecar, not pipeline attributes.
        displacement["reference"] = reference_info
        displacement["minimum_image_convention"] = minimum_image_convention
        configure_displacement_display(displacement_modifier, displacement)
        report["displacement"] = displacement
        if not displacement["render"]["nonzero_projected_count"]:
            report["warnings"].append("No nonzero XY displacement is visible. Pure Z displacement is along the viewing direction; the 3D display limit still applies.")
    encoded_report = json.dumps(report, allow_nan=False)

    def record_metadata(frame, data):
        data.attributes[RENDER_METADATA_ATTRIBUTE] = encoded_report
    pipeline.modifiers.append(record_metadata)
    return pipeline


def render_pipeline_image(
    pipeline: Pipeline,
    output_path: Path | str,
    *,
    image_size: tuple[int, int] = DEFAULT_IMAGE_SIZE,
    camera_dir: Sequence[float] = DEFAULT_CAMERA_DIR,
    show_tripod: bool = False,
    zoom_factor: float = 1.0,
) -> Path:
    """Render *pipeline* to a PNG file and return the output path."""
    from ovito.qt_compat import QtCore
    from ovito.vis import CoordinateTripodOverlay, TachyonRenderer, Viewport

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    viewport = Viewport()
    viewport.type = Viewport.Type.Ortho
    viewport.camera_dir = _normalize_triplet(camera_dir, label="camera_dir")

    if show_tripod:
        tripod = CoordinateTripodOverlay()
        tripod.alignment = QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignBottom
        viewport.overlays.append(tripod)

    renderer = TachyonRenderer(
        ambient_occlusion=False,
        direct_light=True,
        direct_light_intensity=0.9,
        shadows=False,
        antialiasing=True,
        antialiasing_samples=12,
    )
    pipeline.add_to_scene()
    try:
        viewport.zoom_all(size=image_size)
        viewport.fov *= zoom_factor
        viewport.render_image(
            filename=str(target),
            size=image_size,
            renderer=renderer,
            alpha=True,
        )
    finally:
        pipeline.remove_from_scene()
    return target


def crop_to_content(path: Path | str, *, margin: int = CROP_MARGIN_PIXELS) -> dict[str, Any]:
    """Crop a rendered PNG to its non-empty pixels with an empty ``margin`` on each side.

    Empty means transparent, or for an opaque image the colour of its top-left
    pixel; the margin is filled the same way, so it is exact even where content
    reached the render's edge. An image with no content is left as it is.
    ``crop_box`` is the content box plus margin in rendered-image pixels.
    """
    from PIL import Image, ImageChops

    target = Path(path)
    with Image.open(target) as image:
        image.load()
        rendered = image.size
        transparent = "A" in image.getbands()
        empty = (0,) * len(image.getbands()) if transparent else image.getpixel((0, 0))
        if transparent:
            content = image.getchannel("A").point(lambda value: 255 if value else 0).getbbox()
        else:
            content = ImageChops.difference(image, Image.new(image.mode, rendered, empty)).getbbox()
        if content is None:
            return dict(rendered_image_size=list(rendered), crop_box=[0, 0, *rendered],
                        image_size=list(rendered), crop_margin_pixels=0)
        left, top, right, bottom = content
        canvas = Image.new(image.mode, (right - left + 2 * margin, bottom - top + 2 * margin), empty)
        canvas.paste(image.crop(content), (margin, margin))
    temporary = target.with_name(f".{target.name}.crop.png")
    canvas.save(temporary, format="PNG")
    os.replace(temporary, target)
    return dict(rendered_image_size=list(rendered),
                crop_box=[left - margin, top - margin, right + margin, bottom + margin],
                image_size=list(canvas.size), crop_margin_pixels=margin)


def render_atomic_structure_file(
    cfg_path: Path | str,
    output_path: Path | str,
    *,
    analysis_mode: str = "CNA",
    hide_structure_types: Sequence[int] | None = None,
    dxa_lattice: str = "bcc",
    show_tripod: bool = False,
    image_size: tuple[int, int] = DEFAULT_IMAGE_SIZE,
    reference: Path | str | None = None,
    arrow_width: float = 0.1,
    arrow_offset_z: float = 1.0,
    arrow_target_length: float = 1.5,
    arrow_scale_mode: str = "physical_capped",
    arrow_max_length: float = 2.0,
    minimum_image_convention: bool = True,
    pbc: tuple[bool, bool, bool] | None = None,
) -> Path:
    """Write a PNG and a JSON sidecar, optionally including physical displacements."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    report_path = target.with_suffix(".json")
    report = dict(status="failed", source_path=str(Path(cfg_path).resolve()),
                  reference_path=str(Path(reference).resolve()) if reference is not None else None)
    try:
        pipeline = build_render_pipeline(
            cfg_path, analysis_mode=analysis_mode, hide_structure_types=hide_structure_types,
            dxa_lattice=dxa_lattice, reference=reference,
            arrow_width=arrow_width, arrow_offset_z=arrow_offset_z,
            arrow_target_length=arrow_target_length,
            arrow_scale_mode=arrow_scale_mode, arrow_max_length=arrow_max_length,
            minimum_image_convention=minimum_image_convention, pbc=pbc)
        data = pipeline.compute()
        report = json.loads(data.attributes[RENDER_METADATA_ATTRIBUTE])
        report["rendered_particle_count"] = data.particles.count
        report["camera"]["image_size"] = list(image_size)
        report["outputs"] = dict(render=target.name, data=report_path.name)
        if report["displacement"] is not None:
            report["displacement"]["selected"] = dict(
                particle_ids=np.asarray(data.particles["Particle Identifier"]).tolist(),
                vectors=np.asarray(data.particles["Displacement"]).tolist(),
                magnitudes=np.asarray(data.particles["Displacement Magnitude"]).tolist())
        render_pipeline_image(pipeline, target, image_size=image_size, show_tripod=show_tripod)
        report["camera"].update(crop_to_content(target))
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        raise
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return target


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Render an OVITO cfg structure to PNG.")
    parser.add_argument("cfg", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=Path("structure_render.png"))
    parser.add_argument("--mode", default="CNA", choices=["CNA", "DXA", "none"])
    parser.add_argument("--hide-types", type=int, nargs="*", default=None)
    parser.add_argument("--dxa-lattice", default="bcc", choices=["bcc", "fcc", "hcp"])
    parser.add_argument("--show-tripod", action="store_true")
    parser.add_argument("--image-size", type=int, nargs=2, default=list(DEFAULT_IMAGE_SIZE))
    parser.add_argument("--reference", type=Path, help="Optional reference CFG enabling displacement arrows")
    parser.add_argument("--arrow-width", type=float, default=0.1)
    parser.add_argument("--arrow-offset-z", type=float, default=1.0)
    parser.add_argument("--arrow-target-length", type=float, default=1.5,
                        help="Preferred median projection length in median_capped mode")
    parser.add_argument("--arrow-scale-mode", choices=ARROW_SCALE_MODES, default="physical_capped")
    parser.add_argument("--arrow-max-length", type=float, default=2.0,
                        help="Maximum 3D arrow length in input units; cannot exceed 2")
    parser.add_argument("--no-minimum-image", action="store_true", help="Use unwrapped Cartesian displacement differences")
    parser.add_argument("--pbc", help="Three boundary flags, for example 'p f p'; default is OVITO import")
    args = parser.parse_args()
    pbc = None
    if args.pbc:
        tokens = args.pbc.lower().replace(",", " ").split()
        if len(tokens) != 3 or any(token not in ("p", "f") for token in tokens):
            parser.error("--pbc must have three p/f tokens, e.g. 'p f p'")
        pbc = tuple(token == "p" for token in tokens)

    try:
        render_atomic_structure_file(
            args.cfg, args.output, analysis_mode=args.mode,
            hide_structure_types=args.hide_types, dxa_lattice=args.dxa_lattice,
            show_tripod=args.show_tripod, image_size=tuple(args.image_size),
            reference=args.reference, arrow_width=args.arrow_width,
            arrow_offset_z=args.arrow_offset_z, arrow_target_length=args.arrow_target_length,
            arrow_scale_mode=args.arrow_scale_mode, arrow_max_length=args.arrow_max_length,
            minimum_image_convention=not args.no_minimum_image, pbc=pbc)
    except Exception as exc:
        print(json.dumps(dict(status="failed", error=str(exc))))
        return 1
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main())
