"""Evidence-first GB period candidates; no repeat-count prior.

Supported geometry:
one approximately planar boundary normal to y in an orthogonal box, x as the
search direction, periodic vacuum-free z. FCC/BCC crystal indexing is optional.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
import time
from pathlib import Path

import numpy as np

from .ptm import detect_boundary, extract_ptm, local_maxima, translation_scan
from .crystal_mapping import analyze_crystal_mapping
from .geometry import in_plane_acf, refine_translation, registration_evidence
from .modulation import analyze_modulation
from .metadata import normalize_metadata, periodic_flags, attach_source_convention, annotate_counts_and_mismatch
from .plotting import plot_selection, plot_in_plane, plot_modulation


@dataclass(frozen=True)
class CandidatePolicy:
    ptm_cutoff: float = .1
    minimum_lag_in_nn: float = 2.5
    scan_step_in_nn: float = .06
    refinement_step_in_nn: float = .002
    max_pair_mismatch: float = .05
    shortlist_pair_mismatch: float = .02
    max_primitive_count: int = 512
    shortlist_rms_in_nn: float = .15
    minimum_repeat_cycles: float = 3.
    minimum_long_scale_ratio: float = 3.
    maximum_bulk_transverse_in_nn: float = .02
    max_seeds_per_method: int = 12
    x_window: tuple[float, float] | None = None

    def validate(self):
        for key in ("ptm_cutoff", "minimum_lag_in_nn", "scan_step_in_nn", "refinement_step_in_nn",
                    "max_pair_mismatch", "shortlist_pair_mismatch", "shortlist_rms_in_nn", "maximum_bulk_transverse_in_nn"):
            if not np.isfinite(getattr(self, key)) or getattr(self, key) <= 0:
                raise ValueError(f"{key} must be positive and finite")
        if self.minimum_repeat_cycles < 2 or not np.isfinite(self.minimum_repeat_cycles):
            raise ValueError("Need at least two observed repeat cycles")
        if self.minimum_long_scale_ratio <= 1 or not np.isfinite(self.minimum_long_scale_ratio):
            raise ValueError("Long scale separation must be finite and exceed one")
        if min(self.max_primitive_count, self.max_seeds_per_method) < 1:
            raise ValueError("Candidate count limits must be positive")
        if self.shortlist_pair_mismatch > self.max_pair_mismatch:
            raise ValueError("Shortlist mismatch cannot exceed enumeration mismatch")
        if self.x_window is not None and (len(self.x_window) != 2 or not np.all(np.isfinite(self.x_window)) or self.x_window[1] <= self.x_window[0]):
            raise ValueError("x_window needs two ordered finite coordinates")


def near_coincidence_pairs(upper_t, lower_t, minimum, maximum, policy):
    """Integer approximants from measured bulk translations, without an answer.

    Retain the length/mismatch Pareto frontier. Reducing an integer pair only
    affects these lattice seeds, never deletes structural supercell candidates.
    """
    if min(upper_t, lower_t) <= 0 or not np.all(np.isfinite([upper_t, lower_t])):
        raise ValueError("Crystal translations must be positive and finite")
    pairs = []
    for m in range(1, min(policy.max_primitive_count, int(maximum/upper_t)+2)+1):
        predicted_n = m*upper_t/lower_t
        for n in sorted(set((math.floor(predicted_n), math.ceil(predicted_n)))):
            if not 1 <= n <= policy.max_primitive_count or math.gcd(m, n) != 1:
                continue
            lengths = [m*upper_t, n*lower_t]
            length = float(np.mean(lengths))
            mismatch = abs(lengths[0]-lengths[1])/length
            if minimum <= length <= maximum and mismatch <= policy.max_pair_mismatch:
                pairs.append({"upper_primitive_count":m, "lower_primitive_count":n,
                              "mean_length_angstrom":length, "grain_lengths_angstrom":lengths,
                              "relative_mismatch":mismatch, "integer_complexity":m+n})
    pairs.sort(key=lambda x:(x["mean_length_angstrom"], x["relative_mismatch"]))
    best = float("inf")
    for pair in pairs:
        pair["length_mismatch_pareto"] = pair["relative_mismatch"] < best-1e-12
        best = min(best, pair["relative_mismatch"])
    return pairs


def diverse_extrema(x, quality, minimum, maximum, limit):
    """Combine strongest peaks and short-to-long coverage, rather than only top N."""
    peaks = [i for i in local_maxima(quality) if minimum <= x[i] <= maximum]
    chosen = sorted(peaks, key=lambda i:-quality[i])[:limit]
    edges = np.geomspace(minimum, maximum, 7)
    for lo, hi in zip(edges[:-1], edges[1:]):
        band = [i for i in peaks if lo <= x[i] <= hi]
        if band:
            chosen.append(max(band, key=lambda i:quality[i]))
    return sorted(set(chosen))


def add_seed(seeds, x, source, tolerance, *, pair=None):
    previous = next((s for s in seeds if abs(s["x"]-x) <= tolerance), None)
    if previous is None:
        previous = {"x":float(x), "sources":[], "lattice_pairs":[], "source_proposals":[]}
        seeds.append(previous)
    if source not in previous["sources"]:
        previous["sources"].append(source)
    previous["source_proposals"].append({"source":source, "x_angstrom":float(x)})
    if pair is not None and pair not in previous["lattice_pairs"]:
        previous["lattice_pairs"].append(pair)


def _json_write(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False)+"\n")


def _finalize(out, report):
    report["elapsed_seconds"] = time.monotonic()-report.pop("_started")
    # Only advertise products generated on this path through the current run;
    # a reused output directory may contain plots from an earlier success.
    names = ["source_metadata.json", "periodicity_diagnostics.png", "period_candidates.json"]
    if "boundary" in report and report["status"] != "unsupported_or_unresolved_geometry":
        names += ["boundary.json", "boundary_selection.npz"]
    if report.get("crystal_mapping"):
        names += ["crystal_mapping.json"]
    if report["status"] == "candidates_reported":
        names += ["candidate_curves.npz", "candidate_evidence.png", "in_plane_periodicity.png", "long_period_modulation.png", "modulation_profiles.json"]
    artifacts = {Path(name).stem:str(out/name) for name in names if (out/name).is_file()}
    cache = Path(report["source"]["ptm_cache"])
    artifacts.update({name:str(cache/f"{name}.{extension}") for name, extension in (("ptm_atoms", "npz"), ("ptm_metadata", "json"))})
    report["artifacts"] = artifacts
    _json_write(out/"period_candidates.json", report)
    return report


def analyze_coordinates(input_path, output_dir, *, source_metadata=None, cache_dir=None, policy=None):
    """Coordinate-row candidates plus artifacts; never select for the user.

    Builder metadata annotates provenance, names validated crystallographic
    axes, and supplies physical boundary flags. Its repeat counts never seed
    the search or candidate selection.
    """
    policy = policy or CandidatePolicy()
    policy.validate()
    input_path, out = Path(input_path).resolve(), Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(out/"mpl_cache"))
    started = time.monotonic()
    source_metadata = source_metadata or {}
    metadata = normalize_metadata(source_metadata)
    digest = hashlib.sha256(input_path.read_bytes()).hexdigest()
    if metadata.get("source_sha256") and metadata["source_sha256"] != digest:
        raise ValueError("Source SHA256 from metadata disagrees with the analyzed structure")
    if cache_dir is None:
        cache = out/"ptm_cache"
        meta = extract_ptm(input_path, cache, policy.ptm_cutoff, source_metadata=metadata)
    else:
        cache = Path(cache_dir).resolve()
        meta = json.loads((cache/"ptm_metadata.json").read_text())
        if meta["source_sha256"] != digest or not np.isclose(meta["ptm_rmsd_cutoff"], policy.ptm_cutoff):
            raise ValueError("Cached PTM does not match the source SHA256 and cutoff")
        flags, _ = periodic_flags(metadata, meta["pbc"])
        if tuple(meta["pbc"]) != flags:
            raise ValueError("Cached PTM periodic flags disagree with supplied source metadata")
    arrays = dict(np.load(cache/"ptm_atoms.npz"))
    report = {
        "analysis":"gb_periodicity_candidates", "status":"analyzing", "_started":started,
        "source_metadata":source_metadata,
        "source":{"path":str(input_path), "sha256":digest, "ptm_cache":str(cache), "ptm":meta},
        "policy":asdict(policy), "expected_repeat_ratio_used":False,
        "selected_candidate_id":None, "requires_user_decision":True,
        "scope":{"boundary_normal_axis":"y", "search_axis":"x", "transverse_periodic_axis":"z",
                 "box":"orthogonal", "crystal_indexing":"FCC/BCC with resolvable low-index x; optional"},
        "limitations":[
            "Finite-window geometric and structural hypotheses, not proof of exact translational symmetry.",
            "Nearest-atom matches are bidirectional but not a constrained bijection.",
            "No statistical confidence probabilities or automatic Burgers-vector conclusions are assigned.",
            "Small structures cannot exclude a longer modulation outside their observable range.",
        ],
    }
    _json_write(out/"source_metadata.json", source_metadata)
    plot_selection(out, arrays, None, None)
    try:
        boundary, masks = detect_boundary(arrays)
        if not arrays["pbc"][2] or "z" in boundary["vacuum_axes"]:
            raise ValueError("A periodic vacuum-free z direction is required")
    except (ValueError, IndexError) as exc:
        report.update(status="unsupported_or_unresolved_geometry", reason=str(exc), candidates=[])
        return _finalize(out,report)
    report["eligibility"] = {
        "eligible":bool(metadata.get("x_pbc") is not True and "x" in boundary["vacuum_axes"]),
        "construction_x_pbc":metadata.get("x_pbc"),
        "observed_x_vacuum":"x" in boundary["vacuum_axes"],
        "physical_ptm_pbc":meta["pbc"],
        "note":"Construction x_pbc=False means vacuum padding; it is distinct from LAMMPS boundary flags.",
    }
    if metadata.get("x_pbc") is True or "x" not in boundary["vacuum_axes"]:
        report.update(status="unsupported_or_unresolved_geometry", boundary=boundary, candidates=[],
                      reason="This workflow requires an x-vacuum GB structure; construction x_pbc=True or no resolved x vacuum was found")
        plot_selection(out, arrays, boundary, masks)
        return _finalize(out,report)
    if policy.x_window is not None:
        old = boundary["interior_x_interval_angstrom"]
        window = [max(old[0], policy.x_window[0]), min(old[1], policy.x_window[1])]
        if window[1] <= window[0]:
            raise ValueError("Requested x window does not overlap the valid interior")
        in_x = (arrays["positions"][:, 0] >= window[0]) & (arrays["positions"][:, 0] <= window[1])
        masks = {key:mask & in_x for key, mask in masks.items()}
        boundary = {**boundary, "interior_x_interval_angstrom":window,
                    "near_boundary_count_including_crystalline_neighbors":int(masks["near"].sum()),
                    "interior_defect_count":int(masks["defects"].sum()),
                    "analysis_window_note":"PTM and bulk orientation use the full input before this analysis-only crop."}
    report["boundary"] = boundary
    _json_write(out/"boundary.json", boundary)
    np.savez_compressed(out/"boundary_selection.npz", **masks)
    plot_selection(out, arrays, boundary, masks)
    nn, interval = boundary["nearest_neighbor_angstrom"], boundary["interior_x_interval_angstrom"]
    extent = interval[1]-interval[0]
    minimum, maximum = policy.minimum_lag_in_nn*nn, extent/2
    if maximum <= minimum or min(masks["near"].sum(), masks["defects"].sum()) < 30:
        report.update(status="insufficient_domain", candidates=[], reason="Too little interface support for coordinate registration")
        return _finalize(out,report)
    mapping, translations = None, {}
    try:
        mapping = analyze_crystal_mapping(cache/"ptm_atoms.npz", boundary["gb_y_angstrom"])
        for side, grain in mapping["grains"].items():
            checked = grain.get("coordinate_translation_check", {})
            transverse = checked.get("median_transverse_distance_angstrom", float("inf"))
            grain["pure_x_conversion_check"] = {"maximum_transverse_angstrom":policy.maximum_bulk_transverse_in_nn*nn,
                                                 "measured_transverse_angstrom":transverse if np.isfinite(transverse) else None,
                                                 "accepted":bool(transverse <= policy.maximum_bulk_transverse_in_nn*nn)}
            if grain["x_direction"]["accepted"] and checked.get("matched_pairs",0) >= 30 and grain["pure_x_conversion_check"]["accepted"]:
                translations[side] = checked["median_x_translation_angstrom"]
    except (ValueError, KeyError, IndexError) as exc:
        report["crystal_mapping_unavailable_reason"] = str(exc)
    report["crystal_mapping"] = mapping
    attach_source_convention(mapping, metadata)
    if mapping:
        _json_write(out/"crystal_mapping.json", mapping)
    pairs = near_coincidence_pairs(translations["upper"], translations["lower"], minimum, maximum, policy) if len(translations) == 2 else []
    report["lattice_pair_enumeration"] = pairs
    seeds = []
    for pair in pairs:
        if pair["length_mismatch_pareto"]:
            add_seed(seeds, pair["mean_length_angstrom"], "measured_lattice_pair_pareto", .12*nn, pair=pair)

    lags = np.arange(minimum, maximum, policy.scan_step_in_nn*nn)
    support = float(lags[-1])
    report["registration"] = {"fixed_anchor_support_angstrom":support, "search_interval_angstrom":[minimum,support],
                              "same_anchor_population_for_all_candidates":True,
                              "x_periodic_wrapping":False, "z_periodic_wrapping":True,
                              "domain_cycles_policy":"Count repetitions in the cropped physical interval, never across periodic images."}
    # Rational seeds close to the upper edge must obey the same fixed support.
    seeds = [s for s in seeds if s["x"] <= support]
    scans, acfs = {}, {}
    for name in ("near", "defects"):
        scans[name] = translation_scan(arrays, masks[name], lags, interval, reference_max_lag=support)
        for i in diverse_extrema(lags, -scans[name]["rms"], minimum, support, policy.max_seeds_per_method):
            add_seed(seeds, lags[i], f"{name}_xyz_minimum", .12*nn)
        acfs[name] = in_plane_acf(arrays, masks[name], interval)
        acf = acfs[name]
        best_z_correlation = acf["correlation"].max(axis=1)
        for i in diverse_extrema(acf["x_lag"], best_z_correlation, minimum, support, policy.max_seeds_per_method):
            add_seed(seeds, acf["x_lag"][i], f"{name}_layered_xz_correlation", .12*nn)

    # Long spectra also generate proposals, not just annotations on previously
    # selected geometric minima. Estimate the scale split from the coarse data.
    coarse_short = []
    for pair in pairs:
        x = pair["mean_length_angstrom"]
        close = abs(lags-x) <= .3*nn
        if (pair["length_mismatch_pareto"] and pair["relative_mismatch"] <= policy.shortlist_pair_mismatch
                and extent/x >= policy.minimum_repeat_cycles and close.any()
                and scans["near"]["rms"][close].min()/nn <= policy.shortlist_rms_in_nn):
            coarse_short.append(x)
    if not coarse_short:
        coarse_short = [float(lags[i]) for i in local_maxima(-scans["near"]["rms"])
                        if extent/lags[i] >= policy.minimum_repeat_cycles and scans["near"]["rms"][i]/nn <= policy.shortlist_rms_in_nn]
    initial_modulation = analyze_modulation(arrays,masks,boundary,min(coarse_short,default=None),policy.minimum_repeat_cycles,
                                            minimum_scale_ratio=policy.minimum_long_scale_ratio)
    defect_minima = local_maxima(-scans["defects"]["rms"])
    for hypothesis in initial_modulation["hypotheses"]:
        if hypothesis["status"] != "candidate":
            continue
        x = hypothesis["period_angstrom"]
        if minimum <= x <= support:
            add_seed(seeds,x,"scalar_modulation_spectral_peak",.12*nn)
            neighbors = [i for i in defect_minima if abs(lags[i]-x) <= .08*x]
            for i in sorted(neighbors,key=lambda k:scans["defects"]["rms"][k])[:3]:
                add_seed(seeds,lags[i],"nonbulk_xyz_near_modulation_spectrum",.12*nn)
    report["modulation_seed_search"] = {"coarse_data_derived_short_scale_angstrom":min(coarse_short,default=None),
                                         "status":initial_modulation["status"],
                                         "method":"Resolved scalar peaks plus up to three nonbulk xyz minima within 8% propose additional candidates; final evidence is reevaluated using the refined short scale."}

    candidates = []
    for seed in sorted(seeds, key=lambda s:s["x"]):
        refined = [{"objective":name, **refine_translation(arrays, masks[name], seed["x"], interval, support,
                     radius=.2*nn, step=policy.refinement_step_in_nn*nn)} for name in ("near","defects")]
        proposed_x = [r["x_angstrom"] for r in refined]
        # Preserve the original 2D proposals: optimizing pure x alone may
        # destroy an oblique period whose proper z phase has not yet been fitted.
        proposed_x += [p["x_angstrom"] for p in seed["source_proposals"] if "xz_correlation" in p["source"] or "spectral_peak" in p["source"]]
        for x in proposed_x:
            if not minimum <= x <= support:
                continue
            duplicate = next((c for c in candidates if abs(c["x_translation_angstrom"]-x) < .06*nn), None)
            if duplicate:
                duplicate["sources"] = sorted(set(duplicate["sources"]+seed["sources"]))
                duplicate["source_proposals"].extend(seed["source_proposals"])
                duplicate["source_refinements"].extend(refined)
                for pair in seed["lattice_pairs"]:
                    if pair not in duplicate["lattice_pairs"]:
                        duplicate["lattice_pairs"].append(pair)
                continue
            candidates.append({"x_translation_angstrom":x, "sources":list(seed["sources"]),
                               "lattice_pairs":list(seed["lattice_pairs"]),
                               "source_proposals":list(seed["source_proposals"]), "source_refinements":list(refined)})
    candidates.sort(key=lambda c:c["x_translation_angstrom"])
    if not candidates:
        report.update(status="no_resolved_candidates", candidates=[],
                      reason="No lattice or coordinate extrema found in the supported search interval")
        return _finalize(out,report)
    for i, c in enumerate(candidates):
        c["id"] = f"P{i+1:03d}"
    shifts = np.array([[c["x_translation_angstrom"],0.] for c in candidates])
    for name in ("near", "defects"):
        evidence = registration_evidence(arrays, masks[name], shifts, interval, support, nn)
        for c, e in zip(candidates, evidence):
            c.setdefault("geometry", {})[name] = e

    for c in candidates:
        x = c["x_translation_angstrom"]
        c["observable_cycles"] = extent/x
        c["layered_correlation"] = {}
        # A 2D density peak proposes a z phase; xyz registration validates it.
        proposed_z = [0.]
        for name, acf in acfs.items():
            i = int(np.argmin(abs(acf["x_lag"]-x)))
            row = acf["correlation"][i]
            j0 = int(np.argmin(abs(acf["z_lag"])))
            equivalent = np.flatnonzero(row >= row.max()-.01)
            j = min(equivalent, key=lambda k:abs(acf["z_lag"][k]))
            proposed_z.append(float(acf["z_lag"][j]))
            c["layered_correlation"][name] = {"pure_x":float(row[j0]), "best_z":float(acf["z_lag"][j]), "best_value":float(row[j])}
        trial_z = np.unique(np.r_[0., *[np.arange(z-.12*nn, z+.1201*nn, .01*nn) for z in proposed_z]])
        alternative = registration_evidence(arrays, masks["near"], np.column_stack((np.full(len(trial_z),x),trial_z)), interval, support, nn)
        j = min(range(len(trial_z)), key=lambda k:alternative[k]["rms"])
        best_x, best_z, best_joint = x, float(trial_z[j]), alternative[j]
        if best_joint["rms"] < c["geometry"]["near"]["rms"]-.05*nn:
            # Alternating coordinate refinement of promising oblique proposals.
            for axis in (0,1,0):
                shifts_2d = np.tile([best_x,best_z], (25,1))
                shifts_2d[:,axis] += np.linspace(-.12*nn,.12*nn,25)
                shifts_2d = shifts_2d[(shifts_2d[:,0] >= minimum) & (shifts_2d[:,0] <= support)]
                trials = registration_evidence(arrays,masks["near"],shifts_2d,interval,support,nn)
                k = min(range(len(trials)),key=lambda k:trials[k]["rms"])
                best_x,best_z = map(float,shifts_2d[k]); best_joint = trials[k]
        c["joint_xz_check"] = {"z_translation_angstrom":best_z, **best_joint,
                               "method":"Local xyz validation around preserved layered-2D proposals and zero z, with alternating x/z refinement; not an exhaustive continuous 2D optimization."}
        c["joint_xz_defects"] = registration_evidence(arrays,masks["defects"],np.array([[best_x,best_z]]),interval,support,nn)[0]
        c["supporting_evidence"], c["conflicting_evidence"], c["limitations"] = [], [], []
        if c["geometry"]["near"]["rms_over_nn"] <= policy.shortlist_rms_in_nn:
            c["supporting_evidence"].append("near_interface_xyz_residual_below_display_gate")
        else:
            c["conflicting_evidence"].append("pure_x_neighborhood_match_is_weak")
        if c["geometry"]["defects"]["rms_over_nn"] > policy.shortlist_rms_in_nn:
            c["conflicting_evidence"].append("nonbulk_pattern_does_not_match_as_well_as_display_gate")
        if c["observable_cycles"] < policy.minimum_repeat_cycles:
            c["limitations"].append("fewer_than_minimum_observed_cycles")
        if c["lattice_pairs"]:
            c["supporting_evidence"].append("length_mismatch_pareto_lattice_approximant")
        if best_joint["rms_over_nn"] <= policy.shortlist_rms_in_nn and best_joint["rms"] < c["geometry"]["near"]["rms"]-.05*nn:
            c["supporting_evidence"].append("nonzero_z_phase_restores_xyz_matching")
            c["limitations"].append("oblique_period_requires_vector_indexing_not_pure_x_integer_counts")
        c["crystal_counts"] = {}
        for side, t in translations.items():
            grain = mapping["grains"][side]
            factor = grain["shortest_translation_coefficient"]
            c["crystal_counts"][side] = {
                "measured_primitive_count":x/t, "measured_full_a_hkl_count":factor*x/t,
                "direction_family":grain["x_direction"]["cubic_family"],
                "signed_indices_in_ptm_symmetry_branch":grain["x_direction"]["signed_indices_in_ptm_branch"],
                "basis_note":"Pure x counts. Nonzero z alternatives require a full vector lattice check.",
            }
        for pair in c["lattice_pairs"]:
            pair["full_direction_counts"] = {side:pair[f"{side}_primitive_count"]*mapping["grains"][side]["shortest_translation_coefficient"] for side in ("upper","lower")}

    # The smallest data-supported low-complexity approximant defines only a
    # search split; it is not silently selected as the physical fundamental.
    short = [c for c in candidates if c["observable_cycles"] >= policy.minimum_repeat_cycles
             and c["geometry"]["near"]["rms_over_nn"] <= policy.shortlist_rms_in_nn
             and any(p["relative_mismatch"] <= policy.shortlist_pair_mismatch for p in c["lattice_pairs"])]
    fallback = [c for c in candidates if c["observable_cycles"] >= policy.minimum_repeat_cycles
                and c["geometry"]["near"]["rms_over_nn"] <= policy.shortlist_rms_in_nn]
    search_basis = short or fallback
    short_scale = min((c["x_translation_angstrom"] for c in search_basis), default=None)
    modulation = analyze_modulation(arrays, masks, boundary, short_scale, policy.minimum_repeat_cycles,
                                    minimum_scale_ratio=policy.minimum_long_scale_ratio)
    profiles = modulation.pop("profiles", {})
    _json_write(out/"modulation_profiles.json", profiles)
    report["long_modulation"] = modulation
    report["scale_partition"] = {"data_derived_short_scale_angstrom":short_scale,
                                 "source":"shortest supported lattice Pareto candidate; geometry fallback if indexing is unavailable",
                                 "is_user_selection":False,
                                 "sensitivity_note":"This split is a reported analysis policy, not proof of a unique short fundamental."}
    for c in candidates:
        x = c["x_translation_angstrom"]
        c["long_modulation_matches"] = []
        c["weak_modulation_matches"] = []
        for hypothesis in modulation["hypotheses"]:
            # Finite spectra are broad. Keep alternatives within this explicit
            # association tolerance instead of inventing an exact FFT period.
            if abs(x-hypothesis["period_angstrom"])/hypothesis["period_angstrom"] <= .08:
                key = "long_modulation_matches" if hypothesis["status"] == "candidate" else "weak_modulation_matches"
                c[key].append(hypothesis["id"])
        c["roles"] = []
        if c in short:
            c["roles"].append("near_coincidence_candidate")
        if c["geometry"]["near"]["rms_over_nn"] <= policy.shortlist_rms_in_nn:
            c["roles"].append("geometric_repeat_candidate")
        if "nonzero_z_phase_restores_xyz_matching" in c["supporting_evidence"]:
            c["roles"].append("oblique_in_plane_candidate")
        defect_validation = c["joint_xz_defects"] if "oblique_in_plane_candidate" in c["roles"] else c["geometry"]["defects"]
        if (c["long_modulation_matches"] and c["observable_cycles"] >= policy.minimum_repeat_cycles
                and defect_validation["rms_over_nn"] <= policy.shortlist_rms_in_nn):
            c["roles"].append("long_structural_modulation_candidate")
            c["supporting_evidence"].append("scalar_long_modulation_and_nonbulk_xyz_agree")
            c["long_validation"] = defect_validation
        c["approximately_multiple_of"] = []
        for parent in candidates:
            if parent is c or parent["x_translation_angstrom"] >= x:
                continue
            ratio = x/parent["x_translation_angstrom"]
            multiple = round(ratio)
            if 2 <= multiple <= 8 and abs(ratio-multiple)/multiple <= .01:
                c["approximately_multiple_of"].append({"candidate_id":parent["id"], "multiple":multiple,
                                                         "relative_deviation":abs(ratio-multiple)/multiple})
        c["status"] = "candidate" if c["roles"] else "weak_or_unresolved_alternative"
    long = [c for c in candidates if "long_structural_modulation_candidate" in c["roles"]]
    long.sort(key=lambda c:c["long_validation"]["rms_over_nn"])
    geometric = sorted(candidates, key=lambda c:c["geometry"]["near"]["rms_over_nn"])
    short.sort(key=lambda c:c["x_translation_angstrom"])
    all_pairs_supported = list(short)
    hierarchies = [{"long_candidate_id":large["id"],
                   "relative_short_candidate_ids":[small["id"] for small in short
                        if large["x_translation_angstrom"]/small["x_translation_angstrom"] >= policy.minimum_long_scale_ratio],
                   "meaning":"Alternative scale pairings, not an automatic choice of a fundamental cell."}
                  for large in long]
    if long:
        short_ids = {cid for hierarchy in hierarchies for cid in hierarchy["relative_short_candidate_ids"]}
        short = [c for c in short if c["id"] in short_ids]
    for c in short:
        c["roles"].append("relative_short_candidate")
    annotate_counts_and_mismatch(candidates, mapping, metadata)
    report.update(status="candidates_reported", candidates=candidates,
                  period_hierarchies=hierarchies,
                  candidate_groups={"short_near_coincidence":[c["id"] for c in short],
                                    "geometric_matches":[c["id"] for c in geometric if "geometric_repeat_candidate" in c["roles"]],
                                    "long_structural_modulation":[c["id"] for c in long],
                                    "oblique_in_plane":[c["id"] for c in candidates if "oblique_in_plane_candidate" in c["roles"]],
                                    "lattice_alternatives_outside_shortlist_gates":[c["id"] for c in candidates if c["lattice_pairs"] and c not in all_pairs_supported],
                                    "all_supported_lattice_approximants":[c["id"] for c in all_pairs_supported]},
                  selection_rules={
                      "short":"Length/mismatch Pareto lattice seeds + finite-cycle and xyz residual gates; show shortest first.",
                      "geometry":"All-atom xyz residual order, no physical-period designation.",
                      "long":"Generic PTM modulation evidence + nonbulk xyz match + sufficient cycles; order by nonbulk RMS.",
                      "hierarchy":"If long candidates exist, relative short candidates must be smaller by the stated scale-separation factor; retain every admissible pairing.",
                      "modulation_to_geometry_relative_tolerance":.08,
                      "harmonics":"Annotate approximate integer multiples, never discard a supercell only because its ratio is reducible.",
                      "threshold_meaning":"Configurable evidence/display gates, not material laws or calibrated probabilities.",
                  })
    report["modulation_geometry_validation"] = [
        {"modulation_id":h["id"], "period_angstrom":h["period_angstrom"],
         "geometry_candidate_ids":[c["id"] for c in long if h["id"] in c["long_modulation_matches"]],
         "status":"geometry_supported" if any(h["id"] in c["long_modulation_matches"] for c in long)
                    else "spectral_candidate_without_sufficient_geometry_support",
         "local_features_by_signal":h["local_features_by_signal"]}
        for h in modulation["hypotheses"] if h["status"] == "candidate"]
    packed = {f"xyz_{name}_{key}":value for name,c in scans.items() for key,value in c.items()}
    packed.update({f"acf_{name}_{key}":value for name,c in acfs.items() for key,value in c.items()})
    np.savez_compressed(out/"candidate_curves.npz", **packed)
    _json_write(out/"period_candidates.json", report)
    plot_candidates(out, report, scans, profiles)
    plot_in_plane(out, report, acfs)
    plot_modulation(out, report, profiles)
    return _finalize(out, report)


def plot_candidates(out, report, scans, profiles):
    import matplotlib.pyplot as plt
    candidates = report["candidates"]
    fig, axes = plt.subplots(3, 1, figsize=(13, 11), layout="constrained")
    for name,c in scans.items():
        axes[0].plot(c["lag"], c["rms"]/report["boundary"]["nearest_neighbor_angstrom"], label=name)
    highlighted = set(report["candidate_groups"]["short_near_coincidence"][:3] + report["candidate_groups"]["long_structural_modulation"][:3])
    for c in candidates:
        if c["id"] in highlighted:
            x = c["x_translation_angstrom"]
            axes[0].axvline(x, color="gray", ls="--", lw=.7)
            axes[0].annotate(f"{c['id']}\n{x:.2f}", (x, c["geometry"]["near"]["rms_over_nn"]), xytext=(0,15), textcoords="offset points", fontsize=8)
    axes[0].set(xlabel="Pure x translation / Å", ylabel="xyz RMS / nearest-neighbor distance", title="Multiple candidates: geometric match does not uniquely define a structural period")
    axes[0].legend()
    pairs = report["lattice_pair_enumeration"]
    for frontier,label,color in ((False,"Other integer approximants","#cccccc"),(True,"Length / mismatch Pareto frontier","tab:blue")):
        selected = [p for p in pairs if p["length_mismatch_pareto"] == frontier]
        axes[1].scatter([p["mean_length_angstrom"] for p in selected], [100*p["relative_mismatch"] for p in selected], s=20, label=label, color=color)
        if frontier:
            for p in selected:
                axes[1].annotate(f"{p['upper_primitive_count']}:{p['lower_primitive_count']}", (p["mean_length_angstrom"],100*p["relative_mismatch"]), xytext=(3,5),textcoords="offset points",fontsize=8)
    axes[1].set(xlabel="Near-coincidence length / Å", ylabel="Measured mismatch (mean denominator) / %",
                title="Measured grain translations; labels = upper:lower primitive translation counts")
    axes[1].legend()
    x = np.asarray(profiles.get("x_angstrom", []))
    for name, data in profiles.items():
        if name == "x_angstrom":
            continue
        envelope = np.asarray(data["envelope"])
        axes[2].plot(x, (envelope-envelope.mean())/max(float(envelope.std()),1e-15), label=name)
    if len(x):
        axes[2].legend(fontsize=8)
    else:
        axes[2].text(.05,.5,report["long_modulation"].get("reason",report["long_modulation"]["status"]),transform=axes[2].transAxes)
    axes[2].set(xlabel="Position x / Å", ylabel="Standardized envelope", title=f"Long modulation: {report['long_modulation']['status']}; scalar evidence needs xyz validation")
    for ax in axes:
        ax.grid(alpha=.15)
    fig.savefig(out/"candidate_evidence.png", dpi=150)
    plt.close(fig)
