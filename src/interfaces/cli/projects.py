"""Project selection menu for the terminal CLI."""

from __future__ import annotations

from project_workspace.models import ProjectMeta
from project_workspace.store import ProjectStore


def _print_project_menu(store: ProjectStore) -> list[ProjectMeta]:
    projects = store.list_projects()
    width = len(str(len(projects) + 2))
    for index, meta in enumerate(projects, start=1):
        updated = meta.updated_at[:16].replace("T", " ")
        print(f"  [{index:>{width}}] {meta.name}  ({meta.project_id}, updated {updated})")
    create_index = len(projects) + 1
    quit_index = create_index + 1
    print(f"  [{create_index:>{width}}] + Create a new project")
    print(f"  [{quit_index:>{width}}] q Quit")
    return projects


def select_or_create_project(
    store: ProjectStore,
    *,
    title: str = "Select or create a project:",
) -> ProjectMeta | None:
    """Print a numbered menu and return the chosen project, or None on quit/cancel.

    A number picks a project; a project ID typed in full works as well.
    """
    while True:
        print()
        print(title)
        projects = _print_project_menu(store)
        try:
            raw = input("? ")
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        stripped = raw.strip()
        if stripped.lower() in {"q", "quit", "/quit", "/q", "/exit"}:
            return None
        by_id = next((meta for meta in projects if meta.project_id == stripped), None)
        if by_id is not None:
            return by_id
        if not stripped.isdigit():
            print(f"  ! invalid choice: {raw!r}")
            continue
        choice = int(stripped)
        create_index = len(projects) + 1
        quit_index = create_index + 1
        if 1 <= choice <= len(projects):
            return projects[choice - 1]
        if choice == create_index:
            try:
                name = input("  project name (Enter to auto-name): ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return None
            try:
                # Empty name matches the web API: store falls back to project_id.
                return store.create(name=name or None)
            except ValueError as exc:
                print(f"  ! {exc}")
                continue
        if choice == quit_index:
            return None
        print(f"  ! invalid choice: {raw!r}")


__all__ = ["select_or_create_project"]
