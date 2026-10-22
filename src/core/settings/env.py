"""Load GBclaw runtime environment variables from a project .env file."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv as _python_dotenv_load_dotenv

from core.paths import REPO_ROOT

_LOADED_PATHS: set[Path] = set()


def load_dotenv(path: Path | str | None = None) -> Path | None:
    """Load .env entries with python-dotenv, preserving existing environment values."""
    env_path = Path(path) if path is not None else REPO_ROOT / ".env"
    env_path = env_path.resolve()
    if env_path in _LOADED_PATHS:
        return env_path if env_path.is_file() else None
    _LOADED_PATHS.add(env_path)
    if not env_path.is_file():
        return None

    _python_dotenv_load_dotenv(env_path, override=False)
    return env_path


def get_env_str(name: str, default: str = "") -> str:
    """Return a string environment setting after ensuring ``.env`` is loaded."""
    load_dotenv()
    return os.environ.get(name, default)


def get_env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Return a validated integer environment setting or its default."""
    raw = get_env_str(name).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if minimum is not None and value < minimum:
        return default
    if maximum is not None and value > maximum:
        return default
    return value


def get_env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean switch, preserving shell-over-dotenv precedence."""
    raw = get_env_str(name).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default
