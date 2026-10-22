"""Resolve coordinate artifacts without assuming their cache location."""
from __future__ import annotations

import json
from pathlib import Path


def ptm_cache_path(coordinate_dir):
    coordinate_dir = Path(coordinate_dir)
    report = json.loads((coordinate_dir / "period_candidates.json").read_text())
    value = report.get("source", {}).get("ptm_cache")
    if value is None:
        return coordinate_dir / "ptm_cache"
    cache = Path(value)
    return cache if cache.is_absolute() else coordinate_dir / cache
