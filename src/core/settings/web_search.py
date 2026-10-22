"""Load optional web search API settings from the project `.key` file."""

from __future__ import annotations

from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path

from core.paths import REPO_ROOT
from core.llm.config import LLMEndpoint
from core.llm.endpoints import is_official_openai_url

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
WEB_SEARCH_SECTION = "web_search"


@dataclass(frozen=True)
class WebSearchConfig:
    api_key: str
    model: str
    base_url: str = DEFAULT_OPENAI_BASE_URL
    timeout: int = 60


def _default_key_path() -> Path:
    return REPO_ROOT / ".key"


def _enabled(parser: ConfigParser, section: str) -> bool:
    raw = parser.get(section, "enabled", fallback="true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _can_reuse_for_web_search(endpoint: LLMEndpoint | None) -> bool:
    if endpoint is None:
        return False
    if endpoint.provider.strip().lower() != "openai":
        return False
    return is_official_openai_url(endpoint.base_url)


def load_web_search_config(
    key_path: Path | str | None = None,
    *,
    fallback_endpoint: LLMEndpoint | None = None,
) -> WebSearchConfig | None:
    """Load optional [web_search] config, or reuse an official OpenAI endpoint."""
    path = Path(key_path) if key_path is not None else _default_key_path()
    parser = ConfigParser()
    if path.is_file():
        parser.read(path)

    if parser.has_section(WEB_SEARCH_SECTION):
        section = WEB_SEARCH_SECTION
        if not _enabled(parser, section):
            return None

        can_reuse_fallback = _can_reuse_for_web_search(fallback_endpoint)
        api_key = parser.get(section, "api_key", fallback="").strip()
        if not api_key and can_reuse_fallback and fallback_endpoint is not None:
            api_key = fallback_endpoint.api_key
        if not api_key:
            raise ValueError("[web_search] api_key is required")

        model = parser.get(section, "model", fallback="").strip()
        if not model and can_reuse_fallback and fallback_endpoint is not None:
            model = fallback_endpoint.model
        if not model:
            raise ValueError("[web_search] model is required")

        base_url = parser.get(section, "base_url", fallback="").strip()
        if not base_url and can_reuse_fallback and fallback_endpoint is not None:
            base_url = fallback_endpoint.base_url
        if not base_url:
            base_url = DEFAULT_OPENAI_BASE_URL

        return WebSearchConfig(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=parser.getint(section, "timeout", fallback=60),
        )

    if not _can_reuse_for_web_search(fallback_endpoint):
        return None
    assert fallback_endpoint is not None
    return WebSearchConfig(
        api_key=fallback_endpoint.api_key,
        model=fallback_endpoint.model,
        base_url=fallback_endpoint.base_url,
        timeout=fallback_endpoint.timeout,
    )


def is_web_search_available(
    fallback_endpoint: LLMEndpoint,
    *,
    key_path: Path | str | None = None,
) -> bool:
    """Return True only when search can be configured without exposing a broken tool."""
    try:
        return load_web_search_config(
            key_path,
            fallback_endpoint=fallback_endpoint,
        ) is not None
    except ValueError:
        return False
