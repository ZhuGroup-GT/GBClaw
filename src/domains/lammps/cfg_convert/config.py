"""Validated JSON configuration for deterministic CFG-to-LAMMPS conversion runs."""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _relative_path(value: Any, *, label: str, suffix: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(text)
    if (
        not text
        or path.is_absolute()
        or ".." in path.parts
        or path.name in {"", ".", ".."}
    ):
        raise ValueError(f"{label} must be a safe project-relative path")
    if path.suffix.lower() != suffix:
        raise ValueError(f"{label} must end with {suffix}")
    return path.as_posix()


@dataclass(frozen=True)
class CFGConversionInput:
    source_path: str
    source_sha256: str
    source_size_bytes: int
    source_snapshot: str
    output_lmp: str
    box_origin: tuple[float, float, float] | None
    box_origin_reference: str | None
    box_origin_reference_sha256: str | None
    origin: dict[str, Any] | None = None
    purpose: str = ""
    # LAMMPS data of the same atoms that supplies their types (CFG without a type column).
    type_reference: str | None = None
    type_reference_sha256: str | None = None


@dataclass(frozen=True)
class CFGToLammpsConfig:
    version: int
    description: str
    coordinate_origin: str
    atom_style: str
    mass_override: float | None
    inputs: tuple[CFGConversionInput, ...]
    purpose: str = ""


def build_cfg_to_lammps_config(data: dict[str, Any]) -> CFGToLammpsConfig:
    if not isinstance(data, dict):
        raise ValueError("cfg_convert config root must be an object")
    version = int(data.get("version", 0))
    if version != 1:
        raise ValueError(
            f"unsupported cfg_convert config version {version}; expected version 1"
        )
    coordinate_origin = str(data.get("coordinate_origin") or "").strip().lower()
    if coordinate_origin != "preserve_source_when_available":
        raise ValueError(
            "coordinate_origin must be 'preserve_source_when_available'"
        )
    atom_style = str(data.get("atom_style") or "").strip().lower()
    if atom_style != "atomic":
        raise ValueError("atom_style must be 'atomic'")

    raw_override = data.get("mass_override")
    mass_override = None if raw_override is None else float(raw_override)
    if mass_override is not None and (
        not math.isfinite(mass_override) or mass_override <= 0.0
    ):
        raise ValueError("mass_override must be finite and positive when provided")

    raw_inputs = data.get("inputs")
    if not isinstance(raw_inputs, list) or not raw_inputs:
        raise ValueError("cfg_convert config.inputs must be a non-empty list")
    if len(raw_inputs) > 100:
        raise ValueError("cfg_convert accepts at most 100 CFG inputs per run")

    purpose = str(data.get("purpose") or data.get("description") or "").strip()

    inputs: list[CFGConversionInput] = []
    seen_sources: set[str] = set()
    seen_outputs: set[str] = set()
    for index, raw_item in enumerate(raw_inputs):
        if not isinstance(raw_item, dict):
            raise ValueError(f"inputs[{index}] must be an object")
        source = _relative_path(
            raw_item.get("source_path"), label=f"inputs[{index}].source_path", suffix=".cfg"
        )
        snapshot = _relative_path(
            raw_item.get("source_snapshot"),
            label=f"inputs[{index}].source_snapshot",
            suffix=".cfg",
        )
        output = _relative_path(
            raw_item.get("output_lmp"), label=f"inputs[{index}].output_lmp", suffix=".lmp"
        )
        digest = str(raw_item.get("source_sha256") or "").strip().lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError(f"inputs[{index}].source_sha256 must be a SHA-256 hex digest")
        size = int(raw_item.get("source_size_bytes", -1))
        if size < 0:
            raise ValueError(f"inputs[{index}].source_size_bytes must be non-negative")
        if source in seen_sources:
            raise ValueError(f"duplicate source CFG path: {source}")
        if output in seen_outputs:
            raise ValueError(f"duplicate output LMP path: {output}")
        raw_provenance = raw_item.get("origin")
        provenance = None
        if raw_provenance is not None:
            if not isinstance(raw_provenance, dict):
                raise ValueError(f"inputs[{index}].origin must be an object or null")
            provenance = deepcopy(raw_provenance)
            if provenance.get("path") != source or provenance.get("sha256") != digest:
                raise ValueError(
                    f"inputs[{index}].origin must identify the exact source CFG and digest"
                )
            for key in ("run_id", "workflow", "artifact_id"):
                value = provenance.get(key)
                if value is not None and (not isinstance(value, str) or not value.strip()):
                    raise ValueError(f"inputs[{index}].origin.{key} must be a string or null")
            if not isinstance(provenance.get("frame", {}), dict):
                raise ValueError(f"inputs[{index}].origin.frame must be an object")
            try:
                json.dumps(provenance, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"inputs[{index}].origin must contain finite JSON values") from exc
        raw_origin = raw_item.get("box_origin")
        if raw_origin is None:
            box_origin = None
        else:
            if not isinstance(raw_origin, list) or len(raw_origin) != 3:
                raise ValueError(
                    f"inputs[{index}].box_origin must contain three coordinates or be null"
                )
            box_origin = tuple(float(value) for value in raw_origin)
            if any(not math.isfinite(value) for value in box_origin):
                raise ValueError(
                    f"inputs[{index}].box_origin coordinates must be finite"
                )
        raw_origin_reference = raw_item.get("box_origin_reference")
        raw_origin_digest = raw_item.get("box_origin_reference_sha256")
        if box_origin is None:
            if raw_origin_reference is not None or raw_origin_digest is not None:
                raise ValueError(
                    f"inputs[{index}] cannot declare an origin reference without box_origin"
                )
            origin_reference = None
            origin_digest = None
        else:
            origin_reference = _relative_path(
                raw_origin_reference,
                label=f"inputs[{index}].box_origin_reference",
                suffix=".lmp",
            )
            origin_digest = str(raw_origin_digest or "").strip().lower()
            if not _SHA256_RE.fullmatch(origin_digest):
                raise ValueError(
                    f"inputs[{index}].box_origin_reference_sha256 must be a SHA-256 hex digest"
                )
        raw_type_reference = raw_item.get("type_reference")
        type_reference = type_digest = None
        if raw_type_reference is not None:
            type_reference = _relative_path(
                raw_type_reference, label=f"inputs[{index}].type_reference", suffix=".lmp"
            )
            type_digest = str(raw_item.get("type_reference_sha256") or "").strip().lower()
            if not _SHA256_RE.fullmatch(type_digest):
                raise ValueError(
                    f"inputs[{index}].type_reference_sha256 must be a SHA-256 hex digest"
                )
        elif raw_item.get("type_reference_sha256") is not None:
            raise ValueError(f"inputs[{index}] cannot declare a type digest without type_reference")
        seen_sources.add(source)
        seen_outputs.add(output)
        inputs.append(
            CFGConversionInput(
                source_path=source,
                source_sha256=digest,
                source_size_bytes=size,
                source_snapshot=snapshot,
                output_lmp=output,
                box_origin=box_origin,
                box_origin_reference=origin_reference,
                box_origin_reference_sha256=origin_digest,
                origin=provenance,
                purpose=str(raw_item.get("purpose") or purpose).strip(),
                type_reference=type_reference,
                type_reference_sha256=type_digest,
            )
        )

    return CFGToLammpsConfig(
        version=version,
        description=str(data.get("description") or "").strip(),
        coordinate_origin=coordinate_origin,
        atom_style=atom_style,
        mass_override=mass_override,
        inputs=tuple(inputs),
        purpose=purpose,
    )


def _input_to_dict(item: CFGConversionInput) -> dict[str, Any]:
    data = {
        "source_path": item.source_path,
        "source_sha256": item.source_sha256,
        "source_size_bytes": item.source_size_bytes,
        "source_snapshot": item.source_snapshot,
        "output_lmp": item.output_lmp,
        "box_origin": list(item.box_origin) if item.box_origin is not None else None,
        "box_origin_reference": item.box_origin_reference,
        "box_origin_reference_sha256": item.box_origin_reference_sha256,
        "origin": deepcopy(item.origin),
        "purpose": item.purpose,
    }
    # Written only when used, so earlier configurations serialize unchanged.
    if item.type_reference is not None:
        data["type_reference"] = item.type_reference
        data["type_reference_sha256"] = item.type_reference_sha256
    return data


def config_to_dict(config: CFGToLammpsConfig) -> dict[str, Any]:
    return {
        "version": config.version,
        "description": config.description,
        "purpose": config.purpose,
        "coordinate_origin": config.coordinate_origin,
        "atom_style": config.atom_style,
        "mass_override": config.mass_override,
        "inputs": [_input_to_dict(item) for item in config.inputs],
    }


