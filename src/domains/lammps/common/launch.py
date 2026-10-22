"""Shared execution steps of configured LAMMPS workflow runs.

Each workflow keeps its own inputs, metrics and results; this module owns the
steps they all repeat: configured-run lookup, MPI-rank admission, the mpirun
command, launcher logs and diagnostics, terminal status and run completion.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from domains.lammps.common.settings import LAMMPS_AGENT_MPI_RANKS, LAMMPS_TOTAL_MPI_RANKS
from project_memory.models import RunRecord, RunStatus, Workflow
from project_memory.publication.results import RunToolResult
from project_memory.publication.service import RunService
from project_workspace.artifacts.models import ProvenanceClass
from domains.lammps.common.resources import lammps_resource_slot
from domains.lammps.common.runner import CommandResult, command_error_summary, run_in_lammps_env

LAUNCHER_LOGS = (
    ("launcher_stdout", "launcher.stdout.log"),
    ("launcher_stderr", "launcher.stderr.log"),
)


def configured_run(service: RunService, run_id: str, workflow: Workflow) -> RunRecord:
    run = service.memory.get_run(run_id)
    if run.workflow is not workflow or run.status is not RunStatus.CONFIGURED:
        raise ValueError(f"run_id must identify a configured {workflow.value} run")
    return run


def resolve_mpi_ranks(np: int | None) -> int:
    """The agent default, or an explicit rank count within the host-wide budget."""
    ranks = LAMMPS_AGENT_MPI_RANKS if np is None else np
    if isinstance(ranks, bool) or not isinstance(ranks, int):
        raise ValueError("np must be an integer")
    if ranks < 1:
        raise ValueError("np must be >= 1")
    if ranks > LAMMPS_TOTAL_MPI_RANKS:
        raise ValueError(f"np={ranks} exceeds GBCLAW_LAMMPS_TOTAL_MPI_RANKS={LAMMPS_TOTAL_MPI_RANKS}")
    return ranks


def mpirun_argv(script: Path, ranks: int, *script_args: str) -> list[str]:
    return [
        "mpirun", "--map-by", "core", "--bind-to", "core", "-n", str(ranks),
        "python", "-u", str(Path(script).resolve()), *script_args,
    ]


def launch(argv: Sequence[str], *, ranks: int, timeout_sec: float,
           session_key: str | None) -> CommandResult:
    """Wait for MPI ranks, then run *argv* in the LAMMPS environment."""
    with lammps_resource_slot(ranks, session_key=session_key):
        return run_in_lammps_env(list(argv), timeout_sec=timeout_sec, session_key=session_key)


def save_launcher_logs(output_dir: Path, command: CommandResult) -> None:
    """Keep every rank's launcher streams; import failures precede log.lammps."""
    (output_dir / LAUNCHER_LOGS[0][1]).write_text(command.stdout, encoding="utf-8")
    (output_dir / LAUNCHER_LOGS[1][1]).write_text(command.stderr, encoding="utf-8")


def register_launcher_logs(service: RunService, run_id: str, output_dir: Path, *,
                           prefix: str = "", metadata: dict | None = None) -> None:
    for role, filename in LAUNCHER_LOGS:
        if (output_dir / filename).is_file():
            service.register_artifact(run_id, role, f"{prefix}{filename}", ProvenanceClass.PRIMARY,
                                      metadata=metadata)


def command_status(
    command: CommandResult, output_dir: Path, required: Iterable[str], *, label: str,
) -> tuple[RunStatus, str]:
    """Terminal status and the root-cause diagnostic of one LAMMPS launch."""
    error = command_error_summary(command)
    if command.cancelled:
        return RunStatus.CANCELLED, error
    if not command.ok:
        return RunStatus.FAILED, error or (
            f"{label} failed without diagnostic output (exit code {command.returncode})"
        )
    if not all((output_dir / name).is_file() for name in required):
        return RunStatus.FAILED, f"{label} exited successfully but required output artifacts are missing"
    return RunStatus.COMPLETED, ""


def finish_run(
    service: RunService,
    run_id: str,
    status: RunStatus,
    *,
    result: dict[str, Any],
    progress: dict[str, Any] | None,
    error: str,
    memory_tool: str,
    cancelled_message: str,
    conclusion: dict[str, Any] | None = None,
) -> RunRecord:
    if status is RunStatus.COMPLETED:
        return service.complete_run(run_id, result=result, progress=progress,
                                    memory_tool=memory_tool, conclusion=conclusion)
    if status is RunStatus.CANCELLED:
        return service.cancel_run(run_id, error=error or cancelled_message,
                                  result=result, progress=progress)
    return service.fail_run(run_id, error=error, result=result, progress=progress)


def run_tool_result(
    service: RunService, run: RunRecord, *,
    artifact_fields: Sequence[str] = ("role", "path"), error: str | None = None,
) -> RunToolResult:
    """The model-facing run reference with its indexed artifacts."""
    artifacts = service.memory.query_artifacts(run_id=run.run_id)
    return RunToolResult(
        run_id=run.run_id,
        workflow=run.workflow,
        status=run.status,
        manifest=run.manifest_path,
        artifacts=[{field: getattr(item, field) for field in artifact_fields} for item in artifacts],
        result=run.result,
        error=run.error if error is None else error,
    )


__all__ = [
    "LAUNCHER_LOGS", "command_status", "configured_run", "finish_run", "launch", "mpirun_argv",
    "register_launcher_logs", "resolve_mpi_ranks", "run_tool_result", "save_launcher_logs",
]
