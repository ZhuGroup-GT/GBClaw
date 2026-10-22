"""Full-system PTM, free-surface exclusion, and finite-domain registration."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import numpy as np
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

def extract_ptm(source: Path, output: Path, cutoff: float = .1, *, source_metadata=None) -> dict:
    from ovito.io import import_file
    from ovito.modifiers import PolyhedralTemplateMatchingModifier

    pipeline = import_file(str(source))
    from .metadata import periodic_flags
    imported_pbc = tuple(pipeline.compute().cell.pbc)
    flags, flag_sources = periodic_flags(source_metadata or {}, imported_pbc)
    def apply_boundary(frame, data):
        data.cell_.pbc = flags
    pipeline.modifiers.append(apply_boundary)
    modifier = PolyhedralTemplateMatchingModifier(
        rmsd_cutoff=cutoff, output_rmsd=True, output_orientation=True,
        output_interatomic_distance=True, output_deformation_gradient=True,
    )
    pipeline.modifiers.append(modifier)
    data = pipeline.compute()
    arrays = {
        "positions": np.asarray(data.particles.positions),
        "ids": np.asarray(data.particles["Particle Identifier"]) if "Particle Identifier" in data.particles else np.arange(1, data.particles.count+1),
        "types": np.asarray(data.particles["Structure Type"]),
        "orientations": np.asarray(data.particles["Orientation"]),
        "rmsd": np.asarray(data.particles["RMSD"]),
        "interatomic_distance": np.asarray(data.particles["Interatomic Distance"]),
        "deformation_gradients": np.asarray(data.particles["Elastic Deformation Gradient"]),
        "cell": np.asarray(data.cell), "pbc": np.asarray(data.cell.pbc),
    }
    output.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output / "ptm_atoms.npz", **arrays)
    meta = {
        "source": str(source.resolve()), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "ptm_rmsd_cutoff": cutoff, "enabled_templates": [x.name for x in modifier.structures if x.enabled],
        "particle_count": len(arrays["positions"]), "cell": arrays["cell"].tolist(),
        "pbc": arrays["pbc"].tolist(),
        "imported_pbc":list(imported_pbc), "pbc_sources":flag_sources,
        "particle_ids_from_file":"Particle Identifier" in data.particles,
        "structure_counts": {x.name: int(np.sum(arrays["types"] == x.id)) for x in modifier.structures},
        "coordinate_frame": "OVITO imported Cartesian Angstrom coordinates (CFG origin may be shifted)",
        "method_note": "PTM runs on the full input before any surface or slab filtering.",
    }
    (output / "ptm_metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def local_maxima(values: np.ndarray) -> np.ndarray:
    return np.flatnonzero((values[1:-1] > values[:-2]) & (values[1:-1] >= values[2:])) + 1


def detect_boundary(arrays, trim_fraction=.05, trim_neighbors=3.) -> tuple[dict, dict]:
    p, types = arrays["positions"], arrays["types"]
    cell_vectors = arrays["cell"][:, :3]
    if not np.allclose(cell_vectors, np.diag(np.diag(cell_vectors)), atol=1e-8):
        raise ValueError("Period analysis requires an axis-aligned orthogonal simulation cell")
    choices, counts = np.unique(types[types > 0], return_counts=True)
    if len(choices) == 0:
        raise ValueError("No PTM crystalline bulk was identified")
    bulk = int(choices[np.argmax(counts)])
    nn = float(np.median(arrays["interatomic_distance"][types == bulk]))
    if not np.isfinite(nn) or nn <= 0:
        raise ValueError("No finite positive bulk nearest-neighbor spacing")
    lo, hi = p.min(0), p.max(0)
    lengths = np.linalg.norm(arrays["cell"][:, :3], axis=0)
    vacuum = lengths - (hi-lo) > 3*nn
    core = np.ones(len(p), dtype=bool)
    margins = {}
    for axis in (0, 2):
        if vacuum[axis]:
            margin = max(trim_neighbors*nn, trim_fraction*(hi[axis]-lo[axis]))
            core &= (p[:, axis] > lo[axis]+margin) & (p[:, axis] < hi[axis]-margin)
            margins["xyz"[axis]] = margin
    core &= (p[:, 1] > lo[1]+3*nn) & (p[:, 1] < hi[1]-3*nn)
    nonbulk = types != bulk
    seeds = core & nonbulk
    if core.sum() < 30 or seeds.sum() < 2:
        raise ValueError("Insufficient interior atoms after excluding free surfaces")
    edges = np.arange(lo[1], hi[1]+.2*nn, .2*nn)
    hist = np.histogram(p[seeds, 1], edges)[0]
    hot = np.flatnonzero(hist >= max(6, .1*hist.max()))
    groups = np.split(hot, np.flatnonzero(np.diff(hot)*.2*nn > 2*nn)+1)
    bands = []
    for group in groups:
        if not len(group):
            continue
        mask = seeds & (p[:, 1] >= edges[group[0]]) & (p[:, 1] <= edges[group[-1]+1])
        coverage = np.histogram(p[mask, 0], np.linspace(p[core, 0].min(), p[core, 0].max(), 9))[0]
        bands.append((mask, float(np.mean(coverage > 0))))
    supported = [mask for mask, coverage in bands if coverage >= .75]
    if len(supported) != 1:
        raise ValueError(f"Expected one laterally supported interior PTM defect band; found {len(supported)}")
    defects = supported[0]
    y = np.sort(p[defects, 1])
    gap = int(np.argmax(np.diff(y)))
    gb_y = float((y[gap]+y[gap+1])/2)
    band = [float(y.min()), float(y.max())]
    near = core & (p[:, 1] >= band[0]-nn) & (p[:, 1] <= band[1]+nn)
    report = {
        "dominant_bulk_structure_type": bulk, "nearest_neighbor_angstrom": nn,
        "occupied_bounds_angstrom": [lo.tolist(), hi.tolist()],
        "vacuum_axes": ["xyz"[i] for i in range(3) if vacuum[i]],
        "lateral_exclusion_margins_angstrom": margins,
        "normal_exclusion_margin_angstrom": 3*nn,
        "gb_y_angstrom": gb_y, "gb_y_method": "largest interlayer gap within the unique interior PTM defect band",
        "defect_band_y_angstrom": band, "near_boundary_y_angstrom": [band[0]-nn, band[1]+nn],
        "all_nonbulk_count": int(nonbulk.sum()), "interior_defect_count": int(defects.sum()),
        "near_boundary_count_including_crystalline_neighbors": int(near.sum()),
        "interior_x_interval_angstrom": [float(p[core, 0].min()), float(p[core, 0].max())],
        "selection_meaning": "Interior non-bulk PTM atoms are interface seeds, not a complete or unique physical GB definition; neighboring crystalline atoms are retained for pattern matching.",
    }
    return report, {"core": core, "defects": defects, "near": near, "nonbulk": nonbulk}


def translation_scan(arrays, mask, lags, interval, *, reference_max_lag=None, z_shifts=None):
    """Full xyz nearest-atom residual; no x wrapping across cropped free edges."""
    from ovito.data import DataCollection, NearestNeighborFinder

    positions = arrays["positions"][mask]
    data = DataCollection()
    particles = data.create_particles()
    particles.create_property("Position", data=positions)
    data.create_cell(arrays["cell"], pbc=(False, False, bool(arrays["pbc"][2])))
    finder = NearestNeighborFinder(1, data)
    maxlag = float(max(lags) if reference_max_lag is None else reference_max_lag)
    z_shifts = np.zeros(len(lags)) if z_shifts is None else np.asarray(z_shifts)
    if z_shifts.shape != np.shape(lags):
        raise ValueError("Each x translation needs exactly one z translation")
    if max(lags) > maxlag+1e-8:
        raise ValueError("The fixed registration support must cover every requested lag")
    left = positions[(positions[:, 0] >= interval[0]) & (positions[:, 0] <= interval[1]-maxlag)]
    right = positions[(positions[:, 0] >= interval[0]+maxlag) & (positions[:, 0] <= interval[1])]
    anchors = np.concatenate((left, right))
    signs = np.r_[np.ones(len(left)), -np.ones(len(right))]
    if len(anchors) < 30:
        raise ValueError("Too few atoms for bidirectional finite-window registration")
    rms, median, p95 = [], [], []
    for start in range(0, len(lags), 32):
        batch = lags[start:start+32]
        query = np.broadcast_to(anchors, (len(batch), *anchors.shape)).copy()
        query[:, :, 0] += batch[:, None]*signs[None, :]
        query[:, :, 2] += z_shifts[start:start+32, None]*signs[None, :]
        _, vectors = finder.find_all_at(query.reshape(-1, 3))
        distances = np.linalg.norm(vectors[:, 0], axis=1).reshape(len(batch), len(anchors))
        rms.extend(np.sqrt(np.mean(distances**2, axis=1)))
        median.extend(np.median(distances, axis=1))
        p95.extend(np.quantile(distances, .95, axis=1))
    return {"lag": np.asarray(lags), "rms": np.asarray(rms), "median": np.asarray(median),
            "p95": np.asarray(p95), "reference_atom_count": len(anchors)}
