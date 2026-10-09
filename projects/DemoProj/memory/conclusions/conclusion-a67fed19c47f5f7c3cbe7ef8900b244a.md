{
  "status": "ok",
  "error": null,
  "summary": "Completed monotonic shear.\n\n- **gb_shear run:** `shear-20260927-040956-e24773`\n- **Status:** completed; no warnings\n- **Source lineage:** `minimize-20260927-040247-4d2144` (`gb_minimize`), source fingerprint `sha256:cfffb28744a13a038e1cf017513a19fd5dcf033cbe622d088f23055f69c2590b`\n- **Material/model:** FCC Ni, `NiCr.adp`, continuous periodic x (`x_pbc=true`), displacement loading in +x\n- **Protocol:** 70 loading steps, increment `2.5×10⁻⁴`, non-cyclic, `steps_per_half_cycle=null`; 71 recorded points including step 0\n- **Final step/strain:** step 70, cumulative actual shear strain `0.0175`\n- **Convergence:** all 70 loading steps converged (`nonconverged_steps=0`; final step converged)\n- **Final boundary shear stress:** top `+21.7584 MPa`, bottom `−21.7584 MPa`\n- **Peak absolute shear stress:** `834.7864 MPa` at step 36, strain `0.0090`\n\nKey artifacts:\n\n- Manifest: `artifacts/lammps/shear/shear-20260927-040956-e24773/manifest.json`\n- Configuration: `artifacts/lammps/shear/shear-20260927-040956-e24773/input.json`\n- Used configuration: `artifacts/lammps/shear/shear-20260927-040956-e24773/config_used.json`\n- Stress/strain metadata: `artifacts/lammps/shear/shear-20260927-040956-e24773/shear_run.json`\n- Final indexed structure: `artifacts/lammps/shear/shear-20260927-040956-e24773/gb_sheared.lmp`\n- Final CFG: `artifacts/lammps/shear/shear-20260927-040956-e24773/gb_sheared.cfg`\n- Final step frame: `artifacts/lammps/shear/shear-20260927-040956-e24773/structures/gb_shear_step_0070.cfg`\n- Preserved shear boundary IDs:\n  - `.../inputs/top_boundary.ids`\n  - `.../inputs/bottom_boundary.ids`\n\nThe run retained the exact minimized source structure and matching boundary-ID artifacts; no loading continued beyond step 70.",
  "data": {
    "runs": [
      {
        "status": "completed",
        "result": {
          "warnings": [],
          "source_run_id": "minimize-20260927-040247-4d2144",
          "memory_fact_refs": [
            {
              "source_run_id": "shear-20260927-040956-e24773",
              "fact_id": "a13a9b70-470b-5ea3-b4dc-4f5fe313c9f3",
              "kind": "scientific_conclusion",
              "lookup_key": "run:shear-20260927-040956-e24773:main_conclusion"
            }
          ],
          "conclusion_lookup_key": "run:shear-20260927-040956-e24773:main_conclusion",
          "conclusion_fact_id": "a13a9b70-470b-5ea3-b4dc-4f5fe313c9f3",
          "bottom_com_current": {
            "x": 0.6687939011725662,
            "y": -102.01356283705674
          },
          "bottom_com_initial": {
            "x": 0.6687939011725662,
            "y": -102.01356283705674
          },
          "boundary_stress_source": "sigma_xy_top_and_negative_bottom",
          "complete": true,
          "completed_step_count": 71,
          "current_stress_state_mpa": {
            "bottom_shear_stress_mpa": -21.758443040431928,
            "sigma_xx_mpa": -24.11515995846018,
            "sigma_xy_mpa": 21.758443040431928,
            "sigma_xz_mpa": 0.7240585929792429,
            "sigma_yy_mpa": 87.70411377447931,
            "sigma_yz_mpa": 0.0002156719602320683,
            "sigma_zz_mpa": 18.19035065427907,
            "top_shear_stress_mpa": 21.758443040431928
          },
          "cyclic": false,
          "final_actual_shear_strain": 0.017499999999999998,
          "final_loading_cycle": 1,
          "final_loading_direction": "x",
          "final_loading_half_cycle": 1,
          "final_step": 70,
          "final_step_converged": true,
          "final_step_data": {
            "converged": true,
            "actual_shear_strain": 0.017499999999999998,
            "bottom_com": {
              "x": 0.6687939011725662,
              "y": -102.01356283705674
            },
            "bottom_shear_stress_mpa": -21.758443040431928,
            "current_slab_spacing_angstrom": 204.65239845687188,
            "increment_stage": "initial",
            "loading_cycle": 1,
            "loading_direction": "x",
            "loading_half_cycle": 1,
            "relative_shear_strain": 0.017499999999999998,
            "relative_target_shear_strain": 0.0175,
            "sigma_xx_mpa": -24.11515995846018,
            "sigma_xy_mpa": 21.758443040431928,
            "sigma_xz_mpa": 0.7240585929792429,
            "sigma_yy_mpa": 87.70411377447931,
            "sigma_yz_mpa": 0.0002156719602320683,
            "sigma_zz_mpa": 18.19035065427907,
            "step": 70,
            "step_in_loading_half_cycle": 70,
            "target_shear_strain": 0.0175,
            "top_com": {
              "x": 4.182074106399616,
              "y": 102.63883561981515
            },
            "top_shear_stress_mpa": 21.758443040431928
          },
          "initial_cumulative_shear_strain": 0.0,
          "initial_loading_direction": "x",
          "load_type": "displacement",
          "loading_cycle_at_peak": 1,
          "loading_direction_at_peak": "x",
          "loading_half_cycle_at_peak": 1,
          "loading_steps": 70,
          "memory_publication": {
            "status": "completed",
            "artifact_id": "38ee0ff0-7597-5d7e-b56d-44cce6b673a1",
            "attempts": 1,
            "format": "fact_batch",
            "memory_tool": "run_gb_shear",
            "path": "artifacts/lammps/shear/shear-20260927-040956-e24773/memory_facts.json",
            "sha256": "d28f8178f473ed5e4afad35d289e8b314c52b02396dc2b5a21f6e067a8442a79"
          },
          "nonconverged_steps": 0,
          "parent_run_id": "minimize-20260927-040247-4d2144",
          "parent_workflow": "gb_minimize",
          "peak_abs_shear_stress_mpa": 834.7864243899115,
          "planned_loading_cycles": 1,
          "planned_loading_half_cycles": 1,
          "points": 71,
          "requested_half_cycles": 1,
          "source_fingerprint": "sha256:cfffb28744a13a038e1cf017513a19fd5dcf033cbe622d088f23055f69c2590b",
          "step_at_peak": 36,
          "steps_per_half_cycle": null,
          "strain_at_peak": 0.009000000000000003,
          "stress_at_peak_mpa": 834.7864243899115,
          "detail_fields_available": [
            "detail_fields_available"
          ]
        },
        "run_id": "shear-20260927-040956-e24773",
        "workflow": "gb_shear",
        "manifest": "artifacts/lammps/shear/shear-20260927-040956-e24773/manifest.json",
        "conclusion_lookup_key": "run:shear-20260927-040956-e24773:main_conclusion",
        "full_output": {
          "run_id": "shear-20260927-040956-e24773",
          "tool": "query_project_memory",
          "entity": "runs"
        },
        "artifact_roles": [
          "bottom_boundary_ids",
          "config",
          "config_used",
          "intermediate_structure",
          "log",
          "memory_facts",
          "run_metadata",
          "sheared_cfg",
          "sheared_lmp",
          "source_lmp",
          "top_boundary_ids"
        ]
      }
    ]
  },
  "artifacts": [
    "artifacts/lammps/shear/shear-20260927-040956-e24773/manifest.json"
  ]
}