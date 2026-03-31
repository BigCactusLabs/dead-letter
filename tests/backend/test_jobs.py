from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from dead_letter.backend.jobs import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    JobManager,
    bundle_result_to_error,
)
from dead_letter.backend.schemas import JobCreateRequest
from dead_letter.core.types import BundleResult


def _noop_bundle(tmp_path: Path):
    def fake(path: str | Path, **_kwargs: object) -> BundleResult:
        src = Path(path)
        bundle = tmp_path / "Cabinet" / src.stem
        bundle.mkdir(parents=True, exist_ok=True)
        md = bundle / "message.md"
        md.write_text("ok", encoding="utf-8")
        artifact = bundle / src.name
        artifact.write_text("x", encoding="utf-8")
        if src.exists():
            src.unlink()
        return BundleResult(
            source=src, bundle=bundle, markdown=md, source_artifact=artifact,
            attachments=[], success=True, error=None, dry_run=False,
        )
    return fake


def _manager(tmp_path: Path, *, worker_count: int = 1, max_retained_terminal_jobs: int = 2000) -> JobManager:
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    inbox.mkdir(parents=True, exist_ok=True)
    cabinet.mkdir(parents=True, exist_ok=True)
    return JobManager(
        worker_count=worker_count,
        max_retained_terminal_jobs=max_retained_terminal_jobs,
        inbox_root=inbox,
        cabinet_root=cabinet,
    )


@pytest.mark.anyio
async def test_job_manager_runs_directory_job_and_updates_summary(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "in"
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.eml").write_text("x", encoding="utf-8")
    (root / "b.eml").write_text("x", encoding="utf-8")

    def fake_bundle(path: str | Path, **_kwargs: object) -> BundleResult:
        src = Path(path)
        if src.name == "b.eml":
            return BundleResult(
                source=src,
                bundle=None,
                markdown=None,
                source_artifact=None,
                attachments=[],
                success=False,
                error="bad email",
                dry_run=False,
            )
        bundle = tmp_path / "Cabinet" / src.stem
        bundle.mkdir(parents=True, exist_ok=True)
        markdown = bundle / "message.md"
        markdown.write_text("ok", encoding="utf-8")
        artifact = bundle / src.name
        artifact.write_text("x", encoding="utf-8")
        if src.exists():
            src.unlink()
        return BundleResult(
            source=src,
            bundle=bundle,
            markdown=markdown,
            source_artifact=artifact,
            attachments=[],
            success=True,
            error=None,
            dry_run=False,
        )

    import dead_letter.backend.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "core_convert_to_bundle", fake_bundle)

    manager = _manager(tmp_path)
    job = await manager.create_job(JobCreateRequest(mode="directory", input_path=str(root)))

    terminal = await manager.wait_for_terminal(job.id, timeout=2.0)

    assert terminal.status == "completed_with_errors"
    assert terminal.summary.written == 1
    assert terminal.summary.errors == 1
    assert len(terminal.errors) == 1
    assert terminal.diagnostics is None


@pytest.mark.anyio
async def test_job_manager_directory_job_succeeds_for_same_stem_files(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "in"
    (root / "a").mkdir(parents=True, exist_ok=True)
    (root / "b").mkdir(parents=True, exist_ok=True)
    (root / "a" / "same.eml").write_text("From: a@b\nSubject: hi\n\nAlpha\n", encoding="utf-8")
    (root / "b" / "same.eml").write_text("From: c@d\nSubject: hi\n\nBeta\n", encoding="utf-8")

    import dead_letter.core._pipeline as pipeline

    barrier = threading.Barrier(2)
    original = pipeline._collision_safe_bundle_dir

    def synchronized_bundle_dir(target: Path) -> Path:
        barrier.wait(timeout=1)
        return original(target)

    monkeypatch.setattr(pipeline, "_collision_safe_bundle_dir", synchronized_bundle_dir)

    manager = _manager(tmp_path, worker_count=2)
    created = await manager.create_job(JobCreateRequest(mode="directory", input_path=str(root)))

    terminal = await manager.wait_for_terminal(created.id, timeout=2.0)

    assert terminal.status == "succeeded"
    assert terminal.summary.written == 2
    assert terminal.summary.errors == 0
    assert terminal.errors == []
    assert sorted(path.name for path in (tmp_path / "Cabinet").iterdir()) == ["same", "same-2"]


@pytest.mark.anyio
async def test_job_manager_file_mode_projects_diagnostics_summary(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "mail.eml"
    source.write_text("x", encoding="utf-8")

    def fake_bundle(
        path: str | Path,
        *,
        bundle_root: str | Path,
        options: object | None = None,
        source_handling: str = "move",
    ) -> tuple[BundleResult, dict[str, object]]:
        _ = (options, source_handling)
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
            {
                "state": "review_recommended",
                "selected_body": "html",
                "segmentation_path": "plain_fallback",
                "client_hint": "outlook",
                "confidence": "low",
                "fallback_used": "plain_text_reply_parser",
                "warnings": [
                    {
                        "code": "html_segmentation_failed",
                        "message": "HTML segmentation fell back to plain text",
                        "severity": "warning",
                    }
                ],
            },
        )

    import dead_letter.backend.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "run_bundle_conversion", fake_bundle)

    manager = _manager(tmp_path, worker_count=1)
    created = await manager.create_job(JobCreateRequest(mode="file", input_path=str(source)))

    terminal = await manager.wait_for_terminal(created.id, timeout=2.0)

    assert terminal.status == "succeeded"
    assert terminal.diagnostics is not None
    assert terminal.diagnostics.state == "review_recommended"
    assert terminal.diagnostics.selected_body == "html"
    assert terminal.diagnostics.segmentation_path == "plain_fallback"
    assert terminal.diagnostics.client_hint == "outlook"
    assert terminal.diagnostics.confidence == "low"
    assert terminal.diagnostics.fallback_used == "plain_text_reply_parser"
    assert terminal.diagnostics.warnings[0].code == "html_segmentation_failed"


@pytest.mark.anyio
async def test_job_manager_cancel_marks_running_job_cancelled(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "in"
    root.mkdir(parents=True, exist_ok=True)
    for idx in range(4):
        (root / f"{idx}.eml").write_text("x", encoding="utf-8")

    def slow_bundle(path: str | Path, **_kwargs: object) -> BundleResult:
        time.sleep(0.05)
        src = Path(path)
        bundle = tmp_path / "Cabinet" / src.stem
        bundle.mkdir(parents=True, exist_ok=True)
        markdown = bundle / "message.md"
        markdown.write_text("ok", encoding="utf-8")
        return BundleResult(
            source=src,
            bundle=bundle,
            markdown=markdown,
            source_artifact=bundle / src.name,
            attachments=[],
            success=True,
            error=None,
            dry_run=False,
        )

    import dead_letter.backend.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "core_convert_to_bundle", slow_bundle)

    manager = _manager(tmp_path)
    created = await manager.create_job(JobCreateRequest(mode="directory", input_path=str(root)))

    await asyncio.sleep(0.02)
    cancel = await manager.cancel_job(created.id)
    assert cancel.accepted is True

    snapshot = await manager.get_job(created.id)
    assert snapshot is not None
    assert snapshot.cancel_requested is True

    terminal = await manager.wait_for_terminal(created.id, timeout=3.0)
    assert terminal.status == "cancelled"
    assert terminal.cancel_requested is True


@pytest.mark.anyio
async def test_job_manager_file_mode_conversion_error_is_failed_not_cancelled(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "mail.eml"
    source.write_text("x", encoding="utf-8")

    def failing_bundle(path: str | Path, **_kwargs: object) -> BundleResult:
        src = Path(path)
        return BundleResult(
            source=src,
            bundle=None,
            markdown=None,
            source_artifact=None,
            attachments=[],
            success=False,
            error="conversion failed",
            dry_run=False,
        )

    import dead_letter.backend.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "core_convert_to_bundle", failing_bundle)

    manager = _manager(tmp_path, worker_count=4)
    created = await manager.create_job(JobCreateRequest(mode="file", input_path=str(source)))

    terminal = await manager.wait_for_terminal(created.id, timeout=2.0)
    assert terminal.status == "failed"
    assert terminal.cancel_requested is False
    assert terminal.progress.failed == 1
    assert terminal.summary.errors == 1
    assert len(terminal.errors) == 1
    assert terminal.errors[0].code == "conversion_error"


@pytest.mark.anyio
async def test_job_manager_prunes_old_terminal_jobs(tmp_path: Path, monkeypatch) -> None:
    source_one = tmp_path / "one.eml"
    source_two = tmp_path / "two.eml"
    source_one.write_text("x", encoding="utf-8")
    source_two.write_text("x", encoding="utf-8")

    def ok_bundle(path: str | Path, **_kwargs: object) -> BundleResult:
        src = Path(path)
        bundle = tmp_path / "Cabinet" / src.stem
        bundle.mkdir(parents=True, exist_ok=True)
        markdown = bundle / "message.md"
        markdown.write_text("ok", encoding="utf-8")
        return BundleResult(
            source=src,
            bundle=bundle,
            markdown=markdown,
            source_artifact=bundle / src.name,
            attachments=[],
            success=True,
            error=None,
            dry_run=False,
        )

    import dead_letter.backend.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "core_convert_to_bundle", ok_bundle)

    manager = _manager(tmp_path, max_retained_terminal_jobs=1)

    first = await manager.create_job(JobCreateRequest(mode="file", input_path=str(source_one)))
    await manager.wait_for_terminal(first.id, timeout=2.0)
    assert await manager.get_job(first.id) is not None

    second = await manager.create_job(JobCreateRequest(mode="file", input_path=str(source_two)))
    await manager.wait_for_terminal(second.id, timeout=2.0)

    assert await manager.get_job(first.id) is None
    latest = await manager.get_job(second.id)
    assert latest is not None
    assert latest.status == "succeeded"


def test_status_transition_tables_are_well_formed() -> None:
    assert "queued" in ALLOWED_TRANSITIONS
    assert "running" in ALLOWED_TRANSITIONS
    assert TERMINAL_STATUSES == {"succeeded", "completed_with_errors", "failed", "cancelled"}


def test_bundle_result_to_error_mapping() -> None:
    result = BundleResult(
        source=Path("/tmp/fail.eml"),
        bundle=None,
        markdown=None,
        source_artifact=None,
        attachments=[],
        success=False,
        error="broken",
        error_code=None,
        plain_text_fallback_available=None,
        dry_run=False,
    )

    error = bundle_result_to_error(result)

    assert error.code == "conversion_error"
    assert error.stage == "core"
    assert error.path == str(Path("/tmp/fail.eml").resolve())
    assert "broken" in error.message


@pytest.mark.anyio
async def test_job_manager_file_mode_exposes_retry_action_for_recoverable_html_failure(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "mail.eml"
    source.write_text("x", encoding="utf-8")

    def failing_bundle(path: str | Path, **_kwargs: object) -> BundleResult:
        src = Path(path)
        return BundleResult(
            source=src,
            bundle=None,
            markdown=None,
            source_artifact=None,
            attachments=[],
            success=False,
            error="html-to-markdown panic during conversion: boom",
            error_code="html_markdown_failed",
            plain_text_fallback_available=True,
            html_repair_available=True,
            dry_run=False,
        )

    import dead_letter.backend.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "core_convert_to_bundle", failing_bundle)

    manager = _manager(tmp_path, worker_count=1)
    created = await manager.create_job(JobCreateRequest(mode="file", input_path=str(source)))

    terminal = await manager.wait_for_terminal(created.id, timeout=2.0)

    assert terminal.status == "failed"
    assert terminal.recovery_actions == [
        {
            "kind": "retry_with_html_repair",
            "label": "Retry with HTML repair",
            "message": "HTML conversion failed in strict mode. Retry this file with the targeted HTML repair enabled.",
        }
    ]


@pytest.mark.anyio
async def test_job_manager_retry_job_steps_from_html_repair_to_plain_text_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "mail.eml"
    source.write_text("x", encoding="utf-8")

    def retryable_bundle(
        path: str | Path,
        *,
        options: ConvertOptions,
        bundle_root: str | Path,
        **_kwargs: object,
    ) -> tuple[BundleResult, dict[str, object] | None]:
        src = Path(path)
        if not options.allow_html_repair_on_panic and not options.allow_fallback_on_html_error:
            return (
                BundleResult(
                    source=src,
                    bundle=None,
                    markdown=None,
                    source_artifact=None,
                    attachments=[],
                    success=False,
                    error="html-to-markdown panic during conversion: boom",
                    dry_run=False,
                    error_code="html_markdown_failed",
                    plain_text_fallback_available=True,
                    html_repair_available=True,
                ),
                None,
            )

        if options.allow_html_repair_on_panic and not options.allow_fallback_on_html_error:
            return (
                BundleResult(
                    source=src,
                    bundle=None,
                    markdown=None,
                    source_artifact=None,
                    attachments=[],
                    success=False,
                    error="html-to-markdown panic during conversion: still broken after repair",
                    dry_run=False,
                    error_code="html_markdown_failed",
                    plain_text_fallback_available=True,
                    html_repair_available=False,
                ),
                None,
            )

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
            {
                "state": "degraded",
                "selected_body": "html",
                "segmentation_path": "plain_fallback",
                "client_hint": "generic",
                "confidence": "low",
                "fallback_used": "html_failure_plain_text_fallback",
                "warnings": [
                    {
                        "code": "html_markdown_failed",
                        "message": "html-to-markdown panic during conversion: boom",
                        "severity": "warning",
                    }
                ],
            },
        )

    import dead_letter.backend.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "run_bundle_conversion", retryable_bundle)

    manager = _manager(tmp_path, worker_count=1)
    created = await manager.create_job(JobCreateRequest(mode="file", input_path=str(source)))
    failed = await manager.wait_for_terminal(created.id, timeout=2.0)

    repaired = await manager.retry_job(created.id, "retry_with_html_repair")
    repaired_failed = await manager.wait_for_terminal(repaired.id, timeout=2.0)
    fallback = await manager.retry_job(repaired.id, "retry_with_html_fallback")
    terminal = await manager.wait_for_terminal(fallback.id, timeout=2.0)

    assert failed.status == "failed"
    assert failed.recovery_actions == [
        {
            "kind": "retry_with_html_repair",
            "label": "Retry with HTML repair",
            "message": "HTML conversion failed in strict mode. Retry this file with the targeted HTML repair enabled.",
        }
    ]
    assert repaired_failed.status == "failed"
    assert repaired_failed.recovery_actions == [
        {
            "kind": "retry_with_html_fallback",
            "label": "Retry with plain-text fallback",
            "message": "HTML conversion failed after repair. Retry this file with plain-text fallback enabled.",
        }
    ]
    assert terminal.status == "succeeded"
    assert terminal.diagnostics is not None
    assert terminal.diagnostics.fallback_used == "html_failure_plain_text_fallback"


@pytest.mark.anyio
async def test_job_manager_directory_mode_is_case_insensitive_and_skips_escaping_symlinks(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "in"
    root.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)

    lower = root / "a.eml"
    upper = root / "B.EML"
    lower.write_text("x", encoding="utf-8")
    upper.write_text("x", encoding="utf-8")
    outside_target = outside / "secret.eml"
    outside_target.write_text("x", encoding="utf-8")
    (root / "link.eml").symlink_to(outside_target)

    converted: list[str] = []

    def fake_bundle(path: str | Path, **_kwargs: object) -> BundleResult:
        src = Path(path)
        converted.append(src.name)
        bundle = tmp_path / "Cabinet" / src.stem
        bundle.mkdir(parents=True, exist_ok=True)
        markdown = bundle / "message.md"
        markdown.write_text("ok", encoding="utf-8")
        return BundleResult(
            source=src,
            bundle=bundle,
            markdown=markdown,
            source_artifact=bundle / src.name,
            attachments=[],
            success=True,
            error=None,
            dry_run=False,
        )

    import dead_letter.backend.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "core_convert_to_bundle", fake_bundle)

    manager = _manager(tmp_path)
    created = await manager.create_job(JobCreateRequest(mode="directory", input_path=str(root)))

    terminal = await manager.wait_for_terminal(created.id, timeout=2.0)

    assert terminal.status == "succeeded"
    assert sorted(converted) == ["B.EML", "a.eml"]


@pytest.mark.anyio
async def test_create_job_stores_origin(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "Inbox" / "test.eml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("placeholder", encoding="utf-8")

    import dead_letter.backend.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "core_convert_to_bundle", _noop_bundle(tmp_path))

    manager = _manager(tmp_path)

    job_default = await manager.create_job(
        JobCreateRequest(mode="file", input_path=str(source))
    )
    snap_default = await manager.wait_for_terminal(job_default.id, timeout=2.0)
    assert snap_default.origin == "manual"

    source2 = tmp_path / "Inbox" / "test2.eml"
    source2.write_text("placeholder", encoding="utf-8")
    job_watch = await manager.create_job(
        JobCreateRequest(mode="file", input_path=str(source2)),
        origin="watch",
    )
    snap_watch = await manager.wait_for_terminal(job_watch.id, timeout=2.0)
    assert snap_watch.origin == "watch"


@pytest.mark.anyio
async def test_retry_job_preserves_origin(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "Inbox" / "retry.eml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("placeholder", encoding="utf-8")

    def retryable_bundle(
        path: str | Path,
        *,
        options: object | None = None,
        bundle_root: str | Path,
        source_handling: str = "move",
    ) -> tuple[BundleResult, dict[str, object] | None]:
        _ = (bundle_root, source_handling)
        src = Path(path)
        if not getattr(options, "allow_html_repair_on_panic", False):
            return (
                BundleResult(
                    source=src,
                    bundle=None,
                    markdown=None,
                    source_artifact=None,
                    attachments=[],
                    success=False,
                    error="html-to-markdown panic during conversion: boom",
                    error_code="html_markdown_failed",
                    plain_text_fallback_available=True,
                    html_repair_available=True,
                    dry_run=False,
                ),
                None,
            )

        bundle = tmp_path / "Cabinet" / src.stem
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

    monkeypatch.setattr(jobs_mod, "run_bundle_conversion", retryable_bundle)

    manager = _manager(tmp_path, worker_count=1)
    created = await manager.create_job(
        JobCreateRequest(mode="file", input_path=str(source)),
        origin="watch",
    )
    failed = await manager.wait_for_terminal(created.id, timeout=2.0)

    retried = await manager.retry_job(created.id, "retry_with_html_repair")
    retried_terminal = await manager.wait_for_terminal(retried.id, timeout=2.0)

    assert failed.origin == "watch"
    assert retried_terminal.origin == "watch"


@pytest.mark.anyio
async def test_list_terminal_jobs_returns_finished_jobs_sorted(tmp_path: Path, monkeypatch) -> None:
    import dead_letter.backend.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "core_convert_to_bundle", _noop_bundle(tmp_path))

    manager = _manager(tmp_path)

    ids = []
    for i in range(3):
        source = tmp_path / "Inbox" / f"mail{i}.eml"
        source.write_text("placeholder", encoding="utf-8")
        job = await manager.create_job(
            JobCreateRequest(mode="file", input_path=str(source)),
            origin="watch" if i == 1 else "manual",
        )
        await manager.wait_for_terminal(job.id, timeout=2.0)
        ids.append(job.id)

    jobs, totals = await manager.list_terminal_jobs(limit=50)

    assert len(jobs) == 3
    assert jobs[0].id == ids[2]
    assert jobs[1].id == ids[1]
    assert jobs[2].id == ids[0]
    assert jobs[1].origin == "watch"
    assert jobs[0].origin == "manual"
    assert totals.jobs_completed == 3
    assert totals.total_written == 3
    assert totals.total_skipped == 0
    assert totals.total_errors == 0


@pytest.mark.anyio
async def test_list_terminal_jobs_respects_limit(tmp_path: Path, monkeypatch) -> None:
    import dead_letter.backend.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "core_convert_to_bundle", _noop_bundle(tmp_path))

    manager = _manager(tmp_path)
    for i in range(5):
        source = tmp_path / "Inbox" / f"mail{i}.eml"
        source.write_text("placeholder", encoding="utf-8")
        job = await manager.create_job(
            JobCreateRequest(mode="file", input_path=str(source))
        )
        await manager.wait_for_terminal(job.id, timeout=2.0)

    jobs, totals = await manager.list_terminal_jobs(limit=2)

    assert len(jobs) == 2
    assert totals.jobs_completed == 5
    assert totals.total_written == 5


@pytest.mark.anyio
async def test_list_terminal_jobs_excludes_running(tmp_path: Path, monkeypatch) -> None:
    """A running job should not appear in history."""
    import dead_letter.backend.jobs as jobs_mod

    def slow_bundle(path: str | Path, **_kwargs: object) -> BundleResult:
        import time
        time.sleep(5)
        return BundleResult(
            source=Path(path), bundle=None, markdown=None, source_artifact=None,
            attachments=[], success=True, error=None, dry_run=False,
        )

    monkeypatch.setattr(jobs_mod, "core_convert_to_bundle", slow_bundle)

    manager = _manager(tmp_path)
    source = tmp_path / "Inbox" / "slow.eml"
    source.write_text("placeholder", encoding="utf-8")
    job = await manager.create_job(
        JobCreateRequest(mode="file", input_path=str(source))
    )
    await asyncio.sleep(0.05)

    jobs, totals = await manager.list_terminal_jobs()
    assert len(jobs) == 0
    assert totals.jobs_completed == 0

    await manager.cancel_job(job.id)
