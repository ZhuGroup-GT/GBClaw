"""Safe read-only access to files inside a project workspace."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from project_workspace.persistence.locks import file_lock
from project_workspace.execution.ownership import project_lock_directory

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".json",
    ".jsonl",
    ".log",
    ".lammps",
    ".ids",
    ".py",
    ".sh",
    ".yaml",
    ".yml",
    ".csv",
    ".in",
    ".data",
    ".cfg",
    ".conf",
    ".xml",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".toml",
    ".poscar",
    ".vasp",
    ".cif",
    ".lmp",
    ".lammpstrj",
}

WATCH_EXTENSIONS = {".log", ".jsonl", ".out", ".err", ".lammps"}

MAX_PREVIEW_BYTES = 512_000


@dataclass(frozen=True)
class FileEntry:
    name: str
    path: str
    entry_type: str
    size: int | None
    mtime: str | None
    is_text: bool
    watch: bool


@dataclass(frozen=True)
class DirectoryListing:
    path: str
    entries: list[FileEntry]


@dataclass(frozen=True)
class TextFileContent:
    path: str
    content: str
    size: int
    mtime: str
    truncated: bool
    watch: bool
    encoding: str = "utf-8"


@dataclass(frozen=True)
class PathStat:
    path: str
    entry_type: str
    size: int | None
    mtime: str
    is_text: bool
    watch: bool
    fingerprint: str | None = None


def resolve_project_path(project_root: Path, relative_path: str) -> Path:
    root = project_root.resolve()
    rel = relative_path.strip().replace("\\", "/").strip("/")
    target = (root / rel).resolve() if rel else root
    if target != root and root not in target.parents:
        raise ValueError("Path escapes project root")
    return target


def prepare_project_path(project_root: Path, relative_path: str) -> Path:
    """Validate access, then refresh a derived transcript only when requested."""
    target = resolve_project_path(project_root, relative_path)
    root = project_root.resolve()
    chat_root = root / "chat"
    if target in {chat_root, chat_root / "transcript.md"}:
        with file_lock(project_lock_directory(root) / "metadata.lock"):
            if not root.is_dir():
                raise ValueError("Project not found")
            if (chat_root / "history.sqlite").is_file():
                from project_workspace.chat.repository import ChatRepository

                ChatRepository(root).ensure_transcript()
    return target


def _mtime_iso(path: Path) -> str:
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0).isoformat()


def _is_text_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    if path.name in {"POSCAR", "CONTCAR", "INCAR", "KPOINTS", "POTCAR"}:
        return True
    return False


def _should_watch(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix.lower() in WATCH_EXTENSIONS:
        return True
    parts = {part.lower() for part in path.parts}
    return "logs" in parts


def directory_fingerprint(listing: DirectoryListing) -> str:
    blob = "|".join(
        f"{entry.path}:{entry.entry_type}:{entry.mtime}:{entry.size}" for entry in listing.entries
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def stat_path(project_root: Path, relative_path: str = "") -> PathStat:
    target = prepare_project_path(project_root, relative_path)
    rel = (
        ""
        if target.resolve() == project_root.resolve()
        else str(target.relative_to(project_root.resolve())).replace("\\", "/")
    )

    if target.is_dir():
        listing = list_directory(project_root, relative_path)
        return PathStat(
            path=listing.path,
            entry_type="dir",
            size=None,
            mtime=_mtime_iso(target),
            is_text=False,
            watch=False,
            fingerprint=directory_fingerprint(listing),
        )

    if not target.is_file():
        raise ValueError("Path not found")

    stat = target.stat()
    return PathStat(
        path=rel,
        entry_type="file",
        size=stat.st_size,
        mtime=_mtime_iso(target),
        is_text=_is_text_file(target),
        watch=_should_watch(target),
        fingerprint=None,
    )


def list_directory(project_root: Path, relative_path: str = "") -> DirectoryListing:
    directory = prepare_project_path(project_root, relative_path)
    if not directory.is_dir():
        raise ValueError("Not a directory")

    rel_base = "" if directory == project_root.resolve() else str(directory.relative_to(project_root.resolve())).replace("\\", "/")

    entries: list[FileEntry] = []
    children = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    for child in children:
        if child.name.startswith("."):
            continue
        child_rel = child.relative_to(project_root.resolve()).as_posix()
        if child.is_dir():
            entries.append(
                FileEntry(
                    name=child.name,
                    path=child_rel,
                    entry_type="dir",
                    size=None,
                    mtime=_mtime_iso(child),
                    is_text=False,
                    watch=False,
                )
            )
        else:
            stat = child.stat()
            entries.append(
                FileEntry(
                    name=child.name,
                    path=child_rel,
                    entry_type="file",
                    size=stat.st_size,
                    mtime=_mtime_iso(child),
                    is_text=_is_text_file(child),
                    watch=_should_watch(child),
                )
            )

    return DirectoryListing(path=rel_base, entries=entries)


def read_text_content(project_root: Path, relative_path: str) -> TextFileContent:
    target = prepare_project_path(project_root, relative_path)
    if not target.is_file():
        raise ValueError("Not a file")

    if not _is_text_file(target):
        raise ValueError("File is not available for text preview")

    size = target.stat().st_size
    truncated = size > MAX_PREVIEW_BYTES
    read_size = MAX_PREVIEW_BYTES if truncated else size

    with target.open("rb") as handle:
        raw = handle.read(read_size)

    try:
        content = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        content = raw.decode("utf-8", errors="replace")
        encoding = "utf-8-replace"

    if truncated:
        content += f"\n\n… truncated ({size:,} bytes total)"

    return TextFileContent(
        path=target.relative_to(project_root.resolve()).as_posix(),
        content=content,
        size=size,
        mtime=_mtime_iso(target),
        truncated=truncated,
        watch=_should_watch(target),
        encoding=encoding,
    )
