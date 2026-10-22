"""JSON encoding shared by memory tables."""

from __future__ import annotations

import json
from typing import Any


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
