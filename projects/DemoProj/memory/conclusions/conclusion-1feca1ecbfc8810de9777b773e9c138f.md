{
  "status": "ok",
  "error": null,
  "summary": "Completed stress-drop analysis for reverse-unloading run **`shear-20260927-044521-841236`**.\n\n- **Derived run:** `stress-strain-20260927-054451-fe8285`\n- **Source relationship:** `stress_strain_analysis` of `gb_shear` source `shear-20260927-044521-841236`\n- **Points:** all 71 finite points, steps 0–70\n- **Signal:** signed `top_shear_stress_mpa`; absolute stress used for detection/magnitude\n- **Extrema:** unsmoothed local extrema, tolerance 0 MPa\n- **Threshold:** automatic MAD threshold = **18.471859 MPa**\n- **Events:** 18 combined extrema events; **17 complete**, 1 unresolved terminal descent\n- **Support:** all 18 were `extrema_only`; no threshold-supported events\n- **Nonconverged point:** retained in the analysis and flagged; it is step **60**. No event points below are filtered.\n\nThe authoritative event record is:\n`artifacts/analysis/stress_strain/stress-strain-20260927-054451-fe8285/stress_strain_analysis.json`\n\n### Complete stress-drop events\n\nAll listed stresses are signed `top_shear_stress_mpa`. The downstream zero-load minimization candidates are the **post-minimum configurations** in the final column. Pre-peak CFGs are indexed at the corresponding pre-step in the same `intermediate_structure` role; post-step artifact IDs are exact.\n\n| Event | Pre-peak step: strain, stress (MPa), conv. | Post-minimum step: strain, stress (MPa), conv. | Drop (MPa) | Exact post-event artifact |\n|---:|---|---|---:|---|\n| 1 | 4: 0.016500, −19.413221, true | 6: 0.016000, +0.325608, true | 19.087612 | `008a651a-140f-543f-84c4-fc22a8dca0de`; `structures/gb_shear_step_0006.cfg` |\n| 2 | 8: 0.015500, −20.652428, true | 10: 0.015000, −1.738086, true | 18.914342 | `55732240-eb6e-568d-8977-eeecb2305add`; `structures/gb_shear_step_0010.cfg` |\n| 3 | 12: 0.014500, −21.255509, true | 14: 0.014000, −3.792273, true | 17.463236 | `acdb0652-cd3c-5f7b-b8a4-20d113a7e49d`; `structures/gb_shear_step_0014.cfg` |\n| 4 | 16: 0.013500, +16.905573, true | 18: 0.013000, −5.831205, true | 11.074368 | `47a71f90-068d-5773-93f3-fc219faa8f95`; `structures/gb_shear_step_0018.cfg` |\n| 5 | 19: 0.012750, −16.728677, true | 21: 0.012250, +3.833484, true | 12.895193 | `1a6d03fd-5619-564d-97f2-af8dae191d4b`; `structures/gb_shear_step_0021.cfg` |\n| 6 | 23: 0.011750, −18.371480, true | 25: 0.011250, +1.769289, true | 16.602190 | `69a9c82a-f885-5f89-a0d8-69c5712dea53`; `structures/gb_shear_step_0025.cfg` |\n| 7 | 27: 0.010750, −19.824673, true | 29: 0.010250, −0.297322, true | 19.527351 | `e0ab20ca-33ee-5351-afb9-1d3134f26f77`; `structures/gb_shear_step_0029.cfg` |\n| 8 | 31: 0.009750, −20.932384, true | 33: 0.009250, −2.358585, true | 18.573799 | `b5bb06ec-94ad-5272-a958-643d748fc68a`; `structures/gb_shear_step_0033.cfg` |\n| 9 | 35: 0.008750, −21.120630, true | 37: 0.008250, −4.408550, true | 16.712080 | `8d7cfbdc-7e75-5e27-9d7e-1f5ca79bdc75`; `structures/gb_shear_step_0037.cfg` |\n| 10 | 39: 0.007750, +16.384566, true | 40: 0.007500, +5.271683, true | 11.112883 | `b157e759-93d5-5c54-aeed-d1e3f09ad520`; `structures/gb_shear_step_0040.cfg` |\n| 11 | 42: 0.007000, −17.238946, true | 44: 0.006500, +3.212389, true | 14.026558 | `e7265f04-5c62-5f00-9163-d2f1a16bc7d7`; `structures/gb_shear_step_0044.cfg` |\n| 12 | 46: 0.006000, −18.833998, true | 48: 0.005500, +1.146595, true | 17.687403 | `f11b6c1b-c865-5d2e-b4b8-086a9e0548ab`; `structures/gb_shear_step_0048.cfg` |\n| 13 | 50: 0.005000, −20.206324, true | 52: 0.004500, −0.919269, true | 19.287056 | `fb7f6fc4-fd3f-559f-9db4-ac53d9b97af6`; `structures/gb_shear_step_0052.cfg` |\n| 14 | 54: 0.004000, −21.142550, true | 56: 0.003500, −2.977771, true | 18.164779 | `3fd39c34-bbbd-50d7-a7cc-dd8a7c055bc0`; `structures/gb_shear_step_0056.cfg` |\n| 15 | 58: 0.003000, −20.514532, true | 60: 0.002500, −5.023402, **false** | 15.491130 | `c7aebc60-fc3c-53b9-85ab-1dece99c5a80`; `structures/gb_shear_step_0060.cfg` |\n| 16 | 61: 0.002250, −16.039839, true | 63: 0.001750, +4.652441, true | 11.387398 | `150f80cd-4b8f-5a99-95cc-60ea67723d95`; `structures/gb_shear_step_0063.cfg` |\n| 17 | 65: 0.001250, −17.736767, true | 67: 0.000750, +2.589809, true | 15.146958 | `71ed1ef5-b7df-516c-9fa7-fbe052374c05`; `structures/gb_shear_step_0067.cfg` |\n\nThe exact project-relative post-event paths share the source directory:\n\n`artifacts/lammps/shear/shear-20260927-044521-841236/<path shown above>`\n\n### Separately requested final endpoint\n\nStep **70** is not a complete stress-drop event: it is an unresolved terminal descent because no subsequent minimum is observed.\n\n- Step: **70**\n- Cumulative strain: **−1.0408340855860843×10⁻¹⁷**, effectively zero\n- Signed top shear stress: **+12.105451 MPa**\n- Convergence: **true**\n- Exact indexed artifact ID: `3daedc1b-6f67-5059-8a67-6d98683f4caf`\n- Path: `artifacts/lammps/shear/shear-20260927-044521-841236/structures/gb_shear_step_0070.cfg`\n\nThus the requested downstream batch should contain **18 CFGs**: post-event steps  \n**6, 10, 14, 18, 21, 25, 29, 33, 37, 40, 44, 48, 52, 56, 60, 63, 67**, plus final endpoint **70**. Step 60 must retain its **nonconverged warning**.\n\nThe requested displacement-plan-view reference is the original minimized nominal-zero-strain P012 structure:\n\n`minimize-20260927-040247-4d2144` / role `minimized_cfg`.\n\nDiagnostic plot:\n`artifacts/analysis/stress_strain/stress-strain-20260927-054451-fe8285/stress_strain_curve.png`\n\nConclusion fact:\n`run:stress-strain-20260927-054451-fe8285:main_conclusion`\n\nNo mechanism is inferred from the stress curve.",
  "data": {
    "completion_check": {
      "outcome": "completed",
      "verified_run_ids": [
        "stress-strain-20260927-054451-fe8285"
      ],
      "missing_artifact_roles": [],
      "failures": []
    },
    "runs": [
      {
        "status": "completed",
        "result": {
          "summary": "Threshold: 0; extrema: 18; combined: 18 candidate drops. Segment 1: 17 complete, 1 unresolved; first complete drop 19.0876 MPa; 16 subsequent drops, median 16.6571 MPa (range 11.0744 to 19.5274).",
          "data": "artifacts/analysis/stress_strain/stress-strain-20260927-054451-fe8285/stress_strain_analysis.json",
          "source_run_id": "shear-20260927-044521-841236",
          "memory_fact_refs": [
            {
              "source_run_id": "stress-strain-20260927-054451-fe8285",
              "fact_id": "f5eeb270-1c51-5924-ac56-029db930169b",
              "kind": "scientific_conclusion",
              "lookup_key": "run:stress-strain-20260927-054451-fe8285:main_conclusion"
            }
          ],
          "conclusion_lookup_key": "run:stress-strain-20260927-054451-fe8285:main_conclusion",
          "conclusion_fact_id": "f5eeb270-1c51-5924-ac56-029db930169b",
          "combined_event_count": 18,
          "combined_events": [
            {
              "descent_end_step": 6,
              "descent_start_step": 4,
              "drop_amplitude_basis": "peak_to_valley",
              "event_complete": true,
              "event_end_step": 6,
              "event_end_strain": 0.016000000000000004,
              "event_index": 1,
              "event_step": 5,
              "event_strain": 0.016249999999999997,
              "extrema_event_index": 1,
              "largest_single_step_drop_at_step": 6,
              "largest_single_step_drop_mpa": 11.592918836368153,
              "loading_cycle": 1,
              "loading_direction": "-x",
              "loading_half_cycle": 1,
              "detail_fields_available": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 18
              }
            },
            {
              "descent_end_step": 10,
              "descent_start_step": 8,
              "drop_amplitude_basis": "peak_to_valley",
              "event_complete": true,
              "event_end_step": 10,
              "event_end_strain": 0.015000000000000006,
              "event_index": 2,
              "event_step": 9,
              "event_strain": 0.01525,
              "extrema_event_index": 2,
              "largest_single_step_drop_at_step": 9,
              "largest_single_step_drop_mpa": 10.708543821639731,
              "loading_cycle": 1,
              "loading_direction": "-x",
              "loading_half_cycle": 1,
              "detail_fields_available": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 18
              }
            },
            {
              "descent_end_step": 14,
              "descent_start_step": 12,
              "drop_amplitude_basis": "peak_to_valley",
              "event_complete": true,
              "event_end_step": 14,
              "event_end_strain": 0.014000000000000002,
              "event_index": 3,
              "event_step": 13,
              "event_strain": 0.014249999999999995,
              "extrema_event_index": 3,
              "largest_single_step_drop_at_step": 13,
              "largest_single_step_drop_mpa": 13.325475522495573,
              "loading_cycle": 1,
              "loading_direction": "-x",
              "loading_half_cycle": 1,
              "detail_fields_available": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 18
              }
            },
            {
              "omitted_items": 15,
              "total_items": 18,
              "read_from": "full_output"
            }
          ],
          "curve_summary": {
            "summary": "Threshold: 0; extrema: 18; combined: 18 candidate drops. Segment 1: 17 complete, 1 unresolved; first complete drop 19.0876 MPa; 16 subsequent drops, median 16.6571 MPa (range 11.0744 to 19.5274).",
            "descriptive_only": true,
            "event_basis": "combined_events",
            "interpretation_notes": [
              "Complete-event statistics require a confirmed maximum and subsequent minimum in the same loading segment.",
              "The first complete event may follow an unresolved initial descent; inspect first_complete_is_first_observed_event.",
              "Unresolved and threshold-only events remain in the event lists but are excluded from complete-event distributions.",
              "Similar peak or minimum values do not merge events at different steps.",
              "Small local extrema may reflect numerical fluctuations; compare both detectors and the saved structures.",
              "A larger first drop or narrowly distributed later drops alone does not establish structural change or a stable structural state."
            ],
            "segments": [
              {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 16
              }
            ],
            "support_counts": {
              "both": 0,
              "extrema_only": 18,
              "threshold_only": 0
            }
          },
          "detection": {
            "convergence_affects_analysis": false,
            "definition": "chronological union of threshold and peak-to-valley detections matched within the same descent",
            "direction_boundaries_compared": false,
            "equal_height_events_merged": false,
            "extrema_detection": {
              "adjacent_slope_sign": "informational sign of adjacent difference with tolerance_mpa deadband",
              "amplitude_cutoff_mpa": null,
              "definition": "positive-to-negative progression slope maximum paired with next negative-to-positive minimum",
              "direction_boundaries_compared": false,
              "endpoints_are_extrema": false,
              "extrema_slope_sign": "direction between bounded plateau blocks; zero within a block",
              "flat_definition": "consecutive samples coalesced while total signal range <= tolerance_mpa; restart block when range is exceeded",
              "half_cycle_boundaries_compared": false,
              "method": "local_extrema",
              "missing_or_zero_strain": "use signal difference sign; numerical slopes are null",
              "nonconverged_points_included": true,
              "plateau_representative": "extreme signal value within bounded plateau block, last sample on ties",
              "post_frame_selection": "at_or_after_target_within_segment_or_null",
              "slope_definition": "signal difference / absolute actual-strain difference along point order",
              "smoothing_applied": false,
              "detail_fields_available": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 3
              }
            },
            "half_cycle_boundaries_compared": false,
            "matching_basis": "same segment; threshold drop interval contained in extrema descent interval",
            "method": "combined",
            "nonconverged_points_included": true,
            "post_frame_selection": "at_or_after_target_within_segment_or_null",
            "threshold_detection": {
              "consecutive_drops_merged": true,
              "convergence_affects_analysis": false,
              "definition": "abrupt decrease in absolute stress between adjacent points",
              "direction_boundaries_compared": false,
              "frame_selection": "legacy_nearest_frame_may_precede_target",
              "half_cycle_boundaries_compared": false,
              "mad_mpa": 1.6717190308077772,
              "median_absolute_step_change_mpa": 9.797142161904748,
              "method": "automatic_mad",
              "nonconverged_points_included": true,
              "robust_sigma_mpa": 2.47849063507561,
              "sensitivity": 3.5,
              "signal_range_mpa": 20.958187925894492,
              "threshold_mpa": 18.471859384669386
            }
          },
          "event_count": 18,
          "excluded_nonconverged_point_count": 0,
          "extrema_detection": {
            "adjacent_slope_sign": "informational sign of adjacent difference with tolerance_mpa deadband",
            "amplitude_cutoff_mpa": null,
            "definition": "positive-to-negative progression slope maximum paired with next negative-to-positive minimum",
            "direction_boundaries_compared": false,
            "endpoints_are_extrema": false,
            "extrema_slope_sign": "direction between bounded plateau blocks; zero within a block",
            "flat_definition": "consecutive samples coalesced while total signal range <= tolerance_mpa; restart block when range is exceeded",
            "half_cycle_boundaries_compared": false,
            "method": "local_extrema",
            "missing_or_zero_strain": "use signal difference sign; numerical slopes are null",
            "nonconverged_points_included": true,
            "plateau_representative": "extreme signal value within bounded plateau block, last sample on ties",
            "post_frame_selection": "at_or_after_target_within_segment_or_null",
            "slope_definition": "signal difference / absolute actual-strain difference along point order",
            "smoothing_applied": false,
            "detail_fields_available": [
              "tolerance_mpa",
              "tolerance_scope",
              "trailing_descent"
            ]
          },
          "extrema_event_count": 18,
          "extrema_events": [
            {
              "descent_end_step": 6,
              "descent_start_step": 4,
              "drop_amplitude_basis": "peak_to_valley",
              "event_complete": true,
              "event_end_step": 6,
              "event_end_strain": 0.016000000000000004,
              "event_index": 1,
              "event_step": 5,
              "event_strain": 0.016249999999999997,
              "extrema_event_index": 1,
              "largest_single_step_drop_at_step": 6,
              "largest_single_step_drop_mpa": 11.592918836368153,
              "loading_cycle": 1,
              "loading_direction": "-x",
              "loading_half_cycle": 1,
              "detail_fields_available": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 18
              }
            },
            {
              "descent_end_step": 10,
              "descent_start_step": 8,
              "drop_amplitude_basis": "peak_to_valley",
              "event_complete": true,
              "event_end_step": 10,
              "event_end_strain": 0.015000000000000006,
              "event_index": 2,
              "event_step": 9,
              "event_strain": 0.01525,
              "extrema_event_index": 2,
              "largest_single_step_drop_at_step": 9,
              "largest_single_step_drop_mpa": 10.708543821639731,
              "loading_cycle": 1,
              "loading_direction": "-x",
              "loading_half_cycle": 1,
              "detail_fields_available": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 18
              }
            },
            {
              "descent_end_step": 14,
              "descent_start_step": 12,
              "drop_amplitude_basis": "peak_to_valley",
              "event_complete": true,
              "event_end_step": 14,
              "event_end_strain": 0.014000000000000002,
              "event_index": 3,
              "event_step": 13,
              "event_strain": 0.014249999999999995,
              "extrema_event_index": 3,
              "largest_single_step_drop_at_step": 13,
              "largest_single_step_drop_mpa": 13.325475522495573,
              "loading_cycle": 1,
              "loading_direction": "-x",
              "loading_half_cycle": 1,
              "detail_fields_available": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 18
              }
            },
            {
              "omitted_items": 15,
              "total_items": 18,
              "read_from": "full_output"
            }
          ],
          "detail_fields_available": [
            "events",
            "local_extrema",
            "memory_publication",
            "nonconverged_point_count",
            "plot",
            "point_count",
            "source_fingerprint",
            "source_snapshot",
            {
              "omitted_items": 6,
              "total_items": 14,
              "read_from": "full_output"
            }
          ]
        },
        "run_id": "stress-strain-20260927-054451-fe8285",
        "workflow": "stress_strain_analysis",
        "manifest": "artifacts/analysis/stress_strain/stress-strain-20260927-054451-fe8285/manifest.json",
        "conclusion_lookup_key": "run:stress-strain-20260927-054451-fe8285:main_conclusion",
        "full_output": {
          "run_id": "stress-strain-20260927-054451-fe8285",
          "tool": "query_project_memory",
          "entity": "runs"
        },
        "artifact_roles": [
          "source_metadata_snapshot",
          "stress_strain_data",
          "stress_strain_plot"
        ]
      }
    ]
  },
  "artifacts": [
    "artifacts/analysis/stress_strain/stress-strain-20260927-054451-fe8285/manifest.json"
  ]
}