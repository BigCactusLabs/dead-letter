"""Directory contracts with the pinned SDK and fake HTTP; no live inference."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from dead_letter.analysis import (
    AnalysisError, DirectoryAnalysisInterrupted, analyze_directory, analyze_to_sidecar,
)
from dead_letter.analysis import directory, sidecar
from dead_letter.analysis.providers import typesafe
from dead_letter.analysis.providers.typesafe import TypeSafeConfig
from dead_letter.backend import cli
from dead_letter.core._pipeline import _iter_source_eml_files
from tests.backend.test_analysis_eml import email_file
from tests.backend.test_typesafe_provider import KEY, body_for, httpx, success


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", KEY)
    monkeypatch.delenv("TYPESAFE_BASE_URL", raising=False)
    calls, clients = [], []
    state = {"handler": success, "calls": calls, "clients": clients}
    original = typesafe._guarded_http_client

    async def handle(request):
        calls.append(request)
        response = state["handler"](request)
        return await response if hasattr(response, "__await__") else response

    def client(*args):
        result = original(*args[:-1], httpx.MockTransport(handle))
        clients.append(result)
        return result

    monkeypatch.setattr(typesafe, "_guarded_http_client", client)
    return state


def tree(tmp_path, names):
    source = tmp_path / "mail"
    source.mkdir()
    for name in names:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        email_file(target.parent, body=f"Please reply about {name}.").rename(target)
    return source, tmp_path / "results"


def run(source, output, **kwargs):
    return asyncio.run(analyze_directory(
        source, output, provider="typesafe", allow_remote=True,
        config=TypeSafeConfig(max_retries=0), on_disclosure=lambda value: None, **kwargs,
    ))


def sequence(wire, statuses):
    pending = iter(statuses)

    def handler(request):
        status = next(pending)
        if status == "timeout":
            raise httpx.ReadTimeout("synthetic private error", request=request)
        if status == "connection":
            raise httpx.ConnectError("synthetic private error", request=request)
        return success(request) if status == 200 else httpx.Response(status, json={"error": "private"})

    wire["handler"] = handler


def test_nested_success_summary_reuse_and_single_preflight(tmp_path, monkeypatch, wire):
    source, output = tree(tmp_path, ["a.eml", "nested/b.EML", "nested/c.eml"])
    (source / "ignored.txt").write_text("not mail")
    preflights, disclosures = [], []
    original = directory.preflight

    def check(**kwargs):
        preflights.append(kwargs)
        original(**kwargs)

    def response(request):
        body = body_for(request)
        body["model"] = "jev-observed-version"
        return httpx.Response(200, json=body)

    wire["handler"] = response
    monkeypatch.setattr(directory, "preflight", check)
    result = asyncio.run(analyze_directory(
        source, output, provider="typesafe", allow_remote=True, model="jev-alias",
        on_disclosure=disclosures.append,
    ))
    assert result["schema_version"] == 1
    assert result["status"] == "completed" and result["stop_reason"] is None
    assert result["counts"] == dict(discovered=3, succeeded=3, reused=0, skipped=0, failed=0, not_started=0)
    assert result["observed_models"] == {"jev-observed-version": 3}
    assert result["usage"] == {"input_tokens": 369, "output_tokens": 30}
    assert result["items_with_unknown_usage"] == 0
    assert result["billing_status"] == "unknown"
    assert len(preflights) == 1 and len(disclosures) == 3 and len(wire["calls"]) == 3
    before = {path: path.read_bytes() for path in output.rglob("*.json")}
    assert len(before) == 3
    for item in result["items"]:
        assert item["output"] == str(Path(item["source"]).with_suffix(".analysis.json"))
        stored = json.loads((output / item["output"]).read_text())
        assert stored["execution_status"] == "succeeded"
        assert stored["source"]["reference"] == Path(item["source"]).name
    monkeypatch.delenv("TYPESAFE_API_KEY")

    def forbidden(*args, **kwargs):
        pytest.fail("offline reuse attempted SDK/preflight")

    monkeypatch.setattr(directory, "preflight", forbidden)
    monkeypatch.setattr(typesafe.importlib, "import_module", forbidden)
    resumed = run(source, output, model="jev-alias")
    assert resumed["counts"]["reused"] == 3 and resumed["counts"]["succeeded"] == 0
    assert resumed["billing_status"] == "not_attempted"
    assert resumed["observed_models"] == result["observed_models"]
    assert resumed["usage"] == result["usage"]
    assert {path: path.read_bytes() for path in before} == before
    assert len(wire["calls"]) == 3
    assert all(client.is_closed for client in wire["clients"])


@pytest.mark.parametrize("statuses,reason", [
    ([401], "authentication_failed"), ([403], "authentication_failed"),
    ([422, 400, 422], "request_rejected"),
    ([429, 503, "timeout"], "provider_throttled"),
    (["connection", "timeout", 500], "provider_throttled"),
])
def test_stops_at_threshold_without_batch_retries(tmp_path, wire, statuses, reason):
    source, output = tree(tmp_path, [f"{i}.eml" for i in range(7)])
    sequence(wire, statuses)
    result = run(source, output, jobs=1)
    assert result["status"] == "stopped" and result["stop_reason"] == reason
    assert result["counts"]["failed"] == len(statuses)
    assert result["counts"]["not_started"] == 7 - len(statuses)
    assert len(wire["calls"]) == len(statuses)
    assert len(list(output.rglob("*.json"))) == len(statuses)
    for item in result["items"][len(statuses):]:
        assert item["outcome"] == "not_started"
        assert not (output / (item["output"] + ".attempts")).exists()


def test_success_resets_throttle_and_first_three_rejection_window(tmp_path, wire):
    source, output = tree(tmp_path, [f"{i}.eml" for i in range(10)])
    sequence(wire, [429, 500, 200, 422, 422, 422, 429, 500, "timeout"])
    result = run(source, output, jobs=1)
    assert result["stop_reason"] == "provider_throttled"
    assert result["counts"] == dict(discovered=10, succeeded=1, reused=0, skipped=0, failed=8, not_started=1)
    assert len(wire["calls"]) == 9
    assert result["usage"] == {"input_tokens": 123, "output_tokens": 10}
    assert result["items_with_unknown_usage"] == 8


def test_local_refusals_do_not_change_provider_streak(tmp_path, wire):
    source, output = tree(tmp_path, [f"{i}.eml" for i in range(6)])
    output.mkdir()
    corrupt = output / "1.analysis.json"
    corrupt.write_text("corrupt")
    sequence(wire, [429, 500, 503])
    result = run(source, output, jobs=1)
    assert result["stop_reason"] == "provider_throttled"
    assert result["items"][1]["error_code"] == "analysis_output_corrupt"
    assert result["counts"]["failed"] == 4 and result["counts"]["not_started"] == 2
    assert corrupt.read_text() == "corrupt"


def test_mixed_success_skip_failure_and_cli_exit(tmp_path, wire, capsys):
    source, output = tree(tmp_path, ["0.eml", "1.eml", "2.eml"])
    email_file(source, '<div class="gmail_quote"><blockquote>Old request</blockquote></div>', html=True).rename(source / "1.eml")
    sequence(wire, [200, 404])
    code = cli.main(["analyze", str(source), "--output-dir", str(output), "--provider", "typesafe", "--jobs", "1", "--max-retries", "0"])
    result = json.loads(capsys.readouterr().out)
    assert code == 1 and result["status"] == "completed"
    assert result["counts"] == dict(discovered=3, succeeded=1, reused=0, skipped=1, failed=1, not_started=0)
    assert result["items"][1]["error_code"] == "no_authored_text"
    assert len(wire["calls"]) == 2


def test_unknown_usage_and_model_are_not_invented(tmp_path, wire):
    source, output = tree(tmp_path, ["0.eml", "1.eml"])

    def handler(request):
        body = body_for(request)
        body.pop("usage")
        body.pop("model")
        return httpx.Response(200, json=body)

    wire["handler"] = handler
    result = run(source, output)
    assert result["usage"] == {} and result["observed_models"] == {}
    assert result["items_with_unknown_usage"] == 2


def test_case_alias_collision_has_no_execution(tmp_path, monkeypatch, wire):
    source, output = tree(tmp_path, ["x.eml", "safe.eml"])
    # Case-insensitive hosts cannot create two distinct case-only source names.
    # Supply the discovery shape a case-sensitive source volume can contain.
    monkeypatch.setattr(directory, "_iter_source_eml_files", lambda root: [source / "x.eml", source / "X.EML", source / "safe.eml"])
    result = run(source, output)
    assert result["counts"]["failed"] == 2 and result["counts"]["succeeded"] == 1
    assert [item["error_code"] for item in result["items"][:2]] == ["analysis_output_collision"] * 2
    assert not (output / "x.analysis.json").exists()
    assert len(wire["calls"]) == 1


def test_discovery_matches_conversion_including_symlinks(tmp_path, wire):
    source, output = tree(tmp_path, ["z.EML", "a/nested.eml"])
    outside = email_file(tmp_path)
    (source / "outside.eml").symlink_to(outside)
    (source / "alias.eml").symlink_to(source / "z.EML")
    (source / "dir-alias").symlink_to(source / "a", target_is_directory=True)
    (source / "broken.eml").symlink_to(source / "missing.eml")
    expected = [path.relative_to(source).as_posix() for path in _iter_source_eml_files(source)]
    result = run(source, output)
    assert [item["source"] for item in result["items"]] == expected
    assert len(expected) == 2 and len(wire["calls"]) == 2


def test_preflight_missing_key_stops_before_source_read(tmp_path, monkeypatch, wire):
    source, output = tree(tmp_path, ["0.eml", "1.eml"])
    monkeypatch.delenv("TYPESAFE_API_KEY")
    monkeypatch.setattr(sidecar, "prepare_eml", lambda *a, **k: pytest.fail("source read"))
    result = run(source, output)
    assert result["stop_reason"] == "preflight_failed"
    assert result["counts"]["failed"] == 1 and result["counts"]["not_started"] == 1
    assert result["items"][0]["error_code"] == "typesafe_api_key_missing"
    assert result["billing_status"] == "not_attempted"
    assert not wire["calls"] and not list(output.rglob("*.json"))


def test_consent_precedes_discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(directory, "_iter_source_eml_files", lambda *a: pytest.fail("discovery"))
    with pytest.raises(AnalysisError, match="^remote_analysis_not_authorized$"):
        asyncio.run(analyze_directory(tmp_path, tmp_path / "out", provider="typesafe"))


@pytest.mark.parametrize("jobs", [0, 17, True, 1.5])
def test_python_jobs_validation(tmp_path, jobs):
    with pytest.raises(AnalysisError, match="^invalid_analysis_jobs$"):
        run(tmp_path, tmp_path / "out", jobs=jobs)


def test_inside_source_rejected_even_through_alias(tmp_path):
    source, _ = tree(tmp_path, ["x.eml"])
    alias = tmp_path / "alias"
    alias.symlink_to(source, target_is_directory=True)
    with pytest.raises(AnalysisError, match="^analysis_output_inside_source$"):
        run(source, alias / "output")


@pytest.mark.parametrize("options,code", [
    ([], "analysis_output_dir_required"),
    (["--output-dir", "OUT", "--output", "single.json"], "invalid_analysis_arguments"),
    (["--output-dir", "OUT", "--dry-run"], "invalid_analysis_arguments"),
    (["--output-dir", "OUT", "--show-state"], "invalid_analysis_arguments"),
    (["--output-dir", "OUT", "--jobs", "0"], "invalid_analysis_jobs"),
    (["--output-dir", "OUT", "--jobs", "17"], "invalid_analysis_jobs"),
    (["--output-dir", "OUT", "--jobs", "many"], "invalid_analysis_jobs"),
])
def test_cli_directory_arguments(tmp_path, options, code, capsys):
    source, output = tree(tmp_path, ["x.eml"])
    options = [str(output) if arg == "OUT" else arg for arg in options]
    assert cli.main(["analyze", str(source), "--provider", "typesafe", *options]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.err)["error_code"] == code and not captured.out
    assert not output.exists()


def test_cli_file_rejects_output_dir(tmp_path, capsys):
    source, output = tree(tmp_path, ["x.eml"])
    assert cli.main(["analyze", str(source / "x.eml"), "--provider", "typesafe", "--output-dir", str(output)]) == 2
    assert json.loads(capsys.readouterr().err)["error_code"] == "invalid_analysis_arguments"


def test_empty_directory_and_keyless_cli_resume_exit_zero(tmp_path, monkeypatch, wire, capsys):
    source, output = tree(tmp_path, ["x.eml"])
    run(source, output)
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert cli.main(["analyze", str(source), "--provider", "typesafe", "--output-dir", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["reused"] == 1
    empty = tmp_path / "empty"
    empty.mkdir()
    result = run(empty, output)
    assert result["counts"]["discovered"] == 0 and result["status"] == "completed"


def test_external_cancel_preserves_success_and_attempts(tmp_path, wire):
    source, output = tree(tmp_path, [f"{i}.eml" for i in range(7)])

    async def scenario():
        active = asyncio.Event()
        blocked = asyncio.Event()
        started = 0

        async def handler(request):
            nonlocal started
            if len(wire["calls"]) == 1:
                return success(request)
            started += 1
            if started == 3:
                active.set()
            await blocked.wait()
            return success(request)

        wire["handler"] = handler
        task = asyncio.create_task(analyze_directory(
            source, output, provider="typesafe", allow_remote=True, jobs=3,
            on_disclosure=lambda value: None,
        ))
        await asyncio.wait_for(active.wait(), 5)
        saved = (output / "0.analysis.json").read_bytes()
        task.cancel()
        with pytest.raises(DirectoryAnalysisInterrupted) as caught:
            await task
        assert task.cancelled()
        assert (output / "0.analysis.json").read_bytes() == saved
        return caught.value.summary

    result = asyncio.run(scenario())
    assert result["status"] == result["stop_reason"] == "interrupted"
    assert result["counts"] == dict(discovered=7, succeeded=1, reused=0, skipped=0, failed=3, not_started=3)
    assert result["billing_status"] == "unknown"
    assert all(client.is_closed for client in wire["clients"])
    attempts = list(output.glob("*.attempts/*.json"))
    assert len(attempts) == 3
    for path in attempts:
        raw = path.read_text()
        attempt = json.loads(raw)
        assert attempt["execution_status"] == "interrupted"
        assert attempt["billing_status"] == "unknown" and len(attempt["attempts"]) == 1
        assert "Please reply" not in raw and KEY not in raw
    assert not list(output.rglob("*.tmp"))
    # The completed sidecar passes the same strict resume validator.
    reused = asyncio.run(analyze_to_sidecar(source / "0.eml", output / "0.analysis.json", provider="typesafe"))
    assert reused["sidecar"]["outcome"] == "reused"
    wire["handler"] = success
    resumed = run(source, output)
    assert resumed["counts"]["reused"] == 1 and resumed["counts"]["succeeded"] == 6


def test_auth_cancels_inflight_and_closes_clients(tmp_path, wire):
    source, output = tree(tmp_path, [f"{i}.eml" for i in range(6)])

    async def scenario():
        active = asyncio.Event()
        block = asyncio.Event()
        started = 0

        async def handler(request):
            nonlocal started
            started += 1
            number = started
            if started == 3:
                active.set()
            if number == 1:
                await active.wait()
                return httpx.Response(401, json={"error": "private"})
            await block.wait()
            return success(request)

        wire["handler"] = handler
        return await asyncio.wait_for(analyze_directory(
            source, output, provider="typesafe", allow_remote=True, jobs=3,
            on_disclosure=lambda value: None,
        ), 5)

    result = asyncio.run(scenario())
    assert result["status"] == "stopped" and result["stop_reason"] == "authentication_failed"
    assert result["counts"]["failed"] == 3 and result["counts"]["not_started"] == 3
    assert [item["error_code"] for item in result["items"][:3]] == ["provider_authentication_failed", "analysis_interrupted", "analysis_interrupted"]
    assert all(client.is_closed for client in wire["clients"])


def test_cancel_before_http_leaves_no_record(tmp_path, monkeypatch, wire):
    source, output = tree(tmp_path, ["0.eml", "1.eml"])

    async def scenario():
        ready = asyncio.Event()
        block = asyncio.Event()
        original = sidecar.analyze_prepared

        async def before_request(*args, **kwargs):
            ready.set()
            await block.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(sidecar, "analyze_prepared", before_request)
        task = asyncio.create_task(analyze_directory(source, output, provider="typesafe", allow_remote=True))
        await ready.wait()
        task.cancel()
        with pytest.raises(DirectoryAnalysisInterrupted) as caught:
            await task
        return caught.value.summary

    result = asyncio.run(scenario())
    assert result["billing_status"] == "not_attempted"
    assert not wire["calls"] and not list(output.rglob("*.json"))


def test_cli_interrupt_prints_partial_summary(tmp_path, monkeypatch, wire, capsys):
    import dead_letter.analysis as analysis
    source, output = tree(tmp_path, [f"{i}.eml" for i in range(5)])

    async def cancelled_run(*args, **kwargs):
        active = asyncio.Event()
        block = asyncio.Event()

        async def handler(request):
            if len(wire["calls"]) == 1:
                return success(request)
            if len(wire["calls"]) == 3:
                active.set()
            await block.wait()
            return success(request)

        wire["handler"] = handler
        task = asyncio.create_task(analyze_directory(*args, **kwargs))
        await active.wait()
        task.cancel()
        return await task

    monkeypatch.setattr(analysis, "analyze_directory", cancelled_run)
    assert cli.main(["analyze", str(source), "--provider", "typesafe", "--output-dir", str(output), "--jobs", "2"]) == 130
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "interrupted" and result["counts"]["succeeded"] == 1
    assert all(client.is_closed for client in wire["clients"])


def test_convert_dir_never_imports_analysis_providers(tmp_path):
    source, output = tree(tmp_path, ["x.eml"])
    code = """
import sys
from dead_letter.core import convert_dir
assert all(not name.startswith('dead_letter.analysis.providers') for name in sys.modules)
results = convert_dir(sys.argv[1], output=sys.argv[2])
assert len(results) == 1 and results[0].success
assert all(not name.startswith('dead_letter.analysis.providers') for name in sys.modules)
"""
    completed = subprocess.run([sys.executable, "-c", code, str(source), str(output)], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("status,reason", [(422, "request_rejected"), (503, "provider_throttled")])
def test_non_auth_stop_allows_inflight_success(tmp_path, wire, status, reason):
    source, output = tree(tmp_path, [f"{i}.eml" for i in range(8)])

    async def scenario():
        inflight = asyncio.Event()
        release = asyncio.Event()
        started = 0

        async def handler(request):
            nonlocal started
            started += 1
            number = started
            if number == 2:
                inflight.set()
                await release.wait()
                return success(request)
            await inflight.wait()
            return httpx.Response(status, json={"error": "private"})

        wire["handler"] = handler
        task = asyncio.create_task(analyze_directory(
            source, output, provider="typesafe", allow_remote=True, jobs=2,
            config=TypeSafeConfig(max_retries=0), on_disclosure=lambda value: None,
        ))
        # Observe the actual terminal sidecar before allowing the held call out.
        async def until_stopped():
            while len(list(output.glob("*.attempts/*.json"))) < 3:
                await asyncio.sleep(0)
        await asyncio.wait_for(until_stopped(), 5)
        assert not task.done()
        assert started == 4
        release.set()
        return await asyncio.wait_for(task, 5)

    result = asyncio.run(scenario())
    assert result["stop_reason"] == reason
    assert result["counts"] == dict(discovered=8, succeeded=1, reused=0, skipped=0, failed=3, not_started=4)
    assert all(client.is_closed for client in wire["clients"])


def test_sdk_retry_and_long_retry_after_remain_provider_owned(tmp_path, wire):
    source, output = tree(tmp_path, ["0.eml", "1.eml"])
    sequence(wire, [500, 200, 200])
    result = asyncio.run(analyze_directory(
        source, output, provider="typesafe", allow_remote=True, jobs=1,
        config=TypeSafeConfig(max_retries=1), on_disclosure=lambda value: None,
    ))
    assert result["counts"]["succeeded"] == 2 and len(wire["calls"]) == 3
    assert json.loads((output / "0.analysis.json").read_text())["retry_count"] == 1
    wire["handler"] = lambda request: httpx.Response(429, headers={"retry-after": "45"}, json={})
    result = asyncio.run(analyze_directory(
        source, tmp_path / "limited", provider="typesafe", allow_remote=True, jobs=1,
        config=TypeSafeConfig(budget_seconds=45, max_retries=2), on_disclosure=lambda value: None,
    ))
    assert len(wire["calls"]) == 5
    assert all(item["error_code"] == "provider_rate_limited" for item in result["items"])
    for path in (tmp_path / "limited").glob("*.attempts/*.json"):
        assert json.loads(path.read_text())["retry_count"] == 0


def test_preflight_sdk_version_failure_is_once_and_safe(tmp_path, monkeypatch, wire, capsys):
    source, output = tree(tmp_path, ["0.eml", "1.eml"])
    checks = []

    def version(name):
        checks.append(name)
        return "0.0.0"

    monkeypatch.setattr(typesafe, "version", version)
    assert cli.main(["analyze", str(source), "--provider", "typesafe", "--output-dir", str(output)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["stop_reason"] == "preflight_failed"
    assert result["items"][0]["error_code"] == "unsupported_typesafe_sdk_version"
    assert checks == ["typesafe-sdk"] and not wire["calls"]


def test_attempt_write_error_cannot_swallow_cancellation(tmp_path, monkeypatch, wire):
    source, output = tree(tmp_path, ["0.eml"])

    async def scenario():
        ready = asyncio.Event()
        block = asyncio.Event()

        async def handler(request):
            ready.set()
            await block.wait()
            return success(request)

        def cannot_write(*args):
            raise OSError("synthetic private detail")

        wire["handler"] = handler
        monkeypatch.setattr(sidecar, "_record_attempt", cannot_write)
        task = asyncio.create_task(analyze_directory(source, output, provider="typesafe", allow_remote=True))
        await ready.wait()
        task.cancel()
        with pytest.raises(DirectoryAnalysisInterrupted) as caught:
            await task
        assert task.cancelled()
        return caught.value.summary

    result = asyncio.run(scenario())
    assert result["status"] == "interrupted"
    assert result["billing_status"] == "unknown"
    assert result["items"][0]["error_code"] == "analysis_output_write_failed"
    assert all(client.is_closed for client in wire["clients"])
    assert "synthetic private detail" not in json.dumps(result)


def test_cli_sigint_subprocess_prints_partial_summary(tmp_path):
    source, output = tree(tmp_path, [f"{i}.eml" for i in range(5)])
    code = """
import asyncio
import os
import signal
import sys
from dead_letter.analysis.providers import typesafe
from dead_letter.backend.analysis_cli import main
from tests.backend.test_typesafe_provider import KEY, httpx, success
os.environ['TYPESAFE_API_KEY'] = KEY
os.environ.pop('TYPESAFE_BASE_URL', None)
original = typesafe._guarded_http_client
calls = 0
async def handler(request):
    global calls
    calls += 1
    if calls == 1:
        return success(request)
    if calls == 3:
        asyncio.get_running_loop().call_soon(signal.raise_signal, signal.SIGINT)
    await asyncio.Event().wait()
def client(*args):
    return original(*args[:-1], httpx.MockTransport(handler))
typesafe._guarded_http_client = client
sys.exit(main([sys.argv[1], '--output-dir', sys.argv[2], '--provider', 'typesafe', '--jobs', '2']))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, str(source), str(output)],
        capture_output=True, text=True, timeout=10,
    )
    assert completed.returncode == 130, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "interrupted"
    assert result["counts"] == dict(discovered=5, succeeded=1, reused=0, skipped=0, failed=2, not_started=2)
    assert len(list(output.glob("*.attempts/*.json"))) == 2
    assert not list(output.rglob("*.tmp"))


def test_invalid_target_symlink_is_local_refusal(tmp_path, wire):
    source, output = tree(tmp_path, ["0.eml", "1.eml"])
    output.mkdir()
    target = output / "0.analysis.json"
    target.symlink_to(target.name)
    result = run(source, output, jobs=1)
    assert result["status"] == "completed"
    assert result["counts"]["failed"] == result["counts"]["succeeded"] == 1
    assert result["items"][0]["error_code"] == "analysis_output_invalid"
    assert target.is_symlink() and len(wire["calls"]) == 1


def test_resolved_parent_alias_collision(tmp_path, wire):
    source, output = tree(tmp_path, ["a/x.eml", "b/x.eml"])
    (output / "a").mkdir(parents=True)
    (output / "b").symlink_to(output / "a", target_is_directory=True)
    result = run(source, output)
    assert result["counts"]["failed"] == 2
    assert all(item["error_code"] == "analysis_output_collision" for item in result["items"])
    assert not wire["calls"] and not list(output.rglob("*.json"))
