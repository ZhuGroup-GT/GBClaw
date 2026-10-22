# Agent context middleware

This package owns model-request context and thread conversation continuity. It does not
publish scientific facts or regenerate the project semantic summary.

| Module | Responsibility |
| --- | --- |
| `__init__.py` | Public context contracts and lazy middleware/factory exports |
| `contracts.py` | Runtime context, memory snapshot adapter, structured compaction errors and provider-overflow classification (`ContextCompactionError.from_exception`, `provider_overflow`) |
| `factory.py` | Orchestrator/worker assembly and endpoint budgets |
| `session_policy.py` | Session content contract, prompt, input views and validation |
| `session.py` | Checkpoint compaction, current-turn protection, bounded retry and legacy conversion |
| `archive.py` | Recoverable original outputs for messages consumed by a summary |
| `projection.py` | Main-request working/memory allocations and final budget guard |
| `worker_policy.py` | Worker summary contract (results for finished work, detail for work in progress) and fitting it to the in-context limit by moving COMPLETED overflow to a file |
| `worker.py` | Single-pool compaction for disposable workers: overflow retry, look-back archiving, COMPLETED spill to a readable file |
| `recovery.py` | Verified archive cards using the shared pending-decision contract |
| `project.py` | Dynamic injection of the project summary and authoritative plan state |
| `cancellation.py` | Cooperative execution-cancellation check before and after each model call |
| `turn_start.py` | Removes completed turns' reasoning and unanswered copies of the new request from the checkpoint |

Use `agents.middleware` for the public runtime context, factories, middleware and error type.
New ordinary workers use the existing `agents.graph` factory, which assembles the context
middleware automatically. Workers do not get the orchestrator's partitioned budgets: a
disposable worker has one pool (the smaller of its endpoint window and `total_context_tokens`,
minus the output reserve and the static prompt/tool schemas), compacts at
`worker_compact_fraction` of it (default 0.8) with a content-aware summary, keeps the task and
the newest complete tool-call blocks verbatim, and can re-read any consumed result through
`read_tool_output`. Changing session content should normally require editing only
`session_policy.py`, and worker summary content only `worker_policy.py`; changing model
allocation belongs in `factory.py` and `core/settings/runtime_context.py`. The
force/skip/approval plan gates live with the planner in `agents/planner/gates.py`;
`factory.py` assembles them. The old `agents.context_middleware`, `agents.context_recovery`
and `agents.prompts.session_summary` import paths have been removed.

## Session summary contract

The handoff contains `CURRENT FOCUS`, `ACTIVE CONSTRAINTS`, `OPEN ITEMS` and
`RETRIEVAL REFERENCES`. Its target is 2,000 characters, with a 4,000-character hard write cap
and at most three reference lines. The cap does not grow with the conversation or 1M mode.
The actual token allocation remains an additional validation limit. The summarizer sees
the current user request and its preceding answer so a short confirmation or changed task
does not leave the handoff focused on earlier work. These messages remain in the checkpoint.
If that answer alone cannot fit the working limit, it is summarized with the earlier messages;
its full text stays in the chat history.

Tool inputs to this auxiliary model contain status and exact navigation references, rather
than measurements or result prose. Assistant call names/IDs remain, but call arguments and
provider reasoning metadata are omitted. User messages and active constraints are not
silently sliced. Pending choices retain complete call/result blocks. Ordinary assistant
answers remain available to interpret user references, with an explicit instruction to omit
their detailed scientific findings from the handoff.

Raw results belong in runs, artifacts, conclusions or the project tool-output archive.
The archive helper preserves small results consumed during compaction, including results
below the main request projection threshold. It retains the existing project/invocation
scope and delegate visibility rules. Summaries only provide navigation hints; agents must
read evidence before answering requests about exact results.

## Safe replacement

The original read allocation still admits older checkpoints. An old summary is rewritten
from its source plus current retained context, and replaced only after structure, character,
token and provider-completion checks pass. Each batch gets at most one corrective attempt.
Failed conversion leaves the old messages intact; a private fingerprint of the latest user
request and the old summary avoids repeating conversion on every tool step while the request
still fits. Hard budget checks always apply. Middleware class and node names remain stable
for checkpoint resumption.

History may contain a pending tool payload even after a user has acted elsewhere. This
package conservatively retains such blocks; it does not infer resolved approvals from
conversation prose. Large user constraints or pending payloads can still require a narrower
request. The fixed summary cap is not a guarantee of unbounded total conversation capacity.

## Adjacent ownership

- `project_memory/snapshot/contracts.py`: pure `ProjectMemorySnapshot`; it does not import
  Agent or model-construction modules.
- `project_memory/summary/`: selective project working summary, importance/size policy,
  source-separated generation, transactional SQLite history and Markdown views.
  `summary/contracts.py` owns the project-summary format version.
- `project_memory/snapshot/session.py`: `read_project_memory_snapshot()` supplies project
  summary, plan pointers and memory health to request preparation, live prompt injection
  and the UI preview. Its module docstring explicitly distinguishes this project data reader
  from Agent Session summary generation.
- `agents/memory/decisions.py`: shared structured pending-decision predicates for
  tool projection, worker handoff and conversation preservation.
- `agents/memory/projection.py` and `project_memory/storage/tool_outputs.py`: shared durable
  tool output storage and request projection.
- `project_workspace/execution/checkpoint.py`: project/thread checkpoint lifecycle.
- `interfaces/turn.py`: shared request preparation (`prepare_project_session()`), not summary
  generation.

The memory reader returns data; `ProjectRuntimeContext.from_memory_snapshot()` adapts it to
the Agent request contract and adds the current planning mode. `project.py` reads a fresh
snapshot on every model call so tool updates within a turn remain visible. Failed reads
cannot inject an unverified cached summary, and the previous approval-state fallback remains.

## Persistent threads

The main chat thread ID is stored in `project.json`. Reopening the same project normally
uses that same ID, even after a long absence or a service restart. Closing the webpage
releases its project ownership, not its chat history or checkpoint. Session summary and
recent messages are restored from that thread's checkpoint; complete chat history remains
in the separate human-facing archive. A persistent thread does not mean replaying every
past message into each model request. The API can explicitly select another thread, while
the ordinary UI and CLI use the saved project default.

Chat history remains a human-readable record. Conversation-derived facts, where enabled
by the existing extraction pipeline, continue to have their own provenance and are not
made into verified calculation results by this session summary.
