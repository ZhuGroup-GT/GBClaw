# GBclaw src Code Map

Use this map to find the module that owns a behavior before changing it. Start with [Architecture and design](architecture.md) for the research workflow, or the [documentation index](README.md) to choose a user or developer guide. This page connects that design to the current implementation under `src/`.

## Design rationale and ownership

The manuscript separates coordination, specialist execution, and persistent research memory. The source tree follows the same division: agents decide which work to request, domain packages perform scientific operations, and shared services record and retrieve their results. A new scientific workflow should be able to reuse those services without reimplementing conversation handling or storage.

| Package | Owns | Boundary to preserve |
| --- | --- | --- |
| `core/` | Settings, model routing and providers, execution sessions and cancellation, the chat stream event schema, and small I/O helpers | Import no domain, agent, or workspace code; keep machine and model choices separate from scientific task implementations. |
| `extensions/` | The domain registry: declaration types, the host's core declarations, loading, and validation | Keep declarations as plain data and `"module:attr"` strings; runtime hooks are resolved only after bootstrap. |
| `project_workspace/` | Project identity, chat storage, files, checkpoints, and execution ownership | Manage the project lifecycle without owning scientific algorithms. |
| `project_memory/` | Research facts and runs, artifact manifests, summaries, publication, extraction, and retrieval | Preserve evidence origin; a conversational interpretation is not a computed measurement. |
| `planning/` | Versioned project plans, approval-gated drafts, plan rules, and the per-turn planning mode | Activate or revise a plan only on an explicit decision, never on keywords in conversation text. |
| `agents/` | Agent graphs, the orchestrator and planner, the shared worker lifecycle, model-context middleware, agent-facing memory tools, and streaming | Adapt project records into model context; leave database ownership to the memory and workspace services. |
| `domains/` | One package per scientific domain: declarations, worker, delegate, prompts, tools, and simulation or analysis engines | Publish results through the shared run interface instead of writing another memory database. |
| `interfaces/` and `web/` | HTTP API, terminal CLI, the turn pipeline they share, and the static Web UI | Share the backend services and one turn pipeline across the API, CLI, and Web UI rather than implementing a separate research workflow. |

The table lists packages from the lowest layer up, and imports follow that direction: `core ← extensions ← project_workspace ← project_memory ← planning ← agents ← domains ← interfaces`. `agents` reaches domain workers, tool providers, orchestrator tools, and routing and planning text through the registry's `"module:attr"` hook strings rather than importing them. Two narrow exceptions remain: `ProjectStore` opens the memory repository, artifact store, run service, and summary view writer through lazy imports, and `domains/structure/routes.py` uses the API's dependency helpers.

Two packages handle different kinds of context. `agents/middleware/` manages what a conversation can fit in the model window, including its session summary and the single-pool compaction of disposable worker histories (`worker.py`, `worker_policy.py`). `project_memory/snapshot/` reads a reusable snapshot of persistent project memory. Its `session.py` loads that snapshot for a caller; it does not own the conversation summary or create a new thread.

### Where to make an extension

| Desired change | Starting point |
| --- | --- |
| Add a reusable scientific tool | Its domain package under `domains/`: the tool module, the worker's tool list in `catalog.py`, and the memory declarations and publication contract in `extension.py`. |
| Add tools to an existing worker from another package | A `ToolProviderSpec` in that extension's `tool_providers`; its factory returns the extra tools for the named worker. |
| Add a specialist agent | A `WorkerDefinition` and its memory policy in a domain's `extension.py`, plus the worker (`agent.py`), the delegate tool factory (`delegate.py`), and the prompt and orchestrator routing section (`prompt.py`). |
| Add a fact type, workflow, or query tool | The public declarations in `extensions/specs.py`; follow [Extension guide](extension-guide.md). |
| Add an HTTP route, startup or shutdown hook, tool label, or LLM provider | The `routers`, `startup`, `shutdown`, `tool_labels`, and `llm_providers` fields of `DomainExtension`; `interfaces/lifecycle.py` runs them for the API and CLI. Built-in providers are in `core/llm/providers/`. |
| Change a chat stream event or slash command | `core/stream_events.py`, then regenerate `web/assets/js/stream_events.js` with `python -m core.stream_events`; slash commands are declared once in `interfaces/commands.py`. |
| Change summary selection or compression | `project_memory/summary/` for project knowledge; `agents/middleware/` for conversation continuity. |
| Change project files, chat persistence, or locking | The corresponding service under `project_workspace/`. |
| Change plan rules, drafts, or approval | `planning/`; the planning worker, its tools, and its gates are in `agents/planner/`. |
| Support a local Python environment for generated scripts | `src/domains/coding/environments.toml`; see [Coding agent](coding-agent.md). |

Use the shared `ToolRunSession` and worker lifecycle when adding persistent scientific work. They provide stable run references, artifact registration, and completion tracking, so a domain extension does not need its own parallel history or summary mechanism.

## Document Index

- [architecture.md](architecture.md): design goals, the researcher-guided workflow, and the reasons behind the module boundaries.
- [getting-started.md](getting-started.md): installation, launchers, CLI commands, and optional scientific runtimes.
- [agent-runtime.md](agent-runtime.md): orchestrator, workers, delegate tools, LangGraph runtime, and streaming events.
- [tools.md](tools.md): agent-facing tools, internal service functions, and the CSL/LAMMPS/analysis/web-search boundaries.
- [dichromatic-map.md](dichromatic-map.md): interactive dichromatic-map cell selection and what its results mean.
- [project-memory.md](project-memory.md): project workspaces, history, summaries, checkpoints, and memory recall.
- [configuration.md](configuration.md): `.key`, `.env`, model routing, thinking settings, LAMMPS resources, and prompt configuration.
- [coding-agent.md](coding-agent.md): disposable Deep Agents worker, sandbox boundary, and durable publication.
- [api-and-ui.md](api-and-ui.md): FastAPI entry points, chat/project/file APIs, and the static Web UI boundary.
- [extension-guide.md](extension-guide.md): domain declarations, workers, tools, host hooks, and checking an extension without a model.
- [roadmap.md](roadmap.md): possible extensions and the design constraints they must keep.
- [references.md](references.md): the framework manuscript, related study, software, and potentials.

Chinese versions of these files are kept under [zh/](zh/).

## Top-Level Modules

```text
src/
├── core/                   # lowest layer: settings, LLM, execution sessions, stream events, I/O
│   ├── paths.py            # SRC_ROOT and REPO_ROOT
│   ├── identifiers.py      # run ID syntax shared by every layer
│   ├── stream_events.py    # chat stream event types and fields; generates the Web UI module
│   ├── settings/           # .env/.key loading, LangSmith, context budgets, web search, execution mode
│   ├── llm/                # model routing config, build_llm factory, thinking, reasoning, truncation
│   │   └── providers/      # ProviderAdapter contract; DeepSeek, OpenAI and Anthropic adapters; tokens.py + vocab/ (offline token counts)
│   ├── execution/          # execution sessions and cancellation, SlotPool, subprocesses, process pool
│   └── util/               # hashing and atomic writes (stdlib only), LangGraph run identity
├── extensions/             # domain registry: specs, loader, validation, host declarations (core.py)
├── project_workspace/      # ProjectStore (store.py), project paths and metadata
│   ├── chat/               # permanent Chat history, traces, request admission, Markdown transcript
│   ├── artifacts/          # safe project file access and image references for the UI
│   ├── execution/          # per-project checkpointers, execution ownership, checkpoint repair
│   └── persistence/        # process-shared locks and SQLite helpers
├── project_memory/         # semantic memory persistence, publication and recall
│   ├── storage/            # SQLite facts/runs/search, conclusions, task state, tool-output archives
│   ├── publication/        # RunService, ToolRunSession, fact builders, validators and atomic writes
│   ├── run_artifacts/      # per-run artifact directories, manifests, interrupted-run cleanup
│   ├── extraction/         # conversation extraction, key policies and durable retries
│   ├── snapshot/           # read-only project memory snapshots and their data contracts
│   ├── summary/            # Project summary policy, generation, archives, retrieval and views
│   └── prompts/            # conversation fact-extraction prompts
├── planning/               # versioned plans, approval-gated drafts, rules, views, planning mode
├── agents/                 # graph.py agent factory; invoke.py run config and reply parsing
│   ├── orchestrator/       # user-facing graph, registry-assembled prompt, domain tools, web search tool
│   ├── planner/            # planning worker, delegate_plan, plan tools and gates, domain_knowledge.md
│   ├── workers/            # WorkerSpec, shared delegate lifecycle, deliverable checks, results, outcomes, plan tracking
│   ├── middleware/         # per-thread Session summary, context editing, recovery, cancellation, worker compaction
│   ├── memory/             # worker recall, memory handoffs, conclusions and result projection
│   │   └── tools/          # model-facing memory, artifact, conclusion, output and task-state tools
│   ├── streaming/          # chat events from LangGraph streams, tool labels, trace segments
│   └── prompts/            # shared result-handoff prompt
├── domains/                # one package per scientific domain (pattern below)
│   ├── structure/          # csl/, dichromatic/, routes.py (dichromatic HTTP API), memory_query.py
│   ├── lammps/             # common/, orientation.py, one subpackage per LAMMPS workflow
│   ├── analysis/           # stress_strain/ (tool.py, analysis.py, model_view.py), neb_mep/, rendering/,
│   │                       #   periodicity/, periodicity_tools.py, common.py (artifact resolution),
│   │                       #   periodicity_artifacts.py (file names shared with the subprocess)
│   └── coding/             # agent.py, middleware.py (compaction + step budget), run.py, sandbox/, limits/,
│                           #   budget.py, resources.py, settings.py, orchestrator.py, environments.py + .toml
└── interfaces/             # commands.py, turn.py (shared turn pipeline), recording.py, lifecycle.py
    ├── api/                # FastAPI app, deps, schemas, ownership, routers/ (chat, projects, registry)
    └── cli/                # terminal CLI (python -m interfaces.cli): chat loop, input, rendering
```

Outside `src/`, the repository root holds the Web UI and the bundled potentials:

```text
web/                        # static Web UI served by the API: index.html and assets/
└── assets/js/              # registry.js reads GET /api/registry (delegate labels, commands, stream policy);
                            # stream_events.js is generated from core/stream_events.py
data/potentials/            # interatomic potential files and the potentials.json catalog
```

Each domain package follows the same pattern. `extension.py` declares its `DomainExtension` as `MEMORY_EXTENSION`; `agent.py` builds the worker; `delegate.py` provides the delegate tool factory for the orchestrator; `prompt.py` holds the worker prompt and the `ORCHESTRATOR_ROUTING` section that the orchestrator prompt includes; and `catalog.py` lists the worker's tools and provides the factory named by its `ToolProviderSpec`. The coding domain has no `catalog.py`: its Deep Agents worker uses the sandbox backend's file and command tools together with the input and publication tools of its run session (`run.py`), and `sandbox/base.py` defines the `CodingSandbox` contract that `sandbox/bubblewrap.py` implements. In `lammps/`, each workflow subpackage (`minimize/`, `structure_minimize/`, `energy/`, `shear/`, `neb/`) has a `tool.py` agent adapter, a `config.py` schema, and an `engine.py` script that runs in the LAMMPS conda environment; `cfg_convert/` uses `converter.py` in place of an engine, and `common/` holds the shared launcher, MPI-rank admission, settings, potential catalog, fact builders, and geometry.

Under `domains/analysis/`, the `tool.py` module of `stress_strain/`, `neb_mep/`, and `rendering/` provides the agent adapters and records linked analysis runs. `periodicity_tools.py` does the same for the `periodicity/` package, which it runs as a subprocess so the service process does not import that package's numerical and OVITO dependencies. In `rendering/`, `ovito_rendering.py` handles generic structure renders with optional reference-frame displacement arrows and a JSON record; `gb_plan_view.py` is the standalone OVITO subprocess entry point for planar Y-normal boundary detection, facing-layer selection, XZ rendering, and diagnostic JSON/profile output. Both render workflows store their indexed outputs under `artifacts/analysis/render/{run_id}/`. Generic top views scale arrows by their displayed XY projections and offset them along +Z; GB plan views use the slab's XZ projections and a +Y offset. `stress_strain/model_view.py` builds the bounded `model_view` that `stress_strain_analysis` and `plot_stress_strain_comparison` attach to their `RunToolResult` (one row per event, one line per curve, at most 15,000 characters); the full result stays in the run's JSON artifact and the archived reply.

Extension declarations use [extensions/specs.py](../src/extensions/specs.py). [extensions/loader.py](../src/extensions/loader.py) collects the built-in declarations (`extensions.core` and each `domains.<d>.extension`) and trusted modules listed in `GBCLAW_EXTENSIONS` (the older `GBCLAW_MEMORY_EXTENSIONS` is still read), validates their references, and freezes the registry on first use, before storage schemas and tools are constructed. The memory implementation has one home under `project_memory/`, with its agent-facing tools in `agents/memory/`; domain computations and fact builders remain in their domain packages. See [Extension guide](extension-guide.md) for the public extension interface.

## Architecture at a Glance

The orchestrator coordinates a research task and gives bounded assignments to specialists. Each worker receives the relevant evidence, uses its domain tools, and returns a result with references that can be retrieved later. See [Agent runtime](agent-runtime.md) for the invocation lifecycle.

The implementation preserves the following contracts:

- Users interact only with the orchestrator.
- The orchestrator has structured semantic/artifact access, optional web search, the tools domains declare for it (`orchestrator_tools`: the coding environment and potential catalogs), and delegates assembled from registered `WorkerDefinition` declarations. Its capability-routing section is assembled from each worker's `routing_guidance` in registry order.
- The five built-in delegates are `delegate_plan`, `delegate_csl`, `delegate_lammps`, `delegate_analysis`, and `delegate_coding`. [workers/lifecycle.py](../src/agents/workers/lifecycle.py) supplies their common invocation identity, recall, memory tools, conclusion persistence and task-state lifecycle; the planner lives in `agents/planner/`, and each domain package owns its scientific tools and worker.
- Routing, planning gates, tool classification, and the Web UI's delegate labels and slash commands consume the same worker/capability declarations. New domains do not add another central list of agent names. [WorkerOutcome](../src/agents/workers/outcome.py) validates final states and evidence references; unknown statuses cannot become successful completion.
- Chat stream events have one schema in [core/stream_events.py](../src/core/stream_events.py), shared by the API, CLI, chat trace, and Web UI. Worker events forwarded into the orchestrator stream carry `parent_id` and `parent_name`.
- Planning first persists an approval-required draft and pending pointer. Explicit approval atomically activates/revises the versioned plan; planned scientific delegates then update step boundaries and exact progress remains searchable across turns.
- Worker traces are retained once in permanent Chat history; scientific tools write stable runs and manifested artifacts directly.
- Semantic memory and permanent Chat are separate; LangGraph checkpoints are reconstructable runtime state. Worker graphs are created per task.
- Facts retain their origin through `source_type`: tool results, conversation-derived context, agent reports, or workflow state. Extraction remains available, while query filters, worker handoffs and source-separated summaries keep interpretations distinct from computed results.
- Scientific tools bind their memory policy before computation. [ToolRunSession](../src/project_memory/publication/session.py) exposes artifact registration, explicit publication and full-result references. [RunService](../src/project_memory/publication/service.py) preflights trusted hooks and persists contract versions and fingerprints; recovery requires an exact policy match and never reruns science.

Core request flow:

```text
FastAPI / CLI
  -> interfaces.turn.run_turn() (one turn pipeline for both clients)
  -> prepare_project_session() (recover state, then read memory context)
  -> dynamic project-memory prompt + context editing / session summarization
  -> orchestrator LangGraph
  -> registry-built delegate -> shared worker lifecycle -> domain tools
  -> ToolRunSession / RunService -> manifested artifacts and atomic fact publication
  -> complete permanent Chat history (interfaces.recording)
  -> post-completion fact extraction and SQLite semantic-summary/view refresh
```

## Runtime Data Location

Source code lives in `src/`. Runtime data lives under the repository-level `projects/` directory (`GBCLAW_PROJECTS_ROOT` overrides it):

```text
projects/{project_id}/
├── project.json
├── plan.md                  # generated active-plan view for the file browser
├── chat/
│   ├── history.sqlite      # authoritative Chat history and trace
│   └── transcript.md       # atomically generated human-readable view
├── memory/
│   ├── index.sqlite         # semantic records, search index, and authoritative project summary
│   ├── summary.md           # human-readable generated view
│   ├── summary_archive/     # generated views of archived summaries; SQLite is authoritative
│   └── plans/{plan_id}/     # derived current and immutable-revision JSON views (drafts in plans/drafts/)
├── runtime/
│   ├── checkpoints.sqlite    # all threads, partitioned by thread_id
│   ├── tool_outputs/         # archived full tool replies for read_tool_output, incl. worker-compaction spills
│   └── dichromatic_requests/ # pending interactive DichromaticMap cell requests
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

Each workflow's artifact directory is declared in its domain's `extension.py`. `projects/` is a runtime workspace separate from `src`. Chat is used for history replay; agents recall research state from structured runs/facts and manifested artifacts.
