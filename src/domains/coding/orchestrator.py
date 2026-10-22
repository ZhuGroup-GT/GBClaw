"""The coding environment catalog for the orchestrator, sharing its objects with delegate_coding."""

from __future__ import annotations

from agents.orchestrator.contributions import OrchestratorTools
from domains.coding.environments import load_coding_environment_registry, make_list_coding_environments_tool
from domains.coding.resources import get_coding_resource_pool
from domains.coding.settings import load_coding_config


def coding_orchestrator_tools(*, project_store=None) -> OrchestratorTools:
    """``list_coding_environments`` and the registry, limits and pool the delegate uses too."""
    environments = load_coding_environment_registry()
    config = load_coding_config()
    resources = get_coding_resource_pool(config.total_compute_slots)
    return OrchestratorTools(
        tools=(make_list_coding_environments_tool(environments, resource_limits=config.public_resource_limits()),),
        worker_options={"coding": {
            "environment_registry": environments,
            "coding_config": config,
            "resource_pool": resources,
        }},
    )
