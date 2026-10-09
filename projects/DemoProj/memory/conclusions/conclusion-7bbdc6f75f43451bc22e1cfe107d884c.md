{
  "status": "ok",
  "error": null,
  "summary": "Completed ordered batch fixed-box, zero-load minimization.\n\n- **Structure-minimize run:** `structure-minimize-20260927-054622-f7218a`\n- **Status:** completed; **18/18 completed and converged**, 0 failed/cancelled/pending\n- **Manifest:** `artifacts/lammps/structure_minimize/structure-minimize-20260927-054622-f7218a/manifest.json`\n- **Configuration:** `p p p`, fixed box (`box_relax=false`), all atoms free (`fixed_ids=[]`), no loading/shear fixes, 11,052 Ni atoms, `NiCr.adp`, `pair_style adp`, CG (`ftol=1e-6`)\n- **Conversion run:** `cfg-convert-20260927-054600-4cfc7e`; all 18 CFGs converted successfully with no warnings.\n- **Reference retained:** `minimize-20260927-040247-4d2144`, `minimized_cfg` artifact `1bea7725-aea7-5b2f-b0b0-479af5b92778`.\n- **Step 60 warning:** source reverse-shear frame was nonconverged; it was nevertheless minimized successfully and converged.\n- **Step 70:** exact indexed frame used; source reverse-shear point was converged with actual cumulative strain `-1.04×10⁻¹⁷`.\n\nAll minimized CFG/LMP artifact IDs, energies, before/after maximum forces, force norms, iteration counts, and source provenance are recorded in the 18 item conclusion facts and manifest. Representative item 1: energy changed from `-48992.813252461914` to `-48992.82060001776 eV`; maximum force reduced from `3.7900362×10⁻3` to `4.1971043×10⁻8 eV/Å`.\n\n### Ordered source → conversion → minimized CFG mapping\n\nEach source was converted by `cfg-convert-20260927-054600-4cfc7e`; conversion artifact sequence and minimized CFG path are:\n\n1. Step 6 → `converted/000_gb_shear_step_0006.lmp` → `000_gb_shear_step_0006_minimized.cfg`\n2. Step 10 → `converted/001_gb_shear_step_0010.lmp` → `001_gb_shear_step_0010_minimized.cfg`\n3. Step 14 → `converted/002_gb_shear_step_0014.lmp` → `002_gb_shear_step_0014_minimized.cfg`\n4. Step 18 → `converted/003_gb_shear_step_0018.lmp` → `003_gb_shear_step_0018_minimized.cfg`\n5. Step 21 → `converted/004_gb_shear_step_0021.lmp` → `004_gb_shear_step_0021_minimized.cfg`\n6. Step 25 → `converted/005_gb_shear_step_0025.lmp` → `005_gb_shear_step_0025_minimized.cfg`\n7. Step 29 → `converted/006_gb_shear_step_0029.lmp` → `006_gb_shear_step_0029_minimized.cfg`\n8. Step 33 → `converted/007_gb_shear_step_0033.lmp` → `007_gb_shear_step_0033_minimized.cfg`\n9. Step 37 → `converted/008_gb_shear_step_0037.lmp` → `008_gb_shear_step_0037_minimized.cfg`\n10. Step 40 → `converted/009_gb_shear_step_0040.lmp` → `009_gb_shear_step_0040_minimized.cfg`\n11. Step 44 → `converted/010_gb_shear_step_0044.lmp` → `010_gb_shear_step_0044_minimized.cfg`\n12. Step 48 → `converted/011_gb_shear_step_0048.lmp` → `011_gb_shear_step_0048_minimized.cfg`\n13. Step 52 → `converted/012_gb_shear_step_0052.lmp` → `012_gb_shear_step_0052_minimized.cfg`\n14. Step 56 → `converted/013_gb_shear_step_0056.lmp` → `013_gb_shear_step_0056_minimized.cfg`\n15. Step 60 → `converted/014_gb_shear_step_0060.lmp` → `014_gb_shear_step_0060_minimized.cfg`\n16. Step 63 → `converted/015_gb_shear_step_0063.lmp` → `015_gb_shear_step_0063_minimized.cfg`\n17. Step 67 → `converted/016_gb_shear_step_0067.lmp` → `016_gb_shear_step_0067_minimized.cfg`\n18. Step 70 → `converted/017_gb_shear_step_0070.lmp` → `017_gb_shear_step_0070_minimized.cfg`\n\nFull minimized paths are under:\n\n`artifacts/lammps/structure_minimize/structure-minimize-20260927-054622-f7218a/`\n\nThe corresponding minimized LMP files share the same stems. Atom IDs, box lineage, and continuous periodic-x conditions were preserved. No Burgers vector or mechanism was inferred.",
  "data": {
    "runs": [
      {
        "status": "completed",
        "result": {
          "warnings": [],
          "purpose": "Prepare selected reverse-shear frames for residual-force removal before atom-ID displacement comparison to the original nominal-zero-strain minimized P012 reference.",
          "memory_fact_refs": [
            {
              "source_run_id": "cfg-convert-20260927-054600-4cfc7e",
              "fact_id": "d7fe0443-1bf8-5368-9599-1ec9a1a5fbd1",
              "kind": "scientific_conclusion",
              "lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:main_conclusion"
            },
            {
              "source_run_id": "cfg-convert-20260927-054600-4cfc7e",
              "fact_id": "90d69160-3ade-5b01-a9b5-04618b794b47",
              "kind": "scientific_conclusion",
              "lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:item:1"
            },
            {
              "source_run_id": "cfg-convert-20260927-054600-4cfc7e",
              "fact_id": "99ca72d1-7489-51ad-8029-7dfb23106781",
              "kind": "scientific_conclusion",
              "lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:item:2"
            },
            {
              "source_run_id": "cfg-convert-20260927-054600-4cfc7e",
              "fact_id": "aba9c935-a517-556a-aa9e-31c849f60ec9",
              "kind": "scientific_conclusion",
              "lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:item:3"
            },
            {
              "source_run_id": "cfg-convert-20260927-054600-4cfc7e",
              "fact_id": "3435dcce-de36-595b-a8cf-99ca749178ad",
              "kind": "scientific_conclusion",
              "lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:item:4"
            },
            {
              "source_run_id": "cfg-convert-20260927-054600-4cfc7e",
              "fact_id": "ec968d8a-5279-5015-9281-483c415b3fd5",
              "kind": "scientific_conclusion",
              "lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:item:5"
            },
            {
              "source_run_id": "cfg-convert-20260927-054600-4cfc7e",
              "fact_id": "aaffeb3c-3093-5833-b6c2-e62b0682af50",
              "kind": "scientific_conclusion",
              "lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:item:6"
            },
            {
              "source_run_id": "cfg-convert-20260927-054600-4cfc7e",
              "fact_id": "2fee1035-5ad8-5cda-a143-b1da56b38f4f",
              "kind": "scientific_conclusion",
              "lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:item:7"
            },
            {
              "source_run_id": "cfg-convert-20260927-054600-4cfc7e",
              "fact_id": "bae0e06b-db7a-5223-bf87-255c9330aad6",
              "kind": "scientific_conclusion",
              "lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:item:8"
            },
            {
              "source_run_id": "cfg-convert-20260927-054600-4cfc7e",
              "fact_id": "998c6543-aa92-5815-ad40-26708f9a7697",
              "kind": "scientific_conclusion",
              "lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:item:9"
            },
            {
              "source_run_id": "cfg-convert-20260927-054600-4cfc7e",
              "fact_id": "cba747e0-c6db-5715-86e1-94d738c0ef11",
              "kind": "scientific_conclusion",
              "lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:item:10"
            },
            {
              "source_run_id": "cfg-convert-20260927-054600-4cfc7e",
              "fact_id": "138720c3-3b7e-5055-a81c-8fa342c0f585",
              "kind": "scientific_conclusion",
              "lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:item:11"
            },
            {
              "source_run_id": "cfg-convert-20260927-054600-4cfc7e",
              "fact_id": "016f11d0-8783-5e88-9645-602b92cf08bb",
              "kind": "scientific_conclusion",
              "lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:item:12"
            },
            {
              "source_run_id": "cfg-convert-20260927-054600-4cfc7e",
              "fact_id": "0db17a53-d4ad-5d72-9207-f59092864b2c",
              "kind": "scientific_conclusion",
              "lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:item:13"
            },
            {
              "source_run_id": "cfg-convert-20260927-054600-4cfc7e",
              "fact_id": "b40cb272-2bf4-5d43-954f-f94e2e33d24a",
              "kind": "scientific_conclusion",
              "lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:item:14"
            },
            {
              "source_run_id": "cfg-convert-20260927-054600-4cfc7e",
              "fact_id": "96e8153b-117e-5ad1-bc10-fef22e0c42ee",
              "kind": "scientific_conclusion",
              "lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:item:15"
            },
            {
              "source_run_id": "cfg-convert-20260927-054600-4cfc7e",
              "fact_id": "a4d19062-66b9-5a10-9ed6-d9075915e557",
              "kind": "scientific_conclusion",
              "lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:item:16"
            },
            {
              "source_run_id": "cfg-convert-20260927-054600-4cfc7e",
              "fact_id": "e283a111-f310-5478-a1bc-6f6d72f07429",
              "kind": "scientific_conclusion",
              "lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:item:17"
            },
            {
              "source_run_id": "cfg-convert-20260927-054600-4cfc7e",
              "fact_id": "3b0d5641-a2f1-5b5a-ab3b-18f14e876932",
              "kind": "scientific_conclusion",
              "lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:item:18"
            }
          ],
          "conclusion_lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:main_conclusion",
          "conclusion_fact_id": "d7fe0443-1bf8-5368-9599-1ec9a1a5fbd1",
          "atom_style": "atomic",
          "conversion_count": 18,
          "conversions": [
            {
              "purpose": "Prepare selected reverse-shear frames for residual-force removal before atom-ID displacement comparison to the original nominal-zero-strain minimized P012 reference.",
              "atom_types": 1,
              "atoms": 11052,
              "box_origin_reference": "artifacts/lammps/shear/shear-20260927-044521-841236/gb_sheared.lmp",
              "box_origin_reference_sha256": "ad9d473e9c5a66e358fab1ffce300f3d95302b4573c318b7807f69fbeda6d0a9",
              "coordinate_origin": "source_lammps_box",
              "origin": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 6
              },
              "output_lmp": "artifacts/lammps/cfg_convert/cfg-convert-20260927-054600-4cfc7e/converted/000_gb_shear_step_0006.lmp",
              "output_lmp_artifact_id": "c83d26b8-6d8b-59dd-969a-cc4c9afb920b",
              "restricted_cell_angstrom": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 8
              },
              "source_cell_angstrom": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 2
              },
              "source_cfg": "artifacts/lammps/shear/shear-20260927-044521-841236/structures/gb_shear_step_0006.cfg",
              "detail_fields_available": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 4
              }
            },
            {
              "omitted_items": 17,
              "total_items": 18,
              "read_from": "full_output"
            }
          ],
          "coordinate_origin": "preserve_source_when_available",
          "memory_publication": {
            "status": "completed",
            "artifact_id": "9e8cfac2-bcc7-5436-9dd4-62ab6c96dbec",
            "attempts": 1,
            "format": "fact_batch",
            "memory_tool": "convert_cfg_to_lammps",
            "path": "artifacts/lammps/cfg_convert/cfg-convert-20260927-054600-4cfc7e/memory_facts.json",
            "sha256": "143c1ec52bb29ceea3794b2a4d18a31ef97ae9987c74774b9176b76d23826c95"
          },
          "origins": [
            {
              "run_id": "shear-20260927-044521-841236",
              "workflow": "gb_shear",
              "artifact_id": "008a651a-140f-543f-84c4-fc22a8dca0de",
              "frame": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 13
              },
              "path": "artifacts/lammps/shear/shear-20260927-044521-841236/structures/gb_shear_step_0006.cfg",
              "sha256": "6e8fb92d74061201d711180469e1e82d93c1b0aefb254de56780305af3f3fdde"
            },
            {
              "omitted_items": 17,
              "total_items": 18,
              "read_from": "full_output"
            }
          ]
        },
        "run_id": "cfg-convert-20260927-054600-4cfc7e",
        "workflow": "cfg_convert",
        "manifest": "artifacts/lammps/cfg_convert/cfg-convert-20260927-054600-4cfc7e/manifest.json",
        "conclusion_lookup_key": "run:cfg-convert-20260927-054600-4cfc7e:main_conclusion",
        "full_output": {
          "run_id": "cfg-convert-20260927-054600-4cfc7e",
          "tool": "query_project_memory",
          "entity": "runs"
        },
        "artifact_roles": [
          "config",
          "converted_lmp",
          "memory_facts",
          "source_cfg"
        ]
      },
      {
        "status": "completed",
        "result": {
          "summary": "Remove residual forces at zero external load from each selected reverse-unloading post-drop state before atom-ID displacement comparison to the original nominal-zero-strain minimized P012 reference; all atoms free, fixed simulation box, no applied loads or active shear constraints. Step 60 retains its source nonconvergence warning; step 70 is the converged nominal-zero-strain endpoint.; 18 structures in input order; 18 completed, 18 converged, 0 failed, 0 cancelled, 0 pending; fixed cell, no loading; outputs artifacts/lammps/structure_minimize/structure-minimize-20260927-054622-f7218a; each item records its selected source, original frame, purpose and constraints",
          "purpose": "Remove residual forces at zero external load from each selected reverse-unloading post-drop state before atom-ID displacement comparison to the original nominal-zero-strain minimized P012 reference; all atoms free, fixed simulation box, no applied loads or active shear constraints. Step 60 retains its source nonconvergence warning; step 70 is the converged nominal-zero-strain endpoint.",
          "memory_fact_refs": [
            {
              "source_run_id": "structure-minimize-20260927-054622-f7218a",
              "fact_id": "dba30956-1407-5666-8ed1-40b9ad57c589",
              "kind": "scientific_conclusion",
              "lookup_key": "run:structure-minimize-20260927-054622-f7218a:main_conclusion"
            },
            {
              "source_run_id": "structure-minimize-20260927-054622-f7218a",
              "fact_id": "5f896b9c-e18b-5042-a151-d881ac5a3564",
              "kind": "scientific_conclusion",
              "lookup_key": "run:structure-minimize-20260927-054622-f7218a:item:1"
            },
            {
              "source_run_id": "structure-minimize-20260927-054622-f7218a",
              "fact_id": "d38a6b9f-931b-5489-95ae-6691bb4cfa86",
              "kind": "scientific_conclusion",
              "lookup_key": "run:structure-minimize-20260927-054622-f7218a:item:2"
            },
            {
              "source_run_id": "structure-minimize-20260927-054622-f7218a",
              "fact_id": "9a8709ea-5f25-573a-a7e0-3e039517c6f6",
              "kind": "scientific_conclusion",
              "lookup_key": "run:structure-minimize-20260927-054622-f7218a:item:3"
            },
            {
              "source_run_id": "structure-minimize-20260927-054622-f7218a",
              "fact_id": "cad5faf5-b9a3-5828-8fc0-974ec708c89d",
              "kind": "scientific_conclusion",
              "lookup_key": "run:structure-minimize-20260927-054622-f7218a:item:4"
            },
            {
              "source_run_id": "structure-minimize-20260927-054622-f7218a",
              "fact_id": "e2943da3-267a-501c-960a-eadc6f6bb4c2",
              "kind": "scientific_conclusion",
              "lookup_key": "run:structure-minimize-20260927-054622-f7218a:item:5"
            },
            {
              "source_run_id": "structure-minimize-20260927-054622-f7218a",
              "fact_id": "9f20c3a4-2a06-572b-b257-e909cb35e8b0",
              "kind": "scientific_conclusion",
              "lookup_key": "run:structure-minimize-20260927-054622-f7218a:item:6"
            },
            {
              "source_run_id": "structure-minimize-20260927-054622-f7218a",
              "fact_id": "df026e13-d2a0-5863-9d57-6715b17ac939",
              "kind": "scientific_conclusion",
              "lookup_key": "run:structure-minimize-20260927-054622-f7218a:item:7"
            },
            {
              "source_run_id": "structure-minimize-20260927-054622-f7218a",
              "fact_id": "f5aa5700-f08b-5569-a7ee-f73da6c86de7",
              "kind": "scientific_conclusion",
              "lookup_key": "run:structure-minimize-20260927-054622-f7218a:item:8"
            },
            {
              "source_run_id": "structure-minimize-20260927-054622-f7218a",
              "fact_id": "1db51188-faf7-56aa-92e5-1bb87e04de35",
              "kind": "scientific_conclusion",
              "lookup_key": "run:structure-minimize-20260927-054622-f7218a:item:9"
            },
            {
              "source_run_id": "structure-minimize-20260927-054622-f7218a",
              "fact_id": "54eba346-8373-5ebb-99c7-2dc1b8fda086",
              "kind": "scientific_conclusion",
              "lookup_key": "run:structure-minimize-20260927-054622-f7218a:item:10"
            },
            {
              "source_run_id": "structure-minimize-20260927-054622-f7218a",
              "fact_id": "1cc3230e-04e3-5922-8270-63bc04954a09",
              "kind": "scientific_conclusion",
              "lookup_key": "run:structure-minimize-20260927-054622-f7218a:item:11"
            },
            {
              "source_run_id": "structure-minimize-20260927-054622-f7218a",
              "fact_id": "ae925cd7-0eb3-5279-a826-05aa09c30533",
              "kind": "scientific_conclusion",
              "lookup_key": "run:structure-minimize-20260927-054622-f7218a:item:12"
            },
            {
              "source_run_id": "structure-minimize-20260927-054622-f7218a",
              "fact_id": "33188dec-44bf-5a2e-a047-d0a0cea570fc",
              "kind": "scientific_conclusion",
              "lookup_key": "run:structure-minimize-20260927-054622-f7218a:item:13"
            },
            {
              "source_run_id": "structure-minimize-20260927-054622-f7218a",
              "fact_id": "313d1791-314c-54f7-a299-7b26e42b4c34",
              "kind": "scientific_conclusion",
              "lookup_key": "run:structure-minimize-20260927-054622-f7218a:item:14"
            },
            {
              "source_run_id": "structure-minimize-20260927-054622-f7218a",
              "fact_id": "95dec393-5e24-514a-9155-1b4cb740ec13",
              "kind": "scientific_conclusion",
              "lookup_key": "run:structure-minimize-20260927-054622-f7218a:item:15"
            },
            {
              "source_run_id": "structure-minimize-20260927-054622-f7218a",
              "fact_id": "1ba3aeb2-5539-554e-9389-ada6bc853a3f",
              "kind": "scientific_conclusion",
              "lookup_key": "run:structure-minimize-20260927-054622-f7218a:item:16"
            },
            {
              "source_run_id": "structure-minimize-20260927-054622-f7218a",
              "fact_id": "28d32de4-aa88-56a1-b034-0f6aaebb520d",
              "kind": "scientific_conclusion",
              "lookup_key": "run:structure-minimize-20260927-054622-f7218a:item:17"
            },
            {
              "source_run_id": "structure-minimize-20260927-054622-f7218a",
              "fact_id": "d9bface1-4d3a-5631-bd95-a55972038999",
              "kind": "scientific_conclusion",
              "lookup_key": "run:structure-minimize-20260927-054622-f7218a:item:18"
            }
          ],
          "conclusion_lookup_key": "run:structure-minimize-20260927-054622-f7218a:main_conclusion",
          "conclusion_fact_id": "dba30956-1407-5666-8ed1-40b9ad57c589",
          "batch": true,
          "box_relax": false,
          "cancelled_count": 0,
          "completed_count": 18,
          "converged_count": 18,
          "execution_order": "input_order",
          "failed_count": 0,
          "detail_fields_available": [
            "item_count",
            "items",
            "loading",
            "memory_publication",
            "mpi_ranks",
            "output_directory",
            "pending_count"
          ]
        },
        "run_id": "structure-minimize-20260927-054622-f7218a",
        "workflow": "structure_minimize",
        "manifest": "artifacts/lammps/structure_minimize/structure-minimize-20260927-054622-f7218a/manifest.json",
        "conclusion_lookup_key": "run:structure-minimize-20260927-054622-f7218a:main_conclusion",
        "full_output": {
          "run_id": "structure-minimize-20260927-054622-f7218a",
          "tool": "query_project_memory",
          "entity": "runs"
        },
        "artifact_roles": [
          "config",
          "initial_cfg",
          "item_config",
          "log",
          "memory_facts",
          "minimized_cfg",
          "minimized_lmp",
          "potential",
          "run_metadata",
          "source_lmp",
          "structure_metrics",
          "trajectory"
        ]
      }
    ]
  },
  "artifacts": [
    "artifacts/lammps/cfg_convert/cfg-convert-20260927-054600-4cfc7e/manifest.json",
    "artifacts/lammps/structure_minimize/structure-minimize-20260927-054622-f7218a/manifest.json"
  ]
}