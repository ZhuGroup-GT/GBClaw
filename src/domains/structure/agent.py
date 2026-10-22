"""Structure worker: analyzes CSL geometry and GB structures (does not talk to the user).

Uses CSL geometry and DichromaticMap tools. Invoked statelessly by delegate_csl;
results are returned to the orchestrator via WorkerResult. In a project, the
delegate passes ``tools=`` with run-recording CSL and dichromatic tools in place
of the registered defaults.
"""

from __future__ import annotations

from agents.workers.spec import WorkerSpec
from domains.structure.prompt import STRUCTURE_AGENT_PROMPT

STRUCTURE_WORKER = WorkerSpec(
    agent="structure",
    label="Structure agent",
    status_message="Structure analysis running…",
    prompt=STRUCTURE_AGENT_PROMPT,
)
create_structure_agent = STRUCTURE_WORKER.create_graph
invoke_structure_agent = STRUCTURE_WORKER.invoke

__all__ = ["STRUCTURE_WORKER", "create_structure_agent", "invoke_structure_agent"]
