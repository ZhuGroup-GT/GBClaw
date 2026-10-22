"""Trusted publication hooks and versioned recovery contracts.

Only host-registered import paths are loaded. A saved publication must match
the active policy exactly; recovery never silently reinterprets old payloads.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from extensions import ToolMemorySpec
    from project_memory.models import FactRecord, RunRecord


class PublicationCompatibilityError(ValueError):
    """A durable publication does not match the active policy."""


def load_hook(path: str, *, positional_arguments: int, label: str) -> Callable:
    """Resolve a trusted hook and check its call shape before scientific work."""
    from extensions import resolve_hook

    try:
        hook = resolve_hook(path)
        inspect.signature(hook).bind(*([None] * positional_arguments))
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {label} hook {path!r}: {exc}") from exc
    return hook


def preflight_tool(spec: ToolMemorySpec) -> None:
    """Fail before a run/artifact is created when a registered hook is broken."""
    if spec.fact_builder:
        load_hook(spec.fact_builder, positional_arguments=2, label="fact builder")
    if spec.model_result_builder:
        load_hook(spec.model_result_builder, positional_arguments=1, label="model result builder")
    if spec.publication_validator:
        load_hook(spec.publication_validator, positional_arguments=3, label="publication validator")
    from extensions import workflow_memory_spec

    validator = workflow_memory_spec(spec.workflow).publication_validator
    if validator:
        load_hook(validator, positional_arguments=3, label="workflow publication validator")


# Hook import paths are part of each fingerprint. Hooks whose modules moved
# keep fingerprinting under the path that publications recorded before the
# move, so existing evidence stays verifiable. Add an entry when a hook moves
# without a contract change.
_RECORDED_HOOK_PATHS = {
    "domains.lammps.common.facts:cfg_conversion_facts": "tools.lammps.memory_adapter:cfg_conversion_facts",
    "domains.lammps.common.facts:gb_minimize_facts": "tools.lammps.memory_adapter:gb_minimize_facts",
    "domains.lammps.common.facts:structure_minimize_facts": "tools.lammps.memory_adapter:structure_minimize_facts",
    "domains.lammps.common.facts:gb_shear_facts": "tools.lammps.memory_adapter:gb_shear_facts",
    "domains.lammps.common.facts:gb_neb_facts": "tools.lammps.memory_adapter:gb_neb_facts",
    "domains.analysis.stress_strain.tool:stress_strain_comparison_facts":
        "tools.analysis.analysis_tools:stress_strain_comparison_facts",
    "domains.analysis.neb_mep.tool:neb_plot_facts": "tools.analysis.analysis_tools:neb_plot_facts",
    "domains.analysis.periodicity_tools:gb_periodicity_facts": "tools.analysis.gb_periodicity_tools:gb_periodicity_facts",
    "domains.coding.run:coding_result_facts": "tools.coding.coding_run:coding_result_facts",
}


def contract_reference(spec: ToolMemorySpec) -> dict:
    """Version plus a stable hash catches accidental changes without a version bump."""
    from extensions import workflow_memory_spec

    policy = {name: getattr(spec, name) for name in (
        "key", "workflow", "conclusion_lookup_key", "required_artifact_roles",
        "fact_builder", "fact_kinds", "shared_fact_keys", "publication_validator",
        "contract_version",
    )}
    # Include the workflow-level evidence guard in the durable contract.
    policy["workflow_publication_validator"] = workflow_memory_spec(spec.workflow).publication_validator
    for name in ("fact_builder", "publication_validator", "workflow_publication_validator"):
        policy[name] = _RECORDED_HOOK_PATHS.get(policy[name], policy[name])
    digest = hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"contract_version": spec.contract_version, "contract_sha256": digest}


def resolve_publication_spec(memory_tool: str, reference: dict) -> ToolMemorySpec:
    from extensions import tool_memory_spec

    version = reference.get("contract_version")
    if type(version) is not int or version < 1:
        raise PublicationCompatibilityError("Missing or invalid publication contract_version; recovery is unsupported")
    try:
        spec = tool_memory_spec(memory_tool)
    except ValueError as exc:
        raise PublicationCompatibilityError(
            f"Publication contract {memory_tool!r} v{version} is unavailable"
        ) from exc
    expected = reference.get("contract_sha256")
    if version != spec.contract_version or expected != contract_reference(spec)["contract_sha256"]:
        raise PublicationCompatibilityError(
            f"Publication contract {memory_tool!r} v{version} changed or lacks its fingerprint; "
            "recovery requires the original policy"
        )
    return spec


def validate_source_publication(
    run: RunRecord, result: dict, facts: list[FactRecord], *, spec: ToolMemorySpec | None = None,
) -> None:
    """Apply the same trusted policy to all supported fact-writing paths."""
    from extensions import tool_memory_spec, workflow_memory_spec

    hooks = []
    workflow_hook = workflow_memory_spec(run.workflow).publication_validator
    if workflow_hook:
        hooks.append(workflow_hook)
    if spec is None:
        publication = result.get("memory_publication")
        memory_tool = run.parameters.get("memory_tool")
        if isinstance(publication, dict) and publication.get("memory_tool"):
            if memory_tool and memory_tool != publication["memory_tool"]:
                raise ValueError("Publication tool differs from its source run")
            spec = resolve_publication_spec(publication["memory_tool"], publication)
        elif memory_tool:
            reference = run.parameters.get("_memory_contract")
            spec = (resolve_publication_spec(memory_tool, reference)
                    if isinstance(reference, dict) else tool_memory_spec(memory_tool))
    if spec is not None:
        if spec.workflow != run.workflow.value:
            raise ValueError("Publication tool/workflow identity mismatch")
        validate_tool_fact_batch(run, spec, facts)
        if spec.publication_validator:
            hooks.append(spec.publication_validator)
    for path in dict.fromkeys(hooks):
        hook = load_hook(path, positional_arguments=3, label="publication validator")
        # Hooks validate snapshots and cannot mutate the pending persisted payload.
        hook(run.model_copy(deep=True), json.loads(json.dumps(result)),
             [fact.model_copy(deep=True) for fact in facts])


def validate_tool_fact_batch(
    run: RunRecord, spec: ToolMemorySpec, facts: list[FactRecord], *, require_conclusion: bool = False,
) -> None:
    """Enforce tool ownership at both staging and the public atomic writer."""
    from project_memory.models import FactEvidenceKind, FactRecord, FactStatus

    if not isinstance(facts, list) or any(not isinstance(fact, FactRecord) for fact in facts):
        raise ValueError("memory_facts must be a list of FactRecord entries")
    main_key = spec.conclusion_lookup_key and spec.conclusion_lookup_key.format(run_id=run.run_id)
    if require_conclusion and main_key and not any(
        fact.kind == "scientific_conclusion" and fact.lookup_key == main_key for fact in facts
    ):
        raise ValueError(f"Tool {spec.key} requires its declared main conclusion")
    keys, identities = set(), set()
    for fact in facts:
        key = (fact.kind, fact.lookup_key)
        if key in keys or fact.fact_id in identities:
            raise ValueError("Duplicate memory fact key or identity in a tool result")
        keys.add(key)
        identities.add(fact.fact_id)
        if (fact.kind not in spec.fact_kinds or fact.source_run_id != run.run_id
                or fact.source_turn_id is not None or not fact.fact_id
                or fact.status is not FactStatus.ACTIVE
                or fact.evidence_kind is not FactEvidenceKind.DETERMINISTIC_WORKFLOW):
            raise ValueError("Publication fact identity or evidence mismatch")
        if not fact.lookup_key.strip() or len(fact.lookup_key) > 240:
            raise ValueError("Invalid publication fact lookup key")
        if not spec.shared_fact_keys and not (
            fact.lookup_key.startswith(f"run:{run.run_id}:") or fact.lookup_key == main_key
        ):
            raise ValueError("Tool fact key must be scoped to its source run")
        json.dumps(fact.value, ensure_ascii=False, allow_nan=False)
