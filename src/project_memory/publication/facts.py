"""Small fact builders shared by deterministic tool memory adapters.

Builders describe evidence only. RunService validates, stages and publishes the
returned records; tools must not write these records to the memory repository.
"""

from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, uuid5

from project_memory.models import FactRecord, RunRecord


def make_run_fact(
    run: RunRecord,
    value: dict[str, Any],
    *,
    suffix: str = "main_conclusion",
) -> FactRecord:
    """Build one stable, run-owned conclusion with its durable evidence links.

    An item adapter may provide a smaller ``sources`` list for that particular
    item; otherwise all ordered run sources are included. The manifest and input
    config retain complete artifacts and parameters, including bulky data.
    """
    lookup_key = f"run:{run.run_id}:{suffix}"
    return FactRecord(
        fact_id=str(uuid5(NAMESPACE_URL, f"scientific_conclusion:{lookup_key}")),
        kind="scientific_conclusion",
        lookup_key=lookup_key,
        source_run_id=run.run_id,
        value={
            "source_run_id": run.source_run_id,
            "source_fingerprint": run.source_fingerprint,
            "sources": [item.model_dump(mode="json") for item in run.sources],
            **value,
            "run_id": run.run_id,
            "workflow": run.workflow.value,
            "manifest": run.manifest_path,
            "config_path": run.config_path,
        },
        created_at=run.updated_at,
        updated_at=run.updated_at,
    )
