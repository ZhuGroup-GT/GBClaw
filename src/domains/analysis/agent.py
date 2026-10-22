"""Analysis worker: stress--strain analysis and OVITO rendering (does not talk to the user).

Uses the registered analysis tools in the GBclaw environment. Invoked statelessly by delegate_analysis;
results return to the orchestrator via WorkerResult.
"""

from __future__ import annotations

from agents.workers.spec import WorkerSpec
from domains.analysis.prompt import ANALYSIS_AGENT_PROMPT

ANALYSIS_WORKER = WorkerSpec(
    agent="analysis",
    label="Analysis agent",
    status_message="Analysis running…",
    prompt=ANALYSIS_AGENT_PROMPT,
)
create_analysis_agent = ANALYSIS_WORKER.create_graph
invoke_analysis_agent = ANALYSIS_WORKER.invoke

__all__ = ["ANALYSIS_WORKER", "create_analysis_agent", "invoke_analysis_agent"]
