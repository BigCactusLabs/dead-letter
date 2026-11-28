"""Parent-side soft memory watchdog for macOS MBOX message workers (#220).

This is a soft limit, not a memory cap. The parent reads the worker's current
physical footprint, targeting one sample every ``SAMPLE_INTERVAL_SECONDS``, and
kills a worker it sees over the threshold. Memory allocated and released
between two samples is not prevented: it is found afterwards in the kernel's
lifetime peak and reported as an overshoot. The interval is a target, not a
bound on how long a worker can stay over the threshold: the parent can be
descheduled (for example under memory pressure) or delayed by a cancellation
callback while the worker keeps allocating, and termination takes time too.

Only the worker process itself is measured. A process the worker started
would not be counted; the conversion pipeline starts none.

Identity: a child that its parent has not reaped keeps its PID (the kernel does
not reuse the PID of a live or zombie child). The supervisor samples and
reaps on one thread, and reaps only after ``waitid(WNOWAIT)`` reports the exit,
so a sample cannot reach a different process. The kernel's process start time is checked on every
sample as a second guard.
"""

from __future__ import annotations

import ctypes
import errno
import os
import subprocess
from dataclasses import dataclass
from typing import Any

SAMPLE_INTERVAL_SECONDS = 0.01
_MIB = 1024 * 1024
_RUSAGE_INFO_V4 = 4  # <sys/resource.h>; present since macOS 10.13
_P_PID = 1  # <sys/wait.h> idtype_t: P_ALL, P_PID, P_PGID


class _RusageInfoV4(ctypes.Structure):
    # struct rusage_info_v4 from the macOS SDK <sys/resource.h>: a 16-byte UUID,
    # then only uint64_t fields, so there is no padding (sizeof 296).
    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [(name, ctypes.c_uint64) for name in (
        "ri_user_time", "ri_system_time", "ri_pkg_idle_wkups", "ri_interrupt_wkups", "ri_pageins",
        "ri_wired_size", "ri_resident_size", "ri_phys_footprint", "ri_proc_start_abstime",
        "ri_proc_exit_abstime", "ri_child_user_time", "ri_child_system_time", "ri_child_pkg_idle_wkups",
        "ri_child_interrupt_wkups", "ri_child_pageins", "ri_child_elapsed_abstime", "ri_diskio_bytesread",
        "ri_diskio_byteswritten", "ri_cpu_time_qos_default", "ri_cpu_time_qos_maintenance",
        "ri_cpu_time_qos_background", "ri_cpu_time_qos_utility", "ri_cpu_time_qos_legacy",
        "ri_cpu_time_qos_user_initiated", "ri_cpu_time_qos_user_interactive", "ri_billed_system_time",
        "ri_serviced_system_time", "ri_logical_writes", "ri_lifetime_max_phys_footprint",
        "ri_instructions", "ri_cycles", "ri_billed_energy", "ri_serviced_energy",
        "ri_interval_max_phys_footprint", "ri_runnable_time",
    )]


class _SigInfo(ctypes.Structure):
    # siginfo_t from the macOS SDK <sys/signal.h> (sizeof 104 on 64-bit).
    _fields_ = [
        ("si_signo", ctypes.c_int), ("si_errno", ctypes.c_int), ("si_code", ctypes.c_int),
        ("si_pid", ctypes.c_int), ("si_uid", ctypes.c_uint32), ("si_status", ctypes.c_int),
        ("si_addr", ctypes.c_void_p), ("si_value", ctypes.c_void_p), ("si_band", ctypes.c_long),
        ("_pad", ctypes.c_ulong * 7),
    ]


@dataclass(frozen=True, slots=True)
class FootprintSample:
    """One ``proc_pid_rusage(RUSAGE_INFO_V4)`` reading, in bytes and Mach time."""

    footprint: int
    lifetime_peak: int
    start_abstime: int
    exit_abstime: int


class MemoryWatchError(Exception):
    """The footprint of an unreaped worker could not be read or trusted."""


class MemoryWatchExceeded(Exception):
    """A sample showed the worker's current footprint over the threshold."""

    def __init__(self, footprint: int) -> None:
        super().__init__(footprint)
        self.footprint = footprint


_functions: tuple[Any, Any] | None = None


def _library() -> tuple[Any, Any]:
    global _functions
    if _functions is None:
        libc = ctypes.CDLL(None, use_errno=True)
        rusage = libc.proc_pid_rusage
        rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(_RusageInfoV4)]
        rusage.restype = ctypes.c_int
        waitid = libc.waitid
        waitid.argtypes = [ctypes.c_int, ctypes.c_uint32, ctypes.POINTER(_SigInfo), ctypes.c_int]
        waitid.restype = ctypes.c_int
        _functions = (rusage, waitid)
    return _functions


def read_footprint(pid: int) -> FootprintSample:
    """Read one process's footprint; any nonzero return raises ``OSError``."""
    rusage, _waitid = _library()
    info = _RusageInfoV4()
    if rusage(pid, _RUSAGE_INFO_V4, ctypes.byref(info)) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return FootprintSample(
        info.ri_phys_footprint, info.ri_lifetime_max_phys_footprint,
        info.ri_proc_start_abstime, info.ri_proc_exit_abstime,
    )


def exited_unreaped(pid: int) -> bool:
    """Report whether a child has exited, without reaping it (``WNOWAIT``).

    Called through ctypes because CPython 3.12 does not expose ``os.waitid``
    on macOS.
    """
    _rusage, waitid = _library()
    while True:
        info = _SigInfo()
        if waitid(_P_PID, pid, ctypes.byref(info), os.WEXITED | os.WNOHANG | os.WNOWAIT) == 0:
            return info.si_pid == pid
        code = ctypes.get_errno()
        if code != errno.EINTR:
            raise OSError(code, os.strerror(code))


class FootprintWatch:
    """Sampling state for one worker; ``poll`` never reaps the process."""

    interval = SAMPLE_INTERVAL_SECONDS

    def __init__(self, limit_mib: int) -> None:
        self.limit_mib = limit_mib
        self.limit_bytes = limit_mib * _MIB
        self.samples = 0
        self.peak: int | None = None
        self._start: int | None = None
        try:
            # Check before any worker starts that the call exists and is permitted.
            read_footprint(os.getpid())
        except (OSError, AttributeError) as exc:
            raise MemoryWatchError("proc_pid_rusage is unavailable") from exc

    def poll(self, process: subprocess.Popen[Any]) -> bool:
        """Sample once. Return True once waitid reports the unreaped worker as exited.

        Raises ``MemoryWatchExceeded`` for a live worker over the threshold and
        ``MemoryWatchError`` when the reading fails or cannot be attributed to
        this worker. The child is unreaped here, so even ESRCH is an error.
        """
        if process.returncode is not None:
            raise MemoryWatchError("worker was reaped before its final sample")
        try:
            exited = exited_unreaped(process.pid)
            sample = read_footprint(process.pid)
        except OSError as exc:
            raise MemoryWatchError("worker footprint could not be read") from exc
        if self._start is None:
            self._start = sample.start_abstime
        elif sample.start_abstime != self._start:
            raise MemoryWatchError("worker start time changed between samples")
        self.samples += 1
        if sample.exit_abstime:
            # Exit statistics are cached once the worker exits: its current
            # footprint reads 0 and the lifetime peak is final. They can appear
            # before waitid reports the child as waitable, so keep polling
            # (deadline and cancellation still apply) until it does.
            self.peak = sample.lifetime_peak
            return exited
        if exited:
            # Without the cached statistics the peak is unknown, which is a
            # measurement failure.
            raise MemoryWatchError("worker exit statistics are unavailable")
        if sample.footprint > self.limit_bytes:
            raise MemoryWatchExceeded(sample.footprint)
        return False

    def overshoot(self) -> dict[str, Any] | None:
        """Diagnostic for a worker that exited on its own after peaking over the threshold."""
        if self.peak is None or self.peak <= self.limit_bytes:
            return None
        return {"code": "mbox_memory_watch_overshoot", "limit_mib": self.limit_mib, "peak_bytes": self.peak}
