# Project workspace

This package owns project metadata, conversation archives, artifact files and execution
ownership/checkpoints. Semantic facts, extraction, publication, run artifact manifests,
retrieval and summaries belong to [`project_memory`](../project_memory/README.md); the
project-plan workflow belongs to [`planning`](../planning/).

| Location | Responsibility |
| --- | --- |
| `store.py` | Project metadata lifecycle and explicit service factories; metadata reads do not bootstrap memory. |
| `paths.py` | Default workspace location and project ID validation, without runtime imports. |
| `models.py` | Project metadata and timestamps. |
| `chat/models.py` | Conversation/trace records, independent of memory declarations. |
| `chat/repository.py`, `requests.py`, `schema.py` | One SQLite history database, turn state, request identity and atomic admission. |
| `chat/trace.py`, `transcript.py`, `exporter.py` | Stream recording, Markdown rendering and revision-aware exports. |
| `artifacts/` | Artifact records, safe project file browsing and image-reference presentation. |
| `execution/` | Frontend/execution leases, cancellation, checkpoint storage and repair. |
| `persistence/` | SQLite connections/transactions and process-shared file locks. |

Use concrete module paths. Old flat paths and `storage_models.py` have been removed without
aliases. Run/fact/extraction/retry/summary/search records now live in
[`project_memory/models.py`](../project_memory/models.py); artifact records live in
`artifacts/models.py`. Per-run artifact directories and manifests (`ArtifactStore`) and owned-run
cleanup live in [`project_memory/run_artifacts/`](../project_memory/run_artifacts/). Atomic
text/JSON replacement is [`core/util/io.py`](../core/util/io.py). Importing chat or ownership
infrastructure does not load memory extensions.

## Coordination boundaries

`ProjectStore` remains the common project entry point. `chat()`, `memory()`, `artifacts()`,
`runtime()` and `runs()` construct their services on demand. `memory()`, `artifacts()` and
`runs()` import their `project_memory` services lazily; no workspace module imports
`project_memory` when it is loaded. Existing project directories, database locations, manifest
fields, fact keys and public HTTP routes remain the same.

`prepare_project_session()` in [`interfaces/turn.py`](../interfaces/turn.py) coordinates
abandoned-run cleanup, checkpoint repair, interrupted chat finalization and publication recovery
under the execution lease. It then reads the project memory snapshot
([`project_memory/snapshot/session.py`](../project_memory/snapshot/session.py)) without
changing conversation state. `run_turn()` in the same module is the turn pipeline shared by the
API and CLI: it records the turn/trace/terminal state through
[`interfaces/recording.py`](../interfaces/recording.py) and runs post-completion
extraction/retry. Transports retain their event delivery and decide how long they keep the
project after a disconnect.

Project plans remain `workflow_state` facts in the memory database.
[`planning/rules.py`](../planning/rules.py) contains shared state/revision rules;
`planning/repository.py` maps records and commits versioned batches.
Approving a draft commits the draft decision, plan/revision and pointers in one transaction.
`planning/views.py` renders derived JSON/Markdown; those files never become the authoritative plan state.

## Derived chat exports

Chat and trace writes update SQLite and a durable export revision in the same transaction.
Individual writes do not render the complete conversation. The turn recorder
(`interfaces/recording.py`) calls `ensure_transcript()` after it records a turn as completed,
failed, interrupted or paused, and so do file listing of the chat directory and stat/preview/
download/raw access to `chat/transcript.md`. A turn finalized outside the recorder (the
`/chat/stop` endpoint, or orphan recovery at the next request) appears at the next export. Export
failures are logged and never fail the turn. Pending writes are coalesced into one export.
`rebuild_transcript()` explicitly forces a fresh export.
Direct filesystem readers should call one of these methods first when they require a fresh view.
The history API always reads SQLite and does not depend on the Markdown export.

An export uses a consistent SQLite snapshot and a process-shared file lock. Writes during
rendering remain dirty for the next export. A failed export leaves the source records and
pending revision intact. No background worker or additional database is required.

## Artifact and recovery behavior

Artifact stores
([`project_memory/run_artifacts/store.py`](../project_memory/run_artifacts/store.py)) locate
individual runs using registered workflow directories; construction does not scan the whole
artifact tree. Enumeration and usage reports read current manifests, so separate instances
observe additions/removals. Duplicate identities and invalid manifests remain errors. Atomic
file replacement uses unique temporary files.

Run cancellation
([`project_memory/run_artifacts/cleanup.py`](../project_memory/run_artifacts/cleanup.py))
filters the owning chat before pagination and advances by immutable run ID, so changing a run's
status cannot skip later pages. Workflow declarations may specify
`ephemeral_directories` as direct run-relative scratch directory names. Coding declares
`workspace` and `.sandbox-tmp`; generic cleanup preserves permanent registered evidence and
does not follow a scratch symlink outside its run directory.
