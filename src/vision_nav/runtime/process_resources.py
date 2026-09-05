"""Read-only resource counters for one explicitly owned native child process."""

import math
import os
from pathlib import Path
import time
from uuid import UUID

from vision_nav.neural.hardware_resources import process_io_counters

try:
    import psutil
except ImportError:
    psutil = None


class OwnedProcessResources:
    """Bind at spawn, verify PID/birth/parent before and after every measurement.

    No process enumeration, command lines, environment reads, child traversal,
    signals or waits. The native worker remains the sole lifecycle owner.
    """

    def __init__(self, pid, worker_run_id):
        if type(pid) is not int or pid <= 0 or str(UUID(worker_run_id)) != worker_run_id:
            raise ValueError("A child PID and canonical worker UUID are required")
        self.identity = {"pid": pid, "parent_pid": os.getpid(), "worker_run_id": worker_run_id,
                         "created_at_epoch_s": None}
        self.process, self.error = None, None
        if psutil is None:
            self.error = "psutil_not_installed"
            return
        try:
            process = psutil.Process(pid)
            if process.ppid() != os.getpid() or not process.is_running():
                raise ValueError("not_a_live_direct_child")
            created = process.create_time()
            if not math.isfinite(created) or created <= 0:
                raise ValueError("invalid_process_creation_time")
            self.identity["created_at_epoch_s"] = created
            self.process = process
        except (psutil.Error, OSError, ValueError) as exc:
            self.error = f"binding_failed:{type(exc).__name__}:{exc}"[:300]

    def _check(self):
        if not self.process.is_running():
            raise ValueError("process_exited_or_pid_reused")
        # create_time is cached on a Process object: obtain a fresh identity.
        current = psutil.Process(self.identity["pid"])
        if (current.create_time() != self.identity["created_at_epoch_s"]
                or current.ppid() != self.identity["parent_pid"]
                or current.status() == psutil.STATUS_ZOMBIE):
            raise ValueError("process_identity_changed_or_zombie")

    def snapshot(self):
        started = time.monotonic_ns()
        base = {"schema_version": "vision_nav_owned_process_resources_v1", "role": "native_vio",
                "identity": dict(self.identity), "position_authority": False,
                "cpu_scope": "process_user_plus_system_all_threads_excludes_descendants",
                "rss_scope": "current_process_rss_shared_pages_not_deduplicated",
                "psutil_version": psutil.__version__ if psutil is not None else None,
                "sample_started_host_monotonic_ns": started}
        if self.process is None:
            return {**base, "status": "unavailable", "reason": self.error,
                    "observed_host_monotonic_ns": time.monotonic_ns()}
        try:
            self._check()
            cpu = self.process.cpu_times()
            rss = self.process.memory_info().rss
            if (not all(math.isfinite(v) and v >= 0 for v in (cpu.user, cpu.system))
                    or type(rss) is not int or rss < 0):
                raise ValueError("invalid_process_counter")
            io = process_io_counters(Path(f"/proc/{self.identity['pid']}/io"))
            self._check()
            return {**base, "status": "available", "reason": None,
                    "observed_host_monotonic_ns": time.monotonic_ns(),
                    "process_cpu_ns": round((cpu.user + cpu.system)*1e9), "rss_bytes": rss, "process_io": io}
        except (psutil.Error, OSError, ValueError) as exc:
            return {**base, "status": "unavailable", "reason": f"{type(exc).__name__}:{exc}"[:300],
                    "observed_host_monotonic_ns": time.monotonic_ns()}
