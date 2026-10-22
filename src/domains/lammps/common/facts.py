"""Scientific memory summaries for the LAMMPS tool workflows.

Every builder returns immutable evidence candidates for the shared publisher.
Batch entries keep their exact frame, purpose and constraints separately so a
later worker can find one relaxed endpoint without re-running the whole batch.
"""

from __future__ import annotations

from typing import Any

from project_memory.models import FactRecord, RunRecord
from project_memory.publication.facts import make_run_fact


def _configuration(run: RunRecord) -> dict[str, Any]:
    return dict(run.parameters.get("canonical_config") or {})


def _parameters(run: RunRecord) -> dict[str, Any]:
    return {
        key: value for key, value in run.parameters.items()
        if key not in {"canonical_config", "memory_tool"} and not key.startswith("_")
    }


def cfg_conversion_facts(run: RunRecord, result: dict[str, Any]) -> list[FactRecord]:
    """Register the conversion batch and every converted frame independently."""
    conversions = result.get("conversions") or []
    facts = [make_run_fact(run, {
        **{key: value for key, value in result.items() if key != "conversions"},
        "summary": f"Converted {len(conversions)} CFG frames to LAMMPS atomic data; "
                   f"purpose: {result.get('purpose') or run.parameters.get('purpose', '')}",
        "parameters": _parameters(run),
        "mass_override": _configuration(run).get("mass_override"),
        "item_lookup_keys": [f"run:{run.run_id}:item:{index}" for index in range(1, len(conversions) + 1)],
    })]
    for index, item in enumerate(conversions, start=1):
        facts.append(make_run_fact(run, {
            **item,
            "item_index": index,
            "purpose": item.get("purpose") or result.get("purpose", ""),
            "coordinate_origin": result.get("coordinate_origin"),
            "atom_style": result.get("atom_style"),
            "sources": [source.model_dump(mode="json") for source in run.sources if source.ordinal == index - 1],
            "summary": f"Converted frame {item.get('source_cfg')} to {item.get('output_lmp')}",
        }, suffix=f"item:{index}"))
    return facts


def gb_minimize_facts(run: RunRecord, result: dict[str, Any]) -> list[FactRecord]:
    return [make_run_fact(run, {
        **result,
        "summary": "Constructed and relaxed a grain boundary; "
                   f"final minimization converged={result.get('final_minimization_converged')}",
        "parameters": _parameters(run),
        "configuration": _configuration(run),
        "output_roles": ["minimized_lmp", "minimized_cfg", "top_boundary_ids", "bottom_boundary_ids"],
    })]


def structure_minimize_facts(run: RunRecord, result: dict[str, Any]) -> list[FactRecord]:
    """Keep single-frame and ordered batch minimization evidence searchable."""
    configuration = _configuration(run)
    if not result.get("batch"):
        return [make_run_fact(run, {
            **result,
            "parameters": _parameters(run),
            "configuration": configuration,
            "output_roles": ["minimized_lmp", "minimized_cfg", "structure_metrics"],
        })]
    successful_items = [item for item in result.get("items", []) if item.get("status") == "completed"]
    entries = {entry["item_index"]: entry for entry in configuration.get("structures", [])}
    facts = [make_run_fact(run, {
        **{key: value for key, value in result.items() if key != "items"},
        "parameters": _parameters(run),
        "item_lookup_keys": [f"run:{run.run_id}:item:{item['item_index']}" for item in successful_items],
    })]
    for item in successful_items:
        index = item["item_index"]
        facts.append(make_run_fact(run, {
            **item,
            "batch_run_id": run.run_id,
            "box_relax": False,
            "loading": "none",
            "configuration": dict(entries.get(index, {}).get("config") or {}),
            "sources": [source.model_dump(mode="json") for source in run.sources
                        if source.metadata.get("item_index") == index],
            "summary": f"Minimized frame {item.get('source', {}).get('path')}; "
                       f"purpose: {item.get('purpose', '')}; converged={item.get('converged')}",
        }, suffix=f"item:{index}"))
    return facts


def gb_shear_facts(run: RunRecord, result: dict[str, Any]) -> list[FactRecord]:
    return [make_run_fact(run, {
        **result,
        "summary": f"Completed {result.get('loading_steps')} shear loading steps; "
                   f"load type={result.get('load_type')}; final strain={result.get('final_actual_shear_strain')}; "
                   f"nonconverged steps={result.get('nonconverged_steps')}",
        "parameters": _parameters(run),
        "configuration": _configuration(run),
        "output_roles": ["sheared_lmp", "sheared_cfg", "run_metadata", "intermediate_structure"],
    })]


def gb_neb_facts(run: RunRecord, result: dict[str, Any]) -> list[FactRecord]:
    return [make_run_fact(run, {
        **result,
        "parameters": _parameters(run),
        "configuration": _configuration(run),
        "output_roles": ["mep_json", "mep_csv", "initial_structure", "final_structure"],
    })]
