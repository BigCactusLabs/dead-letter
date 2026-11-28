"""Optional macOS soft memory watchdog for MBOX workers (#220)."""
from __future__ import annotations

import ctypes
import errno
import json
import os
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest

from dead_letter.core import mbox_isolation as isolation
from dead_letter.core import mbox_memory_watch as watch
from dead_letter.core.mbox import MboxCancelled
from dead_letter.core.mbox_archive import convert_mbox_archive
from dead_letter.core.mbox_import import convert_mbox

POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"
MESSAGE = b"From: alice@example.test\nSubject: Same\n\nHello\n\n"
MIB = 1024 * 1024

native_macos = pytest.mark.skipif(
    sys.platform != "darwin", reason="The soft memory watch reads macOS proc_pid_rusage footprints",
)

# Test-only program: runs the real worker module after replacing one private
# pipeline function for record 2. The hook writes "ready" (its PID) on entry,
# then holds, spikes or hangs as told. Allocation sizes are explicit and the
# spike waits for the parent's "go" file, so no timing is tuned to a host.
_HOOKED_WORKER = r"""
import json, os, runpy, sys, time
from pathlib import Path
mode, sync, size = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3]) * 1024 * 1024
sys.argv = ["dead_letter._mbox_worker", *sys.argv[4:]]
index = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["record"]["index"]
if index == 2:
    import dead_letter.core.mbox_import as mbox_import
    real = mbox_import._build_rendered_markdown
    def hook(*args, **kwargs):
        (sync / "ready.tmp").write_text(str(os.getpid()), encoding="ascii")
        (sync / "ready.tmp").rename(sync / "ready")
        if mode == "hold":
            block = b"\x01" * size  # every page written, so it counts in the footprint
            while True:
                time.sleep(1)
        if mode == "hang":
            while True:
                time.sleep(1)
        if mode == "spike":
            deadline = time.monotonic() + 60
            while not (sync / "go").exists():
                if time.monotonic() > deadline:
                    raise SystemExit(70)
                time.sleep(0.001)
            block = b"\x01" * size
            del block
        return real(*args, **kwargs)
    mbox_import._build_rendered_markdown = hook
runpy.run_module("dead_letter._mbox_worker", run_name="__main__", alter_sys=True)
"""


def hooked_worker(monkeypatch, tmp_path, mode, size_mib=0):
    sync = tmp_path / "sync"
    sync.mkdir()
    real = isolation._worker_command

    def command(request, *budget_args):
        launch = real(request, *budget_args)
        assert launch[1:4] == ["-I", "-m", "dead_letter._mbox_worker"], "the watch adds no worker arguments"
        return [sys.executable, "-I", "-c", _HOOKED_WORKER, mode, str(sync), str(size_mib), *launch[4:]]

    monkeypatch.setattr(isolation, "_worker_command", command)
    return sync


def tracked_processes(monkeypatch):
    processes = []
    original = subprocess.Popen

    def tracked(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(isolation.subprocess, "Popen", tracked)
    return processes


def archive(tmp_path, count=3):
    source = tmp_path / "mail.mbox"
    source.write_bytes((POSTMARK + MESSAGE) * count)
    return source


def outputs(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()) if root.exists() else []


# -- admission: runs everywhere via a patched sys.platform ---------------------

@pytest.mark.parametrize("platform", ["linux", "win32", "freebsd14"])
def test_memory_watch_is_unsupported_off_macos(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    with pytest.raises(isolation.MboxBudgetError) as caught:
        isolation.validate_budgets(isolation.WorkerBudgets(memory_watch_mib=512), worker_mode=True)
    assert caught.value.code == "mbox_budget_unsupported"
    assert "--mbox-memory-watch-mib" in caught.value.message and "memory_watch_mib" in caught.value.message
    assert {"win32": "Windows", "linux": "Linux"}.get(platform, platform) in caught.value.message


def test_memory_watch_is_admitted_on_macos_and_memory_budget_stays_unsupported(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    isolation.validate_budgets(isolation.WorkerBudgets(memory_watch_mib=512, cpu_seconds=5), worker_mode=True)
    with pytest.raises(isolation.MboxBudgetError) as caught:
        isolation.validate_budgets(
            isolation.WorkerBudgets(memory_limit_mib=512, memory_watch_mib=512), worker_mode=True,
        )
    assert caught.value.code == "mbox_budget_unsupported" and "--mbox-memory-mib" in caught.value.message


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_unsupported_watch_fails_before_any_worker_or_output(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    def unexpected(*args, **kwargs):
        pytest.fail("an unsupported watch must not start a worker")
    monkeypatch.setattr(isolation.subprocess, "Popen", unexpected)
    source = archive(tmp_path)
    with pytest.raises(isolation.MboxBudgetError, match="mbox_budget_unsupported"):
        list(convert_mbox(source, output=tmp_path / "out", timeout_seconds=15, memory_watch_mib=512))
    with pytest.raises(isolation.MboxBudgetError, match="mbox_budget_unsupported"):
        list(convert_mbox_archive(tmp_path / "missing.zip", output=tmp_path / "out",
                                  timeout_seconds=15, memory_watch_mib=512))
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "5", 2**62])
def test_memory_watch_value_must_be_a_positive_bounded_integer(monkeypatch, value):
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(ValueError, match="memory_watch_mib .*positive integer"):
        isolation.validate_budgets(isolation.WorkerBudgets(memory_watch_mib=value), worker_mode=True)


def test_memory_watch_requires_worker_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(ValueError, match="require worker mode"):
        list(convert_mbox(archive(tmp_path), output=tmp_path / "out", memory_watch_mib=512))
    assert not (tmp_path / "out").exists()


def test_watch_is_not_a_worker_argument():
    budgets = isolation.WorkerBudgets(memory_watch_mib=512)
    assert budgets.requested() == [] and budgets.worker_args() == ()


# -- struct layout and sampling logic -------------------------------------------

@native_macos
def test_struct_layouts_match_the_macos_sdk():
    # Offsets from <sys/resource.h> rusage_info_v4 and <sys/signal.h> siginfo_t.
    # siginfo_t uses C long, so its size is only meaningful on the Darwin ABI.
    info = watch._RusageInfoV4
    assert ctypes.sizeof(info) == 296
    assert (info.ri_phys_footprint.offset, info.ri_proc_start_abstime.offset,
            info.ri_proc_exit_abstime.offset, info.ri_lifetime_max_phys_footprint.offset) == (72, 80, 88, 240)
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(watch._SigInfo) == 104 and watch._SigInfo.si_pid.offset == 12


def fake_watch(monkeypatch, samples, exits=None, limit_mib=100):
    readings = iter(samples)
    exited = iter(exits or [False] * len(samples))
    self_sample = watch.FootprintSample(MIB, MIB, 1, 0)
    monkeypatch.setattr(watch, "read_footprint", lambda pid: self_sample if pid == os.getpid() else next(readings))
    monkeypatch.setattr(watch, "exited_unreaped", lambda pid: next(exited))
    return watch.FootprintWatch(limit_mib), cast(Any, SimpleNamespace(pid=os.getpid() + 1, returncode=None))


def sample(footprint_mib, peak_mib=None, start=7, exit=0):
    return watch.FootprintSample(int(footprint_mib * MIB), int((peak_mib or footprint_mib) * MIB), start, exit)


def test_poll_stops_only_above_the_threshold(monkeypatch):
    monitor, process = fake_watch(monkeypatch, [sample(50), sample(100), sample(100.5)])
    assert monitor.poll(process) is False
    assert monitor.poll(process) is False  # equal to the threshold is not over it
    with pytest.raises(watch.MemoryWatchExceeded) as caught:
        monitor.poll(process)
    assert caught.value.footprint == int(100.5 * MIB)


def test_zombie_sample_ends_sampling_and_reports_its_lifetime_peak(monkeypatch):
    # A zombie's current footprint reads 0; it must not read as "alive and small".
    monitor, process = fake_watch(monkeypatch, [sample(50), sample(0, peak_mib=300, exit=99)], exits=[False, True])
    assert monitor.poll(process) is False
    assert monitor.poll(process) is True
    assert monitor.overshoot() == {"code": "mbox_memory_watch_overshoot", "limit_mib": 100,
                                   "peak_bytes": 300 * MIB}


def test_exit_statistics_before_waitid_confirmation_keep_polling(monkeypatch):
    # Exit statistics can appear before waitid reports the child as waitable.
    # Polling must continue (so deadline and cancellation checks still run)
    # instead of handing the supervisor a blocking reap.
    monitor, process = fake_watch(
        monkeypatch, [sample(0, peak_mib=300, exit=99), sample(0, peak_mib=300, exit=99)], exits=[False, True],
    )
    assert monitor.poll(process) is False
    assert monitor.peak == 300 * MIB
    assert monitor.poll(process) is True
    assert monitor.overshoot()["peak_bytes"] == 300 * MIB


def test_unconfirmed_exit_still_reaches_the_deadline(tmp_path, monkeypatch):
    # A worker that never becomes waitable while its statistics read as exited
    # must end as a timeout, not block in a reap.
    class NeverWaitable:
        interval = 0.01
        def __init__(self):
            self.polls = 0
        def poll(self, process):
            self.polls += 1
            return False
        def overshoot(self):
            return None
    monitor = NeverWaitable()
    monkeypatch.setattr(isolation, "_worker_command",
                        lambda request, *args: [sys.executable, "-I", "-c", "import time; time.sleep(30)"])
    request = tmp_path / "request.json"
    request.write_text("{}", encoding="utf-8")
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        isolation._run_worker(request, 0.5, memory_watch=cast(Any, monitor))
    assert time.monotonic() - started < 10 and monitor.polls > 1


def test_exit_within_threshold_has_no_overshoot(monkeypatch):
    monitor, process = fake_watch(monkeypatch, [sample(0, peak_mib=100, exit=99)], exits=[True])
    assert monitor.poll(process) is True and monitor.overshoot() is None


@pytest.mark.parametrize("samples, exits", [
    ([sample(50), sample(50, start=8)], [False, False]),  # a different process start time
    ([sample(0, peak_mib=300)], [True]),  # exited, but the kernel kept no exit statistics
])
def test_untrustworthy_samples_fail_closed(monkeypatch, samples, exits):
    monitor, process = fake_watch(monkeypatch, samples, exits)
    with pytest.raises(watch.MemoryWatchError):
        for _ in samples:
            monitor.poll(process)


def test_read_errors_and_reaped_workers_fail_closed(monkeypatch):
    monitor, process = fake_watch(monkeypatch, [], exits=[False])
    def denied(pid):
        raise OSError(errno.ESRCH, "no such process")
    monkeypatch.setattr(watch, "read_footprint", denied)
    with pytest.raises(watch.MemoryWatchError):
        monitor.poll(process)
    with pytest.raises(watch.MemoryWatchError):
        monitor.poll(cast(Any, SimpleNamespace(pid=process.pid, returncode=0)))


def test_unavailable_footprint_api_aborts_before_a_worker_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    def unavailable(pid):
        raise OSError(errno.EPERM, "denied")
    monkeypatch.setattr(watch, "read_footprint", unavailable)
    def unexpected(*args, **kwargs):
        pytest.fail("no worker may run unwatched")
    monkeypatch.setattr(isolation.subprocess, "Popen", unexpected)
    rows = list(convert_mbox(archive(tmp_path), output=tmp_path / "out", timeout_seconds=15, memory_watch_mib=512))
    assert len(rows) == 1 and rows[0].mbox is None
    assert rows[0].error["code"] == "mbox_budget_apply_failed" and rows[0].error["stage"] == "worker"
    assert outputs(tmp_path / "out") == []


def test_unset_watch_never_samples(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("an unset watch must not sample")
    monkeypatch.setattr(watch, "FootprintWatch", unexpected)
    rows = list(convert_mbox(archive(tmp_path, 1), output=tmp_path / "out", timeout_seconds=30))
    assert [row.success for row in rows] == [True] and "memory_watch" not in rows[0].diagnostics


def test_unwatched_imports_and_conversion_do_not_load_the_native_binding(tmp_path):
    # ctypes blocked: importing the package and an unwatched worker import must
    # work, and the watch module must stay unloaded. A child interpreter keeps
    # this test's own imports out of the check.
    source = archive(tmp_path, 1)
    script = (
        "import sys\n"
        "sys.modules['ctypes'] = None\n"
        "import dead_letter, dead_letter.core.mbox_isolation\n"
        "from dead_letter.core.mbox_import import convert_mbox\n"
        "rows = list(convert_mbox(sys.argv[1], output=sys.argv[2], timeout_seconds=30))\n"
        "assert [row.success for row in rows] == [True], rows\n"
        "assert 'dead_letter.core.mbox_memory_watch' not in sys.modules\n"
    )
    subprocess.run([sys.executable, "-c", script, str(source), str(tmp_path / "out")], check=True, timeout=120)


def test_watch_without_native_binding_aborts_before_a_worker_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "ctypes", None)
    monkeypatch.delitem(sys.modules, "dead_letter.core.mbox_memory_watch", raising=False)
    def unexpected(*args, **kwargs):
        pytest.fail("no worker may run unwatched")
    monkeypatch.setattr(isolation.subprocess, "Popen", unexpected)
    rows = list(convert_mbox(archive(tmp_path), output=tmp_path / "out", timeout_seconds=15, memory_watch_mib=512))
    assert len(rows) == 1 and rows[0].mbox is None
    assert rows[0].error["code"] == "mbox_budget_apply_failed"
    assert outputs(tmp_path / "out") == []


def _worker_claiming_memory_watch(request, timeout, budget_args=(), **kwargs):
    """A receipt that tries to supply the parent-only diagnostic."""
    data = json.loads(request.read_text(encoding="utf-8"))
    record = data["record"]
    artifacts = request.parent / "artifacts"
    artifacts.mkdir()
    (artifacts / f"{record['index']:08d}-{record['sha256'][:16]}.md").write_text("output\n", encoding="utf-8")
    (request.parent / "receipt.json").write_text(json.dumps({
        "schema_version": 1, "index": record["index"], "sha256": record["sha256"], "success": True,
        "diagnostics": {"state": "normal", "memory_watch": {}}, "error_code": None,
    }), encoding="utf-8")
    return 0


@pytest.mark.parametrize("watched", [False, True])
def test_worker_supplied_memory_watch_diagnostic_is_an_invalid_receipt(tmp_path, monkeypatch, watched):
    extra = {}
    if watched:
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(watch, "read_footprint", lambda pid: watch.FootprintSample(MIB, MIB, 1, 0))
        extra["memory_watch_mib"] = 512
    monkeypatch.setattr(isolation, "_run_worker", _worker_claiming_memory_watch)
    root = tmp_path / "out"
    rows = list(convert_mbox(archive(tmp_path), output=root, timeout_seconds=15, **extra))
    assert [row.success for row in rows] == [False, False, False]
    assert all(row.error["code"] == "mbox_worker_invalid_result" for row in rows)
    assert all(row.diagnostics is None for row in rows)
    assert outputs(root) == []


# -- native macOS workers -------------------------------------------------------

@native_macos
def test_native_reading_of_this_process():
    reading = watch.read_footprint(os.getpid())
    assert reading.footprint > 0 and reading.lifetime_peak >= reading.footprint
    assert reading.start_abstime > 0 and reading.exit_abstime == 0
    with pytest.raises(OSError):
        watch.read_footprint(-5)


@native_macos
def test_worker_over_the_threshold_is_stopped_and_later_records_continue(tmp_path, monkeypatch):
    sync = hooked_worker(monkeypatch, tmp_path, "hold", size_mib=512)
    processes = tracked_processes(monkeypatch)
    root = tmp_path / "out"
    rows = list(convert_mbox(archive(tmp_path), output=root, timeout_seconds=60, memory_watch_mib=256))
    assert [row.success for row in rows] == [True, False, True]
    assert [row.mbox["index"] for row in rows] == [1, 2, 3]
    assert rows[1].error == {"code": "mbox_message_memory_watch", "stage": "worker",
                             "message": isolation._ERROR_MESSAGES["mbox_message_memory_watch"]}
    assert rows[1].diagnostics is None and rows[1].output is None
    # The sampled PID is the interpreter that allocated, not a launcher.
    assert int((sync / "ready").read_text(encoding="ascii")) == processes[1].pid
    assert processes[1].returncode == -signal.SIGKILL
    assert all(process.returncode is not None for process in processes)
    assert not [name for name in outputs(root) if name.startswith("00000002-")]
    assert len([name for name in outputs(root) if name.endswith(".md")]) == 2


@native_macos
@pytest.mark.parametrize("bundles", [False, True])
def test_spike_between_samples_is_reported_as_overshoot_and_output_kept(tmp_path, monkeypatch, bundles):
    sync = hooked_worker(monkeypatch, tmp_path, "spike", size_mib=512)
    processes = tracked_processes(monkeypatch)
    real_read = watch.read_footprint
    gate = {"held": False}

    def gated(pid):
        reading = real_read(pid)
        if pid != os.getpid() and not gate["held"] and (sync / "ready").exists():
            # Hold the sampler from before the spike until the worker exits, so
            # the spike falls between two samples by construction.
            gate["held"] = True
            (sync / "go").write_bytes(b"")
            deadline = time.monotonic() + 60
            while not watch.exited_unreaped(pid):
                assert time.monotonic() < deadline, "spiking worker did not exit"
                time.sleep(0.005)
        return reading

    monkeypatch.setattr(watch, "read_footprint", gated)
    root = tmp_path / "out"
    rows = list(convert_mbox(archive(tmp_path), output=root, bundles=bundles, timeout_seconds=60,
                             memory_watch_mib=256))
    assert gate["held"]
    assert [row.success for row in rows] == [True, True, True]
    overshoot = rows[1].diagnostics["memory_watch"]
    assert overshoot["code"] == "mbox_memory_watch_overshoot" and overshoot["limit_mib"] == 256
    assert overshoot["peak_bytes"] > 512 * MIB
    assert rows[1].output is not None and rows[1].output.is_file()
    assert "memory_watch" not in rows[0].diagnostics and "memory_watch" not in rows[2].diagnostics
    assert processes[1].returncode == 0


@native_macos
def test_blocked_worker_still_times_out_while_watched(tmp_path, monkeypatch):
    sync = hooked_worker(monkeypatch, tmp_path, "hang")
    processes = tracked_processes(monkeypatch)
    sampled = []
    real_read = watch.read_footprint
    def counting(pid):
        sampled.append(pid)
        return real_read(pid)
    monkeypatch.setattr(watch, "read_footprint", counting)
    root = tmp_path / "out"
    rows = list(convert_mbox(archive(tmp_path), output=root, timeout_seconds=5, memory_watch_mib=1024))
    assert (sync / "ready").exists()
    assert [row.success for row in rows] == [True, False, True]
    assert rows[1].error["code"] == "mbox_message_timeout"
    assert sampled.count(processes[1].pid) > 1
    assert all(process.returncode is not None for process in processes)
    assert not [name for name in outputs(root) if name.startswith("00000002-")]


@native_macos
def test_cancellation_while_watched_reaps_worker_and_leaves_no_thread(tmp_path, monkeypatch):
    sync = hooked_worker(monkeypatch, tmp_path, "hang")
    processes = tracked_processes(monkeypatch)
    threads = set(threading.enumerate())
    root = tmp_path / "out"
    rows = []
    with pytest.raises(MboxCancelled):
        for row in convert_mbox(archive(tmp_path), output=root, timeout_seconds=60, memory_watch_mib=1024,
                                cancel_requested=lambda: (sync / "ready").exists()):
            rows.append(row)
    assert [row.success for row in rows] == [True]
    assert len(processes) == 2 and all(process.returncode is not None for process in processes)
    assert set(threading.enumerate()) == threads
    assert not [name for name in outputs(root) if name.startswith("00000002-")]


@native_macos
def test_unreadable_footprint_of_a_live_worker_aborts_the_import(tmp_path, monkeypatch):
    sync = hooked_worker(monkeypatch, tmp_path, "hang")
    processes = tracked_processes(monkeypatch)
    real_read = watch.read_footprint
    def denied_after_ready(pid):
        if pid != os.getpid() and (sync / "ready").exists():
            raise OSError(errno.EPERM, "denied")
        return real_read(pid)
    monkeypatch.setattr(watch, "read_footprint", denied_after_ready)
    root = tmp_path / "out"
    rows = list(convert_mbox(archive(tmp_path), output=root, timeout_seconds=60, memory_watch_mib=1024))
    assert [row.success for row in rows] == [True, False]
    assert rows[1].mbox is None
    assert rows[1].error["code"] == "mbox_budget_apply_failed" and rows[1].error["stage"] == "worker"
    assert len(processes) == 2 and all(process.returncode is not None for process in processes)
    assert not [name for name in outputs(root) if name.startswith("00000002-")]
