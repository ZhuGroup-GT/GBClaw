"""Workspace infrastructure, loaded only when explicitly requested.

Importing declarations must not construct memory models before extensions have
been collected. Concrete services are loaded only when explicitly requested.
"""

from importlib import import_module

_EXPORTS = {
    "ArtifactStore": "project_memory.run_artifacts.store",
    "ChatRepository": "project_workspace.chat.repository",
    "ProjectMeta": "project_workspace.models",
    "RuntimeStore": "project_workspace.execution.store",
    "ProjectStore": "project_workspace.store",
    "get_default_projects_root": "project_workspace.paths",
}


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    value = getattr(import_module(_EXPORTS[name]), name)
    globals()[name] = value
    return value

__all__ = [
    "ArtifactStore",
    "ChatRepository",
    "ProjectMeta",
    "ProjectStore",
    "RuntimeStore",
    "get_default_projects_root",
]
