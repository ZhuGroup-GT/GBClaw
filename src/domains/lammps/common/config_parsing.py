"""Validation helpers for JSON workflow configs (stdlib only; engines import them)."""

from __future__ import annotations

from typing import Any


def config_object(data: Any, *, label: str, required: bool = False) -> dict[str, Any]:
    if data is None and not required:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be an object")
    return data


def nonempty_text(value: Any, *, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text
