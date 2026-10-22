# Project memory source map

Project memory has one canonical package; import the layer you use directly. The domain registry it reads lives in [`extensions`](../extensions/), and the agent-facing memory integration lives in [`agents/memory`](../agents/memory/).

| Directory | Responsibility | Main entry points |
| --- | --- | --- |
| `storage/` | SQLite facts/runs/indexes, durable conclusions, task cursors and overflow outputs; `MemoryRepository` composes one mixin per table group (`schema`, `changes`, `runs`, `facts`, `retry_queue`, `artifact_index`, `search`) plus summary storage | `repository.py`, `conclusions.py`, `task_state.py`, `tool_outputs.py` |
| `publication/` | Bound tool runs, artifacts, evidence checks, atomic writes and recovery | `service.py`, `session.py`, `contracts.py`, `writes.py`, `results.py` (`RunToolResult`, `model_view`), `validators.py`, `facts.py` |
| `extraction/` | Conversation evidence policy, canonical keys and bounded extraction retry | `extractor.py`, `policy.py`, `keys.py`, `retry.py` |
| `snapshot/` | Read-only project snapshots and UI previews; no summary generation | `session.py`, `contracts.py` |
| `summary/` | Project-summary importance/size policy, generation, validation, SQLite archives and views | `service.py`, `policy.py`, `batch.py`, `generation.py`, `storage.py`, `views.py` |
| `models.py` | Run/fact, extraction, retry, summary and search record contracts | `FactRecord`, `RunRecord`, `MemoryExtraction` |
| `run_artifacts/` | Per-run artifact directories and manifests; cancellation of a chat's running runs and scratch cleanup | `store.py` (`ArtifactStore`), `cleanup.py` |
| `prompts/` | Extraction prompt; the project-summary prompt is `summary/prompt.py` | `extraction.py` |
| `identifiers.py` | Plan pointer lookup keys (run ID syntax is in `core/identifiers.py`) | `ACTIVE_PLAN_LOOKUP_KEY`, `PENDING_PLAN_LOOKUP_KEY`, `PLAN_POINTER_LOOKUP_KEYS` |

Declarations contain strings and immutable values, not live runtime imports. The registry is collected and frozen on first use, before models are created. Runtime layers resolve trusted hooks after bootstrap. Domain algorithms, adapters and specialized queries belong beside their domain tools; for example, CSL's query is in [`domains/structure/memory_query.py`](../domains/structure/memory_query.py).

Worker recall, memory tool assembly, conclusion handoff and model output projection live in [`agents/memory`](../agents/memory/) (`worker.py`, `handoff.py`, `conclusions.py`, `projection.py`), together with the model-facing memory query, artifact-read and state/conclusion/output retrieval tools in `agents/memory/tools/`; worker outcomes are in [`agents/workers/outcome.py`](../agents/workers/outcome.py). The worker execution coordinator is [`agents/workers/lifecycle.py`](../agents/workers/lifecycle.py): it also owns events and plan tracking. [`agents/graph.py`](../agents/graph.py) injects the active worker's prepared memory when building a standard graph. [`interfaces/turn.py`](../interfaces/turn.py) coordinates session recovery; [`interfaces/recording.py`](../interfaces/recording.py) shares turn recording between API and CLI and refreshes `chat/transcript.md` after each finished turn. [Project workspace](../project_workspace/README.md) owns metadata, chat/checkpoints and artifact files; its models belong to their respective subpackages. Project plans are handled by [`planning`](../planning/). `ProjectStore` constructs memory services on demand.

New extension workflow: [English guide](../../docs/extension-guide.md) · [中文接入指南](../../docs/zh/extension-guide.md).

`FactRecord.source_type` is derived from persisted evidence and provenance: `tool_result`,
`conversation`, `agent_report`, or `workflow_state`. Conversation extraction remains supported;
an interpretation with `completed_run` evidence still belongs to `conversation`. Query/search
filters and worker handoffs expose that origin. Summary format 4 groups tool facts, conversation
facts, agent reports and workflow state separately and validates fact citations against their
source section. Domain builders and summary guidance must preserve this distinction.

The project working summary is a selective index, not an ever-growing report. Its policy
targets 6,000 characters and caps new output at 12,000 characters and generation at
min(configured token ceiling, endpoint output limit, 24,000) tokens. Old completed details are
omitted before current goals, constraints, pending approvals or blockers. Two unsuccessful model
generations use a bounded database index instead. Full
previous summaries are archived in SQLite before replacement; facts and runs are untouched.
`query_project_memory(entity="summary_archive")` discovers history; `archive_id` reads the
exact saved text. Archives may contain superseded statements and never establish current state.
The current summary embeds up to three actual archive IDs, revisions and short topic excerpts.
This bounded index explicitly advertises omitted history and gives search/paging instructions.
See [summary ownership and recovery](summary/README.md).
