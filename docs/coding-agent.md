# Ephemeral Coding Agent

The coding agent fills small gaps in a research workflow: parsing an unfamiliar output, reshaping a dataset, preparing a custom table, or creating a reproducible analysis script or plot. The orchestrator invokes it through `delegate_coding`. When a Structure, LAMMPS, or Analysis tool already covers the task, that established workflow takes precedence.

This guide explains what the worker can access, how to choose its Python environment, and how useful results become durable project evidence. For the system-wide division of responsibilities, see [Architecture and design](architecture.md).

## Design rationale

Research questions often require an analysis that was not anticipated when a tool was written. Custom code provides that flexibility, while predefined tools keep repeated calculations consistent. The coding agent therefore receives a bounded task, exact input references, and an expected result instead of open-ended responsibility for the repository or an entire investigation.

The worker is temporary; its evidence is persistent. Each invocation gets a fresh workspace so that hidden files or state from an earlier task cannot silently affect the next one. Useful scripts and outputs are explicitly validated and published with their source references. That separation allows later agents and researchers to inspect and reuse the result after the worker is gone.

Execution checks answer questions such as “did this script run?” and “does this table have the required columns?” Physical interpretation still depends on the model, assumptions, numerical method, and researcher review. A new analysis that proves useful repeatedly should be reviewed and promoted into a regular tool, rather than regenerated for every case.

## A typical coding task

For example, a researcher may want a custom comparison table from several completed shear runs:

1. The orchestrator retrieves the exact source artifacts and checks `list_coding_environments` for an appropriate installed runtime.
2. It delegates the specific transformation with input references, expected output files, and a compute budget. The host stages verified input copies under `/inputs`.
3. The worker writes and tests its script under `/workspace`. If more evidence is needed, it can request only inputs covered by the delegate's declared grants.
4. `publish_coding_result` reruns the declared validation commands, checks the selected outputs, and records the published files and their sources.
5. The orchestrator receives a concise result with durable references; the temporary workspace is removed.

The [runtime registry](#runtime-environment-registry) includes the optional `ase` alias. Selecting it does not install ASE: the configured Conda environment and required packages must already exist on the host.

## Lifecycle and Trust Boundary

Each call creates a fresh Deep Agents graph with an in-memory checkpointer, which holds execution state only for that invocation. GBclaw does not enable Deep Agents `memory`/`AGENTS.md`, skills, or its nested general-purpose subagent. The graph is discarded after the call; durable records use the normal GBclaw project-memory system described below.

The worker lives in `src/domains/coding/`: `agent.py` builds the Deep Agents graph, `middleware.py` holds its compaction and step-budget middleware, `delegate.py` defines `delegate_coding`, `run.py` holds `CodingRunSession` (run record, input staging, validation, and publication), `sandbox/` holds the sandbox contract and its Bubblewrap backend (which also applies the per-process `prlimit` limits), `limits/` holds the process-tree limits (sampled monitor or delegated cgroup v2) and the trusted cgroup launcher, `environments.py` and `environments.toml` hold the runtime registry, `orchestrator.py` builds `list_coding_environments` and shares the registry, config and slot pool with the delegate, `resources.py` holds the compute-slot pool, `budget.py` holds the task deadline, `prompt.py` holds the worker prompt and orchestrator routing guidance, `extension.py` declares the domain (workflow, `publish_coding_result` memory spec, worker, tool labels), and `settings.py` reads the `.env` limits.

The graph uses `BubblewrapSandboxBackend`, not `LocalShellBackend`:

- `/inputs`: host-verified snapshots of exact completed-run artifacts, mounted read-only.
- `/workspace`: the only writable work directory.
- `/tmp`: disposable backend/editor scratch.
- `/opt/gbclaw`: the current Python environment, mounted read-only.
- `/opt/runtime`: the selected allowlisted Conda environment, mounted read-only and placed first on `PATH`.
- Repository, project database, manifests, published directory, user Home, and host processes: not mounted.
- External network: always denied. Serial sandboxes reject socket syscalls; explicitly allocated local MPI uses sockets only inside a fresh Bubblewrap network namespace. If the host cannot create that namespace, MPI fails closed instead of falling back to host networking.
- Linux capabilities are dropped before the command starts.
- Commands: bounded by wall time, per-process CPU/address-space/file-size/open-file limits, a UID-scoped process-count limit, output and aggregate workspace quotas. `prlimit` is required. Process-tree limits use delegated cgroup v2 when configured, or explicitly reported sampling.
- The Deep Agents graph has a separate bounded recursion limit (`GBCLAW_CODING_RECURSION_LIMIT`, 200 by default). When about eight tool rounds remain, each model request carries a notice to publish now, because unpublished work is discarded at the limit.

`CodingRunSession` uses only the `CodingSandbox` contract in `src/domains/coding/sandbox/base.py` (building `BubblewrapSandboxBackend` when no factory is supplied). It is a Deep Agents `SandboxBackendProtocol` whose shell and file operations see `/inputs` (read-only), `/workspace` and `/tmp`, extended with:

- `publication_guard()`: excludes parallel shell/file mutations while inputs are added and outputs are validated and published.
- `seal()`: rejects all later mutations after durable publication.
- `shutdown()`: stops running commands and releases resources; repeated calls do nothing.
- `resource_record()`: the resource limits actually enforced, saved in the run record.
- `command_records`: one record per executed command, in order.
- `session_key` and `task_budget` attributes for cancellation and the task deadline.

`BubblewrapSandboxBackend` (`src/domains/coding/sandbox/bubblewrap.py`) is the built-in implementation. `CodingRunSession.prepare(sandbox_factory=...)` and `make_delegate_coding_tool(sandbox_factory=...)` accept another `CodingSandboxFactory`, which is called with keyword arguments for the run's sandbox ID, input, workspace and temporary roots, coding config, selected environment, resource allocation, session key, and task budget. The `delegate_coding` tool assembled for the orchestrator uses the Bubblewrap default. The mounts, network policy, and limits listed above are properties of that backend; a replacement backend must provide its own isolation.

Cancellation is tied to the specific execution and its owning API worker. Even if `/chat/stop` reaches a different worker, the shared cancellation signal tells the executing worker to terminate its command. Resource waiting, model/tool calls, and sandbox commands observe that same signal. The live run is marked cancelled, and the workspace is removed after the backend shuts down in the worker's `finally` block. A cancellation from an earlier execution cannot target a later turn.

One elapsed-time budget covers the entire task, starting before resource admission and including setup, model waiting, commands, edits, and publication. A watchdog closes the sandbox when that budget expires, so late model calls cannot publish results. A blocking request may remain in a background daemon thread until its transport timeout, but its tool access has been revoked. Already published outputs remain available if only the final model response times out.

## Additional Inputs

`delegate_coding.input_grants` declares an exact completed-run scope in the active project:

```json
[{"run_id": "exact-completed-run", "roles": ["stress_strain_data"]}]
```

Use `artifact_ids` to narrow the scope; when both fields are supplied, both must match. Empty grants and wildcards are rejected. Omitting grants permits only the initially staged artifacts. The worker's `request_input_artifacts(references, purpose)` verifies source identity/hash and cumulative file/byte quotas, then appends read-only snapshots. Duplicate requests reuse an existing snapshot. Each addition records source relationships, input-request evidence and a new run fingerprint. Publication rechecks the staged input hashes.

Outside-scope requests return `input_scope_required` and save precise missing references in run progress. Scratch is retained for the current invocation; it is still removed when that invocation ends. Durable draft export/resume is a separate capability. Input staging failures prevent a partially indexed run from completing.

## Process-Tree Resource Enforcement

`GBCLAW_CODING_RESOURCE_MODE` accepts `auto` (default), `monitor`, or `cgroup`:

- `auto`: uses `GBCLAW_CODING_CGROUP_ROOT` if a suitable delegated root is supplied, otherwise records the fallback reason and uses `proc_monitor`.
- `monitor`: samples aggregate RSS, CPU time and process/thread count every 0.25 seconds and terminates observed violations. Shared pages can be counted multiple times; short-lived processes can escape sampling. This mode supplies no kernel CPU bandwidth cap.
- `cgroup`: requires delegation; when the limits cannot be enforced, each coding sandbox fails to start (the delegation fails). There is no monitoring fallback.

The host-owned absolute root must be within `/sys/fs/cgroup` and enable `cpu`, `memory` and `pids` in `cgroup.subtree_control`. One task gets its own child group. A trusted launcher joins before exec; all worker forks inherit the group. `memory.max`, `memory.swap.max=0`, `pids.max` and `cpu.max` bound aggregate memory, swap, tasks and CPU bandwidth. CPU quota follows allocated compute slots; cumulative CPU time is checked across commands. `cgroup.kill` terminates the group on cleanup. Controller behavior follows the [Linux cgroup v2 documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html).

Input context and successful/failed run records expose the actual mode, kernel-limit flag, observed resource peaks and fallback reason. Resource admission remains process-local; these additions do not create a shared multi-server compute pool.

## Runtime Environment Registry

Configure runtimes in the host-owned [`src/domains/coding/environments.toml`](../src/domains/coding/environments.toml). Set `GBCLAW_CODING_ENVIRONMENTS_FILE` to use a custom registry; relative paths are resolved from the repository root. The orchestrator selects a registered alias, such as `current` or `ase`; it cannot supply an arbitrary host path. `list_coding_environments` reports which entries are available, their capabilities, and the versions of common installed packages. It also reports an environment fingerprint derived from Conda history and installed distribution metadata, so pip package changes affect it too. Host paths are not exposed.

The built-in entries are:

| Alias | Purpose | Capability |
| --- | --- | --- |
| `current` | Current service Python environment for plotting and post-processing | none |
| `lammps` | Small custom serial or bounded local-MPI LAMMPS validation | `lammps`, `mpi` |
| `ase` | Small atomistic scripts and post-processing with Atomic Simulation Environment | `ase` |

These are stable aliases, not Conda environment names. `current` is the default and uses the Python environment running the service. `lammps` follows `GBCLAW_LAMMPS_CONDA_ENV`, defaulting to `GBclaw-lammps`. `ase` follows `GBCLAW_ASE_CONDA_ENV`, defaulting to `GBclaw-ase`. Changing an actual environment name does not require renaming its alias; restart the service to apply changes.

ASE is an optional coding runtime, not a new specialist workflow. After the host installs the Conda environment and ASE, the orchestrator checks `list_coding_environments` for `available=true` and an `ase` package version, then selects `delegate_coding(environment="ase", ...)`. The worker uses the selected runtime's `python` under `/opt/runtime`; no `conda activate` is needed inside the sandbox. A missing environment stays listed as unavailable and does not prevent GBclaw from starting. A missing package is reported, without automatic installation or runtime fallback. ASE does not grant LAMMPS/MPI access; calculators can require separately installed dependencies.

MPI remains blocked unless the delegate explicitly requests `mpi_ranks > 1`, the environment has `mpi`, and the ranks fit the allocated compute slots. Construction, minimization, shear, NEB, and production MPI jobs stay in `delegate_lammps`, where domain provenance rules are enforced. Add another TOML table to register a future environment, then restart GBclaw.

```toml
[environments.fenicsx]
conda_env = "fenicsx"
description = "Finite-element post-processing environment."
capabilities = []
```

An entry may instead use `prefix = "/absolute/read-only/prefix"`; this remains trusted host configuration and is never accepted as a `delegate_coding` argument. Registering an environment makes an installed dependency available to bounded custom tasks; adding a recurring scientific workflow also requires the tool, validation, and publication integration described in [Memory extension](extension-guide.md).

## Aggregate Resource Admission

All orchestrator graphs in one GBclaw process share one weighted coding resource pool. The default reservation is 4 compute slots and the total is 36, independently configured from LAMMPS through `GBCLAW_CODING_AGENT_COMPUTE_SLOTS` and `GBCLAW_CODING_TOTAL_COMPUTE_SLOTS`. Independent coding delegations may run concurrently while their summed allocations fit. A call that does not fit waits; completion, failure, or cancellation releases its lease.

`delegate_coding(compute_slots=..., mpi_ranks=...)` lets the orchestrator size one sandbox. `mpi_ranks` defaults to 1 and may never exceed `compute_slots`; when only `mpi_ranks` is supplied, the compute reservation equals it. Each MPI launcher must include a literal `-n`/`-np` no larger than the host allocation. `list_coding_environments` reports both capabilities and current static resource limits.

## Persistent Project Layout

```text
projects/{project_id}/artifacts/code/{run_id}/
├── manifest.json              # host controlled
├── input.json                 # task, exact input references, expected outputs, input grants, sandbox policy, runtime environment, resource allocation
├── input_request-*.json       # one per request_input_artifacts addition (purpose + staged inputs)
├── inputs/                    # immutable provenance snapshots
├── published/                 # selected validated scripts/data/plots/reports
├── workspace/                 # exists only while the worker is alive
└── .sandbox-tmp/              # exists only while the worker is alive
```

`publish_coding_result` accepts one or more output files and one to five validation commands. GBclaw executes those commands inside the same sandbox, rejects a failed check, traversal, symlinks, directories, duplicates, or quota violations, copies the selected regular files into `published/` through a SHA-256-verified atomic replace, registers them in the manifest/SQLite artifact index, and completes the `code` run. Staged source snapshots are likewise checked against the source hash before the sandbox starts. The `workspace` and `.sandbox-tmp` trees are destroyed in a `finally` block on success, failure, or cancellation. Workspace files that the worker did not publish and that host salvage did not save as declared `expected_outputs` never become project memory.

If the worker stops without a successful publication (it exits, reaches the step limit, or fails) and the task was not cancelled, timed out, or waiting for more input scope, the host checks the declared `expected_outputs` before the workspace is destroyed. When every required one exists and passes its contract, the host publishes those declared files, and only those, completes the run with `publication.mode="host_salvage"` and `validation.worker_validation_run=false`, and reports the delegation as `partial`: the files are durable, but the worker never validated or summarized them.

`delegate_coding.expected_outputs` accepts exact workspace filenames, or structured contracts:

```json
[
  {"path": "data.csv", "role": "data", "columns": ["strain", "stress"], "min_bytes": 1},
  {"path": "report.json", "role": "report", "required_keys": ["summary"]},
  {"path": "plot.png", "role": "plot"}
]
```

`required` defaults to true. `/workspace/` paths normalize to workspace-relative filenames; traversal, duplicate paths, missing required files, wrong roles and empty permanent outputs are rejected before publication. The host checks JSON syntax/finite constants, CSV headers/row widths, PNG/JPEG encoding and size, and Python syntax. `columns` and `required_keys` declare additional CSV/JSON requirements; a required key is a top-level key or a dot path through nested objects (`pairs.P1`), and a rejection names every missing column or key. A rejected publication may be corrected and retried within the same run. Worker validation commands remain required; successful commands and valid formats do not independently establish scientific correctness. Published validation records separate `output_contract_passed`, `format_checks` and `scientific_checks_applied`.

## Long-Term Memory

The coding worker receives the same bounded, request-specific completed-run/fact handoff as other workers and read-only semantic query/search tools. File content is available only through the explicit staged inputs.

Persistence then uses the existing GBclaw hierarchy:

1. `runs`: the `code` lifecycle row records sources, task, selected environment fingerprint, compute/MPI allocation, status, validation, and result summary.
2. `artifacts`: `input.json`, source snapshots, input-request records, and published outputs are manifested/indexed.
3. `facts`: completing a `code` run, by worker publication or host salvage, deterministically writes the `scientific_conclusion` fact `run:<run_id>:main_conclusion` (summary, task, expected outputs, published artifacts, validation record). The builder refuses a run whose recorded validation did not pass. A salvaged run's record keeps `worker_validation_run=false`.
4. `memory/summary.md` (view of the `semantic_summary` record): the normal incremental summarizer retains relevant coding run IDs, source relationships, validation, and published artifact roles.

This keeps the agent itself use-and-discard while making its reproducible evidence durable.
