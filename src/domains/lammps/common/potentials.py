"""Load registered interatomic potentials for the LAMMPS worker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from domains.lammps.common.settings import POTENTIALS_METADATA, REPO_ROOT

METADATA_PATH = POTENTIALS_METADATA


def _catalog_file_value(file_value: str) -> str | None:
    """Return the unique catalog file matched by an exact value or bare alias."""
    raw = str(file_value or "").strip()
    if not raw:
        return None

    candidate = Path(raw).expanduser()
    # An absolute path is always an explicit user path, even when it does not
    # exist yet or has the same basename as a registered potential.
    if candidate.is_absolute():
        return None

    exact_matches: list[str] = []
    alias_matches: list[str] = []
    for record in load_potentials_metadata().get("potentials") or []:
        if not isinstance(record, dict):
            continue
        catalog_file = str(record.get("file") or "").strip()
        if not catalog_file:
            continue
        reference = str(record.get("reference") or "").strip()
        if raw == catalog_file:
            exact_matches.append(catalog_file)
        elif candidate.name == raw and raw in {
            Path(catalog_file).name,
            reference,
        }:
            alias_matches.append(catalog_file)

    unique_exact_matches = list(dict.fromkeys(exact_matches))
    if len(unique_exact_matches) == 1:
        return unique_exact_matches[0]

    unique_alias_matches = list(dict.fromkeys(alias_matches))
    if len(unique_alias_matches) == 1:
        return unique_alias_matches[0]
    if len(unique_alias_matches) > 1:
        choices = ", ".join(unique_alias_matches)
        raise ValueError(
            f"Ambiguous potential alias {raw!r}; matches: {choices}. "
            "Use the exact catalog file value or an explicit path."
        )
    return None


def resolve_potential_file_for_config(file_value: str) -> Path:
    """Resolve a catalog/custom potential to an absolute path for config writing.

    Registered aliases are first replaced by the ``file`` value from
    ``potentials.json``. Relative catalog paths are resolved only against the
    metadata directory. Explicit relative custom paths are resolved only against
    the repository root. This separation prevents same-named files in the two
    locations from changing the selected potential.
    """
    raw = str(file_value or "").strip()
    catalog_file = _catalog_file_value(raw)
    normalized = catalog_file or raw
    candidate = Path(normalized).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()

    if catalog_file is not None:
        return (METADATA_PATH.parent / candidate).resolve()
    return (REPO_ROOT / candidate).resolve()


def load_potentials_metadata() -> dict[str, Any]:
    if not METADATA_PATH.is_file():
        return {"version": 1, "default_id": "", "potentials": []}
    data = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{METADATA_PATH} root must be a JSON object")
    potentials = data.get("potentials")
    if not isinstance(potentials, list):
        raise ValueError(f"{METADATA_PATH} must contain a potentials array")
    return data


def list_potential_records(*, element: str = "") -> list[dict[str, Any]]:
    data = load_potentials_metadata()
    records = [item for item in data.get("potentials", []) if isinstance(item, dict)]
    filter_el = (element or "").strip()
    if not filter_el:
        return records
    symbol = normalize_element_symbol(filter_el)
    return [
        record
        for record in records
        if symbol in [str(el) for el in record.get("elements") or []]
    ]


def default_potential_record() -> dict[str, Any] | None:
    data = load_potentials_metadata()
    default_id = str(data.get("default_id") or "").strip()
    for record in data.get("potentials") or []:
        if isinstance(record, dict) and record.get("default"):
            return record
    if default_id:
        for record in data.get("potentials") or []:
            if isinstance(record, dict) and record.get("id") == default_id:
                return record
    return None


def normalize_element_symbol(symbol: str) -> str:
    text = (symbol or "").strip()
    if not text:
        return ""
    return text[0].upper() + text[1:].lower()


def _record_by_id(data: dict[str, Any], record_id: str) -> dict[str, Any] | None:
    for record in data.get("potentials") or []:
        if isinstance(record, dict) and record.get("id") == record_id:
            return record
    return None


def _hint_for_element(record: dict[str, Any], symbol: str) -> dict[str, Any]:
    hints = record.get("element_hints")
    if isinstance(hints, dict):
        hint = hints.get(symbol)
        if isinstance(hint, dict):
            return hint
    out: dict[str, Any] = {}
    if record.get("typical_lattice"):
        out["typical_lattice"] = record.get("typical_lattice")
    if record.get("typical_a0_angstrom") is not None:
        out["typical_a0_angstrom"] = record.get("typical_a0_angstrom")
    if record.get("typical_mass_amu") is not None:
        out["typical_mass_amu"] = record.get("typical_mass_amu")
    return out


def _default_element_for_lattice(lattice: str) -> str:
    return {"bcc": "Fe", "fcc": "Ni", "hcp": "Co"}.get(lattice, "")


def _build_resolved(
    record: dict[str, Any],
    *,
    element: str,
    lattice: str,
    reason: str,
) -> dict[str, Any]:
    hint = _hint_for_element(record, element)
    resolved_lattice = (
        lattice
        or str(hint.get("typical_lattice") or record.get("typical_lattice") or "")
        .strip()
        .lower()
    )
    return {
        "id": str(record.get("id") or ""),
        "file": str(record.get("file") or ""),
        "pair_style": str(record.get("pair_style") or ""),
        "element": element,
        "lattice": resolved_lattice,
        "a0": float(
            hint.get("typical_a0_angstrom") or record.get("typical_a0_angstrom") or 0.0
        ),
        "mass": float(
            hint.get("typical_mass_amu") or record.get("typical_mass_amu") or 0.0
        ),
        "reason": reason,
    }


def _score_record(
    record: dict[str, Any],
    *,
    symbol: str,
    lattice: str,
    defaults_by_lattice: dict[str, str],
) -> int:
    hint = _hint_for_element(record, symbol)
    hint_lattice = str(hint.get("typical_lattice") or "").strip().lower()
    record_id = str(record.get("id") or "")
    score = 0
    if lattice and hint_lattice == lattice:
        score += 100
    if lattice and defaults_by_lattice.get(lattice) == record_id:
        score += 50
    if not lattice and hint_lattice and _default_element_for_lattice(hint_lattice) == symbol:
        score += 20
    if record.get("default"):
        score += 10
    if record_id in {"NiCr_adp", "fe_men03_eam"}:
        score += 5
    return score


def resolve_potential(*, lattice: str = "", element: str = "") -> dict[str, Any] | None:
    data = load_potentials_metadata()
    lattice_norm = (lattice or "").strip().lower()
    symbol = normalize_element_symbol(element)
    defaults_by_lattice = {
        str(k).strip().lower(): str(v)
        for k, v in (data.get("defaults_by_lattice") or {}).items()
    }

    if symbol:
        candidates = [
            r
            for r in (data.get("potentials") or [])
            if isinstance(r, dict)
            and symbol in [str(el) for el in (r.get("elements") or [])]
        ]
        if not candidates:
            return None
        ranked = sorted(
            enumerate(candidates),
            key=lambda pair: (
                -_score_record(
                    pair[1],
                    symbol=symbol,
                    lattice=lattice_norm,
                    defaults_by_lattice=defaults_by_lattice,
                ),
                pair[0],
            ),
        )
        best = ranked[0][1]
        hint = _hint_for_element(best, symbol)
        resolved_lattice = (
            lattice_norm or str(hint.get("typical_lattice") or "").strip().lower()
        )
        return _build_resolved(
            best,
            element=symbol,
            lattice=resolved_lattice,
            reason=(
                f"element={symbol}"
                + (f",lattice={lattice_norm}" if lattice_norm else "")
            ),
        )

    if lattice_norm:
        record_id = defaults_by_lattice.get(lattice_norm, "")
        record = _record_by_id(data, record_id) if record_id else None
        if record is None:
            fallback = default_potential_record()
            if fallback is None:
                return None
            return _build_resolved(
                fallback,
                element="Fe",
                lattice="bcc",
                reason="fallback_default",
            )
        default_el = _default_element_for_lattice(lattice_norm)
        if not default_el:
            elements = [str(el) for el in (record.get("elements") or [])]
            default_el = elements[0] if elements else ""
        return _build_resolved(
            record,
            element=default_el,
            lattice=lattice_norm,
            reason=f"defaults_by_lattice[{lattice_norm}]",
        )

    record = default_potential_record()
    if record is None:
        return None
    return _build_resolved(
        record,
        element="Fe",
        lattice="bcc",
        reason="global_default",
    )


def format_potentials_catalog(*, element: str = "") -> str:
    data = load_potentials_metadata()
    records = list_potential_records(element=element)
    if not records:
        if element.strip():
            return (
                f"_No potentials registered at {METADATA_PATH} support "
                f"element {element.strip()}._"
            )
        return f"_No potentials registered in {METADATA_PATH}._"

    default_id = str(data.get("default_id") or "").strip()
    defaults_by_lattice = {
        str(k).strip().lower(): str(v)
        for k, v in (data.get("defaults_by_lattice") or {}).items()
    }
    lines = [f"Registered interatomic potentials (from {METADATA_PATH}):"]
    if element.strip():
        lines[0] = (
            f"Potentials supporting element {element.strip()} "
            f"(from {METADATA_PATH}):"
        )

    for record in records:
        record_id = record.get("id", "")
        lattice_default_tags = [
            lat for lat, rid in defaults_by_lattice.items() if rid == record_id
        ]
        tags: list[str] = []
        if bool(record.get("default")) or record_id == default_id:
            tags.append("[DEFAULT]")
        for lat in sorted(lattice_default_tags):
            tags.append(f"[DEFAULT {lat}]")
        tag_suffix = "".join(f"  {t}" for t in tags)
        elements = ", ".join(str(el) for el in record.get("elements") or [])
        lines.append(
            f"- id={record_id}{tag_suffix}\n"
            f"  file: {record.get('file', '')}\n"
            f"  pair_style: {record.get('pair_style', '')}\n"
            f"  elements: {elements}\n"
            f"  description: {record.get('description', '')}"
        )
        hints = record.get("element_hints")
        if isinstance(hints, dict) and hints:
            lines.append("  element_hints:")
            for symbol, hint in hints.items():
                if not isinstance(hint, dict):
                    continue
                lattice = hint.get("typical_lattice", "")
                a0 = hint.get("typical_a0_angstrom", "")
                mass = hint.get("typical_mass_amu", "")
                lines.append(
                    f"    {symbol}: lattice={lattice}  a0={a0} Å  mass={mass} amu"
                )
        elif record.get("typical_lattice"):
            lines.append(
                f"  typical: lattice={record.get('typical_lattice')}  "
                f"a0={record.get('typical_a0_angstrom')} Å  "
                f"mass={record.get('typical_mass_amu')} amu"
            )

    lines.append(
        "\nTo use a non-default potential in write_gb_minimize_config, set element, lattice, "
        "a0, mass, pair_style, and potential_path from the entry above."
    )
    return "\n".join(lines)
