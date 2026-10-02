"""Win32 ABI and failure-path contracts; native conversion lives in test_mbox_budgets.

The fake API tests run on every host. They are not evidence that Windows has
actually enforced a limit; the windows-latest real-worker cases establish that.
"""
from __future__ import annotations

import ctypes
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from dead_letter import _mbox_windows as windows
from dead_letter import _mbox_worker as worker
from dead_letter.core import mbox_isolation as isolation

NONCE = "0123456789abcdef" * 2
HANDLE = 0x123456789  # Exercise handles that cannot fit in a DWORD.


class Function:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


class FakeKernel:
    def __init__(self, failure=None):
        self.failure = failure
        self.closed = []
        self.opened = []
        self.assigned = []
        self.state = windows._WAIT_TIMEOUT
        self.waits = []
        self.CreateJobObjectW = Function(self.create)
        self.SetInformationJobObject = Function(self.set_limits)
        self.QueryInformationJobObject = Function(self.query_limits)
        self.WaitForSingleObject = Function(self.wait)
        self.CloseHandle = Function(self.close)
        self.OpenJobObjectW = Function(self.open)
        self.GetCurrentProcess = Function(lambda: -1)
        self.AssignProcessToJobObject = Function(self.assign)

    def create(self, security, name):
        assert security is None
        self.name = name
        return 0 if self.failure == "create" else HANDLE

    def set_limits(self, handle, kind, pointer, size):
        assert handle == HANDLE and kind == 9 and size == ctypes.sizeof(windows._ExtendedLimits)
        self.limits = ctypes.string_at(pointer, size)
        return self.failure != "set"

    def query_limits(self, handle, kind, pointer, size, returned):
        assert handle == HANDLE and kind == 9
        ctypes.memmove(pointer, self.limits, size)
        ctypes.cast(returned, ctypes.POINTER(ctypes.c_uint32)).contents.value = size
        actual = ctypes.cast(pointer, ctypes.POINTER(windows._ExtendedLimits)).contents
        if self.failure == "flags":
            actual.BasicLimitInformation.LimitFlags = 0
        elif self.failure == "cpu":
            actual.BasicLimitInformation.PerJobUserTimeLimit += 1
        elif self.failure == "memory":
            actual.ProcessMemoryLimit += 1
        elif self.failure == "processes":
            actual.BasicLimitInformation.ActiveProcessLimit = 2
        elif self.failure == "length":
            ctypes.cast(returned, ctypes.POINTER(ctypes.c_uint32)).contents.value = 0
        return self.failure != "query"

    def wait(self, handle, milliseconds):
        assert handle == HANDLE and milliseconds == 0
        self.waits.append(handle)
        return self.state

    def close(self, handle):
        self.closed.append(handle)
        return self.failure != "close"

    def open(self, rights, inherit, name):
        self.opened.append((rights, inherit, name))
        return 0 if self.failure == "open" else HANDLE

    def assign(self, job, process):
        self.assigned.append((job, process))
        return self.failure != "assign"


@pytest.fixture
def kernel(monkeypatch):
    fake = FakeKernel()
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kw: fake, raising=False)
    monkeypatch.setattr(ctypes, "set_last_error", lambda code: None, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 183 if fake.failure == "existing" else 0, raising=False)
    return fake


def test_windows_abi_widths_and_layout():
    assert ctypes.sizeof(windows._DWORD) == ctypes.sizeof(windows._BOOL) == 4
    assert ctypes.sizeof(windows._HANDLE) == ctypes.sizeof(ctypes.c_void_p)
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(windows._BasicLimits) == 64
        assert ctypes.sizeof(windows._IOCounters) == 48
        assert ctypes.sizeof(windows._ExtendedLimits) == 144
        assert windows._BasicLimits.LimitFlags.offset == 16
        assert windows._BasicLimits.ActiveProcessLimit.offset == 40
        assert windows._ExtendedLimits.ProcessMemoryLimit.offset == 112


@pytest.mark.parametrize("budgets", [{"cpu_seconds": 3}, {"memory_mib": 32}, {"cpu_seconds": 3, "memory_mib": 32}])
def test_verified_limits_use_ticks_commit_bytes_and_one_process(kernel, budgets):
    job = windows.WindowsJob(budgets, NONCE)
    applied = windows._ExtendedLimits.from_buffer_copy(kernel.limits)
    basic = applied.BasicLimitInformation
    assert basic.ActiveProcessLimit == 1
    assert basic.LimitFlags & windows._KILL_ON_JOB_CLOSE
    assert basic.PerJobUserTimeLimit == budgets.get("cpu_seconds", 0) * 10_000_000
    assert applied.ProcessMemoryLimit == budgets.get("memory_mib", 0) * 1024**2
    assert kernel.name == windows.job_name(NONCE) == "Local\\dead-letter-" + NONCE
    assert kernel.CreateJobObjectW.restype is ctypes.c_void_p
    assert kernel.WaitForSingleObject.restype is ctypes.c_uint32
    job.close()
    job.close()
    assert kernel.closed == [HANDLE]


@pytest.mark.parametrize("failure", ["create", "existing", "set", "query", "flags", "cpu", "memory", "processes", "length"])
def test_failed_job_setup_closes_owned_handle(kernel, failure):
    kernel.failure = failure
    with pytest.raises(OSError):
        windows.WindowsJob({"cpu_seconds": 3, "memory_mib": 32}, NONCE)
    assert kernel.closed == ([] if failure == "create" else [HANDLE])


@pytest.mark.parametrize("budgets", [{}, {"max_output_mib": 1}, {"unknown": 1}, {"cpu_seconds": 0},
                                      {"cpu_seconds": True}, {"memory_mib": -1}, {"cpu_seconds": 1.5},
                                      {"cpu_seconds": (2**63 - 1) // 10_000_000 + 1},
                                      {"memory_mib": 2 ** (ctypes.sizeof(ctypes.c_size_t) * 8)}])
def test_invalid_budgets_never_create_a_job(kernel, budgets):
    with pytest.raises(ValueError):
        windows.WindowsJob(budgets, NONCE)
    assert not hasattr(kernel, "name")


@pytest.mark.parametrize("nonce", ["", "a" * 31, "g" * 32, "A" * 32, "é" * 32, "../" * 11])
def test_invalid_names_are_rejected_before_opening_or_creating(kernel, nonce):
    with pytest.raises(ValueError):
        windows.WindowsJob({"cpu_seconds": 1}, nonce)
    with pytest.raises(ValueError):
        worker._join_windows_job(nonce)
    assert kernel.opened == [] and not hasattr(kernel, "name")


def test_cpu_uses_job_signal_not_process_exit_codes(kernel):
    job = windows.WindowsJob({"cpu_seconds": 3}, NONCE)
    assert not job.cpu_exceeded()
    kernel.state = windows._WAIT_OBJECT_0
    assert job.cpu_exceeded()
    kernel.state = 0xFFFFFFFF
    with pytest.raises(OSError, match="state"):
        job.cpu_exceeded()
    job.close()


def test_memory_only_job_does_not_claim_cpu_exhaustion(kernel):
    job = windows.WindowsJob({"memory_mib": 32}, NONCE)
    kernel.state = windows._WAIT_OBJECT_0
    assert not job.cpu_exceeded() and kernel.waits == []
    job.close()


@pytest.mark.parametrize("failure", [None, "open", "assign", "close"])
def test_bootstrap_joins_with_assignment_only_and_drops_child_handle(kernel, failure):
    kernel.failure = failure
    if failure is None:
        worker._join_windows_job(NONCE)
    else:
        with pytest.raises(OSError):
            worker._join_windows_job(NONCE)
    assert kernel.opened == [(1, False, windows.job_name(NONCE))]
    assert kernel.closed == ([] if failure == "open" else [HANDLE])
    assert kernel.assigned == ([] if failure == "open" else [(HANDLE, -1)])
    assert kernel.OpenJobObjectW.restype is ctypes.c_void_p
    assert kernel.GetCurrentProcess.restype is ctypes.c_void_p


def test_unsupported_windows_output_does_not_join_job(kernel, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(ValueError, match="Unsupported"):
        worker._apply_budgets({"max_output_mib": 1}, nonce=NONCE)
    assert kernel.opened == []


@pytest.mark.parametrize("failure", ["open", "assign", "close"])
def test_bootstrap_failure_is_nonce_authenticated_and_never_converts(kernel, monkeypatch, tmp_path, failure):
    kernel.failure = failure
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(worker, "run", lambda *a: pytest.fail("failed job must not parse"))
    assert worker._run_budgeted(tmp_path / "request.json", {"cpu_seconds": 5}, NONCE) == 3
    assert json.loads((tmp_path / worker.BUDGET_STATUS_FILE).read_text()) == {
        "nonce": NONCE, "outcome": "apply_failed",
    }


@pytest.mark.parametrize("field, value", [
    ("cpu_seconds", (2**63 - 1) // 10_000_000 + 1),
    ("memory_limit_mib", (2 * sys.maxsize + 1) // 1024**2 + 1),
])
def test_windows_parent_rejects_unit_overflow_before_launch(monkeypatch, field, value):
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(ValueError, match="positive integer"):
        isolation.validate_budgets(isolation.WorkerBudgets(**{field: value}), worker_mode=True)


@pytest.mark.parametrize("mode", ["ok", "cpu", "exit1816", "launch", "wait", "timeout", "cancel", "query", "close"])
def test_parent_keeps_job_through_reaping_and_closes_on_all_exit_paths(monkeypatch, tmp_path, mode):
    events = []

    class Job:
        def __init__(self, budgets, nonce):
            assert budgets == {"cpu_seconds": 5} and nonce == NONCE
            events.append("job")

        def cpu_exceeded(self):
            events.append("query")
            if mode == "query":
                raise OSError("private native details")
            return mode == "cpu"

        def close(self):
            events.append("close")
            if mode == "close":
                raise OSError("private native details")

    class Process:
        args = ["synthetic-worker"]
        returncode = None

        def __init__(self, *args, **kwargs):
            events.append("launch")
            if mode == "launch":
                raise OSError("launch failed")

        def poll(self):
            return self.returncode

        def kill(self):
            events.append("kill")
            self.returncode = 1

        def wait(self, timeout=None):
            events.append("wait" if timeout is not None else "reap")
            if timeout is not None:
                if mode == "timeout":
                    raise subprocess.TimeoutExpired(self.args, timeout)
                if mode == "cancel":
                    raise KeyboardInterrupt
                if mode == "wait":
                    raise OSError("wait failed")
            self.returncode = 1816 if mode == "exit1816" else 0
            return self.returncode

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(windows, "WindowsJob", Job)
    monkeypatch.setattr(isolation.subprocess, "Popen", Process)
    times = iter([0, 1, 10])
    monkeypatch.setattr(isolation, "monotonic", lambda: next(times))
    call = lambda: isolation._run_worker(tmp_path / "request.json", 5, ("cpu_seconds=5", "nonce=" + NONCE))
    error = {
        "cpu": isolation._WorkerResourceLimit, "launch": OSError, "wait": OSError,
        "timeout": subprocess.TimeoutExpired, "cancel": KeyboardInterrupt,
        "query": isolation.MboxBudgetError, "close": isolation.MboxBudgetError,
    }.get(mode)
    if error:
        with pytest.raises(error) as caught:
            call()
        if mode in ("query", "close"):
            assert caught.value.code == "mbox_budget_apply_failed"
            assert "private" not in caught.value.message
    else:
        assert call() == (1816 if mode == "exit1816" else 0)
    assert events[:2] == ["job", "launch"] and events[-1] == "close"
    if mode != "launch":
        assert events[-2] == "reap"
    if mode in ("wait", "timeout", "cancel"):
        assert "kill" in events


def test_parent_job_setup_failure_never_launches(monkeypatch, tmp_path):
    def denied(*a):
        raise OSError("host job policy")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(windows, "WindowsJob", denied)
    monkeypatch.setattr(isolation.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not launch"))
    with pytest.raises(isolation.MboxBudgetError, match="mbox_budget_apply_failed"):
        isolation._run_worker(tmp_path / "request.json", 5, ("cpu_seconds=5", "nonce=" + NONCE))
