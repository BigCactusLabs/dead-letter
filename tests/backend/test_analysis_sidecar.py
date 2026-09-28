"""Persistence contracts with synthetic email and a provider fake; no live HTTP."""

import asyncio
import builtins
import errno
import hashlib
import json
import os
import socket
import stat
import sys
from types import SimpleNamespace
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


@pytest.mark.parametrize("status", ["failed", "skipped"])
def test_non_success_only_writes_separate_attempts(setup, monkeypatch, status):
    source, output, _ = setup
    if status == "failed":

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
        assert result["execution_status"] == status
        assert result["sidecar"]["outcome"] == "attempt_recorded"
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
    if isinstance(exception, OSError):
        result = run(source, output)
        assert result["execution_status"] == "succeeded"
        assert result["answers"]
        assert result["sidecar"] == {
            "outcome": "write_failed",
            "error_code": "analysis_output_write_failed",
        }
    else:
        with pytest.raises(KeyboardInterrupt):
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
    result = run(source, output)
    assert result["execution_status"] == "succeeded" and result["answers"]
    assert result["sidecar"] == {
        "outcome": "write_failed",
        "error_code": "analysis_output_write_failed",
    }
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


@pytest.mark.parametrize("status,exit_code", [("failed", 1), ("skipped", 0)])
def test_cli_non_success_records_attempt_with_existing_exit(
    setup, monkeypatch, capsys, status, exit_code
):
    source, output, _ = setup
    if status == "failed":

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
        source = email_file(source.parent, body="")
        monkeypatch.setattr(TypeSafeProvider, "evaluate", forbidden)
    assert (
        cli.main(
            ["analyze", str(source), "--provider", "typesafe", "--output", str(output)]
        )
        == exit_code
    )
    result = json.loads(capsys.readouterr().out)
    assert result["execution_status"] == status
    assert result["sidecar"]["outcome"] == "attempt_recorded"
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
    result = run(source, output)
    assert result["execution_status"] == "succeeded" and result["answers"]
    assert result["sidecar"] == {
        "outcome": "write_failed",
        "error_code": "analysis_output_write_failed",
    }
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


@pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX directory permissions require a non-root process",
)
@pytest.mark.parametrize("location", ["parent", "attempts"])
def test_unwritable_destination_refused_before_source_or_provider(
    setup, monkeypatch, capsys, location
):
    source, output, calls = setup
    if location == "parent":
        directory = output.parent / "readonly"
        directory.mkdir()
        output = directory / output.name
    else:
        directory = output.with_name(output.name + ".attempts")
        directory.mkdir()
    directory.chmod(0o500)
    monkeypatch.setattr(sidecar, "preflight", forbidden)
    monkeypatch.setattr(sidecar, "prepare_eml", forbidden)
    try:
        assert (
            cli.main(
                [
                    "analyze",
                    str(source),
                    "--provider",
                    "typesafe",
                    "--output",
                    str(output),
                ]
            )
            == 2
        )
        captured = capsys.readouterr()
        assert captured.out == ""
        assert json.loads(captured.err)["error_code"] == "analysis_output_invalid"
        assert not calls and not output.exists()
        assert not list(directory.iterdir())
    finally:
        directory.chmod(0o700)


def test_attempts_file_refused_before_source_or_provider(setup, monkeypatch, capsys):
    source, output, calls = setup
    attempts = output.with_name(output.name + ".attempts")
    attempts.write_text("do not change")
    monkeypatch.setattr(sidecar, "preflight", forbidden)
    monkeypatch.setattr(sidecar, "prepare_eml", forbidden)
    assert (
        cli.main(
            ["analyze", str(source), "--provider", "typesafe", "--output", str(output)]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error_code"] == "analysis_output_invalid"
    assert not calls and not output.exists()
    assert attempts.read_text() == "do not change"
    assert set(output.parent.iterdir()) == {source, attempts}


@pytest.mark.parametrize(
    "failure",
    [OSError(errno.EACCES, "PRIVATE_ERROR"), AnalysisError("invalid_json_data")],
)
def test_cli_preserves_full_result_on_publication_failure(
    setup, monkeypatch, capsys, failure
):
    source, output, calls = setup
    original = source.read_bytes()
    published = []

    def fail(path, result):
        published.append(json.loads(json.dumps(result)))
        raise failure

    monkeypatch.setattr(sidecar, "_publish", fail)
    assert (
        cli.main(
            ["analyze", str(source), "--provider", "typesafe", "--output", str(output)]
        )
        == 1
    )
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.err == ""
    assert result.pop("sidecar") == {
        "outcome": "write_failed",
        "error_code": "analysis_output_write_failed",
    }
    assert result == published[0]
    assert result["execution_status"] == "succeeded" and result["answers"]
    assert len(calls) == 1 and not output.exists()
    assert source.read_bytes() == original
    assert "PRIVATE_ERROR" not in captured.out


@pytest.mark.parametrize("field", ["adapter_version", "sdk_version"])
@pytest.mark.parametrize("change", ["upgrade", "tamper", "rekey"])
def test_adapter_and_sdk_versions_invalidate_reuse(setup, monkeypatch, field, change):
    from dead_letter.analysis import service

    source, output, calls = setup
    result = run(source, output)
    if change == "upgrade":
        if field == "adapter_version":
            monkeypatch.setattr(service, "ADAPTER_VERSION", "new-adapter-version")
        else:
            monkeypatch.setattr(sidecar, "SDK_VERSION", "9.9.9")
    else:
        result[field] = "9.9.9"
        if change == "rekey":
            result["reuse_key"] = sidecar._reuse_key(result)
        save(output, result)
    original = output.read_bytes()
    monkeypatch.setattr(sidecar, "preflight", forbidden)
    with pytest.raises(AnalysisError, match="^analysis_output_mismatch$"):
        run(source, output)
    assert len(calls) == 1 and output.read_bytes() == original


def test_reuse_reports_current_source_name_without_changing_stored_file(
    setup, monkeypatch, capsys
):
    source, output, calls = setup
    run(source, output, focus_identity=("reader@example.com",))
    saved = output.read_bytes()
    renamed = source.with_name("renamed-copy.eml")
    renamed.write_bytes(source.read_bytes())
    monkeypatch.setattr(sidecar, "preflight", forbidden)
    assert (
        cli.main(
            [
                "analyze",
                str(renamed),
                "--provider",
                "typesafe",
                "--output",
                str(output),
                "--identity",
                "reader@example.com",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["source"]["reference"] == renamed.name
    assert result["sidecar"]["stored_source_name"] == source.name
    assert result["focus_identity"] == {"aliases": ["reader@example.com"]}
    assert json.loads(saved)["source"]["reference"] == source.name
    assert output.read_bytes() == saved and len(calls) == 1
    if os.name != "nt":
        assert stat.S_IMODE(output.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "code", ["typesafe_api_key_missing", "typesafe_sdk_not_installed"]
)
def test_preflight_failure_matches_stdout_only_before_source_read(
    setup, monkeypatch, capsys, code
):
    from dead_letter.analysis import service

    source, output, calls = setup

    def fail(**kwargs):
        raise AnalysisError(code)

    monkeypatch.setattr(sidecar, "preflight", fail)
    monkeypatch.setattr(service, "preflight", fail)
    monkeypatch.setattr(sidecar, "prepare_eml", forbidden)
    monkeypatch.setattr(service, "prepare_eml", forbidden)
    args = ["analyze", str(source), "--provider", "typesafe"]
    assert cli.main(args) == 1
    plain = capsys.readouterr()
    assert cli.main([*args, "--output", str(output)]) == 1
    persisted = capsys.readouterr()
    assert persisted == plain
    assert plain.out == "" and json.loads(plain.err)["error_code"] == code
    assert not calls and set(source.parent.iterdir()) == {source}


def test_missing_key_real_preflight_never_reads_source(setup, monkeypatch, capsys):
    from dead_letter.analysis.providers.typesafe import preflight

    source, output, calls = setup
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(sidecar, "preflight", preflight)
    monkeypatch.setattr(sidecar, "prepare_eml", forbidden)
    assert (
        cli.main(
            ["analyze", str(source), "--provider", "typesafe", "--output", str(output)]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error_code"] == "typesafe_api_key_missing"
    assert not calls and set(source.parent.iterdir()) == {source}


@pytest.mark.parametrize("future_seconds", [2, 300, 300.001])
def test_clock_skew_window(setup, monkeypatch, future_seconds):
    source, output, calls = setup
    result = run(source, output)
    now = datetime.now(timezone.utc)
    result["created_at"] = (now + timedelta(seconds=future_seconds)).isoformat()
    result["returned_model"] = "jev-resolved"
    save(output, result)
    original = output.read_bytes()
    monkeypatch.setattr(sidecar, "_now", lambda: now)
    monkeypatch.setattr(sidecar, "preflight", forbidden)
    if future_seconds > 300:
        with pytest.raises(AnalysisError, match="^analysis_output_corrupt$"):
            run(source, output)
    else:
        reused = run(source, output, alias_max_age=0)
        assert reused["sidecar"]["age_seconds"] == 0
    assert len(calls) == 1 and output.read_bytes() == original


@pytest.mark.parametrize("stage", ["before_reuse", "open"])
def test_vanished_target_reenters_absent_flow_with_preflight_before_reread(
    setup, monkeypatch, stage
):
    source, output, calls = setup
    run(source, output)
    events = []
    prepare = sidecar.prepare_eml
    open_file = os.open

    def prepare_and_remove(*args, **kwargs):
        events.append("read")
        prepared = prepare(*args, **kwargs)
        if stage == "before_reuse" and events == ["read"]:
            output.unlink()
        return prepared

    def open_and_remove(path, flags, *args, **kwargs):
        if stage == "open" and path == output and output.exists():
            output.unlink()
        return open_file(path, flags, *args, **kwargs)

    monkeypatch.setattr(sidecar, "prepare_eml", prepare_and_remove)
    monkeypatch.setattr(os, "open", open_and_remove)
    monkeypatch.setattr(
        sidecar, "preflight", lambda **kwargs: events.append("preflight")
    )
    result = run(source, output)
    assert result["sidecar"]["outcome"] == "written"
    assert events == ["read", "preflight", "read"]
    assert len(calls) == 2 and output.exists()


def test_mac_fullfsync_and_unsupported_fallback(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append("fsync"))

    def fullsync(fd, command):
        assert command == 51
        calls.append("fullfsync")

    module = SimpleNamespace(F_FULLFSYNC=51, fcntl=fullsync)
    monkeypatch.setitem(sys.modules, "fcntl", module)

    def write(name):
        fd = os.open(tmp_path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        sidecar._write_file(fd, b"synthetic")

    write("fullsync")
    assert "fullfsync" in calls
    calls.clear()

    def unsupported(fd, command):
        calls.append("unsupported")
        raise OSError(errno.ENOTSUP, "unsupported")

    module.fcntl = unsupported
    write("fallback")
    assert "unsupported" in calls and "fsync" in calls


def test_repeated_target_disappearance_is_bounded(setup, monkeypatch):
    source, output, calls = setup
    run(source, output)
    stored = output.read_bytes()
    prepare = sidecar.prepare_eml
    read_count = []

    def remove_and_recreate(*args, **kwargs):
        read_count.append(1)
        prepared = prepare(*args, **kwargs)
        output.unlink()
        return prepared

    validate = sidecar._validate_destination

    def recreate_after_disappearance(path):
        if not path.exists():
            path.write_bytes(stored)
        return validate(path)

    monkeypatch.setattr(sidecar, "prepare_eml", remove_and_recreate)
    monkeypatch.setattr(sidecar, "_validate_destination", recreate_after_disappearance)
    monkeypatch.setattr(sidecar, "preflight", forbidden)
    with pytest.raises(AnalysisError, match="^analysis_output_corrupt$"):
        run(source, output)
    assert len(read_count) == 2 and len(calls) == 1
    assert not output.exists()


def test_directory_sync_failure_retains_result_and_complete_output(setup, monkeypatch):
    source, output, calls = setup

    def fail(path):
        raise OSError(errno.EIO, "private sync error")

    monkeypatch.setattr(sidecar, "_fsync_directory", fail)
    result = run(source, output)
    assert result["sidecar"]["outcome"] == "write_failed"
    assert result["execution_status"] == "succeeded"
    saved = json.loads(output.read_text())
    assert saved == {key: value for key, value in result.items() if key != "sidecar"}
    monkeypatch.setattr(sidecar, "preflight", forbidden)
    assert run(source, output)["sidecar"]["outcome"] == "reused"
    assert len(calls) == 1


def test_fullfsync_io_failure_retains_result(setup, monkeypatch):
    source, output, _ = setup

    def fail(fd, command):
        raise OSError(errno.EIO, "private disk error")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(
        sys.modules, "fcntl", SimpleNamespace(F_FULLFSYNC=51, fcntl=fail)
    )
    result = run(source, output)
    assert result["sidecar"]["outcome"] == "write_failed"
    assert result["execution_status"] == "succeeded" and result["answers"]
    assert not output.exists() and not list(output.parent.glob(".*.tmp"))


def test_fullfsync_missing_constant_uses_fsync(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace())
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append(fd))
    fd = os.open(tmp_path / "output", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    sidecar._write_file(fd, b"synthetic")
    assert calls == [fd]
