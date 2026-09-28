"""Opt-in worker resource budgets (#140): capability matrix and real workers."""
from __future__ import annotations

import hashlib
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from dead_letter import _mbox_worker as worker
from dead_letter.core import mbox_import
from dead_letter.core import mbox_isolation as isolation
from dead_letter.core.mbox import MboxRecord
from dead_letter.core.mbox_import import convert_mbox
from dead_letter.core.types import ConvertOptions

POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"
MESSAGE = b"From: alice@example.test\nSubject: Same\n\nHello\n\n"
# A plain-text body over 1 MiB: its rendered Markdown and bundle source copy
# each exceed a 1 MiB per-file output budget.
LARGE = b"From: bob@example.test\nSubject: Large\n\n" + (b"x" * 99 + b"\n") * 21000 + b"\n"

posix_budgets = pytest.mark.skipif(
    sys.platform not in ("linux", "darwin"),
    reason="CPU/output worker budgets are implemented only on Linux and macOS in this slice",
)
linux_only = pytest.mark.skipif(
    sys.platform != "linux", reason="Worker memory budgets (RLIMIT_AS) are supported only on Linux",
)

# Test-only program: wraps the real worker entry point and replaces one private
# pipeline function or stdlib call before it runs. It replaces the private
# command builder, not a production flag, environment hook or email instruction.
_HOOKED_WORKER = r'''
import json, sys
mode = sys.argv.pop(1)
index = json.loads(open(sys.argv[1], encoding="utf-8").read())["record"]["index"]
import dead_letter.core.mbox_import as mbox_import
import dead_letter._mbox_worker as worker
if mode == "setrlimit-fails":
    import resource
    def denied(*args):
        raise OSError("denied")
    resource.setrlimit = denied
elif index == 2 and mode == "spin":
    def spin(*args, **kwargs):
        while True:
            pass
    mbox_import._build_rendered_markdown = spin
elif index == 2 and mode == "allocate":
    def allocate(*args, **kwargs):
        return bytearray(64 * 1024 ** 3)
    mbox_import._build_rendered_markdown = allocate
sys.exit(worker.main())
'''


def hooked_worker(monkeypatch, mode):
    calls = []
    def command(request, *budget_args):
        calls.append(budget_args)
        return [sys.executable, "-I", "-c", _HOOKED_WORKER, mode, str(request), *budget_args]
    monkeypatch.setattr(isolation, "_worker_command", command)
    return calls


def archive(tmp_path, *messages):
    source = tmp_path / "mail.mbox"
    source.write_bytes(b"".join(POSTMARK + message for message in messages))
    return source


def outputs(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()) if root.exists() else []


# -- capability matrix: runs everywhere via a patched sys.platform ------------

@pytest.mark.parametrize("platform, supported", [
    ("linux", {"memory", "cpu", "output"}),
    ("darwin", {"cpu", "output"}),
    ("win32", set()),
    ("freebsd14", set()),
])
@pytest.mark.parametrize("control, kwargs", [
    ("memory", {"memory_limit_mib": 512}),
    ("cpu", {"cpu_seconds": 5}),
    ("output", {"max_output_mib": 8}),
])
def test_capability_matrix_is_decided_in_parent(monkeypatch, platform, supported, control, kwargs):
    monkeypatch.setattr(sys, "platform", platform)
    budgets = isolation.WorkerBudgets(**kwargs)
    if control in supported:
        isolation.validate_budgets(budgets, worker_mode=True)
        return
    with pytest.raises(isolation.MboxBudgetError) as caught:
        isolation.validate_budgets(budgets, worker_mode=True)
    assert caught.value.code == "mbox_budget_unsupported"
    assert control in caught.value.message
    assert {"win32": "Windows", "darwin": "macOS"}.get(platform, platform) in caught.value.message


def test_unsupported_budget_fails_before_any_conversion_or_output(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    def unexpected(*args, **kwargs):
        pytest.fail("an unsupported budget must not start a worker")
    monkeypatch.setattr(isolation.subprocess, "Popen", unexpected)
    source = archive(tmp_path, MESSAGE)
    with pytest.raises(isolation.MboxBudgetError, match="mbox_budget_unsupported"):
        list(convert_mbox(source, output=tmp_path / "out", timeout_seconds=15, cpu_seconds=5))
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("kwargs", [
    {"memory_limit_mib": 512}, {"cpu_seconds": 5}, {"max_output_mib": 8},
])
def test_budget_without_worker_mode_is_a_usage_error(tmp_path, monkeypatch, kwargs):
    def unexpected(*args, **kwargs):
        pytest.fail("a rejected budget must not convert")
    monkeypatch.setattr(mbox_import, "_convert_record", unexpected)
    source = archive(tmp_path, MESSAGE)
    with pytest.raises(ValueError, match="require worker mode"):
        list(convert_mbox(source, output=tmp_path / "out", **kwargs))
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "5", float("nan"), 2**63])
@pytest.mark.parametrize("field", ["memory_limit_mib", "cpu_seconds", "max_output_mib"])
def test_budget_values_must_be_positive_bounded_integers(field, value):
    with pytest.raises(ValueError, match="positive integer"):
        isolation.validate_budgets(isolation.WorkerBudgets(**{field: value}), worker_mode=True)


def test_default_budgets_add_no_worker_arguments():
    assert isolation.WorkerBudgets().requested() == []
    assert isolation.WorkerBudgets().worker_args() == ()
    assert isolation._worker_command(Path("request.json"))[-1] == "request.json"
    assert isolation.WorkerBudgets(1, 2, 3).worker_args() == ("memory_mib=1", "cpu_seconds=2", "max_output_mib=3")


# -- worker argument and exit-status contract ---------------------------------

@pytest.mark.parametrize("args", [
    ["memory_mib"], ["memory_mib=0"], ["memory_mib=-1"], ["memory_mib=1.5"], ["stack=1"],
    ["cpu_seconds=1", "cpu_seconds=2"], ["cpu_seconds=٣"], ["--cpu-seconds=1"],
])
def test_worker_rejects_malformed_budget_arguments(args):
    with pytest.raises(ValueError):
        worker._parse_budgets(args)


def test_worker_parses_parent_budget_arguments():
    assert worker._parse_budgets([]) == {}
    assert worker._parse_budgets(["memory_mib=64", "cpu_seconds=2", "max_output_mib=3"]) == {
        "memory_mib": 64, "cpu_seconds": 2, "max_output_mib": 3,
    }


@pytest.mark.parametrize("returncode, budgets, expected", [
    (worker.EXIT_MEMORY_LIMIT, isolation.WorkerBudgets(memory_limit_mib=64), "memory"),
    (worker.EXIT_MEMORY_LIMIT, isolation.WorkerBudgets(cpu_seconds=5), None),
    (-getattr(signal, "SIGXCPU", 1000), isolation.WorkerBudgets(cpu_seconds=5), "cpu"),
    (-getattr(signal, "SIGXCPU", 1000), isolation.WorkerBudgets(), None),
    (-getattr(signal, "SIGXFSZ", 1001), isolation.WorkerBudgets(max_output_mib=1), "output"),
    (-getattr(signal, "SIGXFSZ", 1001), isolation.WorkerBudgets(cpu_seconds=5), None),
    (-9, isolation.WorkerBudgets(cpu_seconds=5), None),
    (1, isolation.WorkerBudgets(1, 1, 1), None),
])
@posix_budgets
def test_only_matching_budget_outcomes_are_resource_limits(returncode, budgets, expected):
    assert isolation._exceeded_limit(returncode, budgets) == expected


@pytest.mark.parametrize("code", [worker.EXIT_BUDGET_APPLY_FAILED, worker.EXIT_MEMORY_LIMIT])
def test_budget_exit_statuses_without_budgets_remain_crashes(tmp_path, monkeypatch, code):
    monkeypatch.setattr(isolation, "_worker_command",
                        lambda request, *args: [sys.executable, "-I", "-c", f"raise SystemExit({code})"])
    path = tmp_path / "input.eml"
    path.write_bytes(MESSAGE)
    record = MboxRecord(1, 0, len(POSTMARK), len(POSTMARK + MESSAGE), hashlib.sha256(MESSAGE).hexdigest(), path)
    result = isolation.convert_record_isolated(record, Path("archive.mbox"), tmp_path / "out", ConvertOptions(),
        bundles=False, unescape="preserve", timeout=15)
    assert result.error["code"] == "mbox_worker_crashed"


def test_convert_record_reraises_only_requested_exceptions(tmp_path, monkeypatch):
    def exhausted(*args, **kwargs):
        raise MemoryError
    monkeypatch.setattr(mbox_import, "_build_rendered_markdown", exhausted)
    path = tmp_path / "input.eml"
    path.write_bytes(MESSAGE)
    record = MboxRecord(1, 0, len(POSTMARK), len(POSTMARK + MESSAGE), hashlib.sha256(MESSAGE).hexdigest(), path)
    kwargs = {"bundles": False, "unescape": "preserve"}
    default = mbox_import._convert_record(record, Path("a.mbox"), tmp_path / "out", ConvertOptions(), **kwargs)
    assert not default.success and default.error["code"] == "conversion_error"
    with pytest.raises(MemoryError):
        mbox_import._convert_record(record, Path("a.mbox"), tmp_path / "out", ConvertOptions(),
                                    reraise=(MemoryError,), **kwargs)


# -- actual worker processes --------------------------------------------------

@posix_budgets
@pytest.mark.parametrize("bundles", [False, True])
def test_satisfied_budgets_match_unbudgeted_worker_output(tmp_path, bundles):
    source = tmp_path / "archive.mbox"
    source.write_bytes((Path(__file__).parent / "fixtures" / "takeout-synthetic.mbox").read_bytes())
    extra = {"memory_limit_mib": 4096} if sys.platform == "linux" else {}
    plain = list(convert_mbox(source, output=tmp_path / "plain", bundles=bundles, timeout_seconds=30))
    budgeted = list(convert_mbox(source, output=tmp_path / "budget", bundles=bundles, timeout_seconds=30,
                                 cpu_seconds=30, max_output_mib=64, **extra))
    assert [r.success for r in budgeted] == [r.success for r in plain] and all(r.success for r in plain)
    assert [r.diagnostics for r in budgeted] == [r.diagnostics for r in plain]
    def contents(root):
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert contents(tmp_path / "budget") == contents(tmp_path / "plain")


@posix_budgets
@pytest.mark.parametrize("bundles", [False, True])
def test_output_budget_withholds_record_and_later_records_continue(tmp_path, bundles):
    source = archive(tmp_path, MESSAGE, LARGE, MESSAGE)
    original = source.read_bytes()
    root = tmp_path / "out"
    rows = list(convert_mbox(source, output=root, bundles=bundles, timeout_seconds=30, max_output_mib=1))
    assert [r.success for r in rows] == [True, False, True]
    assert [r.mbox["index"] for r in rows] == [1, 2, 3]
    assert rows[1].error == {"code": "mbox_message_resource_limit", "stage": "worker",
                             "message": isolation._RESOURCE_MESSAGES["output"]}
    assert rows[1].output is None
    published = outputs(root)
    assert not [name for name in published if name.startswith("00000002-")]
    assert len([name for name in published if name.endswith(".md")]) == 2
    assert source.read_bytes() == original


@posix_budgets
def test_cpu_budget_is_reaped_withheld_and_later_records_continue(tmp_path, monkeypatch):
    hooked_worker(monkeypatch, "spin")
    processes = []
    original = subprocess.Popen
    def tracked(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(isolation.subprocess, "Popen", tracked)
    source = archive(tmp_path, MESSAGE, MESSAGE, MESSAGE)
    root = tmp_path / "out"
    rows = list(convert_mbox(source, output=root, timeout_seconds=60, cpu_seconds=1))
    assert [r.success for r in rows] == [True, False, True]
    assert rows[1].error["code"] == "mbox_message_resource_limit"
    assert rows[1].error["message"] == isolation._RESOURCE_MESSAGES["cpu"]
    assert processes[1].returncode == -signal.SIGXCPU
    assert all(p.returncode is not None for p in processes)
    assert not [name for name in outputs(root) if name.startswith("00000002-")]


@linux_only
def test_memory_budget_is_withheld_and_later_records_continue(tmp_path, monkeypatch):
    hooked_worker(monkeypatch, "allocate")
    source = archive(tmp_path, MESSAGE, MESSAGE, MESSAGE)
    root = tmp_path / "out"
    rows = list(convert_mbox(source, output=root, bundles=True, timeout_seconds=60, memory_limit_mib=2048))
    assert [r.success for r in rows] == [True, False, True]
    assert rows[1].error["code"] == "mbox_message_resource_limit"
    assert rows[1].error["message"] == isolation._RESOURCE_MESSAGES["memory"]
    assert not [name for name in outputs(root) if name.startswith("00000002-")]


@posix_budgets
def test_budget_apply_failure_aborts_import_before_conversion(tmp_path, monkeypatch):
    calls = hooked_worker(monkeypatch, "setrlimit-fails")
    source = archive(tmp_path, MESSAGE, MESSAGE, MESSAGE)
    root = tmp_path / "out"
    rows = list(convert_mbox(source, output=root, timeout_seconds=30, cpu_seconds=5))
    assert len(rows) == 1 and rows[0].mbox is None and not rows[0].success
    assert rows[0].error["code"] == "mbox_budget_apply_failed"
    assert rows[0].error["stage"] == "worker"
    assert calls == [("cpu_seconds=5",)]
    assert outputs(root) == []
