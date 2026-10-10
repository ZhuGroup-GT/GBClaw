#!/usr/bin/env python3
"""Build the public, read-only DemoProj website snapshot using Python's standard library.

Run from any directory: python3 /path/to/GBClaw/html/scripts/build_demo.py
The project files stay in projects/DemoProj; only small browsing indexes and lazy
chat traces are written to html/data. SQLite connections are immutable/read-only.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "projects" / "DemoProj"
OUTPUT = ROOT / "html" / "data"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
TEXT_SUFFIXES = {
    ".json", ".md", ".txt", ".csv", ".log", ".cfg", ".lmp", ".lammpstrj",
    ".lammps", ".adp", ".ids", ".in", ".coords", ".py", ".neb", ".xml",
}
EXCLUDED_DIRS = {".cache", ".mpl_cache", "__pycache__", ".git"}
EXCLUDED_SUFFIXES = (".lock", ".sqlite-shm", ".sqlite-wal")
# Match the default interactive workspace previews, independently of its backend.
TOOL_INPUT_CHARS = 280
TOOL_OUTPUT_CHARS = 400
DELEGATE_INPUT_CHARS = 1200
DELEGATE_OUTPUT_CHARS = 8000
DELEGATE_TOOLS = {
    "delegate_plan", "delegate_csl", "delegate_lammps",
    "delegate_analysis", "delegate_coding",
}
HISTORICAL_PROJECT_PREFIX = "projects/proj-20260927-164bf2/"
IMAGE_PATTERN = re.compile(
    r"artifacts/[^\s`\"'<>|,;\[\]{}]+?\.(?:png|jpe?g|gif|webp|bmp|svg)(?=$|[?#\s)\]},.;:!\"'`<>])",
    re.IGNORECASE,
)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def public_text(value: str) -> str:
    """Keep links usable after the source project folder was renamed for this demo."""
    return value.replace(HISTORICAL_PROJECT_PREFIX, "projects/DemoProj/")


def database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def existing_path(value: str) -> str | None:
    """Resolve historical artifact references against this exported project only."""
    if not isinstance(value, str):
        return None
    candidate = unquote(value.strip().replace("\\/", "/")).split("?", 1)[0].split("#", 1)[0]
    if len(candidate) > 2048 or any(character in candidate for character in '\n\r\t"`<>'):
        return None
    if "artifacts/" in candidate:
        candidate = candidate[candidate.index("artifacts/"):]
    if not candidate.startswith("artifacts/") or "\\" in candidate:
        return None
    parts = candidate.split("/")
    if any(part in {"", ".", ".."} or len(part.encode("utf-8")) > 255 for part in parts):
        return None
    path = PROJECT / candidate
    if not path.is_file() or not path.resolve().is_relative_to(PROJECT.resolve()):
        return None
    return candidate


def image_paths(value) -> list[str]:
    """Collect existing image references, respecting recorded presentation policies."""
    found = {}

    def visit(item, depth=0):
        if depth > 40:
            return
        if isinstance(item, dict):
            presentation = item.get("result") if isinstance(item.get("result"), dict) else item
            if presentation.get("presentation_policy") == "references_only":
                return
            if presentation.get("presentation_policy") == "summary_only":
                visit(presentation.get("image_paths", []), depth + 1)
                return
            if presentation.get("summary_image_path"):
                visit(presentation["summary_image_path"], depth + 1)
                visit(presentation.get("image_paths", []), depth + 1)
                return
            for child in item.values():
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
        elif isinstance(item, str):
            try:
                parsed = json.loads(item)
            except (json.JSONDecodeError, ValueError):
                parsed = None
            if parsed is not None and parsed != item:
                visit(parsed, depth + 1)
                return
            normalized = existing_path(item)
            if normalized and Path(normalized).suffix.lower() in IMAGE_SUFFIXES:
                found[normalized] = None
            for match in IMAGE_PATTERN.finditer(item.replace("\\/", "/")):
                normalized = existing_path(match.group(0))
                if normalized:
                    found[normalized] = None

    visit(value)
    return list(found)


def bounded_preview(value: str, limit: int) -> str:
    """Keep an exact text prefix, including the ellipsis within the character limit."""
    text = value.strip()
    return text if len(text) <= limit else f"{text[:limit - 1]}…"


def delegate_payload(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def preview_tool_input(value: str, tool_name: str | None = None) -> str:
    if tool_name in DELEGATE_TOOLS:
        task = delegate_payload(value).get("task")
        if task is not None and str(task).strip():
            return bounded_preview(str(task), DELEGATE_INPUT_CHARS)
    return bounded_preview(value, TOOL_INPUT_CHARS)


def preview_tool_output(value: str, tool_name: str | None = None) -> str:
    if tool_name in DELEGATE_TOOLS:
        payload = delegate_payload(value)
        result = payload.get("result")
        candidates = [payload.get("summary")]
        if isinstance(result, dict):
            candidates.append(result.get("summary"))
        candidates.append(payload.get("error"))
        for candidate in candidates:
            summary = str(candidate or "").strip()
            if summary:
                return bounded_preview(summary, DELEGATE_OUTPUT_CHARS)
    return bounded_preview(value, TOOL_OUTPUT_CHARS)


def is_text(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    if path.suffix.lower() in IMAGE_SUFFIXES or path.suffix.lower() in {".sqlite", ".npz"}:
        return False
    with path.open("rb") as stream:
        sample = stream.read(4096)
    if b"\0" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def file_indexes() -> tuple[dict, dict]:
    directories = {"": []}
    files = {}

    def walk(directory: Path):
        relative_dir = directory.relative_to(PROJECT).as_posix()
        if relative_dir == ".":
            relative_dir = ""
        entries = []
        for path in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())):
            if path.name in EXCLUDED_DIRS or path.name.startswith(".") or path.name.endswith(EXCLUDED_SUFFIXES):
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(PROJECT.resolve()):
                continue
            stat = path.stat()
            entry = {
                "name": path.name,
                "path": path.relative_to(PROJECT).as_posix(),
                "entry_type": "dir" if path.is_dir() else "file",
                "size": None if path.is_dir() else stat.st_size,
                "mtime": int(stat.st_mtime),
                "is_text": path.is_file() and is_text(path),
                "watch": False,
            }
            if path.is_file():
                files[entry["path"]] = entry
            elif path.is_dir():
                walk(path)
            else:
                continue
            entries.append(entry)
        directories[relative_dir] = entries

    walk(PROJECT)
    return directories, files


def history_snapshot() -> tuple[dict, int]:
    with closing(database(PROJECT / "chat" / "history.sqlite")) as connection:
        turns = [dict(row) for row in connection.execute("SELECT * FROM turns ORDER BY sequence")]
        events_by_turn = defaultdict(list)
        tool_count = 0
        for row in connection.execute("SELECT * FROM trace_events ORDER BY turn_id, sequence"):
            event = dict(row)
            original_output = event.get("output_text") or ""
            # Image references come from the complete output, even when they occur
            # after the text preview. Reasoning events retain their recorded text.
            event["image_paths"] = image_paths(original_output)
            event["output_text"] = public_text(original_output)
            event["input_json"] = public_text(event.get("input_json") or "")
            if event["event_type"] == "tool_start":
                event["input_json"] = preview_tool_input(event["input_json"], event.get("tool_name"))
            elif event["event_type"] == "tool_end":
                event["output_text"] = preview_tool_output(event["output_text"], event.get("tool_name"))
            events_by_turn[event["turn_id"]].append(event)
            tool_count += event["event_type"] == "tool_start"

    history = []
    expected_paths = set()
    for turn in turns:
        turn["content"] = public_text(turn["content"])
        turn["reasoning"] = public_text(turn["reasoning"])
        trace = events_by_turn[turn["turn_id"]]
        images = dict.fromkeys(image_paths(turn["content"]))
        for event in trace:
            images.update(dict.fromkeys(event["image_paths"]))
        turn["image_paths"] = list(images)
        detail = {**turn, "trace": trace}
        path = OUTPUT / "turns" / (turn["turn_id"] + ".json")
        expected_paths.add(path.name)
        write_json(path, detail)
        history.append({key: value for key, value in turn.items() if key != "reasoning"})
    # A new snapshot should not leave obsolete trace files from an older project.
    for path in (OUTPUT / "turns").glob("*.json"):
        if path.name not in expected_paths:
            path.unlink()
    return {"turns": history, "has_more": False}, tool_count


def memory_snapshot() -> tuple[dict, int]:
    with closing(database(PROJECT / "memory" / "index.sqlite")) as connection:
        runs = []
        for row in connection.execute("SELECT * FROM runs ORDER BY created_at, run_id"):
            record = dict(row)
            result = json.loads(record["result_json"])
            run = {key: record[key] for key in (
                "run_id", "workflow", "status", "source_run_id", "manifest_path",
                "created_at", "completed_at", "updated_at", "error",
            )}
            for key in ("summary", "purpose", "warnings"):
                if key in result:
                    run[key] = result[key]
            runs.append(run)
        artifacts = []
        for row in connection.execute(
            "SELECT artifact_id,run_id,role,path,provenance_class,media_type,size_bytes,created_at "
            "FROM artifacts ORDER BY created_at,artifact_id"
        ):
            record = dict(row)
            if existing_path(record["path"]):
                artifacts.append(record)
        fact_count = connection.execute("SELECT count(*) FROM facts").fetchone()[0]
        summary_row = connection.execute("SELECT content FROM semantic_summary LIMIT 1").fetchone()
    summary_file = PROJECT / "memory" / "summary.md"
    summary = summary_file.read_text(encoding="utf-8") if summary_file.exists() else summary_row[0]
    return {"summary": summary, "runs": runs, "artifacts": artifacts, "pending_plan": None}, fact_count


GALLERY_CHOICES = [
    ("gb-periodicity-20261008-171025-3c6369", "gb_periodicity_candidates", "Periodicity candidate evidence",
     "Saved evidence for selecting the continuous periodic-x P012 cell of the FCC Ni {110} || {111} general tilt boundary."),
    ("render-20260927-040330-4d45df", "render", "Relaxed P012 grain boundary",
     "Rendered production minimization with 11,052 atoms; the ordered grain planes and [1 −1 0] tilt axis preserve the original general boundary."),
    ("render-20260927-040330-b0c165", "gb_plan_view", "Interface plan view",
     "Plan view of the relaxed P012 boundary and periodic seam; the facing geometric layers are visual inspection aids."),
    ("stress-strain-20260927-044120-a8c0ed", "stress_strain_plot", "Forward shear response",
     "Recorded stress–strain evidence includes the dominant step 36→37 drop. A stress drop alone does not establish a structural mechanism."),
    ("stress-strain-20260927-045018-d12bcb", "stress_strain_comparison_plot", "Loading and reverse unloading",
     "Signed forward/reverse curves preserve the measured nominal-zero endpoint stress and mechanical hysteresis evidence."),
    ("neb-analysis-20260927-050841-24df21", "neb_mep_plot", "NEB energy profile",
     "Saved minimum-energy-path analysis between the specified endpoint structures; interpretation is limited to this recorded path."),
    ("dichromatic-map-20260927-051048-fb4d97", "dichromatic_map_png", "Original dichromatic map",
     "Ideal, unstrained FCC lattice projection at the actual 35.26438968° misorientation; this view does not relax a grain boundary."),
    ("dichromatic-map-20260927-053842-684790", "dichromatic_map_png", "Selected near-CSL cell",
     "Saved homogeneous strain fit with all-layer edge-vector analysis. Geometric mismatch vectors remain candidates for Burgers vectors."),
    ("stress-strain-20260927-054451-fe8285", "stress_strain_plot", "Reverse unloading stress drops",
     "Extrema-based candidates in the recorded unloading path; no event passes the automatic MAD threshold."),
    ("render-20260927-055049-03f8dd", "gb_plan_view", "Relaxed reverse step 14",
     "Atom-ID-matched displacement relative to the original P012 minimized reference, with minimum-image convention and without drift or strain subtraction."),
    ("render-20260927-055049-64f2be", "gb_plan_view", "Relaxed reverse step 29",
     "Saved post-drop plan view and displacement comparison against the original P012 minimized reference."),
    ("render-20260927-055049-97c1a5", "gb_plan_view", "Relaxed reverse step 67",
     "Saved late-unloading plan view compared with the original minimized reference; arrow length is a display scale."),
]


def gallery_snapshot(runs: list[dict]) -> list[dict]:
    """Select key existing images from their manifests, never inferred filenames."""
    indexed = {run["run_id"]: run for run in runs}
    gallery = []
    for run_id, role, title, description in GALLERY_CHOICES:
        run = indexed.get(run_id)
        if not run:
            continue
        manifest_path = PROJECT / run["manifest_path"]
        manifest = read_json(manifest_path)
        for artifact in manifest.get("files", []):
            if artifact.get("role") != role:
                continue
            relative = Path(run["manifest_path"]).parent / artifact["path"]
            path = existing_path(relative.as_posix())
            if path and Path(path).suffix.lower() in IMAGE_SUFFIXES:
                gallery.append({
                    "title": title, "path": path, "workflow": run["workflow"],
                    "run_id": run_id, "description": description,
                    "manifest_path": run["manifest_path"],
                })
                break
    return gallery


def main() -> None:
    if not PROJECT.is_dir():
        raise SystemExit(f"Demo project not found: {PROJECT}")
    project = read_json(PROJECT / "project.json")
    project["project_id"] = "DemoProj"
    directories, files = file_indexes()
    history, tool_calls = history_snapshot()
    memory, fact_count = memory_snapshot()
    demo = {
        "schema_version": 1,
        "project": project,
        "memory": memory,
        "directories": directories,
        "files": files,
        "gallery": gallery_snapshot(memory["runs"]),
        "maps": [
            {
                "title": "Original unstrained dichromatic map",
                "path": "artifacts/structure/dichromatic_map/dichromatic-map-20260927-051048-fb4d97/dichromatic_map.json",
            },
            {
                "title": "Saved aligned near-CSL cell and edge vectors",
                "path": "artifacts/structure/dichromatic_map/dichromatic-map-20260927-053842-684790/dichromatic_map.json",
            },
        ],
        "stats": {
            "turns": len(history["turns"]), "tool_calls": tool_calls,
            "runs": len(memory["runs"]), "facts": fact_count, "files": len(files),
            "images": sum(Path(path).suffix.lower() in IMAGE_SUFFIXES for path in files),
            "bytes": sum(entry["size"] for entry in files.values()),
        },
        "snapshot_notes": [
            "This is a read-only snapshot of the recorded DemoProj project.",
            "Cache directories, hidden entries, lock files and SQLite WAL/shared-memory sidecars are excluded from the file index.",
            "Tool trace previews use the default workspace character limits; complete recorded data remains available in the project files.",
        ],
    }
    write_json(OUTPUT / "demo.json", demo)
    write_json(OUTPUT / "history.json", history)
    total_bytes = sum(path.stat().st_size for path in OUTPUT.rglob("*.json"))
    print(f"Built DemoProj snapshot: {demo['stats']}, {len(demo['gallery'])} gallery images; {total_bytes:,} snapshot bytes.")


if __name__ == "__main__":
    main()
