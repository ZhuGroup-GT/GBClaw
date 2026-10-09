{
  "status": "ok",
  "error": null,
  "summary": "Completed stress-drop analysis.\n\n- **Derived run:** `stress-strain-20260927-044120-a8c0ed`\n- **Source shear run:** `shear-20260927-040956-e24773`\n- **Parent minimized GB run:** `minimize-20260927-040247-4d2144`\n- **Input:** full 71-point history, steps 0–70; all finite points included\n- **Signal:** `top_shear_stress_mpa`, absolute-stress convention\n- **Detection:** automatic robust MAD threshold, sensitivity 3.5\n- **Threshold:** 49.1845 MPa\n- **Nonconverged points:** 0; no points filtered\n- **Events:** 1 threshold event, 9 extrema events, 9 combined events\n- **PNG:** `artifacts/analysis/stress_strain/stress-strain-20260927-044120-a8c0ed/stress_strain_curve.png`\n- **Reusable JSON:** `artifacts/analysis/stress_strain/stress-strain-20260927-044120-a8c0ed/stress_strain_analysis.json`\n- **Manifest:** `artifacts/analysis/stress_strain/stress-strain-20260927-044120-a8c0ed/manifest.json`\n- **Conclusion:** `run:stress-strain-20260927-044120-a8c0ed:main_conclusion`\n\n### Peak and combined stress-drop events\n\nAll event frames were available exactly; no nearest-frame approximation was required. Peak/end values use the absolute stress signal.\n\n| Event | Peak/bracket step | Peak strain, stress (MPa) | End step | End strain, stress (MPa) | Drop (MPa) | Support |\n|---:|---|---:|---:|---:|---:|---|\n| Peak / 1 | 36 | 0.009000, 834.786 | 37 | 0.009250, 2.511 | **832.275** | both |\n| 2 | 39 | 0.009750, 20.786 | 41 | 0.010250, 0.316 | 20.470 | extrema-only |\n| 3 | 43 | 0.010750, 21.960 | 45 | 0.011250, 1.884 | 20.076 | extrema-only |\n| 4 | 47 | 0.011750, 22.206 | 49 | 0.012250, 4.082 | 18.124 | extrema-only |\n| 5 | 51 | 0.012750, 17.810 | 52 | 0.013000, 6.208 | 11.602 | extrema-only |\n| 6 | 54 | 0.013500, 17.998 | 56 | 0.014000, 4.037 | 13.961 | extrema-only |\n| 7 | 58 | 0.014500, 19.723 | 60 | 0.015000, 1.850 | 17.873 | extrema-only |\n| 8 | 62 | 0.015500, 21.192 | 64 | 0.016000, 0.347 | **20.846** | extrema-only |\n| 9 | 66 | 0.016500, 22.181 | 68 | 0.017000, 2.547 | 19.634 | extrema-only |\n\nThe global peak is **834.786 MPa at step 36, strain 0.0090**, followed by an abrupt **832.275 MPa** drop to **2.511 MPa** at step 37. Eight later complete local drops range from **11.602 to 20.846 MPa**, with median **18.879 MPa** and mean **17.823 MPa**. The first drop is approximately **44.08×** the median subsequent drop.\n\nThis is curve-level evidence only. The repeated smaller extrema do not establish an atomic mechanism, structural transformation, or stable structural state without separate structural analysis.",
  "data": {
    "completion_check": {
      "outcome": "completed",
      "verified_run_ids": [
        "stress-strain-20260927-044120-a8c0ed"
      ],
      "missing_artifact_roles": [],
      "failures": []
    },
    "runs": [
      {
        "status": "completed",
        "result": {
          "summary": "Threshold: 1; extrema: 9; combined: 9 candidate drops. Segment 1: 9 complete, 0 unresolved; first complete drop 832.275 MPa; 8 subsequent drops, median 18.8793 MPa (range 11.6024 to 20.8459).",
          "data": "artifacts/analysis/stress_strain/stress-strain-20260927-044120-a8c0ed/stress_strain_analysis.json",
          "source_run_id": "shear-20260927-040956-e24773",
          "memory_fact_refs": [
            {
              "source_run_id": "stress-strain-20260927-044120-a8c0ed",
              "fact_id": "28812200-242c-53d0-a43f-d078ee8cb238",
              "kind": "scientific_conclusion",
              "lookup_key": "run:stress-strain-20260927-044120-a8c0ed:main_conclusion"
            }
          ],
          "conclusion_lookup_key": "run:stress-strain-20260927-044120-a8c0ed:main_conclusion",
          "conclusion_fact_id": "28812200-242c-53d0-a43f-d078ee8cb238",
          "combined_event_count": 9,
          "combined_events": [
            {
              "descent_end_step": 37,
              "descent_start_step": 36,
              "drop_amplitude_basis": "peak_to_valley",
              "event_complete": true,
              "event_end_step": 37,
              "event_end_strain": 0.009250000000000005,
              "event_index": 1,
              "event_step": 37,
              "event_strain": 0.009250000000000005,
              "extrema_event_index": 1,
              "largest_single_step_drop_at_step": 37,
              "largest_single_step_drop_mpa": 832.2753087194884,
              "loading_cycle": 1,
              "loading_direction": "x",
              "loading_half_cycle": 1,
              "detail_fields_available": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 18
              }
            },
            {
              "descent_end_step": 41,
              "descent_start_step": 39,
              "drop_amplitude_basis": "peak_to_valley",
              "event_complete": true,
              "event_end_step": 41,
              "event_end_strain": 0.01025000000000001,
              "event_index": 2,
              "event_step": 40,
              "event_strain": 0.009999999999999988,
              "extrema_event_index": 2,
              "largest_single_step_drop_at_step": 41,
              "largest_single_step_drop_mpa": 12.228611361952462,
              "loading_cycle": 1,
              "loading_direction": "x",
              "loading_half_cycle": 1,
              "detail_fields_available": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 18
              }
            },
            {
              "descent_end_step": 45,
              "descent_start_step": 43,
              "drop_amplitude_basis": "peak_to_valley",
              "event_complete": true,
              "event_end_step": 45,
              "event_end_strain": 0.01125,
              "event_index": 3,
              "event_step": 44,
              "event_strain": 0.010999999999999992,
              "extrema_event_index": 3,
              "largest_single_step_drop_at_step": 44,
              "largest_single_step_drop_mpa": 11.48897942540387,
              "loading_cycle": 1,
              "loading_direction": "x",
              "loading_half_cycle": 1,
              "detail_fields_available": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 18
              }
            },
            {
              "omitted_items": 6,
              "total_items": 9,
              "read_from": "full_output"
            }
          ],
          "curve_summary": {
            "summary": "Threshold: 1; extrema: 9; combined: 9 candidate drops. Segment 1: 9 complete, 0 unresolved; first complete drop 832.275 MPa; 8 subsequent drops, median 18.8793 MPa (range 11.6024 to 20.8459).",
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
              "both": 1,
              "extrema_only": 8,
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
              "mad_mpa": 5.0597992238768565,
              "median_absolute_step_change_mpa": 22.928663737047373,
              "method": "automatic_mad",
              "nonconverged_points_included": true,
              "robust_sigma_mpa": 7.501658329319827,
              "sensitivity": 3.5,
              "signal_range_mpa": 834.7857106341163,
              "threshold_mpa": 49.18446788966677
            }
          },
          "event_count": 9,
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
          "extrema_event_count": 9,
          "extrema_events": [
            {
              "descent_end_step": 37,
              "descent_start_step": 36,
              "drop_amplitude_basis": "peak_to_valley",
              "event_complete": true,
              "event_end_step": 37,
              "event_end_strain": 0.009250000000000005,
              "event_index": 1,
              "event_step": 37,
              "event_strain": 0.009250000000000005,
              "extrema_event_index": 1,
              "largest_single_step_drop_at_step": 37,
              "largest_single_step_drop_mpa": 832.2753087194884,
              "loading_cycle": 1,
              "loading_direction": "x",
              "loading_half_cycle": 1,
              "detail_fields_available": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 18
              }
            },
            {
              "descent_end_step": 41,
              "descent_start_step": 39,
              "drop_amplitude_basis": "peak_to_valley",
              "event_complete": true,
              "event_end_step": 41,
              "event_end_strain": 0.01025000000000001,
              "event_index": 2,
              "event_step": 40,
              "event_strain": 0.009999999999999988,
              "extrema_event_index": 2,
              "largest_single_step_drop_at_step": 41,
              "largest_single_step_drop_mpa": 12.228611361952462,
              "loading_cycle": 1,
              "loading_direction": "x",
              "loading_half_cycle": 1,
              "detail_fields_available": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 18
              }
            },
            {
              "descent_end_step": 45,
              "descent_start_step": 43,
              "drop_amplitude_basis": "peak_to_valley",
              "event_complete": true,
              "event_end_step": 45,
              "event_end_strain": 0.01125,
              "event_index": 3,
              "event_step": 44,
              "event_strain": 0.010999999999999992,
              "extrema_event_index": 3,
              "largest_single_step_drop_at_step": 44,
              "largest_single_step_drop_mpa": 11.48897942540387,
              "loading_cycle": 1,
              "loading_direction": "x",
              "loading_half_cycle": 1,
              "detail_fields_available": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 18
              }
            },
            {
              "omitted_items": 6,
              "total_items": 9,
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
        "run_id": "stress-strain-20260927-044120-a8c0ed",
        "workflow": "stress_strain_analysis",
        "manifest": "artifacts/analysis/stress_strain/stress-strain-20260927-044120-a8c0ed/manifest.json",
        "conclusion_lookup_key": "run:stress-strain-20260927-044120-a8c0ed:main_conclusion",
        "full_output": {
          "run_id": "stress-strain-20260927-044120-a8c0ed",
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
    "artifacts/analysis/stress_strain/stress-strain-20260927-044120-a8c0ed/manifest.json"
  ]
}