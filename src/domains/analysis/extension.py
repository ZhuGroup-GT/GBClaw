"""Pure declarations for the analysis memory domain."""

from extensions.specs import (
    AgentMemorySpec,
    DomainExtension,
    ToolMemorySpec,
    ToolProviderSpec,
    WorkerDefinition,
    WorkflowMemorySpec,
)

MEMORY_EXTENSION = DomainExtension(
    key="analysis",
    workflows=(
        WorkflowMemorySpec(
            "stress_strain_analysis",
            "stress-strain",
            "artifacts/analysis/stress_strain",
            summary_guidance="- Stress analysis: derived run ID, source shear run, selected stress field, signed/absolute mode,\n  threshold method/value, included/excluded counts, event and pre-peak rows, exact/nearest frame\n  mappings, conclusion lookup key, manifest, and JSON/PNG roles while relevant.",
        ),
        WorkflowMemorySpec(
            "neb_analysis",
            "neb-analysis",
            "artifacts/analysis/neb",
            summary_guidance="- NEB path analysis: derived run ID, source GB-NEB run and MEP artifact, derivative tolerance,\n  replica count, complete zero/one/many saddle list, highest saddle, per-saddle barriers, plotting\n  parameters, conclusion lookup key, manifest, and JSON/PNG roles while relevant.",
        ),
        WorkflowMemorySpec(
            "gb_periodicity",
            "gb-periodicity",
            "artifacts/analysis/gb_periodicity",
            query_aliases=("gb_periodicity_analysis",),
            summary_guidance="Preserve the exact source run/structure, measured periods, review-priority and alternative candidates, uncertainty, construction mismatch, analysis status, and references to candidate tables. A review candidate is not a user-selected periodic cell.",
        ),
        WorkflowMemorySpec(
            "render",
            "render",
            "artifacts/analysis/render",
            summary_guidance="- Rendering: derived run ID, source run/role/step, analysis mode, hidden structure types,\n  exact artifact IDs and original-frame lineage, purpose, exact/nearest-frame caveat, manifest,\n  PNG and render_data JSON paths. For an optional displacement overlay, retain reference\n  run/role/step/artifact and origin, current-minus-reference definition, displayed selection,\n  physical summary, minimum-image/PBC settings and arrow scaling/style. Full vectors remain in\n  the JSON. These parameters distinguish a reusable render from a different request.\n- GB plan view: also retain the exact source artifact ID, original-frame lineage through\n  conversion/minimization, inspection purpose, estimated Y boundary, slab bounds, two side\n  counts/spacings, camera and PBC settings, warnings, diagnostic JSON/profile roles and\n  conclusion lookup key. Geometric side colors do not establish crystallographic identity.\n  For displacement overlays retain the exact reference artifact and original-frame lineage,\n  current-minus-reference definition, minimum-image setting, physical displacement summary,\n  atom scale and arrow display parameters. Full selected 3D vectors stay in the JSON artifact;\n  display scaling is not a change to their physical values. Drift and strain are not subtracted.",
        ),
    ),
    tools=(
        ToolMemorySpec(
            "plot_stress_strain_comparison",
            "stress_strain_analysis",
            required_artifact_roles=(
                "stress_strain_comparison_data",
                "stress_strain_comparison_plot",
            ),
            fact_builder="domains.analysis.stress_strain.tool:stress_strain_comparison_facts",
        ),
        ToolMemorySpec(
            "plot_neb_mep",
            "neb_analysis",
            required_artifact_roles=("neb_mep_plot_data", "neb_mep_plot"),
            fact_builder="domains.analysis.neb_mep.tool:neb_plot_facts",
        ),
        ToolMemorySpec(
            "render_atomic_structure", "render", required_artifact_roles=("render", "render_data")
        ),
        ToolMemorySpec(
            "render_gb_plan_view",
            "render",
            required_artifact_roles=("gb_plan_view", "gb_plan_view_data", "gb_plan_view_profile"),
        ),
        ToolMemorySpec(
            "stress_strain_analysis",
            "stress_strain_analysis",
            required_artifact_roles=(
                "source_metadata_snapshot",
                "stress_strain_data",
                "stress_strain_plot",
            ),
        ),
        ToolMemorySpec(
            "analyze_neb_mep", "neb_analysis", required_artifact_roles=("neb_mep_analysis",)
        ),
        ToolMemorySpec(
            "analyze_gb_periodicity",
            "gb_periodicity",
            required_artifact_roles=(
                "source_metadata_snapshot",
                "gb_periodicity_data",
                "gb_periodicity_summary",
                "gb_periodicity_overview",
                "pbc_size_comparison",
                "gb_periodicity_multiscale_status",
                "gb_periodicity_multiscale_data",
                "gb_periodicity_candidate_table",
                "gb_periodicity_elastic_data",
                "gb_periodicity_strain_fft_review",
                "gb_periodicity_local_strain_data",
                "gb_periodicity_coordinate_rows_data",
            ),
            fact_builder="domains.analysis.periodicity_tools:gb_periodicity_facts",
            model_result_keys=(
                "presentation_policy",
                "summary_image_path",
                "review_priority_candidate",
                "alternative_candidates",
                "analysis_status",
                "multiscale_analysis_status",
                "selected_candidate_id",
                "requires_user_decision",
                "short_motif_candidates",
                "longer_candidates",
                "total_candidate_count",
                "long_band_observability",
                "lattice_filter",
                "pbc_size_comparison",
                "image_paths",
                "required_action",
                "retry_without_changes",
                "detailed_report_path",
                "candidate_table_path",
            ),
            model_result_builder="domains.analysis.periodicity_tools:gb_periodicity_model_result",
        ),
    ),
    agents=(
        AgentMemorySpec(
            "analysis",
            (
                "stress_strain_analysis",
                "neb_analysis",
                "gb_periodicity",
                "render",
                "gb_neb",
                "gb_shear",
                "gb_minimize",
                "structure_minimize",
                "gb_energy",
            ),
            excluded_fact_kinds=("gb_crystallography",),
        ),
    ),
    workers=(
        WorkerDefinition(
            "analysis",
            "delegate_analysis",
            "analysis",
            "domains.analysis.delegate:make_delegate_analysis_tool",
            plan_capability="analysis",
            display_name="Analysis agent",
            routing_guidance="domains.analysis.prompt:ORCHESTRATOR_ROUTING",
        ),
    ),
    tool_providers=(
        ToolProviderSpec("analysis_tools", "analysis", "domains.analysis.catalog:analysis_agent_tools"),
    ),
    search_aliases=(
        ("周期性", "gb_periodicity"),
        ("位移", "displacement"),
        ("箭头", "displacement"),
        ("应力", "stress"),
        ("应变", "strain"),
        ("绘图", "plot"),
        ("渲染", "render"),
    ),
    tool_labels=(
        ("stress_strain_analysis", "Stress–strain analysis"),
        ("plot_stress_strain_comparison", "Stress–strain comparison"),
        ("analyze_neb_mep", "NEB saddle analysis"),
        ("analyze_gb_periodicity", "GB periodicity and PBC size candidates"),
        ("plot_neb_mep", "NEB minimum-energy path"),
        ("render_atomic_structure", "Structure render"),
        ("render_gb_plan_view", "Grain-boundary plan view"),
    ),
)
