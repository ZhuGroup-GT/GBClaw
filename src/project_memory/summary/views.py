"""Rebuildable Markdown views; authoritative summaries and archives are SQLite."""

from __future__ import annotations

from pathlib import Path

from core.util.io import write_text_atomic


def summary_path(project_root: Path) -> Path:
    return project_root / "memory" / "summary.md"


def save_summary_view(project_root: Path, text: str) -> None:
    """Atomically render the SQLite summary as a Markdown view for humans."""
    body = text.strip() or "_No semantic memory yet._"
    if not body.startswith("#"):
        body = f"# Project summary\n\n{body}"
    write_text_atomic(summary_path(project_root), body + "\n", skip_unchanged=True)


def save_archive_views(project_root: Path, memory, *, rebuild: bool = False) -> None:
    """Render recent archive documents and an index without unbounded refresh I/O.

Normal refresh repairs the most recent page. Explicit rebuild walks all pages;
older archives are always queryable in SQLite even if their view is missing.
"""
    from .navigation import history_blurb

    root = project_root / "memory" / "summary_archive"
    offset = 0
    recent = []
    while True:
        records = memory.query_summary_archives(limit=20, offset=offset)
        if offset == 0:
            recent = records
        for record in records:
            body = (
                f"# Historical project summary: {record.archive_id}\n\n"
                "Historical context only. This may contain superseded decisions or withdrawn "
                "interpretations; query current SQLite facts/runs before using it as evidence "
                "or authorization. Evidence-source labels retain their original meaning.\n\n"
                f"Source revision: {record.source_revision}; format: {record.format_version}; "
                f"generated: {record.generated_at}; archived: {record.archived_at}.\n\n"
                + record.content + "\n"
            )
            write_text_atomic(root / f"{record.archive_id}.md", body, skip_unchanged=True)
        if not rebuild or len(records) < 20:
            break
        offset += len(records)
    if not recent:
        return
    index = [
        "# Historical project summaries", "",
        "SQLite is authoritative. These are complete previous summaries retained before "
        "compaction/replacement, not current instructions or approval. Original facts and runs "
        "remain in the database independently of these documents.", "",
        "Latest 20 archives are listed below. For older entries or search, use "
        "`query_project_memory(entity='summary_archive', query=...)`; follow `next_cursor`. "
        "Read an exact document with `archive_id=...`. Missing Markdown views can be rebuilt.", "",
    ]
    index.extend(f"- [{r.archive_id}]({r.archive_id}.md) — revision {r.source_revision}, "
                 f"archived {r.archived_at}. Topics (excerpts): {history_blurb(r.content)}" for r in recent)
    write_text_atomic(root / "index.md", "\n".join(index) + "\n", skip_unchanged=True)
