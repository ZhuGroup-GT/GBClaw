"""Validated configuration for minimizing an existing atomic structure."""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass(frozen=True)
class StructureMinimizeConfig:
    version: int
    purpose: str
    description: str
    source: dict[str, Any]
    potential: dict[str, Any]
    simulation: dict[str, Any]
    constraints: dict[str, Any]


@dataclass(frozen=True)
class StructureMinimizeBatchConfig:
    version: int
    purpose: str
    description: str
    structures: list[dict[str, Any]]


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return copy.deepcopy(value)


def _digest(value: Any, name: str) -> str:
    result = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", result):
        raise ValueError(f"{name} must be a SHA-256 hex digest")
    return result


def _positive_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def build_structure_minimize_config(data: dict[str, Any]) -> StructureMinimizeConfig:
    raw = _object(data, "structure_minimize config")
    if type(raw.get("version")) is not int or raw["version"] != 1:
        raise ValueError("structure_minimize requires version=1")
    purpose = raw.get("purpose")
    if not isinstance(purpose, str) or not purpose.strip():
        raise ValueError("purpose must explain why this structure is being minimized")
    source = _object(raw.get("source"), "source")
    source_path = str(source.get("path") or "").strip().replace("\\", "/")
    path = PurePosixPath(source_path)
    if not source_path or path.is_absolute() or ".." in path.parts or path.name in {"", "."}:
        raise ValueError("source.path must be a safe project-relative path")
    source["path"] = path.as_posix()
    source["sha256"] = _digest(source.get("sha256"), "source.sha256")
    for key in ("run_id", "artifact_id", "workflow"):
        value = source.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"source.{key} must be a nonempty string or null")
        source[key] = value
    source["origin"] = _object(source.get("origin", {}), "source.origin")

    potential = _object(raw.get("potential"), "potential")
    style = str(potential.get("pair_style") or "").strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_/]*", style):
        raise ValueError("potential.pair_style must be a single LAMMPS style name")
    potential["pair_style"] = style
    potential_path = potential.get("file")
    if not isinstance(potential_path, str) or not Path(potential_path).is_absolute():
        raise ValueError("potential.file must be an absolute path")
    potential["sha256"] = _digest(potential.get("sha256"), "potential.sha256")
    elements = potential.get("elements")
    if not isinstance(elements, list) or not elements or any(
        not isinstance(element, str) or not re.fullmatch(r"[A-Z][a-z]?", element)
        for element in elements
    ):
        raise ValueError("potential.elements must list an element symbol for each atom type")

    simulation = {
        "boundary": "p p p", "min_style": "cg", "min_etol": 0.0,
        "min_ftol": 1.0e-6, "min_maxiter": 1000, "min_maxeval": 10000,
    }
    supplied_simulation = _object(raw.get("simulation", {}), "simulation")
    unknown = set(supplied_simulation) - set(simulation)
    if unknown:
        raise ValueError(f"unsupported simulation options: {', '.join(sorted(unknown))}")
    simulation.update(supplied_simulation)
    boundary = str(simulation["boundary"]).split()
    if len(boundary) != 3 or any(value not in {"p", "f"} for value in boundary):
        raise ValueError("simulation.boundary must contain three p/f values; shrink-wrap changes the box")
    simulation["boundary"] = " ".join(boundary)
    if simulation["min_style"] not in {"cg", "fire", "sd", "hftn"}:
        raise ValueError("simulation.min_style must be cg, fire, sd or hftn")
    for key in ("min_etol", "min_ftol"):
        if isinstance(simulation[key], bool):
            raise ValueError(f"simulation.{key} must be finite and nonnegative")
        value = float(simulation[key])
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"simulation.{key} must be finite and nonnegative")
        simulation[key] = value
    for key in ("min_maxiter", "min_maxeval"):
        simulation[key] = _positive_integer(simulation[key], f"simulation.{key}")

    constraints = _object(raw.get("constraints", {}), "constraints")
    if set(constraints) - {"mode", "fixed_ids", "sources"}:
        raise ValueError("constraints supports only mode, fixed_ids and sources; fixed atoms are frozen in all axes")
    ids = constraints.get("fixed_ids", [])
    if not isinstance(ids, list):
        raise ValueError("constraints.fixed_ids must be a list")
    ids = [_positive_integer(value, "constraints.fixed_ids entry") for value in ids]
    if len(ids) != len(set(ids)):
        raise ValueError("constraints.fixed_ids must not contain duplicate IDs")
    mode = constraints.get("mode", "fixed_ids" if ids else "none")
    if mode not in {"none", "fixed_ids"}:
        raise ValueError("constraints.mode must be none or fixed_ids")
    if (mode == "none" and ids) or (mode == "fixed_ids" and not ids):
        raise ValueError("fixed_ids mode requires IDs; none mode requires an empty ID list")
    sources = constraints.get("sources", [])
    if not isinstance(sources, list) or any(not isinstance(item, dict) for item in sources):
        raise ValueError("constraints.sources must be a list of provenance objects")
    for item in sources:
        provenance_path = PurePosixPath(str(item.get("path") or "").replace("\\", "/"))
        if provenance_path.is_absolute() or ".." in provenance_path.parts or provenance_path.name in {"", "."}:
            raise ValueError("constraints.sources paths must be project-relative")
        item["path"] = provenance_path.as_posix()
        item["sha256"] = _digest(item.get("sha256"), "constraints.sources.sha256")
    return StructureMinimizeConfig(
        version=1, purpose=purpose.strip(), description=str(raw.get("description") or "").strip(),
        source=source, potential=potential, simulation=simulation,
        constraints={"mode": mode, "fixed_ids": sorted(ids), "sources": sources},
    )


def build_structure_minimize_batch_config(data: dict[str, Any]) -> StructureMinimizeBatchConfig:
    raw = _object(data, "structure_minimize batch config")
    if type(raw.get("version")) is not int or raw["version"] != 1:
        raise ValueError("structure_minimize requires version=1")
    purpose = raw.get("purpose")
    if not isinstance(purpose, str) or not purpose.strip():
        raise ValueError("purpose must explain why these structures are being minimized")
    structures = raw.get("structures")
    if not isinstance(structures, list) or not structures:
        raise ValueError("structures must be a nonempty ordered list")
    items = []
    output_stems: set[str] = set()
    for index, entry in enumerate(structures, start=1):
        entry = _object(entry, f"structures[{index - 1}]")
        if type(entry.get("item_index")) is not int or entry["item_index"] != index:
            raise ValueError("batch item_index must follow input order starting at 1")
        key = entry.get("item_key", "")
        if not isinstance(key, str) or not re.fullmatch(rf"{index:04d}_[a-zA-Z0-9_-]+", key):
            raise ValueError("batch item_key must have its zero-padded index and a safe label")
        cfg = build_structure_minimize_config(entry.get("config"))
        item = {"item_index": index, "item_key": key,
                "label": str(entry.get("label") or ""), "config": asdict(cfg)}
        # Preserve already-configured batches that used the indexed filename.
        output_stem = entry.get("output_stem", f"{key}.minimized")
        if not isinstance(output_stem, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", output_stem):
            raise ValueError("batch output_stem must be a safe filename stem")
        if output_stem in output_stems:
            raise ValueError("batch output_stem values must be unique")
        output_stems.add(output_stem)
        if "output_stem" in entry:
            item["output_stem"] = output_stem
        items.append(item)
    return StructureMinimizeBatchConfig(
        version=1, purpose=purpose.strip(), description=str(raw.get("description") or "").strip(),
        structures=items,
    )


def config_to_dict(config: StructureMinimizeConfig | StructureMinimizeBatchConfig) -> dict[str, Any]:
    return asdict(config)


def load_structure_minimize_config(path: Path | str) -> StructureMinimizeConfig:
    return build_structure_minimize_config(json.loads(Path(path).read_text(encoding="utf-8")))
