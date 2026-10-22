#!/usr/bin/env python3
"""Build the public research showcases using Python's standard library.

Run from any directory: python3 /path/to/GBClaw/html/scripts/build_demo.py
Project files stay in projects/; only browsing indexes and lazy chat traces are
written to html/data. SQLite connections are immutable/read-only.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from html import escape
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "projects" / "Demo-GPT"
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
PROJECT_ALIASES = ()
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
    for alias in PROJECT_ALIASES:
        value = value.replace(alias, PROJECT.name)
    return value


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


def ensure_transcript() -> None:
    """Export a missing transcript without changing the recorded chat database.

    Existing exports are preserved. The downloadable export includes complete
    message/reasoning/event data; only website trace previews are truncated.
    """
    path = PROJECT / "chat" / "transcript.md"
    if path.exists():
        return
    with closing(database(PROJECT / "chat" / "history.sqlite")) as connection:
        turns = [dict(row) for row in connection.execute("SELECT * FROM turns ORDER BY sequence")]
        events = defaultdict(list)
        for row in connection.execute("SELECT * FROM trace_events ORDER BY turn_id, sequence"):
            event = dict(row)
            events[event["turn_id"]].append(event)
    sections = [
        f"# Chat transcript — {PROJECT.name}",
        "> Exported from `history.sqlite` for this showcase. Messages and recorded traces are complete; the source database is unchanged.",
    ]
    for turn in turns:
        sections.extend([
            f"## {turn['sequence']:04d} · {turn['role'].title()}",
            f"> status: `{turn['status']}` · created: `{turn['created_at']}` · thread: `{turn['thread_id']}` · turn: `{turn['turn_id']}`",
            "### Answer" if turn["role"] == "assistant" else "### Message",
            turn["content"],
        ])
        trace = events[turn["turn_id"]]
        if turn["reasoning"] or trace:
            sections.append("<details>\n<summary>Reasoning and tool trace</summary>")
            if turn["reasoning"]:
                sections.extend(["#### Reasoning", f"<pre>{escape(turn['reasoning'])}</pre>"])
            for event in trace:
                label = event["label"] or event["tool_name"] or "Reasoning"
                sections.extend([
                    f"<!-- event:{event['event_id']} -->",
                    f"#### {event['event_type']}: {label}",
                    "<pre>" + escape(json.dumps(event, ensure_ascii=False, indent=2)) + "</pre>",
                ])
            sections.append("</details>")
        sections.append("---")
    path.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
    print(f"Exported missing transcript: {path.relative_to(ROOT)} ({len(turns)} messages, {sum(map(len, events.values()))} trace events).")


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
    ("gb-periodicity-20261021-202310-f3831c", "gb_periodicity_overview", "Periodicity analysis summary",
     "Summary of periodicity candidates and supporting evidence for selecting the continuous periodic-x P012 cell."),
    ("render-20261021-202943-baa730", "render", "P012 grain boundary",
     "The 11,052-atom continuous-periodic-x cell, built using the selected P012 dimensions and the original (110) ∥ (111) general-tilt geometry."),
    ("render-20261021-202943-06cd69", "gb_plan_view", "Interface plan view",
     "Plan view after the P012 minimization, showing the interface and periodic seam."),
    ("stress-strain-20261021-212925-c51b01", "stress_strain_comparison_plot", "Loading, unloading and reloading",
     "Recorded stress–strain curves compare the first loading with the subsequent unloading and reloading cycle."),
    ("neb-analysis-20261021-215038-5ed9de", "neb_mep_plot", "NEB energy profile",
     "Saved minimum-energy-path analysis between the specified endpoint structures; interpretation is limited to this recorded path."),
    ("render-20261021-213748-f27cfe", "gb_plan_view", "Zero-strain displacement comparison",
     "Atom-ID-matched displacement from the initial zero-strain structure to the unloaded zero-strain configuration, projected into the grain-boundary plane."),
    ("render-20261021-224107-c440c3", "gb_plan_view", "Relaxed post-drop displacement",
     "Displacement between independently relaxed post-drop states from reloading steps 77 → 80, with collective grain motion retained."),
    ("dichromatic-map-20261021-233535-774f02", "dichromatic_map_png", "Selected near-CSL cell",
     "The saved four-point cell after homogeneous strain fitting, with geometric edge vectors from both FCC axial layers."),
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


DEMO_PROJECTS = {
    "Demo-GPT": {
        "data_path": "",
        "aliases": (),
        "gallery": GALLERY_CHOICES,
        "maps": [
            {"title": "Saved aligned near-CSL cell and edge vectors",
             "path": "artifacts/structure/dichromatic_map/dichromatic-map-20261021-233535-774f02/dichromatic_map.json"},
        ],
    },
}


def presentation(project_id: str) -> dict:
    return {
        "description": (
            "Explore a recorded FCC Ni grain boundary study, from CSL screening and periodic-cell selection "
            "through shear loading, unloading, reloading, and structural analysis."
        ),
        "overview": (
            "Follow a saved FCC Ni study from geometry and cell selection through simulation and analysis. "
            "Browse the original conversation, inspect project files, and explore the figures produced along the way."
        ),
        "facts": [
            {"label": "Material & boundary", "value": "FCC Ni", "detail": "(110) ∥ (111)"},
            {"label": "Ordered misorientation", "value": "35.264°", "detail": "⟨110⟩ tilt-axis family"},
            {"label": "Selected periodic cell", "value": "P012", "detail": "Continuous periodic x"},
        ],
        "workflow": ["CSL screening", "P012 selection", "Minimization", "Loading cycle", "NEB", "Displacement & near-CSL"],
        "file_notice": (
            "To keep this showcase lightweight, some large files have been removed from the project. "
            "Final NEB structures and recorded analysis results are retained."
        ),
    }


def build_project(project_id: str, settings: dict) -> None:
    global PROJECT, OUTPUT, PROJECT_ALIASES, GALLERY_CHOICES
    PROJECT = ROOT / "projects" / project_id
    OUTPUT = ROOT / "html" / "data" / settings["data_path"]
    PROJECT_ALIASES = settings["aliases"]
    GALLERY_CHOICES = settings["gallery"]
    if not PROJECT.is_dir():
        raise SystemExit(f"Demo project not found: {PROJECT}")
    project = read_json(PROJECT / "project.json")
    if project.get("project_id") != project_id or project.get("name") != project_id:
        raise SystemExit(f"Project identity does not match directory: {PROJECT}")
    ensure_transcript()
    directories, files = file_indexes()
    history, tool_calls = history_snapshot()
    memory, fact_count = memory_snapshot()
    gallery = gallery_snapshot(memory["runs"])
    if len(gallery) != len(settings["gallery"]):
        raise SystemExit(f"A configured gallery image is missing in {project_id}")
    for snapshot in settings["maps"]:
        if not existing_path(snapshot["path"]):
            raise SystemExit(f"A configured dichromatic map is missing in {project_id}")
    demo = {
        "schema_version": 1,
        "project": project,
        "showcase": {**project.get("showcase", {}), "presentation": presentation(project_id)},
        "memory": memory,
        "directories": directories,
        "files": files,
        "gallery": gallery,
        "maps": settings["maps"],
        "stats": {
            "turns": len(history["turns"]), "tool_calls": tool_calls,
            "runs": len(memory["runs"]), "facts": fact_count, "files": len(files),
            "images": sum(Path(path).suffix.lower() in IMAGE_SUFFIXES for path in files),
            "bytes": sum(entry["size"] for entry in files.values()),
        },
        "snapshot_notes": [
            f"This is a read-only snapshot of the recorded {project_id} project.",
            "Cache directories, hidden entries, lock files and SQLite WAL/shared-memory sidecars are excluded from the file index.",
            "Tool trace previews use the default workspace character limits; complete recorded data remains available in the project files.",
        ],
    }
    write_json(OUTPUT / "demo.json", demo)
    write_json(OUTPUT / "history.json", history)
    total_bytes = sum(path.stat().st_size for path in [OUTPUT / "demo.json", OUTPUT / "history.json", *(OUTPUT / "turns").glob("*.json")])
    print(f"Built {project_id} snapshot: {demo['stats']}, {len(demo['gallery'])} gallery images; {total_bytes:,} snapshot bytes.")


def main() -> None:
    projects = []
    for project_id, settings in DEMO_PROJECTS.items():
        build_project(project_id, settings)
        projects.append({"project_id": project_id, "name": project_id, "data_path": settings["data_path"]})
    write_json(ROOT / "html" / "data" / "projects.json", projects)


if __name__ == "__main__":
    main()
