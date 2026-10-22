"""Canonical fact keys supplied by trusted domain declarations."""
from __future__ import annotations

import re

from extensions import FACT_MEMORY_SPECS, fact_memory_spec, resolve_hook


def fixed_project_lookup_key(kind: str) -> str | None:
    spec = FACT_MEMORY_SPECS.get(kind)
    return spec.fixed_lookup_key if spec is not None else None


def canonical_lookup_key(*, kind: str, requested_key: str, source_run_id: str | None) -> str:
    spec = fact_memory_spec(kind)
    key = requested_key.strip()
    if spec.fixed_lookup_key is not None:
        if key != spec.fixed_lookup_key:
            raise ValueError(f"{kind} must use canonical lookup key {spec.fixed_lookup_key!r}")
        return key
    if spec.key_pattern is not None:
        if not re.fullmatch(spec.key_pattern, key):
            raise ValueError(f"{kind} lookup key must match {spec.key_pattern!r}")
        return key
    if spec.canonical_key_policy:
        value = resolve_hook(spec.canonical_key_policy)(
            kind=kind, requested_key=key, source_run_id=source_run_id,
        )
        if not isinstance(value, str) or value != key:
            raise ValueError("Canonical key policies must validate the supplied key without changing its identity")
        return value
    raise ValueError(f"No canonical lookup-key policy for fact kind {kind!r}")


def singleton_lookup_keys() -> tuple[str, ...]:
    return tuple(spec.fixed_lookup_key for spec in FACT_MEMORY_SPECS.values() if spec.fixed_lookup_key)
