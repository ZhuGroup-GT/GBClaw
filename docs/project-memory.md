# Project Storage, History, and Semantic Memory

[中文](zh/project-memory.md) · [Architecture and design](architecture.md) · [Agent runtime](agent-runtime.md) · [Extension guide](extension-guide.md)

GBClaw keeps the information needed for the next decision close at hand, while retaining the records needed to check how a result was obtained. Conversation history, research records, execution state, and scientific files have different roles. This guide explains those roles before describing their storage and retrieval contracts.

Shared memory code lives in `src/project_memory/`. Domain packages declare workflows, facts, tools, workers and access policies through `DomainExtension`; the registry in `src/extensions/` assembles those declarations into one validated, immutable vocabulary. See [Extension guide](extension-guide.md) for the standard integration path.

## Why memory has several layers

A long investigation accumulates far more data than an agent should read on every request: alternative structures, simulation settings, trajectories, analyses, and conversations about what to try next. Putting everything into the prompt would make the next decision harder to find and would mix suggestions with calculated evidence. Keeping only a summary would lose the detail needed to reproduce or challenge a finding.

The manuscript's five agent-facing memory levels address this tradeoff. The current implementation maps them to prompts, a selective project summary, conversation state, indexed research records, and files:

| Layer | Question it answers | Where the answer comes from |
| --- | --- | --- |
| Domain knowledge | How should this class of task be carried out? | System instructions, workflow rules, and scientific tools. |
| Project summary | What matters for the investigation now? | A bounded view of current goals, constraints, open work, and key findings in `memory/index.sqlite`. |
| Conversation state | What are we discussing and what needs a response? | Recent messages and a compact session summary in the current thread's checkpoint. |
| Research records | Which result, decision, or run supports this statement? | Structured facts, runs, source relationships, and artifact indexes in `memory/index.sqlite`. |
| Scientific artifacts | What inputs and outputs were actually used? | Registered configurations, structures, trajectories, logs, scripts, and analysis files. |

The complete conversation and tool trace are a separate human-facing archive. Selected goals, preferences, and interpretations can be extracted from a completed exchange, but their `conversation` origin remains visible. Neither a preserved chat message nor a worker's final report becomes a computed result merely because it is stored.

The paper presents the conceptual hierarchy; SQLite supplies transactional records in the current code, and files hold scientific artifacts and readable exports. A Markdown summary is therefore a view of stored state, not a second database. This also explains the two `session.py` modules: `agents/middleware/session.py` maintains the thread's conversational continuity; `project_memory/snapshot/session.py` reads a project-memory snapshot for the current request.

Provenance makes a result traceable, not necessarily physically correct. Before reusing a result, agents and researchers still need to check its model, conditions, convergence, and relevance to the new question.

## How to recover a detail that is no longer in the summary

Suppose an earlier shear result is absent from the working summary. That omission does not mean the result was deleted or the calculation never ran.

1. Search project memory for the boundary, calculation, or scientific question. Search returns candidate records and stable IDs, not a verdict on their correctness.
2. Read the exact run or fact to check its status, source type, inputs, and related records. Use `source_type="tool_result"` when you specifically need tool-published facts.
3. List that run's artifacts and read the relevant report or metadata. Downstream scientific tools resolve structure and trajectory files by run ID and artifact role; those files need not be pasted into the model prompt.
4. If the question is about how the investigation was summarized at an earlier stage, follow a historical summary ID or search the summary archive. Verify any old statement against current records before acting on it.

The current summary explicitly says that it is selective and includes a small history index with archive IDs, topic previews, and retrieval instructions. That index is also bounded; older records remain searchable. See [Agent Query Tools](#agent-query-tools) for calls and [Semantic Summary and Short-term Context](#semantic-summary-and-short-term-context) for retention and compression behavior.

A project and a conversation thread are different scopes. Reopening the same thread after a long interval can resume its saved conversation state; there is no implication that elapsed time alone creates a new thread. Threads within the same project share research memory, while their conversational checkpoints remain separate.

## Source Organization and Extension Boundary

Packages are under `src/project_memory/` unless a `src/` path is given.

| Package | Responsibility | Entry point |
| --- | --- | --- |
| `src/extensions/` | Declaration types, the host vocabulary, loading, cross-reference validation and freezing | [specs.py](../src/extensions/specs.py), [loader.py](../src/extensions/loader.py), [core.py](../src/extensions/core.py) |
| `storage/` | Semantic runs/facts/artifacts and search; durable conclusions, task cursors and tool-output archives. The `MemoryRepository` facade combines one mixin per table group (schema, changes, runs, facts, retry queue, artifact index, search) with summary storage from `summary/` | [repository.py](../src/project_memory/storage/repository.py) |
| `run_artifacts/` | Per-run artifact directories and manifests; cleanup of runs whose chat turn was cancelled | [store.py](../src/project_memory/run_artifacts/store.py), [cleanup.py](../src/project_memory/run_artifacts/cleanup.py) |
| `publication/` | Bound tool runs, artifact verification, fact publication, trusted validators and recovery | [service.py](../src/project_memory/publication/service.py), [session.py](../src/project_memory/publication/session.py) |
| `extraction/` | Conversation extraction, canonical fact keys, evidence policy and durable retries | [extractor.py](../src/project_memory/extraction/extractor.py), [policy.py](../src/project_memory/extraction/policy.py) |
| `snapshot/` | Read-only project-memory snapshots and preview contracts | [session.py](../src/project_memory/snapshot/session.py) |
| `summary/` | Project-summary policy, batching, generation, validation, storage, archives and Markdown views | [package guide](../src/project_memory/summary/README.md), [service.py](../src/project_memory/summary/service.py) |
| `src/agents/memory/` | Worker recall, memory-tool assembly, report persistence and result projection; final outcomes in `src/agents/workers/` | [worker.py](../src/agents/memory/worker.py), [outcome.py](../src/agents/workers/outcome.py) |
| `src/agents/memory/tools/` | Model-facing query/search, artifact, conclusion, tool-output and task-state tools | [query.py](../src/agents/memory/tools/query.py) |
| `prompts/` | Conversation fact-extraction prompt construction | [extraction.py](../src/project_memory/prompts/extraction.py) |

`project_workspace/` owns workspace metadata, permanent Chat, checkpoints and file access, organized into `chat/`, `artifacts/`, `execution/` and `persistence/`. Project plans belong to `src/planning/`: plan models, the approval-gated draft and plan services, derived plan views, planning mode and decision authorization. Chat and artifact models live with their owners; memory records live in `project_memory/models.py`. See the [workspace source map](../src/project_workspace/README.md). Scientific calculations and domain fact builders stay with their domain tools.

The loader collects the host vocabulary in `extensions/core.py`, each built-in domain's `domains/<domain>/extension.py` and trusted modules listed in `GBCLAW_EXTENSIONS` (the older name `GBCLAW_MEMORY_EXTENSIONS` is also read). The registry is built on first use, at the latest when storage models are imported and derive their workflow and fact vocabularies. It rejects duplicate names, invalid ownership and broken references, then freezes the registry. Extensions import declaration types from `extensions.specs` and export `MEMORY_EXTENSION`. Changes to the enabled modules require a process restart; modifying a live registry cannot update already-built schemas.

`WorkerDefinition`, `AgentMemorySpec` and `ToolCapabilitySpec` supply worker routing, recall and artifact permissions, execution classification and planning behavior. The shared [create_worker_delegate() lifecycle](../src/agents/workers/lifecycle.py) binds project and invocation identity, prepares memory tools and handoff, runs the domain worker, validates completion, and persists its conclusion and task cursor. The orchestrator and planning gates use those same declarations. A new worker therefore supplies its own factory, tool providers and policy through an extension, without editing repository, query-tool or agent-name lists.

## Workspace Layout

`ProjectStore` in `src/project_workspace/store.py` owns workspaces under `<repo>/projects` (`GBCLAW_PROJECTS_ROOT` overrides the location). A populated project has this layout (`create()`/`initialize()` create only `project.json`, `chat/history.sqlite`, `memory/index.sqlite`, `memory/summary.md` and empty `artifacts/` and `runtime/`):

```text
projects/{project_id}/
├── project.json
├── plan-draft.md           # generated only while approval is pending
├── plan.md                 # generated view of the active plan
├── chat/
│   ├── history.sqlite
│   └── transcript.md       # exported after each finished turn or on file-browser access
├── memory/
│   ├── index.sqlite
│   ├── summary.md
│   ├── summary_archive/    # generated after a nonempty summary is replaced
│   │   ├── index.md
│   │   └── {archive_id}.md
│   ├── conclusions/
│   │   ├── {conclusion_id}.md
│   │   └── {conclusion_id}.json
│   └── plans/
│       ├── drafts/{draft_id}.json
│       └── {plan_id}/
│           ├── current.json
│           └── revisions/revision-NNNN.json
├── runtime/
│   ├── checkpoints.sqlite
│   └── tool_outputs/{output_id}.json
└── artifacts/
    ├── structure/{run_id}/
    ├── structure/dichromatic_map/{run_id}/
    ├── lammps/cfg_convert/{run_id}/
    ├── lammps/minimize/{run_id}/
    ├── lammps/structure_minimize/{run_id}/
    ├── lammps/energy/{run_id}/
    ├── lammps/shear/{run_id}/
    ├── lammps/neb/{run_id}/
    ├── analysis/stress_strain/{run_id}/
    ├── analysis/neb/{run_id}/
    ├── analysis/gb_periodicity/{run_id}/
    ├── analysis/render/{run_id}/
    └── code/{run_id}/
```

`projects/` is git-ignored research data. Scientific workflow directories are created only when a run is configured or started.

`ProjectStore.get()` and `list_projects()` only read metadata; they do not rewrite `project.json` or initialize databases. `create()` initializes a new workspace, while `initialize(project_id)` explicitly migrates an existing workspace or creates missing metadata. Malformed metadata is preserved and reported rather than reset. Metadata updates use atomic replacement under an external process-shared lock, also held by project deletion.

Structure runs use workflow `csl` and write to `artifacts/structure/{run_id}/`.

## Permanent Chat History

Use this archive to review what was said and how a task unfolded. Use semantic memory and scientific artifacts to establish what the project currently records as a result. Keeping both preserves the investigation's history without making every conversational claim part of routine agent recall.

`ChatRepository` stores the complete transcript in `chat/history.sqlite`:

- `turns` contains ordered user and assistant content, assistant reasoning, status, and timestamps.
- `turn_order` assigns immutable project-wide insertion positions across all threads; legacy turns are backfilled once in rowid order. Conversation-fact freshness checks compare these positions rather than timestamps. The history API orders one thread by `sequence`; `chat/transcript.md` lists turns by `created_at`, thread and sequence.
- `chat_requests` stores API request fingerprints, execution owners, bound assistant turns, terminal results and cancellation receipts. It is not part of semantic fact memory.
- `trace_events` contains ordered reasoning/tool events with nesting and full tool input and output, plus `reasoning_withheld` notes (`input_json` holds `reasoning_tokens`) when the provider reported reasoning it did not disclose. These notes are written immediately, are never added to the turn's `reasoning`, and appear in the transcript as "Reasoning withheld".

Chat, reasoning, and trace are retained permanently in their own archive. The archive is not loaded wholesale into agent context or `summary.md`. A completed turn's final user/assistant content can support policy-validated conversation facts; these retain their conversation origin when queried, recalled or summarized. Hidden reasoning and raw trace are not extraction inputs.

`ChatTraceRecorder` does not write every reasoning delta separately. It keeps independent in-memory segments keyed by parent/label, so parallel workers may interleave without splitting each other's reasoning. A matching tool boundary seals only that parent's segment; final-answer and error boundaries seal the remainder. This keeps browser streaming independent from database write frequency.

`trace_events.tool_call_id` stores the runtime tool-call identity. Worker trace rows use the corresponding tool-start event as `parent_event_id`, while the worker name remains presentation metadata. Existing databases are upgraded by adding the nullable column; old rows continue to render through a name-based read fallback.

`chat/history.sqlite` is authoritative and written immediately. Chat/trace writes also advance a durable export revision. `chat/transcript.md` is a derived Markdown view containing turns plus collapsible reasoning/tool trace, refreshed by the turn recorder (`interfaces/recording.py`) after it records a turn as completed, failed, interrupted or paused, and when the file browser lists the chat directory or stats, previews, downloads or raw-reads the transcript. A turn finalized outside the recorder (the `/chat/stop` endpoint, or orphan recovery at the next request) appears at the next export. Export failures are logged and never fail the turn. Pending changes are coalesced into one export; unchanged reads reuse it. Direct filesystem readers can call `ChatRepository.ensure_transcript()` first, or `rebuild_transcript()` to force regeneration. Manual edits are overwritten on the next rebuild.

The Web API loads history lazily:

```text
GET /api/projects/{project_id}/history?offset=0&limit=200
GET /api/projects/{project_id}/history/{turn_id}
```

The paginated endpoint accepts an optional `&thread_id=` (default: the project's current thread) and returns lightweight turns without reasoning or trace. The detail endpoint is requested only when the user expands one historical execution. Tool input/output in that response is a bounded preview; the authoritative SQLite row and generated Markdown transcript retain the complete value. An interrupted request marks its active assistant turn `interrupted` without creating a semantic record.

## Semantic Memory

Here, “semantic memory” means structured, searchable project knowledge. It includes both computed results and contextual claims, with explicit source labels; the word “fact” names a record type and does not imply that every entry is an experimentally verified truth.

`memory/index.sqlite` is the source of truth for agent-recallable research state. It contains:

- `runs`: stable lifecycle records for CSL/structure, dichromatic maps, CFG conversion, GB/structure minimization, GB energy, shear, NEB, stress--strain/NEB/GB-periodicity analysis, render, and validated general coding workflows.
- `run_sources`: normalized ordered provenance keyed by `(run_id, relation, ordinal)`.
- `facts`: durable current-state facts with stable lookup keys, active/retracted status, optimistic versions, source turn/run references, and evidence classification.
- `artifacts`: indexed manifest entries with role, media type, size, metadata, and provenance.
- `semantic_summary`: the singleton authoritative content, source digest, format version, processed source revision, generation time, and fact/run counts.
- `semantic_summary_archives`: immutable full previous summaries and their source metadata (UPDATE/DELETE triggers abort). A summary is archived atomically with `archive_reason='replaced'` when a nonempty summary is replaced, or `'format_reset'` when an obsolete format is reset for rebuilding.
- `semantic_revision` and `semantic_changes`: a monotonic revision counter and a coalescing queue of changed fact/run identities used to update the working summary incrementally.
- `memory_search`: a derived FTS5 trigram index over all fact values, run state/results, and artifact metadata. `memory_search_state` versions the rebuildable index.
- `memory_retry_jobs`: persistent extract/apply/summarize retry state for failed post-turn memory work.
- `memory_schema`: schema version 4.

### Fact Sources

Each returned `FactRecord` includes a computed `source_type`. The host derives it from stored evidence and provenance; a caller cannot relabel a conversational interpretation as a tool result. `evidence_kind`, `source_turn_id` and `source_run_id` remain available for more precise inspection.

| `source_type` | Meaning |
| --- | --- |
| `tool_result` | A result published by a scientific tool, with its recorded run and artifact evidence. |
| `conversation` | Extracted goals, selections, preferences or model interpretations grounded in a conversation. |
| `agent_report` | A worker's report or interpretation, with outcome and supporting references. |
| `workflow_state` | Plans, approval state and task execution cursors. |

For conversation facts, `user_explicit` records what the user stated; it is not an independently measured observation. `completed_run` means the interpretation is supported by a verified completed run, not that the tool computed or published that interpretation. A report reference and an approved plan likewise do not establish scientific success. Inspect exact runs and artifacts before reuse; a completed run can still carry convergence warnings.

The model cannot write database rows directly. After a successful assistant turn, the host gathers a bounded set of records to compare with the exchange: fixed project slots, facts associated with verified runs mentioned in the turn, and up to 10 related active facts found through FTS5 from the first 250 characters of each message. There are at most 30 candidates in total (up to 20 per verified run). Plan, agent-conclusion and task-state facts are excluded, and values over 4,000 characters are shown as omitted. These search results help the extractor recognize an existing statement, update, or conflict; their ranking does not establish truth. The extractor reads final user/assistant text, verified compact run records, and those candidate facts. It does not read reasoning or tool traces.

The model proposes `upsert`, `retract`, or `noop` mutations. Deterministic policy then checks canonical keys, fact/value bounds, explicit user evidence, completed-run provenance, and the expected version before the whole batch is committed atomically with its search-index and semantic-revision updates. An identical write from the same source turn is a no-op; a newer confirmation advances the source reference and version even if the value is unchanged. An older source cannot update or retract a fact with a newer source. Freshness is checked before correction and again inside the write transaction. Other valid slots in an old batch can still be written. Retraction creates a tombstone rather than deleting the row; normal queries, search, context, and summary use active facts only.

Facts retain only the latest value and an optimistic concurrency version number; there is no fact-history table or historical-value migration. Scientific run/artifact evidence still remains immutable. Schema/policy failures receive at most two corrective re-extractions with bounded host feedback and freshly loaded fact versions before any invalid batch can be committed. Persistent policy/schema failures become structured dead letters. Storage conflicts and model transport failures remain eligible for scheduled retries; transport failures do not spend the local correction loop. Saved apply-stage jobs use the same correction path.

The vocabulary is fixed for each process by its loaded declarations. Built-in kinds include `project_goal`, `user_decision`, `loading_preference`, `csl_orientation`, `gb_crystallography`, `gb_identity`, `material_selection`, `potential_selection`, `scientific_conclusion`, `project_plan`, `agent_conclusion` and `task_state`. Extensions can declare additional kinds with explicit writer, evidence and key policies. Chat extraction may write only the policy-approved subset. In particular, CSL orientation remains owned by its deterministic workflow, and project plans remain owned by the deterministic planning service. Run IDs are collected from structured tool inputs and outputs and verified against the project run index before use. Invalid kinds, unrelated run IDs, failed-run conclusions, assistant suggestions presented as user decisions, and writes that conflict with a newer fact version are rejected.

CSL builds normalized orientation facts in its domain recorder and submits them through the shared publisher. Their stable identity includes the exact `(Sigma, rotation_m, rotation_n)` branch, axis, basis, and GB plane so rotations sharing Sigma do not overwrite each other. Scientific workflows write their own run state and results instead of relying on conversational extraction. Orientation and crystallography facts are committed only after the supporting CSL run completes; every distinct analyzed boundary retains its own fact. Ordinary validation-error strings cannot become successful result artifacts or cache entries. Trace provenance collection includes source, reference, context, and plural run IDs before verification against the project index.

All current scientific completion paths use registered tool policies. A tool can publish one conclusion or a batch of specialized facts. CFG conversion and batch structure minimization record each successful item separately, with its source frame, purpose, constraints and output references. Completed batches are published atomically; a failed batch retains its item files and results but is not promoted to completed-run evidence.

`RunService` saves the exact facts in a permanent publication artifact before completing the run, then calls the registered writer in [publication/writes.py](../src/project_memory/publication/writes.py). That writer also owns the final commit entry points for planning and chat extraction, while their approval and user-evidence guards remain authoritative. Publication retries use stored facts without scientific execution or LLM calls, and refuse to overwrite newer or retracted facts. Chat extraction cannot claim a fact slot already reserved by a pending tool publication. Main-agent access and all registered workers use the same registered memory-tool factory; the main context middleware still owns summary injection.

Tool and workflow declarations select trusted publication validators. The same validators protect staging, recovery, direct deterministic writes and completed-run evidence used by chat extraction. Coding's recorded `validation.passed=true` requirement is declared by its domain and implemented in [publication/validators.py](../src/project_memory/publication/validators.py). The shared writer also checks the bound tool's fact kinds, source identity, evidence and permitted lookup-key scope.

### Versioned Project Plans

A successful planning delegation writes an approval-required proposal, not an active plan:

- `plan_draft:<draft_id>` contains the proposed full plan plus create/revise action, base plan/revision, original user request, status, decision feedback, and approval result.
- `project:pending_plan` is the single stable pointer to the draft awaiting a user decision.

Only explicit approval creates or advances the executable facts:

- `plan:<plan_id>` contains the full objective, evidence/assumptions, ordered steps, dependencies, capabilities, validations, state, attempts, timestamps, result summaries, and associated scientific run IDs.
- `project:active_plan` is a compact stable pointer containing the active plan ID, overall status, step counts, and exact running/next/blocked step IDs.
- `plan:<plan_id>:revision:<n>` is an immutable structural-revision snapshot. Structural revision numbers are separate from the fact's `memory_version`, which also advances for ordinary progress.

The active plan/revision facts, approved draft, and retracted pending pointer are written in one transaction using optimistic fact versions. A stale revision draft is refused when its base structural revision no longer matches. Rejection retracts only the pending pointer and preserves the immutable audit view; it does not change the active plan. The same transaction refreshes their FTS documents and semantic revisions, so search discovers a new plan or progress update immediately. Step status is one of `pending`, `running`, `completed`, `failed`, `blocked`, or `skipped`; the overall status is derived from exact step/dependency state. A new plan supersedes an older unfinished non-running plan, while a plan with a running step cannot be silently replaced. Scientific delegate wrappers update their planned step at worker boundaries and attach discovered run IDs. Cross-turn execution resolves the active pointer and reads the full fact instead of using Chat trace or treating the bounded working summary as authoritative.

When the user changes an existing plan, the orchestrator delegates the complete change request and existing plan ID back to the planning worker. Its planner-only `revise_project_plan_draft` proposes an audited revision after reading the exact plan and relevant memory. Title/objective/evidence fields and pending or blocked steps may change. A running, completed, failed, or skipped step cannot be removed or have its definition rewritten, and its attempts, results, timestamps, and run IDs remain intact. A materially different scientific objective produces a new-plan draft. No proposed change affects execution until it is separately approved. Approval may supersede an old non-running plan. The revision reason, requesting actor, changed fields, prior memory version, source draft, and timestamp are retained in the approved plan.

Pending drafts atomically refresh root `plan-draft.md` and `memory/plans/drafts/<draft_id>.json`. A decision removes the root pending view while retaining the audited JSON. Approved plans refresh root `plan.md`, current JSON, and immutable revision JSON. SQLite remains the only source of truth; manual file edits are overwritten.

The pending pointer has the highest Runtime Context priority, followed by the active pointer and working summary. The dynamic prompt rereads both pointers before every model call. A tool-call middleware independently rereads pending state and blocks replacement planning, scientific delegation, and progress updates while approval is unresolved. It permits approve/reject only when the latest user message explicitly authorizes that decision and names no different draft. Exact full plans/drafts remain available through `get_project_plan` and `get_project_plan_draft`.

`run_sources` allows multiple frames from the same source run under one relation; `(run_id, relation, ordinal)` uniquely identifies each input. This supports exact CFG-frame → conversion → `structure_minimize` lineage. Each minimization records its required `purpose`, immediate input and original frame; those values remain searchable and are included in worker memory handoffs. Memory schema V2 and V3 databases are migrated to V4 automatically, preserving their records and source relationships. Pre-V2 or unknown versions are rejected with an explicit error and are never automatically deleted.

## Structured Runs

A run is one identifiable scientific operation. Recording its lifecycle and sources lets a downstream task distinguish a usable completed result from a configuration, failed attempt, or still-running calculation. This is the main bridge from agent instructions to reproducible scientific evidence.

Runs use a stable lifecycle:

```text
configured -> running -> completed | failed | cancelled
```

Tool results are machine-readable JSON (`RunToolResult` in `publication/results.py`) with `run_id`, workflow, status, manifest, artifact records, compact result, error and, when the tool supplies one, a bounded `model_view` shown at the model boundary instead of the full result. Configuration and execution update the same row.

`RunService.tool_run(memory_tool, parameters=...)` yields a `ToolRunSession` bound to one project, tool and run. Tools write under `session.artifact_directory`, call `session.register_artifact(...)`, then `session.complete(result=..., conclusion=...)` or supply a fact batch. A registered fact builder can derive the batch from the completed scientific result. `render_atomic_structure` is an example in [rendering/tool.py](../src/domains/analysis/rendering/tool.py).

The session's default `mode="facts"` requires a non-empty batch or a conclusion. Explicit `mode="artifacts_only"` is allowed only when the tool policy declares neither a required main conclusion nor a fact builder. Leaving the context without completion fails the run; cancellation records a cancelled run while retaining registered artifacts. `session.result_ref` supplies the project/run/tool identity, manifest, artifact and fact references, contract and full stored result location. It does not replace the scientific payload with a model-facing summary.

Configured workflows retain their separate configure/start/complete lifecycle. Their creation binds `memory_tool` explicitly or through the parameters. All registered creation paths preflight the imports, synchronous callable shape and signature of fact builders, model-result builders and publication validators before creating run artifacts. A broken hook fails before computation. Completion verifies required artifact roles and stages exact facts permanently before recording success; failed fact insertion preserves successful computation and a durable recovery pointer.

Every staged publication stores `contract_version` and `contract_sha256` in both its pointer and artifact. The fingerprint covers the declared publication policy, including fact ownership, required artifacts and validator paths. Recovery requires both values to match the active tool policy; missing versions, version changes and policy drift produce an explicit compatibility error. There is no old-protocol adapter or silent reinterpretation. Restoring the exact policy permits an explicit retry, without rerunning builders, scientific work or LLM calls. Newer and retracted facts remain protected by version checks. Domain authors must advance the declared version when hook semantics change; the fingerprint identifies declarations rather than hashing Python source code.

Configured CFG conversion, GB minimization, structure minimization, GB energy, GB shear, and GB NEB runs keep a searchable `canonical_config` mirror inside the run parameters. The primary `input.json` artifact remains the authoritative config; the mirror exists only so every exact field participates in run search and working-summary updates. GB-energy runs use the same canonical bicrystal construction fields as minimization, require a continuous periodic x-z interface plus y vacuum, and record the relaxed bicrystal and four top/bottom bulk/surface references. The compact run result contains GB energy in J/m^2 and eV/Angstrom^2, reference energies, interface area, and convergence warnings. A successful run also writes one deterministic `scientific_conclusion` fact at `run:<gb_energy_run_id>:main_conclusion`; complete per-reference states remain in the indexed `run_metadata` artifact.

For shear, source files, material, boundary conditions, and potential are inherited from the linked completed minimization or the final state of a completed shear run, while loading/minimization/output fields come from typed writer arguments. When the search index is built or refreshed, missing config mirrors for supported scientific workflows are backfilled from their project-contained `input.json`.

A completed `cfg_convert` run records the exact source CFG path and SHA-256 fingerprint, a primary source snapshot, the derived `converted_lmp` artifact path, atom/type counts, cell metadata, and the source-to-output mapping. Because CFG lacks Cartesian box-origin fields, the conversion also records the compatible parent-LMP origin reference/fingerprint it reused, or an explicit warning when it had to center the box. This lets later LAMMPS work discover and reuse a converted structure by run ID and artifact role without scanning project directories. Final and intermediate shear structures carry their exact loading point metadata. Older final CFG/LMP files can recover their identity from the source metadata's current state; intermediate frames never inherit final-state values. Single and batch structure minimization also register the actual configuration provider under the `simulation_context` source relation.

Stress-strain analysis hashes the captured source metadata bytes and saves a permanent `source_metadata_snapshot`. Source relations, analysis JSON and conclusion facts identify that same snapshot, including when an `allow_partial` source run subsequently advances.

Run provenance is normalized in `run_sources(run_id, source_run_id, relation, ordinal, ...)`. This permits a comparison or other derived run to reference every input without hiding IDs in parameter JSON. `source_run_id` remains a single-primary convenience field and is transactionally mirrored into `run_sources`; reverse lookup uses the normalized table.

At execution time shear copies the exact parent LMP and both boundary-ID files into its own `inputs/` directory, registers those snapshots, and records the parent workflow, parent run ID, source fingerprint, and initial cumulative strain. Live shear metadata records progress, current COM and spacing, relative and cumulative strain, convergence, vacuum-corrected top/bottom and tensor stress, summary peaks, every completed loading point, and each renderable frame. Point history is independent of `dump_every`; that setting controls only which CFG paths appear in the frame index.

Terminal semantic results are built from this metadata and keep compact corrected values such as peak absolute top shear stress in MPa (spring force/area or displacement-mode `sigma_xy`), peak step/strain, stress source, current stress state, convergence counts, and initial/current boundary COM.

While a run is active, semantic progress indexes the latest complete corrected stress state, current spacing, and running peak summary. The full `points` history remains in the registered metadata artifact for later analysis rather than being duplicated into the searchable run row. The monitor skips unchanged metadata and registers only newly discovered CFG frames.

The human-facing shear curve stays in its run directory without Project Memory registration. Structures, logs, run metadata, and intermediate frames remain artifacts. Generic worker `run.json` wrappers and copied scientific outputs are not created for a delegate. Its final conclusion is saved separately as described below; conversation traces remain human-only.

## Agent conclusions and task continuity

[WorkerOutcome](../src/agents/workers/outcome.py) defines the final report contract: schema version, status, summary, typed run/fact/artifact references and next action. Supported states are `completed`, `reused`, `partial`, `failed`, `awaiting_user`, `cancelled` and `interrupted`. Unknown or nonterminal states are rejected. A claimed outcome still passes the host's source and deliverable checks before scientific success is accepted.

Every registered project delegation receives a distinct `conclusion-<id>` tied to its invocation identity. Replaying that identity never overwrites a different report. `memory/conclusions/<id>.md` holds the full final conclusion, and adjacent metadata records its purpose, outcome, sources and content hash. No conversation trace is included. The `agent_conclusion` fact stores a short abstract (at most 800 characters, generated by a clone of the memory endpoint with thinking disabled and an answer cap of 2,048 tokens or the endpoint's `max_tokens` if lower; models that keep reasoning get an extra 4,096-token allowance within the endpoint limit), source pointers and the report reference. It uses `assistant_report` evidence: an interpretation is not a verified scientific fact. Completed, failed, partial and reuse-only calls can all publish conclusions. If abstract generation fails, the report and a brief `summary_pending` index remain available; repairing the index does not rerun scientific work.

Use `query_project_memory` or `search_project_memory` to find the index, then `read_agent_conclusion(conclusion_id, offset_chars=0, max_chars=12000)` for exact report pages. `section="metadata"` retrieves the complete source list when the index only previews it.

`task_state` facts maintain per-thread and per-invocation execution cursors: objective, state, active invocations, conclusion references, blockers and next action. `get_task_state()` retrieves the current cursor; an exact lookup key retrieves a previous invocation. Compare-and-swap updates protect concurrent delegations. The dynamic prompt refreshes a bounded cursor before model calls. An existing approved project plan remains authoritative; unplanned work does not create an approval plan. These cursors preserve continuity without putting every worker report in working context.

## Artifact Manifests and Provenance

Every run directory contains `manifest.json`; configured workflows also contain exactly one canonical `input.json`, registered once with role `config`. GB shear additionally writes a runtime `config_used.json` that points to the structure and boundary-ID snapshots in its own run directory. Artifact roles select files without relying on filenames. Provenance defines retention:

- `primary`: original scientific inputs and outputs; permanent.
- `derived`: scientific products such as stress--strain data/plots, frames, and renders; permanent.
- `debug`: diagnostic-only output; cleanup-eligible by internal maintenance policy.

Agents have no deletion tool. Artifact access is bounded by both the database index and the run manifest. A corrupted index path, a path outside the project/run directory, or a manifest mismatch is rejected.

## Agent Query Tools

`query_project_memory` accepts `entity=runs`, `entity=facts`, `entity=summary`, or `entity=summary_archive` and returns JSON. Run filters are `workflow`, `status`, `source_fingerprint`, `source_run_id`, `source_relation`, and `run_id`; fact filters are `fact_kind`, `lookup_key`, `source_type`, and the audit-only `include_retracted`. Filters from different entity types cannot be mixed.

For example, the model-facing tools accept these arguments:

```text
query_project_memory(entity="facts", source_type="tool_result")
query_project_memory(entity="facts", source_type="conversation")
query_project_memory(entity="summary_archive", query="NEB")
query_project_memory(entity="summary_archive", archive_id="summary-...")
search_project_memory(query="grain boundary energy", source_type="tool_result")
```

A search with `source_type` and no `entity_types` searches only facts. Combining a source filter with explicit run/artifact entity types is rejected. Omitting the filter preserves the ordinary mixed-source query; returned fact records and search references still expose their source type.

Record pagination uses `has_more`, `next_cursor` and `source_revision`; pass the cursor with the same filters. A changed revision returns `query_snapshot_changed` and requires a new query. Large character pages carry `snapshot_id`: supply it with `offset_chars` to continue an immutable snapshot, or use `read_tool_output`. Summary results and the orchestrator context expose `summary_stale`, extraction retries and pending publication health. Decisive facts remain exact database reads. Newly registered permanent artifacts carry SHA-256; reads, analysis inputs, Coding staging and completion checks share manifest/path/role/content verification. Old files without an original digest have weaker historical integrity and need a separate migration.

`search_project_memory(query, entity_types=None, limit=10, offset=0, include_noncompleted_runs=False, include_task_states=False, source_type=None)` searches the complete indexed fact/run/artifact corpus when the bounded working summary does not contain enough detail or the caller does not know an exact ID. It returns ranked, bounded snippets plus stable references and pagination state; it never returns complete artifact file contents. `entity_types` can restrict the search to `fact`, `run`, and/or `artifact`, and `limit` is capped at 25.

Terms of at least three characters use the FTS5 trigram index, including unsegmented Chinese text. Queries containing short identifiers such as `Fe` or `Σ5` use weighted matching instead of recency alone. Short symbols match token boundaries so `Fe` cannot match `manifest`; adjacent Chinese text and short Chinese substrings remain supported.

By default, incomplete runs, their artifacts and computed facts are excluded before pagination. Explicit user choices remain valid even if a linked run failed. Set `include_noncompleted_runs=true` for diagnostic searches. Execution cursors are also excluded by default; use `include_task_states=true` for a cursor audit or `get_task_state` to recover task progress.

Search results are discovery candidates, not authoritative replacements for structured rows or artifact content. Returned run records and run search references include the ordered `sources` list. Important matches must be followed with `query_project_memory`, `list_run_artifacts`, or `read_run_artifact`. The index is updated in the same transaction as each source write. It can be deleted and rebuilt from `runs`, `run_sources`, `facts`, and `artifacts`; existing projects are rebuilt automatically when the index version changes.

`fact_kind` is a category from the startup registry's fact vocabulary (for example `user_decision`, `project_plan`, or `scientific_conclusion`). `lookup_key` is the stable identity of a concrete fact (for example `project:active_material`). An unknown `fact_kind` returns an explicit field-usage error rather than an empty fact list, preventing a lookup key from being mistaken for a category.

Artifact tools are similarly structured:

- `list_run_artifacts(run_id, role=None, offset=0, limit=20, offset_chars=0, max_chars=12000, snapshot_id=None)` lists verified artifact records in pages of at most 100 (`next_offset`). Large pages are lossless JSON text pages continued with `snapshot_id`.
- `read_run_artifact(run_id, role, artifact_id=None, max_chars=12000, offset_chars=0)` reads one unambiguous human-readable text artifact. Reads default to 12,000 characters (24,000 maximum per call); larger files report the exact next offset and can be read completely through successive chunks. Atomic structures and trajectories such as `.lmp`, `.cfg`, and `.lammpstrj` remain permanent machine artifacts but are rejected by this model-facing reader; downstream LAMMPS and analysis tools resolve them by run ID and role.
- `report_artifact_usage(workflow=None)` reports indexed bytes grouped by provenance.

The shared model boundary (`agents/memory/projection.py`) leaves tool replies of at most 4,000 characters unchanged, unless they carry a `model_view` or a worker trace. Larger replies are archived under `runtime/tool_outputs/` and shown as a projection of at most 16,000 characters with a `full_output` reference. A reply that carries its own `model_view` (for example `RunToolResult.model_view`), or a reply over 4,000 characters whose tool declares `ToolCapabilitySpec.model_view`, is shown as that view. A view longer than 15,400 characters is itself projected. Plain text is shown whole up to 15,400 characters; a longer text shows its first 15,400 characters, cut back to the last line break when one falls in the final fifth. Detail-reading memory tools are never projected. `read_tool_output(output_id, offset_chars=0, max_chars=12000, json_pointer=None)` retrieves that original scientific evidence on demand. Legacy delegate archives expose only their final conclusion and scientific references; chat history and nested traces cannot be read through this tool. This is a runtime invocation record, not a duplicate scientific fact; run, artifact, source and conclusion identities keep their existing meaning. Exact memory queries also use bounded, lossless pages for large records.

Memory and artifact access follows each registered `AgentMemorySpec.artifact_access`: `"read"` (default) or `"metadata"`. `read` agents receive `list_run_artifacts` and `read_run_artifact`. `metadata` agents (currently only Coding) receive neither. Their handoff marks artifact references as metadata only, and their `read_tool_output` reads only outputs archived in their current invocation. Coding still requires explicitly authorized input snapshots. `report_artifact_usage` is a separate `report_artifact_usage=True` flag, enabled only for the orchestrator.

Before every registered worker delegation, a deterministic memory preflight selects a bounded evidence preview. Execution-only `task_state` facts are excluded before search ranking and result limits; a worker must not mistake its own newly created cursor for another analysis already running. General search also excludes cursors unless `include_task_states=true`; exact cursor reads remain available through `get_task_state`. Scientific reuse/wait claims require matching actual run records, not a task cursor or an assistant report alone.

The preflight selects a bounded 12,000-character handoff from authoritative SQLite: relevant active facts, completed runs, and their stable artifact references. Orchestrator-facing `gb_crystallography` facts are excluded from automatic worker handoffs and remain available through the project summary and exact memory queries. The selection occurs before a new scientific run is registered, so a worker cannot retrieve its own in-flight task as prior evidence. Only completed runs and their artifacts are included as reusable evidence. Configured, running, failed and cancelled runs are excluded, along with computed facts tied to them; explicit user choices remain. Agent conclusions may still appear and can describe failed, partial or interrupted work; they are labelled `agent_report`, not evidence. CSL artifacts store canonical tool input plus an input fingerprint so exact deterministic calls can be reused across turns.

The handoff resolves explicit run/artifact IDs throughout the task first, discovers existing derived runs, and searches artifact metadata as well as runs and facts with a query of at most 500 characters. Recent fallback candidates are compared across relevant workflows. Material, potential, loading, goal and user-decision facts receive priority.

Records are packed individually: at most 4 completed runs and 8 active facts. Up to 24 explicit run, artifact or conclusion references are resolved from the task, and the keyword search (≤500-character query) takes 12 hits. Each fact value preview gets a fair share of the remaining space (100–1,400 characters). Run input previews are capped at 900 characters and result-summary previews at 700. At most 10 artifact references are listed per run. Returned IDs describe only visible records, and truncated field previews require exact follow-up reads. Its 12,000-character cap (`DEFAULT_HANDOFF_MAX_CHARS`) is independent of the 1M context switch. Coding file access still requires explicitly authorized input snapshots.

CSL cache lookup covers all completed history and verifies the manifest, path, scientific output and any recorded content hash. Local entries are scoped to project and worker thread. Other scientific workflows still require the agent to compare candidates; they have no shared automatic reuse entry point. Possible extensions to result reuse and evidence tracking are described in the [roadmap](roadmap.md).

## Semantic Summary and Short-term Context

Project and session summaries solve different problems. The project summary orients the next research decision across the whole project; the session summary helps the model continue the current conversation. Both are intentionally short and may omit details. Exact research records and files remain the retrieval path for scientific work.

The `semantic_summary` singleton row in `memory/index.sqlite` is the source of truth for the bounded project working summary. Its content, source revision/digest, generation time, fact/run counts, and format version are written transactionally. `memory/summary.md` is only an atomically regenerated human-readable view; agents, APIs, and memory queries never read the Markdown file. Every ``(fact: `lookup_key`)`` citation is validated by indexed lookup against current SQLite facts and its source section before commit.

### What stays in working context

The summary is a selective working index: keep current goals, durable preferences and constraints, pending approvals, unfinished work, blockers and key evidence references. Compress completed execution narratives, parameter lists, duplicate reports and older findings unrelated to the next decision first. Relevant completed findings remain short conclusions with exact references. Registry `context_priority` and fixed lookup keys participate in selection; live status rows are queried before pagination so newer completed work cannot hide an old blocker.

### Historical summaries and lookup pointers

Replacing a nonempty summary archives its entire previous text and source metadata in `semantic_summary_archives`, in the same transaction as the new summary and processed-change acknowledgment. Compare-and-swap checks reject stale concurrent writers before archive or prune. Compression never deletes or rewrites facts, runs, artifacts or conclusions. Only processed change notifications are pruned. Archive history starts with the first replacement under this implementation; existing databases gain the archive table during initialization.

The current summary also contains a host-generated history index: up to three real archive IDs, their source revisions, and short topic previews, with the just-replaced summary taking priority. It explicitly states that both the summary and index are incomplete, and supplies exact `archive_id` reads, `query` searches, and `next_cursor` pagination. The index uses at most 1,400 characters within the overall summary budget, and shrinks further for small budgets. Previews are historical excerpts, not current facts or approval evidence. Previous navigation blocks are removed from summarizer input to prevent recursive growth; archive IDs are generated by the host and committed with the archive. Existing summaries receive this navigation on their next refresh.

Use exact fact/run queries for current evidence and `search_project_memory` to discover older records. Use `query_project_memory(entity="summary_archive", query="NEB")` for archive previews and pagination, then `archive_id="summary-..."` for the full stored summary. Large responses retain lossless tool-output paging. Historical text can contain superseded decisions or retracted interpretations; verify current records before acting. Archives are not re-injected into normal working context. `memory/summary_archive/{archive_id}.md` is a rebuildable view; `index.md` lists the latest 20 archives, while older entries remain queryable in SQLite. Normal refresh repairs recent views; `save_archive_views(project_root, memory, rebuild=True)` rebuilds all views without model calls.

### Ownership and evidence labels

All project-summary code is owned by [`src/project_memory/summary/`](../src/project_memory/summary/README.md). `service.py` coordinates refresh; `policy.py`, `batch.py`, `prompt.py`, `generation.py` and `validation.py` select and validate content; `navigation.py` supplies historical lookup pointers; `storage.py` and `views.py` own persistence and derived documents. `project_memory/snapshot/session.py` only reads project snapshots, while `agents/middleware/session.py` owns the separate thread conversation summary.

Summary format 4 preserves four sections: `Tool results`, `Conversation-derived context`, `Agent reports` and `Workflow state`. Input records are grouped as `changed_facts` (tool results), `conversation_facts`, `agent_reports`, `workflow_state` and `changed_runs`. Conversation-derived interpretations remain in their own section even when they cite a completed run; a worker's interpretation stays in `Agent reports`. A citation in the wrong source section is rejected: the draft gets one correction naming the section its record belongs in, and if the corrected draft still misattributes a citation, the committed summary stays and its source changes remain pending for a later refresh. Older mixed summaries are rebuilt from current persisted records; the old text is archived with `archive_reason='format_reset'`. This does not delete facts or reclassify conversational claims as computed results.

Facts and runs remain the complete durable source of truth. Their create/update/retract transactions advance a monotonic semantic revision and upsert the changed entity into `semantic_changes`. Repeated progress updates for one run coalesce into one pending entry, and no-op fact/progress writes do not advance the revision. A retracted fact is passed to the summarizer as an explicit tombstone so the previous statement and citation are removed; fact counts cover active facts only. After a successful summary commit, processed change entries are pruned.

### Updates, size limits, and failure recovery

Summary generation is deliberately outside the request-preparation critical path. After a completed assistant turn, semantic post-processing executes extract, policy/apply, and `maybe_refresh_summary()`. Both the extractor and summarizer clone the memory endpoint through `core.llm.factory.auxiliary_endpoint` with `thinking="disabled"` and `reasoning_effort=None`. Providers may still send their lowest effort, and models that keep reasoning get a 4,096-token allowance above the answer cap, within the endpoint's output limit. Extraction's answer cap is 8,192 tokens (or the endpoint limit if lower). Its request capacity is at most 64K tokens, from which the output cap and a 1,024-token margin are reserved before the source exchange, run cards and candidate facts are fitted. New project summaries target 6,000 characters and are capped at 12,000 characters, further reduced when the token budget and framing reserve require it. These fixed character limits do not scale in 1M mode and are distinct from provider token counts. Project-summary generation and draft validation use min(`project_summary_max_tokens`, endpoint output limit, 24,000) tokens, two per character of the 12,000-character cap. `[runtime_context] project_summary_max_tokens` remains the injection and context-reservation ceiling (32768 by default, 131072 in 1M mode). The original endpoint remains unchanged.

After the initial generation, size, completion or required-current-pointer failures receive at most one corrective attempt using bounded original inputs, without appending the rejected draft. If it still fails these checks, a bounded database retrieval index becomes the summary without inventing scientific interpretations. Provider/network failures and invalid evidence-source citations preserve the existing summary. The streaming API shows `finalizing` during this post-processing and sends terminal `done` only after the result is durable and the project execution lease is released. The browser keeps Send disabled until then.

If extraction, apply/storage, or summary refresh fails, the completed chat remains valid and a deterministic persistent retry job records the failed stage. Successful extraction JSON is saved so an apply or summary retry does not unnecessarily call the extractor again. Ready jobs are claimed transactionally by the Web application's background loop and opportunistically after Web/CLI turns. Retries use bounded backoff (5 s, 30 s, 2 min, 10 min, then 1 h); a job enters `dead_letter` after 5 failed background retries (`MAX_RETRY_ATTEMPTS`), or immediately for permanent policy/schema, payload, source-context or summary-budget errors. A `processing` job idle for 10 minutes is reclaimed. The project-memory preview API reports pending and dead-letter counts. Historical `dead_letter` jobs are not automatically requeued. Two explicit helpers in `project_memory.extraction.retry`, both dry-run by default and neither calling a model: `requeue_legacy_extraction_budget_failure(store, project_id, assistant_turn_id, dry_run=True)` keeps the attempt count; `requeue_dead_letter_extraction_failures(store, project_id, error_type=..., dry_run=True)` requeues every extract-stage dead letter whose recorded `error_type` a host fix resolved, and resets its attempts.

Shared publication recovery uses a separate `memory_publication` state on the completed run. Each project request retries at most 8 pending publications. After 3 failed attempts, including the first attempt at completion, the publication becomes `blocked`; an explicit `repair_run_memory` call may still retry it. Recovery calls no LLM and reruns no scientific tool. The preview reports `publication_pending_count` and `publication_blocked_count`. Recovery checks the contract version and declaration fingerprint, then the snapshot and its sources, and protects newer or retracted facts; see [Extension guide](extension-guide.md).

Each generation request contains the previous working summary, current priorities, database counts, and the next revision-ordered batch of changed facts/runs, not the whole database. Previous prose longer than the 6,000-character target is replaced only in the generation input by a bounded current database index; the full old text remains stored until an atomic replacement archives it. The complete request is bounded by `project_summary_working_set_max_tokens` (65536 by default, 262144 in 1M mode); at most 512 pending identities are scanned per update. A single oversized row is reduced to identifiers and omission metadata rather than copying its large payload. If more changes remain, a later completed turn consumes the next batch. Missing rows in a batch never mean deletion, and exact historical detail remains available through full-memory search followed by structured memory queries or artifact reads. This budget applies to the separate summary-update request; it does not consume the orchestrator's recent-conversation working budget.

### Supplying context to a model request

`interfaces.turn.prepare_project_session()` coordinates abandoned-run cleanup, checkpoint repair, interrupted chat finalization and publication recovery under the execution lease. It then calls the read-only `project_memory.snapshot.session.read_project_memory_snapshot()` and adapts that snapshot with `ProjectRuntimeContext.from_memory_snapshot()`. A dynamic system prompt injects:

```xml
<gbclaw_memory_context>
<pending_project_plan>
...compact pending pointer, when present...
</pending_project_plan>
<active_project_plan>
...compact active pointer, when present...
</active_project_plan>
</gbclaw_memory_context>
<gbclaw_project_summary>
<project_memory>
...semantic summary...
</project_memory>
</gbclaw_project_summary>
```

Project summary is never stored as fake Human/AI checkpoint messages, and a summary refresh no longer clears recent conversation state. Chat remains available to the Web history API and is not silently converted into model context.

Before each model request, the active orchestrator tokenizer verifies the stored summary against the same configured limit. Unexpected oversized legacy or externally modified content is omitted instead of being truncated into a misleading partial scientific summary; the agent can still use structured project-memory queries.

The default 256K runtime allocation separates the project summary (up to 32K), compressed conversation (up to 32K), recent uncompressed conversation (up to 64K), and live plan/retrieval memory (up to 64K). K means 1024 tokens. The main request reserves at least 32K for generation and safety, leaving 32K for system and tool overhead. Pending and active plan pointers share the live-memory allocation with memory-tool results, independently of the project summary. Request-time memory limiting preserves complete tool results or retrieval pointers rather than cutting JSON partway through a value; it does not truncate database or checkpoint records. `GBCLAW_CONTEXT_1M=1` scales these runtime token budgets by four, subject to the endpoint's configured input limit. Leave numeric budgets undefined to use the defaults that automatically follow this switch; explicitly defining them overrides those defaults. See [configuration](configuration.md#runtime-context-and-working-summary) for runtime overrides and fixed endpoint limits.

### Compacting conversation state

Middleware manages orchestrator approval and short-term state:

- `TurnStartCleanupMiddleware` runs once when a request arrives. It removes earlier turns' reasoning and unanswered copies of the same request from the checkpoint; both remain in the chat history.
- `PlanApprovalGateMiddleware` enforces the current pending decision at tool-call time.
- `ContextPartitionMiddleware` replaces eligible old tool details with reference cards in the model request, retaining status and run/artifact/conclusion identities. Cards never delete files or rewrite the raw tool output. Details with no safe reference or unresolved user choices stay.
- `FailSafeSummarizationMiddleware` persistently replaces old messages with a structured session summary at 48K by default, targeting 24K of recent whole message blocks while retaining a 64K hard working limit. The answer before the latest request stays verbatim unless it alone cannot fit; then it is summarized as well. Memory retrieval is budgeted separately. A failed/invalid summary leaves the checkpoint unchanged; safe reference cards may still allow the request to continue. If the input cannot fit safely, API/CLI return a recoverable pause with saved-state guidance, rather than a generic execution failure or an automatic scientific retry.

Successful session compression changes the active checkpoint message list, not permanent chat, conclusion files, scientific artifacts or archived tool outputs. Those records are not deleted. The working limit covers the currently retained request across turns, not total project history and not a fresh independent allowance for every task.

Session summaries target 2,000 characters with a 4,000-character ceiling. Their four sections contain current focus, active constraints, open items and retrieval references; detailed tool results and completed-work inventories remain in durable project memory. Session summaries are never written back into project semantic memory.

`GET /api/projects/{project_id}/memory` returns the project summary, `semantic_digest`, `source_revision`/`latest_revision`, `changes_pending`, fact/run counts, `generated_at`, `retry_pending_count`, `retry_dead_letter_count`, `publication_pending_count`, `publication_blocked_count`, and exact `active_plan` and `pending_plan` envelopes for the UI.

## Runtime Checkpoints

`ProjectCheckpointRouter` routes LangGraph state by `project_id` to `runtime/checkpoints.sqlite`. Checkpoints are reconstructable execution state, not the source of truth for chat or semantic memory. `project_workspace/execution/recovery.py` removes incomplete tool calls and repairs interrupted message sequences after errors or stop requests.

All threads in one project share this SQLite file and are logically partitioned by LangGraph's `thread_id` and `checkpoint_ns`. Per-thread subdirectories are deliberately avoided: a single database keeps transactions and connection reuse simple, while message compaction and later checkpoint pruning are the mechanisms that actually control growth.

## Web File Access

`project_workspace/artifacts/files.py` provides project-root-bounded directory listing, stat, text preview, download, and raw file access for the Web UI. File browsing is a user interface capability, not an agent recall mechanism; agents use stable run IDs and artifact roles instead.
