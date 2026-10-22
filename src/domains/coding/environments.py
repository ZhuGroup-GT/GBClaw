"""Host-controlled runtime-environment registry for coding sandboxes."""

from __future__ import annotations

import hashlib
from importlib.metadata import distributions
import json
import os
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from langchain_core.tools import BaseTool, tool

from core.paths import REPO_ROOT
from core.settings.env import get_env_str


_REPO_ROOT = REPO_ROOT
_DEFAULT_REGISTRY_PATH = Path(__file__).with_name("environments.toml")
_ALIAS_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_CONDA_ENV_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_KNOWN_CAPABILITIES = frozenset({"ase", "lammps", "mpi"})


@dataclass(frozen=True)
class CodingEnvironment:
    """One allowlisted Conda prefix and its explicitly granted capabilities."""

    alias: str
    prefix: Path
    description: str
    capabilities: frozenset[str]

    @property
    def available(self) -> bool:
        return self.prefix.is_dir() and (self.prefix / "bin" / "python").is_file()

    @property
    def fingerprint(self) -> str | None:
        history = self.prefix / "conda-meta" / "history"
        digest = hashlib.sha256()
        if history.is_file():
            with history.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        digest.update(json.dumps(self.package_versions(), sort_keys=True).encode())
        return digest.hexdigest()

    def package_versions(self) -> dict[str, str]:
        """Read installed distribution metadata without importing environment code."""
        packages = {}
        for directory in (self.prefix / "lib").glob("python*/site-packages"):
            for item in distributions(path=[str(directory)]):
                name = item.metadata.get("Name")
                if name:
                    packages[name.lower().replace("_", "-")] = item.version
        return packages

    def public_record(self) -> dict[str, Any]:
        versions = self.package_versions() if self.available else {}
        return {
            "name": self.alias,
            "description": self.description,
            "capabilities": sorted(self.capabilities),
            "available": self.available,
            "fingerprint": self.fingerprint if self.available else None,
            "packages": {name: versions[name] for name in (
                "numpy", "scipy", "pandas", "matplotlib", "pillow", "ase", "ovito", "lammps"
            ) if name in versions},
        }


class CodingEnvironmentRegistry:
    """Immutable alias registry; model input can never select an arbitrary host path."""

    def __init__(
        self,
        environments: Mapping[str, CodingEnvironment],
        *,
        default_alias: str,
        source_path: Path,
    ) -> None:
        normalized = dict(environments)
        if not normalized:
            raise ValueError("coding environment registry must not be empty")
        if default_alias not in normalized:
            raise ValueError(
                f"default coding environment is not registered: {default_alias}"
            )
        self._environments = MappingProxyType(normalized)
        self.default_alias = default_alias
        self.source_path = source_path

    @property
    def environments(self) -> Mapping[str, CodingEnvironment]:
        return self._environments

    def resolve(
        self,
        alias: str | None,
        *,
        require_available: bool = True,
    ) -> CodingEnvironment:
        name = str(alias or self.default_alias).strip().lower()
        if not _ALIAS_RE.fullmatch(name):
            allowed = ", ".join(sorted(self._environments))
            raise ValueError(
                f"invalid coding environment alias; registered environments: {allowed}"
            )
        try:
            environment = self._environments[name]
        except KeyError as exc:
            allowed = ", ".join(sorted(self._environments))
            raise ValueError(
                f"unknown coding environment {name!r}; registered environments: {allowed}"
            ) from exc
        if require_available and not environment.available:
            raise RuntimeError(
                f"coding environment {name!r} is registered but unavailable"
            )
        return environment

    def public_catalog(self) -> dict[str, Any]:
        return {
            "default_environment": self.default_alias,
            "environments": [
                self._environments[name].public_record()
                for name in sorted(self._environments)
            ],
        }


def _registry_path(path: Path | str | None) -> Path:
    if path is not None:
        candidate = Path(path)
    else:
        raw = get_env_str("GBCLAW_CODING_ENVIRONMENTS_FILE").strip()
        candidate = Path(raw) if raw else _DEFAULT_REGISTRY_PATH
    if not candidate.is_absolute():
        candidate = _REPO_ROOT / candidate
    return candidate.resolve()


def _conda_environment_prefix(name: str) -> Path:
    if not _CONDA_ENV_RE.fullmatch(name):
        raise ValueError(f"invalid Conda environment name: {name!r}")
    roots: list[Path] = []
    configured_roots = get_env_str("CONDA_ENVS_PATH").strip()
    if configured_roots:
        roots.extend(Path(item).expanduser() for item in configured_roots.split(os.pathsep))
    current = Path(sys.prefix).resolve()
    if current.parent.name == "envs":
        roots.append(current.parent)
    active_prefix = get_env_str("CONDA_PREFIX").strip()
    if active_prefix:
        active = Path(active_prefix).resolve()
        if active.parent.name == "envs":
            roots.append(active.parent)
        else:
            roots.append(active / "envs")
    conda = shutil.which("conda")
    if conda:
        conda_path = Path(conda).resolve()
        if conda_path.parent.name in {"bin", "condabin"}:
            roots.append(conda_path.parent.parent / "envs")
    for root in dict.fromkeys(item.resolve() for item in roots):
        candidate = (root / name).resolve()
        if candidate.is_dir():
            return candidate
    searched = ", ".join(str(item) for item in dict.fromkeys(roots)) or "none"
    raise RuntimeError(
        f"Conda environment {name!r} was not found in registered roots: {searched}"
    )


def _environment_from_table(alias: str, raw: Any) -> CodingEnvironment:
    if not _ALIAS_RE.fullmatch(alias):
        raise ValueError(f"invalid coding environment alias: {alias!r}")
    if not isinstance(raw, dict):
        raise ValueError(f"environment {alias!r} must be a TOML table")
    selectors = [
        bool(raw.get("current")),
        bool(str(raw.get("prefix") or "").strip()),
        bool(str(raw.get("conda_env") or "").strip()),
    ]
    if sum(selectors) != 1:
        raise ValueError(
            f"environment {alias!r} must select exactly one of current, prefix, conda_env"
        )
    if selectors[0]:
        prefix = Path(sys.prefix).resolve()
    elif selectors[1]:
        prefix_raw = str(raw["prefix"]).strip()
        prefix_env = str(raw.get("prefix_env") or "").strip()
        if prefix_env:
            prefix_raw = get_env_str(prefix_env, prefix_raw).strip() or prefix_raw
        prefix = Path(prefix_raw).expanduser()
        if not prefix.is_absolute():
            prefix = _REPO_ROOT / prefix
        prefix = prefix.resolve()
    else:
        conda_name = str(raw["conda_env"]).strip()
        conda_env_var = str(raw.get("conda_env_var") or "").strip()
        if conda_env_var:
            conda_name = get_env_str(conda_env_var, conda_name).strip() or conda_name
        try:
            prefix = _conda_environment_prefix(conda_name)
        except RuntimeError:
            # Keep unavailable entries visible in the catalog without accepting them for runs.
            current = Path(sys.prefix).resolve()
            env_root = current.parent if current.parent.name == "envs" else current / "envs"
            prefix = (env_root / conda_name).resolve()
    raw_capabilities = raw.get("capabilities") or []
    if not isinstance(raw_capabilities, list):
        raise ValueError(f"environment {alias!r} capabilities must be a TOML array")
    capabilities = frozenset(
        str(item).strip().lower() for item in raw_capabilities
    )
    unknown = capabilities - _KNOWN_CAPABILITIES
    if unknown:
        raise ValueError(
            f"environment {alias!r} has unknown capabilities: {', '.join(sorted(unknown))}"
        )
    return CodingEnvironment(
        alias=alias,
        prefix=prefix,
        description=str(raw.get("description") or "").strip(),
        capabilities=capabilities,
    )


def load_coding_environment_registry(
    path: Path | str | None = None,
) -> CodingEnvironmentRegistry:
    registry_path = _registry_path(path)
    if not registry_path.is_file():
        raise FileNotFoundError(f"coding environment registry not found: {registry_path}")
    with registry_path.open("rb") as handle:
        payload = tomllib.load(handle)
    if int(payload.get("version") or 0) != 1:
        raise ValueError("coding environment registry version must be 1")
    raw_environments = payload.get("environments")
    if not isinstance(raw_environments, dict):
        raise ValueError("coding environment registry requires [environments.*] tables")
    environments: dict[str, CodingEnvironment] = {}
    for raw_alias, raw in raw_environments.items():
        alias = str(raw_alias).strip().lower()
        if alias in environments:
            raise ValueError(f"duplicate coding environment alias: {alias}")
        environments[alias] = _environment_from_table(alias, raw)
    default_alias = str(payload.get("default_environment") or "").strip().lower()
    return CodingEnvironmentRegistry(
        environments,
        default_alias=default_alias,
        source_path=registry_path,
    )


def make_list_coding_environments_tool(
    registry: CodingEnvironmentRegistry,
    *,
    resource_limits: Mapping[str, Any] | None = None,
) -> BaseTool:
    @tool
    def list_coding_environments() -> str:
        """List allowlisted coding runtime environments and capabilities.

        Call before ``delegate_coding`` when the task needs a specialized package such as ASE
        (Atomic Simulation Environment) or the LAMMPS Python bindings. Check both availability
        and installed packages. Only available aliases may be delegated; host paths are never
        accepted from model input. MPI additionally requires an explicit rank allocation.
        """
        catalog = registry.public_catalog()
        if resource_limits is not None:
            catalog["resource_limits"] = dict(resource_limits)
        return json.dumps(catalog, ensure_ascii=False, sort_keys=True)

    return list_coding_environments


__all__ = [
    "CodingEnvironment",
    "CodingEnvironmentRegistry",
    "load_coding_environment_registry",
    "make_list_coding_environments_tool",
]
