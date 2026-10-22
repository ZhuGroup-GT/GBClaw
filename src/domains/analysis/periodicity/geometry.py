"""Coordinate evidence for grain-boundary periodicity candidates.

No grain repeat counts or orientation hints are used here. Registration keeps
all three coordinates, uses fixed bidirectional anchors, and never wraps x/y.
The density correlation currently requires an orthogonal cell with periodic z.
"""
from __future__ import annotations

import numpy as np

from .ptm import translation_scan


def in_plane_acf(arrays, mask, interval):
    """Linear x, periodic z, separate y channels; sum power, not amplitudes.

    Transform one y channel at a time so a longer sample does not require a
    large complex 3D FFT allocation. No artificial wrapping across x edges.
    """
    p = arrays["positions"][mask]
    if not arrays["pbc"][2]:
        raise ValueError("In-plane correlation requires a periodic, vacuum-free z axis")
    cell_vectors = np.asarray(arrays["cell"])[:, :3]
    if not np.allclose(cell_vectors, np.diag(np.diag(cell_vectors)), atol=1e-8):
        raise ValueError("In-plane correlation requires an axis-aligned orthogonal cell")
    if len(p) == 0 or interval[1] <= interval[0]:
        raise ValueError("In-plane correlation needs atoms and a nonempty x interval")
    nx = int(np.ceil((interval[1]-interval[0])/.15))
    if nx < 4:
        raise ValueError("The x interval is too short for density correlation")
    xb = np.linspace(*interval, nx+1)
    yb = np.arange(p[:, 1].min()-.3, p[:, 1].max()+.6, .3)
    zlo, length = arrays["cell"][2, 3], arrays["cell"][2, 2]
    if length <= 0:
        raise ValueError("The periodic z cell length must be positive")
    zb = np.linspace(zlo, zlo+length, int(np.ceil(length/.15))+1)
    nz = len(zb)-1
    density = np.histogramdd(p, bins=(xb, yb, zb))[0]
    for axis in range(3):
        previous, following = np.roll(density, 1, axis), np.roll(density, -1, axis)
        if axis != 2:
            idx = [slice(None)]*3
            idx[axis] = 0
            previous[tuple(idx)] = 0
            idx[axis] = -1
            following[tuple(idx)] = 0
        density = .25*previous+.5*density+.25*following
    density -= density.mean(axis=(0, 2), keepdims=True)
    nfft = 2**int(np.ceil(np.log2(2*nx)))
    power = np.zeros((nfft, nz//2+1))
    for j in range(density.shape[1]):
        ft = np.fft.rfft2(density[:, j, :], s=(nfft, nz))
        power += abs(ft)**2
    correlation = np.fft.irfft2(power, s=(nfft, nz))[:nx//2]
    energy = np.sum(density*density, axis=(1, 2))
    prefix = np.r_[0., np.cumsum(energy)]
    k = np.arange(len(correlation))
    correlation /= np.maximum(np.sqrt(prefix[nx-k]*(prefix[nx]-prefix[k])), 1e-30)[:, None]
    return {
        "x_lag": k*(xb[1]-xb[0]),
        "z_lag": np.fft.fftshift(np.fft.fftfreq(nz))*length,
        "correlation": np.fft.fftshift(correlation, axes=1),
        "grid_spacing": np.array([xb[1]-xb[0], yb[1]-yb[0], zb[1]-zb[0]]),
    }


def refine_translation(arrays, mask, x, interval, support, radius, step):
    """Refine a pure-x candidate without changing its reference atom support.

    The returned step precision is numerical sampling, not a physical error bar.
    """
    if not np.all(np.isfinite([x, support, radius, step])):
        raise ValueError("Translation search parameters must be finite")
    if x < 0 or support <= 0 or x > support or radius < 0 or step <= 0:
        raise ValueError("Require 0 <= x <= support, support > 0, radius >= 0 and step > 0")
    low, high = max(0., x-radius), min(support, x+radius)
    lags = np.unique(np.r_[np.arange(low, high+step*.01, step), x])
    lags = lags[(lags >= low) & (lags <= high)]
    curve = translation_scan(arrays, mask, lags, interval, reference_max_lag=support)
    i = int(np.argmin(curve["rms"]))
    return {
        "x_angstrom": float(lags[i]),
        "rms": float(curve["rms"][i]),
        "median": float(curve["median"][i]),
        "p95": float(curve["p95"][i]),
        "reference_atom_count": int(curve["reference_atom_count"]),
    }


def registration_evidence(arrays, mask, shifts, interval, support, nn):
    """Measure every [dx,dz] shift on the same bidirectional xyz anchors.

    ``dx`` must be nonnegative and no larger than ``support``. Left anchors
    receive +[dx,0,dz], right anchors receive -[dx,0,dz]. Their union (including
    a repeated atom where both directions are supported) is identical for all
    candidates. Only z inherits input periodicity; x and y never wrap.

    Coverage is the fraction of these directed queries with nearest-atom
    distance <= 0.15*nn. It measures geometric matching, not a bijection or a
    confidence probability. The cutoff is returned explicitly with each item.
    """
    from ovito.data import DataCollection, NearestNeighborFinder

    shifts = np.asarray(shifts, dtype=float)
    if shifts.size == 0:
        return []
    interval = np.asarray(interval, dtype=float)
    if shifts.ndim != 2 or shifts.shape[1] != 2 or not np.isfinite(shifts).all():
        raise ValueError("Shifts must be a finite Nx2 array of [dx,dz]")
    if interval.shape != (2,) or not np.isfinite(interval).all() or interval[1] <= interval[0]:
        raise ValueError("Registration requires a finite increasing x interval")
    if not np.isfinite(support) or support <= 0 or not np.isfinite(nn) or nn <= 0:
        raise ValueError("Registration support and nearest-neighbor distance must be positive")
    if np.any(shifts[:, 0] < 0) or np.any(shifts[:, 0] > support+1e-8):
        raise ValueError("The fixed registration support must cover every nonnegative x shift")
    positions = np.asarray(arrays["positions"], dtype=float)[mask]
    if positions.ndim != 2 or positions.shape[1] != 3 or not np.isfinite(positions).all():
        raise ValueError("Registration requires finite xyz coordinates")
    left = positions[(positions[:, 0] >= interval[0]) & (positions[:, 0] <= interval[1]-support)]
    right = positions[(positions[:, 0] >= interval[0]+support) & (positions[:, 0] <= interval[1])]
    anchors = np.concatenate((left, right))
    if len(anchors) < 30:
        raise ValueError("Too few atoms for bidirectional finite-window registration")
    signs = np.r_[np.ones(len(left)), -np.ones(len(right))]
    data = DataCollection()
    data.create_particles().create_property("Position", data=positions)
    data.create_cell(arrays["cell"], pbc=(False, False, bool(arrays["pbc"][2])))
    finder = NearestNeighborFinder(1, data)
    results = []
    cutoff = .15*float(nn)
    for start in range(0, len(shifts), 32):
        batch = shifts[start:start+32]
        query = np.broadcast_to(anchors, (len(batch), *anchors.shape)).copy()
        query[:, :, 0] += batch[:, 0, None]*signs[None, :]
        query[:, :, 2] += batch[:, 1, None]*signs[None, :]
        indices, vectors = finder.find_all_at(query.reshape(-1, 3))
        if np.any(indices < 0):
            raise ValueError("Registration could not find a target for every query")
        distances = np.linalg.norm(vectors[:, 0], axis=1).reshape(len(batch), len(anchors))
        for shift, d in zip(batch, distances):
            rms = float(np.sqrt(np.mean(d*d)))
            results.append({
                "x_angstrom": float(shift[0]), "z_angstrom": float(shift[1]),
                "rms": rms, "median": float(np.median(d)),
                "p95": float(np.quantile(d, .95)), "rms_over_nn": rms/nn,
                "coverage_fraction": float(np.mean(d <= cutoff)),
                "coverage_cutoff_angstrom": cutoff,
                "reference_atom_count": int(len(anchors)),
            })
    return results
