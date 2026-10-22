"""Project-wide frontend occupancy and execution ownership across processes.

Locks live outside project directories so deleting a project cannot create a
second lock inode. Never unlink these files while servers may be running.
"""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import uuid

from project_workspace.persistence.locks import file_lock
from core.util.io import write_json_atomic
from project_workspace.models import utc_now_iso


class ChatBusyError(ValueError):
    """Another frontend or execution owns this project."""


class ExecutionCancellation:
    """One shared stop signal, scoped to the exact execution generation."""

    def __init__(self, directory: Path, owner: str):
        self.directory = directory
        self.owner = owner
        self._revoked = threading.Event()

    def is_requested(self) -> bool:
        if self._revoked.is_set():
            return True
        try:
            with (self.directory / "cancel.json").open("rb") as handle:
                record = json.loads(handle.read(8192))
        except FileNotFoundError:
            return False
        if not isinstance(record, dict) or not isinstance(record.get("owner"), str):
            raise ValueError("Invalid project cancellation record")
        return record["owner"] == self.owner

    def request(self, reason: str = "user_stop") -> bool:
        with file_lock(self.directory / "gate.lock"):
            active = _live_record(self.directory / "execution.lock")
            if not active or active.get("owner") != self.owner:
                return False
            write_json_atomic(self.directory / "cancel.json", {
                "owner": self.owner, "requested_at": utc_now_iso(), "reason": reason[:1000],
            })
            return True

    def revoke(self):
        self._revoked.set()


def project_lock_directory(project_root: Path) -> Path:
    root = project_root.resolve()
    key = hashlib.sha256(str(root).encode()).hexdigest()
    directory = root.parent / ".project_leases" / key
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _open_lock(path: Path, mode=fcntl.LOCK_EX):
    handle = path.open("a+b")
    try:
        fcntl.flock(handle.fileno(), mode | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise ChatBusyError("Project is occupied; close its other frontend or wait for its execution to finish.") from exc
    except BaseException:
        handle.close()
        raise
    return handle


def _live_record(path: Path) -> dict | None:
    """Read only records whose lock is currently held, under the project gate."""
    with path.open("a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.seek(0)
            try:
                record = json.loads(handle.read(8192))
                if not isinstance(record, dict):
                    raise ValueError("Invalid ownership record")
                return record
            except (ValueError, UnicodeDecodeError) as exc:
                raise ChatBusyError("Project ownership cannot be verified; retry after its owner disconnects.") from exc
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return None


def _require_free(path: Path):
    handle = _open_lock(path)
    handle.close()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _require_frontend(directory: Path, token: str | None):
    record = _live_record(directory / "frontend.lock")
    if not token or not record or not hmac.compare_digest(
        str(record.get("token_hash", "")), _token_hash(token)
    ):
        raise ChatBusyError("This frontend does not own the project; open it through a live project connection first.")


class _Lease:
    def _initialize(self, handle, metadata: dict):
        self._handle = handle
        self._guard = threading.Lock()
        self._closed = False
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps({**metadata, "pid": os.getpid(), "acquired_at": utc_now_iso()}).encode())
            handle.flush()
        except BaseException:
            self.close()
            raise

    def close(self):
        with self._guard:
            if not self._closed:
                self._closed = True
                # Serialize release against new claims and request admission.
                with file_lock(self._directory / "gate.lock"):
                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
                    self._handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class ProjectFrontendLease(_Lease):
    """Held for an entire browser connection (or interactive CLI session)."""

    def __init__(self, project_root: Path):
        self._directory = project_lock_directory(project_root)
        self.token = secrets.token_urlsafe(32)
        with file_lock(self._directory / "gate.lock"):
            if not project_root.is_dir():
                raise ValueError("Project not found")
            _require_free(self._directory / "execution.lock")
            _require_free(self._directory / "requests.lock")
            self._initialize(_open_lock(self._directory / "frontend.lock"),
                             {"token_hash": _token_hash(self.token)})


class ProjectRequestLease(_Lease):
    """Pin admitted writes until their response/stream finishes, even on WS loss."""

    def __init__(self, project_root: Path, token: str | None, *, deleting=False):
        self._directory = project_lock_directory(project_root)
        with file_lock(self._directory / "gate.lock"):
            _require_frontend(self._directory, token)
            if deleting:
                _require_free(self._directory / "execution.lock")
            # Shared writes belong to the same frontend; deletion needs silence.
            self._handle = _open_lock(self._directory / "requests.lock",
                                     fcntl.LOCK_EX if deleting else fcntl.LOCK_SH)
            self._guard = threading.Lock()
            self._closed = False


class ChatLease(_Lease):
    """Exactly one executing turn per project, irrespective of thread_id."""

    def __init__(self, project_root: Path, thread_id: str, *, frontend_token: str | None = None,
                 request_id: str | None = None):
        if not thread_id.strip():
            raise ValueError("Chat lease requires thread_id")
        self._directory = project_lock_directory(project_root)
        self.owner = uuid.uuid4().hex
        self.request_id = request_id or uuid.uuid4().hex
        self.cancellation = ExecutionCancellation(self._directory, self.owner)
        with file_lock(self._directory / "gate.lock"):
            _require_free(self._directory / "stop.lock")
            if frontend_token is not None:
                _require_frontend(self._directory, frontend_token)
            else:
                if _live_record(self._directory / "frontend.lock") is not None:
                    raise ChatBusyError("Project is occupied by another frontend.")
                _require_free(self._directory / "requests.lock")
            self._initialize(_open_lock(self._directory / "execution.lock"),
                             {"thread_id": thread_id, "owner": self.owner, "request_id": self.request_id,
                              "token_hash": _token_hash(frontend_token) if frontend_token else None})

    def close(self):
        try:
            super().close()
        finally:
            self.cancellation.revoke()


class ProjectStopLease(_Lease):
    """Serialize stop cleanup against admission of the next turn."""

    def __init__(self, project_root: Path, token: str | None, thread_id: str,
                 *, request_id: str | None = None):
        self._directory = project_lock_directory(project_root)
        with file_lock(self._directory / "gate.lock"):
            _require_frontend(self._directory, token)
            active = _live_record(self._directory / "execution.lock")
            if active and (active.get("thread_id") != thread_id or
                           active.get("token_hash") != _token_hash(token)):
                raise ChatBusyError("The active execution belongs to a different thread or frontend.")
            if active and request_id is not None and active.get("request_id") != request_id:
                raise ChatBusyError("This stop request belongs to an earlier execution.")
            self.execution_active = active is not None
            self.execution_owner = active.get("owner") if active else None
            self.cancellation = (ExecutionCancellation(self._directory, self.execution_owner)
                                 if self.execution_owner else None)
            self._initialize(_open_lock(self._directory / "stop.lock"), {"thread_id": thread_id})

    def request_cancel(self, reason="user_stop") -> bool:
        return bool(self.cancellation and self.cancellation.request(reason))
