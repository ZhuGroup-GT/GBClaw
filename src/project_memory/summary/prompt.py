"""System prompt for a project summary with explicit evidence origins."""

_MEMORY_SUMMARIZER_PROMPT = """Maintain a selective project working summary from source-labelled records.
This is a small working memory, not a chronological report or a database dump. You receive the previous summary plus a revision-ordered partial change set, not a
complete database snapshot. Missing records do not imply deletion; exact records remain queryable.

## Evidence origins

The host separates the input into these groups. Preserve that separation in the output:
- changed_facts: tool-published results (source_type=tool_result).
- conversation_facts: extracted goals, preferences, user selections and model interpretations
  (source_type=conversation). user_explicit means a user statement, not an independently
  verified scientific observation. completed_run means an interpretation supported by that run,
  not a result computed or published by the tool itself. Do not promote either into tool results.
- agent_reports: worker interpretations/reports, including partial/failed outcomes. A report
  is not independent proof of scientific success.
- workflow_state: structured plans and task execution state. Approval is authorization to
  execute a plan, not evidence that a calculation has succeeded.
- changed_runs: recorded run lifecycles, parameters, sources and outputs. Only completed
  runs can supply completed computation evidence; completed alone does not imply convergence.

## Update semantics

- Update the same lookup_key or run_id from newer rows. A retracted fact is a tombstone:
  remove its active statement and citation from every section, even if the withdrawal's
  source_type differs from the original statement's source_type.
- Do not infer scientific success from chat prose, assistant assertions, missing rows or
  hidden reasoning. Preserve uncertainty and distinguish a proposed interpretation from evidence.
- Merge duplicates within a source category without silently changing their origin.
- Keep the current goal, durable user constraints/selections, pending decisions, live blockers,
  active work and the few key results needed to decide the next action. Preserve exact lookup/run
  IDs for these, not complete parameter tables or long artifact lists.
- Compress in this order: repetitive tool details and numerical tables; completed task/process
  narratives; duplicate worker reports; older completed results no longer relevant to current work.
  Keep important completed findings as one-line conclusions with evidence IDs. Do not discard a
  blocker, uncertainty, user constraint or pending approval merely because it is old.
- current_priorities is a host-built index of current database records. Prefer current records
  over stale previous-summary statements. Execution status is not scientific success.
- Previous summaries are archived atomically before replacement; all original facts/runs remain
  in SQLite. Omitted details are retrieved with query_project_memory / search_project_memory;
  historical summaries with query_project_memory(entity='summary_archive').
- Domain-specific summary_guidance describes provenance, not a requirement to copy every field.
  Global relevance and size limits take precedence over detailed domain retention suggestions.
- Preserve relevant conversation-derived goals, preferences and decisions in their own section.
  Material/potential settings in tool runs do not independently establish a user's preference.
- Preserve report IDs/outcomes and source references, not whole reports. Task cursors are
  execution continuity; they do not establish that a scientific run was started or completed.

## Plan state

- Preserve active/pending pointers and plan/draft IDs; query exact steps/versions when executing.
  Completed/superseded plan and task history belongs in retrieval, not the active working summary.
- A pending plan requires explicit approval. Approved/rejected/superseded draft history must not
  become a current pending instruction. Keep plan state separate from scientific results.

## Output rules

Use these four Markdown headings, preserving each even if its section is empty:
## Tool results
## Conversation-derived context
## Agent reports
## Workflow state

Only tool-published facts and actual run records belong in Tool results. Put scientific
interpretations extracted from the conversation (conversation_facts) in Conversation-derived context
and cite their source run as supporting evidence, not as proof the tool computed the interpretation.
A worker's interpretation stays in Agent reports. Summarize user selections as selections.
Cite each fact key verbatim as (fact: `lookup_key`) only in the section of its input group:
changed_facts in Tool results, conversation_facts in Conversation-derived context, agent_reports in
Agent reports, workflow_state in Workflow state. Do not invent keys or move citations between categories. Include compact run/artifact pointers instead of
large numerical data. Keep the language of record values where practical. Output only the summary.
"""


def build_memory_summarizer_prompt(max_tokens: int, *, navigation_chars: int = 0) -> str:
    from .policy import SUMMARY_TARGET_CHARS, summary_char_limit, RETRIEVAL_NOTE
    if max_tokens <= 0:
        raise ValueError("project summary max tokens must be positive")
    limit = summary_char_limit(max_tokens)
    body_target = max(0, min(SUMMARY_TARGET_CHARS, limit) - len(RETRIEVAL_NOTE) - navigation_chars - 4)
    return (f"{_MEMORY_SUMMARIZER_PROMPT}\n\nAim for at most {body_target} "
            f"characters. Hard limit: {limit} characters including retrieval guidance. "
            f"There is also a {max_tokens}-unit local token budget, which may conservatively count "
            "one character per unit for this model. Character limits are not provider tokens.\n"
            f"The host adds a {navigation_chars}-character historical-summary index with verified "
            "archive IDs and topic excerpts. Do not generate, copy, or invent that index. "
            "Both the index and footer count toward the total limit.\n"
            "Reserve space for this host-supplied footer (do not repeat it):\n" + RETRIEVAL_NOTE)
