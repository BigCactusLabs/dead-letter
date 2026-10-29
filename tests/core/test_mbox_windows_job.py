"""Win32 ABI and failure-path contracts; native conversion lives in test_mbox_budgets.

The fake API tests run on every host. They are not evidence that Windows has
actually enforced a limit; the windows-latest real-worker cases establish that.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import subprocess
import sys

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
        # ActiveProcesses per accounting query (the last value repeats); None
        # fails that query. calls orders the drain and close in the parent.
        self.active = [0]
        self.calls = []
        self.CreateJobObjectW = Function(self.create)
        self.SetInformationJobObject = Function(self.set_limits)
        self.QueryInformationJobObject = Function(self.query_limits)
        self.WaitForSingleObject = Function(self.wait)
        self.TerminateJobObject = Function(self.terminate)
        self.CloseHandle = Function(self.close)
        self.OpenJobObjectW = Function(self.open)
        self.GetCurrentProcess = Function(lambda: -1)
        self.AssignProcessToJobObject = Function(self.assign)

    def create(self, security, name):
        assert security is None
        self.name = name
        return 0 if self.failure == "create" else HANDLE

    def set_limits(self, handle, kind, pointer, size):
        assert (
            handle == HANDLE
            and kind == 9
            and size == ctypes.sizeof(windows._ExtendedLimits)
        )
        self.limits = ctypes.string_at(pointer, size)
        return self.failure != "set"

    def query_limits(self, handle, kind, pointer, size, returned):
        if kind == 1:
            return self.query_accounting(handle, pointer, size, returned)
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

    def query_accounting(self, handle, pointer, size, returned):
        assert handle == HANDLE and size == ctypes.sizeof(windows._BasicAccounting)
        active = self.active.pop(0) if len(self.active) > 1 else self.active[0]
        self.calls.append("active")
        if active is None:
            return False
        info = ctypes.cast(pointer, ctypes.POINTER(windows._BasicAccounting)).contents
        info.TotalProcesses = 1
        info.ActiveProcesses = active
        ctypes.cast(returned, ctypes.POINTER(ctypes.c_uint32)).contents.value = size
        return True

    def wait(self, handle, milliseconds):
        assert handle == HANDLE and milliseconds == 0
        self.waits.append(handle)
        return self.state

    def terminate(self, handle, code):
        assert handle == HANDLE and code == 1
        self.calls.append("terminate")
        return self.failure != "terminate"

    def close(self, handle):
        self.calls.append("close")
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
    monkeypatch.setattr(
        ctypes,
        "get_last_error",
        lambda: 183 if fake.failure == "existing" else 0,
        raising=False,
    )
    return fake


@pytest.fixture
def clock(monkeypatch):
    """Drain-time clock: each sleep advances it; returns the sleeps taken."""
    now = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(windows, "monotonic", lambda: now[0])
    monkeypatch.setattr(windows, "sleep", sleep)
    return sleeps


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
    else:
        assert ctypes.sizeof(windows._BasicLimits) == 48
        assert ctypes.sizeof(windows._IOCounters) == 48
        assert ctypes.sizeof(windows._ExtendedLimits) == 112


@pytest.mark.parametrize(
    "budgets",
    [{"cpu_seconds": 3}, {"memory_mib": 32}, {"cpu_seconds": 3, "memory_mib": 32}],
)
def test_verified_limits_use_ticks_commit_bytes_and_one_process(kernel, budgets):
    job = windows.WindowsJob(budgets, NONCE)
    applied = windows._ExtendedLimits.from_buffer_copy(kernel.limits)
    basic = applied.BasicLimitInformation
    assert basic.ActiveProcessLimit == 1
    # Literal winnt.h values: KILL_ON_JOB_CLOSE | ACTIVE_PROCESS, JOB_TIME, PROCESS_MEMORY.
    expected = 0x2008
    expected |= 0x4 if "cpu_seconds" in budgets else 0
    expected |= 0x100 if "memory_mib" in budgets else 0
    assert basic.LimitFlags == expected
    assert basic.PerJobUserTimeLimit == budgets.get("cpu_seconds", 0) * 10_000_000
    assert applied.ProcessMemoryLimit == budgets.get("memory_mib", 0) * 1024**2
    assert kernel.name == windows.job_name(NONCE) == "Local\\dead-letter-" + NONCE
    assert kernel.CreateJobObjectW.restype is ctypes.c_void_p
    assert kernel.WaitForSingleObject.restype is ctypes.c_uint32
    job.close()
    job.close()
    assert kernel.closed == [HANDLE]


@pytest.mark.parametrize(
    "failure",
    [
        "create",
        "existing",
        "set",
        "query",
        "flags",
        "cpu",
        "memory",
        "processes",
        "length",
    ],
)
def test_failed_job_setup_closes_owned_handle(kernel, failure):
    kernel.failure = failure
    with pytest.raises(OSError):
        windows.WindowsJob({"cpu_seconds": 3, "memory_mib": 32}, NONCE)
    assert kernel.closed == ([] if failure == "create" else [HANDLE])


@pytest.mark.parametrize(
    "budgets",
    [
        {},
        {"max_output_mib": 1},
        {"unknown": 1},
        {"cpu_seconds": 0},
        {"cpu_seconds": True},
        {"memory_mib": -1},
        {"cpu_seconds": 1.5},
        {"cpu_seconds": (2**63 - 1) // 10_000_000 + 1},
        {"memory_mib": 2 ** (ctypes.sizeof(ctypes.c_size_t) * 8)},
    ],
)
def test_invalid_budgets_never_create_a_job(kernel, budgets):
    with pytest.raises(ValueError):
        windows.WindowsJob(budgets, NONCE)
    assert not hasattr(kernel, "name")


@pytest.mark.parametrize(
    "nonce", ["", "a" * 31, "g" * 32, "A" * 32, "é" * 32, "../" * 11]
)
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
def test_bootstrap_failure_is_nonce_authenticated_and_never_converts(
    kernel, monkeypatch, tmp_path, failure
):
    kernel.failure = failure
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        worker, "run", lambda *a: pytest.fail("failed job must not parse")
    )
    assert (
        worker._run_budgeted(tmp_path / "request.json", {"cpu_seconds": 5}, NONCE) == 3
    )
    assert json.loads((tmp_path / worker.BUDGET_STATUS_FILE).read_text()) == {
        "nonce": NONCE,
        "outcome": "apply_failed",
    }


@pytest.mark.parametrize(
    "field, value",
    [
        ("cpu_seconds", (2**63 - 1) // 10_000_000 + 1),
        ("memory_limit_mib", (2 * sys.maxsize + 1) // 1024**2 + 1),
    ],
)
def test_windows_parent_rejects_unit_overflow_before_launch(monkeypatch, field, value):
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(ValueError, match="positive integer"):
        isolation.validate_budgets(
            isolation.WorkerBudgets(**{field: value}), worker_mode=True
        )


@pytest.mark.parametrize(
    "mode",
    ["ok", "cpu", "exit1816", "launch", "wait", "timeout", "cancel", "query", "close"],
)
def test_parent_keeps_job_through_reaping_and_closes_on_all_exit_paths(
    monkeypatch, tmp_path, mode
):
    events = []
    terminations = []

    class Job:
        def __init__(self, budgets, nonce):
            assert budgets == {"cpu_seconds": 5} and nonce == NONCE
            events.append("job")

        def cpu_exceeded(self):
            events.append("query")
            if mode == "query":
                raise OSError("private native details")
            return mode == "cpu"

        def close(self, *, terminate=False):
            events.append("close")
            terminations.append(terminate)
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

    def call():
        return isolation._run_worker(
            tmp_path / "request.json", 5, ("cpu_seconds=5", "nonce=" + NONCE)
        )

    error = {
        "cpu": isolation._WorkerResourceLimit,
        "launch": OSError,
        "wait": OSError,
        "timeout": subprocess.TimeoutExpired,
        "cancel": KeyboardInterrupt,
        "query": isolation.MboxBudgetError,
        "close": isolation.MboxBudgetError,
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
    # The job is terminated and drained exactly when the worker was killed.
    assert terminations == [mode in ("wait", "timeout", "cancel")]


def test_parent_job_setup_failure_never_launches(monkeypatch, tmp_path):
    def denied(*a):
        raise OSError("host job policy")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(windows, "WindowsJob", denied)
    monkeypatch.setattr(
        isolation.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not launch")
    )
    with pytest.raises(isolation.MboxBudgetError, match="mbox_budget_apply_failed"):
        isolation._run_worker(
            tmp_path / "request.json", 5, ("cpu_seconds=5", "nonce=" + NONCE)
        )


# -- drain after a kill (venv launcher) and close-failure precedence ----------

ARGS = ("cpu_seconds=5", "nonce=" + NONCE)
CLOSE_NOTE = (
    "mbox_budget_apply_failed: could not close the message worker job; import aborted"
)


def timed_out():
    return subprocess.TimeoutExpired("synthetic-worker", 5)


def fake_popen(monkeypatch, events, *, wait_error=None, reap_error=None):
    """A direct worker that exits 0 unless its timed wait or its reap raises."""

    class Process:
        args = ["synthetic-worker"]
        returncode = None

        def __init__(self, *args, **kwargs):
            events.append("launch")

        def poll(self):
            return self.returncode

        def kill(self):
            events.append("kill")
            self.returncode = 1

        def wait(self, timeout=None):
            events.append("wait" if timeout is not None else "reap")
            error = wait_error if timeout is not None else reap_error
            if error is not None:
                raise error()
            if self.returncode is None:
                self.returncode = 0
            return self.returncode

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(isolation.subprocess, "Popen", Process)
    times = iter([0, 1, 10])
    monkeypatch.setattr(isolation, "monotonic", lambda: next(times))


def test_basic_accounting_layout_matches_winnt():
    # Four LARGE_INTEGERs then four DWORDs on both 32- and 64-bit Windows.
    assert ctypes.sizeof(windows._BasicAccounting) == 48
    assert windows._BasicAccounting.ActiveProcesses.offset == 40


def test_close_without_terminate_never_queries_or_terminates(kernel):
    job = windows.WindowsJob({"cpu_seconds": 3}, NONCE)
    kernel.active = [1]
    job.close()
    assert kernel.calls == ["close"] and kernel.closed == [HANDLE]


@pytest.mark.parametrize("failure", ["existing", "set", "query", "flags", "length"])
def test_failed_setup_never_terminates_a_job_it_may_not_own(kernel, failure):
    kernel.failure = failure
    kernel.active = [1]
    with pytest.raises(OSError):
        windows.WindowsJob({"cpu_seconds": 3}, NONCE)
    assert kernel.calls == ["close"]


def test_kill_path_terminates_then_drains_before_closing(kernel, clock):
    job = windows.WindowsJob({"cpu_seconds": 3}, NONCE)
    kernel.active = [1, 1, 1, 0]
    job.close(terminate=True)
    assert kernel.calls == [
        "active",
        "terminate",
        "active",
        "active",
        "active",
        "close",
    ]
    assert clock == [windows._DRAIN_INTERVAL] * 2
    assert kernel.TerminateJobObject.restype is windows._BOOL
    job.close(terminate=True)
    assert kernel.closed == [HANDLE]


def test_already_empty_job_is_closed_without_terminating(kernel, clock):
    job = windows.WindowsJob({"memory_mib": 32}, NONCE)
    job.close(terminate=True)
    assert kernel.calls == ["active", "close"] and clock == []


def test_drain_that_never_empties_is_bounded_and_still_closes(kernel, clock):
    job = windows.WindowsJob({"cpu_seconds": 3}, NONCE)
    kernel.active = [1]
    job.close(terminate=True)  # A slow drain is not a failure.
    assert kernel.calls.count("terminate") == 1 and kernel.calls[-1] == "close"
    assert kernel.closed == [HANDLE]
    assert set(clock) == {windows._DRAIN_INTERVAL}
    bound = windows._DRAIN_SECONDS
    assert 1 <= bound <= 10
    assert bound <= sum(clock) < bound + 2 * windows._DRAIN_INTERVAL


@pytest.mark.parametrize(
    "active, failure, match",
    [
        ([None], None, "processes"),  # before terminating
        ([1, 1, None], None, "processes"),  # while draining
        ([1], "terminate", "terminate"),
    ],
)
def test_drain_failure_still_closes_the_handle_and_raises(
    kernel, clock, active, failure, match
):
    job = windows.WindowsJob({"cpu_seconds": 3}, NONCE)
    kernel.active, kernel.failure = active, failure
    with pytest.raises(OSError, match=match) as caught:
        job.close(terminate=True)
    assert kernel.calls[-1] == "close" and kernel.closed == [HANDLE]
    assert not getattr(caught.value, "__notes__", None)


def test_drain_and_close_failures_keep_the_drain_error_with_a_note(kernel, clock):
    job = windows.WindowsJob({"cpu_seconds": 3}, NONCE)
    kernel.active, kernel.failure = [1, None], "close"
    with pytest.raises(OSError, match="processes") as caught:
        job.close(terminate=True)
    assert caught.value.__notes__ == ["Could not close worker job"]


def test_interrupted_drain_still_closes_the_handle(kernel, monkeypatch):
    job = windows.WindowsJob({"cpu_seconds": 3}, NONCE)
    kernel.active = [1]

    def interrupted(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(windows, "sleep", interrupted)
    with pytest.raises(KeyboardInterrupt):
        job.close(terminate=True)
    assert kernel.calls == ["active", "terminate", "active", "close"]


@pytest.mark.parametrize("killed", [False, True])
def test_parent_drains_a_killed_workers_job_after_reaping_and_before_closing(
    kernel, clock, monkeypatch, tmp_path, killed
):
    kernel.active = [1, 0]
    fake_popen(monkeypatch, kernel.calls, wait_error=timed_out if killed else None)
    if killed:
        with pytest.raises(subprocess.TimeoutExpired):
            isolation._run_worker(tmp_path / "request.json", 5, ARGS)
        # Under a venv the reaped child is the launcher; the interpreter in the
        # job is terminated and drained before the handle and workspace go.
        assert kernel.calls == [
            "launch",
            "wait",
            "kill",
            "reap",
            "active",
            "terminate",
            "active",
            "close",
        ]
    else:
        assert isolation._run_worker(tmp_path / "request.json", 5, ARGS) == 0
        assert kernel.calls == ["launch", "wait", "reap", "close"]
    assert kernel.closed == [HANDLE]


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, asyncio.CancelledError])
@pytest.mark.parametrize("phase", ["wait", "reap"])
def test_job_close_failure_never_masks_an_interrupt(
    kernel, clock, monkeypatch, tmp_path, interrupt, phase
):
    kernel.failure = "close"
    if phase == "wait":
        fake_popen(monkeypatch, kernel.calls, wait_error=interrupt)
    else:  # A second interrupt while reaping a timed-out worker.
        fake_popen(
            monkeypatch, kernel.calls, wait_error=timed_out, reap_error=interrupt
        )
    with pytest.raises(interrupt) as caught:
        isolation._run_worker(tmp_path / "request.json", 5, ARGS)
    assert caught.value.__notes__ == [CLOSE_NOTE]
    assert "kill" in kernel.calls and kernel.closed == [HANDLE]


@pytest.mark.parametrize("outcome", ["ok", "timeout", "cpu"])
def test_job_close_failure_still_aborts_ordinary_outcomes(
    kernel, clock, monkeypatch, tmp_path, outcome
):
    kernel.failure = "close"
    if outcome == "cpu":
        kernel.state = windows._WAIT_OBJECT_0
    fake_popen(
        monkeypatch,
        kernel.calls,
        wait_error=timed_out if outcome == "timeout" else None,
    )
    with pytest.raises(isolation.MboxBudgetError) as caught:
        isolation._run_worker(tmp_path / "request.json", 5, ARGS)
    assert str(caught.value) == CLOSE_NOTE
    assert isinstance(caught.value.__cause__, OSError)
    assert kernel.closed == [HANDLE]
