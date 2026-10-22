"""Pure declarations for the coding memory domain."""

from extensions.specs import (
    AgentMemorySpec,
    DomainExtension,
    OrchestratorToolsSpec,
    ToolCapabilitySpec,
    ToolMemorySpec,
    WorkerDefinition,
    WorkflowMemorySpec,
)

MEMORY_EXTENSION = DomainExtension(
    key="coding",
    workflows=(
        WorkflowMemorySpec(
            "code",
            "code",
            "artifacts/code",
            publication_validator="project_memory.publication.validators:validate_coding_publication",
            ephemeral_directories=("workspace", ".sandbox-tmp"),
            summary_guidance="- Coding: code run ID, exact source relationships/input artifact snapshots, task and expected\n  outputs, selected runtime-environment alias/capabilities/fingerprint, compute-slot/MPI-rank\n  allocation, completion/failure, validation commands/status, manifest, and published\n  script/data/plot/report roles. Unpublished\n  workspace scratch is intentionally absent and must not be reconstructed as durable evidence.",
        ),
    ),
    tools=(
        ToolMemorySpec(
            "publish_coding_result",
            "code",
            required_artifact_roles=("config",),
            fact_builder="domains.coding.run:coding_result_facts",
            publication_validator="project_memory.publication.validators:validate_coding_publication",
        ),
    ),
    agents=(
        AgentMemorySpec(
            "coding",
            (
                "code",
                "stress_strain_analysis",
                "neb_analysis",
                "gb_periodicity",
                "gb_shear",
                "gb_neb",
                "gb_minimize",
                "structure_minimize",
                "gb_energy",
            ),
            artifact_access="metadata",
            excluded_fact_kinds=("gb_crystallography",),
        ),
    ),
    workers=(
        WorkerDefinition(
            "coding",
            "delegate_coding",
            "coding",
            "domains.coding.delegate:make_delegate_coding_tool",
            plan_capability="coding",
            display_name="Coding agent",
            routing_guidance="domains.coding.prompt:ORCHESTRATOR_ROUTING",
        ),
    ),
    capabilities=(ToolCapabilitySpec("list_coding_environments", preserve_final_stream=True),),
    orchestrator_tools=(
        OrchestratorToolsSpec("coding_environments", "domains.coding.orchestrator:coding_orchestrator_tools",
                              placement="leading"),
    ),
    tool_labels=(
        ("execute", "Sandbox command"),
        ("ls", "List sandbox files"),
        ("read_file", "Read sandbox file"),
        ("write_file", "Write sandbox file"),
        ("edit_file", "Edit sandbox file"),
        ("glob", "Find sandbox files"),
        ("grep", "Search sandbox files"),
        ("publish_coding_result", "Publish coding result"),
        ("list_coding_environments", "Coding environments"),
    ),
)
