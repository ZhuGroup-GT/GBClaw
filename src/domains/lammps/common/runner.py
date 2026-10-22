"""Run external LAMMPS commands inside the dedicated conda environment."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from domains.lammps.common.settings import LAMMPS_CONDA_ENV
from core.execution.process import supervise
from core.execution.sessions import is_session_cancelled


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.cancelled


def command_error_summary(command: CommandResult, *, limit: int = 2000) -> str:
    """Keep root exception/LAMMPS errors despite repeated MPI launcher output.

    Full stdout/stderr should be saved separately by the owning workflow. A
    traceback's first few lines omit its actual exception, while its tail may
    contain only MPI/Conda shutdown messages.
    """
    if limit < 1:
        raise ValueError("limit must be positive")
    causes: dict[str, None] = {}
    for text in (command.stdout, command.stderr):
        for line in text.splitlines():
            match = re.search(
                r"(\b(?:[\w.]+(?:Error|Exception)|Exception|KeyboardInterrupt|SystemExit):.*|"
                r"\bERROR(?: on proc \d+)?:.*|\[gbclaw\] LAMMPS run (?:cancelled|timed out).*)",
                line,
            )
            if match:
                causes[match[1].strip()] = None
    if causes:
        return "\n".join(list(causes)[-4:])[:limit]
    text = command.stderr.strip() or command.stdout.strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def _resolve_conda() -> str:
    conda = shutil.which("conda")
    if not conda:
        raise RuntimeError(
            "conda not found on PATH. Install conda or activate the LAMMPS environment manually."
        )
    return conda


def run_in_lammps_env(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout_sec: float | None = None,
    env: dict[str, str] | None = None,
    session_key: str | None = None,
) -> CommandResult:
    """Execute *argv* inside ``LAMMPS_CONDA_ENV`` via ``conda run``.

    When *session_key* is set, the subprocess is registered and polled for client
    disconnect / server shutdown (via ``core.execution.sessions.cancel_session``).
    """
    if not argv:
        raise ValueError("argv must not be empty")
    if is_session_cancelled(session_key):
        return CommandResult(command=list(argv), returncode=-15, stdout="",
                             stderr="[gbclaw] LAMMPS run cancelled before launch.", cancelled=True)

    conda = _resolve_conda()
    command = [
        conda,
        "run",
        "--no-capture-output",
        "-n",
        LAMMPS_CONDA_ENV,
        *argv,
    ]

    proc = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    outcome = supervise(proc, session_key=session_key, timeout_sec=timeout_sec)
    stdout, stderr = outcome.stdout or "", outcome.stderr or ""
    if outcome.cancelled:
        returncode = outcome.returncode if outcome.returncode is not None else -15
        stderr += "\n[gbclaw] LAMMPS run cancelled."
    elif outcome.timed_out:
        returncode = 124
        stderr += f"\n[gbclaw] LAMMPS run timed out after {timeout_sec}s."
    else:
        returncode = outcome.returncode if outcome.returncode is not None else 1

    return CommandResult(
        command=command,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        cancelled=outcome.cancelled,
    )
