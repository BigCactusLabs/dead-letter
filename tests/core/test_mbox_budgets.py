"""Opt-in worker resource budgets (#140): capability matrix and real workers."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

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

# Test-only program: runs the real worker file as a script (as a budgeted launch
# does) after optionally replacing one private pipeline function or wrapping
# resource.setrlimit to record what was loaded when limits were applied. It
# replaces the private command builder, not a production flag, environment hook
# or email instruction.
_HOOKED_WORKER = r"""
import json, runpy, sys
mode, probe, script = sys.argv[1:4]
sys.argv = [script, *sys.argv[4:]]
index = json.loads(open(sys.argv[1], encoding="utf-8").read())["record"]["index"]
import atexit, resource
real_setrlimit = resource.setrlimit
FORBIDDEN = ("dead_letter", "mailparser", "nh3", "selectolax", "html_to_markdown")
def note(entry):
    with open(probe, "a", encoding="utf-8") as out:
        out.write(json.dumps(entry) + "\n")
def recording_setrlimit(kind, limits):
    if kind != resource.RLIMIT_CORE:
        note({"loaded": sorted(n for n in sys.modules if n.split(".")[0] in FORBIDDEN),
              "no_bytecode": sys.dont_write_bytecode})
        if mode == "setrlimit-fails":
            raise OSError("denied")
    return real_setrlimit(kind, limits)
resource.setrlimit = recording_setrlimit
if mode == "inherit":
    real_setrlimit(resource.RLIMIT_CPU, (20, resource.getrlimit(resource.RLIMIT_CPU)[1]))
    real_setrlimit(resource.RLIMIT_FSIZE, (2 * 1024 * 1024, resource.getrlimit(resource.RLIMIT_FSIZE)[1]))
atexit.register(lambda: note({"final": {"cpu": resource.getrlimit(resource.RLIMIT_CPU),
                                        "fsize": resource.getrlimit(resource.RLIMIT_FSIZE)}}))
if index == 2 and mode in ("spin", "allocate", "exit3", "exit4", "decode-memory"):
    import dead_letter.core.mbox_import as mbox_import
    def hook(*args, **kwargs):
        if mode == "spin":
            while True:
                pass
        if mode == "allocate":
            return bytearray(64 * 1024 ** 3)
        raise SystemExit(int(mode[-1]))
    if mode == "decode-memory":
        import dead_letter.core.attachments as attachments
        import types
        attachments.base64 = types.SimpleNamespace(b64decode=lambda payload, *a, **k: bytearray(2 ** 62))
    else:
        mbox_import._build_rendered_markdown = hook
runpy.run_path(script, run_name="__main__")
"""


def hooked_worker(monkeypatch, mode, probe=None):
    calls = []
    real = isolation._worker_command
    def command(request, *budget_args):
        calls.append(budget_args)
        launch = real(request, *budget_args)
        assert launch[1:3] == ["-I", "-B"], "hooks model the budgeted script launch"
        return [sys.executable, "-I", "-B", "-c", _HOOKED_WORKER, mode, str(probe or os.devnull), *launch[3:]]
    monkeypatch.setattr(isolation, "_worker_command", command)
    return calls


def probe_entries(probe):
    return [json.loads(line) for line in probe.read_text(encoding="utf-8").splitlines()]


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
    assert isolation._worker_command(Path("request.json")) == [
        sys.executable, "-I", "-m", "dead_letter._mbox_worker", "request.json",
    ]
    assert isolation.WorkerBudgets(1, 2, 3).worker_args() == ("memory_mib=1", "cpu_seconds=2", "max_output_mib=3")


# -- worker argument and exit-status contract ---------------------------------

def test_budgeted_worker_is_launched_as_script_without_bytecode():
    command = isolation._worker_command(Path("request.json"), "cpu_seconds=5", "nonce=" + "a" * 32)
    assert command[:3] == [sys.executable, "-I", "-B"]
    assert Path(command[3]) == Path(worker.__file__).resolve() and Path(command[3]).is_file()
    assert command[4:] == ["request.json", "cpu_seconds=5", "nonce=" + "a" * 32]


NONCE = "nonce=" + "0123456789abcdef" * 2


@pytest.mark.parametrize("args", [
    ["memory_mib", NONCE], ["memory_mib=0", NONCE], ["memory_mib=-1", NONCE], ["memory_mib=1.5", NONCE],
    ["stack=1", NONCE], ["cpu_seconds=1", "cpu_seconds=2", NONCE], ["cpu_seconds=٣", NONCE],
    ["--cpu-seconds=1", NONCE], ["cpu_seconds=1"], [NONCE], ["cpu_seconds=1", "nonce=short"],
    ["cpu_seconds=1", "nonce=" + "0123456789ABCDEF" * 2], ["cpu_seconds=1", NONCE, NONCE],
])
def test_worker_rejects_malformed_budget_arguments(args):
    with pytest.raises(ValueError):
        worker._parse_budgets(args)


def test_worker_parses_parent_budget_arguments():
    assert worker._parse_budgets([]) == ({}, None)
    assert worker._parse_budgets(["memory_mib=64", "cpu_seconds=2", "max_output_mib=3", NONCE]) == (
        {"memory_mib": 64, "cpu_seconds": 2, "max_output_mib": 3}, NONCE[6:],
    )


INF = 2**63 - 1


@pytest.mark.parametrize("requested, inherited, expected", [
    ((30, 31), (INF, INF), (30, 31)),
    ((30, 31), (1, INF), (1, 31)),
    ((30, 31), (1, 5), (1, 5)),
    ((30, 31), (40, 50), (30, 31)),
    ((30, 31), (INF, 10), (30, 10)),
])
def test_limit_pair_never_raises_inherited_soft_or_hard(requested, inherited, expected):
    assert worker._limit_pair(requested, inherited, INF) == expected


@pytest.mark.parametrize("returncode, budgets, expected", [
    (worker.EXIT_MEMORY_LIMIT, isolation.WorkerBudgets(memory_limit_mib=64), None),
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


def test_memory_outcome_requires_matching_control_file():
    budgets = isolation.WorkerBudgets(memory_limit_mib=64)
    assert isolation._exceeded_limit(worker.EXIT_MEMORY_LIMIT, budgets, "memory_limit") == "memory"
    assert isolation._exceeded_limit(worker.EXIT_MEMORY_LIMIT, isolation.WorkerBudgets(cpu_seconds=1),
                                     "memory_limit") is None


_CONTROL_WORKER = r"""
import json, sys
from pathlib import Path
request, mode = Path(sys.argv[1]), sys.argv[2]
nonce = [a for a in sys.argv[3:] if a.startswith("nonce=")][0][6:]
body = {"right": {"nonce": nonce, "outcome": "apply_failed"},
        "wrong-nonce": {"nonce": "f" * 32, "outcome": "apply_failed"},
        "wrong-outcome": {"nonce": nonce, "outcome": "memory_limit"},
        "extra-field": {"nonce": nonce, "outcome": "apply_failed", "x": 1}}.get(mode)
if body is not None:
    (request.parent / "budget-status.json").write_text(json.dumps(body), encoding="utf-8")
raise SystemExit(3)
"""


@pytest.mark.parametrize("mode", ["right", "wrong-nonce", "wrong-outcome", "extra-field", "missing"])
def test_budget_exit_status_needs_matching_nonce_control_file(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(isolation, "_worker_command", lambda request, *args: [
        sys.executable, "-I", "-c", _CONTROL_WORKER, str(request), mode, *args])
    path = tmp_path / "input.eml"
    path.write_bytes(MESSAGE)
    record = MboxRecord(1, 0, len(POSTMARK), len(POSTMARK + MESSAGE), hashlib.sha256(MESSAGE).hexdigest(), path)
    def call():
        return isolation.convert_record_isolated(
            record, Path("archive.mbox"), tmp_path / "out", ConvertOptions(), bundles=False,
            unescape="preserve", timeout=15, budgets=isolation.WorkerBudgets(cpu_seconds=5))
    if mode == "right":
        with pytest.raises(isolation.MboxBudgetError, match="mbox_budget_apply_failed"):
            call()
    else:
        assert call().error["code"] == "mbox_worker_crashed"


ATTACHED = (
    b"From: alice@example.test\nSubject: Attached\nMIME-Version: 1.0\n"
    b"Content-Type: multipart/mixed; boundary=b1\n\n"
    b"--b1\nContent-Type: text/plain\n\nSee attached\n"
    b"--b1\nContent-Type: application/octet-stream\nContent-Disposition: attachment; filename=data.bin\n"
    b"Content-Transfer-Encoding: base64\n\nAAECAwQF\n--b1--\n\n"
)


def test_memory_watch_sees_memoryerror_the_pipeline_swallows(tmp_path, monkeypatch):
    from dead_letter.core import attachments
    def decode(payload, *args, **kwargs):
        return bytearray(2**62)  # MemoryError raised from C, then caught by the pipeline
    # Replace only the attachment module's decoder, not stdlib email's.
    monkeypatch.setattr(attachments, "base64", SimpleNamespace(b64decode=decode))
    path = tmp_path / "input.eml"
    path.write_bytes(ATTACHED)
    record = MboxRecord(1, 0, 0, len(ATTACHED), hashlib.sha256(ATTACHED).hexdigest(), path)
    def convert(root):
        return mbox_import._convert_record(record, Path("a.mbox"), root, ConvertOptions(),
                                           bundles=True, unescape="preserve", reraise=(MemoryError,))
    unwatched = convert(tmp_path / "plain")
    assert unwatched.success and not (unwatched.output.parent / "attachments").exists()
    with worker._MemoryErrorWatch() as watch:
        convert(tmp_path / "watched")
    assert watch.seen
    monkeypatch.undo()
    with worker._MemoryErrorWatch() as clean:
        restored = convert(tmp_path / "clean")
    assert restored.success and (restored.output.parent / "attachments" / "data.bin").read_bytes() == bytes(range(6))
    assert not clean.seen
    assert all(sys.monitoring.get_tool(tool) != "dead-letter-memory-watch" for tool in range(6))


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
    assert len(calls) == 1 and calls[0][0] == "cpu_seconds=5" and calls[0][1].startswith("nonce=")
    assert outputs(root) == []


@posix_budgets
def test_limits_apply_before_package_imports_without_bytecode(tmp_path, monkeypatch):
    probe = tmp_path / "probe.jsonl"
    hooked_worker(monkeypatch, "probe", probe)
    source = archive(tmp_path, MESSAGE)
    extra = {"memory_limit_mib": 4096} if sys.platform == "linux" else {}
    [row] = list(convert_mbox(source, output=tmp_path / "out", timeout_seconds=30,
                              cpu_seconds=30, max_output_mib=16, **extra))
    assert row.success
    applied = [entry for entry in probe_entries(probe) if "loaded" in entry]
    assert len(applied) == 2 + len(extra)
    assert all(entry["loaded"] == [] for entry in applied)
    # -B disables bytecode writes from launch, before any limit is applied.
    assert all(entry["no_bytecode"] is True for entry in applied)


@posix_budgets
def test_stricter_inherited_soft_limits_are_preserved(tmp_path, monkeypatch):
    probe = tmp_path / "probe.jsonl"
    hooked_worker(monkeypatch, "inherit", probe)
    source = archive(tmp_path, MESSAGE)
    [row] = list(convert_mbox(source, output=tmp_path / "out", timeout_seconds=30,
                              cpu_seconds=30, max_output_mib=64))
    assert row.success
    [final] = [entry["final"] for entry in probe_entries(probe) if "final" in entry]
    assert final["cpu"] == [20, 31]
    assert final["fsize"] == [2 * 1024 * 1024, 64 * 1024 * 1024]


@posix_budgets
@pytest.mark.parametrize("mode", ["exit3", "exit4"])
def test_ordinary_exit_codes_cannot_impersonate_budget_outcomes(tmp_path, monkeypatch, mode):
    hooked_worker(monkeypatch, mode)
    source = archive(tmp_path, MESSAGE, MESSAGE, MESSAGE)
    extra = {"memory_limit_mib": 4096} if sys.platform == "linux" else {}
    rows = list(convert_mbox(source, output=tmp_path / "out", timeout_seconds=30, cpu_seconds=30, **extra))
    assert [r.success for r in rows] == [True, False, True]
    assert rows[1].error["code"] == "mbox_worker_crashed"


@linux_only
def test_swallowed_decode_memoryerror_withholds_record_in_real_worker(tmp_path, monkeypatch):
    hooked_worker(monkeypatch, "decode-memory")
    source = archive(tmp_path, ATTACHED, ATTACHED, ATTACHED)
    root = tmp_path / "out"
    rows = list(convert_mbox(source, output=root, bundles=True, timeout_seconds=60, memory_limit_mib=4096))
    assert [r.success for r in rows] == [True, False, True]
    assert rows[1].error["message"] == isolation._RESOURCE_MESSAGES["memory"]
    assert not [name for name in outputs(root) if name.startswith("00000002-")]


def test_memory_watch_without_free_tool_id_fails_instead_of_running_unwatched():
    claimed = [tool for tool in range(6) if sys.monitoring.get_tool(tool) is None]
    for tool in claimed:
        sys.monitoring.use_tool_id(tool, "test-occupied")
    try:
        with pytest.raises(RuntimeError, match="tool id"):
            worker._MemoryErrorWatch().__enter__()
    finally:
        for tool in claimed:
            sys.monitoring.free_tool_id(tool)
