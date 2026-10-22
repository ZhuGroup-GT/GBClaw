"""Derived plan and draft views for people and the workspace file browser."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from core.util.io import write_text_atomic


def active_plan_view_path(project_root: Path) -> Path:
    """Return the root-level plan view shown directly by the file browser."""
    return project_root / "plan.md"


def pending_plan_draft_view_path(project_root: Path) -> Path:
    """Return the root-level pending-draft view shown by the file browser."""
    return project_root / "plan-draft.md"


def plan_current_view_path(project_root: Path, plan_id: str) -> Path:
    return project_root / "memory" / "plans" / plan_id / "current.json"


def plan_revision_view_path(
    project_root: Path,
    plan_id: str,
    revision: int,
) -> Path:
    return (
        project_root
        / "memory"
        / "plans"
        / plan_id
        / "revisions"
        / f"revision-{revision:04d}.json"
    )


def plan_draft_view_path(project_root: Path, draft_id: str) -> Path:
    return project_root / "memory" / "plans" / "drafts" / f"{draft_id}.json"


def save_project_plan_views(
    project_root: Path,
    envelope: Mapping[str, Any],
    *,
    save_active_view: bool = True,
) -> None:
    """Materialize human/file-browser views of an authoritative SQLite plan.

    These files are deliberately derived and must never be read back as plan state.
    """
    payload = dict(envelope)
    plan = payload.get("plan")
    if not isinstance(plan, Mapping):
        raise ValueError("Project plan envelope does not contain a plan object")
    plan_id = str(plan.get("plan_id") or "").strip()
    if not plan_id:
        raise ValueError("Project plan does not contain plan_id")
    view_payload = {
        "_notice": (
            "Generated read-only view. memory/index.sqlite is authoritative; "
            "manual file edits are overwritten."
        ),
        **payload,
    }
    json_text = json.dumps(view_payload, ensure_ascii=False, indent=2) + "\n"
    _write_view(plan_current_view_path(project_root, plan_id), json_text)
    if save_active_view:
        _write_view(active_plan_view_path(project_root), _render_plan_markdown(payload))


def save_project_plan_revision_view(
    project_root: Path,
    envelope: Mapping[str, Any],
) -> None:
    """Write one immutable structural-revision view from its revision fact."""
    payload = dict(envelope)
    plan = payload.get("plan")
    if not isinstance(plan, Mapping):
        raise ValueError("Project plan envelope does not contain a plan object")
    plan_id = str(plan.get("plan_id") or "").strip()
    revision = int(plan.get("revision") or 1)
    if not plan_id:
        raise ValueError("Project plan does not contain plan_id")
    view_payload = {
        "_notice": (
            "Generated immutable revision view. memory/index.sqlite is authoritative; "
            "manual file edits are overwritten."
        ),
        **payload,
    }
    _write_view(
        plan_revision_view_path(project_root, plan_id, revision),
        json.dumps(view_payload, ensure_ascii=False, indent=2) + "\n",
    )


def save_project_plan_draft_views(
    project_root: Path,
    envelope: Mapping[str, Any],
    *,
    save_pending_view: bool,
) -> None:
    """Materialize an audited draft and, while pending, its root-level review view."""
    payload = dict(envelope)
    draft = payload.get("draft")
    if not isinstance(draft, Mapping):
        raise ValueError("Project plan draft envelope does not contain a draft object")
    draft_id = str(draft.get("draft_id") or "").strip()
    if not draft_id:
        raise ValueError("Project plan draft does not contain draft_id")
    view_payload = {
        "_notice": (
            "Generated read-only draft view. memory/index.sqlite is authoritative; "
            "manual file edits are overwritten."
        ),
        **payload,
    }
    _write_view(
        plan_draft_view_path(project_root, draft_id),
        json.dumps(view_payload, ensure_ascii=False, indent=2) + "\n",
    )
    if save_pending_view:
        _write_view(
            pending_plan_draft_view_path(project_root),
            _render_plan_draft_markdown(payload),
        )


def clear_pending_plan_draft_view(project_root: Path) -> None:
    """Remove the derived root review file after the pending pointer is cleared."""
    pending_plan_draft_view_path(project_root).unlink(missing_ok=True)


def _render_plan_markdown(envelope: Mapping[str, Any]) -> str:
    plan = envelope["plan"]
    assert isinstance(plan, Mapping)
    lines = [
        f"# {plan.get('title') or 'Project plan'}",
        "",
        "> Generated read-only view. `memory/index.sqlite` is authoritative; manual edits",
        "> to this file are overwritten by the next plan update.",
        "",
        f"- Plan ID: `{plan.get('plan_id', '')}`",
        f"- Plan revision: `{plan.get('revision', 1)}`",
        f"- Memory version: `{envelope.get('memory_version', '')}`",
        f"- Status: `{plan.get('status', '')}`",
        f"- Updated: `{plan.get('updated_at', '')}`",
        "",
        "## Objective",
        "",
        str(plan.get("objective") or "_Not specified._"),
    ]
    deliverable = str(plan.get("intended_deliverable") or "").strip()
    if deliverable:
        lines.extend(("", "## Intended deliverable", "", deliverable))

    lines.extend(("", "## Steps", ""))
    steps = plan.get("steps") or []
    for index, raw_step in enumerate(steps, start=1):
        if not isinstance(raw_step, Mapping):
            continue
        step_id = str(raw_step.get("step_id") or "")
        title = str(raw_step.get("title") or step_id)
        lines.extend(
            (
                f"### {index}. {title}",
                "",
                f"- ID: `{step_id}`",
                f"- Status: `{raw_step.get('status', '')}`",
                f"- Capability: `{raw_step.get('capability', '')}`",
                "- Depends on: "
                + (
                    ", ".join(f"`{item}`" for item in raw_step.get("depends_on") or [])
                    or "none"
                ),
                f"- Attempts: `{raw_step.get('attempts', 0)}`",
            )
        )
        description = str(raw_step.get("description") or "").strip()
        if description:
            lines.extend(("", description))
        result = str(raw_step.get("result_summary") or "").strip()
        if result:
            lines.extend(("", f"Result: {result}"))
        run_ids = raw_step.get("result_run_ids") or []
        if run_ids:
            lines.extend(("", "Run IDs: " + ", ".join(f"`{item}`" for item in run_ids)))
        blocking_reason = str(raw_step.get("blocking_reason") or "").strip()
        if blocking_reason:
            lines.extend(("", f"Blocked: {blocking_reason}"))
        error = str(raw_step.get("error") or "").strip()
        if error:
            lines.extend(("", f"Error: {error}"))
        lines.append("")

    revisions = plan.get("revision_history") or []
    if revisions:
        lines.extend(("## Revision history", ""))
        for raw_revision in revisions:
            if not isinstance(raw_revision, Mapping):
                continue
            changed = ", ".join(str(item) for item in raw_revision.get("changed_fields") or [])
            suffix = f"; changed: {changed}" if changed else ""
            lines.append(
                f"- Revision `{raw_revision.get('revision', '')}` "
                f"({raw_revision.get('requested_by', '')}, "
                f"{raw_revision.get('created_at', '')}): "
                f"{raw_revision.get('reason', '')}{suffix}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_plan_draft_markdown(envelope: Mapping[str, Any]) -> str:
    draft = envelope["draft"]
    assert isinstance(draft, Mapping)
    plan = draft.get("proposed_plan")
    if not isinstance(plan, Mapping):
        raise ValueError("Project plan draft does not contain proposed_plan")
    lines = [
        f"# Draft: {plan.get('title') or 'Project plan'}",
        "",
        "> This plan is awaiting explicit user approval and is not executable yet.",
        "> Generated read-only view; `memory/index.sqlite` is authoritative.",
        "",
        f"- Draft ID: `{draft.get('draft_id', '')}`",
        f"- Action: `{draft.get('action', '')}`",
        f"- Draft status: `{draft.get('status', '')}`",
        f"- Proposed plan ID: `{plan.get('plan_id', '')}`",
        f"- Proposed revision: `{plan.get('revision', 1)}`",
        f"- Base revision: `{draft.get('base_revision') or 'none'}`",
        f"- Memory version: `{envelope.get('memory_version', '')}`",
        f"- Updated: `{draft.get('updated_at', '')}`",
        "",
        "## Objective",
        "",
        str(plan.get("objective") or "_Not specified._"),
    ]
    deliverable = str(plan.get("intended_deliverable") or "").strip()
    if deliverable:
        lines.extend(("", "## Intended deliverable", "", deliverable))
    lines.extend(("", "## Proposed steps", ""))
    for index, raw_step in enumerate(plan.get("steps") or [], start=1):
        if not isinstance(raw_step, Mapping):
            continue
        dependencies = raw_step.get("depends_on") or []
        lines.extend(
            (
                f"### {index}. {raw_step.get('title') or raw_step.get('step_id', '')}",
                "",
                f"- ID: `{raw_step.get('step_id', '')}`",
                f"- Capability: `{raw_step.get('capability', '')}`",
                "- Depends on: "
                + (", ".join(f"`{item}`" for item in dependencies) or "none"),
                f"- Expected output: {raw_step.get('expected_output') or '_Not specified._'}",
                f"- Validation: {raw_step.get('validation') or '_Not specified._'}",
                "",
                str(raw_step.get("description") or ""),
                "",
            )
        )
    for heading, key in (
        ("Assumptions", "assumptions"),
        ("Unknowns", "unknowns"),
        ("Risks", "risks"),
        ("User decisions required", "user_decisions_required"),
    ):
        values = plan.get(key) or []
        if values:
            lines.extend((f"## {heading}", ""))
            lines.extend(f"- {item}" for item in values)
            lines.append("")
    lines.extend(
        (
            "## Decision",
            "",
            "Use the approval controls next to the chat box, or explicitly approve, reject, "
            "or request changes in the chat.",
            "",
        )
    )
    return "\n".join(lines).rstrip() + "\n"


def _write_view(path: Path, text: str) -> None:
    write_text_atomic(path, text, skip_unchanged=True)
