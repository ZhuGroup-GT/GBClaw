"""Auditable lattice labels and optional display emphasis for  summaries.

The highlight is a local-evidence ranking, not a probability, unique fundamental
period, or identified dislocation spacing. Original measured candidates are kept.
"""
from __future__ import annotations

import math
from statistics import median

from ..crystal_mapping import translation_count_step


SIDES = ("upper", "lower")


def _positive(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def _indices(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and float(v).is_integer() for v in value):
        return None
    result = [int(v) for v in value]
    return result if any(result) else None


def _direction_label(indices):
    return "[" + " ".join(str(v) for v in indices) + "]" if indices is not None else "unresolved"


def _display_number(value):
    # Decimal formatting, never nearest-integer or half-integer quantization.
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _compact_direction(indices):
    if indices is None:
        return "[unresolved]"
    # Delimit multi-digit/signed indices to avoid ambiguous crystallographic text.
    separator = "" if all(0 <= v < 10 for v in indices) else " "
    return "[" + separator.join(str(v) for v in indices) + "]"


def _cubic_translation_step(indices, lattice):
    """Allowed full-a[hkl] count increment, including nonprimitive hkl."""
    if lattice not in ("FCC", "BCC") or indices is None:
        return None
    return translation_count_step(lattice, indices)


def _full_vector_length(grain, side):
    """Prefer actual far-field translation over an isotropic PTM estimate."""
    source = side["basis"] == "validated_source_direction"
    coefficient = (grain.get("source_convention", {}).get("full_direction_translation_coefficient")
                   if source else grain.get("shortest_translation_coefficient"))
    measured = grain.get("coordinate_translation_check", {}).get("median_x_translation_angstrom")
    if _positive(coefficient) and _positive(measured):
        return measured / coefficient, "measured_far_field_translation"
    a = grain.get("local_a_median_angstrom")
    if _positive(a) and side["direction"]:
        return a * math.sqrt(sum(v * v for v in side["direction"])), "PTM_local_cubic_lattice_constant"
    length = grain.get("full_a_hkl_length_angstrom")
    if not source and _positive(length):
        return length, "PTM_full_direction_length"
    return None, None


def _lattice_display(candidate, mapping, sides, tolerance):
    """Choose a nominal pair only after checking both grain lengths explicitly.

    Preserve measured counts and signed PTM directions separately. Cubic family
    representatives are a display convention, never an inferred builder frame.
    """
    directions = {s: (sides[s]["direction"] if sides[s]["basis"] == "validated_source_direction"
                       else sorted(map(abs, sides[s]["direction"]), reverse=True)
                       if sides[s]["direction"] is not None else None) for s in SIDES}
    metadata = {
        "basis": "validated_source_direction" if all(sides[s]["basis"] == "validated_source_direction" for s in SIDES)
                 else "cubic_symmetry_representatives_with_validated_source_preferred",
        "display_directions": directions,
        "original_signed_directions": {s: sides[s]["direction"] for s in SIDES},
        "direction_basis_by_grain": {s: "validated_source_direction" if sides[s]["basis"] == "validated_source_direction"
                                       else "cubic_symmetry_representative" if directions[s] is not None
                                       else "unresolved" for s in SIDES},
        "measured_full_a_hkl_counts": {s: sides[s]["count"] for s in SIDES},
        "maximum_allowed_length_error_angstrom": tolerance,
        "count_kind": "unindexed", "nominal_counts": None,
        "meaning": "Upper : lower full a[hkl] coefficients; canonical cubic-symmetry labels are not recovered builder orientations.",
    }
    if any(sides[s]["count"] is None or directions[s] is None for s in SIDES):
        return "unindexed", metadata
    period = candidate["x_translation_angstrom"]
    grains = {s: mapping.get("grains", {}).get(s, {}) for s in SIDES}
    lengths = {s: _full_vector_length(grains[s], sides[s]) for s in SIDES}
    options = []

    def check(values, origin, pair_index=None):
        if not all(_positive(values.get(s)) for s in SIDES):
            return
        residuals, bases, physical_lengths = {}, {}, {}
        for s in SIDES:
            unit, basis = lengths[s]
            if unit is None:
                if origin != "enumerated_lattice_pair":
                    return
                unit, basis = period / sides[s]["count"], "existing_measured_count_conversion"
            physical_lengths[s] = values[s] * unit
            residuals[s] = abs(physical_lengths[s] - period)
            bases[s] = basis
            if residuals[s] > tolerance:
                return
            step = _cubic_translation_step(sides[s]["direction"], grains[s].get("lattice"))
            if step is None or abs(values[s] / step - round(values[s] / step)) > 1e-7:
                return
        options.append({"nominal_counts": {s: float(values[s]) for s in SIDES},
                        "length_error_angstrom": residuals, "length_validation_basis": bases,
                        "grain_lengths_angstrom": physical_lengths, "count_kind": origin,
                        "lattice_pair_index": pair_index,
                        "maximum_length_error_angstrom": max(residuals.values())})

    for index, pair in enumerate(candidate.get("lattice_pairs", [])):
        source_directions = pair.get("source_signed_directions", {})
        if all(sides[s]["basis"] == "validated_source_direction"
               and _indices(source_directions.get(s)) == sides[s]["direction"] for s in SIDES):
            check(pair.get("source_direction_counts", {}), "enumerated_lattice_pair", index)
        elif all(sides[s]["basis"] == "PTM_cubic_symmetry_branch" for s in SIDES):
            check(pair.get("full_direction_counts", {}), "enumerated_lattice_pair", index)
    if not options and all(grains[s].get("pure_x_conversion_check", {}).get("accepted") is True
                           and grains[s].get("x_direction", {}).get("accepted") is True for s in SIDES):
        proposed = {}
        for s in SIDES:
            step = _cubic_translation_step(sides[s]["direction"], grains[s].get("lattice"))
            unit = lengths[s][0]
            if step is None or unit is None:
                break
            proposed[s] = round(period / unit / step) * step
        if len(proposed) == 2:
            check(proposed, "validated_cubic_translation_grid")
    if options:
        chosen = min(options, key=lambda option: option["maximum_length_error_angstrom"])
        metadata.update(chosen)
        metadata["count_error_relative_to_measured"] = {
            s: chosen["nominal_counts"][s] - sides[s]["count"] for s in SIDES}
        values, prefix = chosen["nominal_counts"], ""
    else:
        metadata["count_kind"] = "measured_relaxed_counts"
        values, prefix = metadata["measured_full_a_hkl_counts"], "≈ "
    label = prefix + " : ".join(_display_number(values[s]) + _compact_direction(directions[s]) for s in SIDES)
    return label, metadata


def _lattice_repeat_check(candidate, mapping, sides, metadata, tolerance):
    """Audit pure-x closure independently in both grains, retaining raw features.

    Coefficient membership is exact for the nominal reference lattice; the
    relaxed coordinate translation is matched with a stated absolute tolerance.
    FFT peaks, row spacings and oblique translations need not pass this gate.
    """
    grains, reasons = {}, []
    nominal = metadata.get("nominal_counts")
    unresolved = False
    for side in SIDES:
        grain = mapping.get("grains", {}).get(side, {})
        direction = sides[side]["direction"]
        step = _cubic_translation_step(direction, grain.get("lattice"))
        unit, basis = _full_vector_length(grain, sides[side])
        indexed = (grain.get("x_direction", {}).get("accepted") is True
                   and grain.get("pure_x_conversion_check", {}).get("accepted") is True)
        if step is None or unit is None or not indexed:
            unresolved = True
            reasons.append(f"{side}_lattice_or_pure_x_indexing_unresolved")
        nearest = max(1, round(candidate["x_translation_angstrom"] / unit / step)) * step if step and unit else None
        error = abs(nearest * unit - candidate["x_translation_angstrom"]) if nearest is not None else None
        if error is not None and error > tolerance:
            reasons.append(f"{side}_outside_allowed_translation_grid")
        grains[side] = {"lattice": grain.get("lattice"), "direction": direction,
                        "direction_basis": sides[side]["basis"], "coefficient_step": step,
                        "full_vector_length_angstrom": unit, "length_basis": basis,
                        "nearest_allowed_count": nearest, "nearest_length_error_angstrom": error,
                        "nominal_count": nominal.get(side) if nominal else None,
                        "indexing_validated": indexed}
    oblique = "oblique_in_plane_candidate" in candidate.get("roles", [])
    if oblique:
        status = "requires_full_vector_check"
        reasons.append("oblique_translation_is_not_a_pure_x_repeat")
    elif unresolved:
        status = "unresolved"
    elif nominal is None:
        status = "incompatible"
        if not reasons:
            reasons.append("no_validated_two_grain_nominal_pair")
    else:
        status = "compatible"
    return {"status": status, "eligible": status == "compatible", "grains": grains,
            "nominal_counts": nominal if status == "compatible" else None,
            "length_tolerance_angstrom": tolerance, "reasons": reasons,
            "meaning": "Necessary pure-x Bravais-lattice closure only; not sufficient for a GB supercell, chemical-order repeat, or a selected PBC size."}


def _grain_count(candidate, mapping, side):
    """Return full conventional a[hkl] counts in one explicit direction basis."""
    grain = mapping.get("grains", {}).get(side, {})
    counts = candidate.get("crystal_counts", {}).get(side, {})
    convention = grain.get("source_convention", {})
    source_indices = _indices(counts.get("signed_indices_from_source"))
    source_count = counts.get("source_full_a_hkl_count")
    if counts.get("source_orientation_validated") and source_indices and _positive(source_count):
        return {"count": float(source_count), "direction": source_indices,
                "basis": "validated_source_direction", "count_source": "source_full_a_hkl_count"}
    if convention.get("accepted") and _indices(convention.get("signed_x_indices")):
        primitive = counts.get("measured_primitive_count")
        coefficient = convention.get("full_direction_translation_coefficient")
        if _positive(primitive) and _positive(coefficient):
            return {"count": float(primitive * coefficient),
                    "direction": _indices(convention["signed_x_indices"]),
                    "basis": "validated_source_direction", "count_source": "measured_primitive_count_times_validated_source_coefficient"}
    x_direction = grain.get("x_direction", {})
    # Explicitly rejected indexing must not be relabeled using an old count.
    if x_direction.get("accepted") is False or grain.get("pure_x_conversion_check", {}).get("accepted") is False:
        return {"count": None, "direction": None, "basis": "unresolved", "count_source": None}
    indices = _indices(counts.get("signed_indices_in_ptm_symmetry_branch")) or _indices(x_direction.get("signed_indices_in_ptm_branch"))
    count = counts.get("measured_full_a_hkl_count")
    count_source = "measured_full_a_hkl_count"
    if not _positive(count):
        primitive = counts.get("measured_primitive_count")
        coefficient = grain.get("shortest_translation_coefficient")
        if _positive(primitive) and _positive(coefficient):
            count = primitive * coefficient
            count_source = "measured_primitive_count_times_ptm_full_direction_coefficient"
        else:
            count = None
    if indices is None or not _positive(count):
        return {"count": None, "direction": indices, "basis": "unresolved", "count_source": None}
    return {"count": float(count), "direction": indices,
            "basis": "PTM_cubic_symmetry_branch", "count_source": count_source}


def _pair_targets(candidate):
    """Nominal lattice-pair targets remain distinct from measured translations."""
    result = []
    for pair in candidate.get("lattice_pairs", []):
        source = pair.get("source_direction_counts", {})
        directions = pair.get("source_signed_directions", {})
        valid_source = all(_positive(source.get(s)) and _indices(directions.get(s)) for s in SIDES)
        values = source if valid_source else pair.get("full_direction_counts", {})
        if all(_positive(values.get(s)) for s in SIDES):
            result.append({"upper_count": float(values["upper"]), "lower_count": float(values["lower"]),
                           "ratio_label": " : ".join(_display_number(values[s]) for s in SIDES),
                           "basis": "validated_source_direction" if valid_source else "PTM_cubic_symmetry_branch",
                           "relative_mismatch": pair.get("relative_mismatch"),
                           "mean_length_angstrom": pair.get("mean_length_angstrom"),
                           "meaning": "enumerated lattice-pair target; not the measured count label"})
    return result


def _group_intervals(values, tolerance):
    """Conservative complete-link groups: a group spans at most 2*tolerance."""
    groups = []
    for value in sorted(float(v) for v in values if _positive(v)):
        if groups and value - groups[-1][0] <= 2 * tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [(median(group), len(group)) for group in groups if len(group) >= 3]


def _local_features(row_features, events, tolerance):
    rows, strain = [], []
    for row in row_features.get("rows", []):
        if row.get("sampling_status") != "approximately_uniform_atom_row" or row.get("grain") not in SIDES:
            continue
        base = {"grain": row["grain"], "id": row.get("row_id", "row"), "quality": 1.0}
        for group in row.get("spacing_groups", []):
            center = group.get("median_angstrom")
            if group.get("count", 0) >= 3 and _positive(center) and group.get("standard_deviation_angstrom", 0) / center <= .15:
                rows.append({**base, "period_angstrom": center, "kind": "row_crest_spacing"})
        sequence = row.get("sequence", {})
        if sequence.get("status") != "predominantly_alternating_two_spacing_modes":
            continue
        center = sequence.get("ordinary_pair_median_angstrom")
        if _positive(center) and len(sequence.get("ordinary_pair_lengths_angstrom", [])) >= 3:
            rows.append({**base, "period_angstrom": center, "kind": "row_ordinary_pair"})
        for center, count in _group_intervals(sequence.get("event_spacings_angstrom", []), tolerance):
            rows.append({**base, "period_angstrom": center, "kind": "row_sequence_interruption", "count": count})
    channels = events.get("channels", [])
    if isinstance(channels, dict):
        channels = [{"id": key, **value} for key, value in channels.items()]
    for channel in channels:
        if channel.get("grain") not in SIDES:
            continue
        for mode in channel.get("spacing_modes", []):
            center = mode.get("center_angstrom")
            if not _positive(center) or mode.get("count", 0) < 3 or mode.get("relative_spread", 1) > .15:
                continue
            strain.append({"grain": channel["grain"], "id": channel.get("id", "strain_channel"),
                           "period_angstrom": center, "kind": "signed_strain_extrema_spacing",
                           "quality": 1.0 if mode.get("prominence_stable") else .5,
                           "prominence_stable": bool(mode.get("prominence_stable"))})
    return rows, strain


def _support(period, features, tolerance):
    matches = []
    per_grain = {side: 0.0 for side in SIDES}
    for feature in features:
        difference = abs(period - feature["period_angstrom"])
        if difference <= tolerance:
            value = feature["quality"] * math.exp(-.5 * (difference / tolerance) ** 2)
            matches.append({**feature, "difference_angstrom": difference, "association_score": value})
            per_grain[feature["grain"]] = max(per_grain[feature["grain"]], value)
    return {"score": sum(per_grain.values()) / 2, "per_grain": per_grain, "matches": matches}


def _short_reference(row_features):
    """Use a measured row motif, keeping the two grains equally weighted."""
    references = []
    for side in SIDES:
        pairs, crests = [], []
        for row in row_features.get("rows", []):
            if row.get("grain") != side or row.get("sampling_status") != "approximately_uniform_atom_row":
                continue
            sequence = row.get("sequence", {})
            pair = sequence.get("ordinary_pair_median_angstrom")
            if (sequence.get("status") == "predominantly_alternating_two_spacing_modes"
                    and _positive(pair) and len(sequence.get("ordinary_pair_lengths_angstrom", [])) >= 3):
                pairs.append(pair)
            modes = [m for m in row.get("spacing_groups", [])
                     if m.get("count", 0) >= 3 and _positive(m.get("median_angstrom"))
                     and m.get("standard_deviation_angstrom", 0) / m["median_angstrom"] <= .15]
            if modes:
                crests.append(max(modes, key=lambda m: m["count"])["median_angstrom"])
        values = pairs or crests
        if values:
            references.append({"grain": side, "period_angstrom": median(values),
                               "source": "ordinary_crest_pair" if pairs else "dominant_row_crest_spacing",
                               "row_count": len(values)})
    return references


def _nearby_short_eligibility(record, mapping):
    """A nearby label requires independently checked lattice and geometry evidence.

    The row wavelength is not used to derive a nominal lattice ratio. The ratio
    must already have passed the two-grain length check at the measured atomic
    translation, so widening the association window cannot manufacture a pair.
    """
    reasons = []
    for side in SIDES:
        grain = mapping.get("grains", {}).get(side, {})
        if grain.get("lattice") not in ("FCC", "BCC"):
            reasons.append(f"{side}_unsupported_lattice")
        if (grain.get("x_direction", {}).get("accepted") is not True
                or grain.get("pure_x_conversion_check", {}).get("accepted") is not True):
            reasons.append(f"{side}_unresolved_pure_x_indexing")
    metadata = record["display_lattice_metadata"]
    if metadata.get("nominal_counts") is None:
        reasons.append("no_independently_validated_nominal_pair")
    near, core = record["near_rms_over_nn"], record["defect_rms_over_nn"]
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in (near, core)):
        reasons.append("missing_coordinate_geometry")
    elif near > .20 or core > .25:
        reasons.append("coordinate_geometry_above_residual_limits")
    if "oblique_in_plane_candidate" in record.get("roles", []):
        reasons.append("oblique_only_translation")
    cycles = record.get("observable_cycles")
    if not _positive(cycles) or cycles < 3:
        reasons.append("fewer_than_three_verified_cycles")
    return reasons


def _short_period_annotations(records, row_features, nn, mapping):
    """Expose observed row motifs; never invent a short scale from a multiple."""
    references = _short_reference(row_features)
    tolerance = .3 * nn
    policy = {
        "meaning": "Observed short row motifs and their associated coordinate candidates; provisional structural scales, not necessarily FFT maxima.",
        "source": "Uniformly sampled atomic rows only; at least three ordinary crest-pair or dominant crest-spacing observations. No supplied target period or integer-multiple inference.",
        "association_tolerance_angstrom": tolerance,
        "association": "First associate within 0.3 NN. Otherwise search nearby within both 0.6 NN and 10% of the measured row motif; a nearby candidate is distinct from the measured motif, not an equality or FFT confirmation.",
        "nearby_requirements": "Accepted FCC/BCC pure-x indexing in both grains, a nominal lattice pair independently validated at the coordinate translation, near-GB RMS/NN <=0.20, core RMS/NN <=0.25, at least 3 observed cycles, and no oblique-only role.",
        "nearby_selection": "Nearest eligible candidate, then smaller translation and candidate id; all considered nearby alternatives and rejection reasons remain in the annotation. No target ratio is supplied.",
        "separate_grains": "Do not average upper/lower motifs separated by more than 0.3 NN into an unobserved intermediate period.",
        "none": "Missing or unresolved row evidence does not establish that a short structural period is absent.",
    }
    groups = []
    for reference in sorted(references, key=lambda r: r["period_angstrom"]):
        if groups and reference["period_angstrom"] - groups[-1][0]["period_angstrom"] <= tolerance:
            groups[-1].append(reference)
        else:
            groups.append([reference])
    result = []
    for group in groups:
        row_period = median(r["period_angstrom"] for r in group)
        matches = [r for r in records if abs(r["translation_angstrom"] - row_period) <= tolerance]
        # A row motif can survive without a lattice repeat. Prefer a physically
        # admissible association when several translations explain that motif.
        matches = [r for r in matches if r["lattice_repeat_check"]["eligible"]] or matches
        nearby_tolerance = min(.6 * nn, .10 * row_period)
        alternatives = []
        for item in records:
            difference = abs(item["translation_angstrom"] - row_period)
            if tolerance < difference <= nearby_tolerance:
                reasons = _nearby_short_eligibility(item, mapping)
                alternatives.append({
                    "candidate_id": item["candidate_id"], "coordinate_period_nm": item["translation_nm"],
                    "difference_angstrom": difference, "relative_difference": difference / row_period,
                    "display_lattice_label": item["display_lattice_label"],
                    "eligible": not reasons, "rejection_reasons": reasons,
                    "near_rms_over_nn": item["near_rms_over_nn"], "defect_rms_over_nn": item["defect_rms_over_nn"],
                    "nominal_pair_basis": item["display_lattice_metadata"].get("count_kind"),
                })
        if matches:
            association_kind = "within_tolerance"
        else:
            eligible_ids = {item["candidate_id"] for item in alternatives if item["eligible"]}
            matches = [item for item in records if item["candidate_id"] in eligible_ids]
            association_kind = "nearby_candidate" if matches else "unmatched"
        record = min(matches, key=lambda r: (abs(r["translation_angstrom"] - row_period),
                                             r["translation_angstrom"], r["candidate_id"])) if matches else None
        nearby = association_kind == "nearby_candidate"
        difference = abs(record["translation_angstrom"] - row_period) if record else None
        result.append({
            "candidate_id": record["candidate_id"] if record else None,
            "lattice_repeat_status": record["lattice_repeat_check"]["status"] if record else "unresolved",
            "pure_x_repeat_eligible": bool(record and record["lattice_repeat_check"]["eligible"]),
            "period_nm": record["translation_nm"] if record and not nearby else row_period / 10,
            "row_period_nm": row_period / 10,
            "coordinate_period_nm": record["translation_nm"] if record else None,
            "association_kind": association_kind,
            "association_difference_angstrom": difference,
            "association_relative_difference": difference / row_period if record else None,
            "association": {
                "kind": association_kind,
                "within_tolerance_angstrom": tolerance,
                "nearby_search_tolerance_angstrom": nearby_tolerance,
                "nearby_alternatives": alternatives,
                "nearby_eligible_count": sum(item["eligible"] for item in alternatives),
                "nearby_considered_count": len(alternatives),
                "coordinate_geometry": {"near_rms_over_nn": record["near_rms_over_nn"],
                                        "defect_rms_over_nn": record["defect_rms_over_nn"]} if record else None,
                "nominal_pair_validation": record["display_lattice_metadata"] if record else None,
            },
            "kind": "short_row_motif_with_nearby_coordinate_candidate" if nearby else
                    "short_coordinate_row_motif" if record else "short_row_motif_without_coordinate_association",
            "label": "Short motif / nearby lattice candidate" if nearby else
                     "Short structural candidate" if record else "Short row motif",
            "evidence": ["repeated_native_coordinate_row_motif"], "row_references": group,
            "display_lattice_label": record["display_lattice_label"] if record else "unindexed row motif",
            "reason": "A nearby coordinate translation has an independently validated lattice pair and acceptable atomic matching. Its length differs from the observed row motif; the two are not asserted to be the same physical period."
                      if nearby else
                      "Repeated atomic-row motif agrees with the coordinate translation; a matching FFT peak is not required."
                      if record else "Repeated atomic-row motif has no coordinate candidate within the association tolerance; no lattice ratio assigned.",
        })
    status = ("supported_coordinate_candidates" if any(r["candidate_id"] is not None for r in result)
              else "row_motifs_without_coordinate_association" if result else "short_reference_unresolved")
    return result, policy, status


def _long_period_annotations(candidates, records, row_features, events, nn):
    """Display evidence for longer scales without declaring a unique period.

    A Fourier harmonic or an integer multiple by itself is not sufficient.
    Stronger geometry-only recurrences are labeled separately from local event
    spacings, including field intervals that do not pass pure-x registration.
    """
    references = _short_reference(row_features)
    short = median(r["period_angstrom"] for r in references) if references else None
    tolerance = .3 * nn
    policy = {
        "meaning": "Provisional longer-scale labels for review, not confirmed secondary dislocations or PBC dimensions.",
        "short_reference": references,
        "short_reference_angstrom": short,
        "short_reference_method": "Per grain: median ordinary crest-pair length from uniformly sampled alternating rows; otherwise median dominant row-crest spacing. Equal weight per grain.",
        "scale_separation": "At least twice the measured short reference, allowing 0.3 NN association tolerance. No integer-ratio requirement.",
        "coverage": "At least 3 observed cycles, using the minimum of reported candidate cycles and available spatial window / period. Missing coverage does not pass.",
        "local_evidence": "A repeated coordinate-row interruption interval, or prominence-stable strain-spacing modes in both grains; local modes require at least 3 observations and relative spread <= 0.15.",
        "geometry_supported_local_repeat": "Near-GB RMS/NN <= 0.20 and non-bulk RMS/NN <= 0.25 with no oblique-only role. Otherwise retain a field/row interval without calling it a pure-x lattice repeat.",
        "coordinate_only": "Near-GB RMS/NN <= 0.10, core RMS/NN <= 0.15, and mean residual >=20% lower than the nearest distinct candidates on both sides (> half a short reference away). No strain confirmation required.",
        "repeated_cell_guard": "A geometry-only candidate is not a new longer scale if a shorter integer-related candidate has both residuals within 20% of its residuals. Multiples alone do not establish modulation.",
        "none": "No supported longer scale is not proof that none exists; the window or current evidence may be insufficient.",
    }
    if not short:
        return [], policy, "short_reference_unresolved"
    x = [v for v in events.get("x_angstrom", []) if isinstance(v, (int, float)) and math.isfinite(v)]
    span = max(x) - min(x) if len(x) >= 2 else None
    records_by_id = {r["candidate_id"]: r for r in records}
    ordered = sorted(candidates, key=lambda c: c["x_translation_angstrom"])
    result = []
    for candidate in ordered:
        period = candidate["x_translation_angstrom"]
        record = records_by_id[candidate["id"]]
        reported = candidate.get("observable_cycles")
        coverage = [v for v in (reported, span / period if span is not None else None)
                    if isinstance(v, (int, float)) and math.isfinite(v) and v >= 0]
        cycles = min(coverage) if coverage else None
        if period + tolerance < 2 * short or cycles is None or cycles < 3:
            continue
        near, core = record["near_rms_over_nn"], record["defect_rms_over_nn"]
        valid_geometry = all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in (near, core))
        oblique = "oblique_in_plane_candidate" in candidate.get("roles", [])
        row_matches = [m for m in record["row_support"]["matches"] if m["kind"] == "row_sequence_interruption"]
        strain_matches = [m for m in record["strain_support"]["matches"] if m.get("prominence_stable")]
        strain_grains = {m["grain"] for m in strain_matches}
        local = bool(row_matches) or strain_grains == set(SIDES)
        evidence = []
        if row_matches:
            evidence.append("repeated_coordinate_row_interruptions")
        if strain_grains == set(SIDES):
            evidence.append("stable_local_strain_intervals_in_both_grains")
        if local:
            good = (valid_geometry and near <= .20 and core <= .25 and not oblique
                    and record["lattice_repeat_check"]["eligible"])
            kind = "longer_local_repeat" if good else "longer_field_interval"
            label = "Long candidate" if good else "Long field interval"
            reason = ("Repeated local spacing agrees with this coordinate candidate and passes the declared pure-x residual limits."
                      if good else "Repeated local spacing is present, but pure-x lattice closure or geometry is unverified; this is not a confirmed PBC repeat.")
        else:
            if not valid_geometry or near > .10 or core > .15 or oblique:
                continue
            previous = [r for r in records if r["translation_angstrom"] < period - .5 * short]
            following = [r for r in records if r["translation_angstrom"] > period + .5 * short]
            if not previous or not following:
                continue
            neighbors = [max(previous, key=lambda r: r["translation_angstrom"]),
                         min(following, key=lambda r: r["translation_angstrom"])]
            neighbor_residuals = [(r["near_rms_over_nn"], r["defect_rms_over_nn"]) for r in neighbors]
            if not all(all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in pair)
                       for pair in neighbor_residuals):
                continue
            if (near + core) > .8 * min(sum(pair) for pair in neighbor_residuals):
                continue
            repeated_cell = False
            for earlier in previous:
                multiplier = round(period / earlier["translation_angstrom"])
                ratios_match = multiplier >= 2 and abs(period - multiplier * earlier["translation_angstrom"]) <= tolerance
                residuals = earlier["near_rms_over_nn"], earlier["defect_rms_over_nn"]
                if ratios_match and all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in residuals):
                    repeated_cell |= residuals[0] <= 1.2 * near and residuals[1] <= 1.2 * core
            if repeated_cell:
                continue
            kind, label = "longer_coordinate_recurrence", "Long coordinate candidate"
            evidence = ["pronounced_pure_x_coordinate_recurrence_minimum"]
            reason = "Strong atomic-pattern recurrence relative to neighboring translations; independent local strain/row modulation is not established."
        result.append({"candidate_id": candidate["id"], "period_nm": period / 10,
                       "lattice_repeat_status": record["lattice_repeat_check"]["status"],
                       "pure_x_repeat_eligible": record["lattice_repeat_check"]["eligible"],
                       "display_lattice_label": record["display_lattice_label"],
                       "kind": kind, "label": label, "evidence": evidence, "reason": reason,
                       "short_reference_nm": short / 10, "observable_cycles": cycles,
                       "near_rms_over_nn": near, "defect_rms_over_nn": core,
                       "row_interruption_matches": row_matches, "strain_spacing_matches": strain_matches})
    return result, policy, "supported_candidates" if result else "no_supported_longer_scale"


def build_candidate_annotations(candidates, mapping, row_features, events, nn):
    """Return all labels, ranking details, a highlighted id, and the full policy.

    Inputs are existing JSON dictionaries; ``candidates`` may be the list or its
    parent coordinate report. No file name, expected period, or supplied ratio
    contributes to the display priority.
    """
    if not _positive(nn):
        raise ValueError("nn must be a finite positive measured nearest-neighbor distance")
    if isinstance(candidates, dict):
        candidates = candidates["candidates"]
    mapping, row_features, events = mapping or {}, row_features or {}, events or {}
    tolerance = .3 * nn
    row_evidence, strain_evidence = _local_features(row_features, events, tolerance)
    policy = {
        "meaning": "Priority for review based on local repeat evidence; heuristic, not a probability or a selected physical period.",
        "weights": {"coordinate_row_local_spacing": .4, "strain_local_spacing": .3, "xyz_geometry": .3},
        "local_association_tolerance_angstrom": tolerance,
        "local_association": "Within 0.3 NN; exp(-0.5*(distance/tolerance)^2). Keep maximum per grain, average over both grains; channels are correlated, not extra votes.",
        "local_quality": "At least 3 observations; strain relative spread <= 0.15. Prominence-stable strain modes weight 1; otherwise 0.5. Only uniformly sampled coordinate rows.",
        "geometry": "Mean of 1/(1+(RMS/NN/0.15)^2) for near-GB and non-bulk populations.",
        "highlight_eligibility": "Validated pure-x FCC/BCC lattice closure in both grains, positive local row or strain support, both finite geometry residuals, near-GB RMS/NN <= 0.20, at least 3 observable cycles when reported, and no oblique-only role.",
        "tie_break": "Equal score to 8 decimals: smaller physical translation first, then candidate id.",
        "count_labels": "Upper : lower full a[hkl] coefficients. Display a nominal enumerated pair or allowed FCC/BCC translation-grid pair only when each independently predicted grain length lies within 0.3 NN of the coordinate candidate. Otherwise prefix measured counts with approximation sign; exact measured counts remain separate.",
        "indexing": "Validated source orientation when present; otherwise nonnegative descending-index cubic-symmetry representatives for compact display. Original signed PTM directions are preserved in metadata; unresolved directions stay unresolved.",
    }
    records = []
    for candidate in sorted(candidates, key=lambda c: c["x_translation_angstrom"]):
        period = candidate["x_translation_angstrom"]
        sides = {side: _grain_count(candidate, mapping, side) for side in SIDES}
        lattice_label, lattice_metadata = _lattice_display(candidate, mapping, sides, tolerance)
        lattice_check = _lattice_repeat_check(candidate, mapping, sides, lattice_metadata, tolerance)
        complete = all(sides[s]["count"] is not None and sides[s]["direction"] is not None for s in SIDES)
        near = candidate.get("geometry", {}).get("near", {}).get("rms_over_nn")
        defects = candidate.get("geometry", {}).get("defects", {}).get("rms_over_nn")
        geometry_valid = all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in (near, defects))
        geometry_score = sum(1 / (1 + (v / .15) ** 2) for v in (near, defects)) / 2 if geometry_valid else 0.0
        row = _support(period, row_evidence, tolerance)
        strain = _support(period, strain_evidence, tolerance)
        score = .4 * row["score"] + .3 * strain["score"] + .3 * geometry_score
        oblique = "oblique_in_plane_candidate" in candidate.get("roles", [])
        eligible = (geometry_valid and near <= .20 and max(row["score"], strain["score"]) > 0
                    and candidate.get("observable_cycles", 3) >= 3 and not oblique and lattice_check["eligible"])
        records.append({
            "candidate_id": candidate["id"], "translation_angstrom": period, "translation_nm": period / 10,
            "upper": sides["upper"], "lower": sides["lower"],
            "upper_direction_label": _direction_label(sides["upper"]["direction"]),
            "lower_direction_label": _direction_label(sides["lower"]["direction"]),
            "direction_ratio_label": f"upper {_direction_label(sides['upper']['direction'])} : lower {_direction_label(sides['lower']['direction'])}",
            "ratio_label": ("≈ " + " : ".join(_display_number(sides[s]["count"]) for s in SIDES)) if complete else "unindexed",
            "ratio_meaning": "measured full-a[hkl] counts, upper : lower; pure x component only" if oblique else "measured full-a[hkl] counts, upper : lower",
            "display_lattice_label": lattice_label, "display_lattice_metadata": lattice_metadata,
            "lattice_repeat_check": lattice_check,
            "display_direction_ratio_label": "upper " + _compact_direction(lattice_metadata["display_directions"]["upper"])
                                             + " : lower " + _compact_direction(lattice_metadata["display_directions"]["lower"]),
            "nominal_lattice_pairs": _pair_targets(candidate),
            "near_rms_over_nn": near, "defect_rms_over_nn": defects,
            "roles": candidate.get("roles", []), "observable_cycles": candidate.get("observable_cycles"),
            "row_support": row, "strain_support": strain, "geometry_score": geometry_score,
            "highlight_score": score, "highlight_eligible": bool(eligible), "highlighted": False,
        })
    eligible = [record for record in records if record["highlight_eligible"]]
    best = min(eligible, key=lambda r: (-round(r["highlight_score"], 8), r["translation_angstrom"], r["candidate_id"])) if eligible else None
    if best:
        best["highlighted"] = True
        reason = (f"{best['candidate_id']}: local-repeat display score {best['highlight_score']:.3f} = "
                  f"0.4×row {best['row_support']['score']:.3f} + 0.3×strain {best['strain_support']['score']:.3f} "
                  f"+ 0.3×geometry {best['geometry_score']:.3f}. This is visual emphasis, not a unique or confirmed period.")
    else:
        reason = "No candidate meets the lattice-closure, local-evidence and geometry requirements for a defensible highlight; raw hypotheses remain visible."
    direction_labels = sorted(set(record["direction_ratio_label"] for record in records))
    basis = sorted(set(record[side]["basis"] for record in records for side in SIDES))
    glossary = {
        "direction_counts": "Labels use full conventional a[hkl] coefficients, upper : lower. Nominal lattice pairs require <=0.3 NN length error in each grain; approximate measured labels use ≈. Original relaxed counts are retained in JSON.",
        "direction_basis": "Validated source directions take precedence. Otherwise compact nonnegative cubic-symmetry representatives are used, preserving original signed PTM directions in JSON; these are not recovered builder orientations.",
        "near_GB_atoms": "Atoms in the surface-excluded interface neighborhood, including the non-bulk core and adjacent crystalline atoms; used to match the complete local pattern.",
        "non_bulk_core_atoms": "Interior interface atoms not assigned to the dominant bulk lattice by PTM; a structural seed set, not all physical GB atoms and not automatically dislocations.",
        "NN": f"NN = measured nearest-neighbor distance ({nn:.4f} Å here); RMS/NN is a dimensionless mismatch. Smaller means a closer translated atomic-pattern match.",
        "highlight": "The highlighted candidate has the highest declared review-priority score among eligible local repeats; it is not a probability, a unique physical period, or a user selection.",
        "long_periods": "Long labels indicate evidence for a scale larger than the measured row motif. Coordinate recurrence, local modulation, and field intervals are different evidence; none alone confirms a secondary dislocation or a PBC cell.",
        "short_periods": "Short labels identify measured atomic-row motifs. Associations within 0.3 NN are shown normally; nearby validated lattice candidates within both 0.6 NN and 10% retain their own distinct coordinate length. Neither association claims a Fourier maximum or equality of the two lengths.",
    }
    short_annotations, short_policy, short_status = _short_period_annotations(records, row_features, nn, mapping)
    long_annotations, long_policy, long_status = _long_period_annotations(candidates, records, row_features, events, nn)
    lattice_filter = {
        "compatible_count": sum(r["lattice_repeat_check"]["eligible"] for r in records),
        "distinct_pair_count": len({tuple((tuple(r["lattice_repeat_check"]["grains"][side]["direction"]),
                                            r["lattice_repeat_check"]["nominal_counts"][side])
                                           for side in SIDES)
                                    for r in records if r["lattice_repeat_check"]["eligible"]}),
        "incompatible_count": sum(r["lattice_repeat_check"]["status"] == "incompatible" for r in records),
        "unresolved_count": sum(r["lattice_repeat_check"]["status"] == "unresolved" for r in records),
        "oblique_count": sum(r["lattice_repeat_check"]["status"] == "requires_full_vector_check" for r in records),
        "total_count": len(records), "length_tolerance_angstrom": tolerance,
        "rule": "For reduced [uvw]: FCC half-integer steps iff u+v+w is even; BCC half-integer steps iff all three are odd; otherwise integer steps. Divide by gcd for unreduced indices.",
        "scope": "Filter reported pure-x lattice-repeat candidates, not measured FFT wavelengths, spatial intervals or row motifs. Compatible means necessary closure only; geometry, mismatch and user choice still apply.",
    }
    return {"records": records, "highlighted_id": best["candidate_id"] if best else None,
            "lattice_filter": lattice_filter,
            "reason": reason, "policy": policy, "direction_labels": direction_labels,
            "direction_bases": basis, "glossary": glossary,
            "short_period_annotations": short_annotations, "short_period_policy": short_policy,
            "short_period_status": short_status,
            "long_period_annotations": long_annotations, "long_period_policy": long_policy,
            "long_period_status": long_status}
