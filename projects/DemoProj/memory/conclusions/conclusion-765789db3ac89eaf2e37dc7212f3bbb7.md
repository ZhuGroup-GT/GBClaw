{
  "status": "ok",
  "error": null,
  "summary": "Published the requested unsmoothed single-run P012 FCC Ni +x displacement shear stress–strain plot and its data products from staged run `shear-20260927-040956-e24773`. All 71 finite recorded points were included without convergence filtering; nonconverged count is 0. Peak signed top-boundary stress is 834.786424 MPa at step 36 and strain 0.009000000000000003; final stress is 21.758443 MPa at step 70 and strain 0.017499999999999998. Summary JSON preserves staged-artifact and parent-minimization lineage.",
  "data": {
    "run": {
      "status": "completed",
      "result": {
        "summary": "Published the requested unsmoothed single-run P012 FCC Ni +x displacement shear stress–strain plot and its data products from staged run `shear-20260927-040956-e24773`. All 71 finite recorded points were included without convergence filtering; nonconverged count is 0. Peak signed top-boundary stress is 834.786424 MPa at step 36 and strain 0.009000000000000003; final stress is 21.758443 MPa at step 70 and strain 0.017499999999999998. Summary JSON preserves staged-artifact and parent-minimization lineage.",
        "memory_fact_refs": [
          {
            "source_run_id": "code-20260927-041423-4734dc",
            "fact_id": "84b287bf-a51d-5145-a72a-22861411f93a",
            "kind": "scientific_conclusion",
            "lookup_key": "run:code-20260927-041423-4734dc:main_conclusion"
          }
        ],
        "conclusion_lookup_key": "run:code-20260927-041423-4734dc:main_conclusion",
        "conclusion_fact_id": "84b287bf-a51d-5145-a72a-22861411f93a",
        "memory_publication": {
          "status": "completed",
          "artifact_id": "77d1cf85-0dd5-58ef-8f97-223d816ea8bc",
          "attempts": 1,
          "format": "fact_batch",
          "memory_tool": "publish_coding_result",
          "path": "artifacts/code/code-20260927-041423-4734dc/memory_facts.json",
          "sha256": "5cfb9d6c004a94e541749b1c910cca097ff748e3c2e843be01b097809e9ddd13"
        },
        "published_artifacts": [
          {
            "run_id": "code-20260927-041423-4734dc",
            "artifact_id": "dabb59f7-68d4-5959-b5fc-3406f24f4a22",
            "created_at": "2026-09-27T04:15:00+00:00",
            "media_type": "image/png",
            "metadata": {
              "description": "Publication-quality PNG of signed actual cumulative shear strain versus signed top-boundary shear stress for P012 FCC Ni.",
              "sha256": "0e7e7c0e7e70fd14bd3d70b6d23c9c7151dffe3e9ae882432375e4d212598ac8"
            },
            "path": "artifacts/code/code-20260927-041423-4734dc/published/p012_shear_stress_strain.png",
            "provenance_class": "derived",
            "role": "plot",
            "size_bytes": 169695
          },
          {
            "run_id": "code-20260927-041423-4734dc",
            "artifact_id": "805fca7c-928b-586a-b6dc-a99f722bd008",
            "created_at": "2026-09-27T04:15:00+00:00",
            "media_type": "text/csv",
            "metadata": {
              "description": "Unsmoothed 71-point table with step, actual shear strain, top shear stress in MPa, and convergence flag.",
              "sha256": "47382a4d8c8fbc536a01622558da320979d1056c1b1224ccf3020acb703f3f17"
            },
            "path": "artifacts/code/code-20260927-041423-4734dc/published/p012_shear_stress_strain.csv",
            "provenance_class": "derived",
            "role": "data",
            "size_bytes": 3415
          },
          {
            "run_id": "code-20260927-041423-4734dc",
            "artifact_id": "f4738469-7f69-52a1-977b-992b868291b2",
            "created_at": "2026-09-27T04:15:00+00:00",
            "media_type": "application/json",
            "metadata": {
              "description": "Source lineage plus exact point-count, convergence, peak, and final-point metrics.",
              "sha256": "ac3509dd4c0cdd9c4f33a7b51a8316e89f9100361898966deca6229bcefc1054"
            },
            "path": "artifacts/code/code-20260927-041423-4734dc/published/p012_shear_stress_strain_summary.json",
            "provenance_class": "derived",
            "role": "summary",
            "size_bytes": 1151
          },
          {
            "run_id": "code-20260927-041423-4734dc",
            "artifact_id": "4f41b7a3-4b69-54e7-9e2b-3dfa4edb2893",
            "created_at": "2026-09-27T04:15:00+00:00",
            "media_type": "text/x-python",
            "metadata": {
              "description": "Reproducible script used to derive the CSV, PNG, and JSON summary from the staged metadata.",
              "sha256": "045d2d3a3b5cb7cbdc0828c725aac3cf6b31769ce97f16c97a6ae5aedbe7061d"
            },
            "path": "artifacts/code/code-20260927-041423-4734dc/published/create_p012_shear_plot.py",
            "provenance_class": "derived",
            "role": "script",
            "size_bytes": 5366
          }
        ],
        "sandbox": {
          "command_count": 10,
          "external_network": "disabled",
          "network_namespace": "no_sockets",
          "resource_allocation": {
            "compute_slots": 4,
            "mpi_enabled": false,
            "mpi_ranks": 1,
            "total_compute_slots": 36
          },
          "runtime_environment": {
            "available": true,
            "capabilities": [],
            "description": "Current service Python environment for plotting and general post-processing.",
            "fingerprint": "fb3d7e4fb8c8afb752a054065fd4f902fef1b0232e465e30f2198fed2fb97f3c",
            "name": "current"
          },
          "workspace_retention": "destroyed_after_worker"
        },
        "validation": {
          "commands": [
            {
              "command": "python /workspace/create_p012_shear_plot.py",
              "exit_code": 0,
              "output": "findfont: Failed to find font weight medium, now using 400.\nfindfont: Failed to find font weight medium, now using 400.\n{\"points\": 71, \"nonconverged\": 0, \"peak\": {\"step\": 36, \"actual_shear_strain\": 0.009000000000000003, \"top_shear_stress_mpa\": 834.7864243899115, \"converged\": true}, \"final\": {\"step\": 70, \"actual_shear_strain\": 0.017499999999999998, \"top_shear_stress_mpa\": 21.758443040431928, \"converged\": true}}\n",
              "truncated": false
            },
            {
              "command": "python -m py_compile /workspace/create_p012_shear_plot.py",
              "exit_code": 0,
              "output": "",
              "truncated": false
            },
            {
              "command": "python -c \"import csv,json,os; r=list(csv.DictReader(open('/workspace/p012_shear_stress_strain.csv'))); s=json.load(open('/workspace/p012_shear_stress_strain_summary.json')); assert len(r)==71 and r[-1]['step']=='70' and abs(float(r[-1]['actual_shear_strain'])-0.0175)<1e-12; assert s['peak_signed_top_shear_stress']['step']==36 and abs(s['peak_signed_top_shear_stress']['stress_mpa']-834.7864243899115)<1e-6 and s['nonconverged_point_count']==0; assert os.path.getsize('/workspace/p012_shear_stress_strain.png')>0\"",
              "exit_code": 0,
              "output": "",
              "truncated": false
            }
          ],
          "passed": true
        }
      },
      "run_id": "code-20260927-041423-4734dc",
      "workflow": "code",
      "manifest": "artifacts/code/code-20260927-041423-4734dc/manifest.json",
      "conclusion_lookup_key": "run:code-20260927-041423-4734dc:main_conclusion",
      "full_output": {
        "run_id": "code-20260927-041423-4734dc",
        "tool": "query_project_memory",
        "entity": "runs"
      },
      "artifact_roles": [
        "config",
        "data",
        "memory_facts",
        "plot",
        "script",
        "source_input",
        "summary"
      ]
    },
    "runs": [
      {
        "status": "completed",
        "result": {
          "summary": "Published the requested unsmoothed single-run P012 FCC Ni +x displacement shear stress–strain plot and its data products from staged run `shear-20260927-040956-e24773`. All 71 finite recorded points were included without convergence filtering; nonconverged count is 0. Peak signed top-boundary stress is 834.786424 MPa at step 36 and strain 0.009000000000000003; final stress is 21.758443 MPa at step 70 and strain 0.017499999999999998. Summary JSON preserves staged-artifact and parent-minimization lineage.",
          "memory_fact_refs": [
            {
              "source_run_id": "code-20260927-041423-4734dc",
              "fact_id": "84b287bf-a51d-5145-a72a-22861411f93a",
              "kind": "scientific_conclusion",
              "lookup_key": "run:code-20260927-041423-4734dc:main_conclusion"
            }
          ],
          "conclusion_lookup_key": "run:code-20260927-041423-4734dc:main_conclusion",
          "conclusion_fact_id": "84b287bf-a51d-5145-a72a-22861411f93a",
          "memory_publication": {
            "status": "completed",
            "artifact_id": "77d1cf85-0dd5-58ef-8f97-223d816ea8bc",
            "attempts": 1,
            "format": "fact_batch",
            "memory_tool": "publish_coding_result",
            "path": "artifacts/code/code-20260927-041423-4734dc/memory_facts.json",
            "sha256": "5cfb9d6c004a94e541749b1c910cca097ff748e3c2e843be01b097809e9ddd13"
          },
          "published_artifacts": [
            {
              "run_id": "code-20260927-041423-4734dc",
              "artifact_id": "dabb59f7-68d4-5959-b5fc-3406f24f4a22",
              "created_at": "2026-09-27T04:15:00+00:00",
              "media_type": "image/png",
              "metadata": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 2
              },
              "path": "artifacts/code/code-20260927-041423-4734dc/published/p012_shear_stress_strain.png",
              "provenance_class": "derived",
              "role": "plot",
              "size_bytes": 169695
            },
            {
              "run_id": "code-20260927-041423-4734dc",
              "artifact_id": "805fca7c-928b-586a-b6dc-a99f722bd008",
              "created_at": "2026-09-27T04:15:00+00:00",
              "media_type": "text/csv",
              "metadata": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 2
              },
              "path": "artifacts/code/code-20260927-041423-4734dc/published/p012_shear_stress_strain.csv",
              "provenance_class": "derived",
              "role": "data",
              "size_bytes": 3415
            },
            {
              "run_id": "code-20260927-041423-4734dc",
              "artifact_id": "f4738469-7f69-52a1-977b-992b868291b2",
              "created_at": "2026-09-27T04:15:00+00:00",
              "media_type": "application/json",
              "metadata": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 2
              },
              "path": "artifacts/code/code-20260927-041423-4734dc/published/p012_shear_stress_strain_summary.json",
              "provenance_class": "derived",
              "role": "summary",
              "size_bytes": 1151
            },
            {
              "run_id": "code-20260927-041423-4734dc",
              "artifact_id": "4f41b7a3-4b69-54e7-9e2b-3dfa4edb2893",
              "created_at": "2026-09-27T04:15:00+00:00",
              "media_type": "text/x-python",
              "metadata": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 2
              },
              "path": "artifacts/code/code-20260927-041423-4734dc/published/create_p012_shear_plot.py",
              "provenance_class": "derived",
              "role": "script",
              "size_bytes": 5366
            }
          ],
          "sandbox": {
            "command_count": 10,
            "external_network": "disabled",
            "network_namespace": "no_sockets",
            "resource_allocation": {
              "compute_slots": 4,
              "mpi_enabled": false,
              "mpi_ranks": 1,
              "total_compute_slots": 36
            },
            "runtime_environment": {
              "available": true,
              "capabilities": {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 0
              },
              "description": "Current service Python environment for plotting and general post-processing.",
              "fingerprint": "fb3d7e4fb8c8afb752a054065fd4f902fef1b0232e465e30f2198fed2fb97f3c",
              "name": "current"
            },
            "workspace_retention": "destroyed_after_worker"
          },
          "validation": {
            "commands": [
              {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 4
              },
              {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 4
              },
              {
                "details_omitted": true,
                "read_from": "full_output",
                "item_count": 4
              }
            ],
            "passed": true
          }
        },
        "run_id": "code-20260927-041423-4734dc",
        "workflow": "code",
        "manifest": "artifacts/code/code-20260927-041423-4734dc/manifest.json",
        "conclusion_lookup_key": "run:code-20260927-041423-4734dc:main_conclusion",
        "full_output": {
          "run_id": "code-20260927-041423-4734dc",
          "tool": "query_project_memory",
          "entity": "runs"
        },
        "artifact_roles": [
          "config",
          "data",
          "memory_facts",
          "plot",
          "script",
          "source_input",
          "summary"
        ]
      }
    ]
  },
  "artifacts": [
    "artifacts/code/code-20260927-041423-4734dc/manifest.json"
  ]
}