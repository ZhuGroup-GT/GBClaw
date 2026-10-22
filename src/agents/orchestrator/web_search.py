"""OpenAI Responses API web search tool for current external facts."""

from __future__ import annotations

from typing import Any, Callable

from langchain_core.tools import BaseTool, tool

from core.llm.config import LLMEndpoint
from core.settings.web_search import (
    WebSearchConfig,
    load_web_search_config,
)


def _split_domains(value: str | None) -> list[str]:
    if not value:
        return []
    domains: list[str] = []
    for raw in value.split(","):
        domain = raw.strip()
        if not domain:
            continue
        if "://" in domain:
            domain = domain.split("://", 1)[1]
        domain = domain.split("/", 1)[0].strip()
        if domain:
            domains.append(domain)
    return domains


def _web_search_tool_config(
    *,
    allowed_domains: str | None = None,
    blocked_domains: str | None = None,
) -> dict[str, Any]:
    config: dict[str, Any] = {"type": "web_search"}
    filters: dict[str, list[str]] = {}
    allowed = _split_domains(allowed_domains)
    blocked = _split_domains(blocked_domains)
    if allowed:
        filters["allowed_domains"] = allowed[:100]
    if blocked:
        filters["blocked_domains"] = blocked[:100]
    if filters:
        config["filters"] = filters
    return config


def _value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _output_text(response: Any) -> str:
    text = _value(response, "output_text", "")
    if text:
        return str(text).strip()
    pieces: list[str] = []
    for item in _value(response, "output", []) or []:
        if _value(item, "type") != "message":
            continue
        for content in _value(item, "content", []) or []:
            piece = _value(content, "text") or _value(content, "output_text")
            if piece:
                pieces.append(str(piece))
    return "\n".join(pieces).strip()


def _citations(response: Any, *, limit: int) -> list[tuple[str, str]]:
    citations: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in _value(response, "output", []) or []:
        if _value(item, "type") != "message":
            continue
        for content in _value(item, "content", []) or []:
            for annotation in _value(content, "annotations", []) or []:
                url = str(_value(annotation, "url", "") or "").strip()
                if not url or url in seen:
                    continue
                title = str(_value(annotation, "title", "") or "").strip() or url
                citations.append((title, url))
                seen.add(url)
                if len(citations) >= limit:
                    return citations
    return citations


def run_web_search(
    query: str,
    *,
    config: WebSearchConfig | None = None,
    fallback_endpoint: LLMEndpoint | None = None,
    allowed_domains: str = "",
    blocked_domains: str = "",
    max_sources: int = 8,
    client_factory: Callable[..., Any] | None = None,
) -> str:
    """Run a bounded OpenAI web search and return text plus citation URLs."""
    query = query.strip()
    if not query:
        return "Search query must not be empty."

    resolved = config or load_web_search_config(fallback_endpoint=fallback_endpoint)
    if resolved is None:
        return (
            "Web search is not configured. Add a [web_search] section to .key with "
            "api_key, model, optional base_url, and optional timeout."
        )

    if client_factory is None:
        from openai import OpenAI

        client_factory = OpenAI

    client = client_factory(
        api_key=resolved.api_key,
        base_url=resolved.base_url,
        timeout=resolved.timeout,
    )
    response = client.responses.create(
        model=resolved.model,
        tools=[
            _web_search_tool_config(
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains,
            )
        ],
        input=query,
    )

    text = _output_text(response) or "_No text returned by web search._"
    citations = _citations(response, limit=max(1, min(max_sources, 20)))
    if not citations:
        return f"{text}\n\nSources:\n- _No citations returned._"
    source_lines = "\n".join(f"- {title}: {url}" for title, url in citations)
    return f"{text}\n\nSources:\n{source_lines}"


def make_search_web_tool(endpoint: LLMEndpoint) -> BaseTool:
    @tool
    def search_web(
        query: str,
        allowed_domains: str = "",
        blocked_domains: str = "",
        max_sources: int = 8,
    ) -> str:
        """Search the public web for current external facts using OpenAI web_search.

        Use only for current literature, software/API documentation, potential/database
        pages, or when the user explicitly asks to look something up. Do not use this
        instead of project memory, local artifacts, CSL tools, or LAMMPS outputs.

        allowed_domains / blocked_domains are comma-separated domain names, for example
        "developers.openai.com,docs.lammps.org".
        """
        return run_web_search(
            query,
            fallback_endpoint=endpoint,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains,
            max_sources=max_sources,
        )

    return search_web
