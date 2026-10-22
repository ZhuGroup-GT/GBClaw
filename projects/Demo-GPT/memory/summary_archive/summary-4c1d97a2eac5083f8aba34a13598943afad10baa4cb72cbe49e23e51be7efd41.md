# Historical project summary: summary-4c1d97a2eac5083f8aba34a13598943afad10baa4cb72cbe49e23e51be7efd41

Historical context only. This may contain superseded decisions or withdrawn interpretations; query current SQLite facts/runs before using it as evidence or authorization. Evidence-source labels retain their original meaning.

Source revision: 512; format: 4; generated: 2026-10-21T23:11:50+00:00; archived: 2026-10-21T23:12:08.749626Z.

## Tool results
- Exact relaxed-frame 77→80 measurement: 11,052 matched atom IDs using source-cell all-axis minimum-image convention. With 20 Å FCC-interior exclusion, top-minus-bottom translation is **(+0.195673781, +0.000000643, +0.061142988) Å**, XZ norm **0.205004130 Å**; mean exclusion sensitivity <1e-7 Å. Retrieve distributions, cubic coefficients and FCC-translation residual tests before assigning a Burgers vector. Completed run `code-20261021-224156-7649e8`; artifact pointer: `artifacts/code/code-20261021-224156-7649e8/manifest.json`. (fact: `run:code-20261021-224156-7649e8:main_conclusion`)
- Rendering `render-20261021-224107-c440c3` completed for two batch-minimized outputs. Estimated Y-normal boundary y=-0.7007784843609599; lower side black, upper side milk white, transparent background, radius scale 0.7. Inspect layer profile/warnings before interpreting grain identity. (fact: `run:render-20261021-224107-c440c3:main_conclusion`)
- Batch `structure-minimize-20261021-220732-c86c47` completed from `cfg-convert-20261021-220654-f4066c`, ultimately continuation shear `shear-20261021-211934-d84742`. Retrieve exact ordered frame→item→output mappings and per-item convergence. (fact: `run:structure-minimize-20261021-220732-c86c47:main_conclusion`)
- Reload-start step70/item1 and postdrop steps119/item13, 122/item14, 130/item16 explicitly completed and converged under independent zero-load, all-atom-free, fixed-box minimization. Historical source strains are provenance, not relaxed-output strains; energies are not comparable to constrained NEB energies. (fact: `run:structure-minimize-20261021-220732-c86c47:item:1`) (fact: `run:structure-minimize-20261021-220732-c86c47:item:13`) (fact: `run:structure-minimize-20261021-220732-c86c47:item:14`) (fact: `run:structure-minimize-20261021-220732-c86c47:item:16`)
- Relevant lineage: `minimize-20261021-202820-718597` → `shear-20261021-203147-9d18c4` → continuation `shear-20261021-211934-d84742`. Reload-event analysis completed; exact event/frame mappings remain queryable. (fact: `run:stress-strain-20261021-212925-9f1375:main_conclusion`)
- Earlier `neb-20261021-214036-574393` and analyses `neb-analysis-20261021-215038-ae08e5`, `neb-analysis-20261021-215038-5ed9de` completed; completion alone does not establish convergence or barriers. Retrieve constraints before comparison.

## Conversation-derived context
- User goal: study general tilt grain boundaries in FCC Ni with a <110> tilt axis. (fact: `project:primary_goal`)
- User material selection: Ni, fcc. (fact: `project:active_material`)
- User-specified boundary identity: general tilt, FCC Ni, {110} and {111} plane families, <110> tilt-axis family. (fact: `gb:ni_110_111:identity`)
- User selected **P012**, using its candidate dimensions and periodic boundary conditions along the grain-boundary direction. This is a user selection, not independently verified geometry. (fact: `project:decision:periodic_gb_candidate`)

## Agent reports
- Host-prioritized report has no exposed value/outcome; retrieve before relying on it. (fact: `agent_conclusion:conclusion-adaf6e41efde4cdb5a4c3ffc7aa54e9f`)
- Comparison task reported completed for consecutive freely relaxed postdrop configurations and relative-grain shift, supported by rendering and batch minimization; no Burgers-vector conclusion exposed. (fact: `agent_conclusion:conclusion-ddc7848e619d960328a3b124b4e3a466`)
- Coding task reported completed for relaxed original77/current80 CFGs using OVITO/NumPy (ASE unavailable), supported by `code-20261021-224156-7649e8`; interpretation requires retrieval. (fact: `agent_conclusion:conclusion-0907066c192a7a399d1af3528c0d6a95`)

## Workflow state
- Current host-prioritized thread/invocation values are omitted: query exact state before resuming or inferring execution/approval. No explicit pending plan or approval is visible. (fact: `task:thread:50189f73fd4cbf8a7cf94d546809be49`) (fact: `task:invocation:beae10bd97a50b536acb6c651b60c006`)

Details omitted here remain in SQLite. Use query_project_memory(entity='facts', lookup_key=...) or entity='runs', run_id=... for exact current evidence; search_project_memory finds older records. Use query_project_memory(entity='summary_archive') then archive_id=... for historical summaries (also memory/summary_archive/index.md). Archives are historical context, not current decisions, approval or verified evidence.

<!-- project-summary-history:v1 -->
## Historical summary index
Selective current summary and recent-history index; both are incomplete. Omitted content still exists in SQLite. Archives are historical, not current facts or approvals.
- `summary-f1c93d1b27da6813c6828e9a6a4d2f136d8133967312a2de13782a46134bb1b7` (revision 511): Tools: Exact relaxed-frame 77→80 measurement: 11,052 mat… | Conversation: User goal: study general tilt grain bounda… | Agent reports: Host-prioritized report has no exposed va… | Workflow: Current host-prioritized thread/invocation val…
- `summary-82ad7de7737ec3ef4445b8ebb3d19ce7ece392cf7c4dc74680ab887a7514c7f4` (revision 508): Tools: Exact relaxed-frame 77→80 measurement: 11,052 mat… | Conversation: No conversation-fact records supplied. Use… | Agent reports: Latest analysis task reported completed:… | Workflow: Current thread and latest coding invocation re…
Read: query_project_memory(entity='summary_archive', archive_id='<ID>'). Find older: query_project_memory(entity='summary_archive', query='<topic>'); follow next_cursor as cursor. Verify current facts/runs via query_project_memory. Files: memory/summary_archive/index.md.
<!-- /project-summary-history -->
