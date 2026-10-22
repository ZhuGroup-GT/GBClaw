"""File hashing and atomic replacement (stdlib only; LAMMPS subprocesses import it)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any


def sha256_file(path: Path | str) -> str:
    """Stream a file's SHA-256 hex digest."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_text_atomic(
    path: Path, text: str, *, skip_unchanged: bool = False, fsync: bool = True,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if skip_unchanged:
        try:
            if path.read_text(encoding="utf-8") == text:
                return
        except FileNotFoundError:
            pass
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            if fsync:
                handle.flush()
                os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json_atomic(
    path: Path,
    payload: Any,
    *,
    skip_unchanged: bool = False,
    fsync: bool = True,
    sort_keys: bool = True,
    allow_nan: bool = True,
) -> None:
    """Write indented UTF-8 JSON; callers keep their established key ordering."""
    text = json.dumps(
        payload, ensure_ascii=False, indent=2, sort_keys=sort_keys, allow_nan=allow_nan,
    ) + "\n"
    write_text_atomic(path, text, skip_unchanged=skip_unchanged, fsync=fsync)
