# Agent Tools

Use this reference to find the tools behind a scientific workflow, understand their inputs and outputs, and check the assumptions attached to a result. The tool names are the agent-facing interface; you can ask the orchestrator for the scientific task in ordinary language. For the overall design, start with [Architecture and design](architecture.md).

## Find a workflow

| What you want to do | Start here | What passes to the next stage |
| --- | --- | --- |
| Propose or revise an investigation | [Project Plan Tools](#project-plan-tools) | An approval draft, followed by an approved plan with stable step IDs. |
| Resolve crystallography and candidate boundary planes | [CSL Tools](#csl-tools) | Exact rotation identities, orientations, or qualified near-CSL candidates. |
| Inspect and select a projected near-CSL cell | [Dichromatic maps](#dichromatic-maps) and the [interactive guide](dichromatic-map.md) | A user-confirmed selection, fitted transforms, and geometric evidence. |
| Build or relax a bicrystal | [LAMMPS Tools](#lammps-tools) | A configured run, then completed structures, logs, and convergence records. |
| Relax existing frames without rebuilding the boundary | [Existing-structure minimization](#existing-structure-minimization) | Exact source-to-output mappings for one frame or an ordered batch. |
| Measure energy, shear response, or a transition path | [GB Energy](#gb-energy), [GB Shear](#gb-shear), and [GB NEB](#gb-neb) | Simulation records and indexed outputs for downstream analysis. |
| Interpret or visualize a completed calculation | [Analysis Tools](#analysis-tools) | Derived measurements, figures, and links to the original simulation. |
| Write a small analysis not covered by an existing tool | [Coding agent](coding-agent.md) | Validated scripts and selected outputs published from a temporary workspace. |
| Recover an earlier result or its exact files | [Project Memory and Artifact Tools](#project-memory-and-artifact-tools) | Search candidates, exact records, and file references. |

A **run** is a recorded execution with a stable ID and status. An **artifact** is an indexed file produced or used by that run; its **role** describes its purpose, such as a configuration or final structure. Downstream tools use these references to select the intended dataset without copying trajectories or large tables into the model's context.

The usual sequence is **plan → establish geometry → configure and run simulations → analyze → review and refine**. Independent cases can proceed concurrently, but an analysis still needs the relevant simulation output. This reference follows those boundaries so that users and contributors can see where each decision belongs.

## Why the tools are structured this way

The architecture separates scientific decisions from repeatable numerical operations. An agent can choose which boundary to investigate, which result to inspect, or which follow-up to propose. The tool implementation owns geometry checks, parameter validation, execution, and publication. Keeping these operations explicit makes comparable calculations use the same numerical procedure.

Inputs and outputs carry run and artifact identities because a scientific conclusion depends on its model, parameters, and source data. For example, an NEB endpoint must refer to a particular structure, not merely to a conversation that mentioned a stress drop. A completed execution is also distinct from a converged minimization or a confirmed physical interpretation; each workflow records the relevant qualifications.

Predefined tools cover established workflows, while the [coding agent](coding-agent.md) handles small gaps. When a custom script becomes a recurring method, contributors can review its assumptions and numerical behavior, then promote it into a regular tool with the same provenance and publication rules. See the [memory extension guide](extension-guide.md) for that integration boundary.

## Tool Layering Principle

The public interface exposes the decisions the model needs to make while the service layer handles bookkeeping and execution:

- Agent-facing tools expose only the actions where the LLM needs to make decisions.
- Worker-owned implementations live in their domain packages `domains/structure/`, `domains/lammps/`, and `domains/analysis/`, with a tool module per workflow (for example `domains/lammps/neb/tool.py`); each domain's `catalog.py` collects its worker's tools. Memory adapters live under `agents/memory/tools/`, and plan tools in `agents/planner/tools.py`.
- File writes, memory records, subprocess execution, schema validation, and deterministic mechanics stay in internal helper/service functions.
- Project-memory writes are usually internal tool side effects. The agent is not given a manual memory-write tool.
- Project-memory reads use full-index search for discovery and structured semantic queries for exact records; artifact reads use stable run IDs and roles.

## Orchestrator Tools

The orchestrator tools are assembled in `src/agents/orchestrator/graph.py`. The `delegate_*` tools come from the registry: each `WorkerDefinition` names its delegate factory, and `build_registered_worker_tools()` in `src/agents/workers/lifecycle.py` creates the delegates in registry order. Domain tools such as `list_available_potentials` and `list_coding_environments` come from `DomainExtension.orchestrator_tools` factories (`build_domain_orchestrator_tools()` in `src/agents/orchestrator/contributions.py`), placed before or after the memory/plan/web tools.

| Tool | Source | Purpose |
| --- | --- | --- |
| `query_project_memory` | `agents/memory/tools/query.py` | Query structured runs, durable facts, the semantic summary, or historical summary archives. |
| `search_project_memory` | `agents/memory/tools/query.py` | Search all fact/run/artifact metadata and return bounded candidate references. |
| `list_run_artifacts` / `read_run_artifact` | `agents/memory/tools/artifacts.py` | List or read manifest-verified artifacts by run ID and role. |
| `read_tool_output` | `agents/memory/tools/output.py` | Retrieve a saved original tool reply by output ID, JSON field, and bounded page. |
| `read_agent_conclusion` | `agents/memory/tools/conclusions.py` | Read the full final report or provenance metadata for one uniquely numbered worker invocation. |
| `get_task_state` | `agents/memory/tools/task_state.py` | Read current thread progress or an exact invocation cursor, including blockers and conclusion references. |
| `report_artifact_usage` | `agents/memory/tools/artifacts.py` | Report artifact bytes by provenance. |
| `get_project_plan` | `agents/planner/tools.py` | Read the exact active/named plan or an immutable structural revision. |
| `get_project_plan_draft` | `agents/planner/tools.py` | Read the exact pending/named approval draft. |
| `approve_project_plan_draft` / `reject_project_plan_draft` | `agents/planner/tools.py` | Apply the current user's explicit decision. |
| `update_project_plan_step` | `agents/planner/tools.py` | Apply a validated transition to a non-scientific plan step. |
| `search_web` | `agents/orchestrator/web_search.py` | Optional search for current external facts, returning sources. |
| `list_available_potentials` | `domains/lammps/catalog.py` | Read registered potential metadata. |
| `list_coding_environments` | `domains/coding/environments.py` | List allowlisted coding Conda aliases and capabilities without host paths. |
| `delegate_plan` | `agents/planner/delegate.py` | Analyze complex requests and return an advisory, dependency-ordered plan. |
| `delegate_csl` | `domains/structure/delegate.py` | Delegate CSL, GB-plane, and orientation tasks to the Structure worker. |
| `delegate_lammps` | `domains/lammps/delegate.py` | Delegate LAMMPS config and run tasks. |
| `delegate_analysis` | `domains/analysis/delegate.py` | Delegate OVITO rendering and post-processing tasks. |
| `delegate_coding` | `domains/coding/delegate.py` | Delegate a small uncovered script/transformation/plot in a disposable sandbox. |

A scientific worker's business tools also come from the registry. Each domain's `catalog.py` builds its tool list, and its `extension.py` declares that list as a `ToolProviderSpec(key, worker, factory)` in `DomainExtension.tool_providers`. `WorkerSpec` loads the tools through `registered_worker_tools()` in `src/agents/workers/spec.py`, which combines every provider for that worker in registry order and rejects repeated tool names, so an extension can add tools to a built-in worker without editing it. Display names in streams, traces, and the Web UI come from `ToolCapabilitySpec.display_name` or `DomainExtension.tool_labels`, falling back to the tool name.

Workers receive read-only semantic search/query and artifact list/read tools. They cannot edit the summary or delete artifacts. The planning worker has no scientific execution tools; it alone receives a request-scoped `create_project_plan_draft` tool. When `delegate_plan` is called with an `existing_plan_id`, it also receives exact `get_project_plan` and planner-only `revise_project_plan_draft`. Its delegate accepts an explicit future `extra_tools` injection point but does not receive web search by default.

The coding worker is narrower at the project boundary: exact inputs must be supplied as completed run artifact references and are staged under `/inputs`. Deep Agents provides `ls`, `read_file`, `write_file`, `edit_file`, `delete`, `glob`, `grep`, and `execute`; the host adds `request_input_artifacts` (grant-scoped additional inputs) and `publish_coding_result`. Durable outputs are created only by `publish_coding_result`, or by host salvage of declared `expected_outputs` when the worker stops without publishing.

The delegate's `environment` argument must be an alias from `list_coding_environments`. The default `current` alias uses the service's Python environment. The `lammps` alias uses the Conda environment selected by `GBCLAW_LAMMPS_CONDA_ENV`. The optional `ase` alias follows `GBCLAW_ASE_CONDA_ENV`, defaulting to `GBclaw-ase`; it is usable only after that environment and ASE are installed. Aliases remain stable when actual environment names change. See the [runtime registry](coding-agent.md#runtime-environment-registry) for availability checks and dependency boundaries.

The same listing reports the coding compute-slot limits. `delegate_coding` accepts optional `compute_slots` and `mpi_ranks`; all concurrent workers share the coding-only weighted budget. `lammps` enables small custom serial or bounded local-MPI LAMMPS validation, never established or production LAMMPS runs.

## Project Plan Tools

Plans separate proposing work from authorizing its execution. A draft lets the researcher inspect the intended calculations and dependencies; approval creates the active plan. Later changes preserve the identity and history of work already running or completed.

The public domain API is the `planning` package: `models.py` owns schemas and stable keys, `service.py` owns approved-plan progress/revisions, and `draft_service.py` owns proposal/approval/rejection. `agents/planner/tools.py` is only the LangChain adapter layer.

`create_project_plan_draft` writes an audited `plan_draft:<draft_id>` plus `project:pending_plan`; it does not activate or execute the proposed plan. Plan steps use stable IDs, declared capabilities, and ordered dependencies. `get_project_plan_draft` returns the exact proposal, while `get_project_plan` returns an approved full record or named revision.

For modification requests, the orchestrator supplies the user's full request and existing plan ID to the planning worker rather than revising structure itself. `revise_project_plan_draft` can propose updates to metadata and future pending/blocked work, but cannot remove or rewrite a running or historical step. Explicit approval atomically creates/revises the active plan, records the source draft, retracts the pending pointer, and writes the immutable revision snapshot. Rejection keeps the draft audit record but changes no active plan.

Step transitions are validated and versioned; scientific delegates perform their own boundary updates when passed `plan_id`/`step_id`, while `update_project_plan_step` covers orchestrator, memory, user-input, and future search steps. Every database write updates FTS and semantic revisions in the same transaction. Pending `plan-draft.md`, active `plan.md`, audited draft JSON, current JSON, and revision JSON make plans visible in the file browser but are not writable state. `PlanApprovalGateMiddleware` rereads `project:pending_plan` for every tool call; while it exists, execution/progress and replacement planning are blocked, and approve/reject calls must be authorized by the latest user message.

## CSL Tools

Start here when the question concerns crystallographic identity or boundary geometry. Exact CSL results and near-CSL approximations have different meanings: a nearby reference rotation helps describe a general boundary, but does not make that boundary an exact CSL construction. The tools retain that distinction in both their outputs and stored records.

Defined in `src/domains/structure/csl/tools.py` and collected in `STRUCTURE_AGENT_TOOLS`. `src/domains/structure/catalog.py` adds the [dichromatic-map tools](#dichromatic-maps) to form the Structure worker's tool provider:

| Tool | Purpose |
| --- | --- |
| `analyze_tilt_geometry(plane1, plane2, axis=None, plane1_is_family=False, plane2_is_family=False, axis_is_family=False)` | Resolve optional cubic plane/direction families and report pure-tilt geometry without CSL enumeration. Raw signed/permuted family variants are grouped into ordered unoriented plane-pair orbits under proper cubic symmetry, so `ambiguous` means multiple physical classes. With two grain-local concrete planes and `axis_is_family=True`, it resolves separate `plane1_axis`/`plane2_axis` members through proper cubic symmetry and returns a common-frame representation; it never promotes the raw cross product of local Miller triples to a physical axis. |
| `compare_miller_planes(plane1, plane2=None, axis=None, reference_family=None)` | Reduce one Miller normal, or compare two: test axis perpendicularity, compare plane angles, and find the nearest cubic-family member without CSL enumeration. |
| `match_csl_tilt_references(axis, basis, plane1, plane2, axis_is_family=False, ...)` | Validate Sigma rotations for the requested basis, scan every rotation inside the angle tolerance, then match exact ordered plane pairs and report per-side deviations. `axis_is_family=True` resolves and aligns grain-local plane/axis pairs before the search and returns reference planes in both shared and local coordinates. |
| `score_csl_tilt_plane_pairs(axis, basis, plane1, plane2, candidate_pairs, ...)` | Validate and deterministically score supplied exact CSL tilt-plane rows under fixed-axis symmetry without enumerating Sigma values or generating plane tables. |
| `suggest_low_index_near_csl_planes(axis, source_run_id=None, source_branches=None, source_pairs=None, ..., deduplicate=True, gb_type="tilt")` | Screen exact CSL tilt-plane pairs for axis-compatible low-index approximations and report per-side errors plus the misorientation change. `source_run_id` reads every saved plane-table row of a completed exact enumeration in one call (at most 2,000 rows; narrow larger runs with `source_branches`); `source_pairs` takes up to 50 rows that have no saved run. Within each branch, suggestions that are the same plane pair under an axis-preserving cubic symmetry (each normal without sign, either grain order) merge into one candidate listing all its source rows. The model sees a per-branch table of unique candidates, bounded at 14,000 characters with an `omitted_candidates` marker for the rest; the saved result keeps every row. Only pure tilt boundaries are accepted. |
| `list_csl_sigmas(basis, u, v, w, limit=50)` | List every basis-validated `(Sigma, angle, rotation_m, rotation_n)` branch for a rotation axis; repeated Sigma rows are preserved. |
| `list_csl_gb_planes(..., rotation_m=None, rotation_n=None)` | List symmetry-filtered GB planes for one exact rotation. Branch integers are required when Sigma is ambiguous. |
| `list_csl_gb_planes_batch(..., sigmas=None, rotations=None)` | List GB planes for up to 25 rotations in parallel. Use `rotations` descriptors for repeated Sigma branches. For a large table of either plane tool, the model receives the rows as compact `[GB1, GB2, type code, reference atoms]` entries up to a 14,000-character view; any remainder is counted in `omitted_rows`, with a pointer to `full_output`. The reply, trace and saved artifact keep the full text table. |
| `get_tilt_gb_orientation(..., rotation_m=None, rotation_n=None)` | Generate top/bottom orientations and period data for one exact CSL tilt rotation; ambiguous Sigma requires its branch integers. |

The Structure worker also receives the typed memory query `query_csl_orientation`, which returns one saved exact CSL rotation/plane orientation by basis, axis, Sigma, GB plane and branch integers.

Important constraints:

- `basis` supports `fcc`, `bcc`, `diamond`, and `sc`.
- GB-plane search `limit` is clamped to `1..6`.
- GB1/GB2 output is reduced automatically to primitive Miller indices. Internal CSL-compatible scaling is retained only for common-cell construction.
- Sigma 1 is the identity and is not queried for GB planes.
- Sigma is a coincidence index, not a unique rotation ID. The exact identity is `(axis, basis, Sigma, rotation_m, rotation_n)`. Bare Sigma input is accepted only when one fundamental branch exists; otherwise the tool returns the valid branch choices.
- Batch lookup uses the Structure worker's shared process pool in `core/execution/process_pool.py`. Serial mode puts a task-level lock around the pool; parallel mode admits concurrent callers while the executor keeps their combined process use within `GBCLAW_WORKERS`. The shared limit defaults to 18 and is capped by available physical cores. Each Linux pool process binds to a distinct physical core and uses one native math computation thread.
- `get_tilt_gb_orientation` supports tilt GBs only. The simulation frame is `y = GB normal`, `z = tilt axis`, `x = in-plane`.
- Project-bound CSL tools fingerprint canonical inputs and return an identical completed artifact, with its run/artifact IDs, instead of recomputing it.
- CSL orientation facts and their typed memory queries include `rotation_m`/`rotation_n` in the lookup identity. A branch-aware query may read a matching legacy fact, but never a different branch that happened to share its Sigma.
- The composite reverse lookup is the normal fast path. The primitive inspection path is `analyze_tilt_geometry` → `list_csl_sigmas` → `list_csl_gb_planes(_batch)` → `score_csl_tilt_plane_pairs`; it supports intermediate evidence, custom candidate sets, reproducibility, and diagnosis without asking the model to rank tables visually.
- A completed reverse/scoring analysis writes one deterministic `gb_crystallography` fact keyed by normalized basis, axis, and ordered planes. It records the actual geometry and descriptive CSL assessment with `authoritative_for_structure_construction=false`; failed/incomplete runs do not write this fact.
- Near-CSL suggestions are geometric candidates. A changed plane pair is referenced to the source Sigma but is not claimed to retain that exact Sigma; downstream builds use the general tilt-orientation workflow rather than `get_tilt_gb_orientation` with the old Sigma. Forward screening requires a non-null exact source Sigma; general input must use reverse lookup.
- Reverse lookup starts from a supplied general pair, derives its tilt misorientation, enumerates Sigma rotations from the requested crystal basis, and scans every rotation inside the default 5° angle tolerance. A selected reference must also satisfy the worst-side plane tolerance and is ranked by plane-pair quality. Any nonzero deviation leaves the boundary general/non-Sigma and makes the selected Sigma a reference only. The reverse-only exact-plane limit defaults to 5.
- `suggest_low_index_near_csl_planes` requires `gb_type="tilt"`; twist and mixed boundaries are rejected because plane-pair angles do not encode their full misorientation.

The lower-level crystallography implementation lives in `src/domains/structure/csl/generator.py`; agents do not call its many functions directly.

## Dichromatic maps

The Structure worker exposes these tools from `src/domains/structure/dichromatic/tools.py`:

| Tool | Purpose |
| --- | --- |
| `get_dichromatic_map_options(lattice="FCC", axis="110")` | Return the library's supported angle interval, stacking layers, and CSL presets for an integer tilt axis. |
| `render_dichromatic_map(parameters, purpose, source_run_id=None)` | Generate a project PNG and numerical JSON through the headless CLI, recording why the map was made and its optional completed source run. |
| `prepare_dichromatic_cell_selection(purpose, parameters=None, source_run_id=None)` | Open and prefill the floating picker for initial setup; the user chooses four cell vertices. Omit `parameters` to reuse a completed unstrained dichromatic-map run's settings. |
| `get_dichromatic_cell_selection(request_id)` | Read the authoritative request state, confirmed selection, and any existing strain result. |
| `apply_dichromatic_cell_strain(request_id, ...)` | Apply strain only to an already confirmed selection, recording its transforms, residuals, artifacts, and selection-run provenance. |
| `analyze_dichromatic_cell_edges(request_id)` | Reuse saved strain to compare same-layer edge pairs across all axial layers and return global minima, both grain-vector representations, multiple annotations and a candidate summary; reuse current all-layer analysis. |

The shared `DichromaticMapRequest` accepts `lattice` (`SC`, `FCC`, or `BCC`), `axis`, `angle_deg`, `lattice_constant`, `width`, `height`, `center`, `marker_size`, `display_rotation_deg`, `g1_color`, `g2_color`, `visible_grain_layers`, `show_coincidence`, `show_csl_cell`, and `local_cutoff`. An omitted angle selects the library's axis preset; an explicit angle is preserved without snapping to a nearby CSL value. Query options before selecting layers or an angle: the symmetry-reduced angle interval depends on the axis. Layer lists are zero-based, with one list for each grain; `null` displays all layers.

Coordinates, field width/height, cell vectors, and local-pair cutoffs use a0. The explicit `lattice_constant` gives a0 in Angstrom and defaults to `1.0`; no material is inferred. Marker size is an area in points squared. Grain 1 is filled and grain 2 outlined; each axial phase has its own marker. Display rotation and visibility change the picture, while exact-cell counts describe the complete physical cell independently of the displayed field.

The adapter imports the installed [DichromaticMap](https://github.com/Yazhuo-Liu/DichromaticMap) numerical modules and renders with Matplotlib Agg. The package's desktop CLI opens Qt even when saving a PNG, so the server uses `domains.structure.dichromatic.adapter` instead. It compares only sites in the same axial phase. Optional local pairs are mutual nearest neighbors with separation greater than the exact tolerance and no greater than `local_cutoff` (at most `0.5 a0`); a local pair does not establish an exact CSL. An unrecognized exact cell remains absent rather than being replaced by a strained fit. The reported common cell preserves the A layer in two dimensions and is not necessarily primitive in three dimensions; its atom counts use a half-open planar cell and one full axial repeat. This tool projects ideal unstrained lattices and does not build a relaxed boundary or calculate its energy.

Each agent or browser export creates the independent `dichromatic_map` workflow under `artifacts/structure/dichromatic_map/{run_id}/`. Artifacts include canonical `input.json`, `dichromatic_map.png` (`dichromatic_map_png`), `dichromatic_map.json` (`dichromatic_map_data`), `render.log`, and the run manifest. The completed run and its `scientific_conclusion` fact retain purpose, origin, optional `source_run_id`, actual geometry, unit conversion, counts, limitations, and exact artifact references. Full point arrays remain in the JSON artifact. The source-run link records scientific context; no CFG is imported by this ideal-lattice projection tool. The browser and agent use the same export path and memory.

The [interactive cell workflow](dichromatic-map.md) keeps preparation, user confirmation, and strain application separate. The agent cannot choose candidate IDs for the user; it receives only a request reference until the browser posts the selection. Strain is an explicit operation on that selected projected cell and does not change the original unstrained CSL classification.

Standalone use in the `GBclaw` conda environment:

```bash
PYTHONPATH=src conda run --no-capture-output -n GBclaw \
  python -m domains.structure.dichromatic.adapter \
  --input-json /tmp/dichromatic-input.json --output-dir /tmp/dichromatic-output
```

The input file contains a `DichromaticMapRequest` JSON object, for example `{"lattice":"FCC","axis":"110","angle_deg":38.94244126898138,"width":12,"height":9}`. Use module invocation to avoid shadowing the installed `dichromatic_map` package. Standalone exports write PNG and JSON; project run/fact recording belongs to the agent/browser adapter. Requests are bounded before projection: reduced axis components must lie in `[-12,12]`, and the combined temporary candidate mesh for both grains must not exceed 100,000 columns.

## LAMMPS Tools

Simulation setup and execution are separate actions. A configuration call resolves and validates the intended physical model, records its parameters, and returns a run ID. The execution call uses that same ID to produce structures and numerical results. This makes it possible to inspect what will run and later trace each result back to its exact input.

CFG exports from GB minimization, shear, GB energy, and structure minimization include the configured chemical element for every LAMMPS atom type, including boundary slabs and intermediate frames. Grain type IDs may map to the same element (for example, `Ni Ni`), so each atom's LAMMPS type is also written as an auxiliary `type` column. Coordinates use 12 significant digits, and the H0 cell is rewritten with the exact box (LAMMPS itself prints six), so these CFG files convert back to LAMMPS data without loss.

Each workflow's tools are implemented in its own `tool.py` under `src/domains/lammps/` (`minimize/`, `structure_minimize/`, `energy/`, `shear/`, `neb/`, and `cfg_convert/`); `infer_tilt_orientation` is in `orientation.py`. `src/domains/lammps/catalog.py` defines `list_available_potentials` and collects all of them in the single `LAMMPS_AGENT_TOOLS` list, which the LAMMPS extension declares as the LAMMPS worker's tool provider:

| Tool | Purpose |
| --- | --- |
| `list_available_potentials(element="")` | Read `potentials.json` from the configured potentials directory, optionally filtered by element. |
| `convert_cfg_to_lammps(cfg_paths, ...)` | Convert project-local extended CFG files into `atom_style atomic` LMP artifacts with their LAMMPS atom types, recovering a compatible registered source-LMP box origin when available, and index the complete mapping. |
| `infer_tilt_orientation(...)` | Infer a LAMMPS orientation from a tilt axis and GB plane/family. Concrete planes such as `(100)` use `plane_is_family=false` (the default); only families such as `{100}` use `true`. |
| `estimate_gb_box_size(...)` | Read-only estimate of dimensions, x mismatch, valid repeat-match candidates, and approximate atoms. Omitted grain sizes are auto-sized; explicit sizes are measured unchanged. Every candidate includes material dimensions in nm plus complete top/bottom `[nx,ny,nz]` counts, independent of `x_pbc`. |
| `write_gb_minimize_config(...)` | Validate input, auto-size omitted repeat counts, persist the resolved explicit counts, and return a configured minimization `run_id`. |
| `run_gb_minimize(run_id, ...)` | Run external MPI minimization under the same stable run. |
| `write_structure_minimize_config(purpose=..., source_lmp=..., structures=..., ...)` | Configure an existing LMP or ordered batch at fixed box; supply either `source_lmp` or `structures`, preserving each item's original-frame provenance and purpose. |
| `run_structure_minimize(run_id, ...)` | Run the independent `structure_minimize` workflow; batch items execute sequentially into one run directory, without applied loads. Optional fixed IDs freeze all coordinates. |
| `write_gb_energy_config(...)` | Validate a periodic x-z/y-vacuum GB-energy input and return a configured `gb_energy` run ID. |
| `run_gb_energy(run_id, ...)` | Relax the bicrystal and four bulk/surface references, calculate GB energy, and persist its artifacts/run/fact. |
| `write_gb_shear_config(source_run_id, ...)` | Link a completed minimization or completed shear final state and return a configured shear `run_id`. |
| `run_gb_shear(run_id, ...)` | Run external MPI quasi-static x shear under that run. |
| `write_gb_neb_config(initial_structure_path, final_structure_path, ...)` | Configure a path between exact CFG/LMP endpoints with optional shear boundary constraints and tracked endpoint lineage. |
| `run_gb_neb(run_id)` | Run the configured NEB path, converting CFG endpoints internally and exporting the MEP profile. |

`convert_cfg_to_lammps()` creates and completes one `cfg_convert` run in a single call. Its canonical `input.json` records source paths and SHA-256 fingerprints; the run snapshots each CFG, writes each `converted_lmp`, and registers both sides in the manifest and semantic artifact index. Because extended CFG omits `xlo/ylo/zlo`, the converter looks for a compatible registered `minimized_lmp` or `sheared_lmp` in the source run and reuses its box origin. If none is available, it explicitly returns a warning and centers the box at `(0,0,0)`. When the CFG cell is that LMP's box, exactly or as LAMMPS prints it, the exact cell is used as well (`cell_source`). Atom IDs and image flags are preserved. Atom types come from the CFG `type` column; a CFG without one takes them from that LMP by atom ID; otherwise types follow element labels, so types that share an element merge, and a warning says so (`type_source`). Triclinic cells use the H0 rows as cell vectors. The returned run result contains exact source-to-output paths for downstream `read_data` workflows plus the origin reference and fingerprint.

### Existing-structure minimization

Use `convert_cfg_to_lammps(cfg_paths=[exact_frame_path], purpose=...)` when the desired frame is only available as CFG. Select its returned `converted_lmp` artifact ID or exact project-relative path, then pass it as `source_lmp` to `write_structure_minimize_config`, with a required `purpose` describing the intended operation. Run the returned ID with `run_structure_minimize`. Existing LMPs skip conversion.

For multiple frames, give the entire ordered list to one LAMMPS worker. Convert missing LMPs in one `convert_cfg_to_lammps(cfg_paths=[...], purpose=...)` call, preserving every returned source/output mapping. Then call the minimization writer once with `structures` instead of `source_lmp`, and call its returned run ID once:

```python
write_structure_minimize_config(
    purpose="Compare unloaded states selected around stress drops",
    structures=[
        {"source_lmp": "<exact first LMP artifact ID>",
         "purpose": "Relax the selected pre-drop peak without loads"},
        {"source_lmp": "<exact second LMP artifact ID>",
         "purpose": "Relax the selected post-drop minimum without loads"},
    ],
)
```

Items execute sequentially in input order under one `structure_minimize` run. Each item may override the shared purpose, constraints, potential/PBC and minimization settings. Supplying an item's `fixed_ids` or `fixed_ids_paths` replaces its entire shared fixed-ID selection; `fixed_ids=[]` therefore makes that item all-free. All inputs are validated before execution. The batch result preserves every item and its exact source/output mapping; downstream tools must select the intended output artifact rather than only the batch run ID. `label` is an optional human-readable item name, such as `before_drop`; it does not rename the published structure. Omit it when processing many frames. An omitted item `purpose` inherits the batch purpose.

The box remains fixed (including tilt and origin), and exactly one `minimize` runs per item. All atoms are free by default; no springs, applied loads or `box/relax` are restored. `fixed_ids` and/or `fixed_ids_paths` (project-local ID files or artifact IDs) freeze every listed atom in x/y/z using `setforce 0 0 0`. PBC is separate from these constraints: inherit the selected source's PBC/potential, or explicitly supply `boundary`, registered `potential_name`, and per-type `elements` for an untracked import. Fixed-cell boundaries accept `p` and `f`; shrink-wrap is rejected. Atomic orthogonal/restricted-triclinic data and mapped `eam/fs`, `eam/alloy`, `adp`, `nep` potentials are supported.

Runs live in `artifacts/lammps/structure_minimize/{run_id}/`. Single runs contain `minimized.lmp`, `minimized.cfg`, input/potential snapshots, trajectory, log and `minimize_run.json`. A batch publishes every minimized structure in that same run directory using the resolved input LMP basename plus `_minimized`: `frame_120.lmp` produces `frame_120_minimized.lmp` and `frame_120_minimized.cfg`. Repeated basenames receive numbered suffixes such as `frame_120_minimized_2.lmp` and `.cfg`. Artifact-ID inputs use the actual source filename, not the artifact ID or optional label.

Each item's inputs, working files and logs are kept under `items/<item_key>/`, where the internal key includes its ordered index. Ordered result `items` retain `item_index`, `item_key`, purpose, source/origin, constraints, status and exact output artifact IDs/paths. The first failure or cancellation stops the batch; successful outputs remain available and later items remain `pending`. Aggregate counts distinguish completion, failure, cancellation, pending work and convergence.

Results record before/after energies and forces, stopping criterion, convergence, preserved cell/IDs/types and fixed-atom displacement. Completed status does not imply convergence. Unconstrained relaxation can change the loading displacement; source strain describes the original frame only.

Conversion records every exact parent frame in `run_sources`, including multiple frames from one run. Original run/artifact/path/hash and available step/strain metadata, along with `purpose`, survive conversion and minimization in configs, artifact metadata, semantic memory, search and worker handoffs. The independent workflow does not change the default shear source, which still requires GB-construction boundary-ID artifacts. Its outputs can be selected directly as endpoints for all-free NEB paths.

### Config Writing

`write_gb_minimize_config()`:

- Exposes every `GBMinimizeConfig` scientific field as a typed tool parameter; uncommon minimization controls retain defaults and can be omitted by the agent.
- Resolves catalog defaults, builds the complete canonical config, and validates its types and numerical ranges.
- Resolves and validates the potential file, then stores its absolute path in the config.
- For `eam/alloy`, `eam/fs` and `adp`, LAMMPS sets each element's mass from the potential file, so the config records that mass; an explicit `mass` that differs is replaced and reported as a warning. `write_gb_energy_config()` does the same.
- Estimates geometry from that exact final config and applies `box_size_violations()`.
- Auto-sizes an omitted `top_size` or `bottom_size` with integer recommendations. Explicit positive counts, including fractions, are preserved without truncation and produce warnings.
- Agent-selected invalid geometry creates no run and returns measurements and recommendations. Exact sizes explicitly confirmed by the user may set `user_confirmed_size_override=true` to create the run while persisting all policy findings as warnings.
- Uses 20 nm as the default preferred x size for both boundary modes. A positive `target_x_nm` changes that ranking preference but is not a lower bound; agent recommendations retain the 10 nm y and 1 nm z per-grain minima. Every recommended integer top/bottom x-repeat pair must have mismatch below the default 2% threshold from `domains/lammps/common/defaults.py`.
- Applies the same x repeat matching and mismatch validation for `x_pbc=false` and `x_pbc=true`; the flag affects only x padding and continuous connection in the LAMMPS box.
- Creates a configured semantic run and writes the single canonical `input.json` in its artifact directory.
- Mirrors the canonical config into searchable run parameters while keeping `input.json` authoritative.
- Registers that file once under the primary `config` artifact role. The execution path reuses it and does not create a duplicate `config_used.json`.

The config schema lives in `src/domains/lammps/minimize/config.py`. Core dataclasses:

- `MaterialConfig`
- `SimulationConfig`
- `PotentialConfig`
- `Orientation`
- `GrainConfig`
- `GBMinimizeConfig`

The default simulation is Fe BCC with `a0=2.8553`, `min_style=cg`, `relax_cycles=4`, `boundary="p p p"`, and `x_pbc=false`, so the x box retains `y_vacuum` on each side. Prefer keeping `boundary="p p p"`; open vs periodic x is controlled by `x_pbc` / `y_vacuum`. Non-`p p p` boundary values are rejected at write/load time so the agent can regenerate. Setting `x_pbc=true` uses `y_eps` instead, producing an effectively contiguous periodic x box suitable for subsequent sliding workflows. `enable_initial_staged_relaxation=false` by default; enabling it runs three preliminary minimizations (freeze bottom, freeze top, then all atoms free) before the normal cyclic box relaxation.

### Run Execution

`run_gb_minimize()`:

1. Resolves the configured `run_id` inside the active project.
2. Uses `LAMMPS_AGENT_MPI_RANKS` by default unless the user explicitly passes `np`.
3. Writes directly to `artifacts/lammps/minimize/{run_id}/`.
4. Uses the already registered config and registers newly produced logs, structures, boundary IDs, and trajectory by artifact role.
5. Calls `run_in_lammps_env()` to execute:

```text
conda run --no-capture-output -n <LAMMPS_CONDA_ENV>
  mpirun --map-by core --bind-to core -n <np>
  python -u src/domains/lammps/minimize/engine.py --output-dir ... --config ...
```

6. Extracts key metrics from `log.lammps`.
7. Transitions the same semantic run to completed, failed, or cancelled.

The minimization runner saves complete launcher streams as `launcher.stdout.log` and `launcher.stderr.log`, indexed as `launcher_stdout` and `launcher_stderr`. These remain available when Python imports fail before `log.lammps` is created. The returned error extracts the actual Python exception or LAMMPS error instead of truncating the start of the traceback. For deterministic dependency/launcher failures, agents report the blocker and wait for a fix before creating an unchanged replacement, unless the user explicitly requests a retry.

Managed LAMMPS workflows map each MPI rank to a physical core. LAMMPS screen and logfile output uses `-nonbuf`, with explicit flushing after each Python command and `thermo_modify flush yes` for thermo lines during long minimizations. This also supports LAMMPS versions where `-nonbuf` only affects partitioned runs. The web log preview checks for updates every 500 ms. Thermo output keeps its configured step interval (100 steps for GB minimization).

The runner also writes and indexes `minimize_run.json`. It contains one record for every LAMMPS minimization stage, including its stopping criterion, convergence flag, final energy/forces, and iteration counts. Convergence is assessed only from the final stage (`convergence_basis="final_minimization"`). Intermediate nonconvergence counts remain historical diagnostics and do not generate convergence warnings. A final stage that misses the tolerance does produce a warning; completed outputs remain usable with that qualification.

The LAMMPS delegate returns the worker's conclusion, compact run/manifest references, final convergence metrics and verified analysis follow-ups. It does not return the complete tool trace or repeat the delegated task. Internal traces still support plan tracking and streamed UI events; full scientific evidence remains available through indexed artifacts.

The completed result and `minimize_run.json` also retain `structure_metadata` from the canonical construction config. When `x_pbc=false`, `y_vacuum>0`, and the minimized CFG is present, the result includes a required `analysis_followups` request for `analyze_gb_periodicity`. The orchestrator delegates this to Analysis by default unless the user opted out; the LAMMPS runner does not start OVITO itself. This is configured X-vacuum eligibility, and Analysis independently checks the relaxed structure. `boundary="p p p"` and imported CFG PBC flags may remain periodic. Failed/cancelled runs and continuous-periodic X runs do not trigger this handoff. Reuse an equivalent completed analysis of the exact source rather than rerunning minimization. An unsupported or failed analysis is reported separately from its successful source minimization.

LAMMPS subprocess control is split across:

- `src/domains/lammps/common/runner.py`: `conda run`, timeout handling, and cancel polling.
- `src/core/execution/sessions.py`: session cancel events, active process tracking, and process-tree termination.
- `src/domains/lammps/common/resources.py`: serial/weighted MPI-rank admission control through the `SlotPool` in `src/core/execution/resources.py`.

### GB Energy

`write_gb_energy_config()` exposes the same typed material, orientation, repeat-count, potential, and minimization fields as the minimization writer. It uses the same deterministic sizing and geometry limits, while requiring `x_pbc=true`, `boundary="p p p"`, and positive y vacuum. This restriction matches the implemented thermodynamic cycle: the x-z GB plane is periodic and only the two outer y free surfaces need explicit subtraction. The cell contains exactly one internal GB at y=0; y vacuum separates periodic images. Results record `interface_count=1`, two free surfaces, and denominator `A`. There is no two-GB/`2A` mode.

`run_gb_energy()` relaxes the bicrystal, then independently relaxes top/bottom bulk and two-surface slab references with the bicrystal's relaxed x/z dimensions. It records bulk energies per atom, one-surface energies, interface area, and `gamma_GB = (E_bicrystal - E_bulk,top - E_bulk,bottom - gamma_s,top*A - gamma_s,bottom*A) / A`, reported in J/m² and eV/Å². The run registers the bicrystal and all four reference logs, structures, and trajectories, plus `gb_energy_summary.csv` and complete `gb_energy.json`. The compact run result is searchable in Project Memory. A successful run also deterministically writes the searchable `scientific_conclusion` fact `run:<run_id>:main_conclusion`; no manual model memory-write tool is introduced.

### GB Shear

`write_gb_shear_config()` accepts an explicit completed minimization or completed shear `source_run_id`, or resolves the latest completed minimization. It resolves the final LMP and both boundary ID files through indexed artifact roles. Source paths, element/mass, boundary condition, pair style, and potential file are inherited from that parent and are not arguments of the shear writer. Loading, minimization, and output settings are exposed as typed arguments; there is no free-form `overrides_json`. The canonical validated JSON is stored as the primary `input.json` and mirrored into searchable run parameters.

`run_gb_shear()` reconstructs the two boundary groups by atom ID and performs the configured `cg` or `fire` minimization at every target strain. `load_type="displacement"` constrains x and y for both boundary groups and directly displaces only the top group at each increment. `load_type="spring_force"` constrains each boundary atom's y coordinate and applies bottom/top x spring tethers. Actual shear strain uses the current top/bottom y-COM spacing at each point.

Material element and mass are required canonical fields inherited from the parent; mass is retained as provenance because `read_data` already restores it. Before launching LAMMPS, the managed runner re-resolves that parent and rejects changes to the source fingerprint, artifact names, material, boundary condition, or potential.

Both strain increments are non-negative magnitudes. `loading_direction` accepts `x` or `-x` and selects the first loading direction. A null `steps_per_half_cycle` keeps the full run unidirectional; a positive integer reverses the direction after that many steps. `half_cycles` declares the intended number of segments. When total steps are less than `steps_per_half_cycle*half_cycles`, the writer returns a warning and does not silently add work. A completed shear can continue from its final `gb_sheared.lmp`; the child run snapshots that LMP and both ID files and adds its relative strain to the parent's cumulative strain. Readers validate the schema of `input.json` and `shear_run.json` and reject missing or incompatible schema versions rather than migrating these files.

The copied `top_boundary_ids` and `bottom_boundary_ids` are registered on the shear run itself, so downstream NEB configuration should use those roles without walking back to the minimization.

When arguments are omitted, every value is read from the flat `SHEAR_*` constants in `src/domains/lammps/common/defaults.py`; the tool schema and runtime config classes import those same constants. For spring loading, the driver multiplies its configured per-atom value by the boundary atom count to form the total LAMMPS spring constant. For displacement loading, both spring fields in canonical `input.json` are `null`.

The run writes `inputs/source.lmp`, copied boundary-ID files, `config_used.json`, `shear_curve.csv`, `structures/gb_shear_step_*.cfg`, `gb_sheared.cfg`, and `gb_sheared.lmp`. The curve contains step, increment stage, active loading direction, 1-based loading half-cycle and full-cycle numbers, step within the half-cycle, target and actual shear strain, current slab spacing, corrected top/bottom shear stress, and all six corrected stress-tensor components. Spring reaction stresses use the initial occupied-atom x-z area. The LAMMPS tensor is converted from pressure to Cauchy stress and multiplied by the initial-box-volume / initial-occupied-atom-volume correction, avoiding vacuum dilution. A failed force-tolerance check is counted in run metadata and does not terminate later loading steps. For displacement loading, top shear stress is the corrected `sigma_xy` value and bottom shear stress is `-sigma_xy`; all six stress-tensor components remain populated.

`shear_run.json` is written atomically after every completed loading point. It contains `running`/`completed`/`failed`/`cancelled` status, last completed step, initial atom bounds, reference area/volumes and stress correction factor, current top/bottom COM values, current spacing, corrected top/bottom and tensor stress, a `points` array containing every completed point together with its loading direction/half-cycle/full-cycle identifiers, and a `frames` array mapping CFG-producing steps to exact paths. `dump_every` controls only the CFG/frame frequency and never the point history.

The semantic run row is updated with the last completed step/strain/convergence, current loading direction/cycle, current spacing, the complete corrected current stress state, and the running peak summary. The monitor skips unchanged metadata and registers only newly discovered frame artifacts as permanent derived results.

Terminal results index source run/fingerprint, result completeness, corrected peak top shear stress with step/strain/direction/cycle, its boundary-stress source, corrected current stress state, COM values, final strain/direction/cycle, and convergence count directly from `shear_run.json`. The human-facing `shear_curve.csv` remains in the run directory but is not registered in Project Memory. Frame metadata, structures, logs, and `shear_run.json` remain permanent artifacts.

### GB NEB

`write_gb_neb_config(initial_structure_path, final_structure_path, ...)` accepts exact project-relative paths or artifact IDs for `.cfg`/`.lmp` endpoints. They can be shear frames or selected outputs of single/batch `structure_minimize` runs. Each NEB call takes exactly two endpoint structures. Select their exact output artifacts from the same batch or different single/batch runs. A batch is a source of candidate endpoints; the tool does not accept the whole batch as one endpoint, pair its items automatically, or calculate a multi-endpoint path. A structure file does not contain active LAMMPS `fix` commands.

`constraint_mode="none"` makes all atoms free and requires no boundary-ID files. For the shear boundary constraint, use `constraint_mode="shear_boundaries"` with both `top_boundary_ids_path` and `bottom_boundary_ids_path`. If the mode is omitted, supplied ID files select `shear_boundaries`; otherwise it selects `none`. Explicit `none` rejects ID inputs, so supplied constraints cannot be silently ignored.

Material, mass, PBC, pair style and potential are inherited from the exact registered endpoint's simulation context, including its batch item. Optional `shear_config_path` supplies a shear simulation context explicitly. Untracked imports require registered `potential_name`, `boundary` and `element`. The endpoints must have compatible physics, atom IDs and cells; the NEB workflow supports one element. The writer freezes file sizes and SHA-256 digests in the canonical config, and records the exact endpoint/original-frame sources plus `purpose` in project memory.

CFG endpoints are converted deterministically during preparation inside `run_gb_neb()`; there is no need for a separate CFG conversion call. Direct LMP inputs use `atom_style atomic`.

The default partition topology is 16 replicas × 1 rank per replica. `.env` may override these with `GBCLAW_LAMMPS_NEB_REPLICAS` and `GBCLAW_LAMMPS_NEB_RANKS_PER_REPLICA`, and `write_gb_neb_config` accepts `replicas`/`ranks_per_replica`; the product may not exceed `GBCLAW_LAMMPS_TOTAL_MPI_RANKS` (default 18). The driver passes the intact global communicator to LAMMPS and uses `-partition`, preserving the universe communication required by `fix neb`. In `shear_boundaries` mode it rebuilds the top/bottom groups by atom ID and installs `fix setforce 0 0 NULL` after `fix neb`; `none` installs no boundary force constraint.

A completed run registers per-replica trajectories/final LMP files, logs, human-readable `mep_profile.csv`, and agent-readable `mep.json`. The JSON contains only the complete reaction-coordinate/energy profile and topology. Energies are each final replica's potential energy at full precision (`energy_source: replica_evaluation`); the NEB table that LAMMPS prints has eight significant digits (1 meV for a 10^4 eV cell) and is used only if the two disagree (`neb_table`). Saddle candidates and barriers are derived separately by Analysis, so a path may correctly contain zero, one, or multiple saddles.

## Analysis Tools

Analysis consumes identified simulation outputs and produces new evidence linked to them. Choose the established analysis tool when it covers the question: this keeps definitions such as stress-drop detection, displacement references, and NEB saddle selection consistent across cases. Consult the method-specific qualifications below before interpreting a plot as a physical conclusion.

Defined in `src/domains/analysis/periodicity_tools.py` and in the `tool.py` of `stress_strain/`, `neb_mep/`, and `rendering/` under `src/domains/analysis/`. `src/domains/analysis/catalog.py` collects them in `ANALYSIS_AGENT_TOOLS`, which the Analysis extension declares as the Analysis worker's tool provider:

| Tool | Purpose |
| --- | --- |
| `analyze_gb_periodicity(...)` | Combine coordinate and elastic-strain analysis of a completed X-vacuum `gb_minimize` CFG, return a summary and ranked review candidates, retain complete evidence, and compare construction mismatch/size options for user selection. |
| `stress_strain_analysis(...)` | Analyze a `gb_shear` history with stress-drop thresholds and local extrema, combine their evidence, summarize first and recurrent drops, map event steps to frames, and write JSON plus a comparison PNG. |
| `plot_stress_strain_comparison(...)` | Plot 1–12 completed shear histories; a one-item source list renders a single curve. Supports labels, colors, line styles, markers, signed/absolute axes, legend, titles, and line/marker sizes. |
| `analyze_neb_mep(...)` | Detect every discrete positive-to-negative MEP derivative transition, including flat saddle plateaus, and write reusable multi-saddle JSON plus a conclusion fact. |
| `plot_neb_mep(...)` | Plot a completed `gb_neb` run's indexed minimum-energy path as reaction coordinate versus energy difference, with configurable curve/saddle styling, legend, annotation, title, and labels. |
| `render_atomic_structure(...)` | Render an indexed CFG through OVITO, optionally adding reference-frame displacement arrows, with PNG/JSON output and source provenance. |
| `render_gb_plan_view(...)` | Locate an approximately planar Y-normal boundary in an indexed CFG and render its facing layers, optionally with reference-frame displacement arrows, diagnostic profiles, and source lineage. |

Implementation notes:

- Ordinary single/multiple-curve plotting uses `plot_stress_strain_comparison`; drop/extrema identification uses `stress_strain_analysis`. `delegate_analysis(required_artifact_roles=...)` can require exact output roles. The delegate checks scientific tool results against the central memory registry and verifies indexed files against their manifests before publishing completion. Missing outputs or unrecovered tool failures are recorded as failed/partial in conclusion memory and task state. Corrected retries and verified reuse remain supported; full traces are not returned.
- Stress–strain input is selected by a completed shear `source_run_id` (a running/failed source only with explicit `allow_partial=true`); the tool reads the indexed `run_metadata` artifact instead of putting the full point history into model context.
- The default signal is absolute `top_shear_stress_mpa`. Two methods run on the same history. `threshold_detection`/`threshold_events` retain the median/MAD robust threshold for adjacent stress drops and merge consecutive above-threshold drops; `minimum_drop_mpa` selects an explicit threshold. `extrema_detection`/`extrema_events` identify maxima where slope signs change from positive to negative, and minima where they change from negative to positive. Flat plateaus count as single extrema. Each peak is paired with its following minimum to describe the whole drop, including gradual descent below the threshold method's cutoff.
- Extrema slopes follow loading order and divide stress differences by absolute actual-strain increments, so reverse loading has the same peak/minimum interpretation. Zero or missing strain increments use stress-difference signs and record unavailable slopes. With default `extrema_tolerance_mpa=0.0`, every sign reversal is retained and equal-value plateaus are combined. A positive value combines consecutive points only while their total stress range remains within the tolerance; cumulative drift beyond that range still forms a trend. This bounded-plateau tolerance can coalesce small fluctuations, but is not a drop-amplitude cutoff. No smoothing or separate event-amplitude filter is applied.
- Neither method compares across loading half-cycle boundaries or missing steps. Every finite completed point is included regardless of its convergence flag; nonconvergence is retained as warning metadata and never creates a plot break.
- `combined_events` merges overlapping evidence for each chronological descent, retaining `both`, `extrema_only`, or `threshold_only` support and the contributing event indices. Top-level `events`/`event_count` refer to this combined view; the separate methods retain their own event lists and counts. Similar-valued repeated peaks/minima remain separate events. A terminal descent without a following rise has `post_minimum_confirmed=false`; its endpoint is not evidence that the structure has reached a new stable state.
- `curve_summary` separates the first complete drop from subsequent drops within each segment/half-cycle through `first_complete_event` and `subsequent_events`. It records amplitude distributions, peak/minimum ranges, first-to-later median ratios, recurrence statistics, representative event IDs, and `interpretation_notes`. An unresolved initial descent is kept separately; `first_complete_is_first_observed_event` shows whether the first complete event is also the first observed event. This exposes both an initial large drop and later smaller repeated drops even if the global threshold misses the latter. These are descriptions of the stress curve; a structural-change interpretation requires structural evidence.
- `stress_strain_analysis.json` contains column arrays, all three detection views, peak/minimum steps/strain/drop, minimum confirmation, curve summaries, and frame mappings. `stress_strain_curve.png` compares the threshold, extrema, and combined views in three panels. Extrema/combined post-event frames must be at or after their targets within the same segment; if none exists, the mapping is unavailable. The raw threshold view retains nearest-step frame mappings, which may precede the target and cannot then represent a post-drop structure. Approximate later frames retain their step offset and must not be described as the exact minimum structure. Both files, plus the primary `source_shear_run.json` snapshot (`source_metadata_snapshot`), are permanent artifacts under `artifacts/analysis/stress_strain/{run_id}/`. The tool replies carry a `model_view` for the model boundary: `stress_strain_analysis` lists every combined event as one row (support, peak step/strain/stress, event and end steps, drop, post-event stress and frame, minimum confirmation) with the detection parameters, local extrema and curve summary, and `plot_stress_strain_comparison` lists per-curve statistics (point counts, loading directions, first/last/min/max strain and stress with their steps). The worker reports from that view and reads the JSON artifacts only for a field it lacks. Above 15,000 characters the view first replaces `local_extrema` with `{"omitted": true, "read_from": "stress_strain_data"}`, then cuts trailing rows behind one `{"omitted_events", "total_events", "read_from"}` marker (the comparison view uses the same marker for omitted curves); these markers are the only signs of left-out content.
- After the analysis run completes, the tool deterministically writes exactly one `scientific_conclusion` fact at `run:<analysis_run_id>:main_conclusion`. The compact fact contains the source shear run, detection parameters, counts and event evidence for all three views, combined support, curve summary, peaks/minima, and frame steps; full arrays remain only in the JSON artifact. This makes the conclusion searchable without depending on post-turn model extraction. Memory handoffs are bounded previews; use the exact conclusion or indexed analysis JSON when either method or first/subsequent-event details are missing from a preview.
- `analyze_neb_mep` and `plot_neb_mep` require one completed `gb_neb` source with exactly one indexed `mep_json`. Both validate the profile and use the same finite-difference definition: every interior positive-to-negative derivative transition is a saddle candidate, while a zero derivative plateau between those signs yields one candidate. The analysis writes `neb_mep_analysis.json`; plotting writes `neb_mep_plot.json` plus `neb_mep.png` under `artifacts/analysis/neb/{run_id}/`. Results retain the source fingerprint, derivative tolerance, full saddle list, highest saddle, endpoint energy difference, and per-saddle barriers.
- Generic render input is selected by `source_run_id` plus artifact role; output is written to `artifacts/analysis/render/{run_id}/render.png` and `render.json`.
- When an `intermediate_structure` role has multiple CFG artifacts, `render_atomic_structure` requires an exact `source_step` or `source_artifact_id`; use an artifact ID for a batch-minimized CFG. An approximate frame can only describe a nearby structure when `dump_every` did not save the event step itself. Post-minimum structures must use an exact or later frame in the same segment; a pre-minimum frame cannot stand in for a post-drop structure.
- `analysis_mode` supports `CNA`, `DXA`, and `none`.
- Default mode is CNA, and no atom types are hidden by default.
- OVITO rendering is launched as `sys.executable src/domains/analysis/rendering/ovito_rendering.py ...` in a subprocess to avoid crashing the API process with Qt/OVITO failures.
- A successful render creates a linked semantic run and registers the PNG and JSON as permanent derived provenance.

### Relaxed grain-boundary periodicity candidates

`analyze_gb_periodicity(source_run_id=..., purpose=...)` requires a completed `gb_minimize` source, its indexed `minimized_cfg`, and canonical construction metadata showing `x_pbc=false` with positive X vacuum padding. An exact `source_artifact_id` resolves ambiguous artifact roles. The supported geometry is one approximately planar, Y-normal interface in an axis-aligned orthogonal cell. The analyzer verifies occupied extents and excludes exterior surface atoms; configured vacuum and imported PBC flags alone do not establish usable interior evidence.

No top:bottom ratio is supplied. PTM bulk orientations and actual shortest crystal translations generate low-order lattice-match candidates. Layered coordinate FFT, full 3D matching and atomic-row motifs are combined with signed elastic-strain FFT and local strain intervals. Each grain's dominant FCC/BCC type and effective lattice constant are inferred from far-field crystalline atoms. Only valid crystalline atoms contribute to elastic-strain profiles; noncrystalline GB-core atoms are excluded. The reported volumetric strain is OVITO's `tr(Eulerian-Almansi strain)/3`, not the fractional volume change. Source orientation metadata anchors grain labels and signed indices, while source construction counts remain context rather than a target result. Candidates retain supporting and conflicting evidence, finite-window limits, harmonics and local-spacing variation. A small structure can leave the long scale unresolved; a Fourier peak alone cannot identify a unique repeat or a secondary-dislocation Burgers vector.

Coordinate and strain spectra retain weak actual maxima and explicit review failures, without a fixed percentage-of-largest-peak cutoff. Strain review uses absolute amplitude, local prominence, taper sensitivity, actual atom rebinning and smoothing checks. Near/far strain curves use common physical power units; a peak shared with the far field is descriptive evidence and does not automatically fail review. Colored S/L guides are independent motifs, local intervals or coordinate hypotheses, not assigned Fourier maxima. A nearby lattice association retains both the measured row-motif length and the coordinate translation, their discrepancy and alternative associations; fractional full `a[hkl]` counts are preserved.

Ranked pure-X repeat candidates must pass a two-grain lattice-translation check. For `c*a[uvw]` in a reduced integer direction, FCC permits half-integer coefficient steps when `u+v+w` is even, while BCC permits them only when all three indices are odd; otherwise the step is one. Zero is even. For nonprimitive indices, divide the step by their gcd. These real-space rules follow from the primitive lattice vectors ([FCC](https://lampz.tugraz.at/~hadley/ss1/crystalstructure/structures/fcc/fcc.php), [BCC](https://lampz.tugraz.at/~hadley/ss1/crystalstructure/structures/bcc/bcc.php)); they are not diffraction extinction conditions. Each allowed nominal grain length must match the measured translation within 0.3 nearest-neighbor distances. This numerical tolerance does not make arbitrary coefficients legal. The check is necessary but not sufficient for a PBC cell; chemical ordering, mismatch and boundary matching remain separate. Unknown indexing and oblique translations do not pass a pure-X check.

`lattice_filter` reports compatible/excluded/unresolved counts. Only compatible candidates enter the default priority/alternatives and measured PBC size options. All original hypotheses and rejection reasons remain in JSON/CSV and the summary plot. Short row motifs, strain intervals and FFT peaks retain their measured lengths; `pure_x_repeat_eligible=false` distinguishes these observations from construction options.

The linked `gb_periodicity` run writes permanent artifacts under `artifacts/analysis/gb_periodicity/{run_id}/`:

| File | Artifact role | Content |
| --- | --- | --- |
| `multiscale_review/multiscale_summary.png` | `gb_periodicity_overview` | The only summary: structure/strain spectra, spatial features, short/long scales, all coordinate candidates and glossary. |
| `period_candidates.json` | `gb_periodicity_data` | Detailed candidates, parameters, evidence, limitations and source metadata. |
| `agent_summary.json` | `gb_periodicity_summary` | Compact candidate evidence and unresolved user selection. |
| `multiscale_review/multiscale_review.json` | `gb_periodicity_multiscale_data` | Joint evidence, review ranking, motif associations and scale relations. |
| `multiscale_review/analysis_status.json` | `gb_periodicity_multiscale_status` | Joint-analysis outcome, compact candidate handoff and any unavailable-evidence reason. |
| `multiscale_review/all_candidates.csv` | `gb_periodicity_candidate_table` | Every coordinate candidate, lattice counts, matching errors and spectral evidence. |
| `multiscale_review/ALL_CANDIDATES.md` | `gb_periodicity_candidate_notes` | Readable complete candidate comparison. |
| `elastic/elastic_periodicity.json` | `gb_periodicity_elastic_data` | Full strain profiles/spectra, crystalline references, peak audits and coordinate comparisons. |
| `elastic/elastic_atoms.npz` | `gb_periodicity_elastic_atoms` | Per-atom strain, validity and grain/reference masks. |
| `elastic/review_index.json` | `gb_periodicity_elastic_index` | Compact index linking the current summary and detailed evidence. |
| `elastic/structure_fft_peaks.csv` | `gb_periodicity_structure_fft_peaks` | Actual structure maxima, far-field comparisons and review results. |
| `elastic/strain_fft_peaks.csv` | `gb_periodicity_strain_fft_peaks` | Actual signed-strain maxima, amplitude, numerical stability and far-field comparisons. |
| `elastic/strain_fft_review.json` | `gb_periodicity_strain_fft_review` | Strain review policy, profile counts and source fingerprints. |
| `local_strain/local_strain_events.json`, `.png` | `gb_periodicity_local_strain_data`, `gb_periodicity_local_strain_plot` | Spatial strain extrema, local intervals and their diagnostic plot. |
| `coordinate_rows/coordinate_rows.json`, `.png` | `gb_periodicity_coordinate_rows_data`, `gb_periodicity_coordinate_rows_plot` | Measured native row motifs, interruptions and their diagnostic plot. No plot is drawn when no interior row has 30 atoms; the JSON then has `status: no_qualifying_rows` and the reason. |
| `pbc_size_comparison.json` | `pbc_size_comparison` | Construction mismatch, repeat triplets, dimensions and sizing warnings. |
| `source_metadata.json` | `source_metadata_snapshot` | Exact source artifact and canonical construction context. |
| `analysis_policy.json` | `analysis_policy` | Analysis policy passed to the subprocess. |
| `boundary.json` | `gb_periodicity_boundary` | Resolved interface location, surface exclusions and selection evidence. |
| `boundary_selection.npz` | `gb_periodicity_selection` | Interior/defect/neighborhood masks for the analyzed structure. |
| `crystal_mapping.json` | `gb_periodicity_crystal_mapping` | PTM grain orientations and coordinate-to-crystal translation mapping. |
| `candidate_curves.npz` | `gb_periodicity_curves` | Full coordinate matching curves and numerical evidence. |
| `modulation_profiles.json` | `gb_periodicity_profiles` | Coordinate modulation profiles and local-spacing evidence. |
| `ptm_cache/ptm_atoms.npz` | `gb_periodicity_ptm` | Per-atom PTM structure, orientation and coordinates. |
| `ptm_cache/ptm_metadata.json` | `gb_periodicity_ptm_metadata` | PTM input fingerprint and analysis metadata. |
| `candidate_evidence.png` | `gb_periodicity_candidates` | Candidate scales and competing evidence. |
| `periodicity_diagnostics.png` | `gb_periodicity_diagnostics` | Boundary selection and periodicity diagnostics. |
| `in_plane_periodicity.png` | `gb_periodicity_in_plane` | Layer-aware in-plane evidence. |
| `long_period_modulation.png` | `gb_periodicity_modulation` | Long-scale signals, including unresolved/insufficient-window results. |
| `analysis.log` | `log` | Subprocess stdout/stderr (debug). |

Coordinate diagnostics and all joint-analysis detail outputs remain available alongside the summary. PTM structure panels use equal physical X/Y scales so their geometry is not stretched to fill the panel. The summary labels possible periods and unresolved evidence; it does not select a unique period.

Files are registered only when their analysis stage produces them. Unsupported or unresolved geometry retains the available diagnostic evidence and its outcome JSON; it does not imply that every PNG, a resolved boundary, or a crystal mapping is available. Consult the returned artifact manifest for the actual outputs. `multiscale_analysis_status` records whether joint evidence is available. If geometry, an elastic reference or a calculation prevents completion of the joint workflow, the run is marked failed with the reason and available diagnostics. No coordinate-only summary is generated as a replacement. When the joint analysis is unavailable, or completes without a longer-period candidate, the result warns that the GB may be too short along x to show a longer period.

The compact tool result provides the project-relative `summary_image_path`, with `image_paths` containing only the verified summary. Complete plots remain indexed in the manifest and in the saved `diagnostic_image_paths`; the compact reply omits their inventory. Analysis and the orchestrator use the compact candidate conclusions.

`review_priority_candidate` identifies the best-supported candidate under the declared review heuristic; `alternative_candidates` retains up to four alternatives. The priority may be unresolved and is not a probability, unique physical period or user selection. `short_motif_candidates` and `longer_candidates` distinguish scales, while `total_candidate_count` reports the complete retained candidate count.

Use `detailed_report_path`, `candidate_table_path`, the artifact manifest and conclusion lookup key to retrieve full evidence for a concrete question not answered by the summary. Normal replies do not reread the large numerical reports. Additional diagnostic images are displayed only when requested. `presentation_policy=summary_only` also applies to failures: an empty `image_paths` list displays no images, and a failure never advertises an uncreated summary. Unchanged deterministic failures should not be retried automatically.

When the requested long-wave band exceeds the observation window, the summary labels that panel unresolved and preserves the usable full-band evidence. Empty or nonpositive power is not forced onto a logarithmic axis and does not prevent summary generation.

Numerical curves/profiles, atomic strain, masks, local events, coordinate rows and peak audits remain in the indexed evidence artifacts instead of filling model context. PNG paths use the normal conversation image pipeline. The run and its deterministic conclusion fact preserve exact source lineage and analysis purpose for recall and reuse.

The orchestrator combines the returned candidates with `pbc_size_comparison`, which evaluates the original material/orientations and retained Y/Z sizes for `x_pbc=true`. Fractional X counts are preserved. Coordinate pairing uses mismatch `abs(L_top-L_bottom)/mean(L_top,L_bottom)`; the construction estimator uses `abs(L_top-L_bottom)/max(L_top,L_bottom)`. Report their definitions when comparing percentages. Further exact sizing uses read-only `estimate_gb_box_size`, not a new minimization. `selected_candidate_id` stays null and `requires_user_decision` stays true: neither analysis nor the default follow-up chooses a periodic cell, applies strain, or authorizes a new simulation.

### Generic structure displacement rendering

`render_atomic_structure` keeps the selected `CNA`, `DXA`, or `none` rendering mode and its atom display settings. Displacement arrows are disabled by default. Set `reference_run_id` to enable them, with `reference_artifact_role="minimized_cfg"` and an exact `reference_step` or `reference_artifact_id` when the role has multiple files. Both current and reference structures are selected from indexed CFG artifacts; `source_artifact_id` selects an exact current batch item. Both rendering tools crop the PNG to its non-transparent pixels with an empty 50-pixel border; the JSON camera record keeps the rendered size, the crop box and the final size.

The full structures must contain the same set of unique integer particle IDs. Matching happens before `hide_structure_types` is applied. OVITO computes current minus reference with affine mapping off; the complete three-component displacement retains rigid translation and strain. `minimum_image_convention=True` uses periodic minimum-image displacement. For motion exceeding half a periodic box between frames, use unwrapped positions and disable that setting. Optional `pbc` X/Y/Z booleans apply to both inputs; otherwise their imported periodicity is used.

The generic top view looks along -Z. Red arrows use centered alignment, `arrow_width=0.1`, and `arrow_offset_z=1.0`. The default `arrow_scale_mode="physical_capped"` leaves small vectors unamplified and uniformly reduces displayed vectors when their maximum 3D length exceeds `arrow_max_length=2.0`. A smaller positive limit is allowed; values above 2 are rejected. The optional `median_capped` mode maps the median nonzero XY projection toward `arrow_target_length=1.5`, then applies the same 3D cap. Relative lengths and physical vectors are preserved. Pure normal motion also obeys the cap. The GB plan view uses XZ and +Y instead.

The `render` run registers `render.png` as role `render` and `render.json` as `render_data`. The JSON retains both inputs' fingerprints and original-frame lineage when available, `purpose`, analysis mode, displayed selection, optional physical displacement vectors/statistics, and arrow settings. Completed runs write a searchable `run:<render_run_id>:main_conclusion` fact; compact results and facts omit the per-atom arrays. All vector and display lengths use the input length units (angstroms for GBclaw metal-unit inputs). An external file with unknown units has a cap of 2 input units. `displacement.render` records the policy, physical/display maxima, cap activation and algorithm version independently of the physical displacement arrays.

Standalone rendering uses the `GBclaw` conda environment:

```bash
conda run --no-capture-output -n GBclaw python src/domains/analysis/rendering/ovito_rendering.py \
  path/to/current.cfg --reference path/to/reference.cfg \
  -o render/current/render.png
```

Omit `--reference` for the usual rendering without displacement arrows. Optional controls include `--arrow-width`, `--arrow-offset-z`, `--arrow-target-length`, `--arrow-scale-mode`, `--arrow-max-length`, `--no-minimum-image`, and `--pbc 'p f p'`. The CLI writes the JSON beside the PNG; the agent adapter adds project indexing and memory provenance.

### Grain-boundary plan view

`render_gb_plan_view` accepts `source_run_id`, `source_artifact_role="minimized_cfg"`, optional `source_step` or exact `source_artifact_id`, `gb_y`, `slab_thickness`, `pbc`, `image_width=3200`, `view_width`, and `purpose`. Choose a shear frame through `intermediate_structure` and its exact indexed step; choose one batch-minimized CFG through its artifact ID. Multiple matching inputs are rejected rather than selecting an arbitrary frame or batch item.

The tool computes adaptive CNA on the complete structure before slicing. Non-FCC/BCC atoms provide interface seeds. It excludes the outer Y regions and, only where the cell contains lateral vacuum, trims side-surface regions for detection. Dense interior defect bands and resolved atomic-layer gaps provide an approximate boundary position. The two facing layers are first identified in the detection interior, then extended across the full lateral extent using midplanes to the neighboring interior layers. The default symmetric slab expands to include the assigned facing-layer atoms at distorted free edges only when every neighboring-layer atom can remain outside it; otherwise explicit thickness is required. This geometric assignment at free edges remains approximate. JSON records the detection crops, candidate bands, interior and full-width layer bounds/populations, and independently estimated side spacings.

The view is orthographic along `(0,-1,0)`, with up direction `(0,0,-1)` (180-degree roll): X points right and Z down. Its center uses the simulation box's X/Z center. Lower-Y atoms are black and upper-Y atoms milk white; these colors label geometric sides rather than CNA types or proven grain membership. The default view fits the whole selected slab and adds a centered detail for wide structures. `view_width` overrides the centered field width in input length units. Native OVITO particle radii are displayed at `atom_scale=0.7` (70%) by default. PNG outputs use transparent backgrounds.

Automatic detection is intended for one approximately planar Y-normal boundary in an axis-aligned orthogonal cell. Multiple supported bands, unresolved layers, or a symmetric slab that cannot isolate both facing layers require explicit `gb_y` and/or `slab_thickness`. A manual thickness may include partial or multiple layers and is reported as such. Coordinates use OVITO's imported Cartesian origin and input length units. `pbc` is an optional list of X/Y/Z booleans; otherwise OVITO's imported periodicity is retained. Vacuum detection does not change periodicity or recover the original LAMMPS boundary settings.

To overlay displacements, supply `reference_run_id` with `reference_artifact_role="minimized_cfg"` and, where needed, exact `reference_step` or `reference_artifact_id`. Both CFGs must contain the same set of unique integer particle IDs; matching never falls back to file row order. The current frame still supplies the CNA boundary location and selected slab. OVITO's native displacement modifier computes current minus reference with affine cell mapping off, retaining all three components, rigid translation, and strain. `minimum_image_convention=True` applies the periodic minimum-image convention; use unwrapped coordinates with this setting disabled when displacement can exceed half a periodic box between frames. Explicit `pbc` flags apply to both inputs.

Red native OVITO vector arrows are centered at the current atoms and shifted by `arrow_offset_y=1.0` along Y; the default `arrow_width` is `0.1`. These are display settings and do not move the atoms or alter physical vectors. The same `physical_capped`/`median_capped` policy applies to the selected slab, with `arrow_max_length=2.0` as the hard maximum 3D display length. Only the optional median mode uses `arrow_target_length=1.5` and XZ projection. Pure Y motion is along the viewing direction and still obeys the 3D cap. All lengths use input units, angstroms for GBclaw metal-unit inputs.

`gb_plan_view.json` stores `displacement.summary` for the full structure and current slab, `displacement.selected` with slab particle IDs, unscaled 3D vectors and magnitudes, and `displacement.render` with the uniform scale and arrow settings. It also records the reference fingerprint, cell and periodicity. Compact tool results and memory facts omit the per-atom arrays.

Each agent call creates a `render` run with `render_kind="gb_plan_view"` under `artifacts/analysis/render/{run_id}/`, registering these permanent derived artifacts:

| File | Artifact role | Content |
| --- | --- | --- |
| `plan_view.png` | `gb_plan_view` | Full slab view, or the requested centered field. |
| `plan_view_detail.png` | `gb_plan_view_detail` | Optional centered detail of the same slab. |
| `y_profile.png` | `gb_plan_view_profile` | Full and zoomed CNA/layer profiles with selected bounds. |
| `gb_plan_view.json` | `gb_plan_view_data` | Detection evidence, layers, slab, sources, camera, optional displacements, and warnings. |

The run and artifacts preserve the selected CFG's artifact ID and fingerprint, original-frame lineage when available, and `purpose`. Displacement views additionally retain the exact reference artifact and its original-frame lineage through `displacement_reference` and `reference_origin_frame` source relationships. A completed result writes a searchable `run:<render_run_id>:main_conclusion` fact; the JSON remains the detailed evidence. Ambiguous detection retains its diagnostic JSON on the failed run so the agent can inspect the reason.

The standalone entry point uses the `GBclaw` conda environment containing OVITO:

```bash
conda run --no-capture-output -n GBclaw python src/domains/analysis/rendering/gb_plan_view.py \
  path/to/structure.cfg --output-dir plan_view/structure
```

For a current/reference comparison:

```bash
conda run --no-capture-output -n GBclaw python src/domains/analysis/rendering/gb_plan_view.py \
  path/to/current.cfg --reference path/to/reference.cfg \
  --output-dir plan_view/current
```

The same CLI accepts `--gb-y`, `--slab-thickness`, `--pbc 'p f p'`, `--image-width`, and `--view-width`, plus `--atom-scale`, `--arrow-width`, `--arrow-offset-y`, `--arrow-target-length`, `--arrow-scale-mode`, `--arrow-max-length`, and `--no-minimum-image`. Direct CLI use writes files without creating project memory records; the agent adapter adds indexed provenance and memory. Both modes isolate OVITO/Qt in a subprocess from the agent runtime.

## Project Memory and Artifact Tools

Use the project summary to orient the investigation, then retrieve the evidence needed for the next decision. Search finds candidate records; exact queries establish which run or fact is relevant; artifact tools locate the supporting files. A missing detail in the summary does not mean the underlying result was deleted. See [Project memory](project-memory.md) for summary archives, provenance, and retention.

`query_project_memory` returns structured semantic records. Select exactly one entity:

| Entity | Filters |
| --- | --- |
| `runs` | `workflow`, `status`, `source_fingerprint`, `source_run_id`, `source_relation`, `run_id`, `limit` |
| `facts` | `fact_kind`, `lookup_key`, `source_type`, `include_retracted`, `limit` |
| `summary` | no filters |
| `summary_archive` | optional query text, `archive_id` for the full snapshot, `limit` |

Run and fact filters cannot be mixed. Chat history, reasoning, and trace are deliberately not available through this tool.

Run/fact queries return up to `limit` records (maximum 100), `has_more`, `next_cursor` and `source_revision`. Use `cursor=next_cursor` with the same filters to fetch remaining records. Concurrent semantic changes return `query_snapshot_changed`; restart the query. Summary responses include freshness, extraction and publication health.

Exact queries default to 12,000 characters per page (maximum 24,000), using `offset_chars` and `max_chars`. Large replies contain a lossless `serialized_json_page`, `next_offset_chars`, and a `full_output` snapshot reference. Use that reference with `read_tool_output` for stable paging or a `json_pointer` to the required field instead of repeatedly loading the complete report. Continuing the query tool with a nonzero `offset_chars` requires its returned `snapshot_id`; this reads the original immutable page rather than re-querying live records.

Every returned run includes an ordered `sources` list. `source_run_id` matches any normalized source rather than only the legacy single-source shortcut; `source_relation` can narrow that reverse lookup (for example, `comparison_curve` or `neb_profile`).

Workflow filters are enumerated from the central registry in the tool schema. For periodicity, use `workflow="gb_periodicity"`; `analyze_gb_periodicity` is the producing tool. Recognized tool names and explicit compatibility aliases resolve to canonical filters without changing stored workflows. Unknown names return the valid choices and never imply that a calculation is running.

`search_project_memory(query, entity_types=None, limit=10, offset=0, include_noncompleted_runs=False, include_task_states=False, source_type=None)` searches all indexed fact values, run parameters/progress/results, and artifact paths/metadata. It is intended for recall when the working summary is incomplete or an exact stable key is unknown. Results contain a bounded title/snippet, entity type and ID, timestamp, rank, and a stable `reference` object. They are paginated with `has_more` and `next_offset`; the agent must use an exact memory query or artifact tool before relying on important scientific detail. `entity_types` accepts `fact`, `run`, and `artifact`, while the agent-facing `limit` is capped at 25. Execution-only `task_state` facts are excluded by default; `include_task_states=true` enables an explicit cursor audit. Running, failed and cancelled runs (and their artifacts/derived facts) are excluded unless `include_noncompleted_runs=true`; `source_type` filters facts by origin and implies `entity_types=["fact"]`. Use `get_task_state` for continuity, and actual run records to verify scientific execution or reuse. A worker's own running cursor is not a submitted analysis job.

For facts, `fact_kind` is a fixed category understood by the persistence layer, while `lookup_key` is the concrete fact identity. Unknown filter names are rejected explicitly; an empty result therefore means that no matching persisted fact exists, not that a mock or fallback lookup was used.

Scientific files are accessed through stable run IDs and manifest roles:

| Tool | Purpose |
| --- | --- |
| `list_run_artifacts(run_id, role=None, offset=0, limit=20, offset_chars=0, max_chars=12000, snapshot_id=None)` | List manifest-verified artifacts in pages of up to 100 records (`next_offset`); large listings return lossless character pages with a `full_output` snapshot continued via `snapshot_id`. |
| `read_run_artifact(run_id, role, artifact_id=None, max_chars=12000, offset_chars=0)` | Read one unambiguous human-readable text artifact, up to 24,000 characters per call; larger files report the next offset. Atomic structures and trajectories are rejected. |
| `read_tool_output(output_id, offset_chars=0, max_chars=12000, json_pointer=None)` | Read archived scientific tool output on demand; optionally select a JSON field before paging. Chat history and delegate traces are excluded. This never reruns the original tool. |
| `read_agent_conclusion(conclusion_id, offset_chars=0, max_chars=12000, section="report")` | Read a hash-verified final conclusion in pages; `section="metadata"` returns its full purpose/outcome/source metadata. |
| `get_task_state(lookup_key=None, thread_id=None, offset_chars=0, max_chars=12000, snapshot_id=None)` | Read the current thread by default, or an exact prior task-state reference. Execution state does not imply scientific success or approval. Large results return a `full_output` reference; continue with `snapshot_id=full_output.output_id` and `offset_chars=next_offset_chars`, or read the snapshot with `read_tool_output`. |
| `report_artifact_usage(workflow=None)` | Report artifact bytes grouped by provenance. |

These tools reject path escape and index/manifest disagreement. They expose no deletion operation; primary and derived artifacts are permanent scientific records. Text-encoded machine artifacts such as `.lmp`, `.cfg`, and `.lammpstrj` are passed to LAMMPS or analysis tools by stable run ID and role rather than copied into language-model context.

## Web Search Tool

`agents/orchestrator/web_search.py` uses the OpenAI Responses API `web_search` tool.

- The orchestrator receives `search_web` only when `core.settings.web_search.is_web_search_available()` returns true.
- Supports `allowed_domains`, `blocked_domains`, and `max_sources`.
- Returns answer text plus citations.
- The prompt instructs the agent to use web search only for current external facts, not as a replacement for project memory, local artifacts, CSL tools, or LAMMPS outputs.

## Potentials Catalog

`domains/lammps/common/potentials.py` reads `potentials.json` from `GBCLAW_POTENTIALS_DIR` (default: `data/potentials`):

- `list_potential_records()` is used for display.
- `resolve_potential()` selects a record from `element`, `lattice`, defaults, and hints.
- Relative catalog file entries are resolved strictly from the metadata directory; explicit custom relative paths are resolved from the repository root.
