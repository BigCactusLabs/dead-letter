from __future__ import annotations

from pathlib import Path

import pytest

from dead_letter.backend.jobs import JobManager
from dead_letter.backend.schemas import JobCreateRequest, JobOptions
from dead_letter.core.types import BundleResult


def _make_success_result(source: Path, bundle: Path) -> BundleResult:
    return BundleResult(
        source=source,
        bundle=bundle,
        markdown=None,
        source_artifact=None,
        attachments=[],
        success=True,
        error=None,
        dry_run=False,
        error_code=None,
        html_repair_available=False,
        plain_text_fallback_available=False,
    )


@pytest.mark.anyio
async def test_batch_dir_removed_after_job_succeeds_with_delete_eml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    cabinet.mkdir()
    batch_dir = inbox / "_batch-test123"
    batch_dir.mkdir(parents=True)
    eml = batch_dir / "a.eml"
    eml.write_bytes(b"From: a@b\n\nHello\n")

    def fake_convert(path, *, bundle_root, options, source_handling):
        _ = (bundle_root, options)
        result = _make_success_result(Path(path), cabinet / "a")
        if source_handling == "delete":
            Path(path).unlink(missing_ok=True)
        return result, None

    import dead_letter.backend.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "run_bundle_conversion", fake_convert)

    manager = JobManager(worker_count=1, inbox_root=inbox, cabinet_root=cabinet)
    job = await manager.create_job(
        JobCreateRequest(
            mode="directory",
            input_path=str(batch_dir),
            options=JobOptions(delete_eml=True),
        ),
        origin="import",
    )

    final = await manager.wait_for_terminal(job.id, timeout=5.0)
    assert final.status == "succeeded"
    assert not batch_dir.exists()


@pytest.mark.anyio
async def test_batch_dir_preserved_when_not_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    cabinet.mkdir()
    batch_dir = inbox / "_batch-test456"
    batch_dir.mkdir(parents=True)
    eml = batch_dir / "a.eml"
    eml.write_bytes(b"From: a@b\n\nHello\n")

    def fake_convert(path, *, bundle_root, options, source_handling):
        _ = (path, bundle_root, options, source_handling)
        return _make_success_result(eml, cabinet / "a"), None

    import dead_letter.backend.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "run_bundle_conversion", fake_convert)

    manager = JobManager(worker_count=1, inbox_root=inbox, cabinet_root=cabinet)
    job = await manager.create_job(
        JobCreateRequest(
            mode="directory",
            input_path=str(batch_dir),
            options=JobOptions(delete_eml=False),
        ),
        origin="import",
    )

    final = await manager.wait_for_terminal(job.id, timeout=5.0)
    assert final.status == "succeeded"
    assert batch_dir.exists()
    assert eml.exists()


@pytest.mark.anyio
async def test_non_batch_dir_not_cleaned_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    cabinet.mkdir()
    normal_dir = inbox / "regular"
    normal_dir.mkdir(parents=True)
    eml = normal_dir / "a.eml"
    eml.write_bytes(b"From: a@b\n\nHello\n")

    def fake_convert(path, *, bundle_root, options, source_handling):
        _ = (bundle_root, options)
        result = _make_success_result(Path(path), cabinet / "a")
        if source_handling == "delete":
            Path(path).unlink(missing_ok=True)
        return result, None

    import dead_letter.backend.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "run_bundle_conversion", fake_convert)

    manager = JobManager(worker_count=1, inbox_root=inbox, cabinet_root=cabinet)
    job = await manager.create_job(
        JobCreateRequest(
            mode="directory",
            input_path=str(normal_dir),
            options=JobOptions(delete_eml=True),
        ),
        origin="import",
    )

    final = await manager.wait_for_terminal(job.id, timeout=5.0)
    assert final.status == "succeeded"
    assert normal_dir.exists()
