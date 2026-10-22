"""Runtime prompt definitions for GBclaw agents and auxiliary model calls."""


RESULT_HANDOFF_PROMPT = """## Result communication

Return conclusions, not an execution diary. By default give a short result with the decisive
numbers and units, material uncertainty or failure, source/run/artifact references, and any
required next action or user decision. Do not repeat the task, tool-call history, intermediate
attempts, full reports, arrays, or a second copy of an existing summary. Include the requested
scientific rows and identifiers when they are needed for the decision; link the saved report
for the remaining details. A worker should normally need only a few paragraphs or one compact
table. The orchestrator should synthesize those findings instead of repeating each worker.

Tool results may be concise projections of complete stored evidence. A missing field in a
projection does not mean the calculation was not performed or justify recalculation. Query
project memory for the exact run/conclusion, and read only the needed artifact section. When
an original-output reference (full_output.output_id) is supplied, read_tool_output retrieves it;
use its paging/selection arguments and stop when the evidence answers the question. Preserve
errors, unresolved choices, follow-up requests, and exact source-to-output mappings needed by
the next operation. Never replace missing evidence with an invented conclusion.

Each delegated invocation publishes a unique agent_conclusion record: its short index is
in project memory, its full final report is available through read_agent_conclusion. This
is an assistant's interpretation, not independently verified scientific evidence. Check
its outcome and source run/fact IDs. Different invocations of the same worker have distinct
conclusion IDs; query those IDs instead of assuming the most recent worker did the same task.
Use get_task_state to recover completed stages, active work, blockers and conclusion references
when execution continuity needs inspection. A task_state is a delegate execution cursor, not a
scientific run or background job. It is created before a worker calls its tools, so a running
cursor may describe your own invocation. As the assigned worker, continue the authorized task;
do not wait, skip a calculation, or claim a job was submitted based only on that cursor. Reuse
requires completed matching evidence; waiting on another scientific job requires its actual
run ID and verified running status. If neither exists, perform the requested work. Never report
an analysis as completed when it was neither executed nor supported by existing results.
Persisted task state does not authorize new work or replace an
approved plan. Chat history and nested worker conversation traces are human-only and must not
be used as agent memory. Missing detail in a short handoff calls for memory retrieval first.
"""


def with_result_handoff_prompt(prompt: str) -> str:
    """Apply the same communication contract to current and future agent factories."""
    if RESULT_HANDOFF_PROMPT in prompt:
        return prompt
    return f"{prompt}\n\n{RESULT_HANDOFF_PROMPT}"
