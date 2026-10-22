"""Bounded host-generated pointers to immutable historical summary snapshots.

The working summary and this small index are deliberately incomplete. Archive
blurbs are extractive historical excerpts, never new facts or current approvals.
The predecessor's ID is computable before the transaction that archives it and
replaces the working summary, so that transaction can publish a valid pointer.
"""

from __future__ import annotations

import hashlib
import json
import re

from project_memory.models import SemanticSummaryRecord

HISTORY_START = "<!-- project-summary-history:v1 -->"
HISTORY_END = "<!-- /project-summary-history -->"
_START_RE = r"<!--\s*project-summary-history(?::v\d+)?\s*-->"
_END_RE = r"<!--\s*/project-summary-history\s*-->"
_HISTORY_RE = re.compile(_START_RE + r".*?(?:" + _END_RE + r"|\Z)", re.DOTALL | re.IGNORECASE)
_CITATION_RE = re.compile(r"\(?\b(?:fact|run)\s*:\s*`[^`]*`\)?", re.IGNORECASE)
_NOTICE = (
    "Selective current summary and recent-history index; both are incomplete. "
    "Omitted content still exists in SQLite. Archives are historical, not current facts or approvals."
)
_LOOKUP = (
    "Read: query_project_memory(entity='summary_archive', archive_id='<ID>'). "
    "Find older: query_project_memory(entity='summary_archive', query='<topic>'); "
    "follow next_cursor as cursor. Verify current facts/runs via query_project_memory. "
    "Files: memory/summary_archive/index.md."
)
_COMPACT_NOTICE = "Summary/index are incomplete; archives are historical, not current evidence."
_COMPACT_LOOKUP = (
    "query_project_memory(entity='summary_archive', archive_id='<ID>'); "
    "search query='<topic>', follow next_cursor as cursor."
)
_SHORT_SOURCE_LABELS = {
    "Tool results": "Tools",
    "Conversation-derived context": "Conversation",
    "Agent reports": "Agent reports",
    "Workflow state": "Workflow",
}


def summary_archive_id(summary: SemanticSummaryRecord) -> str:
    """Match the archive primary key used by the atomic storage transaction."""
    payload = summary.model_dump(mode="json")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "summary-" + hashlib.sha256(encoded).hexdigest()


def strip_history_navigation(text: str) -> str:
    """Remove host indexes, including repeated blocks or an unfinished tail.

    Older snapshots retain their own navigation in SQLite. Those nested indexes
    must not become input for another index or consume the working prose budget.
    """
    text = _HISTORY_RE.sub("", text)
    return re.sub(_END_RE, "", text, flags=re.IGNORECASE).strip()


def has_history_navigation(text: str) -> bool:
    """Whether the current host format has a complete, delimited index."""
    start = text.find(HISTORY_START)
    return start >= 0 and text.find(HISTORY_END, start + len(HISTORY_START)) >= 0


def _shorten(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 1].rstrip(" \\") + "…"


def _plain_excerpt(text: str) -> str:
    # Historical citations may point to retracted records: never let previews
    # masquerade as the host's active (fact: `key`) evidence references.
    text = _CITATION_RE.sub("", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s*(?:[-*+>]\s+|\d+[.)]\s+)", "", text)
    text = " ".join(text.split()).replace("`", "'")
    # Keep each preview inert inside a single Markdown list item.
    return re.sub(r"([\\*_\[\]<>])", r"\\\1", text).strip()


def history_blurb(content: str, max_chars: int = 240) -> str:
    """Extract bounded, source-labeled topics without recursively summarizing.

    Take one excerpt per source section so a long tools section does not hide
    conversation interpretations or workflow state. Exact historical text and
    the original source labels remain available in the immutable archive.
    """
    if max_chars <= 0:
        return ""
    # Local import keeps storage and host formatting free to reuse these helpers
    # without creating a policy/navigation import cycle.
    from .policy import RETRIEVAL_NOTE

    content = strip_history_navigation(content).replace(RETRIEVAL_NOTE, "")
    excerpts: dict[str, str] = {}
    section = "Context"
    for raw_line in content.splitlines():
        heading = re.match(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$", raw_line)
        if heading:
            section = _plain_excerpt(heading.group(1))
            section = _SHORT_SOURCE_LABELS.get(section, section)
            continue
        excerpt = _plain_excerpt(raw_line)
        if excerpt and section not in excerpts:
            excerpts[section] = excerpt
    if not excerpts:
        return _shorten("Historical snapshot; open the archive for its source sections.", max_chars)
    items = list(excerpts.items())[:4]
    while len(items) > 1 and sum(len(label) + 18 for label, _ in items) + 3 * (len(items) - 1) > max_chars:
        items.pop()
    # Share the space before clipping individual excerpts; do not let the first
    # source section take the entire preview merely because it appears first.
    per_item = max(1, (max_chars - 3 * (len(items) - 1)) // len(items))
    return _shorten(" | ".join(_shorten(f"{label}: {text}", per_item) for label, text in items), max_chars)


def build_history_navigation(
    memory,
    predecessor: SemanticSummaryRecord,
    max_chars: int = 1400,
    max_entries: int = 3,
) -> str:
    """Build a small retrieval index, always preferring the direct predecessor.

    No database mutation occurs here. The caller must archive ``predecessor`` in
    the same transaction that saves the summary containing its pointer. A too
    small budget raises rather than silently truncating an ID or lookup advice.
    """
    if max_chars <= 0 or max_entries < 1:
        raise ValueError("History navigation requires a positive budget and at least one entry")
    entries: list[tuple[str, SemanticSummaryRecord]] = []
    seen: set[str] = set()
    if predecessor.content.strip():
        predecessor_id = summary_archive_id(predecessor)
        entries.append((predecessor_id, predecessor))
        seen.add(predecessor_id)
    for archive in memory.query_summary_archives(limit=min(1000, max_entries + 1)):
        if archive.archive_id not in seen and len(entries) < max_entries:
            entries.append((archive.archive_id, archive))
            seen.add(archive.archive_id)

    compact = False

    def render(lines: list[str]) -> str:
        if compact:
            return "\n".join((HISTORY_START, _COMPACT_NOTICE, *lines, _COMPACT_LOOKUP, HISTORY_END))
        return "\n".join((
            HISTORY_START, "## Historical summary index", _NOTICE, *lines, _LOOKUP, HISTORY_END,
        ))

    if not entries:
        text = render(["No earlier summary has been archived yet."])
        if len(text) > max_chars:
            compact = True
            text = render(["No earlier archived summary."])
            if len(text) > max_chars:
                raise ValueError("History navigation budget cannot fit retrieval guidance")
        return text

    pointers = [f"- `{archive_id}` (revision {record.source_revision})" for archive_id, record in entries]
    if len(render(pointers[:1])) + 30 > max_chars:
        compact = True
    while len(pointers) > 1 and len(render(pointers)) > max_chars:
        pointers.pop()
        entries.pop()
    if len(render(pointers)) > max_chars:
        raise ValueError("History navigation budget cannot fit the predecessor ID and retrieval guidance")
    available = max_chars - len(render(pointers))
    lines: list[str] = []
    for index, (pointer, (_, record)) in enumerate(zip(pointers, entries)):
        allowance = min(240, available // (len(pointers) - index) - 2)
        blurb = history_blurb(record.content, max_chars=allowance)
        suffix = ": " + blurb if blurb else ""
        available -= len(suffix)
        lines.append(pointer + suffix)
    return render(lines)
