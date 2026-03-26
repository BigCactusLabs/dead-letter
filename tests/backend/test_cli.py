from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from dead_letter.backend import cli
from dead_letter.core.types import ConvertOptions, ConvertResult


EXPECTED_FLAGS = {
    "output",
    "strip_signatures",
    "strip_disclaimers",
    "strip_quoted_headers",
    "strip_signature_images",
    "strip_tracking_pixels",
    "embed_inline_images",
    "include_all_headers",
    "include_raw_html",
    "no_calendar_summary",
    "allow_fallback_on_html_error",
    "allow_html_repair_on_panic",
    "delete_eml",
    "dry_run",
    "report",
}


def _ok_result(path: Path, *, dry_run: bool = False) -> ConvertResult:
    return ConvertResult(
        source=path,
        output=None,
        subject="Subject",
        sender="sender@example.com",
        date=None,
        attachments=[],
        success=True,
        error=None,
        dry_run=dry_run,
    )


def test_parser_contains_contract_flags() -> None:
    parser = cli.build_parser()
    convert_parser = None
    for action in parser._subparsers._actions:
        if isinstance(action, argparse._SubParsersAction):
            convert_parser = action.choices["convert"]
            break
    assert convert_parser is not None
    available = {action.dest for action in convert_parser._actions}
    assert EXPECTED_FLAGS.issubset(available)
    assert "input_path" in available


def test_cli_file_mode_calls_core_convert(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "mail.eml"
    source.write_text("placeholder", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_convert(
        path: str | Path,
        *,
        output: str | Path | None = None,
        options: ConvertOptions | None = None,
    ) -> ConvertResult:
        captured["path"] = Path(path)
        captured["output"] = output
        captured["options"] = options
        assert isinstance(options, ConvertOptions)
        return _ok_result(Path(path), dry_run=options.dry_run)

    def fail_convert_dir(*_args: object, **_kwargs: object) -> list[ConvertResult]:
        pytest.fail("convert_dir should not be used for file input")

    monkeypatch.setattr(cli, "core_convert", fake_convert)
    monkeypatch.setattr(cli, "core_convert_dir", fail_convert_dir)

    rc = cli.main(["convert", "--dry-run", "--delete-eml", str(source)])

    assert rc == 0
    assert captured["path"] == source
    assert captured["output"] is None
    options = captured["options"]
    assert isinstance(options, ConvertOptions)
    assert options.dry_run is True
    assert options.delete_eml is False


def test_cli_directory_mode_calls_core_convert_dir(monkeypatch, tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "one.eml").write_text("placeholder", encoding="utf-8")

    captured: dict[str, object] = {}

    def fail_convert(*_args: object, **_kwargs: object) -> ConvertResult:
        pytest.fail("convert should not be used for directory input")

    def fake_convert_dir(
        path: str | Path,
        *,
        output: str | Path | None = None,
        options: ConvertOptions | None = None,
    ) -> list[ConvertResult]:
        captured["path"] = Path(path)
        captured["output"] = output
        captured["options"] = options
        assert isinstance(options, ConvertOptions)
        return [_ok_result(Path(path) / "one.eml")]

    monkeypatch.setattr(cli, "core_convert", fail_convert)
    monkeypatch.setattr(cli, "core_convert_dir", fake_convert_dir)

    rc = cli.main([str(input_dir)])

    assert rc == 0
    assert captured["path"] == input_dir
    assert captured["output"] is None
    assert isinstance(captured["options"], ConvertOptions)


def test_convert_subcommand_calls_core_convert(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "mail.eml"
    source.write_text("placeholder", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_convert(path, *, output=None, options=None):
        captured["path"] = Path(path)
        captured["options"] = options
        return _ok_result(Path(path))

    monkeypatch.setattr(cli, "core_convert", fake_convert)
    rc = cli.main(["convert", str(source)])
    assert rc == 0
    assert captured["path"] == source


def test_bare_path_backward_compat(monkeypatch, tmp_path: Path) -> None:
    """Bare path without 'convert' subcommand still works."""
    source = tmp_path / "mail.eml"
    source.write_text("placeholder", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_convert(path, *, output=None, options=None):
        captured["path"] = Path(path)
        return _ok_result(Path(path))

    monkeypatch.setattr(cli, "core_convert", fake_convert)
    rc = cli.main([str(source)])
    assert rc == 0
    assert captured["path"] == source


def test_doctor_subcommand_exits_zero(monkeypatch) -> None:
    """Doctor subcommand runs without error when deps are available."""
    rc = cli.main(["doctor"])
    assert rc == 0


def test_top_level_help_does_not_route_to_convert(capsys) -> None:
    """--help at top level should show subcommands, not convert help."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "convert" in captured.out
    assert "doctor" in captured.out


def test_convert_report_flag_sets_option(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "mail.eml"
    source.write_text("placeholder", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_convert(path, *, output=None, options=None):
        captured["options"] = options
        return _ok_result(Path(path))

    monkeypatch.setattr(cli, "core_convert", fake_convert)
    cli.main(["convert", "--report", str(source)])
    assert captured["options"].report is True


def test_convert_without_report_defaults_false(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "mail.eml"
    source.write_text("placeholder", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_convert(path, *, output=None, options=None):
        captured["options"] = options
        return _ok_result(Path(path))

    monkeypatch.setattr(cli, "core_convert", fake_convert)
    cli.main(["convert", str(source)])
    assert captured["options"].report is False


def test_cli_report_writes_artifact_for_single_file_output_directory(tmp_path: Path) -> None:
    source = tmp_path / "mail.eml"
    output_dir = tmp_path / "out"
    source.write_text("From: sender@example.com\nSubject: Report Me\n\nHello\n", encoding="utf-8")

    rc = cli.main(["convert", str(source), "--output", str(output_dir), "--report"])

    assert rc == 0
    report_path = output_dir / ".dead-letter-report.json"
    assert report_path.exists()
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["job"]["input_mode"] == "file"
    assert data["summary"]["total"] == 1
    assert data["summary"]["written"] == 1


def test_cli_report_writes_artifact_for_directory_output_root(tmp_path: Path) -> None:
    input_dir = tmp_path / "inbox"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "one.eml").write_text("From: sender@example.com\nSubject: One\n\nHello\n", encoding="utf-8")
    (input_dir / "two.eml").write_text("From: sender@example.com\nSubject: Two\n\nHello\n", encoding="utf-8")

    rc = cli.main(["convert", str(input_dir), "--output", str(output_dir), "--report"])

    assert rc == 0
    report_path = output_dir / ".dead-letter-report.json"
    assert report_path.exists()
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["job"]["input_mode"] == "directory"
    assert data["summary"]["total"] == 2
    assert data["summary"]["written"] == 2
