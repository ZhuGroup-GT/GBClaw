# Configuration

Use this guide to choose models, connect scientific runtimes, and set resource limits. For a first installation, follow [Getting started](getting-started.md); most users can keep the built-in budgets and change only their model endpoint and local environment names.

## Design rationale

GBclaw separates research decisions, model access, and machine resources. A planning model may need stronger reasoning than a routine analysis task, while both still share the same project records and execution rules. Configuration changes which resources a role can use; it does not replace the scientific checks performed by tools.

| What you want to change | Where to configure it | Why it is separate |
| --- | --- | --- |
| Model credentials, endpoints, and role routing | Repository `.key` | Model access can change without changing scientific tools. |
| Model input/output limits and context allocations | `.key`, including `[runtime_context]` | The application must budget each request within the selected endpoint's capacity. |
| Conda environment names, scheduling, timeouts, and process limits | Repository `.env` or process environment | Machine-specific execution settings should not live in prompts or model credentials. |
| Environments available to generated Python scripts | `src/domains/coding/environments.toml` | The host declares the runtimes the coding worker may use. |
| Agent responsibilities and domain instructions | Prompt modules beside each agent and domain (see [Prompt Files](#prompt-files)) | Instructions explain how to use registered tools; execution services enforce their contracts. |

There are three different limits to keep in mind: how much a model can read, how much it can generate, and how much computation a tool may run. Increasing one does not increase the others. Project and session summaries also have small content limits so that a larger context window does not turn them into growing archives. Exact records remain available through memory tools.

The broader reasoning behind these boundaries is described in [Architecture and design](architecture.md). The sections below document the current configuration fields and defaults.

## LLM Config

`src/core/llm/config.py` reads model configuration from the repository-level `.key` file.

There are two modes.

### Single Default Model

When `[settings] use_tier_routing = false`, the config must include:

```ini
[default]
provider = openai
api_mode = responses
api_key = ...
base_url = ...
model = ...
temperature = 0.3
timeout = 120
thinking = disabled
```

All roles use the default endpoint.

### Tier Routing

When `use_tier_routing = true`, the config must include:

```ini
[model_routing]
default = medium
orchestrator = premium
planning = premium
memory = medium
structure = medium
lammps = medium
analysis = medium
coding = medium
literature = premium

[premium]
...

[medium]
...

[light]
...
```

Supported tier names:

- `premium`
- `medium`
- `light`

`ModelRouter.for_role(role)` checks the role first, then falls back to `model_routing.default`.

The `planning` role is used only for on-demand deep planning. A stronger reasoning model can be routed here without making routine orchestrator or worker calls equally expensive.

The `coding` role drives the disposable Deep Agents worker. It falls back to `default` when no explicit route is configured.

## Coding Sandbox

The coding worker requires Bubblewrap (default `/usr/bin/bwrap`). Allowlisted read-only Conda prefixes are configured in `src/domains/coding/environments.toml`; an alternate registry path may be set with `GBCLAW_CODING_ENVIRONMENTS_FILE`. Relative registry paths are resolved from the repository root.

The default alias `current` uses the service's Python environment; `lammps` follows `GBCLAW_LAMMPS_CONDA_ENV`. The optional `ase` alias follows `GBCLAW_ASE_CONDA_ENV` (default `GBclaw-ase`) for Atomic Simulation Environment scripts. Aliases do not depend on Conda names. Registering an environment does not install it or its packages; unavailable environments remain visible in the catalog without blocking startup.

Runtime limits live in `.env` and are read by `src/domains/coding/settings.py`: `GBCLAW_CODING_COMMAND_TIMEOUT_SEC`, `GBCLAW_CODING_TASK_TIMEOUT_SEC`, input/output/workspace/publication limits, memory/CPU/process/open file limits, the Deep Agents step limit `GBCLAW_CODING_RECURSION_LIMIT` (default `200`), and optional `GBCLAW_BWRAP_PATH`. The process-wide coding pool is independently controlled by `GBCLAW_CODING_AGENT_COMPUTE_SLOTS` (default `4`) and `GBCLAW_CODING_TOTAL_COMPUTE_SLOTS` (default `36`). External networking has no opt-out; an MPI sandbox uses only a private network namespace. See [coding-agent.md](coding-agent.md) for the trust boundary and persistent layout.

The relaxed per-sandbox defaults are 300 seconds per command, 1800 seconds per task, 128 MiB writable workspace, 4096 MiB address space per process, 600 CPU seconds, and at most 96 processes before the dynamic allocation-derived cap is applied. These use `GBCLAW_CODING_COMMAND_TIMEOUT_SEC`, `GBCLAW_CODING_TASK_TIMEOUT_SEC`, `GBCLAW_CODING_MAX_WORKSPACE_MB`, `GBCLAW_CODING_MEMORY_MB`, `GBCLAW_CODING_CPU_SECONDS`, and `GBCLAW_CODING_MAX_PROCESSES` respectively.

Memory, CPU-time and task-count budgets now also apply to the process tree. Select `GBCLAW_CODING_RESOURCE_MODE=auto|monitor|cgroup`; default `auto` explicitly falls back to sampled monitoring when no delegated `GBCLAW_CODING_CGROUP_ROOT` is available. A cgroup root must be absolute, under `/sys/fs/cgroup`, with delegated `cpu`, `memory`, `pids` controllers. In `cgroup` mode a coding sandbox refuses to start (the delegation fails) if it cannot enforce limits, including when the kernel lacks `cgroup.kill`; the service itself still starts. `GBCLAW_CODING_MEMORY_MB` is also the tree memory budget; `GBCLAW_CODING_MAX_PROCESSES` counts processes and threads at tree level. The task timeout includes admission and model waiting. See the coding guide for sampling limits.

## Memory Model Calls

The `memory` role is the base endpoint for post-completion fact extraction and summary generation. Each auxiliary call clones this endpoint, disables thinking, clears reasoning effort, and does not mutate the endpoint used by other roles. A model whose lowest reasoning level is not off (GPT-6, the o-series, always-adaptive Claude) keeps reasoning at that floor; such a clone gets a 4,096-token allowance above its answer-sized output cap, within the endpoint's own limit, so reasoning tokens cannot truncate the answer. Fact extraction runs in a request window of at most 64K tokens, from which its output cap and a 1,024-token margin are reserved (about 55K input tokens with an 8,192-token cap), and writes up to 8,192 output tokens (20 mutations with values and excerpts); conclusion abstracts get 2,048, and the project and session summaries get two tokens per character of their caps so CJK text is not cut. Project-summary generation targets 6,000 characters and is capped at 12,000 characters. The effective cap is `min(12,000, T − 256)` characters, where `T = min([runtime_context] project_summary_max_tokens, the memory endpoint's output cap, 24,000)`. A memory endpoint with the default 8,192-token output cap (thinking disabled, `max_tokens` unset) therefore caps new summaries at 7,936 characters.

## LLMEndpoint Fields

`LLMEndpoint` contains:

- `provider`
- `api_mode`
- `api_key`
- `base_url`
- `model`
- `temperature`
- `max_tokens`
- `max_input_tokens`
- `timeout`
- `thinking`
- `reasoning_effort`

If `max_tokens` is not explicit:

- thinking disabled: 8192
- thinking enabled: 32768

`thinking` supports `enabled` / `disabled` and also accepts true/false-style aliases. `reasoning_effort` uses one portable ladder: `low` / `medium` / `high` / `xhigh` / `max`. For known model generations, an unavailable level is mapped to the nearest supported level rather than requiring a provider-specific value.

`max_tokens` limits model output (thinking + final answer together), while `max_input_tokens` describes the input context window. The adapter sends this generation ceiling as `max_output_tokens` for OpenAI Responses, `max_completion_tokens` for OpenAI Chat Completions, or the provider's `max_tokens` field. It does not limit Python tool output, nested worker histories, or retrieved reports. Those use the shared result projection and paged memory readers. Increasing working context or enabling 1M does not increase the model's generation ceiling.

Concise result prompts keep final answers short without forcing reasoning and tool-call generation into an overly small hard limit. With `thinking = enabled`, omit `max_tokens` (defaults to 32768) or set it to at least 16384, and prefer `reasoning_effort = low` unless the task needs deep chain-of-thought. Pairing thinking with a small budget such as 8192 can exhaust tokens on reasoning and leave an empty final answer.

If `max_input_tokens` is omitted, GBclaw uses 262144 tokens in default mode or 1048576 with `GBCLAW_CONTEXT_1M=1`. These are local budgeting defaults; set the field explicitly when the endpoint exposes a smaller context window. Explicit endpoint limits are never scaled by the switch, and available model-profile limits also bound runtime context. Defining `max_input_tokens` overrides that automatic default. Leave it undefined unless you intend to impose a fixed endpoint limit and understand the effect on both context modes.

The same four fields control every provider:

- `max_tokens`: hard ceiling for all generated output, including thinking and the final answer.
- `max_input_tokens`: GBclaw's local input-context ceiling; it is not sent as a generation field.
- `thinking`: common on/off intent.
- `reasoning_effort`: common soft reasoning-depth intent.

Provider adapters translate those fields as follows:

- DeepSeek: `thinking.type` plus `reasoning_effort`; `medium` maps to `high` and `xhigh` maps to `max`. Reasoning is read from `reasoning_content`.
- OpenAI: `reasoning_effort` for Chat Completions, or `reasoning.effort` and `reasoning.summary=auto` for Responses. GPT-5.6 accepts `max`; older known GPT-5 variants map `max` to their portable upper level. With `thinking = disabled`, GBclaw sends the lowest level that generation accepts: `minimal` for GPT-5, `none` for GPT-5.1–5.x, `low` for GPT-6 and the o-series, and no level at all for an unrecognized deployment name.
- Anthropic: Claude 4.5 and earlier use manual `budget_tokens` derived from `max_tokens` and `reasoning_effort`; Claude 4.6 and later use adaptive thinking plus `output_config.effort`. Claude thinking blocks become the same UI event used by other providers.

Reasoning models often reject sampling controls. GBclaw omits `temperature` when thinking is enabled, and for adaptive Claude generations even when thinking is explicitly disabled.

For `provider = openai`, `api_mode` supports:

- `chat_completions` (default): widest OpenAI-compatible gateway support. Final-answer tokens stream normally. The reasoning panel is populated only when the gateway exposes a non-standard `reasoning_content`, `reasoning`, `thinking`, or `reasoning_details` field; hidden OpenAI reasoning is not fabricated.
- `responses`: uses `/responses` with `output_version="responses/v1"`. When thinking is enabled, GBclaw requests `reasoning.summary = auto` and streams disclosed reasoning-summary blocks into the reasoning panel. The endpoint must implement the Responses API and function calling. GBclaw omits `temperature` in this mode because reasoning deployments reject it, including when an enterprise deployment name prevents the SDK from recognizing the underlying public model.

Official and private OpenAI deployments share the same GBclaw fields, but deployment names, supported API modes, context/rate limits, authentication, and disclosed reasoning fields remain endpoint capabilities. A private gateway may also map its deployment ID to a different concrete model version, so the response metadata is authoritative for audits.

`provider = anthropic` uses the Anthropic Messages API; `provider = claude` is accepted as an alias. Keep `api_mode = chat_completions` in that section: in the common config this means “not OpenAI Responses” and is not sent to Anthropic. `base_url` may point to the official Anthropic API or a compatible Messages endpoint.

## Runtime Context and Working Summary

### Choosing a budget

Start with the defaults and set an explicit endpoint `max_input_tokens` if your provider has a smaller input window. Enable 1M mode only for an endpoint that supports it. A context allocation is space available to a layer, not a request to fill that space.

Two summaries serve different purposes: the project summary points to important research state across the project; the session summary keeps the current conversation coherent. Neither needs to repeat full tool outputs. See [Project memory](project-memory.md) for what is retained and how an agent retrieves details.

`[runtime_context]`, read by `src/core/settings/runtime_context.py`, configures short-term context for the user-facing orchestrator, bounds the incremental project working-summary flow, and sizes delegated-worker compaction. Delegated workers (structure, LAMMPS, analysis, planning and coding) are ephemeral per task and use a simpler policy: one pool equal to the runtime window (`total_context_tokens`, bounded by the endpoint and model-profile `max_input_tokens`) minus the output reserve (the larger of the endpoint's output cap and `safety_margin_tokens`) and the static prompt/tool schemas. History is compacted once it reaches `worker_compact_fraction` of that pool (default 0.8; at 256K with a 32K reserve this is 0.8 × (229,376 − static tokens), at most about 179K). Compaction keeps the task and the newest complete tool-call blocks (about a quarter of the pool) verbatim and replaces the rest with one summary, written by the worker's own endpoint with thinking off, that keeps results for finished work and detail for work in progress. The in-context summary may take up to a sixth of the pool; completed results that do not fit are moved to an archived file with a pointer in their place, never dropped. Consumed tool results are archived first and can be re-read with `read_tool_output`. A provider overflow compacts the request and retries once; a single tool-call block larger than the pool fails the delegation with a structured context error (`worker_block_exceeds_context`). The coding worker keeps Deep Agents' summarizer and its history offload to the sandbox backend, with the same thresholds, token counter, summary contract and COMPLETED spill; on a provider overflow Deep Agents summarizes and retries instead of raising that error. With `enabled = false`, GBclaw installs no worker compaction; the coding worker then keeps only Deep Agents' own summarization defaults.

**Leave numeric token budgets undefined unless you know why you need an override.** Defining them replaces GBclaw's built-in defaults. The defaults automatically scale with `GBCLAW_CONTEXT_1M`; the commented values below document the base 256K profile and do not need to be enabled for that profile to take effect.

```ini
[runtime_context]
enabled = true
# Overrides replace built-in defaults; leave undefined unless intentional.
# total_context_tokens = 262144
# active_context_tokens = 65536
# project_summary_max_tokens = 32768
# summary_model_max_tokens = 32768
# memory_context_tokens = 65536
# safety_margin_tokens = 32768
# project_summary_working_set_max_tokens = 65536
# worker_compact_fraction = 0.8
```

Related environment / `.env` settings (not in `.key`):

```bash
GBCLAW_CONTEXT_1M=0
# GBCLAW_TOOL_KEEP_RESULTS=4
```

Here K means 1024 tokens. The default allocation is:

| Context component | Default | `GBCLAW_CONTEXT_1M=1` |
|---|---:|---:|
| Total context | 256K | 1M (1024K) |
| Project summary context reservation | Up to 32K | Up to 128K |
| New project summary content | Target 6,000 / maximum 12,000 characters (7,936 when the memory endpoint keeps its default 8,192-token output cap) | Same fixed limits |
| Session summary read allocation | Up to 32K | Up to 128K |
| New session summary content | Target 2,000 / maximum 4,000 characters | Same fixed limits |
| Working context: recent uncompressed conversation | Up to 64K | Up to 256K |
| Live memory: plan state and retrieved memory | Up to 64K | Up to 256K |
| Generation and safety reserve | 32K | 128K |
| Remaining room for system prompt and tool schemas | 32K | 128K |

These are ceilings and reservations, not padding added to every request. Set `GBCLAW_CONTEXT_1M=1` to automatically multiply the default runtime token budgets by four. Intentional `[runtime_context]` overrides are also multiplied by four and must therefore be written in base 256K-mode units. Explicit model `max_input_tokens` overrides instead remain fixed, and can prevent the runtime from using the full 1M budget. The separate project-summary update input budget scales from 64K to 256K as well. Counts such as `GBCLAW_TOOL_KEEP_RESULTS` do not scale. Restart the service after changing this setting.

- `total_context_tokens`: the overall runtime budget, also bounded by the selected endpoint's `max_input_tokens`. The switch does not increase the provider's actual context capacity or override an explicitly configured endpoint limit.
- `active_context_tokens`: the ceiling for recent uncompressed conversation. Project summary, compressed conversation, and live memory have separate budgets.
- `GBCLAW_TOOL_KEEP_RESULTS`: the number of most recent tool results retained by ordinary context editing (default 4 when unset). Before a summary call, the runtime may also replace these recent results with verified archive cards to fit the request. This setting is a preference, not an exemption from context limits.
- `project_summary_max_tokens`: the independent token ceiling for project-summary generation, validation, dynamic injection, and orchestrator context reservation. New content targets 6,000 characters and cannot exceed 12,000 characters; a smaller token allocation further reduces this character budget with a framing reserve. Character limits do not scale in 1M mode and are not provider token counts. An oversized stored summary is omitted from model context. Generation also honors the memory endpoint's output ceiling.
- `summary_model_max_tokens`: the session-summary token allocation, including existing checkpoints and validation before replacing them. New summaries also have a fixed content target of 2,000 characters and a hard cap of 4,000 characters (smaller when the token allocation requires it). These content limits do not scale in 1M mode. The generation endpoint respects both its configured output ceiling and this small write budget. Existing summaries retain their larger read allocation until a valid replacement succeeds.
- `memory_context_tokens`: the request-time allowance for pending/active plan pointers and retrieved memory. Pointers are reread from SQLite on each orchestrator model call. Oversized query results are represented by complete tool results or retrieval pointers; the original database and checkpoint records remain available.
- `project_summary_working_set_max_tokens`: maximum input for one incremental project-summary update, including the previous summary or bounded database index, current priorities, summarizer prompt, and revision-ordered changed runs/facts. It must be greater than `project_summary_max_tokens`; the effective value is also capped by the memory model input window after the generation/safety reserve. This is a separate summarizer request, not the orchestrator's working conversation budget.
- `safety_margin_tokens`: the minimum main-request reserve, including generation. If the endpoint's configured output allowance is larger, the larger allowance is reserved.
- `worker_compact_fraction`: the share of a delegated worker's usable window at which its history is compacted (default 0.8; 0.3–0.95). It is a ratio, so it is not scaled in 1M mode. Lower it to make long worker tasks cheaper per call; the full history remains readable through archived tool outputs.

With default budgets, uncompressed conversation triggers summarization at 48K, targeting a recent-message window of 24K with a 64K hard working limit. Retrieved memory does not consume that trigger budget. These thresholds scale with the effective working allocation, including 1M mode. Smaller endpoint limits, larger output reservations, or larger system/tool overhead reduce the effective allocations; invalid configurations fail with an actionable error instead of sending an oversized request.

### When conversation context grows

Compaction first uses deterministic, request-only tool-result projections. If a complete reference index is too large, a small archive card is allowed only after checking the archive's hash, project/invocation access, tool identity and correspondence to the current result. `read_tool_output` recovers the full result in pages without rerunning science. User messages, pending choices and raw checkpoint records remain intact; an archive card is a preview, not new scientific evidence or a new durable fact.

Remaining conversation pressure or an older summary format triggers the session-summary model. Empty, malformed, oversized or explicitly truncated summaries receive at most one corrective attempt using the original source. Every attempt, including correction instructions, must fit the summary input budget. Transport/authentication failures receive no extra middleware retry. A failed summary does not pause a request that fits after deterministic projection. When the preserved content still exceeds a hard limit, the checkpoint is retained and the response is `paused`. An unsuccessful old-summary conversion records a private fingerprint in the checkpoint and does not repeat at each tool step of the same request. A new user request can retry; this marker never bypasses a hard working/summary limit. It contains no conversation text. Recovery diagnostics record failure categories, attempt counts and budget usage, without logging provider error bodies or full prompts. A retry of a paused chat requires a new `request_id`; reusing one returns its cached response.

### Where summaries live

Project semantic summary and session summary are separate layers. The former is stored in `memory/index.sqlite` and injected dynamically through Runtime Context; the latter is stored by LangChain middleware in the thread checkpoint and only maintains conversation continuity. The default thread ID is persisted in `project.json`; revisiting a project after a long absence or a restart uses the same thread. Session summary therefore survives browser visits. Thread persistence does not require replaying the full chat archive to the model. Project-memory snapshot reading is shared by request preparation, the UI preview and live prompt injection, with a fresh read on each model call; it never changes thread identity.

### Keeping project summaries small

Project summaries retain goals, durable constraints, unresolved work, blockers and key evidence references. Older completed narratives, parameter lists and duplicate reports are compressed first. The model receives bounded changes plus current priorities. Oversized previous prose is replaced only in that generation input with a bounded database index. One initial generation and at most one corrective attempt are allowed; persistent size, completion or required-pointer failures use the bounded index as the summary. Provider/network failures and invalid evidence citations preserve the current summary. A replacement atomically archives the full previous summary in SQLite; facts, runs, artifacts and conclusions are never deleted by compression. Use `query_project_memory(entity="summary_archive", query=...)` to find old summaries and `archive_id=...` to read one. Markdown views live in `memory/summary_archive/`; current exact records remain available through the usual fact/run queries. See the [project-summary package](../src/project_memory/summary/README.md).

The streaming API emits a `finalizing` status during post-completion memory work. It sends terminal `done` only after the result is durable and the project execution lease is released, so the browser keeps Send disabled until the next turn can be admitted.

### Keeping conversation continuity

The session summary's four sections are current focus, active constraints, open items, and retrieval references (at most three). Tool measurements, scientific explanations and completed-work inventories belong in durable outputs and project memory. The summarizer receives tool status/reference cards and tool-call identities rather than raw result data or execution arguments; user text, the immediately preceding answer and unresolved choices remain protected. If that answer alone cannot fit the working allocation, it is summarized too; its full text stays in the chat history. Small consumed outputs receive archive references too, without changing the saved raw ToolMessages. The full source prompt, current retained context and correction instructions all count toward the summary input budget. Validation never truncates an oversized summary to force it to fit. The implementation lives in [`src/agents/middleware/`](../src/agents/middleware/README.md).

These budgets do not change evidence origin. The project summary preserves separate `Tool results`, `Conversation-derived context`, `Agent reports` and `Workflow state` sections, with source-checked fact citations. Conversation extraction remains available; `source_type` is derived from stored provenance rather than selected through a configuration switch. See [fact sources](project-memory.md#fact-sources). `GBCLAW_TOOL_KEEP_RESULTS` counts recent tool results, not conversation turns.

## Model Provider

`src/core/llm/factory.py` selects a registered provider adapter by the endpoint's `provider` value. The built-in adapters live in `src/core/llm/providers/`:

- `deepseek`
- `openai` (also accepted as `openai-compatible` or `openai_compatible`)
- `anthropic` (also accepted as `claude`)

A domain extension can add a provider by declaring its adapter factory in `DomainExtension.llm_providers`; the API and CLI register it at startup, after which its name is accepted in `.key`. See [agent runtime](agent-runtime.md#llm-construction-and-thinking-adaptation).

Thinking adaptation:

- DeepSeek native or DeepSeek-compatible OpenAI endpoint: `extra_body.thinking.type` and reasoning replay for the current turn's tool-call steps.
- OpenAI official/private Chat Completions: `reasoning_effort`; compatible gateways use `ChatOpenAIWithReasoning` to preserve non-standard reasoning fields.
- OpenAI official/private Responses: `reasoning.effort` plus disclosed reasoning summaries.
- Anthropic: legacy manual or adaptive thinking is selected from the Claude model generation.

All disclosed provider shapes (`reasoning_content`, OpenAI reasoning summaries, Anthropic `thinking` blocks, and LangChain v1 `reasoning` blocks) become the same internal `thinking` stream event. The browser, CLI, transcript, and trace persistence are provider-neutral. The APIs do not disclose identical material: OpenAI exposes a summary rather than hidden chain-of-thought, and a Chat Completions gateway that returns no reasoning field cannot display one without fabricating content. When a reply carries a reasoning item with an empty summary (common at low reasoning effort, or through gateways that strip summaries) or a reasoning token count without any text, GBclaw emits `thinking_withheld` with the provider's reasoning-token count (0 when none is reported), so the trace distinguishes "did not reason" from "reasoned, nothing disclosed".

Reasoning is display output. The chat history archives it (`turns.reasoning` and trace events), and the browser and CLI show it. While a turn is in progress, its tool-call steps replay their `reasoning_content` to the provider; a final answer is sent as text alone, and the context budget counts exactly what is sent. When the next request arrives, the orchestrator removes reasoning from earlier turns in the checkpoint, together with unanswered copies of that same request left by a paused or failed attempt. Session summaries, project memory and the tool-output archive never contain reasoning. Reasoning that a provider returns as content blocks (Anthropic thinking, OpenAI Responses reasoning items) is kept as returned, because those APIs pair it with tool calls.

## Web Search Config

`src/core/settings/web_search.py` reads optional `[web_search]` settings from `.key`:

```ini
[web_search]
enabled = true
api_key = ...
model = ...
base_url = https://api.openai.com/v1
timeout = 60
```

If `[web_search]` is absent, or leaves `api_key`, `model` or `base_url` blank, and the literature endpoint uses `provider = openai` with an official OpenAI/Azure OpenAI `base_url`, web search reuses that endpoint's values.

When `is_web_search_available()` returns false, the orchestrator does not receive the `search_web` tool.

## `.env` Loading

`src/core/settings/env.py` reads environment variables from the repository-level `.env` file:

- Uses `python-dotenv`.
- Uses `override=False`, so existing process environment values are preserved.
- Loads each path at most once.

`GBCLAW_SERIAL_EXECUTION` (`src/core/settings/execution.py`) is the shared Structure/LAMMPS scheduling switch. Its default is `1`:

- `1`: admit one Structure computation or LAMMPS run at a time in its respective scheduler.
- `0`: allow independent work to run concurrently. Structure calls share the single `GBCLAW_WORKERS` process pool; LAMMPS runs share the total MPI-rank budget.

The Structure pool (`src/core/execution/process_pool.py`) defaults to 18 processes. `GBCLAW_WORKERS` overrides this shared total, capped by the physical cores available within the service's CPU affinity. Linux workers each bind to a distinct physical core (including its allowed SMT threads); native BLAS/OpenMP libraries are limited to one computation thread per process. One task can use multiple processes, and concurrent tasks share the same pool. This budget is separate from the LAMMPS MPI budget. Restart the service to apply pool changes. Physical-core binding requires Linux CPU affinity and sysfs topology support.

Two further settings choose where projects live and which domain extensions load:

- `GBCLAW_PROJECTS_ROOT`: directory holding project workspaces (default `<repo>/projects`), for example to run against an isolated copy.
- `GBCLAW_EXTENSIONS`: comma-separated `module[:attribute]` paths of trusted extension modules loaded after the built-in domains; the default attribute is `MEMORY_EXTENSION`. The older name `GBCLAW_MEMORY_EXTENSIONS` is still read when `GBCLAW_EXTENSIONS` is unset. The registry is built once per process on first use, so restart after changing it. See [extension guide](extension-guide.md).

## LAMMPS Config

`src/domains/lammps/common/settings.py` defines LAMMPS execution settings.

Environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `GBCLAW_LAMMPS_CONDA_ENV` | `GBclaw-lammps` | Conda environment that provides the LAMMPS Python bindings. |
| `GBCLAW_POTENTIALS_DIR` | `<repo>/data/potentials` | Directory containing `potentials.json` and its registered potential files. Relative overrides are anchored at the repository root. |
| `GBCLAW_LAMMPS_AGENT_MPI_RANKS` | `18` | Default MPI ranks for one agent run. |
| `GBCLAW_LAMMPS_TOTAL_MPI_RANKS` | `18` | Total MPI rank budget shared by minimization, GB-energy, shear, and NEB runs. |
| `GBCLAW_LAMMPS_NEB_REPLICAS` | `16` | Default number of LAMMPS NEB partitions/replicas. |
| `GBCLAW_LAMMPS_NEB_RANKS_PER_REPLICA` | `1` | Default MPI ranks inside each NEB replica. |
| `GBCLAW_GB_MINIMIZE_TIMEOUT_SEC` | `3600` | Wall-clock timeout for one gb_minimize run. |
| `GBCLAW_STRUCTURE_MINIMIZE_TIMEOUT_SEC` | `3600` | Wall-clock timeout for one structure_minimize run. |
| `GBCLAW_GB_ENERGY_TIMEOUT_SEC` | `14400` | Wall-clock timeout for one gb_energy run (bicrystal plus four references). |
| `GBCLAW_GB_SHEAR_TIMEOUT_SEC` | `7200` | Wall-clock timeout for one gb_shear run. |
| `GBCLAW_GB_NEB_TIMEOUT_SEC` | `14400` | Wall-clock timeout for one partitioned gb_neb run. |

Managed launches use `--map-by core --bind-to core`. The defaults are 18 MPI ranks per ordinary run and 18 ranks in total; they are fixed rather than detected from the host's CPU count. Configure both budgets for the physical cores available to the application. NEB defaults to 16 replicas with one rank each. If you reduce the total budget, also adjust the NEB topology so all replicas fit within it.

Derived values:

- Serial mode: `LAMMPS_MAX_PARALLEL_AGENTS = 1`
- Parallel mode: `LAMMPS_MAX_PARALLEL_AGENTS = TOTAL_MPI_RANKS // AGENT_MPI_RANKS`
- `GB_MINIMIZE_SCRIPT = <repo>/src/domains/lammps/minimize/engine.py`
- `STRUCTURE_MINIMIZE_SCRIPT = <repo>/src/domains/lammps/structure_minimize/engine.py`
- `GB_ENERGY_SCRIPT = <repo>/src/domains/lammps/energy/engine.py`
- `GB_SHEAR_SCRIPT = <repo>/src/domains/lammps/shear/engine.py`
- `GB_NEB_SCRIPT = <repo>/src/domains/lammps/neb/engine.py`
- `POTENTIALS_METADATA = <GBCLAW_POTENTIALS_DIR>/potentials.json`

The engine scripts run by file path inside `GBCLAW_LAMMPS_CONDA_ENV`. `src/domains/lammps/common/resources.py` reserves the actual MPI ranks used by each run. In parallel mode a launch waits until its ranks fit within `GBCLAW_LAMMPS_TOTAL_MPI_RANKS`; in serial mode it waits until no other LAMMPS run is active. For example, a total budget of 100 and a default run size of 20 permits five simultaneous default-size runs. Explicit `np` and NEB topologies are accounted by their actual sizes, and a single run may not exceed the total budget.

Relative `file` entries inside `potentials.json` are resolved only from the directory containing that metadata file. Explicit absolute potential paths are used as written, while explicit non-catalog relative paths are resolved from the repository root. Using `./name.eam` explicitly selects a repository-relative file when `name.eam` is also a catalog alias.

## LAMMPS Artifact Names

`src/domains/lammps/common/artifacts.py` centralizes LAMMPS output filenames:

- `gb_minimize.cfg`
- `gb_minimized.cfg`
- `gb_minimized.lmp`
- `top_boundary.ids`
- `bot_boundary.ids`
- `gb_minimize.lammpstrj`
- `gb_energy_summary.csv`
- `gb_energy.json` (full bicrystal/reference states, results, and convergence metadata)
- `shear_curve.csv`
- `shear_run.json` (atomically updated status, progress, complete per-point data, and CFG frame index)
- `gb_sheared.cfg`
- `gb_sheared.lmp`
- `structures/` (CFG files at loading steps selected by `output.dump_every`)
- `mep_profile.csv` (human-readable NEB minimum-energy path)
- `minimize_run.json` (all minimization stages, final metrics, and convergence warnings)
- `mep.json` (agent-readable raw NEB reaction-coordinate/energy profile)

These constants are shared by launchers, tools, and artifact indexing to avoid scattering string literals.

## GB LAMMPS Sizing Policy

`src/domains/lammps/common/defaults.py` is the shared source for LAMMPS worker sizing and quasi-static shear defaults. Its box-sizing policy is:

- `TARGET_X_NM = 20.0`
- `TARGET_Y_PER_GRAIN_NM = 10.0`
- `TARGET_Z_NM = 1.0`
- `DEFAULT_NZ_PERIODS = "1-3"`
- `MIN_Y_PER_GRAIN_NM = 10.0`
- `MIN_Z_NM = 1.0`
- `X_MISMATCH_MAX_PERCENT = 2.0`
- `X_MATCH_CANDIDATE_LIMIT = 6`
- `BOX_DIM_FACTOR = 3.0`
- `BOX_VOLUME_FACTOR = 4.0`

Both `estimate_gb_box_size()` and the config writers use these values for agent-selected geometry. Omitting repeat counts applies the policy automatically and produces integer recommendations. Explicit positive counts are measured without rounding, including fractional values. When the user explicitly confirms both triplets, `user_confirmed_size_override=true` allows an otherwise out-of-policy configuration and records every finding as a construction warning; agents must not use it for their own choices. For either x-boundary mode, `target_x_nm` is only a ranking preference. `x_pbc` changes only x padding/continuous-connection behavior and never changes repeat matching.

The same module defines the code-level NEB topology defaults `NEB_REPLICAS = 16` and `NEB_RANKS_PER_REPLICA = 1`. The `.env` values above override the writer tool defaults; each canonical NEB config records the resolved topology and validates its 16-rank (or overridden) product against the total budget.

The flat `SHEAR_*` constants in that module are the only definitions of quasi-static shear defaults. They cover the loading mode and direction, coarse/fine schedule, reversal interval, spring stiffness, per-point minimization controls, and output frequencies. The writer tool signature, validated runtime config classes, and all dynamic agent prompts import them directly, so changing a constant updates every executable consumer. Static documentation does not duplicate the values. Displacement mode stores both spring fields as `null`; spring mode multiplies the configured per-atom value by each boundary group's atom count.

## Prompt Files

Each prompt lives beside the agent or domain that uses it:

| File | Purpose |
| --- | --- |
| `src/agents/orchestrator/prompt.py` | Highest-level GBclaw identity/global rules, planning and memory contracts, and `build_orchestrator_prompt()`, which inserts each registered worker's routing section. |
| `src/agents/orchestrator/web_search_prompt.py` | Optional web-search boundaries. |
| `src/agents/planner/prompt.py` | Planning-worker contract and dynamic domain-knowledge prompt construction. |
| `src/agents/planner/domain_knowledge.md` | Maintainer-editable library of common analysis methods, reloaded for each planning call. |
| `src/agents/prompts/__init__.py` | Shared result-handoff rules appended to the orchestrator and worker prompts. |
| `src/domains/structure/prompt.py` | Structure worker tool-use rules and Sigma/plane/orientation constraints. |
| `src/domains/lammps/prompt.py` | LAMMPS worker frame, sizing, config, and run workflow. |
| `src/domains/analysis/prompt.py` | Analysis worker stress-history and structure-rendering rules. |
| `src/domains/coding/prompt.py` | Coding worker sandbox, input, and publication rules. |

Each domain `prompt.py` also defines `ORCHESTRATOR_ROUTING`, that worker's section of the orchestrator's capability routing, referenced by `WorkerDefinition.routing_guidance`. `domains/lammps/prompt.py` also defines `PLANNER_GUIDANCE`, the shear-default rule in the planning agent's prompt, referenced by `WorkerDefinition.planning_guidance`.

`src/project_memory/prompts/extraction.py` defines the constrained post-turn fact extraction contract. Project-summary prompts live with their policy, generation, validation, storage and views in `src/project_memory/summary/`; `prompt.py` defines source separation and compression priorities. Domain extraction hints and summary guidance come from registry manifests.

## LangSmith

`src/core/settings/langsmith.py` reads LangSmith settings and configures environment variables. `create_agent_graph()` calls `configure_langsmith()` by default unless `enable_tracing=False`.
