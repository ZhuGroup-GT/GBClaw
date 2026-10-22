"""Durable run ID syntax shared by every layer, including extension declarations."""
from __future__ import annotations

from datetime import datetime
import re
import uuid

RUN_PREFIX_PATTERN = r"[a-z][a-z0-9-]*"
RUN_PREFIX_RE = re.compile(rf"^{RUN_PREFIX_PATTERN}$")
RUN_ID_PATTERN = rf"{RUN_PREFIX_PATTERN}-\d{{8}}-\d{{6}}-[a-f0-9]{{6}}"
RUN_ID_RE = re.compile(rf"^{RUN_ID_PATTERN}$")
REFERENCE_RE = re.compile(
    rf"(?<![A-Za-z0-9_-])(?:{RUN_ID_PATTERN}"
    r"|conclusion-[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})(?![A-Za-z0-9_-])"
)


def new_run_id(prefix: str, timestamp: str) -> str:
    if not RUN_PREFIX_RE.fullmatch(prefix):
        raise ValueError(f"Invalid workflow run prefix: {prefix!r}")
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return f"{prefix}-{parsed.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
