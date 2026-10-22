"""Pure declarations for the structure memory domain."""

from extensions.specs import (
    AgentMemorySpec,
    DomainExtension,
    FactMemorySpec,
    QueryProviderSpec,
    ToolCapabilitySpec,
    ToolMemorySpec,
    ToolProviderSpec,
    WorkerDefinition,
    WorkflowMemorySpec,
)

MEMORY_EXTENSION = DomainExtension(
    key="structure",
    facts=(
        FactMemorySpec(
            "csl_orientation", key_pattern=r"^gb:[A-Za-z0-9][A-Za-z0-9_-]{0,79}:orientation$"
        ),
        FactMemorySpec("gb_crystallography", key_pattern=r"^gb:crystallography:[0-9a-f]{24}$"),
        FactMemorySpec(
            "gb_identity",
            "conversation_extraction",
            "user_explicit",
            key_pattern=r"^gb:[A-Za-z0-9][A-Za-z0-9_-]{0,79}:(?:identity|plane_selection)$",
        ),
    ),
    workflows=(
        WorkflowMemorySpec(
            "csl",
            "csl",
            "artifacts/structure",
            summary_guidance="- CSL enumeration/screening: axis, basis, Sigma bound, plane-index bound, ``gb_type``, requested\n  rotation branches as ``(Sigma, rotation_m, rotation_n, angle)``, selected/requested GB1/GB2 rows,\n  and the run/artifact reference holding the full table. Never merge two branches that share Sigma.\n  For near-CSL results, retain source pair provenance, thresholds, selected concrete planes,\n  deviations, snapped angle/delta-angle, and the caveat that a changed pair is not proven to keep\n  the source Sigma. For reverse general→CSL lookup, retain the input ordered planes, derived\n  misorientation, selected reference Sigma/angle/delta, selected exact plane pair and both\n  deviations, search bounds, and the caveat that a nonzero-deviation Sigma is a reference only.\n  Do not store an unselected angle-only diagnostic as the nearest reference. Keep these reference\n  fields presentation-only; never promote them to actionable GB orientation, period, or size facts.\n- Deterministic ``gb_crystallography`` facts: preserve the actual ordered planes, basis, tilt axis,\n  misorientation, exact-Sigma status, descriptive reference, source run/artifact, and the complete\n  ``downstream_policy``. These facts describe one normalized geometry only; do not apply one\n  geometry's analysis to another GB or promote an informational near-CSL reference into build\n  input.\n- Exact CSL tilt orientation: lookup key, axis, basis, Sigma, GB plane, top/bottom x/y/z integers,\n  exact ``rotation_m``/``rotation_n`` branch, and period lengths.",
        ),
        WorkflowMemorySpec(
            "dichromatic_map",
            "dichromatic-map",
            "artifacts/structure/dichromatic_map",
            summary_guidance="Preserve selected point IDs, cell geometry, source run/artifacts, strain transform, residuals, boundary vectors, purpose and interaction state. A preview is not a completed scientific result.",
        ),
    ),
    tools=(
        ToolMemorySpec(
            "csl",
            "csl",
            conclusion_lookup_key=None,
            fact_kinds=("gb_crystallography", "csl_orientation"),
            shared_fact_keys=True,
        ),
        ToolMemorySpec(
            "render_dichromatic_map",
            "dichromatic_map",
            conclusion_lookup_key="dichromatic_map:{run_id}",
            required_artifact_roles=("dichromatic_map_png", "dichromatic_map_data"),
        ),
        ToolMemorySpec(
            "select_dichromatic_cell",
            "dichromatic_map",
            required_artifact_roles=("config", "dichromatic_cell_selection"),
        ),
        ToolMemorySpec(
            "apply_dichromatic_cell_strain",
            "dichromatic_map",
            required_artifact_roles=("config", "dichromatic_map_png", "dichromatic_map_data"),
        ),
        ToolMemorySpec(
            "analyze_dichromatic_cell_edges",
            "dichromatic_map",
            required_artifact_roles=("config", "dichromatic_map_png", "dichromatic_map_data"),
        ),
    ),
    agents=(
        AgentMemorySpec(
            "structure",
            ("csl", "dichromatic_map", "gb_periodicity"),
            query_providers=("query_csl_orientation",),
            excluded_fact_kinds=("gb_crystallography",),
        ),
    ),
    workers=(
        WorkerDefinition(
            "structure",
            "delegate_csl",
            "structure",
            "domains.structure.delegate:make_delegate_structure_tool",
            plan_capability="csl",
            display_name="Structure agent",
            routing_guidance="domains.structure.prompt:ORCHESTRATOR_ROUTING",
        ),
    ),
    query_providers=(
        QueryProviderSpec(
            "query_csl_orientation", "domains.structure.memory_query:make_query_csl_orientation_tool"
        ),
    ),
    routers=("domains.structure.routes:router",),
    # The CSL tools use the shared process pool; start its workers before the first request.
    startup=("core.execution.process_pool:warmup_process_pool",),
    shutdown=("core.execution.process_pool:shutdown_process_pool",),
    tool_providers=(
        ToolProviderSpec("structure_tools", "structure", "domains.structure.catalog:structure_agent_tools"),
    ),
    search_aliases=(
        ("晶界", "boundary"),
        ("取向", "orientation"),
        ("晶向", "axis"),
        ("构型", "structure"),
    ),
    tool_labels=(
        ("get_dichromatic_map_options", "Dichromatic map options"),
        ("render_dichromatic_map", "Dichromatic map"),
        ("prepare_dichromatic_cell_selection", "Choose a near-CSL cell"),
        ("get_dichromatic_cell_selection", "Saved near-CSL selection"),
        ("apply_dichromatic_cell_strain", "Align the selected near-CSL cell"),
        ("analyze_dichromatic_cell_edges", "Measure selected-cell edge vectors"),
        ("query_csl_orientation", "Saved CSL orientation"),
        ("list_csl_sigmas", "CSL Σ lookup"),
        ("list_csl_gb_planes", "GB plane lookup"),
        ("list_csl_gb_planes_batch", "GB planes (batch)"),
        ("get_tilt_gb_orientation", "Tilt GB orientation"),
        ("analyze_tilt_geometry", "Tilt geometry analysis"),
        ("compare_miller_planes", "Miller plane comparison"),
        ("score_csl_tilt_plane_pairs", "CSL plane-pair scoring"),
        ("suggest_low_index_near_csl_planes", "Low-index near-CSL candidates"),
        ("match_csl_tilt_references", "Near-CSL reference search"),
    ),
    # Large fixed-width plane tables reach the model as compact rows; the text
    # table remains the reply, the trace and the saved artifact.
    capabilities=tuple(
        ToolCapabilitySpec(name, model_view="domains.structure.csl.tools:plane_table_model_view")
        for name in ("list_csl_gb_planes", "list_csl_gb_planes_batch")
    ),
)
