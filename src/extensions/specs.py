"""Immutable declarations shared by domains and runtime consumers.

Extension modules import this module directly. Declarations contain import paths,
never live storage/model/tool objects, so collecting them cannot create cycles.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DEFAULT_WORKER_EXCLUDED_FACT_KINDS = ()


@dataclass(frozen=True)
class FactMemorySpec:
    key: str
    writer: Literal[
        "run_publication",
        "project_plan",
        "conversation_extraction",
        "agent_conclusion",
        "task_state",
    ] = "run_publication"
    extraction_policy: Literal["deterministic", "user_explicit", "completed_run"] = "deterministic"
    fixed_lookup_key: str | None = None
    key_pattern: str | None = None
    canonical_key_policy: str | None = None
    context_priority: int = 0
    search_aliases: tuple[tuple[str, str], ...] = ()
    extraction_hint: str = ""
    summary_guidance: str = ""


@dataclass(frozen=True)
class WorkflowMemorySpec:
    key: str
    run_prefix: str
    artifact_directory: str
    canonical_config: bool = False
    query_aliases: tuple[str, ...] = ()
    publication_validator: str | None = None
    summary_guidance: str = ""
    search_aliases: tuple[tuple[str, str], ...] = ()
    # Run-relative scratch directories; durable artifacts must never live here.
    ephemeral_directories: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentMemorySpec:
    key: str
    recall_workflows: tuple[str, ...] = ()
    all_workflows: bool = False
    artifact_access: Literal["read", "metadata"] = "read"
    excluded_fact_kinds: tuple[str, ...] = DEFAULT_WORKER_EXCLUDED_FACT_KINDS
    report_artifact_usage: bool = False
    query_providers: tuple[str, ...] = ()

    @property
    def preferred_workflows(self):
        from . import Workflow

        return (
            tuple(Workflow)
            if self.all_workflows
            else tuple(Workflow(key) for key in self.recall_workflows)
        )


@dataclass(frozen=True)
class ToolMemorySpec:
    key: str
    workflow: str
    conclusion_lookup_key: str | None = "run:{run_id}:main_conclusion"
    required_artifact_roles: tuple[str, ...] = ()
    fact_builder: str | None = None
    fact_kinds: tuple[str, ...] = ("scientific_conclusion",)
    shared_fact_keys: bool = False
    model_result_keys: tuple[str, ...] = ()
    model_result_builder: str | None = None
    publication_validator: str | None = None
    contract_version: int = 1


@dataclass(frozen=True)
class MemoryWriterSpec:
    key: str
    fact_kinds: tuple[str, ...]
    evidence_kinds: tuple[str, ...]
    mutation_mode: Literal["cas", "extraction"] = "cas"


@dataclass(frozen=True)
class QueryProviderSpec:
    """factory(store) -> BaseTool with name == key, loaded after bootstrap."""

    key: str
    factory: str


@dataclass(frozen=True)
class ToolProviderSpec:
    """factory() -> Sequence[BaseTool]: business tools for one worker's graph.

    Providers for the same worker contribute in registry order, so an
    extension can add tools to a built-in worker without editing it.
    """

    key: str
    worker: str
    factory: str


@dataclass(frozen=True)
class OrchestratorToolsSpec:
    """factory(*, project_store) -> OrchestratorTools, called once per orchestrator.

    The returned tools join the orchestrator's own tools in registry order:
    ``"leading"`` before the host's memory, planning and web-search tools,
    ``"trailing"`` after them, ahead of the delegates. Its ``worker_options``
    are extra keyword arguments for the domain's delegate factories, so a tool
    and a delegate can share one object such as a resource pool.
    """

    key: str
    factory: str
    placement: Literal["leading", "trailing"] = "trailing"


@dataclass(frozen=True)
class WorkerDefinition:
    agent: str
    delegate_tool: str
    endpoint_role: str
    factory: str
    execution_kind: Literal["scientific", "planning", "read"] = "scientific"
    plan_capability: str | None = None
    display_name: str = ""
    # Embedding this declaration avoids a second registration for new workers.
    memory: AgentMemorySpec | None = None
    # "module:ATTRIBUTE" of a str: this worker's section of the orchestrator's
    # capability-routing guidance, appended in registry order.
    routing_guidance: str | None = None
    # "module:ATTRIBUTE" of a str: rules the planning agent must follow when it
    # plans this worker's steps, added to its prompt in registry order.
    planning_guidance: str | None = None


@dataclass(frozen=True)
class ToolCapabilitySpec:
    """Host-owned execution/visibility policy, independent of fact publication.

    ``model_view`` is an optional ``"module:callable"`` taking the tool's reply
    text and returning a bounded dict the model sees instead of a generic
    preview when the reply is large, or None to keep the generic preview. The
    reply itself, its archive and any saved artifact are unchanged.
    """

    key: str
    operation: Literal["query", "configuration", "scientific", "interaction"] = "query"
    plan_tracking: bool = False
    memory_read: bool = False
    preserve_final_stream: bool = False
    plan_effect: Literal["none", "draft", "approve", "reject", "update"] = "none"
    memory_context: bool = False
    display_name: str = ""
    model_view: str | None = None


@dataclass(frozen=True)
class DomainExtension:
    """One domain's declarations, collected before any dynamic model is built."""

    key: str
    facts: tuple[FactMemorySpec, ...] = ()
    workflows: tuple[WorkflowMemorySpec, ...] = ()
    tools: tuple[ToolMemorySpec, ...] = ()
    agents: tuple[AgentMemorySpec, ...] = ()
    workers: tuple[WorkerDefinition, ...] = ()
    query_providers: tuple[QueryProviderSpec, ...] = ()
    capabilities: tuple[ToolCapabilitySpec, ...] = ()
    search_aliases: tuple[tuple[str, str], ...] = ()
    tool_providers: tuple[ToolProviderSpec, ...] = ()
    orchestrator_tools: tuple[OrchestratorToolsSpec, ...] = ()
    # (tool name, display name) for streams, traces and the Web UI.
    tool_labels: tuple[tuple[str, str], ...] = ()
    # "module:attribute" hooks used by the hosts (API and CLI):
    routers: tuple[str, ...] = ()  # FastAPI APIRouter objects mounted by the HTTP API
    startup: tuple[str, ...] = ()  # zero-argument callables run once when a host starts
    shutdown: tuple[str, ...] = ()  # zero-argument callables run when it stops
    llm_providers: tuple[str, ...] = ()  # zero-argument factories returning a ProviderAdapter
