"""Resource and publication limits for the ephemeral coding worker."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    value = int(raw) if raw else default
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class CodingConfig:
    """Hard bounds applied by the host-side sandbox and publisher."""

    command_timeout_seconds: int
    task_timeout_seconds: int
    max_output_bytes: int
    max_workspace_bytes: int
    max_input_files: int
    max_input_bytes: int
    max_published_files: int
    max_published_bytes: int
    memory_bytes: int
    cpu_seconds: int
    max_processes: int
    max_open_files: int
    default_compute_slots: int
    total_compute_slots: int
    recursion_limit: int
    environment_prefix: Path
    bubblewrap_path: Path
    resource_mode: str = "auto"
    cgroup_root: Path | None = None

    def resolve_resource_request(
        self,
        *,
        compute_slots: int | None = None,
        mpi_ranks: int | None = None,
    ) -> tuple[int, int]:
        """Normalize one sandbox request against the process-wide slot budget."""
        resolved_mpi_ranks = 1 if mpi_ranks is None else int(mpi_ranks)
        if resolved_mpi_ranks < 1:
            raise ValueError("coding mpi_ranks must be >= 1")
        if compute_slots is None:
            resolved_compute_slots = (
                resolved_mpi_ranks
                if mpi_ranks is not None
                else self.default_compute_slots
            )
        else:
            resolved_compute_slots = int(compute_slots)
        if resolved_compute_slots < 1:
            raise ValueError("coding compute_slots must be >= 1")
        if resolved_mpi_ranks > resolved_compute_slots:
            raise ValueError("coding mpi_ranks cannot exceed compute_slots")
        if resolved_compute_slots > self.total_compute_slots:
            raise ValueError(
                "coding task requires "
                f"{resolved_compute_slots} compute slots, exceeding "
                "GBCLAW_CODING_TOTAL_COMPUTE_SLOTS="
                f"{self.total_compute_slots}"
            )
        return resolved_compute_slots, resolved_mpi_ranks

    def public_resource_limits(self) -> dict:
        return {
            "default_compute_slots": self.default_compute_slots,
            "total_compute_slots": self.total_compute_slots,
            "max_memory_bytes_per_process": self.memory_bytes,
            "max_processes_per_uid": self.max_processes,
            "max_memory_bytes_per_tree": self.memory_bytes,
            "max_tasks_per_tree": self.max_processes,
            "resource_mode": self.resource_mode,
            "task_timeout_seconds": self.task_timeout_seconds,
        }


def load_coding_config() -> CodingConfig:
    resource_mode = os.getenv("GBCLAW_CODING_RESOURCE_MODE", "auto").strip().lower()
    if resource_mode not in {"auto", "cgroup", "monitor"}:
        raise ValueError("GBCLAW_CODING_RESOURCE_MODE must be auto, cgroup or monitor")
    cgroup_root = os.getenv("GBCLAW_CODING_CGROUP_ROOT", "").strip()
    command_timeout = _positive_int("GBCLAW_CODING_COMMAND_TIMEOUT_SEC", 300)
    task_timeout = _positive_int("GBCLAW_CODING_TASK_TIMEOUT_SEC", 1800)
    if command_timeout > task_timeout:
        raise ValueError(
            "GBCLAW_CODING_COMMAND_TIMEOUT_SEC cannot exceed "
            "GBCLAW_CODING_TASK_TIMEOUT_SEC"
        )
    default_compute_slots = _positive_int(
        "GBCLAW_CODING_AGENT_COMPUTE_SLOTS",
        4,
    )
    total_compute_slots = _positive_int(
        "GBCLAW_CODING_TOTAL_COMPUTE_SLOTS",
        36,
    )
    if default_compute_slots > total_compute_slots:
        raise ValueError(
            "GBCLAW_CODING_AGENT_COMPUTE_SLOTS cannot exceed "
            "GBCLAW_CODING_TOTAL_COMPUTE_SLOTS"
        )
    return CodingConfig(
        command_timeout_seconds=command_timeout,
        task_timeout_seconds=task_timeout,
        max_output_bytes=_positive_int("GBCLAW_CODING_MAX_OUTPUT_BYTES", 100_000),
        max_workspace_bytes=(
            _positive_int("GBCLAW_CODING_MAX_WORKSPACE_MB", 128) * 1024 * 1024
        ),
        max_input_files=_positive_int("GBCLAW_CODING_MAX_INPUT_FILES", 20),
        max_input_bytes=(
            _positive_int("GBCLAW_CODING_MAX_INPUT_MB", 256) * 1024 * 1024
        ),
        max_published_files=_positive_int("GBCLAW_CODING_MAX_PUBLISHED_FILES", 20),
        max_published_bytes=(
            _positive_int("GBCLAW_CODING_MAX_PUBLISHED_MB", 32) * 1024 * 1024
        ),
        memory_bytes=_positive_int("GBCLAW_CODING_MEMORY_MB", 4096) * 1024 * 1024,
        cpu_seconds=_positive_int("GBCLAW_CODING_CPU_SECONDS", 600),
        max_processes=_positive_int("GBCLAW_CODING_MAX_PROCESSES", 96),
        max_open_files=_positive_int("GBCLAW_CODING_MAX_OPEN_FILES", 256),
        default_compute_slots=default_compute_slots,
        total_compute_slots=total_compute_slots,
        recursion_limit=_positive_int("GBCLAW_CODING_RECURSION_LIMIT", 200),
        environment_prefix=Path(sys.prefix).resolve(),
        bubblewrap_path=Path(os.getenv("GBCLAW_BWRAP_PATH", "/usr/bin/bwrap")).resolve(),
        resource_mode=resource_mode,
        cgroup_root=Path(cgroup_root) if cgroup_root else None,
    )


__all__ = ["CodingConfig", "load_coding_config"]
