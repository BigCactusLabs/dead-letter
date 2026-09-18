"""Real synthetic EML preparation and CLI privacy/dispatch regressions."""

from __future__ import annotations

import json
import socket
import sys
from dataclasses import replace
from email.message import EmailMessage
from types import SimpleNamespace

import pytest

from dead_letter.analysis import AnalysisError, message_from_snapshot, prepare_eml
from dead_letter.backend import cli
from dead_letter.core.snapshot import read_snapshot

PRIVATE = "PRIVATE_ANALYSIS_BODY_796c1"
KEY = "PRIVATE_KEY_DO_NOT_PRINT_358a7"


def email_file(tmp_path, body=PRIVATE, *, html=False, reply=False, attachment=False):
    message = EmailMessage()
    message["From"] = "Sender <sender@example.com>"
    message["To"] = "Quinn <quinn@example.com>, Alex <alex@example.com>"
    message["Subject"] = "PRIVATE_ANALYSIS_SUBJECT"
    message["Date"] = "Tue, 02 Jan 2001 10:30:00 -0500"
    message["X-Private-Header"] = "PRIVATE_HEADER_NEVER_SEND"
    if reply:
        message["In-Reply-To"] = "<missing@example.com>"
    message.set_content(body, subtype="html" if html else "plain")
    if attachment:
        message.add_attachment(b"PRIVATE_ATTACHMENT_NEVER_SEND", maintype="application",
                               subtype="octet-stream", filename="private.bin")
    path = tmp_path / "private-analysis-source.eml"
    path.write_bytes(message.as_bytes())
    return path


def test_prepared_eml_separates_local_provenance_from_effective_request(tmp_path, monkeypatch):
    source = email_file(tmp_path, attachment=True)
    original = source.read_bytes()
    monkeypatch.setenv("TYPESAFE_API_KEY", KEY)
    monkeypatch.setenv("TYPESAFE_LOG_LEVEL", "debug")
    def forbidden(*args, **kwargs):
        pytest.fail("offline EML preparation attempted network access")
    monkeypatch.setattr(socket, "socket", forbidden)
    before_modules = set(sys.modules)
    prepared = prepare_eml(source, focus_identity=("quinn@example.com",))
    public = json.dumps(prepared.preview())
    for value in (PRIVATE, KEY, "PRIVATE_ANALYSIS_SUBJECT", source.name, "quinn@example.com"):
        assert value not in public
        assert value not in repr(prepared)
    explicit = prepared.preview(include_state=True)
    assert PRIVATE in json.dumps(explicit)
    assert explicit["source_reference"] == source.name
    assert explicit["state"]["request_scope"] == "focus_identity"
    assert explicit["state"]["coverage"]["attachment_count"] == 1
    assert not explicit["state"]["coverage"]["attachment_text_available"]
    payload = json.dumps(prepared.request.payload())
    for excluded in (source.name, str(source), "PRIVATE_HEADER_NEVER_SEND",
                     "PRIVATE_ATTACHMENT_NEVER_SEND", KEY, "source_sha256", "runtime_packages"):
        assert excluded not in payload
    assert source.read_bytes() == original
    assert list(tmp_path.iterdir()) == [source]
    assert not any(name.startswith("typesafe") for name in set(sys.modules) - before_modules)


def test_missing_parent_is_coverage_not_a_negative_answer(tmp_path):
    prepared = prepare_eml(email_file(tmp_path, "Yes, please do that.", reply=True))
    state = prepared.request.payload()["state"]
    assert state["coverage"]["reply_headers_present"]
    assert not state["coverage"]["thread_context_available"]
    assert not state["coverage"]["external_thread_context_available"]
    assert "answers" not in prepared.preview(include_state=True)
    assert state["reference_time_policy"] == "message_time_not_processing_time"


def test_forward_marker_does_not_consume_only_context_slot(tmp_path):
    source = email_file(tmp_path, "Please handle below.\n\n---------- Forwarded message ---------\n"
                       "From: Someone <other@example.com>\n\nPlease pay the invoice.")
    prepared = prepare_eml(source, max_context_segments=1)
    state = prepared.request.payload()["state"]
    assert any("Please handle below" in part["text"] for part in state["message"]["authored_segments"])
    assert len(state["context"]["segments"]) == 1
    assert "Please pay the invoice" in state["context"]["segments"][0]["text"]
    assert state["context"]["segments"][0]["author"] is None
    assert all("Please pay the invoice" not in part["text"]
               for part in state["message"]["authored_segments"])


def test_quote_only_eml_has_no_authored_evidence(tmp_path):
    source = email_file(tmp_path, '<div class="gmail_quote"><blockquote>Earlier request.</blockquote></div>', html=True)
    state = prepare_eml(source).request.payload()["state"]
    assert state["message"]["authored_segments"] == []
    assert not state["coverage"]["authored_text_available"]
    assert state["coverage"]["thread_context_available"]


def test_fingerprints_follow_inputs_not_source_location(tmp_path):
    source = email_file(tmp_path)
    first = prepare_eml(source)
    renamed = tmp_path / "renamed.eml"
    renamed.write_bytes(source.read_bytes())
    second = prepare_eml(renamed)
    assert first.request.fingerprint == second.request.fingerprint
    assert first.snapshot.source_sha256 == second.snapshot.source_sha256
    assert first.snapshot.source != second.snapshot.source
    variants = [
        prepare_eml(source, focus_identity=("quinn@example.com",)),
        prepare_eml(source, focus_identity=("alex@example.com",)),
        prepare_eml(source, max_context_segments=0),
        prepare_eml(source, profile_name="triage-choice-v1"),
        prepare_eml(source, model="jev-latest"),
        prepare_eml(source, base_url="https://proxy.example.com/gateway"),
    ]
    assert len({first.request.fingerprint, *(item.request.fingerprint for item in variants)}) == 7
    normalization = first.snapshot.normalization
    normalization["runtime_packages"]["html-to-markdown"] = "test-upgrade"
    altered = replace(first.snapshot, normalization_json=json.dumps(normalization, sort_keys=True))
    assert message_from_snapshot(altered).normalization_version != message_from_snapshot(first.snapshot).normalization_version


def test_unknown_zone_kind_fails_closed(tmp_path):
    snapshot = read_snapshot(email_file(tmp_path))
    altered = replace(snapshot, zones=(replace(snapshot.zones[0], kind="new_core_kind"),))
    with pytest.raises(AnalysisError, match="^unsupported_snapshot_zone$"):
        message_from_snapshot(altered)


def test_cli_dry_run_is_not_dispatched_to_conversion(tmp_path, monkeypatch, capsys):
    source = email_file(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("analyze was dispatched to conversion or network")
    monkeypatch.setattr(cli, "core_convert", forbidden)
    monkeypatch.setattr(cli, "core_convert_dir", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setenv("TYPESAFE_API_KEY", KEY)
    result = cli.main(["analyze", str(source), "--provider", "typesafe", "--dry-run",
                       "--identity", "quinn@example.com", "--identity", "Quinn"])
    captured = capsys.readouterr()
    assert result == 0
    assert captured.err == ""
    preview = json.loads(captured.out)
    assert preview["remote_enabled"] is False
    assert preview["execution_status"] == "skipped"
    assert preview["focus_identity_configured"] is True
    for private in (KEY, PRIVATE, source.name, "quinn@example.com"):
        assert private not in captured.out
    assert list(tmp_path.iterdir()) == [source]


def test_cli_show_state_is_explicit(tmp_path, capsys):
    source = email_file(tmp_path)
    assert cli.main(["analyze", str(source), "--provider", "typesafe", "--dry-run", "--show-state"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert PRIVATE in json.dumps(preview["state"])
    assert preview["source_reference"] == source.name


def test_cli_without_dry_run_rejects_before_reading_or_preparing(monkeypatch, capsys):
    import dead_letter.analysis
    def forbidden(*args, **kwargs):
        pytest.fail("remote invocation was not rejected before preparation")
    monkeypatch.setattr(dead_letter.analysis, "prepare_eml", forbidden)
    monkeypatch.setenv("TYPESAFE_API_KEY", KEY)
    result = cli.main(["analyze", "unread-private-file.eml", "--provider", "typesafe"])
    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert json.loads(captured.err)["error_code"] == "remote_analysis_not_implemented"
    assert "unread-private-file" not in captured.err
    assert KEY not in captured.err


@pytest.mark.parametrize("extra", [
    ["--api-key", KEY], ["--delete-eml"], ["--output", "private-output.json"],
    ["--max-context-segments", "PRIVATE_BAD_INT"], ["--prov", "typesafe"],
])
def test_cli_invalid_arguments_do_not_echo_private_values(extra, capsys):
    result = cli.main(["analyze", "private-source.eml", "--provider", "typesafe", "--dry-run", *extra])
    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert json.loads(captured.err)["error_code"] == "invalid_analysis_arguments"
    for value in (KEY, "private-source.eml", "private-output.json", "PRIVATE_BAD_INT"):
        assert value not in captured.err


def test_cli_provider_is_required(capsys):
    assert cli.main(["analyze", "unread.eml", "--dry-run"]) == 2
    assert json.loads(capsys.readouterr().err)["error_code"] == "invalid_analysis_arguments"


def test_cli_endpoint_env_is_disclosed_but_python_api_is_explicit(tmp_path, monkeypatch, capsys):
    source = email_file(tmp_path)
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://proxy.example.com:8443/gateway")
    assert prepare_eml(source).request.base_url == "https://api.typesafe.ai"
    assert cli.main(["analyze", str(source), "--provider", "typesafe", "--dry-run"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["destination_host"] == "proxy.example.com:8443"
    monkeypatch.setenv("TYPESAFE_BASE_URL", f"https://{KEY}@proxy.example.com")
    assert cli.main(["analyze", str(source), "--provider", "typesafe", "--dry-run"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error_code"] == "invalid_provider_endpoint"
    assert KEY not in captured.err


def test_preparation_errors_are_safe_json_not_tracebacks(tmp_path, capsys):
    missing = tmp_path / "private-missing.eml"
    assert cli.main(["analyze", str(missing), "--provider", "typesafe", "--dry-run"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error_code"] == "source_not_found"
    assert str(missing) not in captured.err
    assert "Traceback" not in captured.err


def test_oversized_normalized_text_is_rejected_without_truncation(tmp_path):
    source = email_file(tmp_path, "x" * 25_000)
    original = source.read_bytes()
    with pytest.raises(AnalysisError, match="^state_byte_limit_exceeded$"):
        prepare_eml(source)
    assert source.read_bytes() == original


def test_bare_path_conversion_remains_backward_compatible(monkeypatch):
    calls = []
    def converted(path, **kwargs):
        calls.append((path, kwargs))
        return SimpleNamespace(success=True)
    monkeypatch.setattr(cli, "core_convert", converted)
    assert cli.main(["existing.eml", "--dry-run"]) == 0
    assert len(calls) == 1
    assert calls[0][1]["options"].dry_run


def test_analyze_help_is_available_without_a_key(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["analyze", "--help"])
    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "--dry-run" in output
    assert "--show-state" in output
    assert "analyze" in cli.build_parser().format_help()
