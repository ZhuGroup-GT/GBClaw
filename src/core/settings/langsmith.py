"""Load LangSmith tracing settings from the project `.key` file."""

from __future__ import annotations

import os
from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path

from core.paths import REPO_ROOT


@dataclass(frozen=True)
class LangSmithConfig:
    enabled: bool
    api_key: str
    project: str
    endpoint: str


def load_langsmith_config(key_path: Path | str | None = None) -> LangSmithConfig:
    path = Path(key_path) if key_path is not None else REPO_ROOT / ".key"
    parser = ConfigParser()
    # Without a .key (graphs built from explicit endpoints) tracing stays off.
    if path.is_file():
        parser.read(path)

    if not parser.has_section("langsmith"):
        return LangSmithConfig(
            enabled=False,
            api_key="",
            project="GBclaw",
            endpoint="https://api.smith.langchain.com",
        )

    enabled = parser.get("langsmith", "enabled", fallback="false").strip().lower()
    return LangSmithConfig(
        enabled=enabled in {"1", "true", "yes", "on"},
        api_key=parser.get("langsmith", "api_key", fallback="").strip(),
        project=parser.get("langsmith", "project", fallback="GBclaw").strip(),
        endpoint=parser.get(
            "langsmith",
            "endpoint",
            fallback="https://api.smith.langchain.com",
        ).strip(),
    )


def configure_langsmith(config: LangSmithConfig | None = None) -> bool:
    """Apply LangSmith env vars so LangChain/LangGraph runs are traced."""
    cfg = config or load_langsmith_config()
    if not cfg.enabled or not cfg.api_key or "your-langsmith" in cfg.api_key:
        return False

    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = cfg.api_key
    os.environ["LANGSMITH_PROJECT"] = cfg.project
    os.environ["LANGSMITH_ENDPOINT"] = cfg.endpoint
    # Legacy aliases kept for older LangChain integrations.
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = cfg.api_key
    os.environ["LANGCHAIN_PROJECT"] = cfg.project
    return True


def is_tracing_enabled(config: LangSmithConfig | None = None) -> bool:
    cfg = config or load_langsmith_config()
    return cfg.enabled and bool(cfg.api_key) and "your-langsmith" not in cfg.api_key
