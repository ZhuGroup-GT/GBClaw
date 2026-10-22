"""Finalize delegated scientific runs when their owning chat turn is cancelled."""

from __future__ import annotations

import shutil

from extensions import workflow_memory_spec
from project_workspace.store import ProjectStore


def cancel_running_runs_for_chat(
    store: ProjectStore,
    project_id: str,
    chat_thread_id: str,
    *,
    reason: str = "Owning chat turn was interrupted.",
    cleanup_ephemeral: bool = True,
) -> list[str]:
    """Cancel running rows tagged with the exact chat thread by RunService."""
    thread_id = str(chat_thread_id or "").strip()
    if not thread_id:
        return []
    try:
        service = store.runs(project_id)
    except (OSError, ValueError):
        return []

    cancelled: list[str] = []
    after_run_id = ""
    while True:
        try:
            rows = service.memory.query_running_runs_for_chat(thread_id, after_run_id=after_run_id)
        except (OSError, ValueError):
            break
        if not rows:
            break
        # Advance by immutable identity, not an offset into rows being removed
        # from the running set. A failed cancellation must not loop forever.
        after_run_id = rows[-1].run_id
        for run in rows:
            try:
                service.cancel_run(run.run_id, error=reason)
            except (OSError, ValueError):
                # A concurrently finishing tool may already have made the row terminal.
                continue
            cancelled.append(run.run_id)
            if cleanup_ephemeral:
                _cleanup_run_scratch(service, run)
    return cancelled


def _cleanup_run_scratch(service, run) -> None:
    names = workflow_memory_spec(run.workflow).ephemeral_directories
    if not names:
        return
    try:
        # Registration and cleanup must not race between inspecting evidence
        # references and removing a scratch tree.
        with service.artifacts.manifest_lock(run.run_id):
            run_root = service.artifacts.run_directory(run.run_id).resolve()
            manifest = service.artifacts.read_manifest(run.run_id)
            permanent_paths = []
            for item in manifest.get("files", []):
                if not isinstance(item, dict):
                    raise ValueError("Cannot clean scratch with an invalid manifest entry")
                if item.get("provenance_class") not in {"primary", "derived"}:
                    continue
                relative_path = item.get("path")
                if not isinstance(relative_path, str) or not relative_path:
                    raise ValueError("Cannot clean scratch without a permanent artifact path")
                resolved = (run_root / relative_path).resolve(strict=False)
                if not resolved.is_relative_to(run_root):
                    raise ValueError("Cannot clean scratch with a permanent artifact outside its run")
                permanent_paths.append(resolved)

            for name in names:
                path = run_root / name
                if path.is_symlink():
                    continue
                scratch_root = path.resolve(strict=False)
                if scratch_root.parent != run_root:
                    continue
                # A registered alias may resolve into scratch even when its
                # manifest path starts elsewhere, e.g. results -> workspace.
                if any(evidence.is_relative_to(scratch_root) for evidence in permanent_paths):
                    continue
                shutil.rmtree(path, ignore_errors=True)
    except (OSError, ValueError):
        # The worker's own finally block remains the primary cleanup path.
        pass


__all__ = ["cancel_running_runs_for_chat"]
