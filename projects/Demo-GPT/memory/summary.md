## Demo-GPT model configuration

The entire recorded study used **GPT-6.1 Sol** for the Orchestrator, Coding, Structure, LAMMPS and Analysis agents.

## Recorded study overview

- The FCC Ni (110) ∥ (111) general tilt boundary has an actual misorientation of 35.264390° and a physical ⟨110⟩ tilt axis. CSL screening and near-CSL comparisons preceded the selection of the continuous-periodic-x, 11,052-atom P012 cell. Sources: `csl-20261021-201945-0edf88`, `gb-periodicity-20261021-202310-f3831c`, `minimize-20261021-202820-718597`.
- The loading study comprises 70 initial loading increments, followed by 70 unloading and 70 reloading increments. It includes a zero-strain atomic displacement comparison and an 18-replica NEB path. Sources: `shear-20261021-203147-9d18c4`, `shear-20261021-211934-d84742`, `render-20261021-213748-f27cfe`, `neb-20261021-214036-574393`.
- All 18 independently relaxed zero-load states converged to energetically equivalent states within numerical resolution. Displacement comparisons of relaxed steps 77 → 80 → 84 show alternating transverse shifts and a nearly pure-x two-increment relative slide. Sources: `structure-minimize-20261021-220732-c86c47`, `code-20261021-231633-f084af`.
- The selected four-point near-CSL cell was strain-fitted and analyzed using both FCC axial layers. Its complete saved geometric map and edge-vector data are available in `dichromatic-map-20261021-233535-774f02`. The final recorded conversation contains the full project summary and supporting files.

## Tool results
- Completed map `dichromatic-map-20261021-233535-774f02`, sourced from completed `dichromatic-map-20261021-233334-57bf8b` (itself sourced from `csl-20261021-201945-0edf88`). Both conclusions are omitted: retrieve selected point IDs, cell/boundary geometry, strain transform, residuals and interaction state before continuing. Completion alone does not confirm a Burgers/DSC vector. Latest manifest: `artifacts/structure/dichromatic_map/dichromatic-map-20261021-233535-774f02/manifest.json`. (fact: `run:dichromatic-map-20261021-233535-774f02:main_conclusion`) (fact: `run:dichromatic-map-20261021-233334-57bf8b:main_conclusion`)
- CSL run `csl-20261021-233528-a21f24` completed; parameters/results omitted, so no geometry or convergence inference. Manifest: `artifacts/structure/csl-20261021-233528-a21f24/manifest.json`.
- Completed `code-20261021-231633-f084af`: 11,052-ID three-frame MIC translations show alternating relative Z and nearly pure-X net 77→84. Fixed z0=0 mirrors preserve Ni/side bijections but yield 0.618383574 Å RMS mismatch dominated by Z; translated unreflected control RMS 0.020322449 Å. (fact: `run:code-20261021-231633-f084af:main_conclusion`)
- Completed `code-20261021-224156-7649e8`: relaxed 77→80 top-minus-bottom translation (+0.195673781,+0.000000643,+0.061142988) Å; XZ norm 0.205004130 Å, mean exclusion sensitivity <1e-7 Å. Retrieve cubic coefficients/FCC residual tests before assigning a Burgers vector. (fact: `run:code-20261021-224156-7649e8:main_conclusion`)
- Zero-load, all-atom-free, fixed-box minimizations converged for postdrop steps119/122/130; energies are not comparable to constrained NEB energies. (fact: `run:structure-minimize-20261021-220732-c86c47:item:13`) (fact: `run:structure-minimize-20261021-220732-c86c47:item:14`) (fact: `run:structure-minimize-20261021-220732-c86c47:item:16`)

## Conversation-derived context
- User goal/material: general tilt GBs in FCC Ni, <110> tilt axis, {110}/{111} plane families. (fact: `project:primary_goal`) (fact: `project:active_material`) (fact: `gb:ni_110_111:identity`)
- User selected P012, candidate dimensions and periodicity along the GB direction; not verified geometry. (fact: `project:decision:periodic_gb_candidate`)
- Loading preference: shear, 70 steps, strain increment 0.00025; not execution evidence. (fact: `project:active_loading`)
- Retain displacement emerging during x loading/unloading in NEB endpoint comparison as atomic structural change, not independently imposed rigid-body displacement. (fact: `project:decision:retain_emergent_displacement`)

## Agent reports
- Latest report updated to v2; content/outcome omitted and must be retrieved before relying on it. (fact: `agent_conclusion:conclusion-8b97540d2fdd4a82cc848cf0b0f876a7`)
- Earlier report was awaiting_user for four paired sites in one axial layer/perimeter order, supporting `csl-20261021-233131-32e139`; check against newer maps rather than assuming confirmation still pending. Report interpretation: 0.391348465 Å slide matches stated magnitude, but a[1,-2,1]/22 is ~0.15% larger; no exact Burgers/DSC vector or Σ established, and ideal maps do not establish physical dislocations. (fact: `agent_conclusion:conclusion-82d098b701c0e032584d9356d7a7418a`)
- Other prioritized report outcome omitted. (fact: `agent_conclusion:conclusion-adaf6e41efde4cdb5a4c3ffc7aa54e9f`)

## Workflow state
- Updated thread and invocation values omitted; retrieve before resuming. No explicit pending plan/approval exposed. (fact: `task:thread:50189f73fd4cbf8a7cf94d546809be49`) (fact: `task:invocation:24c7fe764b5a21a0a25124c0071a1a86`)
- Other prioritized task pointers require status lookup, not assumptions of activity or scientific success. (fact: `task:invocation:b71c1c7e61bb54f93f7afa69f479fd27`) (fact: `task:invocation:beae10bd97a50b536acb6c651b60c006`)

Details omitted here remain in SQLite. Use query_project_memory(entity='facts', lookup_key=...) or entity='runs', run_id=... for exact current evidence; search_project_memory finds older records. Use query_project_memory(entity='summary_archive') then archive_id=... for historical summaries (also memory/summary_archive/index.md). Archives are historical context, not current decisions, approval or verified evidence.

<!-- project-summary-history:v1 -->
## Historical summary index
Selective current summary and recent-history index; both are incomplete. Omitted content still exists in SQLite. Archives are historical, not current facts or approvals.
- `summary-f3c596e5e23091fee44006bd6610dd4dc9e47b4dd9c2c02b94d1fcd8451944ee` (revision 547): Tools: Completed dichromatic map 'dichromatic… | Conversation: User goal/material: general til… | Agent reports: Report 'conclusion-82d098b701c… | Workflow: Current task values are omitted; qu…
- `summary-e216e92b2e99b2cea87179ad0521e6a2e48c1a46498f89c5f450b9a5578d3a23` (revision 542): Tools: Completed 'csl-20261021-233131-32e139'… | Conversation: User goal/material: general til… | Agent reports: Structure report 'conclusion-8… | Workflow: Thread v44 and invocation v2 are ac…
- `summary-8cd49d3a843f9fb4261efd2c3e7708461e18881a991995278e20692b44920113` (revision 532): Tools: Completed 'code-20261021-231633-f084af… | Conversation: User goal: general tilt grain b… | Agent reports: Updated reports have omitted v… | Workflow: Thread v42 and updated invocation s…
Read: query_project_memory(entity='summary_archive', archive_id='<ID>'). Find older: query_project_memory(entity='summary_archive', query='<topic>'); follow next_cursor as cursor. Verify current facts/runs via query_project_memory. Files: memory/summary_archive/index.md.
<!-- /project-summary-history -->

## Additional showcase file selection

- Intermediate minimization trajectories and runtime checkpoints are omitted. All 18 batch-final structures remain available in both CFG and LAMMPS data formats, together with their metrics, energy records, plots and analysis.
- Duplicate per-item final copies and staged LAMMPS source copies are omitted; the canonical final files and original converted inputs are retained. Initial per-item CFG conversions are omitted; the original shear frames and before/after measurements are retained.
- Current manifests list these removals under `pruned_from_demo`, with original checksums and retained replacements. The retained conversation and downloadable transcript remain available.
