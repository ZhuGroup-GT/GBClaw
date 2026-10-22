# Project working summary

This package owns the project-wide derived summary. `project_memory/snapshot/session.py`
reads persisted project snapshots; `agents/middleware/session.py` maintains a separate short
conversation-continuity summary in a thread checkpoint. Neither generates this summary.

| Module | Responsibility |
| --- | --- |
| `service.py` | Refresh coordination, input/output budgets, validation, atomic commit and view repair |
| `policy.py` | Importance ordering, character bounds, registry priorities and deterministic retrieval index |
| `batch.py` | Bounded revision-ordered changes and compact source cards |
| `prompt.py` | Relevance, compression order and evidence-origin instructions |
| `generation.py` | Initial generation, one size/retention/citation correction, bounded fallback |
| `validation.py` | Current SQLite fact keys, retractions and evidence-source sections |
| `navigation.py` | Host-built archive IDs, historical topic excerpts and bounded retrieval index |
| `storage.py` | Current row, immutable history, compare-and-swap commits and archive lookup |
| `views.py` | Rebuildable current/history Markdown documents |
| `contracts.py` | Format version, errors and batch data |

## Selection and retention

Keep current goals, durable preferences/constraints, pending approvals, unfinished work,
blockers and key result references. First remove detailed completed execution narratives,
parameter lists, duplicate reports and older results irrelevant to the next decision.
Relevant completed findings stay as short conclusions with exact evidence references.
Domain `context_priority` and fixed lookup keys participate in host selection. Live status
rows are queried before pagination so recent completed history cannot hide an old blocker.

The normal target is 6,000 characters; the ceiling is 12,000, reduced to the generation token
limit minus a 256-token framing reserve. Generation uses min(`project_summary_max_tokens`,
endpoint output limit, 24,000) tokens. These are explicit character limits, not provider token
counts. The runtime `project_summary_max_tokens` remains the injection ceiling.
The model sees bounded changes and current priorities, not the entire source database.
Oversized old prose is replaced **only in the generation input** by a current database index.
If generation still fails size/completion/required-pointer checks after one correction,
the same bounded index supplies the summary without inventing scientific interpretation.
Provider/network failures preserve the current summary. An invalid evidence-source citation gets
the same single correction, naming the section its record belongs in; if the corrected draft
still misattributes a citation, the current summary is preserved and its changes stay pending.

All four evidence-origin sections remain required: tool results, conversation-derived
context, agent reports and workflow state. Completed run status alone is not convergence;
approval is not scientific evidence. Current plan pointers are separately injected by the
runtime and remain authoritative.

## History and exact retrieval

Replacing a nonempty summary archives its complete previous text and source metadata in
`semantic_summary_archives`, in the same transaction as installing the new summary and
acknowledging processed changes. A concurrent summary update aborts a stale writer before
archive/prune. Compaction does not delete or rewrite any fact, run, artifact or conclusion.
Only derived change notifications are pruned after a successful commit.

Every nonempty working summary includes a host-generated historical index. It explicitly
states that **both the summary and its index are incomplete**: missing prose is not evidence
that a record does not exist. Up to three entries identify the immediate predecessor and
recent archives by their actual SQLite `archive_id`, source revision and short topic excerpts.
The index is capped at 1,400 characters (less for small budgets) and counts toward the total
summary character/token limits. Exact IDs and the lookup instructions are never truncated.
The predecessor ID is derived before generation and archived in the same transaction that
publishes the new summary, so a committed pointer resolves to a complete archived snapshot.

Topic previews are deterministic excerpts, not newly inferred facts. They omit historical
citation syntax and retain source labels. The host replaces model-invented navigation and
removes old indexes from summarizer inputs/previews to prevent recursive growth. Older entries
remain discoverable by `query` and `next_cursor`; query responses also expose `topic_summary`.

Use `query_project_memory(entity="facts", lookup_key=...)` or `entity="runs", run_id=...`
for current exact records; `search_project_memory` discovers older evidence. For old prose:

```python
query_project_memory(entity="summary_archive", query="NEB")  # previews + next_cursor
query_project_memory(entity="summary_archive", archive_id="summary-...")
```

Large responses retain lossless tool-output paging. Historical text can contain superseded
decisions or retracted interpretations: verify current records before acting. The archive is
not re-injected into the normal working context.

`memory/summary.md` and `memory/summary_archive/<archive_id>.md` are derived views. The
archive index lists the most recent 20 entries; older entries remain queryable in SQLite.
Normal refresh repairs recent views. `save_archive_views(project_root, memory, rebuild=True)`
rebuilds all historical documents without model calls. Existing databases acquire the archive
table on initialization; history begins with the first replacement under this implementation.

## Legacy extraction failures

Old candidate-budget errors may already be `dead_letter`. They are not silently replayed
at startup. The narrowly scoped maintenance helper
`project_memory.extraction.retry.requeue_legacy_extraction_budget_failure(store, project_id,
assistant_turn_id, dry_run=True)` checks eligibility without writes. An explicit
`dry_run=False` queues that eligible turn using a guarded update; it does not invoke a model
or reset its retry count. The normal retry worker subsequently handles it. New source-only
overflows, other permanent failures and already claimed jobs are excluded.

`requeue_dead_letter_extraction_failures(store, project_id, error_type=..., dry_run=True)` in the
same module requeues every extract-stage `dead_letter` job whose recorded `error_type` a host or
provider fix resolved, for example a request parameter the model rejected. It is dry-run by
default, never calls a model, and resets the attempt count because none of the failed attempts
exercised the source. Source overflow and policy failures are never requeued this way.

No old Python import shims are maintained. Consumers import this package directly.
