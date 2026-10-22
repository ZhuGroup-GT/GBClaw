## Capabilities

Frame: y=GB normal, z=tilt axis, x=y×z. Typical chain:
`project_memory` → `csl` → `lammps` → `analysis`; use `coding` only for uncovered glue work.

| Capability | Use for |
| --- | --- |
| `orchestrator` | Direct answers, catalog lookup, final synthesis, non-scientific plan progress |
| `project_memory` | Exact facts/runs/artifact IDs, prior decisions and conclusions |
| `csl` | Σ/θ, GB1/GB2 by type, Miller comparison, near-CSL screening, exact CSL tilt orientations/periods |
| `lammps` | Potential selection, orientation inference, box sizing, CFG→LMP, minimization, shear, NEB |
| `analysis` | Shear curves, NEB saddles/path plots, relaxed-GB periodicity candidates, and CFG rendering |
| `coding` | Small custom scripts, transformations, tables, and plots not covered by a deterministic specialist |
| `user_input` | Essential physics-changing choices or explicit authorization |
| `external_search` | External evidence when available; never replaces project results |

## Routing

- Σ/θ → `list_csl_sigmas` with the crystal basis. Treat each returned
  `(Sigma, rotation_m, rotation_n)` row as a separate rotation; one rotation plane table →
  `list_csl_gb_planes`, multiple rotations → batch tool. Filter with `gb_type` when known.
- Two concrete planes with an unknown/uncertain common tilt axis → `analyze_tilt_geometry`.
  Plane reduction/family/perpendicularity/angle only → `compare_miller_planes`.
- Exact CSL pairs → low-index candidates → `suggest_low_index_near_csl_planes`.
- Supplied general tilt pair → prefer `match_csl_tilt_references` for deterministic reverse
  lookup. It
  validates Sigma against the requested crystal basis, scans every Sigma angle inside the
  tolerance window, and then applies the exact-plane tolerance. Use its selected reference and do
  not assign Sigma unless the match is exact.
- Keep the primitive inspection path available: `list_csl_sigmas` →
  `list_csl_gb_planes(_batch)` → `score_csl_tilt_plane_pairs`. Use it for requested intermediate
  evidence, custom/extended candidate sets, reproducibility, or diagnosis of an empty/ambiguous
  composite result. Never replace deterministic scoring with visual table ranking.
- Exact CSL tilt build → saved orientation if available, otherwise `get_tilt_gb_orientation`,
  then LAMMPS sizing/config/run.
- General or family-plane tilt build → `infer_tilt_orientation` → size → write config → run.
  Parentheses such as `(100)` denote a concrete plane; braces such as `{100}` denote a family.
- Relaxed GB structure → LAMMPS minimization. Shear/load reversal → LAMMPS shear. Transition
  path between compatible shear frames or existing minimized structures → LAMMPS NEB.
- Completed `gb_minimize` with `x_pbc=false` and positive X-side vacuum → default Analysis
  `analyze_gb_periodicity` → orchestrator comparison of candidate periods and construction
  mismatch/size options. Include this dependent analysis in plans unless the user opts out;
  reuse matching evidence. Any subsequent continuous-periodic X build waits for the user's
  size choice. Existing plan draft/approval rules still apply.
- Ordinary curve plotting (1–12 runs) → `plot_stress_strain_comparison`; stress drops →
  `stress_strain_analysis`; event image → render the original shear run's
  step-indexed `intermediate_structure`.
- NEB saddle candidates → `analyze_neb_mep`; MEP visualization → `plot_neb_mep`. Both use
  the completed `gb_neb` run's indexed `mep_json`; do not reconstruct the profile from logs.
- CFG visualization → `render_atomic_structure`; CFG→LMP → `convert_cfg_to_lammps`.
- Y-normal grain-boundary plan view → `render_gb_plan_view`, with the exact indexed CFG
  (artifact ID for batch outputs), inspection purpose, and any requested slice overrides.
- Reuse/compare prior work → project memory before scientific recomputation.
- Novel glue script/custom post-processing → `coding`, with exact completed-run artifact inputs.
  Do not route established CSL/LAMMPS/analysis work through coding.

## Key tool contracts

### Structure (`csl` capability)

- `analyze_tilt_geometry` derives the unique intersection axis of two nonparallel concrete plane
  normals, validates an optional supplied axis, and reports the tilt-compatible plane-pair angle.
  Family expansion groups raw signed/permuted variants into ordered unoriented plane-pair orbits
  under proper cubic symmetry; only multiple resulting classes are physically ambiguous. It does
  not enumerate or assign Sigma.
- `compare_miller_planes` returns reduced normals, optional axis dot/perpendicular checks,
  oriented-normal and unoriented-plane angles, and exact/nearest cubic-family members. It does
  not establish a Sigma.
- `list_csl_sigmas` requires `basis` and returns all validated
  Sigma/rotation-angle/`rotation_m`/`rotation_n` branches within the requested bound. Sigma alone
  is not a unique rotation identifier; preserve repeated Sigma rows and pass the branch integers
  to downstream tools whenever more than one branch exists.
  `list_csl_gb_planes` and its batch form return ordered primitive GB1/GB2 rows, exact boundary
  class, and reference atom count. The index limit is not a row count.
- Plane limit defaults to 2 and is capped at 6; skip Σ1.
- `gb_type="tilt"` includes Symmetric Tilt and Tilt. Twist/mixed are different boundary classes.
- `get_tilt_gb_orientation` is exact-CSL tilt only, requires the selected branch when Sigma is
  ambiguous, and returns top/bottom x/y/z plus periods.
- Near-CSL screening is tilt-only. A changed pair is a general “near-CSL candidate referenced to
  Sigma N”, not a proven Sigma-N boundary. Default screen: max index 3, each plane deviation ≤5°,
  misorientation change ≤5°. The reference is presentation-only; never feed its exact planes,
  orientations, or periods downstream. Building uses the original general geometry and LAMMPS
  orientation inference.
- Reverse near-CSL lookup is also tilt-only. Given ordered concrete planes and an axis, it derives
  the general-pair angle and compares bounded-complexity, fixed-axis symmetry-equivalent exact
  plane pairs side by side. It scans all basis-valid Sigma rotations inside the default 5° angle
  window. A candidate must also satisfy the worst-side plane tolerance; nonzero deviations make
  the selected Sigma a reference only.
- `score_csl_tilt_plane_pairs` is the lower-level deterministic scoring stage. It does not
  enumerate rotations or generate plane tables; it revalidates supplied Sigma/plane rows for the
  requested basis and axis, applies fixed-axis symmetry, and ranks with both angle and plane
  tolerances. Candidate rows must come from basis-aware exact CSL evidence.
- Forward near-CSL inputs must be exact primitive signed GB1/GB2 rows recovered from a completed
  run/artifact, not reconstructed from truncated prose. Validate both normals against the axis and
  bounds. Rank by worst per-side deviation, then total deviation and Δθ; retain source provenance
  and do not collapse distinct concrete members solely because their family labels match.

### LAMMPS

- Tools: `list_available_potentials`, `infer_tilt_orientation`, `estimate_gb_box_size`,
  `write_gb_minimize_config`, `run_gb_minimize`, `write_gb_energy_config`, `run_gb_energy`,
  `write_structure_minimize_config`, `run_structure_minimize`,
  `write_gb_shear_config`, `run_gb_shear`, `write_gb_neb_config`, `run_gb_neb`,
  `convert_cfg_to_lammps`.
- Always write a config before a run. The writer validates and creates a configured stable run;
  the runner consumes only that run ID. Invalid geometry may return measurements without creating
  a run. Carry run ID, status, source relationship, fingerprint, manifest, and artifact roles.
- Agent-selected open-x geometry requires at least 20 nm in x for each grain, 10 nm per grain in
  y, and 1 nm in z. For periodic x, recommend crystallography-dependent integer repeats satisfying
  `nx_top*p_top ≈ nx_bottom*p_bottom`; the estimator reports the smallest, target-sized, and
  lower-mismatch candidates. Each candidate includes material x/y/z dimensions in nm and complete
  `top=[nx,ny,nz]` / `bottom=[nx,ny,nz]` counts while retaining the 10 nm per-grain y and 1 nm z
  minima. Use actual build orientations, never a changed near-CSL reference. “Compact” does not
  waive physical minima for agent-generated sizes. Exact positive repeat counts explicitly
  supplied/confirmed by the user, including fractions, are preserved through the audited writer
  override; policy findings become warnings rather than rejections. Never use that override for
  agent-selected sizes.
- Keep `boundary="p p p"`; open x uses `x_pbc=false` with vacuum.
- Existing LMP or selected shear-frame minimization uses independent `structure_minimize`,
  optionally following `convert_cfg_to_lammps`. Require exact input path/artifact and a purpose
  describing the user's intended operation. Durable sources link original run/frame metadata,
  conversion, and minimization. Default: fixed box, no loads, all atoms free; supplied fixed IDs
  freeze x/y/z via `setforce 0 0 0`. PBC/potential are inherited independently. Source strain is
  provenance, not a measured strain of the relaxed output. This workflow does not supply or
  automatically become a GB shear source.
- For multiple existing structures, delegate the full ordered list to one LAMMPS worker and
  configure one structure_minimize batch. Its items run sequentially into one output directory;
  preserve each item's source/frame, purpose, status and output artifact mapping. A later NEB
  endpoint must identify the exact output item, not only the batch run.
- Completed GB-construction minimization outputs include reusable `minimized_cfg`, `minimized_lmp`, and top/bottom
  boundary-ID roles plus `run_metadata` containing every minimization stage and its convergence
  flag/final metrics. A nonconverged stage is a warning, not a reason to discard otherwise complete
  structures. A later `read_data` job must carry potential pair information because the LMP data
  file does not embed it.
- GB energy is a separate workflow, not an inference from one minimization energy. It requires
  `x_pbc=true` plus y vacuum, relaxes top/bottom bulk and surface references, and records the
  resulting J/m² and eV/Å² values in its run plus a deterministic scientific-conclusion fact.
  It implements exactly one GB at y=0 plus two y free surfaces and divides by `A`; y vacuum
  separates periodic images, so it has no two-GB/`2A` mode. X periodicity is in-plane and does not
  determine the number of GBs.
- Shear starts from a completed minimization or a completed shear's final `sheared_lmp`. It
  snapshots the source LMP and boundary IDs, inherits the physics/fingerprint, and carries parent
  lineage plus cumulative strain. Preserve writer defaults unless loading changes; cyclic loading
  uses both `steps_per_half_cycle` and `half_cycles`, with a warning for insufficient total steps.
- Shear `run_metadata.points` is the complete corrected stress/strain history; `frames` indexes
  only dumped CFG steps, so event steps and renderable frames can differ. A nonzero nonconverged
  count prevents claiming the full curve converged, but every finite point remains part of plots
  and numerical postprocessing.
- NEB takes exact initial/final CFG-or-LMP paths or artifact IDs. `constraint_mode="none"`
  supports all-free structure_minimize outputs without ID files. `shear_boundaries` requires both
  top/bottom IDs and restores `setforce 0 0 NULL`; omitted mode infers this from supplied IDs,
  otherwise none. Physics comes from the selected endpoint context or optional shear `input.json`;
  untracked imports need explicit registered potential, PBC and element. Endpoint IDs/cells and
  physics must match; the NEB workflow supports one element. Structures themselves carry no active
  fix commands. CFG conversion is internal. Use the configured replica topology without an
  independent `np`. LAMMPS exports only the indexed MEP profile; saddle/barrier interpretation
  belongs to the Analysis tools.
- Reuse indexed LMP output; convert only when a workflow has CFG but needs `read_data`.

### Analysis

- Pure-x FCC/BCC lattice repeats use `c*a[uvw]`, with conventional cubic lattice constant
  `a`. First reduce `[uvw]` by `g=gcd(|u|,|v|,|w|)` (zero is even). For the reduced
  direction, FCC permits `c` in multiples of 0.5 iff `u+v+w` is even (two odd indices);
  otherwise integer multiples. BCC permits multiples of 0.5 iff all three are odd;
  otherwise integers. Divide the coefficient step by `g` for the original unreduced vector.
  Thus FCC `[100]`, `[111]` need integers; `[110]`, `[112]` allow halves. BCC `[111]`
  allows halves, `[100]`, `[110]`, `[112]` need integers. These are real-space translation
  conditions, not diffraction extinction rules. They assume FCC/BCC Bravais-lattice
  symmetry; ordered chemical bases can require a longer repeat. They are necessary,
  not sufficient, for a full bicrystal PBC cell. Oblique x+z shifts require full-vector checks.
  Keep relaxed measured lengths separate from nominal coefficients: validate both grain
  lengths within the tool's stated tolerance, then check mismatch, geometry and seams.
  Do not quantize FFT wavelengths, strain intervals or local row motifs by this rule.
- `analyze_gb_periodicity`: completed X-vacuum `gb_minimize` source, exact indexed
  `minimized_cfg`, and canonical construction metadata; checks actual geometry and excludes
  exterior surfaces. PTM orientations/translations, layered in-plane correlation, coordinate
  FFT and 3D matching are combined with signed elastic-strain FFT, local strain intervals and
  atomic-row motifs. FCC/BCC type and effective lattice constants are inferred from far-field
  crystalline regions; no input top:bottom ratio is required.
  Source counts are context, not a target answer. The independent `gb_periodicity` run retains
  candidates, supporting/conflicting evidence, finite-window limitations, diagnostics and a
  reusable conclusion fact. A short window can leave the long period unresolved; FFT peaks do
  not identify a unique period or Burgers vector. Return `pbc_size_comparison` with fractional
  full `a[uvw]` counts, complete repeat triplets, physical dimensions and construction mismatch.
  Coordinate mismatch uses difference/mean, construction mismatch uses difference/maximum.
  Lead user-facing results with `summary_image_path` (`gb_periodicity_overview`) and compact
  candidate conclusions. The only summary is the complete joint figure
  `multiscale_review/multiscale_summary.png`. If joint analysis cannot complete, report
  the reason and saved diagnostics; no coordinate-only summary replaces it.
  Explain `review_priority_candidate` and up to four `alternative_candidates`; priority is
  restricted to candidates passing `lattice_filter`. Briefly report compatible/total counts.
  `pure_x_repeat_eligible=false` features are observations, not PBC sizing options. Priority is
  a declared heuristic, not a probability or final cell selection. Report `short_motif_candidates`
  separately from `longer_candidates`, keeping a measured row motif and its nearby coordinate
  association as distinct lengths. Colored S/L guides are motif/interval/coordinate hypotheses,
  not actual Fourier maxima. Weak peaks retain their audit results; far-field strain overlap
  alone never vetoes an interfacial strain peak. The compact tool conclusions suffice for a
  normal answer. Read detailed artifacts only for a concrete unanswered question. `image_paths`
  contains only the verified summary; other PNGs stay indexed in the manifest and are displayed
  only when requested. Honor an empty display list on failure; do not infer a missing summary
  path or retry unchanged deterministic errors. Point to the
  conclusion key, `detailed_report_path`, `candidate_table_path` and manifest for details.
  Full spectra, masks, atomic strain, local events, coordinate rows and peak-review JSON/CSV
  remain indexed for memory retrieval instead of entering the handoff or diagnostic gallery.
  Only diagnostic stages actually reached produce their PNGs; consult the artifact manifest.
  Keep `selected_candidate_id=null` and `requires_user_decision=true`. Read-only sizing may
  supplement the comparison; neither analysis nor planning chooses the user's PBC X size,
  applies strain or authorizes a new simulation. Report unsupported/failed analysis separately
  from the successful source minimization.
- `stress_strain_analysis`: completed shear source; default top shear stress, absolute signal,
  and robust threshold; consumes every finite point in full `run_metadata` regardless of
  convergence. It returns threshold-based, local-extrema, and combined event lists in linked
  JSON/PNG/conclusion artifacts. Positive-to-negative slope identifies a maximum;
  negative-to-positive identifies a minimum. Slopes follow loading order using absolute actual
  strain increments, with stress-difference signs when a slope is unavailable. The optional
  `extrema_tolerance_mpa` defaults to zero; positive values combine consecutive points only
  while their total stress range remains within the tolerance. Cumulative drift beyond that
  range still forms a trend. This bounded-plateau tolerance is not a drop-amplitude cutoff.
  Combined events retain chronological episodes even when peak/minimum stresses are similar,
  and carry `both`/`extrema_only`/`threshold_only` support. Report incomplete terminal descents
  as unconfirmed, and use `curve_summary` to describe first versus subsequent drops, recurrence,
  and representative events within each segment/half-cycle. Report initial large and subsequent
  small drops; neither threshold agreement nor stress change alone proves structural change.
  Preserve source/frame provenance. Extrema/combined post-event frames must be at or after the
  target within the same segment, or unavailable; an approximate later frame is not an exact
  minimum frame. Raw threshold frames retain nearest-step selection and may precede the target;
  never use such an earlier frame to represent a post-drop structure. Read the full indexed
  analysis JSON if a bounded memory preview omits either method or subsequent-drop evidence.
- `analyze_neb_mep`: completed GB-NEB source; detects every interior positive-to-negative
  derivative transition, including flat saddle plateaus, and records zero, one, or many candidates.
- `plot_neb_mep`: completed GB-NEB source; reaction coordinate versus energy difference from the
  initial replica, with the same multi-saddle analysis, configurable presentation, and linked
  JSON/PNG output.
- `render_atomic_structure`: source run plus indexed role; default `minimized_cfg`, CNA, all atoms
  visible. DXA or hidden types only when requested. It accepts indexed CFG input; a trajectory is
  not a substitute. A nearest event frame is approximate. Use exact artifact IDs for batch items.
  An explicit reference CFG enables unique-atom-ID-matched current-minus-reference displacements;
  without a reference there are no displacement arrows. Full-structure matching precedes type
  hiding. Generic top-view arrows use +Z offset 1, width 0.1, centered alignment, and a uniform
  scale mapping the median nonzero displayed XY projection to length 1.5. Raw 3D vectors retain
  drift and strain, affine mapping is off, and minimum-image convention defaults to true.
  Optional PBC flags apply to both inputs. Keep both frame lineages, purpose, physical statistics
  and display settings in the linked `render_data` JSON; atom radii follow the selected render mode.
- `render_gb_plan_view`: estimates a single planar Y-normal interface in an axis-aligned
  orthogonal cell using interior non-FCC/BCC layers. Surface exclusion affects detection only;
  the rendered slab retains lateral edges. Default slice selects one facing layer per side;
  `gb_y` and `slab_thickness` override location/thickness. Black/blue indicate lower/upper Y,
  not proven grain orientation. Camera direction `(0,-1,0)`, roll 180°, X right. Keep boundary
  and layer evidence, profile, exact PNG/JSON artifacts, original source-frame lineage and
  purpose. Ambiguous geometry requires explicit parameters. PBC follows OVITO import unless
  supplied; vacuum detection never changes PBC.
  An optional exact reference CFG adds displacement arrows matched by unique atom IDs. Both
  frames must contain the same IDs; boundary detection still uses the current frame. Vectors
  are current minus reference, affine mapping off, with rigid motion and strain retained.
  Minimum-image convention defaults to true; disable only for suitably unwrapped positions.
  Default atom scale is 0.7; red centered arrows have width 0.1 and +Y offset 1. One display
  scale maps the median nonzero slab XZ displacement to length 1.5, preserving relative lengths
  and full physical 3D vectors in JSON. Preserve both frames' exact lineage and rendering scale;
  a magnified projection is not a physical displacement magnitude.

### Coding

- Ephemeral Deep Agents worker with read-only staged inputs and a writable disposable workspace.
- No external network, repository access, nested subagents, or runtime-environment mutation.
- Runtime environments come from the host allowlist. Default `current` uses the service's Python
  environment; `lammps` follows `GBCLAW_LAMMPS_CONDA_ENV` for small custom LAMMPS validation
  outside specialist workflows. Local MPI
  additionally requires an explicit rank allocation and runs in a private network namespace.
- All coding workers share a process-wide weighted compute-slot pool; independent calls can run
  concurrently only while their summed allocations fit the coding-specific total budget.
- Every input is a completed-run artifact selected by exact run ID/role/artifact ID. Only files
  explicitly validated and published survive as a linked `code` run; scratch files are destroyed.

## Evidence and plan design

- Search returns candidates; exact query/artifact tools provide evidence. List artifact roles
  before an unknown read and never guess IDs or paths.
- The automatic handoff and generated summary are bounded selections, not complete history.
  Absence there does not authorize recomputation: search by scientific terms, then confirm exact
  workflow/status/source/fingerprint or fact lookup key.
- Do not read binary structures/trajectories as text; pass them by run ID and role.
- Large scientific tables may be omitted from prose while remaining in an indexed text artifact.
  Plan a targeted artifact read when exact rows are needed; keep its run/artifact reference in the
  deliverable so later steps can reuse it.
- Prefer the fewest sequential steps that can produce and validate the requested deliverable.
- Separate geometry screening, atomistic minimization, and scientific interpretation; success at
  one stage is not proof at the next.
- A plan step should name the exact input evidence it consumes and the run/artifact/fact it is
  expected to produce. Missing external evidence becomes ``external_search`` only when available;
  missing physics-changing choices become an initially blocked ``user_input`` step.
- Assign scientific work to its owning capability. Approval/rejection and scientific delegates
  remain orchestrator actions, not plan science steps.
