"""Project-scoped, content-addressed originals of model-projected tool outputs.

These are invocation records, not new scientific runs or new scientific facts.
Scientific results keep their existing run/artifact/memory provenance. The raw
invocation record also covers unregistered and future tools, including errors.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path

_OUTPUT_ID = re.compile(r"^[0-9a-f]{64}$")


class ToolOutputStore:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.root = self.project_root / "runtime" / "tool_outputs"

    def _path(self, output_id: str) -> Path:
        if not _OUTPUT_ID.fullmatch(output_id):
            raise ValueError("Invalid tool output ID")
        path = (self.root / f"{output_id}.json").resolve()
        if not path.is_relative_to(self.project_root) or not self.root.resolve().is_relative_to(self.project_root):
            raise ValueError("Tool output path must remain inside the active project")
        return path

    def write(self, content: str | list, *, tool_name: str, scope_thread_id: str | None = None) -> dict:
        encoded = json.dumps({"tool_name": tool_name, "content": content,
                              "scope_thread_id": scope_thread_id}, ensure_ascii=False, sort_keys=True)
        output_id = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        path = self._path(output_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with tempfile.NamedTemporaryFile("w", dir=path.parent, suffix=".tmp", encoding="utf-8", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(encoded)
            try:
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return {"tool": "read_tool_output", "output_id": output_id,
                "path": path.relative_to(self.project_root).as_posix()}

    def read(self, output_id: str) -> dict:
        path = self._path(output_id)
        raw = path.read_text(encoding="utf-8")
        if hashlib.sha256(raw.encode("utf-8")).hexdigest() != output_id:
            raise ValueError("Archived tool output failed its content hash check")
        result = json.loads(raw)
        if not isinstance(result, dict) or "content" not in result:
            raise ValueError("Invalid archived tool output")
        return result
