"""Linux process-tree limits: delegated cgroup v2 or explicit sampled monitoring."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import sys
import threading
import time
import uuid


def _stat(pid: int):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return dict(pid=pid, ppid=int(fields[1]), start=int(fields[19]),
                    cpu=(int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK"),
                    rss=max(0, int(fields[21])) * os.sysconf("SC_PAGE_SIZE"), tasks=int(fields[17]))
    except (OSError, ValueError, IndexError):
        return None


class ProcessTreeMonitor:
    """Sample aggregate RSS, CPU and thread count; this is not kernel containment."""
    mode = "proc_monitor"

    def __init__(self, config, compute_slots):
        self.config = config
        self.root_pid = None
        self.known = {}
        self.cpu_before = 0.0
        self.cpu_seconds = 0.0
        self.peak_memory = 0
        self.peak_tasks = 0
        self.lock = threading.RLock()

    def wrap_argv(self, argv):
        return argv

    def start(self, pid):
        if pid == os.getpid() or pid <= 1:
            raise ValueError("Cannot monitor the service process as a coding tree")
        with self.lock:
            self.root_pid = pid
            self.known = {}
            self.cpu_before = self.cpu_seconds
            self.check()

    def check(self):
        with self.lock:
            if self.root_pid is None:
                return None
            records = {pid: record for name in os.listdir("/proc") if name.isdecimal()
                       for pid in [int(name)] for record in [_stat(pid)] if record is not None}
            members = {pid for pid, (start, _cpu) in self.known.items()
                       if pid in records and records[pid]["start"] == start}
            root = records.get(self.root_pid)
            if root and (self.root_pid not in self.known or
                         self.known[self.root_pid][0] == root["start"]):
                members.add(self.root_pid)
            while True:
                added = {pid for pid, record in records.items() if record["ppid"] in members} - members
                if not added:
                    break
                members.update(added)
            for pid in members:
                row = records[pid]
                self.known[pid] = (row["start"], max(row["cpu"], self.known.get(pid, (0, 0))[1]))
            memory = sum(records[pid]["rss"] for pid in members)
            tasks = sum(records[pid]["tasks"] for pid in members)
            self.cpu_seconds = self.cpu_before + sum(cpu for _start, cpu in self.known.values())
            self.peak_memory = max(self.peak_memory, memory)
            self.peak_tasks = max(self.peak_tasks, tasks)
            if memory > self.config.memory_bytes:
                return "Coding process tree exceeded its aggregate RSS limit."
            if tasks > self.config.max_processes:
                return "Coding process tree exceeded its process/thread limit."
            if self.cpu_seconds > self.config.cpu_seconds:
                return "Coding process tree exceeded its cumulative CPU-time limit."
            return None

    def finish(self):
        with self.lock:
            self.check()
            self.root_pid = None

    def kill(self):
        with self.lock:
            self.check()
            for pid, (start, _cpu) in list(self.known.items()):
                row = _stat(pid)
                if row and row["start"] == start:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def close(self):
        self.kill()

    def public_record(self):
        return {"mode": self.mode, "kernel_tree_limits": False,
                "sample_interval_seconds": 0.25, "rss_shared_pages_may_be_counted_multiple_times": True,
                "short_lived_processes_may_escape_sampling": True,
                "cpu_seconds_observed": round(self.cpu_seconds, 3),
                "peak_memory_bytes_observed": self.peak_memory, "peak_tasks_observed": self.peak_tasks}


def _key_values(path):
    return {key: int(value) for key, value in (line.split() for line in path.read_text().splitlines())}


class CgroupTreeLimits:
    mode = "cgroup_v2"

    def __init__(self, config, compute_slots):
        root = config.cgroup_root
        if root is None or not root.is_absolute():
            raise ValueError("Cgroup limits require an absolute delegated GBCLAW_CODING_CGROUP_ROOT")
        root = root.resolve(strict=True)
        if not root.is_relative_to(Path("/sys/fs/cgroup")):
            raise ValueError("Coding cgroup root must be within the kernel cgroup hierarchy")
        available = set((root / "cgroup.subtree_control").read_text().split())
        if not {"cpu", "memory", "pids"}.issubset(available):
            raise ValueError("Delegated cgroup root must enable cpu, memory and pids controllers")
        self.config = config
        self.peak_memory = 0
        self.directory = root / ("gbclaw-code-" + uuid.uuid4().hex)
        self.directory.mkdir()
        try:
            (self.directory / "memory.max").write_text(str(config.memory_bytes))
            (self.directory / "memory.swap.max").write_text("0")
            (self.directory / "pids.max").write_text(str(config.max_processes))
            (self.directory / "cpu.max").write_text(f"{compute_slots * 100000} 100000")
            if not (self.directory / "cgroup.kill").exists():
                raise ValueError("Cgroup limits require kernel cgroup.kill support")
        except BaseException:
            self.directory.rmdir()
            raise

    def wrap_argv(self, argv):
        # A trusted launcher joins before exec: workers cannot fork before attachment.
        return [sys.executable, str(Path(__file__).with_name("cgroup_launch.py")),
                str(self.directory / "cgroup.procs"), *argv]

    def start(self, pid):
        pass

    def check(self):
        self.peak_memory = max(self.peak_memory, int((self.directory / "memory.current").read_text()))
        memory_events = _key_values(self.directory / "memory.events")
        if memory_events.get("oom", 0) or memory_events.get("oom_kill", 0):
            return "Coding process tree reached its kernel memory limit."
        if _key_values(self.directory / "pids.events").get("max", 0):
            return "Coding process tree reached its kernel process/thread limit."
        if self.cpu_seconds > self.config.cpu_seconds:
            return "Coding process tree exceeded its cumulative CPU-time limit."
        return None

    @property
    def cpu_seconds(self):
        return _key_values(self.directory / "cpu.stat").get("usage_usec", 0) / 1_000_000

    def finish(self):
        pass

    def kill(self):
        if self.directory.exists():
            (self.directory / "cgroup.kill").write_text("1")

    def close(self):
        self.kill()
        deadline = time.monotonic() + 2
        while self.directory.exists():
            try:
                self.directory.rmdir()
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Coding cgroup could not be removed after killing its processes")
                time.sleep(0.05)

    def public_record(self):
        peak_path = self.directory / "memory.peak"
        return {"mode": self.mode, "kernel_tree_limits": True,
                "cpu_seconds_observed": round(self.cpu_seconds, 3),
                "peak_memory_bytes_observed": int(peak_path.read_text()) if peak_path.exists() else self.peak_memory,
                "kernel_memory_peak_available": peak_path.exists(),
                "max_tasks": self.config.max_processes}


def make_process_limits(config, compute_slots):
    if config.resource_mode != "monitor" and (config.cgroup_root is not None or config.resource_mode == "cgroup"):
        try:
            return CgroupTreeLimits(config, compute_slots), None
        except (OSError, ValueError) as exc:
            if config.resource_mode == "cgroup":
                raise RuntimeError(f"Required cgroup limits are unavailable: {exc}") from exc
            reason = f"Cgroup limits unavailable: {exc}"
    else:
        reason = "No delegated cgroup root configured; using sampled process-tree limits"
    return ProcessTreeMonitor(config, compute_slots), reason
