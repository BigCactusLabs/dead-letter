"""Persistence contracts with synthetic email and a provider fake; no live HTTP."""

import asyncio
import builtins
import errno
import hashlib
import json
import os
import socket
from datetime import datetime, timedelta, timezone

import pytest

from dead_letter.analysis import AnalysisError, analyze_to_sidecar, prepare_eml
from dead_letter.analysis import sidecar
from dead_letter.analysis.providers.typesafe import TypeSafeConfig, TypeSafeProvider
from dead_letter.backend import cli
from tests.backend.test_analysis_contracts import response_for
from tests.backend.test_analysis_eml import KEY, PRIVATE, email_file


@pytest.fixture
def setup(tmp_path, monkeypatch):
    source = email_file(tmp_path, attachment=True)
    output = tmp_path / "analysis.json"
    calls = []

    async def evaluate(self, request, **kwargs):
        calls.append(request)
        return {
            "execution_status": "succeeded",
            "error_code": None,
            "attempts": [
                {
                    "number": 1,
                    "http_status": 200,
                    "request_id": "req-123",
                    "wire_sha256": "a" * 64,
                    "status": "response_received",
                    "duration_ms": 2,
                }
            ],
            "retry_count": 0,
            "billing_status": "unknown",
            "sdk_version": "0.7.1",
            "response": response_for(request),
        }

    monkeypatch.setattr(sidecar, "preflight", lambda **kwargs: None)
    monkeypatch.setattr(TypeSafeProvider, "evaluate", evaluate)
    return source, output, calls


def run(source, output, **kwargs):
    return asyncio.run(
        analyze_to_sidecar(
            source, output, provider="typesafe", allow_remote=True, **kwargs
        )
    )


def save(output, result):
    result.pop("sidecar", None)
    output.write_text(json.dumps(result), encoding="utf-8")


def forbidden(*args, **kwargs):
    pytest.fail("reuse/refusal invoked provider, preflight, or network")


def test_written_then_offline_reused_exact_bytes_and_no_private_content(
    setup, monkeypatch
):
    source, output, calls = setup
    original = source.read_bytes()
    first = run(source, output)
    stored_bytes = output.read_bytes()
    stored = json.loads(stored_bytes)
    assert first["sidecar"] == {"path": str(output), "outcome": "written"}
    assert "sidecar" not in stored
    assert stored["source"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert stored["source"]["size_bytes"] == len(original)
    assert len(stored["reuse_key"]) == 64
    assert stored["request_fingerprint"] == prepare_eml(source).request.fingerprint
    assert stored["answers"] == response_for(prepare_eml(source).request)["answers"]
    assert len(calls) == 1
    monkeypatch.setattr(sidecar, "preflight", forbidden)
    monkeypatch.setattr(TypeSafeProvider, "evaluate", forbidden)
    original_import = builtins.__import__

    def no_sdk(name, *args, **kwargs):
        assert not name.startswith(("typesafe_sdk", "httpx2"))
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_sdk)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    async def offline():
        monkeypatch.setattr(socket, "socket", forbidden)
        return await analyze_to_sidecar(source, output, provider="typesafe")

    second = asyncio.run(offline())
    assert second["sidecar"]["outcome"] == "reused"
    assert second["sidecar"]["created_at"] == stored["created_at"]
    assert second["sidecar"]["age_seconds"] >= 0
    assert second["sidecar"]["returned_model"] == "jev-1.13.0"
    assert output.read_bytes() == stored_bytes
    assert source.read_bytes() == original
    assert set(output.parent.iterdir()) == {source, output}
    for marker in (
        PRIVATE,
        KEY,
        "PRIVATE_ATTACHMENT_NEVER_SEND",
        "PRIVATE_HEADER_NEVER_SEND",
    ):
        assert marker not in stored_bytes.decode()


@pytest.mark.parametrize("status", ["failed", "skipped", "preflight"])
def test_non_success_only_writes_separate_attempts(setup, monkeypatch, status):
    source, output, _ = setup
    if status == "preflight":

        def fail(**kwargs):
            raise AnalysisError("typesafe_sdk_not_installed")

        monkeypatch.setattr(sidecar, "preflight", fail)
    elif status == "failed":

        async def fail(*args, **kwargs):
            return {
                "execution_status": "failed",
                "error_code": "provider_timeout",
                "attempts": [],
                "retry_count": 0,
                "billing_status": "not_attempted",
                "sdk_version": "0.7.1",
            }

        monkeypatch.setattr(TypeSafeProvider, "evaluate", fail)
    else:
        source = email_file(
            source.parent,
            '<div class="gmail_quote"><blockquote>Old request</blockquote></div>',
            html=True,
        )
        monkeypatch.setattr(TypeSafeProvider, "evaluate", forbidden)
    original = source.read_bytes()
    for _ in range(2):
        result = run(source, output)
        assert result["execution_status"] == (
            "failed" if status == "preflight" else status
        )
        assert result["sidecar"]["outcome"] == "written"
        assert result["answers"] == {}
    assert not output.exists()
    attempts = list(output.with_name(output.name + ".attempts").iterdir())
    assert len(attempts) == 2
    for attempt in attempts:
        raw = attempt.read_text()
        saved = json.loads(raw)
        assert {
            "error_code",
            "execution_status",
            "attempts",
            "retry_count",
            "request_fingerprint",
            "reuse_key",
            "created_at",
        } <= saved.keys()
        assert "sidecar" not in saved
        assert PRIVATE not in raw and KEY not in raw
    assert source.read_bytes() == original


@pytest.mark.parametrize(
    "bad",
    [
        "garbage",
        '{"schema_version":',
        "[]",
        '{"x":1,"x":2}',
        "schema",
        "schema_bool",
        "missing",
        "extra",
        "bad_answer",
        "bad_probability",
        "bad_usage",
        "extra_answer_field",
        "bad_attempt",
        "negative_duration",
        "nan",
        "future",
        "bad_date",
    ],
)
def test_corrupt_outputs_refused_unchanged(setup, monkeypatch, bad):
    source, output, calls = setup
    result = run(source, output)
    if bad == "schema":
        result["schema_version"] = 100
    elif bad == "schema_bool":
        result["schema_version"] = True
    elif bad == "missing":
        del result["source"]
    elif bad == "extra":
        result["body"] = PRIVATE
    elif bad == "bad_answer":
        result["answers"] = {}
    elif bad == "bad_probability":
        result["answers"]["expressed_urgency"]["probabilities"]["0"] = 0.1
    elif bad == "bad_usage":
        result["usage"] = {"total_tokens": -1}
    elif bad == "extra_answer_field":
        result["answers"]["reply_request_present"]["body"] = PRIVATE
    elif bad == "bad_attempt":
        result["attempts"][0]["headers"] = {"secret": KEY}
    elif bad == "negative_duration":
        result["attempts"][0]["duration_ms"] = -1
    elif bad == "nan":
        result["answers"]["reply_request_present"]["noul"] = float("nan")
    elif bad == "future":
        result["created_at"] = (
            datetime.now(timezone.utc) + timedelta(days=1)
        ).isoformat()
    elif bad == "bad_date":
        result["created_at"] = "2026-01-01T00:00:00"
    else:
        output.write_text(bad)
        result = None
    if result is not None:
        save(output, result)
    original = output.read_bytes()
    monkeypatch.setattr(sidecar, "preflight", forbidden)
    with pytest.raises(AnalysisError, match="^analysis_output_corrupt$"):
        run(source, output)
    assert len(calls) == 1
    assert output.read_bytes() == original


@pytest.mark.parametrize(
    "change",
    [
        "source",
        "profile",
        "model",
        "endpoint",
        "context",
        "identity",
        "normalizer",
        "state_builder",
        "reuse_key",
        "source_size",
        "provider",
    ],
)
def test_mismatched_identity_never_runs_provider(setup, monkeypatch, change):
    source, output, calls = setup
    result = run(source, output)
    options = {}
    if change == "source":
        source.write_bytes(source.read_bytes() + b"\n")
    elif change == "profile":
        options["profile_name"] = "triage-choice-v1"
    elif change == "model":
        options["model"] = "jev-latest"
    elif change == "endpoint":
        options["config"] = TypeSafeConfig(base_url="https://proxy.example.com")
    elif change == "context":
        options["max_context_segments"] = 0
    elif change == "identity":
        options["focus_identity"] = ("someone@example.com",)
    elif change == "normalizer":
        import dead_letter.analysis.eml as eml

        monkeypatch.setattr(eml, "PROJECTION_VERSION", "new-normalizer")
    elif change == "state_builder":
        import dead_letter.analysis.state as state

        monkeypatch.setattr(state, "STATE_BUILDER_VERSION", "new-state-builder")
    else:
        if change == "reuse_key":
            result["reuse_key"] = "0" * 64
        elif change == "source_size":
            result["source"]["size_bytes"] += 1
        elif change == "provider":
            result["provider"] = "other"
        save(output, result)
    original = output.read_bytes()
    monkeypatch.setattr(sidecar, "preflight", forbidden)
    with pytest.raises(AnalysisError, match="^analysis_output_mismatch$"):
        run(source, output, **options)
    assert len(calls) == 1
    assert output.read_bytes() == original


@pytest.mark.parametrize("status", ["failed", "skipped"])
def test_non_success_at_result_path_is_not_reused(setup, monkeypatch, status):
    source, output, calls = setup
    result = run(source, output)
    result.update(execution_status=status, answers={}, usage={}, assessment_status=None)
    save(output, result)
    monkeypatch.setattr(sidecar, "preflight", forbidden)
    with pytest.raises(AnalysisError, match="^analysis_output_mismatch$"):
        run(source, output)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "returned,age,limit,stale",
    [
        ("jev-resolved", 20, 60, False),
        ("jev-resolved", 86401, 86400, True),
        ("jev-resolved", 60, 60, False),
        ("jev-1.13.0", 99999999, 0, False),
        (None, 86401, 86400, True),
    ],
)
def test_alias_age_and_exact_model_rules(
    setup, monkeypatch, returned, age, limit, stale
):
    source, output, calls = setup
    result = run(source, output)
    now = datetime.now(timezone.utc)
    result.update(
        returned_model=returned, created_at=(now - timedelta(seconds=age)).isoformat()
    )
    save(output, result)
    monkeypatch.setattr(sidecar, "_now", lambda: now)
    monkeypatch.setattr(sidecar, "preflight", forbidden)
    if stale:
        with pytest.raises(AnalysisError, match="^analysis_output_stale_alias$"):
            run(source, output, alias_max_age=limit)
    else:
        reused = run(source, output, alias_max_age=limit)
        assert reused["sidecar"]["age_seconds"] == age
        assert reused["sidecar"]["returned_model"] == returned
    assert len(calls) == 1


@pytest.mark.parametrize(
    "winner", ["matching", "mismatch", "corrupt", "stale", "symlink"]
)
def test_concurrent_publish_validates_winner_and_never_clobbers(
    setup, monkeypatch, winner
):
    source, output, calls = setup
    link = os.link
    winner_bytes = []

    def racing_link(tmp, target):
        result = json.loads(tmp.read_text())
        if winner == "mismatch":
            result["reuse_key"] = "0" * 64
        elif winner == "stale":
            result.update(
                returned_model="jev-resolved", created_at="2000-01-01T00:00:00+00:00"
            )
        if winner == "symlink":
            target.symlink_to(source)
        else:
            target.write_text("{" if winner == "corrupt" else json.dumps(result))
        winner_bytes.append(target.read_bytes())
        link(tmp, target)

    monkeypatch.setattr(os, "link", racing_link)
    if winner == "matching":
        result = run(source, output)
        assert result["sidecar"]["outcome"] == "reused"
        assert result["sidecar"]["discarded_fresh_result"] is True
    else:
        code = {
            "mismatch": "mismatch",
            "corrupt": "corrupt",
            "stale": "stale_alias",
            "symlink": "invalid",
        }[winner]
        with pytest.raises(AnalysisError, match=f"^analysis_output_{code}$"):
            run(source, output)
    assert output.read_bytes() == winner_bytes[0]
    assert len(calls) == 1
    assert not list(output.parent.glob(".*.tmp"))


@pytest.mark.parametrize(
    "exception", [OSError(errno.EIO, "private write error"), KeyboardInterrupt()]
)
def test_interrupted_after_temp_write_cleans_own_temp_only(
    setup, monkeypatch, exception
):
    source, output, _ = setup
    unrelated = output.parent / ".analysis.json.other.tmp"
    unrelated.write_text("other process")
    original = source.read_bytes()

    def interrupted(tmp, target):
        assert json.loads(tmp.read_text())["execution_status"] == "succeeded"
        assert not target.exists()
        raise exception

    monkeypatch.setattr(os, "link", interrupted)
    expected = AnalysisError if isinstance(exception, OSError) else KeyboardInterrupt
    with pytest.raises(expected):
        run(source, output)
    assert not output.exists()
    assert list(output.parent.glob(".*.tmp")) == [unrelated]
    assert source.read_bytes() == original


@pytest.mark.parametrize(
    "invalid", ["directory", "symlink", "dangling", "missing_parent", "fifo"]
)
def test_invalid_target_refused_before_preparation(setup, monkeypatch, invalid):
    source, output, calls = setup
    if invalid == "directory":
        output.mkdir()
    elif invalid == "symlink":
        output.symlink_to(source)
    elif invalid == "dangling":
        output.symlink_to(output.parent / "absent")
    elif invalid == "missing_parent":
        output = output / "result.json"
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO requires POSIX")
        os.mkfifo(output)
    monkeypatch.setattr(sidecar, "prepare_eml", forbidden)
    with pytest.raises(AnalysisError, match="^analysis_output_invalid$"):
        run(source, output)
    assert not calls


@pytest.mark.parametrize("collision", [False, True])
def test_link_unsupported_exclusive_fallback(setup, monkeypatch, collision):
    source, output, calls = setup

    def unsupported(tmp, target):
        if collision:
            target.write_bytes(tmp.read_bytes())
        raise OSError(errno.ENOTSUP, "unsupported")

    monkeypatch.setattr(os, "link", unsupported)
    result = run(source, output)
    assert result["sidecar"]["outcome"] == ("reused" if collision else "written")
    assert json.loads(output.read_text())["execution_status"] == "succeeded"
    assert len(calls) == 1
    assert not list(output.parent.glob(".*.tmp"))


def test_fallback_write_failure_removes_own_partial_output(setup, monkeypatch):
    source, output, _ = setup
    write = sidecar._write_file
    writes = []

    def fail_second(fd, data):
        writes.append(fd)
        if len(writes) == 2:
            os.write(fd, b"{")
            os.close(fd)
            raise OSError(errno.EIO, "private error")
        write(fd, data)

    def unsupported(*args):
        raise OSError(errno.ENOTSUP, "unsupported")

    monkeypatch.setattr(os, "link", unsupported)
    monkeypatch.setattr(sidecar, "_write_file", fail_second)
    with pytest.raises(AnalysisError, match="^analysis_output_write_failed$"):
        run(source, output)
    assert not output.exists()
    assert not list(output.parent.glob(".*.tmp"))


def test_cli_write_reuse_and_invalid_output(setup, capsys, monkeypatch):
    source, output, calls = setup
    args = ["analyze", str(source), "--provider", "typesafe", "--output", str(output)]
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["sidecar"]["outcome"] == "written"
    monkeypatch.setattr(sidecar, "preflight", forbidden)
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["sidecar"]["outcome"] == "reused"
    assert len(calls) == 1
    output.write_text("{")
    assert cli.main(args) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["error_code"] == "analysis_output_corrupt"
    assert "another output path" in error["hint"]
    args[-1] = str(output.parent / "missing" / "out.json")
    assert cli.main(args) == 2
    assert (
        json.loads(capsys.readouterr().err)["error_code"] == "analysis_output_invalid"
    )


@pytest.mark.parametrize("age", ["-1", "nan", "inf"])
def test_cli_invalid_alias_age_rejected_before_inference(setup, capsys, age):
    source, output, calls = setup
    assert (
        cli.main(
            [
                "analyze",
                str(source),
                "--provider",
                "typesafe",
                "--output",
                str(output),
                "--alias-max-age",
                age,
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().err)["error_code"] == "invalid_alias_max_age"
    assert not calls and not output.exists()


def test_source_mutation_during_provider_call_binds_original_snapshot(
    setup, monkeypatch
):
    source, output, _ = setup
    original = source.read_bytes()
    evaluate = TypeSafeProvider.evaluate

    async def mutation(self, request, **kwargs):
        source.write_bytes(original + b"\nChanged by another process")
        return await evaluate(self, request, **kwargs)

    monkeypatch.setattr(TypeSafeProvider, "evaluate", mutation)
    result = run(source, output)
    assert result["source"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert result["source"]["size_bytes"] == len(original)
    monkeypatch.setattr(sidecar, "preflight", forbidden)
    with pytest.raises(AnalysisError, match="^analysis_output_mismatch$"):
        run(source, output)


def test_cli_race_refusal_reports_discarded_call(setup, monkeypatch, capsys):
    source, output, calls = setup

    def race(tmp, target):
        target.write_text("{")
        raise FileExistsError(errno.EEXIST, "exists")

    monkeypatch.setattr(os, "link", race)
    assert (
        cli.main(
            ["analyze", str(source), "--provider", "typesafe", "--output", str(output)]
        )
        == 1
    )
    error = json.loads(capsys.readouterr().err)
    assert error["error_code"] == "analysis_output_corrupt"
    assert error["discarded_fresh_result"] is True
    assert len(calls) == 1


def test_cli_failure_writes_attempt_and_retains_failure_exit(
    setup, monkeypatch, capsys
):
    source, output, _ = setup

    def fail(**kwargs):
        raise AnalysisError("typesafe_api_key_missing")

    monkeypatch.setattr(sidecar, "preflight", fail)
    assert (
        cli.main(
            ["analyze", str(source), "--provider", "typesafe", "--output", str(output)]
        )
        == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["error_code"] == "typesafe_api_key_missing"
    assert not output.exists()
    assert len(list(output.with_name(output.name + ".attempts").glob("*.json"))) == 1


def test_no_output_keeps_stdout_only_and_original_preflight(setup, monkeypatch, capsys):
    from dead_letter.analysis import service

    source, output, calls = setup
    preflights = []
    monkeypatch.setattr(
        service, "preflight", lambda **kwargs: preflights.append(kwargs)
    )
    assert cli.main(["analyze", str(source), "--provider", "typesafe"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert (
        "sidecar" not in result
        and "reuse_key" not in result
        and "created_at" not in result
    )
    assert len(calls) == len(preflights) == 1
    assert list(source.parent.iterdir()) == [source]


def test_fsync_failure_before_publication_leaves_no_output(setup, monkeypatch):
    source, output, _ = setup

    def fail(fd):
        raise OSError(errno.EIO, "disk failure")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(AnalysisError, match="^analysis_output_write_failed$"):
        run(source, output)
    assert not output.exists()
    assert not list(output.parent.glob(".*.tmp"))


def test_existing_attempts_symlink_is_never_used(setup, monkeypatch, tmp_path):
    source, output, _ = setup
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    output.with_name(output.name + ".attempts").symlink_to(
        elsewhere, target_is_directory=True
    )

    def fail(**kwargs):
        raise AnalysisError("typesafe_api_key_missing")

    monkeypatch.setattr(sidecar, "preflight", fail)
    with pytest.raises(AnalysisError, match="^analysis_output_invalid$"):
        run(source, output)
    assert not list(elsewhere.iterdir()) and not output.exists()
