"""Pure declarations for the core memory domain."""

from extensions.specs import (
    AgentMemorySpec,
    DomainExtension,
    FactMemorySpec,
    ToolCapabilitySpec,
    WorkerDefinition,
)

MEMORY_EXTENSION = DomainExtension(
    key="core",
    facts=(
        FactMemorySpec(
            "project_goal",
            "conversation_extraction",
            "user_explicit",
            fixed_lookup_key="project:primary_goal",
            context_priority=20,
        ),
        FactMemorySpec(
            "user_decision",
            "conversation_extraction",
            "user_explicit",
            key_pattern=r"^project:decision:[a-z0-9][a-z0-9_-]{0,79}$",
            context_priority=10,
        ),
        FactMemorySpec(
            "scientific_conclusion",
            "run_publication",
            "completed_run",
            canonical_key_policy="extensions.core:scientific_conclusion_key",
            extraction_hint="Use run:<source_run_id>:conclusion:<lowercase_type> only for a new interpretation not already recorded by a scientific tool.",
        ),
        FactMemorySpec("project_plan", "project_plan"),
        FactMemorySpec("agent_conclusion", "agent_conclusion"),
        FactMemorySpec("task_state", "task_state"),
    ),
    agents=(
        AgentMemorySpec("plan", all_workflows=True, excluded_fact_kinds=("gb_crystallography",)),
        AgentMemorySpec(
            "orchestrator",
            all_workflows=True,
            report_artifact_usage=True,
            excluded_fact_kinds=("gb_crystallography",),
        ),
    ),
    workers=(
        WorkerDefinition(
            "plan",
            "delegate_plan",
            "planning",
            "agents.planner.delegate:make_delegate_plan_tool",
            execution_kind="planning",
            display_name="Planning agent",
        ),
    ),
    capabilities=(
        ToolCapabilitySpec(
            "get_project_plan", memory_read=True, memory_context=True, preserve_final_stream=True
        ),
        ToolCapabilitySpec(
            "get_project_plan_draft",
            memory_read=True,
            memory_context=True,
            preserve_final_stream=True,
        ),
        ToolCapabilitySpec(
            "query_project_memory",
            memory_read=True,
            memory_context=True,
            preserve_final_stream=True,
        ),
        ToolCapabilitySpec(
            "search_project_memory",
            memory_read=True,
            memory_context=True,
            preserve_final_stream=True,
        ),
        ToolCapabilitySpec(
            "list_run_artifacts", memory_read=True, memory_context=True, preserve_final_stream=True
        ),
        ToolCapabilitySpec(
            "read_run_artifact", memory_read=True, memory_context=True, preserve_final_stream=True
        ),
        ToolCapabilitySpec(
            "read_tool_output", memory_read=True, memory_context=True, preserve_final_stream=True
        ),
        ToolCapabilitySpec(
            "read_agent_conclusion",
            memory_read=True,
            memory_context=True,
            preserve_final_stream=True,
        ),
        ToolCapabilitySpec(
            "get_task_state", memory_read=True, memory_context=True, preserve_final_stream=True
        ),
        ToolCapabilitySpec(
            "report_artifact_usage",
            memory_read=True,
            memory_context=True,
            preserve_final_stream=True,
        ),
        ToolCapabilitySpec(
            "create_project_plan_draft",
            operation="interaction",
            plan_effect="draft",
            memory_context=True,
            preserve_final_stream=False,
        ),
        ToolCapabilitySpec(
            "revise_project_plan_draft",
            operation="interaction",
            plan_effect="draft",
            memory_context=True,
            preserve_final_stream=False,
        ),
        ToolCapabilitySpec(
            "approve_project_plan_draft",
            operation="interaction",
            plan_effect="approve",
            memory_context=True,
            preserve_final_stream=False,
        ),
        ToolCapabilitySpec(
            "reject_project_plan_draft",
            operation="interaction",
            plan_effect="reject",
            memory_context=True,
            preserve_final_stream=False,
        ),
        ToolCapabilitySpec(
            "update_project_plan_step",
            operation="interaction",
            plan_effect="update",
            memory_context=True,
            preserve_final_stream=True,
        ),
    ),
    search_aliases=(("记忆", "memory"), ("计划", "project_plan")),
    tool_labels=(
        ("create_project_plan_draft", "Draft project plan"),
        ("revise_project_plan_draft", "Draft plan revision"),
        ("get_project_plan", "Project plan"),
        ("get_project_plan_draft", "Plan draft"),
        ("approve_project_plan_draft", "Approve plan draft"),
        ("reject_project_plan_draft", "Reject plan draft"),
        ("update_project_plan_step", "Update plan step"),
        ("query_project_memory", "Project memory"),
        ("search_project_memory", "Memory search"),
        ("list_run_artifacts", "Run artifacts"),
        ("read_run_artifact", "Read artifact"),
        ("report_artifact_usage", "Artifact usage"),
        ("search_web", "Web search"),
    ),
)


def scientific_conclusion_key(*, kind: str, requested_key: str, source_run_id: str | None) -> str:
    """Keep extracted conclusions scoped to the run that supplies their evidence."""
    import re

    key = requested_key.strip()
    if not source_run_id:
        raise ValueError("scientific_conclusion requires source_run_id")
    if key == f"run:{source_run_id}:main_conclusion":
        return key
    prefix = f"run:{source_run_id}:conclusion:"
    if not key.startswith(prefix):
        raise ValueError("scientific_conclusion lookup key must be scoped to source_run_id")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", key[len(prefix) :]):
        raise ValueError("scientific conclusion type must be a lowercase slug")
    return key
