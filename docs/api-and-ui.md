# API and Web UI Entry Points

Use this guide to understand a chat request from browser input to a saved project result, or to build another client for the same backend. `src/interfaces/api/` owns HTTP/WebSocket endpoints; `web/` presents the conversation, plans, and artifacts. For launch commands, see [Getting started](getting-started.md).

## Design rationale

A research task can outlive a browser interaction. The backend therefore owns execution and persistent records, while the browser displays progress and sends user decisions. Reloading a page reads those records again; it does not recreate the research state from the visible messages.

Three identifiers answer different questions:

| Identifier | Meaning | Lifetime |
| --- | --- | --- |
| `project_id` | Which research workspace owns the memory, plans, and artifacts? | Across visits and backend restarts. |
| `thread_id` | Which conversation checkpoint should the agent continue? | Persisted; reopening the same project normally resumes its default thread. |
| `request_id` | Has this particular chat request already started or finished? | Stored with request admission and its terminal result. |

Frontend ownership and execution ownership are separate. A live browser or CLI session reserves the project so that two clients cannot make conflicting changes. Each admitted turn then holds an execution lock until its work and cleanup finish. This allows independent projects to run while keeping writes within one project ordered.

The last answer token is not the end of a request. The backend still needs to save the answer, finish post-completion memory work, and release execution ownership. Streaming uses `phase="finalizing"` for this interval and emits terminal `done` only when the next turn can be admitted. This distinction also explains why an HTTP 409 can mean a project is still occupied even though an answer is already visible.

Chat history, research memory, and artifacts are persisted separately so that the UI can replay a conversation without treating everything said in it as a computed result. See [Architecture and design](architecture.md) and [Project memory](project-memory.md) for the reasoning behind that separation.

## FastAPI App

Entry point:

```text
src/interfaces/api/app.py
```

The same package holds `deps.py` (route access to the services built at startup in `app.state`; HTTP 503 until they exist), `ownership.py` (the project-write token check described under [Project occupancy](#project-occupancy)), `schemas.py` (Pydantic models), and `routers/` (`chat.py`, `projects.py`, `registry.py`).

Startup lifespan initializes:

- `ProjectStore(get_default_projects_root())`
- `ModelRouter(load_llm_config())`
- `memory_endpoint = model_router.for_role("memory")`
- `runtime_context_config = load_runtime_context_config()`
- `start_extensions()` from `src/interfaces/lifecycle.py`: registers the LLM providers that extensions declare, then runs their `startup` hooks once per process. The Structure extension's hook warms up the shared process pool used by the CSL tools.
- `agent = create_orchestrator_agent(model_router, project_store=..., runtime_context_config=...)`
- a background task (`_memory_retry_loop`) that periodically drains queued memory retry jobs with `drain_ready_retry_jobs()`, polling faster while work remains

Shutdown lifespan:

- Stops the memory retry task (waits up to 5 s, then cancels it).
- Cancels every execution session and terminates its registered processes, including LAMMPS jobs (`cancel_all_sessions()`).
- Closes SQLite connections when the checkpointer is `ProjectCheckpointRouter`.
- Runs extension `shutdown` hooks in reverse order (`stop_extensions()`); the Structure extension shuts down the shared process pool.

FastAPI mounts:

- `/api/...` chat routes (`routers/chat.py`)
- `/api/projects/...` project routes (`routers/projects.py`)
- `/api/registry` (`routers/registry.py`)
- routers declared by domain extensions in `DomainExtension.routers`, resolved in registry order by `extension_routers()` in `src/interfaces/lifecycle.py`; the Structure extension contributes the [DichromaticMap routes](#dichromaticmap-api-and-widget)
- `/brand-assets/` shared artwork from `docs/assets/`
- `/` static `web/` frontend
- `/favicon.ico` default project icon, served as SVG

The README, documentation, and Web UI share the [artwork in `docs/assets/`](assets/README.md). The header and tab icon use the square SVG and switch to its light-foreground variant when the application uses a dark theme. Deployments must include `docs/assets/` alongside `web/`; the server mounts only that asset directory, not the complete documentation tree.

## Project occupancy

For a custom client, the order is: claim the project, keep the connection open, and attach its token to write requests. A `thread_id` identifies conversation state; it does not grant permission to write to the project.

A browser tab claims a project with `WS /api/projects/{id}/connection` before loading it. The first message is `{type: "claimed", token, project}`; a competing tab receives `{type: "error", detail}` followed by close code 1008. The connection holds a project-wide file lock for its entire lifetime, including idle and background periods. Each tab obtains its own server-issued token; tokens are not stored in browser storage.

Keep that connection open and send `X-GBclaw-Project-Token: <token>` on chat, stop, project deletion and all project-scoped DichromaticMap POST requests. Missing, old or foreign tokens return HTTP 409 before changing project state. The check is the router dependency `protect_project_write` in `src/interfaces/api/ownership.py`, shared by the chat, project, and DichromaticMap routers. Listing, creation, read-only file/history/memory APIs and previews without a project do not need this header. CLI sessions also hold occupancy until exit or project switch. Different projects can operate independently; different threads in one project share one execution lock.

Switching first claims the destination, then releases the previous project; if the claim fails, the page keeps the previous project. Closing the page releases its WebSocket; transport ping/pong detects broken connections. Already admitted writes and executing turns continue to prevent takeover until cleanup finishes. A lost connection disables the composer and aborts the stream of any running turn, which the server treats as a disconnect and cancels; reopen the project using **Switch**. After a browser cache restore, the page reacquires its project before writing. Switching and deletion are disabled during chat.

`start_web.sh` listens on `127.0.0.1:9999` by default (`API_HOST`, `API_PORT`) and selects Uvicorn's `websockets` transport with a 20-second ping interval and timeout. A reverse proxy must forward WebSocket upgrades as well as SSE. External HTTP clients must also claim and keep a connection; `thread_id` alone does not grant write access.

## Chat API

Defined in `src/interfaces/api/routers/chat.py`.

### Request identity and retries

Each turn may provide a unique `request_id` (1–128 letters, digits, `_` or `-`). The browser generates one ID and uses it for streaming, classic fallback and stop. The server generates an ID when omitted and returns it in the first SSE status or classic response. Admission and results persist in `chat/history.sqlite`: the same ID with the same normalized message, planning mode and thread returns its terminal result with `replayed=true`, across both transports. Changing the task or thread under an existing ID returns 409. A healthy running request also returns 409; it is never started twice. After owner loss, a committed answer is recovered; otherwise the request becomes interrupted without replaying tools. Failed requests retain their original error. Use a new ID to intentionally start another attempt.

### Planning and approval

Each chat request accepts `planning_mode="auto" | "force" | "skip"`. The default `auto` lets the orchestrator decide; `/plan <task>` selects `force`, and `/noplan <task>` selects `skip`. Skip mode disables durable planning and approval tools for that turn but leaves all scientific inference, sizing, configuration, and provenance checks intact. If a draft is pending, a `skip` request (`/noplan <task>` or `planning_mode="skip"`) rejects and archives it before direct execution; active plans are left unchanged. While a draft is pending, the browser does not send `/plan`; it asks the user to approve, reject, or request changes first. `planning_mode` is the only planning-policy field accepted by the API. The commands are declared once, in `CHAT_COMMANDS` in `src/interfaces/commands.py`; the API, the CLI help, and the Web UI all read that declaration. In the browser composer, typing `/` opens the command menu built from the `chat_commands` list of [`GET /api/registry`](#registry-api); the same list handles filtering, keyboard selection, and `/plan`/`/noplan` parsing.

### `GET /api/health`

Returns:

```json
{"status": "ok"}
```

### `POST /api/chat`

Non-streaming chat.

Flow:

1. Parse the message and planning mode (HTTP 422 for a bare `/plan` or a conflicting mode), load the project (404), resolve the thread (the request's `thread_id`, else the project's stored one), and check the startup services, including the memory LLM endpoint (503).
2. Validate the live frontend token, pin the request, acquire the project execution lock, then register the LAMMPS cancellation session.
3. For a `skip` request, reject any pending plan draft. Then run `prepare_project_session()` in a thread pool: it cancels runs left over from an earlier turn, repairs the checkpoint, interrupts an orphaned running assistant turn, replays pending memory publications, and returns request-scoped project context from SQLite. A dynamic prompt injects the summary at model call time without rewriting checkpoint messages.
4. Run the shared recorded-chat execution path. It creates the user turn and a running assistant turn, feeds agent events into `ChatTraceRecorder`, then completes, fails, or interrupts that same assistant turn.
5. After a successful assistant turn, extract durable facts and refresh the semantic summary when its refresh policy is due.
6. Repair the checkpoint on error and unregister the cancellation session on exit.

The ownership in step 2 (`TurnOwnership`) and the turn in steps 3–6 (`run_turn()`) come from `src/interfaces/turn.py`, the turn pipeline the CLI uses as well. The non-streaming response waits for the same recorded execution used by the streaming route, so both APIs have identical history and semantic-memory behavior.

### `POST /api/chat/stream`

SSE streaming chat.

The route sends a `status` event before project preparation, then runs the same recorded-chat execution as `/chat`. Agent output comes from `iter_chat_events()` in `src/agents/streaming/events.py`; the event types are listed under [Stream events](#stream-events):

- Each event is sent as `data: <json>\n\n`.
- A bounded queue with capacity one applies backpressure without accumulating a large delayed batch of tokens.
- Internal session-summarization tokens are suppressed; compaction emits only one `Compressing conversation context…` status event.
- `ChatTraceRecorder` keeps independent open reasoning segments per parent/label, so parallel worker deltas are coalesced even when their SSE events interleave.
- Worker events forwarded into the orchestrator stream carry `parent_id`, the delegate's unique tool-call ID, and `parent_name`, its tool name for display only; the name is never used as identity. Older name-only events remain readable through a fallback.
- The browser updates open reasoning nodes at most once per animation frame and does not rebuild the complete live trace DOM when switching between parallel parents.
- A `tool_progress` event, sent while the model is still writing a named tool call, creates a provisional tool row immediately; the later `tool_start` fills in its stable call ID and bounded input instead of making the UI appear idle while arguments finish.
- Worker final-answer deltas (`agent_result_delta`) stream into a dedicated `Agent result` box after that delegate's last reasoning/tool step. If the model instead calls another tool, the provisional box is reset (`agent_result_reset`); `tool_end` then canonicalizes it with the bounded worker summary. That result closes the delegate-parenting scope, so later unparented reasoning is rendered as orchestrator reasoning outside the worker card. The live trace stays visible while the orchestrator streams its final answer, then becomes the normal collapsible execution trace.
- If the UI disconnects, the route sets the cancel event and calls `cancel_session()`.
- The event consumer then drains the original execution in the background without sending more UI events. Its project execution lock remains held until synchronous delegates return; a cancelled turn cannot become completed or trigger semantic extraction on a late reply.
- The graph's internal `done` becomes a `status` event with `phase="finalizing"` while the assistant turn is persisted and fact extraction and any due summary refresh finish. The route emits terminal `done` only after the request receipt is durable and the execution lease is released; the browser keeps its composer busy until then.
- On exception, the route attempts checkpoint repair and sends an `error` event.

SQLite is the authoritative chat store. `chat/transcript.md` is a human-readable view regenerated atomically from SQLite once per finished turn, and again whenever the file API lists `chat/` or reads `chat/transcript.md`; it is never updated per token.

### Stream events

`src/core/stream_events.py` declares the event vocabulary shared by every producer (graph stream, worker invocations, turn pipeline, API transport) and consumer (trace recorder, CLI renderer, Web UI). `StreamEventType` names the types, one `TypedDict` per type lists its fields, and `validate_stream_event()` checks an event against them:

| `type` | Fields | Meaning |
| --- | --- | --- |
| `status` | `message`, optional `phase`, `request_id`, `replayed` | Progress text for the waiting indicator; `phase` marks transport stages such as `finalizing`; `replayed` marks the answer to a repeated request ID. |
| `thinking` | `text` | Next piece of model reasoning. |
| `thinking_withheld` | `reasoning_tokens` | The model reasoned but the provider disclosed no text (an empty reasoning summary, or a token count without text); `reasoning_tokens` is 0 when the count is unknown. |
| `final_start` | — | The final answer begins; `token` events follow. |
| `token` | `text` | Next piece of the final answer. |
| `reset_stream` | — | Discard the final answer streamed so far; a tool call superseded it. |
| `tool_start` | `id`, `name`, `label`, `input` | A tool call starts; `input` is a bounded preview of its arguments. |
| `tool_progress` | `id`, `name`, `label` | The model is still writing a named tool call. |
| `tool_end` | `id`, `name`, `label`, `output`, `image_paths`, optional `status`, `interactions` | A tool call returned; `output` is a bounded preview of its reply. |
| `agent_result_delta` | `text` | Next piece of a worker's final answer, shown under its delegate. |
| `agent_result_reset` | — | Discard the worker answer streamed so far. |
| `done` | `reply`, optional `thread_id`, `project_id`, `request_id`, `replayed`, `image_paths`, `status`, `recovery` | The turn's answer. The graph emits `reply` and `thread_id`; the API's terminal event carries the rest of its `ChatResponse`. |
| `error` | `message` | The turn failed. |

`WORKER_FORWARDED_EVENTS` lists the types a worker forwards from its own graph into the orchestrator stream: `tool_start`, `tool_end`, `thinking`, `thinking_withheld`, `status`, `agent_result_delta`, and `agent_result_reset`. These also carry `parent_id` and `parent_name`. In-process consumers can see private `_raw_*` fields, which are removed before an event leaves the process.

The trace recorder stores `thinking_withheld` as a `reasoning_withheld` trace event (`input_json` `{"reasoning_tokens": N}`), so `GET /api/projects/{id}/history/{turn_id}` returns it; it never adds text to the turn's reasoning. The Web UI, the CLI and `chat/transcript.md` show a note such as "Reasoned for N tokens; the provider returned no summary."

The Web UI imports the same type names from `web/assets/js/stream_events.js`. That file is generated: run `PYTHONPATH=src python -m core.stream_events` from the repository root with the `GBclaw` environment active after changing `StreamEventType`; add `--check` to report drift without writing (exit status 1).

### `POST /api/chat/stop`

Stops the active run by user request.

Flow:

1. Validate the live frontend owner and the active thread; foreign owners and mismatched threads return 409. Hold a stop lock to prevent admission of a new turn during cleanup.
2. Validate a supplied `request_id`, then atomically signal the exact execution owner. A different service worker can receive the HTTP request; the executing worker observes the shared signal, cancels resource waiting and terminates its own LAMMPS/Coding subprocesses.
3. Repair the checkpoint only when no execution owns it. A live execution recovers during normal cleanup or preparation of the next turn.
4. Find the active assistant turn and mark it interrupted. Its reasoning comes from the `kind: "reasoning"` segments of `partial_trace`, else the legacy `reasoning` field, else the reasoning trace events already persisted. Persisted trace events are kept; other `partial_trace` segments (tool calls, agent results) are not stored.

`ChatStopRequest` supports `partial_trace` and legacy `reasoning` for compatibility. Stopping does not append a second standalone interruption record. An accepted active stop returns `status="stop_requested"`; actual thread exit can take longer. A supplied ID with no active execution returns `not_running` without changing conversation history. If the ID has not been admitted, a cancellation receipt prevents a later chat with that ID from starting. An existing terminal result is preserved. A stale active ID or a different recorded thread returns 409. Omitting the ID retains current-thread stopping without stale-request protection; when nothing is executing, that call repairs the checkpoint, interrupts any running assistant turn, and returns `status="stopped"`. Repeated interruption preserves terminal history. Active Coding workspace cleanup waits for the worker to close its backend; stop only marks the run cancelled. If the original chat connection remains open, cooperative cancellation returns an `interrupted` classic response or SSE `done` event rather than HTTP 500. Cancellation takes precedence over simultaneous context recovery errors and does not run post-completion semantic extraction.

In the Web UI the stop button appears only while a turn runs. It posts `/api/chat/stop` with the turn's `request_id` and `partial_trace`, then aborts the stream locally, marks running tool rows as stopped and shows "Stopped by user. Partial progress was saved." If the backend does not confirm, the page reports that only a local stop happened.

## Project API

Defined in `src/interfaces/api/routers/projects.py`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/projects` | List projects, sorted by creation time descending. |
| `POST` | `/api/projects` | Create project. |
| `WS` | `/api/projects/{id}/connection` | Exclusively occupy a project and receive its write token. |
| `GET` | `/api/projects/{id}` | Get project metadata. |
| `DELETE` | `/api/projects/{id}` | Delete the whole project workspace. |
| `GET` | `/api/projects/{id}/memory` | Return semantic summary metadata plus exact active and pending-plan records when present. |
| `GET` | `/api/projects/{id}/history` | Return user/assistant transcript for UI replay. |
| `GET` | `/api/projects/{id}/history/{turn_id}` | Return one turn with its reasoning and trace events. |
| `GET` | `/api/projects/{id}/files` | List a directory. |
| `GET` | `/api/projects/{id}/files/stat` | Get file or directory stat; directories also return the listing fingerprint the file browser polls. |
| `GET` | `/api/projects/{id}/files/content` | Read text preview. |
| `GET` | `/api/projects/{id}/files/download` | Download file. |
| `GET` | `/api/projects/{id}/files/raw` | Return the file inline with a guessed media type (image previews and chat images). |

All file routes resolve paths through `prepare_project_path()` in `project_workspace/artifacts/files.py`, which calls `resolve_project_path()` to reject paths that escape the project and regenerates `chat/transcript.md` when the request targets `chat/` or that file.

### Why plan approval uses chat

Plan approval is a research decision that belongs in the same durable conversation as the task and its results. The browser's buttons therefore submit ordinary user messages through the shared chat path; they do not edit plan files directly.

While a draft awaits approval, the composer shows its title/ID/revision/step count directly above the message box. **Approve & execute** sends an explicit user chat message; **Disagree / request changes** prefills that same message box so guidance can be edited before sending. Natural-language approval or feedback works through the same chat path. The panel is driven by `pending_plan` from the memory response, so it survives reloads and project switches.

The file routes expose pending root `plan-draft.md`, active `plan.md`, audited draft JSON under `memory/plans/drafts/`, current plan JSON, and immutable revision JSON. The browser remains read-only; SQLite-backed plan tools, not file edits, change state.

## Registry API

Defined in `src/interfaces/api/routers/registry.py`. `GET /api/registry` returns the backend declarations the Web UI needs to label and stream events, so the browser does not keep its own copy (`RegistryResponse`):

| Field | Content |
| --- | --- |
| `delegate_labels` | Delegate tool name → display label for every registered worker, for example `delegate_csl` → `Structure agent`. |
| `preserve_final_stream` | Sorted names of tools whose `ToolCapabilitySpec.preserve_final_stream` is set. An orchestrator call to one of them keeps the final answer already streamed instead of resetting it. |
| `chat_commands` | One `ChatCommandRecord` (`command`, `planning_mode`, `description`, `missing_task_message`) per entry in `CHAT_COMMANDS`. |

`web/assets/js/registry.js` loads the registry at startup, alongside the health check. If the request fails, the UI falls back to humanized tool names and an empty command menu; the server still parses `/plan` and `/noplan`.

## DichromaticMap API and widget

Defined in `src/domains/structure/routes.py` and mounted through the `routers` declaration of the Structure extension in `src/domains/structure/extension.py`. Numerical previews do not invoke an agent or LLM. Project exports use the same headless CLI, run records, and memory facts as the Structure worker's `render_dichromatic_map` tool.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/dichromatic-map/options?lattice=FCC&axis=110` | Return `geometry`, `presets`, `defaults`, and resource `limits`. Geometry includes layer names and `angle_range.minimum_deg` / `maximum_deg`. |
| `POST` | `/api/dichromatic-map/preview` | Accept a `DichromaticMapRequest` and return coordinates, same-layer coincidences, optional near pairs, exact-cell metadata, and view bounds without saving a project run. |
| `POST` | `/api/projects/{project_id}/dichromatic-map/export` | Accept `{parameters, purpose, source_run_id?}` and return a `RunToolResult` with run/artifact references and a compact scientific result. The optional source run must be completed in this project. |
| `POST` | `/api/projects/{project_id}/dichromatic-map/requests` | Prepare an interactive cell request from `{parameters, purpose, source_run_id?}`. |
| `GET` | `/api/projects/{project_id}/dichromatic-map/requests?status=pending` | Recover requests for the current project after reload or project switching. |
| `GET` | `/api/projects/{project_id}/dichromatic-map/requests/{request_id}` | Read authoritative geometry, candidate IDs, revision, selection, and result. |
| `POST` | `/api/projects/{project_id}/dichromatic-map/requests/{request_id}/preview` | Extend a pending request's viewport with `{center, width, height}` without changing its physical geometry. |
| `POST` | `/api/projects/{project_id}/dichromatic-map/requests/{request_id}/selection` | Submit four `candidate_ids` and `expected_revision`; validate and save the user's cell. |
| `POST` | `/api/projects/{project_id}/dichromatic-map/requests/{request_id}/cancel` | Cancel a pending selection with `expected_revision`. |
| `POST` | `/api/projects/{project_id}/dichromatic-map/requests/{request_id}/apply` | Apply strain to a confirmed cell with bounded strain/rotation settings; also available through the Structure tool. |
| `POST` | `/api/projects/{project_id}/dichromatic-map/requests/{request_id}/edge-analysis` | Analyze the four selected edges from saved strain, or reuse their existing measurements and annotations. |

A preview body can be `{"lattice":"FCC","axis":"110","width":12,"height":9}`. Export wraps that object in `parameters`; `purpose` records the reason for saving the diagram and defaults to browser exploration when omitted. The complete parameter list and scientific scope are described in [Tools](tools.md#dichromatic-maps). The server returns validation errors for unsupported geometry, invalid layers, or excessive projected meshes.

The floating DichromaticMap widget opens from a bubble and minimizes back to it. It exposes lattice/axis/angle and preset selection, per-grain layer visibility and colors, display rotation, same-phase coincidence and exact-cell overlays, and optional local near pairs. Marker appearance uses fixed defaults. Its plot supports pan, zoom, and a two-atom projected distance measurement. Dragging and zooming request coordinates for the visible area through the numerical API; the canvas draws those atoms without downloading rendered PNGs. Coordinates are in a0; the explicit lattice constant supplies the Angstrom conversion. **Save current view** saves the visible field and its parameters as permanent PNG/JSON artifacts in the current project (creating one if none is open); the field must be 0.1–100 a₀ on each axis. **Use in chat** first saves the map, or reuses its unchanged export, then fills the composer with purpose, run ID, PNG path, parameters, and any selected measurement. The user sends that message; the widget does not send it automatically. Browsing and previewing alone do not create memory runs.

For an interactive near-CSL cell, an agent tool emits compact `interactions` beside the ordinary tool preview. The widget opens the request, lets the user choose four candidate sites, and posts their IDs to the selection endpoint. **Confirm cell and continue** saves that choice and starts the next chat turn to apply strain; if the current turn is still running, continuation waits until it finishes. Preparation alone does not keep an agent call open. See the [full workflow](dichromatic-map.md), including project isolation and memory provenance. The browser uses the numerical library without embedding a desktop Qt window.

## Scientific images in chat

Image discovery uses project artifact references rather than a single expected PNG filename. `project_workspace/artifacts/presentation.py` extracts image paths from complete nested tool results before trace previews are shortened. Streaming events and history responses expose these as `image_paths`, preserving multiple images even when the displayed tool text is truncated. `web/assets/js/artifact_images.js` resolves project-scoped paths, rewrites Markdown images and links to the file API, appends referenced images missing from the answer, and removes duplicate canonical paths. This covers DichromaticMap, OVITO renders, stress curves, NEB plots, and other project image artifacts in both live chat and history replay. Paths from other projects and filesystem traversal are rejected; images use the existing modal preview.

## Schemas

`src/interfaces/api/schemas.py` defines Pydantic request/response models:

- health: `HealthResponse`
- project: `ProjectSummary`, `ProjectResponse`, `ProjectListResponse`, `CreateProjectRequest`
- file browser: `FileEntryResponse`, `FileListResponse`, `FileStatResponse`, `FileContentResponse`
- memory/history: `ProjectMemoryResponse`, `HistoryTurnSummaryResponse`, `HistoryTurnDetailResponse`, `TraceEventResponse`, `ProjectHistoryResponse`
- chat: `ChatRequest`, `ChatResponse`, `ChatStopRequest`, `ChatStopResponse`
- registry: `RegistryResponse`, `ChatCommandRecord`

## Web UI Files

The static frontend lives in `web/` (entry page `web/index.html`) and is mounted by FastAPI.

Important JavaScript files:

- `web/assets/js/app.js`: main chat app logic.
- `web/assets/js/config.js`: API endpoint/runtime configuration.
- `web/assets/js/registry.js`: loads `GET /api/registry` at startup and serves delegate labels, the preserve-final-stream set, and slash commands to the other modules.
- `web/assets/js/stream_events.js`: stream event type names, generated from `src/core/stream_events.py`.
- `web/assets/js/chat_commands.js`: slash-command matching and parsing over the registry's command list.
- `web/assets/js/project_connection.js`: project occupancy WebSocket and write-token headers.
- `web/assets/js/files.js`: project file browser with adaptive change polling (`files/stat` fingerprints), text preview with a live tail for growing log files, images opened in the image viewer, downloads, a project picker when no project is open, and the citation block at the bottom of the files panel (hidden in the stacked small-screen layout).
- `web/assets/js/content.js`: page copy (including the citation text) and theme-specific brand icon URLs.
- `web/assets/js/markdown.js`: markdown, KaTeX, and syntax highlighting.
- `web/assets/js/autoScroll.js`: chat auto-scroll.
- `web/assets/js/panelResize.js`: panel resizing.
- `web/assets/js/theme.js`: theme handling.
- `web/assets/js/image_modal.js`: full-screen image viewer: opens with the whole image fitted and centred (never enlarged beyond its own pixels); zoom with the wheel, the −/+ buttons or `+`/`-` keys around the cursor (from 10%, or the fitted scale if smaller, up to 1200%); drag to pan; double-click toggles between fit and 100%; **Reset** or `0` returns to fit; Escape closes.
- `web/assets/js/artifact_images.js`: shared project-image discovery and chat rendering.
- `web/assets/js/dichromatic_widget.js`: floating map controls, canvas interaction, and project/chat handoff.

Vendor assets live in `web/assets/vendor/`, and styles live in `web/assets/css/` (`main.css`, plus `dichromatic_widget.css` for the map widget).

## CLI

The `src/interfaces/cli/` package provides the command-line chat entry point (`PYTHONPATH=src python -m interfaces.cli` from the repository root, or `./start_cli.sh`, which activates the `GBclaw` Conda environment; both need an interactive terminal and a `.key` file in the repository root). `main.py` builds the model router, starts extensions, creates the orchestrator, shows the project menu, and on exit cancels sessions, closes the checkpointer and stops extensions; `chat.py` runs the chat loop and one turn at a time on a worker thread while the main thread renders events and handles Ctrl+C; `input.py`, `render.py`, and `projects.py` provide readline input (slash-command completion, per-project history, backslash and ``` continuation, bracketed paste), streamed-event rendering, and the project menu. It uses the same core components as the API:

- `ProjectStore`
- `ModelRouter`
- orchestrator graph
- `start_extensions()` / `stop_extensions()`
- the shared turn pipeline (`run_turn()` in `src/interfaces/turn.py`, including `prepare_project_session()`)
- `CHAT_COMMANDS` for `/plan` and `/noplan`
- project history
- the stream event vocabulary (`StreamEventType` in `src/core/stream_events.py`), rendered by the CLI's own `render.py`
- project occupancy (`ProjectFrontendLease`, held for the whole session) and `TurnOwnership`, created on the main thread so that Ctrl+C calls its `cancel()` (the session flag plus termination of registered subprocesses), aborts the turn coroutine through `TurnControl` so an in-flight model stream stops at once, and then calls `cancel_running_runs_for_chat()`, as the API's stop route does

The CLI adapts those shared services to terminal input, slash commands, project switching, history display, streamed output, and interruption handling. A change to scientific execution or persistence should belong in the shared service so that browser and terminal users receive the same behavior.
