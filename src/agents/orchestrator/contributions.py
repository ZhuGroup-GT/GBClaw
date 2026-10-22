"""Tools that domains add to the orchestrator (``DomainExtension.orchestrator_tools``)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool

from extensions import get_registry, resolve_hook, worker_definition
from project_workspace.store import ProjectStore


@dataclass(frozen=True)
class OrchestratorTools:
    """What an orchestrator-tools factory returns.

    ``worker_options`` maps a worker agent to extra keyword arguments for its
    delegate factory.
    """

    tools: Sequence[BaseTool] = ()
    worker_options: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class DomainOrchestratorTools:
    leading: list[BaseTool]
    trailing: list[BaseTool]
    worker_options: dict[str, dict[str, Any]]


def build_domain_orchestrator_tools(project_store: ProjectStore | None) -> DomainOrchestratorTools:
    """Call every declared factory once, in registry order."""
    placed: dict[str, list[BaseTool]] = {"leading": [], "trailing": []}
    worker_options: dict[str, dict[str, Any]] = {}
    for spec in get_registry().orchestrator_tools.values():
        result = resolve_hook(spec.factory)(project_store=project_store)
        if not isinstance(result, OrchestratorTools):
            raise TypeError(f"Orchestrator tools factory {spec.factory!r} must return OrchestratorTools")
        placed[spec.placement].extend(result.tools)
        for agent, options in result.worker_options.items():
            worker_definition(agent)
            if agent in worker_options:
                raise ValueError(f"Several orchestrator tools factories pass options to worker {agent!r}")
            worker_options[agent] = dict(options)
    return DomainOrchestratorTools(placed["leading"], placed["trailing"], worker_options)


__all__ = ["DomainOrchestratorTools", "OrchestratorTools", "build_domain_orchestrator_tools"]
