"""Compare spatial intervals, Fourier wavelengths and atomic translations.

 review using the coordinate analysis artifacts. No expected
period or material-specific branch enters candidate generation or display.
"""
from __future__ import annotations

import csv
import json
import os

os.environ.setdefault("MPLBACKEND", "Agg")
from .scale_relations import describe_scale_relations
from .summary_candidates import build_candidate_annotations
from .summary_figure import candidate_scale_tracks, render_summary
from .structure_spectrum import match_structure_peak


def read(path):
    return json.loads(path.read_text())


def structure_peak_evidence(structure, period):
    """Query observed maxima; a candidate does not seed a spectral peak."""
    return {name: match_structure_peak(channel, period)
            for name, channel in structure.get("channels", {}).items()}


def strain_peak_evidence(elastic, period):
    from .strain_spectrum import match_strain_peak
    return {name: match_strain_peak(profile["spectrum"], period)
            for name, profile in elastic.get("profiles", {}).items()
            if profile["z_channel"] == "all" and profile.get("distance_band") != "far_field"}


def catalog(case, coordinate_dir=None):
    coordinate_dir = coordinate_dir if coordinate_dir is not None else case
    coordinate = read(coordinate_dir/"period_candidates.json")
    elastic = read(case/"elastic/elastic_periodicity.json")
    by_id = {r["candidate_id"]:r for r in elastic["coordinate_candidates_with_strain"]}
    rows = []
    for c in sorted(coordinate["candidates"], key=lambda r:r["x_translation_angstrom"]):
        e = by_id.get(c["id"], {})
        near, defect = c["geometry"]["near"], c["geometry"]["defects"]
        joint = c.get("joint_xz_check", {})
        # Facets stay independent: no 'FFT peak AND recurrence' eligibility gate.
        recurrence, spectrum = [], []
        for name, channel in e.get("channels", {}).items():
            if not name.endswith("_all") or elastic["profiles"].get(name, {}).get("distance_band") == "far_field":
                continue
            if channel.get("signed_correlation") is not None and channel["signed_correlation"] >= .5:
                recurrence.append(name)
            if channel.get("spectral_peak_match"):
                spectrum.append(name)
        rows.append({
            "candidate_id":c["id"], "translation_angstrom":c["x_translation_angstrom"],
            "translation_nm":c["x_translation_angstrom"]/10,
            "near_rms_angstrom":near["rms"], "defect_rms_angstrom":defect["rms"],
            "near_rms_over_nn":near["rms_over_nn"], "defect_rms_over_nn":defect["rms_over_nn"],
            "near_coverage_fraction":near["coverage_fraction"], "defect_coverage_fraction":defect["coverage_fraction"],
            "joint_xz_rms_angstrom":joint.get("rms"), "joint_z_shift_angstrom":joint.get("z_translation_angstrom"),
            "strain_recurrent_strata":recurrence, "strain_spectral_peak_strata":spectrum,
            "lower_near_strain_correlation":e.get("channels",{}).get("lower_near_all",{}).get("signed_correlation"),
            "upper_near_strain_correlation":e.get("channels",{}).get("upper_near_all",{}).get("signed_correlation"),
            "legacy_roles":c.get("roles", []), "legacy_status":c.get("status"),
            "crystal_counts":c.get("crystal_counts", {}),
            "structure_fft_evidence":structure_peak_evidence(elastic.get("structure_fft", {}), c["x_translation_angstrom"]),
            "strain_fft_evidence":strain_peak_evidence(elastic, c["x_translation_angstrom"]),
            "evidence_type":"atomic_translation_hypothesis_not_automatically_a_local_event_spacing",
        })
        for name, evidence in rows[-1]["structure_fft_evidence"].items():
            peak = evidence.get("matched_peak") or {}
            rows[-1].update({
                f"structure_{name}_fft_status": evidence["status"],
                f"structure_{name}_fft_peak_nm": peak.get("period_angstrom", 0) / 10 if peak else None,
                f"structure_{name}_fft_classification": peak.get("classification"),
                f"structure_{name}_fft_review_eligible": peak.get("review_eligible"),
                f"structure_{name}_fft_relative_power": peak.get("relative_power"),
                f"structure_{name}_fft_background_contrast": peak.get("background_contrast"),
            })
        evidence = rows[-1]["strain_fft_evidence"]
        rows[-1]["strain_actual_peak_channels"] = [name for name, item in evidence.items() if item.get("matched_peak")]
        rows[-1]["strain_reviewed_peak_channels"] = [name for name, item in evidence.items()
            if (item.get("matched_peak") or {}).get("review_eligible")]
    return rows, elastic


def review(case, coordinate_dir=None):
    coordinate_dir = coordinate_dir if coordinate_dir is not None else case
    out = case/"multiscale_review"
    out.mkdir(exist_ok=True)
    rows, elastic = catalog(case, coordinate_dir)
    event_file = case/"local_strain/local_strain_events.json"
    row_file = case/"coordinate_rows/coordinate_rows.json"
    events = read(event_file)
    row_features = read(row_file)
    for local in (events,row_features):
        fingerprint = local.get("source_sha256", local.get("ptm_source_provenance", {}).get("source_sha256"))
        if fingerprint is not None and fingerprint != elastic["source_sha256"]:
            raise ValueError("Spatial diagnostics do not match the source fingerprint")
    short_scales = []
    for row in row_features.get("rows", []):
        if row["row_id"] not in row_features.get("plotted_row_ids", []) or row["sampling_status"] != "approximately_uniform_atom_row":
            continue
        seq = row["sequence"]
        if seq["status"] == "predominantly_alternating_two_spacing_modes":
            short_scales.append({"id":row["row_id"],"source":"observed_ordinary_crest_pair",
                                 "period_angstrom":seq["ordinary_pair_median_angstrom"]})
        elif row["spacing_groups"]:
            mode = max(row["spacing_groups"],key=lambda m:m["count"])
            short_scales.append({"id":row["row_id"],"source":"dominant_observed_row_crest_spacing",
                                 "period_angstrom":mode["median_angstrom"]})
    for i,p in enumerate(elastic["strain_proposals"]):
        if p["assessment"] == "corroborated_by_layered_strain":
            short_scales.append({"id":f"strain_peak_{i}","source":"layered_strain_fft_candidate",
                                 "period_angstrom":p["period_angstrom"]})
    long_scales = [{"id":r["candidate_id"],"source":"atomic_translation_candidate",
                    "period_angstrom":r["translation_angstrom"]} for r in rows]
    long_scales += [{"id":f"local_strain_{i}","source":"spatial_strain_interval_candidate",
                     "period_angstrom":m["center_angstrom"]} for i,m in enumerate(events.get("spacing_candidates", []))]
    nn = events["nearest_neighbor_angstrom"]
    annotations = build_candidate_annotations(
        read(coordinate_dir/"period_candidates.json"),
        read(coordinate_dir/"crystal_mapping.json"), row_features, events, nn)
    for note in [*annotations.get("short_period_annotations", []), *annotations.get("long_period_annotations", [])]:
        note["structure_fft_evidence"] = structure_peak_evidence(elastic.get("structure_fft", {}), note["period_nm"] * 10)
        note["strain_fft_evidence"] = strain_peak_evidence(elastic, note["period_nm"] * 10)
    labels = {r["candidate_id"]: r for r in annotations["records"]}
    long_labels = {r["candidate_id"]: (f"L{i}", r["kind"])
                   for i, r in enumerate(annotations["long_period_annotations"], 1)}
    short_labels = {r["candidate_id"]: f"S{i}" for i, r in enumerate(annotations["short_period_annotations"], 1)
                    if r["candidate_id"] is not None}
    short_notes = {r["candidate_id"]: r for r in annotations["short_period_annotations"] if r["candidate_id"] is not None}
    for row in rows:
        label = labels[row["candidate_id"]]
        row.update({"measured_upper_lower_ratio": label["ratio_label"],
                    "display_lattice_label": label["display_lattice_label"],
                    "display_direction_comparison": label["display_direction_ratio_label"],
                    "display_count_kind": label["display_lattice_metadata"]["count_kind"],
                    "direction_comparison": label["direction_ratio_label"],
                    "upper_count_basis": label["upper"]["basis"],
                    "lower_count_basis": label["lower"]["basis"],
                    "display_priority_score": label["highlight_score"],
                    "display_highlighted": label["highlighted"],
                    "lattice_repeat_status": label["lattice_repeat_check"]["status"],
                    "pure_x_repeat_eligible": label["lattice_repeat_check"]["eligible"],
                    "lattice_repeat_check": label["lattice_repeat_check"],
                    "short_period_label": short_labels.get(row["candidate_id"], ""),
                    "long_period_label": long_labels.get(row["candidate_id"], ("", ""))[0],
                    "long_period_evidence_kind": long_labels.get(row["candidate_id"], ("", ""))[1]})
        motif = short_notes.get(row["candidate_id"], {})
        row.update({"associated_row_motif_nm": motif.get("row_period_nm"),
                    "motif_association_kind": motif.get("association_kind"),
                    "motif_to_coordinate_difference_angstrom": motif.get("association_difference_angstrom")})
    relations = describe_scale_relations(short_scales,long_scales,tolerance_angstrom=.3*nn)
    relations["tolerance_policy"] = "0.3 measured nearest-neighbor distance: a soft numerical association, not a confidence interval or an eligibility gate."
    keys = [k for k in rows[0] if k not in ("crystal_counts", "evidence_type", "structure_fft_evidence", "strain_fft_evidence")]
    with (out/"all_candidates.csv").open("w",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key:json.dumps(row[key], allow_nan=False) if isinstance(row[key], dict)
                             else "; ".join(row[key]) if isinstance(row[key],list) else row[key] for key in keys})
    report = {
        "status":"summary_pending", "source":elastic["source"], "source_sha256":elastic["source_sha256"],
        "long_band_observability": {
            "status":"available" if elastic["long_band_policy"]["min_period_angstrom"] < elastic["long_band_policy"]["max_period_angstrom"] else "unresolved_window",
            "minimum_nm":elastic["long_band_policy"]["min_period_angstrom"] / 10,
            "maximum_nm":elastic["long_band_policy"]["max_period_angstrom"] / 10,
            "meaning":"The exploratory long-wave band requires its lower bound to be below the observable upper bound. An unresolved band does not establish absence of long periodicity.",
        },
        "all_atomic_translation_candidates":rows,
        "candidate_annotations":annotations,
        "structure_fft_review":elastic.get("structure_fft_review", {}),
        "strain_fft_review":elastic.get("strain_fft_review", {}),
        "summary_scale_tracks":[{"label":name, "periods_angstrom":periods}
            for name,periods,_ in candidate_scale_tracks(rows,elastic,events,row_features)],
        "local_strain_spacing_candidates":events.get("spacing_candidates", []),
        "local_strain_report":str(event_file.resolve()), "coordinate_rows_report":str(row_file.resolve()),
        "coordinate_row_count":len(row_features.get("rows", [])),
        "scale_relations":relations,
        "coordinate_row_sequence_evidence":[{
            "row_id":r["row_id"],"grain":r["grain"],"sampling_status":r["sampling_status"],
            "ordinary_pair_median_nm":r["sequence"]["ordinary_pair_median_angstrom"]/10,
            "interruption_positions_nm":[v/10 for v in r["sequence"]["event_positions_x_angstrom"]],
            "interruption_spacings_nm":[v/10 for v in r["sequence"]["event_spacings_angstrom"]]}
            for r in row_features.get("rows", []) if r["sequence"]["status"] == "predominantly_alternating_two_spacing_modes"
            and r["sampling_status"] == "approximately_uniform_atom_row"],
        "original_full_spectrum_report":str((case/"elastic/elastic_periodicity.json").resolve()),
        "candidate_table":str((out/"all_candidates.csv").resolve()),
        "summary_image":None,
        "interpretation_limits":[
            "All coordinate candidates are retained; a display highlight is not automatic physical identification.",
            "Low-mismatch lattice alternatives and measured structural motifs describe different lengths.",
            "A Fourier peak and recurrence are evaluated separately because local spacings and supercells need not have their own peak.",
            "Bulk atomic density spectra and broad strain averages are not complete descriptors of interfacial spatial motifs.",
            "A strong whole-pattern atomic translation can combine several different local feature intervals.",
            "A fixed percentage of the strongest density peak suppresses weak peaks; geometric far-field references and audited local maxima are recorded separately.",
        ],
        "evidence_semantics":{
            "fourier_wavelength":"Global oscillation of the selected signal; may describe a harmonic, average scale or combination repeat.",
            "local_feature_spacing":"Consecutive extrema in a stated signed signal, depth and smoothing scale; may vary in space. Not an identified dislocation spacing.",
            "atomic_translation":"Full-coordinate nearest-atom registration; can fail when the field repeats with transverse phase change.",
            "candidate_eligibility":"No FFT, recurrence, mismatch or physical scale criterion removes rows from the catalog.",
            "structure_fft_review":"An actual local maximum is associated by native frequency resolution. Background contrast compares normalized spectral shapes; it is not physical intensity gain or a statistical significance test.",
            "strain_fft_review":"Actual signed-strain maxima, amplitude and numerical checks are separate from recurrence. A peak shared with a far-field strain profile remains available; shared field evidence is not an automatic rejection.",
            "short_motif_association":"A nearby indexed coordinate candidate may accompany a measured row motif; both lengths and their discrepancy remain explicit, and a nearby association does not assert equality.",
        },
        "selected_periods":[], "requires_user_decision":True,
    }
    (out/"multiscale_review.json").write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
    try:
        render_summary(case,out,rows,elastic,events,row_features,annotations, coordinate_dir=coordinate_dir)
        if not (out/"multiscale_summary.png").is_file():
            raise RuntimeError("Summary renderer did not produce its image")
    except Exception as exc:
        report.update(status="summary_failed", summary_error=str(exc))
        (out/"multiscale_review.json").write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
        raise
    report.update(status="completed", summary_image=str((out/"multiscale_summary.png").resolve()))
    (out/"multiscale_review.json").write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
    lines=[f"# {case.name}: 全部坐标候选", "", "长度单位 nm；残差单位 Å。频谱、场的空间递归与完整原子匹配分别列出。",
           "原分类只保留作追溯；不把 relative_short 当成唯一短周期。所有局部事件间距及原始序列请查看旁边的 local_strain / coordinate_rows 报告。", "",
           "方向：" + "; ".join(annotations["direction_labels"]),
           "原始比值是完整 a[hkl] 向量的实测上 : 下倍数；方向依据：" + ", ".join(annotations["direction_bases"]),
           "图中采用经双侧长度验证的名义配对；无合格配对时保留 ≈ 实测值。无已验证的源取向时，以立方对称等价晶向简写显示；原有有符号晶向不变。",
           "高亮说明：" + annotations["reason"], "",
           "结构 FFT 列给出实际测得的峰位置，括号为背景／网格检查分类；不把候选平移改写为 FFT 波长。完整背景对照及每个局部峰见 `../elastic/structure_fft_peaks.csv`。",
           "应变 FFT 层数只统计通过审查的真实峰，不含远场参考。所有未通过审查的峰也保存在 `../elastic/strain_fft_peaks.csv`；CSV 的 strain_actual_peak_channels 与 strain_reviewed_peak_channels 分别列出实际峰和通过审查的峰。", ""]
    for i, note in enumerate(annotations["short_period_annotations"], 1):
        coordinate_nm = note.get("coordinate_period_nm")
        comparison = f"；关联坐标平移 {coordinate_nm:.6f} nm" if coordinate_nm is not None else ""
        lines.append(f"S{i}: 原子行 motif {note['row_period_nm']:.6f} nm{comparison}；{note['display_lattice_label']}；{note.get('association_kind', 'unmatched')}。")
    lines += ["",
           "| ID | 平移 / nm | 显示晶格配对 | 上 : 下实测倍数 | near RMS | defect RMS | 两侧近场应变相关性 | 有递归的层数 | 有通过审查 strain FFT 峰的层数 | near structure FFT / nm | core structure FFT / nm |",
           "|---|---:|---|---|---:|---:|---|---:|---:|---|---|"]
    for r in rows:
        correlations=" / ".join("—" if r[k] is None else f"{r[k]:.3f}" for k in ("lower_near_strain_correlation","upper_near_strain_correlation"))
        ident = r['candidate_id'] + (" ★" if r["display_highlighted"] else "") + (f" ({r['short_period_label']})" if r['short_period_label'] else "") + (f" ({r['long_period_label']})" if r['long_period_label'] else "")
        spectral = []
        for name in ("near", "defects"):
            evidence = r["structure_fft_evidence"].get(name, {})
            peak = evidence.get("matched_peak")
            spectral.append(f"{peak['period_angstrom']/10:.3f} ({peak['classification']})" if peak
                            else evidence.get("status", "unavailable"))
        lines.append(f"| {ident} | {r['translation_nm']:.4f} | {r['display_lattice_label']} | {r['measured_upper_lower_ratio']} | {r['near_rms_angstrom']:.4f} | {r['defect_rms_angstrom']:.4f} | {correlations} | {len(r['strain_recurrent_strata'])}/6 | {len(r['strain_spectral_peak_strata'])}/6 | {' | '.join(spectral)} |")
    (out/"ALL_CANDIDATES.md").write_text("\n".join(lines)+"\n")
    # Keep one current entry point, with details loaded from the latest report.
    elastic["multiscale_review"]={"report":str((out/"multiscale_review.json").resolve()),
        "summary_image":str((out/"multiscale_summary.png").resolve()),
        "highlighted_candidate_id":annotations["highlighted_id"],
        "meaning":"Primary summary combines structure/strain spectra, spatial features and every coordinate candidate with direction ratios; a display highlight is review priority, not a selected physical period."}
    (case/"elastic/elastic_periodicity.json").write_text(json.dumps(elastic,indent=2,allow_nan=False)+"\n")
    index_file = case/"elastic/review_index.json"
    index = {"status": "completed", "source": elastic["source"],
             "source_sha256": elastic["source_sha256"], "summary_image": str((out/"multiscale_summary.png").resolve()),
             "full_report": str((case/"elastic/elastic_periodicity.json").resolve()),
             "multiscale_review": elastic["multiscale_review"],
             "complete_candidate_table": str((out/"all_candidates.csv").resolve()),
             "structure_fft_review": elastic.get("structure_fft_review", {}),
             "strain_fft_review": elastic.get("strain_fft_review", {}),
             "short_motif_candidates": [{key: note.get(key) for key in (
                 "candidate_id", "row_period_nm", "period_nm", "coordinate_period_nm", "association_kind",
                 "association_relative_difference", "association_difference_angstrom", "display_lattice_label", "reason")}
                 for note in annotations["short_period_annotations"]],
             "longer_candidates": [{k: v for k, v in note.items() if k not in
                                    ("strain_spacing_matches", "row_interruption_matches", "strain_fft_evidence", "structure_fft_evidence")}
                                   for note in annotations["long_period_annotations"]],
             "selected_candidate_id": None, "requires_user_decision": True}
    index_file.write_text(json.dumps(index,indent=2,allow_nan=False)+"\n")
    return report
