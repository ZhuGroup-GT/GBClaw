"""Extract project image references before tool previews are shortened for the UI."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

_IMAGE_SUFFIX = re.compile(r"\.(?:png|jpe?g|gif|webp|bmp|svg)$", re.I)
_IMAGE_END = re.compile(r"\.(?:png|jpe?g|gif|webp|bmp|svg)(?=$|[?#\s)\]},.;:!])", re.I)
_QUOTED = re.compile(r'`([^`\n]+)`|"([^"\n]+)"|\'([^\'\n]+)\'|<([^<>\n]+)>')
_TOKENS = re.compile(r'''[^\s`"'<>|,;]+''')


def normalize_artifact_image_path(value: str, project_id: str | None = None) -> str | None:
    """Return a canonical path inside artifacts, never a foreign project or filesystem path."""
    path = value.strip().replace(r"\/", "/")
    if not path or re.search(r"[\x00-\x1f\x7f]", path):
        return None
    if path.startswith("/api/projects/"):
        url = urlsplit(path)
        match = re.fullmatch(r"/api/projects/([^/]+)/files/(?:raw|download)", url.path)
        if not match or not project_id or unquote(match[1]) != project_id:
            return None
        path = parse_qs(url.query).get("path", [""])[0]
    else:
        if re.match(r"^[a-z][a-z\d+.-]*:", path, re.I) or path.startswith("//"):
            return None
        path = unquote(re.split(r"[?#]", path, maxsplit=1)[0])
    if re.search(r"[\x00-\x1f\x7f\\]", path) or re.search(r"%(?:2e|2f|5c|00)", path, re.I):
        return None
    if path.startswith("./"):
        path = path[2:]
    if not path.startswith("artifacts/"):
        match = re.search(r"(?:^|/)projects/([^/]+)/(artifacts/.*)$", path)
        if not match or not project_id or match[1] != project_id:
            return None
        path = match[2]
    if any(part in {"", ".", ".."} for part in path.split("/")):
        return None
    return path if _IMAGE_SUFFIX.search(path) else None


def _text_candidates(text: str) -> list[tuple[int, str]]:
    candidates = [
        (match.start(), next(group for group in match.groups() if group is not None))
        for match in _QUOTED.finditer(text)
    ]
    for match in re.finditer(r"\]\(", text):
        start = end = match.end()
        depth = 1
        while end < len(text) and depth:
            char = text[end]
            if char == "\\":
                end += 2
                continue
            if char == "\n":
                break
            if char == "(":
                depth += 1
            if char == ")":
                depth -= 1
            end += 1
        if not depth:
            value = text[start : end - 1].strip()
            value = re.sub(r'''\s+(?:"[^"\n]*"|'[^'\n]*')\s*$''', "", value)
            if value.startswith("<") and value.endswith(">"):
                value = value[1:-1]
            candidates.append((start, re.sub(r"\\([()])", r"\1", value)))
    for match in _TOKENS.finditer(text):
        value = match[0].lstrip("([{")
        if "](" in value:
            continue
        ending = _IMAGE_END.search(value)
        if ending:
            value = value[: ending.end()]
        candidates.append((match.start(), value))
    return sorted(candidates, key=lambda candidate: candidate[0])


def extract_artifact_image_paths(value: Any, project_id: str | None = None) -> list[str]:
    """Read image references, honoring a tool's explicit summary presentation.

    ``summary_image_path`` opts a result (and its run envelope) into displaying
    just that summary plus any explicitly listed ``image_paths``. Diagnostic
    artifacts remain readable and still display when requested independently.
    """
    paths: dict[str, None] = {}
    # Keep parsed JSON containers alive: CPython can reuse their IDs between
    # separate string payloads, which must not be mistaken for a visited cycle.
    seen: dict[int, Any] = {}

    def visit(item: Any, depth: int = 0) -> None:
        if depth > 40:
            return
        if isinstance(item, (dict, list, tuple)):
            if id(item) in seen:
                return
            seen[id(item)] = item
            if isinstance(item, dict):
                presentation = item
                if isinstance(item.get("result"), dict):
                    presentation = item["result"]
                if presentation.get("presentation_policy") == "references_only":
                    return
                if presentation.get("presentation_policy") == "summary_only":
                    visit(presentation.get("image_paths", []), depth + 1)
                    return
                summary = presentation.get("summary_image_path")
                if isinstance(summary, str) and normalize_artifact_image_path(summary, project_id):
                    visit(summary, depth + 1)
                    visit(presentation.get("image_paths", []), depth + 1)
                    return
            for child in item.values() if isinstance(item, dict) else item:
                visit(child, depth + 1)
        elif isinstance(item, str):
            direct = normalize_artifact_image_path(item, project_id)
            ending = _IMAGE_END.search(direct) if direct else None
            if direct and ending and ending.end() == len(direct):
                paths[direct] = None
                return
            try:
                parsed = json.loads(item)
            except (ValueError, TypeError):
                parsed = item
            if parsed != item:
                visit(parsed, depth + 1)
                return
            for _, candidate in _text_candidates(item.replace(r"\/", "/")):
                path = normalize_artifact_image_path(candidate, project_id)
                if path:
                    paths[path] = None

    visit(value)
    return list(paths)
