# Agent Runtime

[中文](zh/agent-runtime.md) · [Architecture and design](architecture.md) · [Project memory](project-memory.md) · [Extension guide](extension-guide.md)

GBClaw keeps one conversation with the researcher while delegating scientific work to specialist agents. This guide explains how a request moves through that system, what each agent owns, and how results return with evidence. The later sections describe the implementation under `src/agents/`.

## Why coordination and execution are separate

A grain-boundary investigation rarely follows one fixed script. Screening may reveal an unexpected structure, shear calculations may motivate a new analysis, and a researcher may request a different hypothesis. The orchestrator keeps track of the research question and decides the next task; specialists receive the inputs, tools, and prior evidence needed to carry it out.

This division gives each worker a smaller, more focused context and allows independent cases to proceed concurrently within the configured resource limits. Dependencies still matter: analysis of a trajectory must wait for that trajectory, and an NEB calculation needs explicitly identified endpoint structures. Delegation is therefore a coordination boundary, not a promise that every task runs in parallel.

Workers are temporary, but their research outputs are durable. A later worker retrieves run records and artifacts by stable IDs instead of inheriting an earlier worker's entire conversation. The cost is an explicit handoff: every result must say what happened, where its evidence is stored, and what remains unresolved. A worker's confident wording alone cannot mark a scientific task as successfully completed.

The planning agent supports researcher review. A proposed plan is stored separately from an approved plan, and the runtime checks approval before executing planned scientific work. This makes changes to the investigation deliberate while still allowing bounded direct tasks through `/noplan`.

## Follow one research task

For a request such as “compare the shear response of these two relaxed boundaries,” the typical flow is:

1. The orchestrator reads the current goal, plan state, and relevant project memory. It resolves which completed structures the user means instead of reconstructing them from chat prose.
2. When a plan is needed, the planning agent proposes the calculation and analysis steps. The orchestrator presents that draft and waits for the researcher's decision.
3. Each delegated worker receives a focused task plus a bounded set of existing facts and run/artifact references. It can retrieve more detail through memory tools.
4. Scientific tools perform the work and save their inputs, outputs, and run status. The shared worker lifecycle checks the returned evidence and records the worker's conclusion and next action.
5. The orchestrator compares the findings, explains limitations or open questions, and responds to the researcher. Completed-turn memory processing then updates the project's compact working summary.

For a user-facing walkthrough, start with [Getting started](getting-started.md). To add a specialist, follow [Extension guide](extension-guide.md); it explains the registration contract used by the runtime below.

## Key Files

- `src/agents/orchestrator/graph.py`: creates the user-facing orchestrator agent.
- `src/agents/orchestrator/prompt.py`: `build_orchestrator_prompt()`, the orchestrator's global rules plus each registered worker's routing section.
- `src/agents/graph.py`: shared `create_agent_graph()` factory for all agents.
- `src/agents/invoke.py`: synchronous invocation, run config construction, and final reply extraction.
- `src/agents/middleware/`: orchestrator context partition and session-summary compaction, single-pool compaction for disposable workers (`WorkerCompactionMiddleware`), project-summary and plan-state injection, turn-start cleanup, and cancellation checks; budgets are resolved in `src/core/settings/runtime_context.py`.
- `src/agents/streaming/events.py`: translates LangGraph streams into chat stream events for the Web UI and CLI.
- `src/core/stream_events.py`: the stream event schema shared by every producer and consumer.
- `src/interfaces/turn.py`: the turn pipeline shared by API and CLI chat.
- `src/interfaces/recording.py`: shared turn recording for API and CLI chat.
- `src/interfaces/api/routers/chat.py`: API execution lifecycle for streaming and non-streaming requests.
- `src/project_workspace/chat/trace.py`: in-memory trace coalescing and boundary persistence.
- `src/agents/workers/spec.py`: `WorkerSpec`, the declarative per-task worker graph and its run loop.
- `src/agents/workers/result.py`: structured result format returned by workers to the orchestrator.
- `src/agents/workers/lifecycle.py`: registered delegation, memory handoff, plan tracking, and durable worker conclusions.
- `src/agents/planner/`: the planning worker, its `delegate_plan` tool, plan tools, and plan gates.
- `src/domains/<domain>/agent.py` and `delegate.py`: Structure, LAMMPS, analysis, and disposable coding workers and the delegate tools available to the orchestrator.
- `src/core/llm/factory.py`: builds LangChain chat models from `LLMEndpoint` through registered provider adapters.
- `src/core/execution/`: execution sessions, cancellation, resource slots, and supervised subprocesses.

## Agent Graph Creation

`create_agent_graph()` is the shared entry point:

- Calls `build_llm(endpoint)` to create the underlying model.
- Uses `ProjectCheckpointRouter` when `projects_root` is provided, so checkpoints are written to the corresponding project SQLite file.
- Uses an in-memory `MemorySaver` when neither a project root nor an explicit checkpointer is provided.
- Inside a registered worker invocation, adds that worker's registered memory tools and appends its memory handoff to the system prompt.
- Installs `ExecutionCancellationMiddleware` and `ModelToolOutputMiddleware`, then the caller's `middleware_factory` (the orchestrator's partitioned context middleware) or, by default, `build_worker_context_middleware()`, which adds `WorkerCompactionMiddleware`.
- Calls LangChain `create_agent()` with tools, system prompt, middleware, and checkpointer.

As a result, the orchestrator is checkpoint-persistent in API mode. Worker graphs are temporary by default unless an external persistent checkpointer is supplied.

## Orchestrator

`create_orchestrator_agent()` performs these steps:

1. Validates the call shapes of every registered extension hook (publication validators, fact and model-view builders, tool/query providers, orchestrator-tool and worker factories, routing guidance, startup/shutdown and LLM-provider hooks) and resolves the `orchestrator`, `literature`, and `memory` endpoints through `ModelRouter.for_role()`. Registered workers resolve their declared endpoint roles during delegate assembly.
2. Checks whether web search can be enabled from the `literature` endpoint.
3. Assembles the tools domains declare for it (`DomainExtension.orchestrator_tools`: coding environment discovery before the host tools, potential discovery after them), optional search, and registered worker delegates (`build_registered_worker_tools()`). When a `ProjectStore` is available, it also adds the registered memory and project-plan tools. The built-in tool set includes:
   - `list_coding_environments`
   - `query_project_memory`
   - `search_project_memory`
   - `read_agent_conclusion`
   - `get_task_state`
   - `read_tool_output`
   - `list_run_artifacts`
   - `read_run_artifact`
   - `report_artifact_usage`
   - `get_project_plan`
   - `get_project_plan_draft`
   - `approve_project_plan_draft`
   - `reject_project_plan_draft`
   - `update_project_plan_step`
   - optional `search_web`
   - `list_available_potentials`
   - `delegate_plan`
   - `delegate_csl`
   - `delegate_lammps`
   - `delegate_analysis`
   - `delegate_coding`
4. Builds the system prompt with `build_orchestrator_prompt()`, with `WEB_SEARCH_PROMPT` appended when web search is available. Its capability-routing section joins each registered worker's `WorkerDefinition.routing_guidance` text in registry order; the built-in domains keep that text as `ORCHESTRATOR_ROUTING` in `src/domains/<domain>/prompt.py`.

The orchestrator is the only agent that should speak directly to the user. Domain calculations and long-running work are delegated to workers.

## Worker Agents

Each specialist declares a `WorkerSpec` (`src/agents/workers/spec.py`): its agent name, label, status message, and system prompt. The prompt may be a callable, so the planner reloads its domain knowledge for each task. Unless a caller passes explicit tools or the spec declares its own `tools` callable, the worker's business tools come from the registry's tool providers for that agent (`DomainExtension.tool_providers`), so an extension can add tools to a built-in worker. `create_*_agent()` is the spec's `create_graph()`, and `invoke_*_agent()` is its `invoke()`:

- `create_plan_agent()` (`src/agents/planner/agent.py`): dynamically loaded domain knowledge plus read-only semantic and artifact tools; no scientific execution tools.
- `create_structure_agent()` (`src/domains/structure/agent.py`): CSL/GB domain tools plus semantic search/query and artifact list/read.
- `create_lammps_agent()` (`src/domains/lammps/agent.py`): domain tools plus semantic search/query and artifact list/read.
- `create_analysis_agent()` (`src/domains/analysis/agent.py`): domain tools plus semantic search/query and artifact list/read.
- `create_coding_agent()` (`src/domains/coding/agent.py`): Deep Agents filesystem/execution tools backed by an isolated, disposable sandbox workspace, plus read-only semantic tools, `request_input_artifacts` for files the delegate's `input_grants` authorize, and controlled publication through `publish_coding_result`. The coding worker keeps its own invoker, bounded by the task deadline and by `GBCLAW_CODING_RECURSION_LIMIT` graph steps (default 200), and reuses the same event and reply handling. `StepBudgetMiddleware` adds a request-only notice when about eight tool rounds remain; if the worker stops without publishing and was not cancelled or waiting for more input scope, the host saves the declared `expected_outputs` that pass their contracts (`publication.mode="host_salvage"`) and reports the delegation as `partial` (see [Coding agent](coding-agent.md)).

Each `invoke_*_agent()` function:

1. Rejects an empty task.
2. Creates a worker graph.
3. Generates a temporary `thread_id`.
4. Places `project_id` and optional `chat_thread_id` into config metadata/configurable.
5. Uses `iter_worker_tool_events()` when `on_event` is provided, so sub-tool events can stream back to the UI.
6. Extracts the final reply from messages.
7. Extracts the tool trace with `extract_tool_trace_from_messages()`.
8. Returns `WorkerResult.ok(...)` or `WorkerResult.fail(...)`.

`WorkerResult` fields:

- `status`: `ok` or `error`
- `summary`: worker text summary for the orchestrator
- `data`: additional structured data
- `artifacts`: artifact paths
- `trace`: internal tool-call trace used for tracking; omitted from model-facing handoffs
- `error`: error text
- `worker_outcome`: optional validated `WorkerOutcome` (`completed`, `reused`, `partial`, `failed`, `awaiting_user`, `cancelled` or `interrupted`, with evidence references and a next action); the serialized handoff always carries one, derived from the result when absent

All delegates serialize through a shared compact handoff: conclusions, necessary metrics, deduplicated run/status/manifest references, and required follow-ups or user decisions. Repeated tasks and tool histories are not sent back to the orchestrator. Plan tracking reads the original worker result before this projection. Full scientific results remain in run memory and artifacts.

If the graph raises (a context limit, the step limit, or a provider error), `WorkerSpec.invoke()` returns `WorkerResult.fail(...)` with the tool trace read back from its in-memory checkpoint, so runs it had already completed are verified and the outcome is `partial` rather than `failed`. A context-limit failure, whether it comes from GBclaw's own guard, a `ContextOverflowError`, or a provider 400/413 that names the context window (`ContextCompactionError.from_exception()`), adds `data.context_failure` with the code, a host-written message, safe diagnostics (never the provider's response text), `recoverable=true`, `checkpoint_preserved=false`, and an action to re-delegate a narrower task that reuses the completed run IDs; the action is also copied to `data.next_action`. The worker's conversation itself cannot be resumed; only the orchestrator's chat thread keeps a persistent checkpoint. The coding worker's own invoker reads back no trace: a run it already published is returned as success with `post_publication_warning`; otherwise the host salvages declared outputs as `partial` before reporting the failure with the same `context_failure`.

Workers do not use the orchestrator's partitioned context budgets. `WorkerCompactionMiddleware` gives each disposable worker one pool (`resolve_worker_context_budget`): the smaller of the endpoint window (`.key` `max_input_tokens`, the model profile, or the default) and `[runtime_context] total_context_tokens` (256K, ×4 with `GBCLAW_CONTEXT_1M=1`), minus the larger of the endpoint's output limit and the scaled `safety_margin_tokens`, minus the static system prompt and tool schemas. A pool below 16,384 tokens fails the delegation, and `[runtime_context] enabled = false` turns worker compaction off. Once the conversation exceeds `worker_compact_fraction` of the pool (default 0.8, range 0.3–0.95, not scaled in 1M mode), the task message and the newest complete tool-call blocks (about a quarter of the pool) stay verbatim and the rest becomes one summary with `COMPLETED`, `IN PROGRESS`, `OPEN ITEMS` and `LOOK-BACK REFERENCES` sections, written by the worker's own endpoint with thinking off: finished work keeps only its results and references, work in progress keeps its parameters, last observed state and next action. The in-context summary may use as many characters as a sixth of the pool's tokens (at least 4,096), and the summary model may write twice that: when the summary is too long, the complete COMPLETED section is archived as a `compacted_worker_results` tool output, only its leading lines stay in context, and a `read_tool_output` pointer replaces the rest. The other sections must fit on their own, and without an active project nothing can be archived, so an oversized summary is rejected. An invalid summary gets one corrective attempt; if summarization still fails while the request fits the pool, the worker continues uncompacted and retries at the next step. Consumed tool results are archived first so the worker can re-read them with `read_tool_output`, and their trace records travel on the summary message (`compacted_tool_trace`) so completion checks still see every finished call. Counting uses GBclaw's tokenizers with 2,000 tokens per inline media block. A provider overflow (`ContextOverflowError`, or a 400/413 naming the context window) compacts the request in place and retries once; a block that cannot be shrunk fails the delegation with `worker_block_exceeds_context`, and a retry that still overflows fails with `worker_context_exhausted`. The coding worker keeps Deep Agents' summarizer and its history offload to the sandbox backend (`src/domains/coding/middleware.py`) with the same trigger, keep target, counter, summary prompt and COMPLETED spill; it also truncates old tool-call arguments from half the trigger, keeps a malformed summary as Deep Agents wrote it instead of correcting it, and falls back to Deep Agents' profile defaults when runtime context is disabled.

The shared agent factory and the coding worker also project tool results before each model request. This covers workflow JSON, plain-text tools, and older checkpoint results. Full tool outputs are archived in the project's runtime store before details are omitted; the returned `full_output.output_id` can be read with `read_tool_output`. Explicit memory/artifact reads use bounded pages. This projection does not rewrite raw tool events or scientific reports. New tools inherit the same boundary; tool-specific conclusion fields can be declared in the central memory registry.

## Delegate Tools

`delegate_plan`, `delegate_csl`, `delegate_lammps`, `delegate_analysis`, and `delegate_coding` are orchestrator tools, not public API routes.

Each `WorkerDefinition` names a delegate factory (`src/domains/<domain>/delegate.py`, or `src/agents/planner/delegate.py` for planning). `build_registered_worker_tools()` builds these tools in registry order and wraps each one with `create_worker_delegate()` from `src/agents/workers/lifecycle.py`. Domain delegate functions specify the scientific task and call their worker. The shared lifecycle, together with domain-specific resource handling, performs the surrounding work:

- Reading `project_id` from the LangGraph run config.
- Building a bounded, task-specific memory handoff before worker/run creation. The handoff contains active facts, relevant completed runs, and stable artifact IDs; non-terminal runs are excluded.
- Injecting structured, read-only semantic/artifact tools into domain workers; coding receives semantic tools plus only the exact artifact snapshots declared in that call's `source_artifacts`, and may request further snapshots with `request_input_artifacts` only within the call's `input_grants`.
- Reserving each coding call's `compute_slots`/`mpi_ranks` from the process-wide coding pool before sandbox creation and releasing the lease on every terminal path.
- Emitting the worker events listed in `WORKER_FORWARDED_EVENTS` (`status`, `thinking`, `thinking_withheld`, `tool_start`, `tool_end`, `agent_result_delta`, and `agent_result_reset`) through `get_stream_writer()`, tagged with the delegate tool-call `parent_id` plus display-only `parent_name` (the delegate tool name).
- Calling the corresponding `invoke_*_agent()`.
- Accepting a structured `WorkerResult`, `RunToolResult`, or `WorkerOutcome`, checking completion evidence, recording the outcome, and returning a compact serialized handoff to the orchestrator.

### Task context and evidence

Delegate reasoning and tool events are retained in permanent Chat history. The shared worker lifecycle separately persists an agent conclusion and task cursor, with report provenance; it does not copy the complete trace into semantic memory. Domain tools publish scientific runs, artifacts and result facts through their own registered publication contracts.

Every recalled fact includes `source_type`, `evidence_kind`, and its source turn/run references. The handoff distinguishes tool-published results (`tool_result`), extracted conversational context (`conversation`), worker reports (`agent_report`) and plans/task cursors (`workflow_state`). A `completed_run` interpretation still has conversation origin; a worker report's references are supporting pointers, not independent proof of scientific success. Workers may query `source_type="tool_result"` when the task requires computed evidence.

The handoff is capped at 12,000 characters and is appended to the worker system prompt as untrusted project data. It tells a stateless worker which completed evidence already exists and which exact IDs to use for follow-up reads. The orchestrator-facing `gb_crystallography` conclusion is intentionally excluded from automatic worker handoffs; the orchestrator reads it through the project summary or an exact memory query and decides what, if anything, belongs in a delegated task. If no relevant evidence exists, the worker retains its normal memory search/query tools.

CSL result artifacts additionally record normalized tool input and an input fingerprint; an identical future CSL call returns the completed artifact instead of recomputing it. Cancellation tags newly created runs with the owning chat thread and transitions matching running rows to `cancelled` when that turn is stopped or disconnected.

### Plans, approval, and revision

`delegate_plan` is used only when deeper method selection or dependency analysis is useful. It returns advice to the orchestrator, which remains responsible for executing the selected scientific delegates. Its worker reads `src/agents/planner/domain_knowledge.md` when each graph is created. New-plan requests receive `create_project_plan_draft`; calls with `existing_plan_id` additionally receive exact plan read plus `revise_project_plan_draft`. A shared request-scoped guard allows exactly one successful draft write. Those deterministic tools store `plan_draft:<draft_id>` plus `project:pending_plan`; they do not activate or execute it. The delegate factory accepts explicit `extra_tools`, reserved for a future external or server-side search integration; none is enabled by default.

After delegation the orchestrator must summarize the draft, ask the user to approve/reject/change it, and end the turn. `PlanApprovalGateMiddleware` rereads the pending pointer at every tool call. It blocks scientific delegates, step transitions, and replacement planning until the draft is decided; it also verifies that approve/reject is authorized by the latest user message. On approval, active-plan/revision facts, draft decision, and pending-pointer retraction commit in one SQLite transaction. Rejection only closes the pending draft. The next planner call turns user revision guidance into a new draft that requires another approval.

Planned CSL/LAMMPS/analysis/coding calls carry `plan_id` and `step_id`. Their delegate wrapper validates the recorded capability and dependencies, changes the step to `running` before worker execution, then writes `completed` or `failed` with timestamps, attempts, result summary, and discovered run IDs, or `blocked` with a reason when the worker awaits a user decision or a near-CSL cell selection is unfinished. The full plan and active pointer are updated atomically with optimistic fact versions. Orchestrator/memory/user-input/search steps use `update_project_plan_step`; cross-turn resume uses `get_project_plan` rather than inferring progress from Chat or a possibly stale summary.

For a user-requested plan change, the orchestrator reads the exact plan and calls `delegate_plan` with the complete modification request and `existing_plan_id`. The planning worker receives read-only memory/artifact tools, exact `get_project_plan`, planner-only `revise_project_plan_draft`, and `create_project_plan_draft`; it performs exactly one successful draft write. A same-objective change proposes a structural revision, while a materially different objective proposes a new plan. Neither affects the active plan until separately approved. The orchestrator never receives the structural draft-write tools directly. Structural revision is independent of ordinary progress-driven memory versions.

Pending work refreshes root `plan-draft.md` and audited draft JSON; approval/rejection removes the root pending view, and approved updates refresh `plan.md` plus current/revision JSON. These files are derived from SQLite and never read back as execution state.

The compact `project:pending_plan` pointer has the highest Runtime Context priority, followed by `project:active_plan`, then the generated working summary. Dynamic prompt middleware rereads both pointers from SQLite on every orchestrator model call, so approval and progress changes between tool calls are immediately visible. Full plans/drafts remain available through exact tools.

### Choosing whether to plan

Planning is controlled per turn by `auto`, `force`, or `skip` (`PlanningMode` in `src/planning/mode.py`; the chat commands are declared in `src/interfaces/commands.py`). `/plan` forces draft creation and ends the turn for approval. `/noplan` removes durable planning tools for the turn and permits direct scientific delegation; it does not bypass worker validation. A pending draft superseded by `/noplan` is rejected with an audit reason before context preparation, while any active plan is left untouched.

### Cancellation and execution sessions

Each turn registers one execution session (`src/core/execution/sessions.py`) under its project, thread, and execution owner, and runs inside it. Every delegate passes the current chat thread id to its worker as `chat_thread_id`. Tools resolve the active session key from the run config with `runtime_session_key()` and launch LAMMPS, analysis and rendering, dichromatic-map, and sandboxed coding subprocesses under it through `supervise()`/`run_cancellable()` (`src/core/execution/process.py`) or explicit process registration. A stop request or disconnect cancels the session: registered process trees are terminated, waits for LAMMPS ranks or coding slots end, and `ExecutionCancellationMiddleware` stops further model and tool calls. Server shutdown cancels every session with `cancel_all_sessions()`.

## Streaming Events

`src/agents/streaming/events.py` translates LangGraph `updates`, `messages`, and `custom` stream modes into UI events. The event vocabulary is declared once in `src/core/stream_events.py`: `StreamEventType`, one `TypedDict` per event listing its fields, `validate_stream_event()`, and `WORKER_FORWARDED_EVENTS`. The Web UI imports the same type names from `web/assets/js/stream_events.js`, which `PYTHONPATH=src python -m core.stream_events` generates from the repository root (`--check` reports drift).

Main event types:

- `status`: current status text.
- `thinking`: reasoning/thinking delta.
- `thinking_withheld`: the model reasoned but the provider disclosed no text; carries `reasoning_tokens` (0 when unknown). The trace stores it as a `reasoning_withheld` record that never joins the turn's reasoning text.
- `tool_start`: tool start, including name, label, and truncated client input.
- `tool_end`: tool end, including truncated client output.
- `tool_progress`: detected tool-call chunk during streaming.
- `agent_result_delta`: final-answer text streamed by a delegated worker.
- `agent_result_reset`: discard provisional worker text when that model turn calls a tool.
- `final_start`: final answer begins.
- `token`: final answer token.
- `reset_stream`: temporary text was interrupted by a tool call and the UI should reset.
- `done`: full reply and thread id.
- `error`: invalid empty reply or execution error.

`tool_label()` resolves display names from the registry: a delegate shows its worker's display name, other tools use `ToolCapabilitySpec.display_name` or the domain's `DomainExtension.tool_labels`, and an undeclared tool shows its raw name. For example, `delegate_plan` is displayed as `Planning agent`, `delegate_lammps` as `LAMMPS agent`, and `run_gb_minimize` as `GB minimization`.

The API connects these events to the browser through a bounded queue with capacity one. This keeps backpressure close to the model stream instead of allowing a large server-side batch to accumulate. `ChatTraceRecorder` keeps one open reasoning segment per parent and label, so interleaved parallel workers append to their own segment. A worker's segment is sealed at that worker's tool boundary; all remaining segments are sealed at final-answer/error boundaries. SQLite remains authoritative; the human-readable `chat/transcript.md` is regenerated atomically when a turn finishes, and on file-browser access when its export revision is stale. API and CLI run each turn through `run_turn()` in `src/interfaces/turn.py`, which prepares the session, streams the graph, records the turn and trace through `src/interfaces/recording.py`, and schedules post-completion memory work; only input parsing and event rendering remain transport-specific.

Tool events carry private, untruncated input/output fields inside the process. `ChatTraceRecorder` persists those complete values to SQLite, while the SSE and CLI boundaries remove private fields and retain only bounded previews. Historical trace API responses also project stored tool I/O back to bounded previews, so browser rendering and network payload size remain independent from the complete permanent transcript. The preview bounds default to 280 characters for tool input and 400 characters for tool output; set `GBCLAW_MAX_TOOL_INPUT` and `GBCLAW_MAX_TOOL_OUTPUT` in `.env` (a shell variable of the same name takes precedence) and restart to override them without changing permanent transcript contents.

Delegate tools are projected differently because their raw output is a large `WorkerResult` or run-result JSON envelope. Interactive clients receive the delegated `task` and the worker's `summary`, not a syntactically truncated JSON prefix. Their independent defaults are 1200 input characters and 8000 output characters, configurable with `GBCLAW_MAX_DELEGATE_INPUT` and `GBCLAW_MAX_DELEGATE_OUTPUT`; the complete envelopes remain stored in SQLite.

Nested worker events identify their delegate with the actual LangChain tool-call ID in `parent_id`; `parent_name` is display metadata only. Tool start/end records persist that call ID as nullable `tool_call_id`, allowing same-name delegate calls to be paired independently in live and historical traces. Name-based matching remains only as a read fallback for traces created before these fields existed.

## LLM Construction and Thinking Adaptation

`src/core/llm/factory.py` builds each model through the `ProviderAdapter` registered for the endpoint's `provider` (`src/core/llm/providers/base.py`: `name`, `aliases`, `apply_thinking()`, `build()`, and optionally `token_counter()` and `reasons_when_disabled()`). The built-in adapters are:

- `deepseek`
- `openai` (aliases `openai-compatible`, `openai_compatible`)
- `anthropic` (alias `claude`)

`register_provider()` adds another adapter. A domain extension declares one in `DomainExtension.llm_providers` as a `"module:attr"` zero-argument factory; the API and CLI register these at startup (`start_extensions()` in `src/interfaces/lifecycle.py`) before the orchestrator is built.

The `.key` fields `thinking` and `reasoning_effort` are first normalized into `ThinkingSettings`, then mapped to each provider:

- DeepSeek or DeepSeek-compatible OpenAI endpoint: writes `extra_body.thinking.type`, maps the common effort ladder, preserves streamed `reasoning_content`, and replays it on tool-call turns.
- OpenAI Chat Completions: uses `reasoning_effort`; compatible gateways may additionally expose non-standard reasoning fields.
- OpenAI Responses: uses `reasoning.effort`, requests `reasoning.summary=auto`, and normalizes streamed `reasoning.summary[]` blocks for the UI.
- Non-official OpenAI-compatible gateway: uses `ChatOpenAIWithReasoning` to preserve `reasoning_content` / `reasoning_details`, allowing the streaming UI to show thinking when those fields exist and preserving required reasoning across a tool-call loop.
- Anthropic: automatically selects manual `budget_tokens` for Claude 4.5 and earlier or adaptive thinking plus effort for Claude 4.6 and later. Anthropic output uses LangChain v1 blocks, while signed thinking blocks remain round-trippable during tool use.

Every disclosed form is normalized to the same `{type: "thinking", text: ...}` application event; when a provider reports reasoning (a reasoning block or a reasoning-token count) without any text, the stream emits `{type: "thinking_withheld", reasoning_tokens: ...}` instead of fabricating text. The stream and persistence layers therefore do not branch on provider.

### Memory updates after a completed turn

Fact extraction and summary generation are auxiliary post-completion calls. They clone the configured `memory` endpoint with `dataclasses.replace()`, set `thinking="disabled"`, and clear `reasoning_effort`. Fact extraction runs in a request window of at most 64K tokens, from which its output cap (8,192 tokens) and a 1,024-token margin are reserved; semantic-summary generation writes up to the smallest of `[runtime_context] project_summary_max_tokens`, the endpoint's output limit, and 24,000 tokens (two per character of the 12,000-character cap). This leaves the configured endpoint object unchanged. A model that cannot stop reasoning (GPT-6, the o-series, always-adaptive Claude) gets a 4,096-token allowance above that answer-sized cap, within the endpoint's own limit. DeepSeek receives an explicit disabled thinking body; OpenAI models receive the lowest effort their generation accepts (`minimal` for GPT-5, `none` for GPT-5.1–5.x, `low` for GPT-6 and the o-series, and no level for an unrecognized deployment name); Anthropic receives explicit disabled thinking (except always-thinking Claude families, where low-effort omitted-display mode is the nearest valid behavior). These calls run after the user-facing assistant turn. Before a turn starts, `prepare_project_session()` in `src/interfaces/turn.py` coordinates orphan-run cleanup, checkpoint repair, interrupted chat finalization and publication recovery before reading the project-memory snapshot. Recovery does not call an LLM or rerun scientific tools.

Semantic-summary generation is incremental: each call combines the previous bounded working summary with a token-bounded, revision-ordered batch of coalesced run/fact changes. It does not serialize the complete semantic database into the model request.

The project summary keeps `Tool results`, `Conversation-derived context`, `Agent reports` and `Workflow state` as separate sections. The host groups input facts by derived source and checks that each fact citation remains in its matching section. Conversation extraction and session summarization remain enabled; neither may relabel conversational interpretations as tool results. See [fact sources](project-memory.md#fact-sources) for filtering and provenance semantics.

### Keeping the working conversation bounded

The orchestrator also enables short-term context middleware. Recoverable old tool details become reference cards only in the current model request, based on working-conversation usage rather than retrieved memory; once messages reach the configured budget, the non-thinking memory endpoint produces a structured session summary that persistently updates the checkpoint. Project semantic summary is injected through a dynamic system prompt and is never stored as a Human/AI message. The SSE layer suppresses internal summarization output and emits only one context-compression status event. Ordinary per-task workers use the same tool-result projection but not this session compaction: within one invocation they compact through the single-pool `WorkerCompactionMiddleware` described under Worker Agents, and their graph remains disposable between invocations. Their final conclusions and execution cursors persist independently in project memory.

These token allocations are ceilings, not desired summary lengths. Project summaries target 6,000 characters and are capped at 12,000; session summaries target 2,000 and are capped at 4,000. Detailed evidence remains available through exact queries and artifact reads.

The default total context budget is 256K tokens (K = 1024), divided into project summary (up to 32K), session summary (up to 32K), recent uncompressed conversation (up to 64K), live plan/retrieval memory (up to 64K), and a 32K generation/safety reserve. The remaining 32K accommodates system prompts and tool schemas.

Working conversation triggers compaction at its 48K soft threshold, targeting 24K of retained recent messages with a 64K ceiling; retrieved memory does not trigger that compaction. Summary validation and request-time budget enforcement keep the categories bounded, without truncating the underlying semantic database or checkpoint tool results. The recent-message retention target is soft: if a complete tool-call/result block exceeds that target but still fits the working limit, it is retained whole. An input that cannot safely fit yields a recoverable pause with saved-state guidance, without deleting outputs or automatically replaying calculations.

Context budgets count tokens locally with the model family's vocabulary, bundled in [`src/core/llm/providers/vocab/`](../src/core/llm/providers/vocab/README.md): tiktoken's o200k/cl100k for GPT names, also inside gateway deployment aliases, and the DeepSeek-V3 tokenizer for DeepSeek models, whose counts equal the input tokens the API reports. A 5% margin covers per-message and tool framing, and tool schemas are counted in the form a request sends them. Models without a bundled vocabulary, including Claude, count one token per character.

Set `GBCLAW_CONTEXT_1M=1` in `.env` and restart to multiply runtime token budgets by four. Leave numeric budgets undefined unless you intend to override the defaults, which automatically follow this switch. Explicit endpoint `max_input_tokens` limits are still honored, and a larger generation allowance or system/tool footprint reduces the effective input allocations. Full settings are documented in [configuration](configuration.md#runtime-context-and-working-summary).
