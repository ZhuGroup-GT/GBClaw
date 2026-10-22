"""What a coding run needs from its sandbox, whatever mechanism isolates it."""

from __future__ import annotations

from abc import abstractmethod
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol

from deepagents.backends.protocol import SandboxBackendProtocol

from domains.coding.budget import CodingTaskBudget
from domains.coding.environments import CodingEnvironment
from domains.coding.resources import CodingResourceAllocation
from domains.coding.settings import CodingConfig


class CodingSandbox(SandboxBackendProtocol):
    """A Deep Agents backend with the lifecycle ``CodingRunSession`` relies on.

    Shell and file operations see ``/inputs`` (read-only), ``/workspace`` and
    ``/tmp``. The session validates and publishes outputs under
    ``publication_guard``, then seals the sandbox and finally shuts it down.
    ``BubblewrapSandboxBackend`` is the built-in implementation; another one
    usually subclasses deepagents' ``BaseSandbox`` together with this class.
    """

    session_key: str | None
    task_budget: CodingTaskBudget

    @abstractmethod
    def publication_guard(self) -> AbstractContextManager[None]:
        """Exclude parallel shell/file mutations during validation and publication."""

    @abstractmethod
    def seal(self) -> None:
        """Reject all future mutations after durable publication."""

    @abstractmethod
    def shutdown(self) -> None:
        """Stop running commands and release resources; repeated calls are no-ops."""

    @abstractmethod
    def resource_record(self) -> dict:
        """The resource limits actually enforced, for the run record."""

    @property
    @abstractmethod
    def command_records(self) -> list[dict]:
        """One record per executed command, in order."""


class CodingSandboxFactory(Protocol):
    """Build the sandbox for one coding run; ``CodingRunSession.prepare`` calls it."""

    def __call__(
        self,
        *,
        sandbox_id: str,
        inputs_root: Path,
        workspace_root: Path,
        temp_root: Path,
        config: CodingConfig,
        runtime_environment: CodingEnvironment,
        resource_allocation: CodingResourceAllocation,
        session_key: str | None,
        task_budget: CodingTaskBudget,
    ) -> CodingSandbox: ...


__all__ = ["CodingSandbox", "CodingSandboxFactory"]
