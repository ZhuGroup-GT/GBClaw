"""Deep Agents backend confined to one disposable Bubblewrap workspace."""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

from domains.coding.settings import CodingConfig
from domains.coding.resources import CodingResourceAllocation
from domains.coding.environments import CodingEnvironment
from domains.coding.budget import CodingTaskBudget
from domains.coding.limits.process_limits import make_process_limits
from domains.coding.sandbox.base import CodingSandbox
from core.execution.sessions import (
    is_session_cancelled,
    register_process,
    terminate_process_tree,
    unregister_process,
)


_MAX_COMMAND_CHARS = 20_000
_MPI_LAUNCHER_RE = re.compile(r"(?:^|[\s/])(mpirun|mpiexec)(?=$|\s)")
_MPI_RANK_RE = re.compile(r"(?:^|\s)(?:-n|-np|--np)(?:\s+|=)(\d+)(?=$|\s)")


@dataclass(frozen=True)
class CommandRecord:
    command: str
    exit_code: int | None
    elapsed_seconds: float
    timed_out: bool = False
    truncated: bool = False


class BubblewrapSandboxBackend(BaseSandbox, CodingSandbox):
    """Expose only ``/inputs`` (read-only), ``/workspace`` and disposable ``/tmp``.

    The selected allowlisted runtime environment is mounted read-only. The repository,
    project metadata, manifests, published output directory, user home directory, host
    process namespace, and host network are not mounted.
    """

    enable_capture_offload = False

    def __init__(
        self,
        *,
        sandbox_id: str,
        inputs_root: Path,
        workspace_root: Path,
        temp_root: Path,
        config: CodingConfig,
        runtime_environment: CodingEnvironment | None = None,
        resource_allocation: CodingResourceAllocation | None = None,
        session_key: str | None = None,
        task_budget: CodingTaskBudget | None = None,
    ) -> None:
        self._id = sandbox_id
        self.inputs_root = inputs_root.resolve()
        self.workspace_root = workspace_root.resolve()
        self.temp_root = temp_root.resolve()
        self.config = config
        self.runtime_environment = runtime_environment or CodingEnvironment(
            alias="current",
            prefix=config.environment_prefix,
            description="Current service Python environment.",
            capabilities=frozenset(),
        )
        self.resource_allocation = resource_allocation or CodingResourceAllocation(
            compute_slots=config.default_compute_slots,
            mpi_ranks=1,
            total_compute_slots=config.total_compute_slots,
        )
        if (
            self.resource_allocation.total_compute_slots
            != config.total_compute_slots
        ):
            raise ValueError("coding allocation was created for a different total budget")
        if self.resource_allocation.compute_slots > config.total_compute_slots:
            raise ValueError("coding allocation exceeds the configured compute-slot budget")
        if (
            self.resource_allocation.mpi_enabled
            and "mpi" not in self.runtime_environment.capabilities
        ):
            raise ValueError(
                "selected coding environment does not grant the mpi capability"
            )
        self.session_key = session_key
        self.task_budget = task_budget or CodingTaskBudget(config.task_timeout_seconds)
        self._records: list[CommandRecord] = []
        self._records_lock = threading.Lock()
        self._file_lock = threading.Lock()
        self._execute_lock = threading.Lock()
        self._activity_lock = threading.RLock()
        self._sealed = False
        self._active_processes = set()
        self._process_lock = threading.Lock()
        for path in (self.inputs_root, self.workspace_root, self.temp_root):
            path.mkdir(parents=True, exist_ok=True)
        if not self.config.bubblewrap_path.is_file():
            raise RuntimeError(
                f"Bubblewrap executable not found: {self.config.bubblewrap_path}"
            )
        if not shutil.which("prlimit"):
            raise RuntimeError("Coding requires prlimit to enforce configured process resource limits")
        if not self.config.environment_prefix.is_dir():
            raise RuntimeError(
                "Coding environment prefix is unavailable: "
                f"{self.config.environment_prefix}"
            )
        if not self.runtime_environment.available:
            raise RuntimeError(
                "Coding runtime environment is unavailable: "
                f"{self.runtime_environment.alias}"
            )
        self.task_budget.check()
        self.process_limits, self.resource_fallback_reason = make_process_limits(
            config, self.resource_allocation.compute_slots)

    def ensure_active(self):
        self.task_budget.check()
        if self._sealed:
            raise RuntimeError("Coding sandbox is sealed.")
        if is_session_cancelled(self.session_key):
            self.task_budget.cancel("Coding task was cancelled.")
            self.task_budget.check()

    def resource_record(self):
        return {**self.process_limits.public_record(), "fallback_reason": self.resource_fallback_reason}

    def shutdown(self):
        self.task_budget.cancel("Coding worker has exited; sandbox is closed.")
        with self._process_lock:
            processes = list(self._active_processes)
        for proc in processes:
            terminate_process_tree(proc)
        self.process_limits.kill()
        with self._activity_lock:
            if getattr(self, "_shutdown", False):
                return
            self._shutdown = True
            self._sealed = True
            self.process_limits.close()

    @property
    def id(self) -> str:
        return self._id

    @property
    def command_records(self) -> list[dict]:
        with self._records_lock:
            return [asdict(item) for item in self._records]

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        with self._activity_lock, self._execute_lock:
            if self._sealed:
                return ExecuteResponse(
                    output="Coding sandbox is sealed after publication.",
                    exit_code=126,
                )
            try:
                self.ensure_active()
            except TimeoutError as exc:
                return ExecuteResponse(output=str(exc), exit_code=124)
            except RuntimeError as exc:
                return ExecuteResponse(output=str(exc), exit_code=130)
            return self._execute_unlocked(command, timeout=timeout)

    def _execute_unlocked(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        if not isinstance(command, str) or not command.strip():
            return ExecuteResponse(output="Command must not be empty.", exit_code=126)
        if len(command) > _MAX_COMMAND_CHARS or "\x00" in command:
            return ExecuteResponse(
                output=(
                    f"Command must contain at most {_MAX_COMMAND_CHARS:,} characters "
                    "and no NUL bytes."
                ),
                exit_code=126,
            )
        mpi_error = self._validate_mpi_command(command)
        if mpi_error:
            return ExecuteResponse(output=mpi_error, exit_code=126)
        if is_session_cancelled(self.session_key):
            return ExecuteResponse(output="Coding task was cancelled.", exit_code=130)
        requested = self.config.command_timeout_seconds if timeout is None else int(timeout)
        if requested <= 0:
            requested = self.config.command_timeout_seconds
        remaining = self.task_budget.remaining()
        effective_timeout = min(
            requested,
            self.config.command_timeout_seconds,
            remaining,
        )
        if effective_timeout <= 0:
            return ExecuteResponse(
                output="Coding task time limit exceeded.",
                exit_code=124,
            )
        if self._writable_size() > self.config.max_workspace_bytes:
            return ExecuteResponse(
                output="Workspace quota exceeded before command execution.",
                exit_code=125,
            )

        started = time.monotonic()
        timed_out = False
        quota_exceeded = False
        output_exceeded = False
        resource_error = None
        cancelled = False
        exit_code: int | None = None
        output = b""
        with (
            tempfile.TemporaryFile(mode="w+b", dir=self.temp_root) as capture,
            tempfile.TemporaryFile(
                mode="w+b", dir=self.temp_root
            ) as seccomp_filter,
        ):
            seccomp_filter.write(
                self._isolated_network_filter()
                if self.resource_allocation.mpi_enabled
                else self._network_deny_filter()
            )
            seccomp_filter.flush()
            seccomp_filter.seek(0)
            argv = self.process_limits.wrap_argv(self._command_argv(command, seccomp_filter.fileno()))
            try:
                proc = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=capture,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    pass_fds=(seccomp_filter.fileno(),),
                )
            except OSError as exc:
                return ExecuteResponse(
                    output=f"Unable to start coding sandbox: {exc}",
                    exit_code=126,
                )
            register_process(self.session_key, proc)
            with self._process_lock:
                self._active_processes.add(proc)
            try:
                self.process_limits.start(proc.pid)
                deadline = time.monotonic() + effective_timeout
                while exit_code is None:
                    try:
                        exit_code = proc.wait(timeout=0.25)
                    except subprocess.TimeoutExpired:
                        if (
                            os.fstat(capture.fileno()).st_size
                            > self.config.max_output_bytes
                        ):
                            output_exceeded = True
                            terminate_process_tree(proc)
                            exit_code = proc.wait()
                        elif self._writable_size() > self.config.max_workspace_bytes:
                            quota_exceeded = True
                            terminate_process_tree(proc)
                            exit_code = proc.wait()
                        elif time.monotonic() >= deadline:
                            timed_out = True
                            terminate_process_tree(proc)
                            exit_code = proc.wait()
                    resource_error = self.process_limits.check()
                    try:
                        self.ensure_active()
                    except TimeoutError:
                        timed_out = True
                    except RuntimeError:
                        cancelled = True
                    if resource_error or cancelled or timed_out:
                        terminate_process_tree(proc)
                        self.process_limits.kill()
                        exit_code = proc.wait()
                output_exceeded = (
                    output_exceeded
                    or os.fstat(capture.fileno()).st_size
                    > self.config.max_output_bytes
                )
                capture.seek(0)
                output = capture.read(self.config.max_output_bytes + 1)
            finally:
                try:
                    if proc.poll() is None:
                        terminate_process_tree(proc)
                    try:
                        self.process_limits.kill()
                    finally:
                        self.process_limits.finish()
                finally:
                    with self._process_lock:
                        self._active_processes.discard(proc)
                    unregister_process(self.session_key, proc)

        workspace_exceeded = (
            quota_exceeded
            or self._writable_size() > self.config.max_workspace_bytes
        )
        truncated = len(output) > self.config.max_output_bytes
        if truncated:
            output = output[: self.config.max_output_bytes]
        text = output.decode("utf-8", errors="replace")
        if timed_out:
            text = f"{text}\nCommand timed out after {effective_timeout} seconds.".strip()
            exit_code = 124
        if cancelled:
            text = f"{text}\nCoding task was cancelled or its sandbox closed.".strip()
            exit_code = 130
        if resource_error:
            self.task_budget.cancel(resource_error)
            text = f"{text}\n{resource_error}".strip()
            exit_code = 125
        if workspace_exceeded:
            text = f"{text}\nWorkspace quota exceeded.".strip()
            exit_code = 125
        if output_exceeded:
            text = f"{text}\nCommand output limit exceeded.".strip()
            exit_code = 125
        record = CommandRecord(
            command=str(command)[:4000],
            exit_code=exit_code,
            elapsed_seconds=round(time.monotonic() - started, 3),
            timed_out=timed_out,
            truncated=truncated,
        )
        with self._records_lock:
            self._records.append(record)
        return ExecuteResponse(output=text, exit_code=exit_code, truncated=truncated)

    def upload_files(
        self,
        files: list[tuple[str, bytes]],
    ) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for virtual_path, content in files:
            try:
                if is_session_cancelled(self.session_key):
                    raise ValueError("coding task was cancelled")
                with self._activity_lock, self._file_lock:
                    self.ensure_active()
                    if self._sealed:
                        raise ValueError("coding sandbox is sealed after publication")
                    host_path = self._host_path(virtual_path, writable=True)
                    host_path.parent.mkdir(parents=True, exist_ok=True)
                    target_root = self._root_for_virtual(virtual_path)
                    if not host_path.parent.resolve().is_relative_to(target_root):
                        raise ValueError("parent path escapes writable mount")
                    if target_root in {self.workspace_root, self.temp_root}:
                        existing_size = (
                            host_path.stat().st_size if host_path.is_file() else 0
                        )
                        projected = self._writable_size() - existing_size + len(content)
                        if projected > self.config.max_workspace_bytes:
                            raise ValueError("workspace quota exceeded")
                    host_path.write_bytes(content)
                responses.append(FileUploadResponse(path=virtual_path))
            except (OSError, ValueError, RuntimeError) as exc:
                responses.append(FileUploadResponse(path=virtual_path, error=str(exc)))
        return responses

    @contextmanager
    def publication_guard(self):
        """Exclude parallel shell/file mutations during validation and publication."""
        with self._activity_lock:
            yield

    def seal(self) -> None:
        """Reject all future mutations after durable publication."""
        with self._activity_lock:
            self._sealed = True

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for virtual_path in paths:
            try:
                host_path = self._host_path(virtual_path, writable=False)
                if not host_path.exists():
                    responses.append(
                        FileDownloadResponse(path=virtual_path, error="file_not_found")
                    )
                elif host_path.is_dir():
                    responses.append(
                        FileDownloadResponse(path=virtual_path, error="is_directory")
                    )
                else:
                    responses.append(
                        FileDownloadResponse(path=virtual_path, content=host_path.read_bytes())
                    )
            except (OSError, ValueError) as exc:
                responses.append(FileDownloadResponse(path=virtual_path, error=str(exc)))
        return responses

    def _command_argv(self, command: str, seccomp_fd: int) -> list[str]:
        bwrap = [
            str(self.config.bubblewrap_path),
            "--die-with-parent",
            "--new-session",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--cap-drop",
            "ALL",
            "--clearenv",
            "--ro-bind",
            "/usr",
            "/usr",
            "--tmpfs",
            "/usr/local",
            "--remount-ro",
            "/usr/local",
            "--symlink",
            "usr/bin",
            "/bin",
            "--symlink",
            "usr/lib",
            "/lib",
        ]
        if self.resource_allocation.mpi_enabled:
            # MPI may use sockets only inside a fresh network namespace. Never
            # fall back to the host namespace if this kernel operation is denied.
            bwrap.insert(bwrap.index("--cap-drop"), "--unshare-net")
        if Path("/usr/lib64").exists():
            bwrap.extend(("--symlink", "usr/lib64", "/lib64"))
        bwrap.extend(("--dir", "/etc"))
        system_paths = ["/etc/ld.so.cache", "/etc/fonts", "/etc/ssl/certs"]
        if self.resource_allocation.mpi_enabled:
            system_paths.extend(("/etc/hosts", "/etc/nsswitch.conf"))
        for system_path in system_paths:
            if Path(system_path).exists():
                bwrap.extend(("--ro-bind", system_path, system_path))
        bwrap.extend(
            (
                "--dir",
                "/opt",
                "--ro-bind",
                str(self.config.environment_prefix),
                "/opt/gbclaw",
                "--ro-bind",
                str(self.runtime_environment.prefix),
                "/opt/runtime",
                "--ro-bind",
                str(self.inputs_root),
                "/inputs",
                "--bind",
                str(self.workspace_root),
                "/workspace",
                "--bind",
                str(self.temp_root),
                "/tmp",
            )
        )

        def add_original_prefix_alias(prefix: Path, target: str) -> None:
            """Preserve absolute Conda shebangs without mounting the surrounding tree."""
            reserved_roots = {
                "bin",
                "dev",
                "etc",
                "inputs",
                "lib",
                "lib64",
                "opt",
                "proc",
                "tmp",
                "usr",
                "workspace",
            }
            parts = prefix.resolve().parts
            if len(parts) < 2 or parts[1] in reserved_roots:
                return
            current = Path("/")
            for part in parts[1:-1]:
                current /= part
                bwrap.extend(("--dir", current.as_posix()))
            bwrap.extend(("--symlink", target, prefix.as_posix()))

        if self.runtime_environment.prefix == self.config.environment_prefix:
            add_original_prefix_alias(
                self.runtime_environment.prefix,
                "/opt/runtime",
            )
        else:
            add_original_prefix_alias(
                self.config.environment_prefix,
                "/opt/gbclaw",
            )
            add_original_prefix_alias(
                self.runtime_environment.prefix,
                "/opt/runtime",
            )
        mpi_executables = (
            "mpirun",
            "mpiexec",
            "mpiexec.hydra",
            "mpiexec.gforker",
            "orterun",
        )
        lammps_executables = (
            "lmp",
            "lmp_mpi",
            "lmp_serial",
            "lammps",
        )

        def block_prefix_executable(
            prefix: Path,
            sandbox_prefix: str,
            executable: str,
        ) -> None:
            if (prefix / "bin" / executable).exists():
                bwrap.extend(
                    (
                        "--ro-bind",
                        "/dev/null",
                        f"{sandbox_prefix}/bin/{executable}",
                    )
                )

        for executable in (*mpi_executables, *lammps_executables):
            block_prefix_executable(
                self.config.environment_prefix,
                "/opt/gbclaw",
                executable,
            )
        runtime_blocked = [] if self.resource_allocation.mpi_enabled else list(
            mpi_executables
        )
        if "lammps" not in self.runtime_environment.capabilities:
            runtime_blocked.extend(lammps_executables)
        for executable in runtime_blocked:
            block_prefix_executable(
                self.runtime_environment.prefix,
                "/opt/runtime",
                executable,
            )
        for executable in (*mpi_executables, *lammps_executables):
            system_executable = Path("/usr/bin") / executable
            if system_executable.exists() and not system_executable.is_symlink():
                bwrap.extend(
                    ("--ro-bind", "/dev/null", f"/usr/bin/{executable}")
                )
        bwrap.extend(
            (
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--chdir",
                "/workspace",
                "--setenv",
                "HOME",
                "/tmp",
                "--setenv",
                "TMPDIR",
                "/tmp",
                "--setenv",
                "MPLCONFIGDIR",
                "/tmp/matplotlib",
                "--setenv",
                "XDG_CACHE_HOME",
                "/tmp/cache",
                "--setenv",
                "FONTCONFIG_FILE",
                "/etc/fonts/fonts.conf",
                "--setenv",
                "PYTHONNOUSERSITE",
                "1",
                "--setenv",
                "CONDA_PREFIX",
                "/opt/runtime",
                "--setenv",
                "CONDA_DEFAULT_ENV",
                self.runtime_environment.alias,
                "--setenv",
                "GBCLAW_CODING_ENVIRONMENT",
                self.runtime_environment.alias,
                "--setenv",
                "GBCLAW_CODING_COMPUTE_SLOTS",
                str(self.resource_allocation.compute_slots),
                "--setenv",
                "GBCLAW_CODING_MPI_RANK_LIMIT",
                str(self.resource_allocation.mpi_ranks),
                "--setenv",
                "OPENBLAS_NUM_THREADS",
                self._thread_count(),
                "--setenv",
                "OMP_NUM_THREADS",
                self._thread_count(),
                "--setenv",
                "MKL_NUM_THREADS",
                self._thread_count(),
                "--setenv",
                "NUMEXPR_NUM_THREADS",
                self._thread_count(),
                "--setenv",
                "PATH",
                "/opt/runtime/bin:/opt/gbclaw/bin:/usr/bin:/bin",
                "--seccomp",
                str(seccomp_fd),
                "--",
                "/bin/sh",
                "-lc",
                command,
            )
        )
        prlimit = shutil.which("prlimit")
        if not prlimit:
            raise RuntimeError("prlimit is unavailable; refusing to execute without resource limits")
        return [
            prlimit,
            f"--as={self.config.memory_bytes}",
            f"--cpu={self.config.cpu_seconds}",
            f"--nproc={self._host_task_count() + self._sandbox_process_limit()}",
            f"--nofile={self.config.max_open_files}",
            f"--fsize={self.config.max_workspace_bytes}",
            "--",
            *bwrap,
        ]

    def _thread_count(self) -> str:
        if self.resource_allocation.mpi_enabled:
            return "1"
        return str(self.resource_allocation.compute_slots)

    def _sandbox_process_limit(self) -> int:
        dynamic_limit = max(
            32,
            self.resource_allocation.compute_slots * 4 + 8,
        )
        return min(self.config.max_processes, dynamic_limit)

    def _validate_mpi_command(self, command: str) -> str | None:
        if not _MPI_LAUNCHER_RE.search(command):
            return None
        if not self.resource_allocation.mpi_enabled:
            return "MPI requires an explicit host allocation with mpi_ranks > 1."
        ranks = [int(value) for value in _MPI_RANK_RE.findall(command)]
        if not ranks:
            return "MPI commands must include a literal -n/-np rank count."
        if max(ranks) > self.resource_allocation.mpi_ranks:
            return (
                f"MPI command requests {max(ranks)} ranks, exceeding the sandbox "
                f"allocation of {self.resource_allocation.mpi_ranks}."
            )
        return None

    @staticmethod
    def _host_task_count() -> int:
        """Return the current real-UID task baseline used by RLIMIT_NPROC.

        Linux accounts RLIMIT_NPROC across the host real UID, not just the new
        PID namespace. Adding the sandbox allowance to this baseline avoids
        blocking Bubblewrap when the API server or test runner already owns
        processes/threads, while still bounding how many new ones the command can add.
        """
        uid = os.getuid()
        count = 0
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                if entry.stat().st_uid == uid:
                    count += sum(
                        1
                        for task in (entry / "task").iterdir()
                        if task.name.isdigit()
                    )
            except OSError:
                continue
        return count

    @staticmethod
    def _network_deny_filter() -> bytes:
        """Return classic BPF that makes networking syscalls fail with EPERM.

        A network namespace is preferable, but some managed kernels block the
        NETLINK_ROUTE socket Bubblewrap uses to configure loopback. Seccomp is
        fail-closed here and also blocks AF_UNIX sockets; the coding worker does
        not need sockets of any kind.
        """
        return BubblewrapSandboxBackend._seccomp_filter(block_network=True)

    @staticmethod
    def _isolated_network_filter() -> bytes:
        """Permit sockets only when Bubblewrap also creates a private net namespace."""
        return BubblewrapSandboxBackend._seccomp_filter(block_network=False)

    @staticmethod
    def _seccomp_filter(*, block_network: bool) -> bytes:
        machine = os.uname().machine.lower()
        if machine in {"x86_64", "amd64"}:
            audit_arch = 0xC000003E
            reject_x32 = True
            network_syscall_numbers = (
                41,
                42,
                43,
                44,
                45,
                46,
                47,
                48,
                49,
                50,
                51,
                52,
                53,
                54,
                55,
                288,
                299,
                307,
            )
        elif machine in {"aarch64", "arm64"}:
            audit_arch = 0xC00000B7
            reject_x32 = False
            network_syscall_numbers = tuple(range(198, 213)) + (242, 243, 269)
        else:
            raise RuntimeError(
                f"coding sandbox has no audited network seccomp policy for {machine}"
            )

        # struct sock_filter { unsigned short code; unsigned char jt, jf; unsigned int k; }
        instructions = [
            (0x20, 0, 0, 4),  # LD W ABS seccomp_data.arch
            (0x15, 1, 0, audit_arch),  # JEQ expected arch, else terminate
            (0x06, 0, 0, 0x80000000),  # RET KILL_PROCESS
            (0x20, 0, 0, 0),  # LD W ABS seccomp_data.nr
        ]
        if reject_x32:
            instructions.extend(
                (
                    (0x35, 0, 1, 0x40000000),  # JGE __X32_SYSCALL_BIT
                    (0x06, 0, 0, 0x00050001),  # RET ERRNO(EPERM)
                )
            )
        syscall_numbers = network_syscall_numbers if block_network else ()
        for syscall_number in syscall_numbers:
            instructions.append((0x15, 0, 1, syscall_number))  # JEQ, else skip reject
            instructions.append((0x06, 0, 0, 0x00050001))  # RET ERRNO(EPERM)
        instructions.append((0x06, 0, 0, 0x7FFF0000))  # RET ALLOW
        return b"".join(struct.pack("HBBI", *item) for item in instructions)

    def _root_for_virtual(self, virtual_path: str) -> Path:
        path = PurePosixPath(virtual_path)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("sandbox paths must be absolute and may not contain '..'")
        if path == PurePosixPath("/workspace") or path.is_relative_to("/workspace"):
            return self.workspace_root
        if path == PurePosixPath("/tmp") or path.is_relative_to("/tmp"):
            return self.temp_root
        if path == PurePosixPath("/inputs") or path.is_relative_to("/inputs"):
            return self.inputs_root
        raise ValueError("path is outside /inputs, /workspace, and /tmp")

    def _host_path(self, virtual_path: str, *, writable: bool) -> Path:
        path = PurePosixPath(virtual_path)
        root = self._root_for_virtual(virtual_path)
        if writable and root == self.inputs_root:
            raise ValueError("/inputs is read-only")
        relative = path.relative_to(f"/{path.parts[1]}")
        host_path = root.joinpath(*relative.parts)
        resolved = host_path.resolve(strict=False)
        if not resolved.is_relative_to(root):
            raise ValueError("path escapes sandbox mount")
        return resolved

    def _writable_size(self) -> int:
        return self._tree_size(self.workspace_root) + self._tree_size(self.temp_root)

    @staticmethod
    def _tree_size(tree_root: Path) -> int:
        total = 0
        for root, _dirs, files in os.walk(tree_root, followlinks=False):
            root_path = Path(root)
            for name in files:
                path = root_path / name
                try:
                    if not path.is_symlink():
                        total += path.stat().st_size
                except OSError:
                    continue
        return total


__all__ = ["BubblewrapSandboxBackend", "CommandRecord"]
