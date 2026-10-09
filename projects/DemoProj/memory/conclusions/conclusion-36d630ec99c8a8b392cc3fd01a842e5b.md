{
  "status": "ok",
  "error": null,
  "summary": "## NEB MEP analysis\n\n**Source:** `neb-20260927-045814-1d9965`  \n**Endpoint order preserved:**\n\n1. Replica 0: original minimized nominal-zero-strain P012 FCC Ni GB  \n2. Replica 17: reverse-unloaded nominal-zero-strain post-collapse state  \n\nThe completed path contains **18 replicas** and was analyzed with derivative tolerance **0.0 eV per reaction-coordinate unit**.\n\n### Supported saddle and endpoint energetics\n\n| Feature | Replica | Reaction coordinate | Relative energy (eV) | Barrier / energy |\n|---|---:|---:|---:|---:|\n| Initial endpoint | 0 | 0.00000000 | 0.00000000 | Absolute energy −48992.796 eV |\n| Saddle 1 | 9 | 0.57804419 | +0.08500000 | +0.08500000 eV from initial |\n| Final endpoint | 17 | 1.00000000 | −0.01600000 | Absolute energy −48992.812 eV |\n\nThe derivative changes from positive to negative at replica 9:\n\n- Left derivative: **+0.482697 eV / reaction-coordinate unit**\n- Right derivative: **−1.042723 eV / reaction-coordinate unit**\n- Plateau bounds collapse to the single point at replica 9: reaction coordinate **0.57804419**\n\nActivation barriers:\n\n- **Forward, initial → final:** **0.085000 eV**\n- **Reverse, final → initial:** **0.101000 eV**\n- Endpoint energy difference, final − initial: **−0.016000 eV**\n\nThus, the reverse-unloaded post-collapse state is lower in energy by approximately **16 meV** within this constrained NEB calculation.\n\n### Local extrema\n\nThe derivative-defined scan found **one supported interior local maximum** and no additional interior saddle candidates. The final replica is the right-hand minimum for the reported saddle; no separate interior minimum was identified between replicas 9 and 17.\n\n### Convergence and force evidence\n\nThe source NEB run is completed and used:\n\n- 18 replicas\n- FIRE minimization\n- `ftol = 0.0001`\n- `etol = 0.0`\n- Up to 10,000 steps in each minimization stage\n- Shear-boundary constraint mode\n- Ni potential: `NiCr.adp`\n\nThe reverse-unloaded final endpoint was recorded as converged in its source shear run. The NEB analysis itself returned **no warnings**, but the saddle analysis output does **not** provide per-replica residual-force norms or a separate saddle force-convergence table. Therefore, the energy-profile saddle is supported by the discrete derivative criterion, while quantitative force convergence at every intermediate replica cannot be independently assessed from this analysis output.\n\n## Recommended structures for inspection\n\nFor the single supported transition, inspect:\n\n- **Replica 8:** immediately before the saddle  \n  `artifacts/lammps/neb/neb-20260927-045814-1d9965/neb.replica.8.final.lmp`\n- **Replica 9:** saddle candidate  \n  `artifacts/lammps/neb/neb-20260927-045814-1d9965/neb.replica.9.final.lmp`\n- **Replica 10:** immediately after the saddle  \n  `artifacts/lammps/neb/neb-20260927-045814-1d9965/neb.replica.10.final.lmp`\n\nFor endpoint comparison, also inspect replica 0 and replica 17. The present MEP establishes an energetic transition through replica 9 but does **not** identify an atomistic mechanism, dislocation, or uniquely localized structural event.\n\n## Derived analysis and plot runs\n\n### Saddle analysis\n\n- **Derived run:** `neb-analysis-20260927-050841-049ce9`\n- **Source relationship:** derived from `neb-20260927-045814-1d9965`\n- **Analysis JSON:**  \n  `artifacts/analysis/neb/neb-analysis-20260927-050841-049ce9/neb_mep_analysis.json`\n- **Manifest:**  \n  `artifacts/analysis/neb/neb-analysis-20260927-050841-049ce9/manifest.json`\n- **Conclusion lookup key:**  \n  `run:neb-analysis-20260927-050841-049ce9:main_conclusion`\n\n### Publication-quality MEP plot\n\n- **Derived run:** `neb-analysis-20260927-050841-24df21`\n- **Source relationship:** plotted from `neb-20260927-045814-1d9965`\n- **PNG:**  \n  `artifacts/analysis/neb/neb-analysis-20260927-050841-24df21/neb_mep.png`\n- **Plot data JSON:**  \n  `artifacts/analysis/neb/neb-analysis-20260927-050841-24df21/neb_mep_plot.json`\n- **Manifest:**  \n  `artifacts/analysis/neb/neb-analysis-20260927-050841-24df21/manifest.json`\n- **Conclusion lookup key:**  \n  `run:neb-analysis-20260927-050841-24df21:main_conclusion`\n\nThe plot marks both endpoints and the single derivative-supported saddle at reaction coordinate **0.57804419**.",
  "data": {
    "completion_check": {
      "outcome": "completed",
      "verified_run_ids": [
        "neb-analysis-20260927-050841-049ce9",
        "neb-analysis-20260927-050841-24df21"
      ],
      "missing_artifact_roles": [],
      "failures": []
    },
    "runs": [
      {
        "status": "completed",
        "result": {
          "warnings": [],
          "data": "artifacts/analysis/neb/neb-analysis-20260927-050841-049ce9/neb_mep_analysis.json",
          "source_run_id": "neb-20260927-045814-1d9965",
          "memory_fact_refs": [
            {
              "source_run_id": "neb-analysis-20260927-050841-049ce9",
              "fact_id": "380e5d85-2ccd-5e64-be8c-7f07973f0920",
              "kind": "scientific_conclusion",
              "lookup_key": "run:neb-analysis-20260927-050841-049ce9:main_conclusion"
            }
          ],
          "conclusion_lookup_key": "run:neb-analysis-20260927-050841-049ce9:main_conclusion",
          "conclusion_fact_id": "380e5d85-2ccd-5e64-be8c-7f07973f0920",
          "derivative_tolerance_eV_per_coordinate": 0.0,
          "final_relative_energy_eV": -0.01599999999598367,
          "highest_saddle": {
            "barrier_from_final_eV": 0.10099999999511056,
            "barrier_from_initial_eV": 0.08499999999912689,
            "forward_barrier_from_left_minimum_eV": 0.08499999999912689,
            "left_derivative_eV_per_coordinate": 0.4826972498775993,
            "left_minimum": {
              "potential_energy_eV": -48992.796,
              "reaction_coordinate": 0.0,
              "relative_energy_eV": 0.0,
              "replica": 0
            },
            "plateau_end_reaction_coordinate": 0.57804419,
            "plateau_end_replica": 9,
            "plateau_start_reaction_coordinate": 0.57804419,
            "plateau_start_replica": 9,
            "potential_energy_eV": -48992.711,
            "profile_index": 9,
            "reaction_coordinate": 0.57804419,
            "relative_energy_eV": 0.08499999999912689,
            "replica": 9,
            "reverse_barrier_from_right_minimum_eV": 0.10099999999511056,
            "right_derivative_eV_per_coordinate": -1.0427226208110936,
            "right_minimum": {
              "potential_energy_eV": -48992.812,
              "reaction_coordinate": 1.0,
              "relative_energy_eV": -0.01599999999598367,
              "replica": 17
            },
            "saddle_number": 1
          },
          "memory_publication": {
            "status": "completed",
            "artifact_id": "dbd8f1ea-6fd8-5328-ad97-f0c6bbb293b6",
            "attempts": 1,
            "memory_tool": "analyze_neb_mep",
            "path": "artifacts/analysis/neb/neb-analysis-20260927-050841-049ce9/memory_conclusion.json",
            "sha256": "f140704429878d810f73e69424a8988f3d53712ba6f28466bfd019572fb3f3fd"
          },
          "replica_count": 18,
          "saddle_count": 1,
          "saddles": [
            {
              "barrier_from_final_eV": 0.10099999999511056,
              "barrier_from_initial_eV": 0.08499999999912689,
              "forward_barrier_from_left_minimum_eV": 0.08499999999912689,
              "left_derivative_eV_per_coordinate": 0.4826972498775993,
              "left_minimum": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 4
              },
              "plateau_end_reaction_coordinate": 0.57804419,
              "plateau_end_replica": 9,
              "plateau_start_reaction_coordinate": 0.57804419,
              "plateau_start_replica": 9,
              "potential_energy_eV": -48992.711,
              "profile_index": 9,
              "reaction_coordinate": 0.57804419,
              "relative_energy_eV": 0.08499999999912689,
              "replica": 9,
              "reverse_barrier_from_right_minimum_eV": 0.10099999999511056,
              "right_derivative_eV_per_coordinate": -1.0427226208110936,
              "right_minimum": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 4
              },
              "saddle_number": 1
            }
          ]
        },
        "run_id": "neb-analysis-20260927-050841-049ce9",
        "workflow": "neb_analysis",
        "manifest": "artifacts/analysis/neb/neb-analysis-20260927-050841-049ce9/manifest.json",
        "conclusion_lookup_key": "run:neb-analysis-20260927-050841-049ce9:main_conclusion",
        "full_output": {
          "run_id": "neb-analysis-20260927-050841-049ce9",
          "tool": "query_project_memory",
          "entity": "runs"
        },
        "artifact_roles": [
          "neb_mep_analysis"
        ]
      },
      {
        "status": "completed",
        "result": {
          "warnings": [],
          "data": "artifacts/analysis/neb/neb-analysis-20260927-050841-24df21/neb_mep_plot.json",
          "source_run_id": "neb-20260927-045814-1d9965",
          "memory_fact_refs": [
            {
              "source_run_id": "neb-analysis-20260927-050841-24df21",
              "fact_id": "52e86e7c-1751-54c7-87b8-e491b8be3807",
              "kind": "scientific_conclusion",
              "lookup_key": "run:neb-analysis-20260927-050841-24df21:main_conclusion"
            }
          ],
          "conclusion_lookup_key": "run:neb-analysis-20260927-050841-24df21:main_conclusion",
          "conclusion_fact_id": "52e86e7c-1751-54c7-87b8-e491b8be3807",
          "derivative_tolerance_eV_per_coordinate": 0.0,
          "final_relative_energy_eV": -0.01599999999598367,
          "highest_saddle": {
            "barrier_from_final_eV": 0.10099999999511056,
            "barrier_from_initial_eV": 0.08499999999912689,
            "forward_barrier_from_left_minimum_eV": 0.08499999999912689,
            "left_derivative_eV_per_coordinate": 0.4826972498775993,
            "left_minimum": {
              "potential_energy_eV": -48992.796,
              "reaction_coordinate": 0.0,
              "relative_energy_eV": 0.0,
              "replica": 0
            },
            "plateau_end_reaction_coordinate": 0.57804419,
            "plateau_end_replica": 9,
            "plateau_start_reaction_coordinate": 0.57804419,
            "plateau_start_replica": 9,
            "potential_energy_eV": -48992.711,
            "profile_index": 9,
            "reaction_coordinate": 0.57804419,
            "relative_energy_eV": 0.08499999999912689,
            "replica": 9,
            "reverse_barrier_from_right_minimum_eV": 0.10099999999511056,
            "right_derivative_eV_per_coordinate": -1.0427226208110936,
            "right_minimum": {
              "potential_energy_eV": -48992.812,
              "reaction_coordinate": 1.0,
              "relative_energy_eV": -0.01599999999598367,
              "replica": 17
            },
            "saddle_number": 1
          },
          "memory_publication": {
            "status": "completed",
            "artifact_id": "94d67117-1fcc-5417-b09f-58e9443ef8e2",
            "attempts": 1,
            "format": "fact_batch",
            "memory_tool": "plot_neb_mep",
            "path": "artifacts/analysis/neb/neb-analysis-20260927-050841-24df21/memory_facts.json",
            "sha256": "118f20aba82de1db3f5a14848c22dfe97ddb8a81cefedca0455d6b23552d27b0"
          },
          "plot": "artifacts/analysis/neb/neb-analysis-20260927-050841-24df21/neb_mep.png",
          "replica_count": 18,
          "saddle_count": 1,
          "saddles": [
            {
              "barrier_from_final_eV": 0.10099999999511056,
              "barrier_from_initial_eV": 0.08499999999912689,
              "forward_barrier_from_left_minimum_eV": 0.08499999999912689,
              "left_derivative_eV_per_coordinate": 0.4826972498775993,
              "left_minimum": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 4
              },
              "plateau_end_reaction_coordinate": 0.57804419,
              "plateau_end_replica": 9,
              "plateau_start_reaction_coordinate": 0.57804419,
              "plateau_start_replica": 9,
              "potential_energy_eV": -48992.711,
              "profile_index": 9,
              "reaction_coordinate": 0.57804419,
              "relative_energy_eV": 0.08499999999912689,
              "replica": 9,
              "reverse_barrier_from_right_minimum_eV": 0.10099999999511056,
              "right_derivative_eV_per_coordinate": -1.0427226208110936,
              "right_minimum": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 4
              },
              "saddle_number": 1
            }
          ]
        },
        "run_id": "neb-analysis-20260927-050841-24df21",
        "workflow": "neb_analysis",
        "manifest": "artifacts/analysis/neb/neb-analysis-20260927-050841-24df21/manifest.json",
        "conclusion_lookup_key": "run:neb-analysis-20260927-050841-24df21:main_conclusion",
        "full_output": {
          "run_id": "neb-analysis-20260927-050841-24df21",
          "tool": "query_project_memory",
          "entity": "runs"
        },
        "artifact_roles": [
          "neb_mep_plot",
          "neb_mep_plot_data"
        ]
      }
    ]
  },
  "artifacts": [
    "artifacts/analysis/neb/neb-analysis-20260927-050841-049ce9/manifest.json",
    "artifacts/analysis/neb/neb-analysis-20260927-050841-24df21/manifest.json"
  ]
}