"""Headless DichromaticMap adapter shared by the Structure worker and web widget.

The installed library owns the crystallography. This module validates requests,
projects its numerical results for the browser, and exports the same view using
Matplotlib's Agg canvas. Qt and the library's desktop CLI are never imported.
All plotted coordinates are in a0, with an explicit a0-to-Angstrom conversion.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


MAX_CANDIDATE_COLUMNS = 100_000


class DichromaticMapRequest(BaseModel):
    """Physical geometry and display settings, with lengths expressed in a0."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    lattice: Literal["FCC", "BCC", "SC"] = "FCC"
    axis: str | list[StrictInt] = "110"
    angle_deg: float | None = Field(default=None, ge=0, le=180)
    lattice_constant: float = Field(default=1.0, gt=0, le=1000, description="a0 in Angstrom")
    width: float = Field(default=12.0, ge=0.1, le=100)
    height: float = Field(default=9.0, ge=0.1, le=100)
    center: tuple[float, float] = (0.0, 0.0)
    marker_size: float = Field(default=32.0, ge=1, le=400, description="Marker area in points squared")
    display_rotation_deg: float = Field(default=0.0, ge=-180, le=180)
    g1_color: str = "#1677d2"
    g2_color: str = "#e35d35"
    visible_grain_layers: list[list[StrictInt]] | None = None
    show_coincidence: bool = True
    show_csl_cell: bool = True
    local_cutoff: float | None = Field(default=None, gt=0, le=0.5)

    @field_validator("center")
    @classmethod
    def _center(cls, value: tuple[float, float]) -> tuple[float, float]:
        if max(abs(coordinate) for coordinate in value) > 1_000_000:
            raise ValueError("Viewport center must be within 1,000,000 a0 of the origin")
        return value

    @field_validator("lattice", mode="before")
    @classmethod
    def _lattice(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("axis")
    @classmethod
    def _axis(cls, value: Any) -> str:
        from dichromatic_map.crystal import axis_key, parse_axis

        indices = parse_axis(value)
        if max(abs(index) for index in indices) > 12:
            raise ValueError("Web/agent projections require reduced axis indices between -12 and 12")
        return axis_key(indices)

    @field_validator("g1_color", "g2_color")
    @classmethod
    def _color(cls, value: str) -> str:
        import re

        if re.fullmatch(r"#[0-9a-fA-F]{6}", value) is None:
            raise ValueError("Grain colors must use #RRGGBB hexadecimal notation")
        return value.lower()

    @field_validator("visible_grain_layers")
    @classmethod
    def _layers(cls, value: list[list[int]] | None) -> list[list[int]] | None:
        if value is not None:
            if len(value) != 2:
                raise ValueError("Provide one layer list per grain")
            if any(any(layer < 0 for layer in group) or len(set(group)) != len(group)
                   for group in value):
                raise ValueError("Visible layers must be unique nonnegative integers")
        return value


def _geometry_data(geometry: Any) -> dict[str, Any]:
    from dichromatic_map.crystal import layer_name, misorientation_range

    limits = misorientation_range(geometry.axis, geometry.lattice)
    return {
        "lattice": geometry.lattice,
        "axis": geometry.axis,
        "axis_indices": geometry.axis_indices.tolist(),
        "axis_label": geometry.axis_label,
        "x_label": geometry.x_label,
        "y_label": geometry.y_label,
        "frame": geometry.frame.tolist(),
        "layer_count": geometry.layer_count,
        "layer_names": [layer_name(layer) for layer in range(geometry.layer_count)],
        "axial_period_a0": float(geometry.axial_period),
        "layer_spacing_a0": float(geometry.layer_spacing),
        "angle_range": {"minimum_deg": 0.0, "maximum_deg": limits.maximum_deg},
    }


def dichromatic_options(lattice: str = "FCC", axis: str | list[int] = "110") -> dict[str, Any]:
    """Return library-derived geometry, phase names and exact preset angles."""
    from dichromatic_map.crystal import csl_presets, get_geometry
    from dichromatic_map.state import default_angle_deg

    request = DichromaticMapRequest(lattice=lattice, axis=axis)
    geometry = get_geometry(request.lattice, request.axis)
    return {
        "geometry": _geometry_data(geometry),
        "presets": [dict(sigma=p.sigma, m=p.m, n=p.n, angle_deg=p.angle_deg, label=p.label)
                    for p in csl_presets(geometry.axis)],
        "defaults": request.model_dump() | {"angle_deg": default_angle_deg(geometry.axis)},
        "limits": {"max_candidate_columns": MAX_CANDIDATE_COLUMNS,
                   "max_reduced_axis_index": 12,
                   "width_a0": [0.1, 100], "height_a0": [0.1, 100]},
    }


def _candidate_count(
    geometry: Any, width: float, height: float, angle: float,
    center: np.ndarray | None = None, fit: Any = None,
) -> int:
    """Bound allocation for both grains before generating any atom arrays.

    This uses the same per-phase integer mesh rectangles as projected_columns.
    The guard counts temporary candidate columns, not just retained atoms.
    """
    from dichromatic_map.crystal import rotation_matrix_2d

    corners = np.array([[-1, -1], [-1, 1], [1, -1], [1, 1]]) * [width / 2, height / 2]
    if center is not None:
        corners += center
    count = 0
    for grain_index, sign in enumerate((1, -1)):
        rotation = rotation_matrix_2d(sign * angle / 2)
        translation = np.zeros(2)
        if fit is not None:
            rotation = (fit.cell.f1, fit.cell.f2)[grain_index] @ rotation
            translation = fit.translations[grain_index]
        inverse = np.linalg.inv(rotation @ geometry.planar_basis)
        for offset in geometry.layer_offsets_half_indices:
            projected_offset = rotation @ (offset @ geometry.frame[:, :2] / 2) + translation
            integer_corners = (corners - projected_offset) @ inverse.T
            low = np.floor(integer_corners.min(axis=0)).astype(np.int64) - 1
            high = np.ceil(integer_corners.max(axis=0)).astype(np.int64) + 1
            count += int(np.prod(high - low + 1))
            if count > MAX_CANDIDATE_COLUMNS:
                raise ValueError(
                    f"View exceeds {MAX_CANDIDATE_COLUMNS:,} candidate columns across both grains; "
                    "reduce width or height, especially for a high-index axis."
                )
    return count


def build_dichromatic_map(payload: dict[str, Any] | DichromaticMapRequest) -> dict[str, Any]:
    """Build an unstrained, same-axial-phase dichromatic diagram without Qt.

    An explicitly supplied angle is never rounded or snapped to a CSL preset.
    Display rotation and layer visibility do not alter physical cell counts.
    """
    return _build_dichromatic_map(payload)


def _build_dichromatic_map(
    payload: dict[str, Any] | DichromaticMapRequest, *, fit: Any = None,
) -> dict[str, Any]:
    from dichromatic_map.appearance import default_layer_symbols
    from dichromatic_map.crystal import get_geometry, projected_columns, rotation_matrix_2d
    from dichromatic_map.matching import (
        COINCIDENCE_TOLERANCE_FACTOR, exact_csl_cell, local_near_pairs,
        same_layer_coincidence_sites, _nearest_in_radius,
    )

    request = payload if isinstance(payload, DichromaticMapRequest) else DichromaticMapRequest.model_validate(payload)
    options = dichromatic_options(request.lattice, request.axis)
    geometry = get_geometry(request.lattice, request.axis)
    angle = request.angle_deg if request.angle_deg is not None else options["defaults"]["angle_deg"]
    maximum = options["geometry"]["angle_range"]["maximum_deg"]
    if not 0 <= angle <= maximum:
        raise ValueError(f"angle_deg must be between 0 and {maximum:g} for {geometry.lattice} {geometry.axis_label}")
    visible = request.visible_grain_layers
    if visible is None:
        visible = [list(range(geometry.layer_count)), list(range(geometry.layer_count))]
    if any(layer >= geometry.layer_count for group in visible for layer in group):
        raise ValueError(f"Visible layer indices must be less than {geometry.layer_count}")
    parameters = request.model_dump(mode="json") | {"angle_deg": angle, "axis": geometry.axis,
                                       "visible_grain_layers": visible}

    display_rotation = rotation_matrix_2d(request.display_rotation_deg)
    view_center = np.asarray(request.center)
    generation_center = display_rotation.T @ view_center
    # Generate the inverse-rotated screen rectangle's bounding box, then crop
    # after rotating. Merely rotating a pre-cropped box would leave empty corners.
    # Near-neighbor assignment needs a halo: a closer partner just outside the
    # displayed field must still prevent a spurious pair at the viewport edge.
    halo = 2 * max(request.local_cutoff or 0.0, COINCIDENCE_TOLERANCE_FACTOR)
    generation_width, generation_height = np.abs(display_rotation.T) @ [request.width + halo, request.height + halo]
    candidate_count = _candidate_count(geometry, generation_width, generation_height, angle,
                                       generation_center, fit)
    grains = [projected_columns(generation_width, generation_height, sign * angle / 2,
                                center=generation_center, lattice=geometry.lattice, axis=geometry.axis,
                                deformation=None if fit is None else (fit.cell.f1, fit.cell.f2)[index],
                                translation=None if fit is None else fit.translations[index])
              for index, sign in enumerate((1, -1))]

    def displayed(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        rotated = points @ display_rotation.T
        keep = np.all(np.abs(rotated - view_center) <= np.array([request.width, request.height]) / 2 + 1e-9, axis=1)
        return rotated[keep], keep

    colors = (request.g1_color, request.g2_color)
    symbols = default_layer_symbols(geometry.layer_count)
    traces = []
    counts = [[0] * geometry.layer_count for _ in range(2)]
    for grain_index, grain in enumerate(grains):
        for layer in visible[grain_index]:
            phase_mask = grain.layers == layer
            points, keep = displayed(grain.positions[phase_mask])
            counts[grain_index][layer] = len(points)
            traces.append({"grain": grain_index, "layer": layer,
                           "label": f"G{grain_index + 1} / {options['geometry']['layer_names'][layer]}",
                           "color": colors[grain_index], "marker": symbols[layer],
                           "x": points[:, 0].tolist(), "y": points[:, 1].tolist(),
                           "half_indices": grain.half_indices[phase_mask][keep].tolist()})

    common_layers = set(visible[0]) & set(visible[1])
    coincidences = []
    candidates = []
    # Original integer identities survive viewport movement. The browser never
    # supplies floating point endpoints to the scientific strain solver.
    endpoint_indices = [
        {tuple(point): indices.tolist() for point, indices in zip(grain.positions, grain.half_indices)}
        for grain in grains
    ]

    def add_candidate(first: np.ndarray, second: np.ndarray, layer: int, source: str) -> None:
        if fit is not None:
            return  # Applied maps are results, not a new unstrained picking session.
        half_indices = [endpoint_indices[0][tuple(first)], endpoint_indices[1][tuple(second)]]
        center = (first + second) / 2
        candidates.append({
            "id": _candidate_id(parameters, layer, half_indices), "layer": int(layer), "source": source,
            "position": (display_rotation @ center).tolist(), "position_a0": center.tolist(),
            "grain_positions_a0": [first.tolist(), second.tolist()], "grain_half_indices": half_indices,
            "distance_a0": float(np.linalg.norm(first - second)),
        })

    if request.show_coincidence:
        sites = same_layer_coincidence_sites(grains[0], grains[1], COINCIDENCE_TOLERANCE_FACTOR)
        for layer in sorted(common_layers):
            points, keep = displayed(sites[layer])
            coincidences.append({"layer": layer, "x": points[:, 0].tolist(), "y": points[:, 1].tolist()})
            if fit is None:
                centers = sites[layer][keep]
                first = grains[0].positions[grains[0].layers == layer]
                second = grains[1].positions[grains[1].layers == layer]
                first_partners = _nearest_in_radius(centers, first, COINCIDENCE_TOLERANCE_FACTOR)
                second_partners = _nearest_in_radius(centers, second, COINCIDENCE_TOLERANCE_FACTOR)
                for first_index, second_index in zip(first_partners, second_partners):
                    if first_index >= 0 and second_index >= 0:
                        add_candidate(first[first_index], second[second_index], layer, "exact")

    local = {"first": [], "second": [], "layers": [], "distances_a0": []}
    if fit is None and request.local_cutoff is not None and common_layers:
        pairs = local_near_pairs(grains[0], grains[1], request.local_cutoff,
                                 layers=sorted(common_layers), exact_tolerance=COINCIDENCE_TOLERANCE_FACTOR)
        _, keep_first = displayed(pairs.first)
        _, keep_second = displayed(pairs.second)
        keep = keep_first & keep_second
        local = {"first": (pairs.first[keep] @ display_rotation.T).tolist(),
                 "second": (pairs.second[keep] @ display_rotation.T).tolist(),
                 "layers": pairs.layers[keep].tolist(),
                 "distances_a0": pairs.distances[keep].tolist()}
        for first, second, layer in zip(pairs.first[keep], pairs.second[keep], pairs.layers[keep]):
            add_candidate(first, second, int(layer), "local")

    cell = fit.cell if fit is not None else exact_csl_cell(angle, lattice=geometry.lattice, axis=geometry.axis)
    cell_data = None
    if cell is not None:
        basis = cell.cell
        vertices = np.array([np.zeros(2), basis[:, 0], basis.sum(axis=1), basis[:, 1]])
        if fit is not None:
            vertices += fit.origin
        per_layer = [abs(int(round(np.linalg.det(matrix)))) for matrix in (cell.m1, cell.m2)]
        cell_data = {"basis_a0": basis.tolist(), "vertices": (vertices @ display_rotation.T).tolist(),
                     "vertices_a0": vertices.tolist(), "area_a0_squared": abs(float(np.linalg.det(basis))),
                     "grain_integer_matrices": [cell.m1.tolist(), cell.m2.tolist()],
                     "atom_counts": {"per_grain_per_layer": per_layer,
                                     "per_grain_full_axial_repeat": list(cell.atoms),
                                     "convention": "half-open planar cell, one full axial repeat"},
                     "kind": ("exact layer-preserving planar common cell" if fit is None else
                              "selected-pair homogeneous strained common cell"),
                     "max_strain": float(cell.max_strain)}

    warnings = [
        ("This is an ideal, unstrained lattice projection; it does not relax or reconstruct a grain boundary."
         if fit is None else "This is a homogeneous geometric strain fit, not an elastic-energy minimum or relaxed grain boundary."),
        "Coincidences and local pairs compare atoms in the same axial phase only.",
        "The common cell preserves axial layers and is not necessarily primitive in three dimensions.",
    ]
    if cell is None:
        warnings.append("No exact common cell recognized at this angle within the library's bounded rational search; no strained fit was substituted.")
    return {
        "parameters": parameters,
        "units": {"coordinates": "a0", "lattice_constant_angstrom": request.lattice_constant,
                  "marker_size": "points squared", "half_indices": "reference crystal a0/2"},
        "geometry": options["geometry"], "presets": options["presets"],
        "traces": traces, "coincidences": coincidences, "local_pairs": local,
        "pick_candidates": candidates,
        "selection_hint": "Select four distinct exact CSL or near-pair markers in one axial layer, in perimeter order around a convex cell.",
        "csl_cell": cell_data,
        "view_bounds": [request.center[0] - request.width / 2, request.center[0] + request.width / 2,
                        request.center[1] - request.height / 2, request.center[1] + request.height / 2],
        "summary": {"visible_atoms_per_grain": [sum(group) for group in counts],
                    "visible_atoms_per_grain_per_layer": counts,
                    "visible_coincidences": sum(len(group["x"]) for group in coincidences),
                    "visible_local_pairs": len(local["layers"]),
                    "exact_csl_cell_available": cell is not None and fit is None,
                    "strain_applied": fit is not None,
                    "pick_candidate_count": len(candidates),
                    "candidate_columns": candidate_count},
        "warnings": warnings,
        "software": {"name": "dichromatic-map", "version": importlib.metadata.version("dichromatic-map"),
                     "repository": "https://github.com/Yazhuo-Liu/DichromaticMap"},
    }


def _matplotlib_marker(symbol: str) -> str:
    choices = {"o": "o", "d": "D", "t": "^", "s": "s", "p": "p", "h": "h",
               "star": "*", "+": "+", "x": "x", "t1": "v", "t2": "<", "t3": ">"}
    return choices.get(symbol, f"${symbol.split(':')[-1]}$")


def _candidate_id(parameters: dict[str, Any], layer: int, half_indices: list) -> str:
    identity = {key: parameters[key] for key in ("lattice", "axis", "angle_deg", "lattice_constant")}
    identity.update(layer=int(layer), grain_half_indices=half_indices)
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "pair-" + hashlib.sha256(encoded.encode()).hexdigest()[:32]


def validate_dichromatic_selection(
    payload: dict[str, Any] | DichromaticMapRequest,
    candidate_ids: list[str], *, selection_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve four server-generated identities, including picks from older viewports.

    ``selection_candidates`` is a trusted server-side candidate pool. HTTP clients
    must supply IDs only. Reconstructing endpoints from integer reference indices
    also rejects stale/tampered stored coordinates or another physical geometry.
    Display rotation, viewport size and panning do not change candidate identity.
    This validates picking without implicitly applying a strain fit.
    """
    from dichromatic_map.cells import validate_cell_vertices
    from dichromatic_map.crystal import get_geometry, rotation_matrix_2d
    from dichromatic_map.matching import COINCIDENCE_TOLERANCE_FACTOR

    if (not isinstance(candidate_ids, list) or len(candidate_ids) != 4
            or any(not isinstance(key, str) for key in candidate_ids)
            or len(set(candidate_ids)) != 4):
        raise ValueError("Select four distinct candidate IDs in perimeter order")
    request = payload if isinstance(payload, DichromaticMapRequest) else DichromaticMapRequest.model_validate(payload)
    if selection_candidates is None:
        diagram = build_dichromatic_map(request)
        parameters, candidates = diagram["parameters"], diagram["pick_candidates"]
    else:
        options = dichromatic_options(request.lattice, request.axis)
        angle = request.angle_deg if request.angle_deg is not None else options["defaults"]["angle_deg"]
        if not 0 <= angle <= options["geometry"]["angle_range"]["maximum_deg"]:
            raise ValueError("Reference angle exceeds the geometry's allowed range")
        parameters = request.model_dump(mode="json") | {"angle_deg": angle}
        candidates = selection_candidates
    by_id = {item["id"]: item for item in candidates}
    if any(key not in by_id for key in candidate_ids):
        raise ValueError("A selected candidate is not in this selection request; refresh the diagram and pick again")
    geometry = get_geometry(request.lattice, request.axis)
    display_rotation = rotation_matrix_2d(request.display_rotation_deg)
    selected = []
    for key in candidate_ids:
        item = by_id[key]
        indices = np.asarray(item.get("grain_half_indices"))
        layer = item.get("layer")
        if (indices.shape != (2, 3) or not np.issubdtype(indices.dtype, np.integer)
                or isinstance(layer, bool) or not isinstance(layer, int)
                or not 0 <= layer < geometry.layer_count):
            raise ValueError("Candidate requires valid integer atom identities and an axial layer")
        indices_list = indices.tolist()
        if key != _candidate_id(parameters, layer, indices_list):
            raise ValueError("Candidate identity does not match this physical geometry")
        if request.visible_grain_layers is not None and any(
            layer not in group for group in request.visible_grain_layers
        ):
            raise ValueError("Selected candidates must belong to a visible layer in both grains")
        endpoints = []
        for sign, identity in zip((1, -1), indices):
            relative = identity - geometry.layer_offsets_half_indices[layer]
            coefficients = np.linalg.lstsq(geometry.basis_half_indices, relative, rcond=None)[0]
            integers = np.rint(coefficients).astype(np.int64)
            if not np.array_equal(geometry.basis_half_indices @ integers, relative):
                raise ValueError("Candidate is not an original atom in its declared axial layer")
            reference = identity @ geometry.frame[:, :2] / 2
            endpoints.append(rotation_matrix_2d(sign * parameters["angle_deg"] / 2) @ reference)
        endpoints = np.array(endpoints)
        stored = np.asarray(item.get("grain_positions_a0"), dtype=float)
        if (stored.shape != (2, 2) or not np.all(np.isfinite(stored))
                or not np.allclose(stored, endpoints, rtol=0, atol=1e-8)):
            raise ValueError("Candidate coordinates do not match its original atom identities")
        distance = float(np.linalg.norm(endpoints[0] - endpoints[1]))
        source = "exact" if distance <= COINCIDENCE_TOLERANCE_FACTOR else "local"
        if source == "local" and (request.local_cutoff is None or distance > request.local_cutoff + 1e-10):
            raise ValueError("Selected pair exceeds the requested near-CSL cutoff")
        center = endpoints.mean(axis=0)
        selected.append({"id": key, "layer": layer, "source": source,
                         "grain_half_indices": indices_list, "grain_positions_a0": endpoints.tolist(),
                         "position_a0": center.tolist(), "position": (display_rotation @ center).tolist(),
                         "distance_a0": distance})
    if len({item["layer"] for item in selected}) != 1:
        raise ValueError("All four candidates must belong to the same axial layer")
    vertices = np.stack([item["grain_positions_a0"] for item in selected], axis=1)
    for grain, polygon in enumerate(vertices):
        try:
            validate_cell_vertices(polygon)
        except ValueError as error:
            raise ValueError(f"Grain {grain + 1}: {error}") from error
    return {"candidate_ids": list(candidate_ids), "layer": selected[0]["layer"],
            "candidates": selected, "grain_vertices_a0": vertices.tolist(),
            "grain_half_indices": np.stack([item["grain_half_indices"] for item in selected], axis=1).tolist(),
            "vertices": [item["position"] for item in selected],
            "vertices_a0": [item["position_a0"] for item in selected],
            "reference_angle_deg": parameters["angle_deg"],
            "is_parallelogram": [bool(np.allclose(polygon[0] + polygon[2], polygon[1] + polygon[3],
                                                  rtol=0, atol=1e-8)) for polygon in vertices]}


def apply_dichromatic_strain(
    payload: dict[str, Any] | DichromaticMapRequest,
    candidate_ids: list[str], *, selection_candidates: list[dict[str, Any]] | None = None,
    strain_limit_percent: float = 2.0, max_rotation_deg: float = 1.0,
) -> dict[str, Any]:
    """Use DichromaticMap's desktop solver and regenerate both complete lattices."""
    from dichromatic_map.strain import strain_selected_cell, strain_tensors

    request = payload if isinstance(payload, DichromaticMapRequest) else DichromaticMapRequest.model_validate(payload)
    selection = validate_dichromatic_selection(request, candidate_ids, selection_candidates=selection_candidates)
    angle = selection["reference_angle_deg"]
    fit = strain_selected_cell(selection["grain_vertices_a0"], angle, strain_limit_percent,
                               request.lattice, request.axis, selection["layer"], max_rotation_deg)
    result = _build_dichromatic_map(request, fit=fit)
    result["selected_cell"] = selection | {"transformed_grain_vertices_a0": fit.vertices.tolist(),
                                           "common_origin_a0": fit.origin.tolist()}
    result["strain"] = {
        "method": "dichromatic_map.strain.strain_selected_cell",
        "deformation_gradients": [fit.cell.f1.tolist(), fit.cell.f2.tolist()],
        "translations_a0": fit.translations.tolist(),
        "common_basis_a0": fit.cell.cell.tolist(), "common_origin_a0": fit.origin.tolist(),
        "grain_integer_matrices": [fit.cell.m1.tolist(), fit.cell.m2.tolist()],
        "residual_a0": fit.residual, "max_principal_strain": float(fit.cell.max_strain),
        "principal_engineering_strains": (fit.stretches - 1).tolist(),
        "rotations_deg": fit.rotations_deg.tolist(),
        "green_lagrange_tensors_reference_cubic": [value.tolist() for value in strain_tensors(fit.cell, angle)],
        "strain_limit_percent": strain_limit_percent, "max_rotation_deg": max_rotation_deg,
        "reference_angle_deg": angle,
        "polar_frame_angle_deg": float(angle + fit.rotations_deg[0] - fit.rotations_deg[1]),
        "convention": "x_current = F_grain @ x_reference + translation; F uses unrotated analysis xy coordinates",
    }
    return attach_dichromatic_edge_analysis(result)


def attach_dichromatic_edge_analysis(result: dict[str, Any], edge_analysis: dict | None = None) -> dict:
    """Add finite-edge measurements and display-only projections of their arrows."""
    from dichromatic_map.crystal import rotation_matrix_2d
    from domains.structure.dichromatic.vectors import analyze_dichromatic_edge_vectors

    report = edge_analysis if edge_analysis is not None else analyze_dichromatic_edge_vectors(
        result["parameters"], result["strain"], result["selected_cell"],
    )
    rotation = rotation_matrix_2d(result["parameters"]["display_rotation_deg"])
    annotations = []
    for edge in report["edges"]:
        for index, vector in enumerate(edge["vectors"], start=1):
            endpoints = np.asarray(vector["endpoints_a0"], dtype=float)
            displayed = endpoints @ rotation.T
            annotations.append({
                "id": vector["id"], "edge_index": edge["edge_index"], "edge_label": edge["edge_label"],
                "label": f"E{edge['edge_index'] + 1}.{index}",
                "start": displayed[0].tolist(), "end": displayed[1].tolist(),
                "start_a0": endpoints[0].tolist(), "end_a0": endpoints[1].tolist(),
                "length_a0": vector["length_a0"], "length_angstrom": vector["length_angstrom"],
                "grain_notations": [item["lattice_notation"] for item in vector["grain_coordinates"]],
                "layer": vector.get("layer", report.get("layer")),
                "layer_name": vector.get("layer_name"),
                "candidate_secondary_misfit": vector.get("candidate_secondary_misfit",
                                                          edge.get("candidate_secondary_misfit", False)),
            })
    # The user's convex quadrilateral need not equal the lattice basis parallelogram.
    outline = (np.array([edge["vertices_a0"][0] for edge in report["edges"]]) @ rotation.T).tolist()
    result.update(edge_analysis=report, annotations=annotations, selected_cell_outline=outline)
    result["summary"]["edge_vector_count"] = len(annotations)
    result["summary"]["candidate_burgers_vector_count"] = sum(
        item["candidate_secondary_misfit"] for item in annotations
    )
    return result


def save_dichromatic_map(
    payload: dict[str, Any] | DichromaticMapRequest, output_dir: str | Path,
) -> dict[str, Any]:
    """Save the generated unstrained field as PNG and reproducible JSON data."""
    return save_dichromatic_result(build_dichromatic_map(payload), output_dir)


def rebuild_strained_dichromatic_view(
    payload: dict[str, Any] | DichromaticMapRequest,
    strain: dict[str, Any], selected_cell: dict[str, Any],
    *, edge_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pan/zoom a saved strain result using its transformations, without refitting.

    Both reports must come from the completed project run, not an HTTP client's
    proposed tensors. Only viewport fields in ``payload`` should be mutable.
    """
    from types import SimpleNamespace
    from dichromatic_map.cells import StrainedCell

    request = payload if isinstance(payload, DichromaticMapRequest) else DichromaticMapRequest.model_validate(payload)
    f = np.asarray(strain["deformation_gradients"], dtype=float)
    shifts = np.asarray(strain["translations_a0"], dtype=float)
    basis = np.asarray(strain["common_basis_a0"], dtype=float)
    origin = np.asarray(strain["common_origin_a0"], dtype=float)
    matrices = np.asarray(strain["grain_integer_matrices"])
    if (f.shape != (2, 2, 2) or shifts.shape != (2, 2) or basis.shape != (2, 2)
            or origin.shape != (2,) or matrices.shape != (2, 2, 2)
            or not np.issubdtype(matrices.dtype, np.integer)
            or any(not np.all(np.isfinite(array)) for array in (f, shifts, basis, origin))
            or np.any(np.linalg.det(f) <= 0)):
        raise ValueError("The saved strain transformations are invalid")
    if request.angle_deg != strain["reference_angle_deg"]:
        raise ValueError("Saved strain reference angle does not match the requested geometry")
    cell = StrainedCell(matrices[0], matrices[1], f[0], f[1], basis,
                       strain["max_principal_strain"], request.lattice, request.axis)
    fit = SimpleNamespace(cell=cell, translations=shifts, origin=origin)
    result = _build_dichromatic_map(request, fit=fit)
    result.update(strain=strain, selected_cell=selected_cell)
    if edge_analysis is not None:
        attach_dichromatic_edge_analysis(result, edge_analysis)
    return result


def _square_export_view(result: dict[str, Any]) -> dict[str, Any]:
    """Regenerate a square field around the complete selected cell, using saved F.

    Merely changing axis limits cannot recover atoms clipped by the browser's
    rectangular viewport. Reprojection also keeps JSON coordinates reproducible.
    The caller's interactive viewport and scientific result remain unchanged.
    """
    from dichromatic_map.crystal import rotation_matrix_2d

    parameters = dict(result["parameters"])
    selection = result.get("selected_cell")
    fit_selected = bool(result.get("strain") and selection)
    if fit_selected:
        vertices = np.asarray(selection["transformed_grain_vertices_a0"], dtype=float)[0]
        vertices = vertices @ rotation_matrix_2d(parameters["display_rotation_deg"]).T
        lower, upper = vertices.min(axis=0), vertices.max(axis=0)
        center = (lower + upper) / 2
        side = max(float(np.max(upper - lower)) * 1.2, 0.1)
        parameters.update(center=center.tolist(), width=side, height=side)
    else:
        side = max(parameters["width"], parameters["height"])
        parameters.update(width=side, height=side)
    if result.get("strain") and selection:
        view = rebuild_strained_dichromatic_view(
            parameters, result["strain"], selection, edge_analysis=result.get("edge_analysis"),
        )
        # Old saved strain maps may predate edge analysis; still fit their cell.
        view["selected_cell_outline"] = vertices.tolist()
    else:
        view = build_dichromatic_map(parameters)
    exported = {**result, **view}
    exported["export_view"] = {
        "aspect_ratio": 1.0, "image_aspect_ratio": 1.0,
        "fit": "selected_cell" if fit_selected else "requested_viewport",
        "padding_fraction_per_side": 0.1 if fit_selected else None,
        "source_view_bounds": list(result["view_bounds"]),
        "view_bounds": list(view["view_bounds"]),
        "center": parameters["center"], "width_a0": side, "height_a0": side,
    }
    return exported


def save_dichromatic_result(result: dict[str, Any], output_dir: str | Path) -> dict[str, Any]:
    """Export a square PNG/field fitted to the selected cell without refitting strain."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    result = _square_export_view(result)
    parameters = result["parameters"]
    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    png = directory / "dichromatic_map.png"
    report = directory / "dichromatic_map.json"
    figure = Figure(figsize=(12, 12), dpi=140, facecolor="white")
    FigureCanvasAgg(figure)
    # A square figure AND a square plotting region. Keep vector explanations in
    # a bounded four-column footer, so long legends cannot flatten the map.
    has_vectors = bool(result.get("edge_analysis"))
    axes = figure.add_axes((.11, .23, .72, .72) if has_vectors else (.11, .12, .80, .80))
    for trace in result["traces"]:
        color_options = {"edgecolors": trace["color"],
                         "facecolors": trace["color"] if trace["grain"] == 0 else "none"}
        if trace["marker"] in ("+", "x"):
            color_options = {"c": trace["color"]}
        axes.scatter(trace["x"], trace["y"], s=parameters["marker_size"], **color_options,
                     marker=_matplotlib_marker(trace["marker"]), alpha=0.8,
                     linewidths=0.8, label=trace["label"])
    for sites in result["coincidences"]:
        axes.scatter(sites["x"], sites["y"], s=parameters["marker_size"] * 1.5,
                     facecolors="none", edgecolors="#249c65", linewidths=1.1, zorder=4)
    if result["local_pairs"]["first"]:
        from matplotlib.collections import LineCollection

        segments = np.stack((result["local_pairs"]["first"], result["local_pairs"]["second"]), axis=1)
        axes.add_collection(LineCollection(segments, colors="#8b5cf6", linewidths=1.2, zorder=5))
    if parameters["show_csl_cell"] and result["csl_cell"] is not None:
        polygon = np.array(result.get("selected_cell_outline") or result["csl_cell"]["vertices"])
        polygon = np.vstack((polygon, polygon[0]))
        axes.plot(polygon[:, 0], polygon[:, 1], color="#27364b", linewidth=1.4, zorder=5)
    edge_colors = ("#a52663", "#15784b", "#8a5b12", "#6245ac")
    for annotation in result.get("annotations", []):
        start, end = np.array(annotation["start"]), np.array(annotation["end"])
        color = edge_colors[annotation["edge_index"]]
        delta = end - start
        length = float(np.linalg.norm(delta))
        axes.plot([start[0], end[0]], [start[1], end[1]], color=color, lw=1.8, zorder=7)
        if length:
            direction = delta / length
            normal = np.array([-direction[1], direction[0]])
            head_length = min(.4 * length, .012 * parameters["width"])
            base = end - direction * head_length
            head = np.array([end, base + normal * head_length * .4, base - normal * head_length * .4])
            axes.fill(head[:, 0], head[:, 1], color=color, zorder=7)
        # Only the label is offset; endpoints and vector lengths stay physical.
        axes.annotate(annotation["label"], xy=(start + end) / 2,
                      xytext=(5, 9 + 10 * (int(annotation["label"].split(".")[1]) % 2)),
                      textcoords="offset points", fontsize=7, color=color, zorder=8,
                      arrowprops={"arrowstyle": "-", "color": color, "lw": .5})
    xmin, xmax, ymin, ymax = result["view_bounds"]
    axes.set(xlim=(xmin, xmax), ylim=(ymin, ymax), xlabel="x / a0", ylabel="y / a0",
             title=f"{parameters['lattice']} [{result['geometry']['axis']}]  |  theta = {parameters['angle_deg']:.8g} degrees")
    axes.set_aspect("equal", adjustable="box")
    axes.set_box_aspect(1)
    axes.grid(alpha=0.12)
    if len(result["traces"]) <= 12:
        axes.legend(loc="upper right", fontsize=8, ncols=2)
    state_label = "selected-cell homogeneous strain" if result["summary"].get("strain_applied") else "ideal unstrained lattices"
    figure.text(0.5, 0.012,
                f"a0 = {parameters['lattice_constant']:g} Angstrom  |  {state_label}  |  same-phase coincidence rings",
                ha="center", fontsize=9)
    if has_vectors:
        import textwrap

        scope = ("minima across all axial layers" if result["edge_analysis"].get("analyzed_layers")
                 else "historical selected-layer measurements")
        figure.text(.5, .165, f"Edge vectors: G1 → G2 · actual length · {scope}",
                    ha="center", fontsize=9)
        figure.text(.5, .147, "Geometric candidates, not identified dislocations. Full vectors and atom identities in JSON.",
                    ha="center", fontsize=8)
        for edge in result["edge_analysis"]["edges"]:
            distance = edge.get("minimum_distance_a0")
            lines = [f"E{edge['edge_index'] + 1} {edge['edge_label']}: " + (
                f"{distance:.6g} a₀, {len(edge['vectors'])} pair(s)" if distance is not None else edge["status"]
            )]
            unique = dict.fromkeys((vector.get("layer_name", str(vector.get("layer", ""))),
                                   *(item["lattice_notation"] for item in vector["grain_coordinates"]))
                                  for vector in edge["vectors"])
            # Keep footer height bounded even for many layers or representations.
            for layer, first, second in list(unique)[:2]:
                for text in (f"Layer {layer}: G1 {first}", f"G2 {second}"):
                    lines.extend(textwrap.wrap(text, width=35, max_lines=2, placeholder="…"))
            if len(unique) > 2:
                lines.append(f"{len(unique) - 2} more representations in JSON.")
            if not edge.get("candidate_secondary_misfit", False) and edge.get("status") == "ok":
                lines.append("No mismatch evidence on this edge.")
            figure.text(.055 + edge["edge_index"] * .24, .128, "\n".join(lines),
                        ha="left", va="top", fontsize=7, color=edge_colors[edge["edge_index"]])
    figure.savefig(png)
    result["output_files"] = {"png": str(png), "json": str(report)}
    report.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-json", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        request = json.loads(arguments.input_json.read_text(encoding="utf-8"))
        result = save_dichromatic_map(request, arguments.output_dir)
    except (ValueError, OSError, ImportError) as error:
        parser.exit(2, f"DichromaticMap: {error}\n")
    print(json.dumps({"parameters": result["parameters"], "summary": result["summary"],
                      "output_files": result["output_files"]}, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
