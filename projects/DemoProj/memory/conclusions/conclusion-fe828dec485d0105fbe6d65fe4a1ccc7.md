{
  "status": "ok",
  "error": null,
  "summary": "Completed signed stress–strain comparison for the exact forward/reverse P012 FCC Ni GB paths.\n\n**Derived run:** `stress-strain-20260927-045018-d12bcb`  \n**Source order and lineage:**\n1. `shear-20260927-040956-e24773` — `Forward +x loading`, 71 points, strain 0 → 0.0175, 0 nonconverged points.\n2. `shear-20260927-044521-841236` — `Reverse unloading`, 71 points, strain 0.0175 → nominal 0, 1 nonconverged point retained.\n\n**Outputs**\n- PNG: `artifacts/analysis/stress_strain/stress-strain-20260927-045018-d12bcb/stress_strain_comparison.png`\n- Reusable comparison data: `artifacts/analysis/stress_strain/stress-strain-20260927-045018-d12bcb/stress_strain_comparison.json`\n- Manifest: `artifacts/analysis/stress_strain/stress-strain-20260927-045018-d12bcb/manifest.json`\n- Conclusion: `run:stress-strain-20260927-045018-d12bcb:main_conclusion`\n\n### Mechanical-state metrics\n\n| Quantity | Value |\n|---|---:|\n| Forward initial stress at cumulative strain 0 | **−0.000714 MPa** |\n| Reverse final stress at nominal zero strain | **+12.105451 MPa** |\n| Final-state stress difference, reverse − forward | **+12.106165 MPa** |\n| Reverse-path peak \\(|\\)top shear stress\\(|\\) | **21.255509 MPa** |\n| Reverse peak location | step 12, cumulative strain ≈ **0.0145** |\n| Forward global peak/drop | **834.786424 MPa** at step 36, strain 0.0090; collapse occurs on step 36 → 37 |\n\nThe plot uses contrasting red/blue curves, signed actual cumulative strain, signed `top_shear_stress_mpa`, legend, and point markers. Every finite point is connected and retained; the single reverse nonconverged point was not filtered or used to split the curve.\n\n**Caveat:** the plotting interface marks all sampled points uniformly and does not provide special event annotations, so the forward global drop and reverse final zero-strain point are represented by their plotted data markers rather than dedicated callouts. This is a mechanical hysteresis/state comparison only; no atomic mechanism or NEB interpretation is implied.",
  "data": {
    "completion_check": {
      "outcome": "completed",
      "verified_run_ids": [
        "stress-strain-20260927-045018-d12bcb"
      ],
      "missing_artifact_roles": [],
      "failures": []
    },
    "runs": [
      {
        "status": "completed",
        "result": {
          "data": "artifacts/analysis/stress_strain/stress-strain-20260927-045018-d12bcb/stress_strain_comparison.json",
          "memory_fact_refs": [
            {
              "source_run_id": "stress-strain-20260927-045018-d12bcb",
              "fact_id": "b906b930-04ad-5645-819a-c46ce849a307",
              "kind": "scientific_conclusion",
              "lookup_key": "run:stress-strain-20260927-045018-d12bcb:main_conclusion"
            }
          ],
          "conclusion_lookup_key": "run:stress-strain-20260927-045018-d12bcb:main_conclusion",
          "conclusion_fact_id": "b906b930-04ad-5645-819a-c46ce849a307",
          "memory_publication": {
            "status": "completed",
            "artifact_id": "a7773feb-0c54-5d7f-b96b-67280df661c6",
            "attempts": 1,
            "format": "fact_batch",
            "memory_tool": "plot_stress_strain_comparison",
            "path": "artifacts/analysis/stress_strain/stress-strain-20260927-045018-d12bcb/memory_facts.json",
            "sha256": "4b27c60ef90aabd7e24803a7321167066b54d588ee7368a396704b7fc74c06e0"
          },
          "plot": "artifacts/analysis/stress_strain/stress-strain-20260927-045018-d12bcb/stress_strain_comparison.png",
          "series": [
            {
              "source_run_id": "shear-20260927-040956-e24773",
              "label": "Forward +x loading",
              "color": "#c62828",
              "line_style": "-",
              "marker": "o",
              "nonconverged_point_count": 0,
              "point_count": 71
            },
            {
              "source_run_id": "shear-20260927-044521-841236",
              "label": "Reverse unloading",
              "color": "#1565c0",
              "line_style": "-",
              "marker": "s",
              "nonconverged_point_count": 1,
              "point_count": 71
            }
          ],
          "source_run_ids": [
            "shear-20260927-040956-e24773",
            "shear-20260927-044521-841236"
          ]
        },
        "run_id": "stress-strain-20260927-045018-d12bcb",
        "workflow": "stress_strain_analysis",
        "manifest": "artifacts/analysis/stress_strain/stress-strain-20260927-045018-d12bcb/manifest.json",
        "conclusion_lookup_key": "run:stress-strain-20260927-045018-d12bcb:main_conclusion",
        "full_output": {
          "run_id": "stress-strain-20260927-045018-d12bcb",
          "tool": "query_project_memory",
          "entity": "runs"
        },
        "artifact_roles": [
          "stress_strain_comparison_data",
          "stress_strain_comparison_plot"
        ]
      }
    ]
  },
  "artifacts": [
    "artifacts/analysis/stress_strain/stress-strain-20260927-045018-d12bcb/manifest.json"
  ]
}