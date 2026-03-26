from __future__ import annotations

from dead_letter.backend.schemas import JobOptions, JobStatusResponse


def test_job_options_has_report_field() -> None:
    opts = JobOptions(report=True)
    assert opts.report is True


def test_job_options_report_defaults_false() -> None:
    opts = JobOptions()
    assert opts.report is False


def test_job_status_response_has_report_path() -> None:
    fields = JobStatusResponse.model_fields
    assert "report_path" in fields


# ---------------------------------------------------------------------------
# Report-on-failure regression tests
# ---------------------------------------------------------------------------

import asyncio
import json
from pathlib import Path

import pytest

from dead_letter.backend.jobs import JobManager
from dead_letter.backend.schemas import JobCreateRequest
from dead_letter.core.types import BundleResult


def _report_manager(tmp_path: Path, *, worker_count: int = 1) -> JobManager:
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    inbox.mkdir(parents=True, exist_ok=True)
    cabinet.mkdir(parents=True, exist_ok=True)
    return JobManager(
        worker_count=worker_count,
        max_retained_terminal_jobs=2000,
        inbox_root=inbox,
        cabinet_root=cabinet,
    )


@pytest.mark.anyio
async def test_report_written_on_happy_path(tmp_path: Path, monkeypatch) -> None:
    """When report=True and all files succeed, report_path should be populated."""
    source = tmp_path / "Inbox" / "ok.eml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("x", encoding="utf-8")

    def ok_bundle(
        path: str | Path,
        *,
        bundle_root: str | Path,
        options: object | None = None,
        source_handling: str = "move",
    ) -> tuple[BundleResult, dict[str, object] | None]:
        src = Path(path)
        bundle = Path(bundle_root) / src.stem
        bundle.mkdir(parents=True, exist_ok=True)
        markdown = bundle / "message.md"
        markdown.write_text("ok", encoding="utf-8")
        return (
            BundleResult(
                source=src,
                bundle=bundle,
                markdown=markdown,
                source_artifact=bundle / src.name,
                attachments=[],
                success=True,
                error=None,
                dry_run=False,
            ),
            {"state": "normal", "selected_body": "plain", "segmentation_path": "plain_fallback",
             "client_hint": "generic", "confidence": "high", "fallback_used": None, "warnings": []},
        )

    import dead_letter.backend.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "run_bundle_conversion", ok_bundle)

    manager = _report_manager(tmp_path)
    created = await manager.create_job(
        JobCreateRequest(mode="file", input_path=str(source), options=JobOptions(report=True))
    )
    terminal = await manager.wait_for_terminal(created.id, timeout=2.0)

    assert terminal.status == "succeeded"
    assert terminal.report_path is not None
    report_file = Path(terminal.report_path)
    assert report_file.exists()
    assert report_file.name == f".dead-letter-report-{terminal.id}.json"


@pytest.mark.anyio
async def test_report_written_when_worker_raises_exception(tmp_path: Path, monkeypatch) -> None:
    """Regression: report must still be written even when the TaskGroup except branch runs."""
    source = tmp_path / "Inbox" / "crash.eml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("x", encoding="utf-8")

    def crashing_bundle(
        path: str | Path,
        *,
        bundle_root: str | Path,
        options: object | None = None,
        source_handling: str = "move",
    ) -> tuple[BundleResult, dict[str, object] | None]:
        raise RuntimeError("unexpected worker crash")

    import dead_letter.backend.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "run_bundle_conversion", crashing_bundle)

    manager = _report_manager(tmp_path)
    created = await manager.create_job(
        JobCreateRequest(mode="file", input_path=str(source), options=JobOptions(report=True))
    )
    terminal = await manager.wait_for_terminal(created.id, timeout=2.0)

    assert terminal.status == "failed"
    # The key assertion: report_path must still be populated because the
    # report-writing block runs after the try/except/else, not inside else.
    assert terminal.report_path is not None
    report_file = Path(terminal.report_path)
    assert report_file.exists()
    assert report_file.name == f".dead-letter-report-{terminal.id}.json"


@pytest.mark.anyio
async def test_dry_run_report_counts_skipped_not_written(tmp_path: Path) -> None:
    source = tmp_path / "Inbox" / "dry.eml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("From: a@b\nSubject: hi\n\nHello\n", encoding="utf-8")

    manager = _report_manager(tmp_path)
    created = await manager.create_job(
        JobCreateRequest(
            mode="file",
            input_path=str(source),
            options=JobOptions(report=True, dry_run=True),
        )
    )
    terminal = await manager.wait_for_terminal(created.id, timeout=2.0)

    assert terminal.status == "succeeded"
    assert terminal.summary.written == 0
    assert terminal.summary.skipped == 1
    report = json.loads(Path(terminal.report_path).read_text(encoding="utf-8"))
    assert report["summary"]["written"] == 0
    assert report["summary"]["skipped"] == 1
    assert report["results"][0]["output"] is None


@pytest.mark.anyio
async def test_each_backend_job_gets_an_immutable_report_file(tmp_path: Path, monkeypatch) -> None:
    first = tmp_path / "Inbox" / "one.eml"
    second = tmp_path / "Inbox" / "two.eml"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("x", encoding="utf-8")
    second.write_text("x", encoding="utf-8")

    def ok_bundle(
        path: str | Path,
        *,
        bundle_root: str | Path,
        options: object | None = None,
        source_handling: str = "move",
    ) -> tuple[BundleResult, dict[str, object] | None]:
        _ = options, source_handling
        src = Path(path)
        bundle = Path(bundle_root) / src.stem
        bundle.mkdir(parents=True, exist_ok=True)
        markdown = bundle / "message.md"
        markdown.write_text("ok", encoding="utf-8")
        return (
            BundleResult(
                source=src,
                bundle=bundle,
                markdown=markdown,
                source_artifact=bundle / src.name,
                attachments=[],
                success=True,
                error=None,
                dry_run=False,
            ),
            None,
        )

    import dead_letter.backend.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "run_bundle_conversion", ok_bundle)

    manager = _report_manager(tmp_path)
    created_one = await manager.create_job(
        JobCreateRequest(mode="file", input_path=str(first), options=JobOptions(report=True))
    )
    terminal_one = await manager.wait_for_terminal(created_one.id, timeout=2.0)
    created_two = await manager.create_job(
        JobCreateRequest(mode="file", input_path=str(second), options=JobOptions(report=True))
    )
    terminal_two = await manager.wait_for_terminal(created_two.id, timeout=2.0)

    assert terminal_one.report_path != terminal_two.report_path
    report_one = json.loads(Path(terminal_one.report_path).read_text(encoding="utf-8"))
    report_two = json.loads(Path(terminal_two.report_path).read_text(encoding="utf-8"))
    assert report_one["job"]["id"] == terminal_one.id
    assert report_two["job"]["id"] == terminal_two.id


@pytest.mark.anyio
async def test_directory_report_uses_source_relative_paths(tmp_path: Path, monkeypatch) -> None:
    inbox = tmp_path / "Inbox"
    (inbox / "a").mkdir(parents=True, exist_ok=True)
    (inbox / "b").mkdir(parents=True, exist_ok=True)
    first = inbox / "a" / "same.eml"
    second = inbox / "b" / "same.eml"
    first.write_text("x", encoding="utf-8")
    second.write_text("x", encoding="utf-8")

    def ok_bundle(
        path: str | Path,
        *,
        bundle_root: str | Path,
        options: object | None = None,
        source_handling: str = "move",
    ) -> tuple[BundleResult, dict[str, object] | None]:
        _ = options, source_handling
        src = Path(path)
        bundle = Path(bundle_root) / f"{src.parent.name}-{src.stem}"
        bundle.mkdir(parents=True, exist_ok=True)
        markdown = bundle / "message.md"
        markdown.write_text("ok", encoding="utf-8")
        return (
            BundleResult(
                source=src,
                bundle=bundle,
                markdown=markdown,
                source_artifact=bundle / src.name,
                attachments=[],
                success=True,
                error=None,
                dry_run=False,
            ),
            None,
        )

    import dead_letter.backend.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "run_bundle_conversion", ok_bundle)

    manager = _report_manager(tmp_path)
    created = await manager.create_job(
        JobCreateRequest(mode="directory", input_path=str(inbox), options=JobOptions(report=True))
    )
    terminal = await manager.wait_for_terminal(created.id, timeout=2.0)

    assert terminal.status == "succeeded"
    report = json.loads(Path(terminal.report_path).read_text(encoding="utf-8"))
    assert [item["source"] for item in report["results"]] == ["a/same.eml", "b/same.eml"]


@pytest.mark.anyio
async def test_directory_report_uses_same_source_format_for_backend_exceptions(tmp_path: Path, monkeypatch) -> None:
    inbox = tmp_path / "Inbox"
    (inbox / "nested").mkdir(parents=True, exist_ok=True)
    source = inbox / "nested" / "boom.eml"
    source.write_text("x", encoding="utf-8")

    def crashing_bundle(
        path: str | Path,
        *,
        bundle_root: str | Path,
        options: object | None = None,
        source_handling: str = "move",
    ) -> tuple[BundleResult, dict[str, object] | None]:
        _ = path, bundle_root, options, source_handling
        raise RuntimeError("unexpected worker crash")

    import dead_letter.backend.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "run_bundle_conversion", crashing_bundle)

    manager = _report_manager(tmp_path)
    created = await manager.create_job(
        JobCreateRequest(mode="directory", input_path=str(inbox), options=JobOptions(report=True))
    )
    terminal = await manager.wait_for_terminal(created.id, timeout=2.0)

    assert terminal.status == "completed_with_errors"
    report = json.loads(Path(terminal.report_path).read_text(encoding="utf-8"))
    assert report["results"][0]["source"] == "nested/boom.eml"


@pytest.mark.anyio
async def test_zero_file_directory_job_still_writes_report(tmp_path: Path) -> None:
    inbox = tmp_path / "Inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    manager = _report_manager(tmp_path)
    created = await manager.create_job(
        JobCreateRequest(mode="directory", input_path=str(inbox), options=JobOptions(report=True))
    )
    terminal = await manager.wait_for_terminal(created.id, timeout=2.0)

    assert terminal.status == "succeeded"
    assert terminal.report_path is not None
    report = json.loads(Path(terminal.report_path).read_text(encoding="utf-8"))
    assert report["summary"] == {"total": 0, "written": 0, "skipped": 0, "errors": 0}
    assert report["results"] == []
