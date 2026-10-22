"""CSL-specific exact memory queries, loaded only for registered consumers."""
from __future__ import annotations

import json
from langchain_core.tools import BaseTool, tool
from project_memory.models import RunStatus
from project_workspace.store import ProjectStore
from core.util.graph_config import runtime_project_id
from domains.structure.csl import generator as csl
from domains.structure.csl.recorder import csl_orientation_lookup_key


def make_query_csl_orientation_tool(store: ProjectStore) -> BaseTool:
    """Bind a typed CSL-orientation lookup that does not require key guessing."""

    @tool
    def query_csl_orientation(
        basis: str,
        u: int,
        v: int,
        w: int,
        sigma: int,
        gb_plane: list[int],
        rotation_m: int | None = None,
        rotation_n: int | None = None,
    ) -> str:
        """Query one saved exact CSL rotation/plane orientation.

        Pass rotation_m and rotation_n from list_csl_sigmas whenever Sigma has
        multiple branches on the requested axis.
        """
        project_id = runtime_project_id()
        if not project_id:
            return "No active project_id in run config."
        if len(gb_plane) != 3:
            return "gb_plane must contain exactly three integers"
        try:
            _theta, resolved_m, resolved_n = csl.resolve_csl_rotation(
                [u, v, w],
                basis,
                sigma,
                rotation_m=rotation_m,
                rotation_n=rotation_n,
            )
            lookup_key = csl_orientation_lookup_key(
                basis=basis,
                axis=[u, v, w],
                sigma=sigma,
                plane=gb_plane,
                rotation_m=resolved_m,
                rotation_n=resolved_n,
            )
            memory = store.memory(project_id)
            rows = memory.query_facts(
                kind="csl_orientation",
                lookup_key=lookup_key,
                limit=1,
            )
            legacy_lookup_key = None
            if not rows:
                legacy_lookup_key = csl_orientation_lookup_key(
                    basis=basis,
                    axis=[u, v, w],
                    sigma=sigma,
                    plane=gb_plane,
                )
                legacy_rows = memory.query_facts(
                    kind="csl_orientation",
                    lookup_key=legacy_lookup_key,
                    limit=10,
                )
                rows = [
                    item
                    for item in legacy_rows
                    if int(item.value.get("m", -1)) == resolved_m
                    and int(item.value.get("n", -1)) == resolved_n
                ][:1]
            # Historical orientation facts could be written while the worker
            # was running. Only a completed source supports reusable geometry.
            verified_rows = []
            for item in rows:
                if not item.source_run_id:
                    continue
                try:
                    source = memory.get_run(item.source_run_id)
                except ValueError:
                    continue
                if source.status is RunStatus.COMPLETED:
                    verified_rows.append(item)
            rows = verified_rows
        except (TypeError, ValueError) as exc:
            return str(exc)
        return json.dumps(
            {
                "entity": "csl_orientation",
                "lookup_key": lookup_key,
                "rotation_m": resolved_m,
                "rotation_n": resolved_n,
                "legacy_lookup_key_checked": legacy_lookup_key,
                "facts": [item.model_dump(mode="json") for item in rows],
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    return query_csl_orientation

