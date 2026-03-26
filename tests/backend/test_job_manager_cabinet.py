from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from dead_letter.backend.jobs import JobManager
from dead_letter.backend.schemas import JobCreateRequest
from dead_letter.core.types import BundleResult


@pytest.mark.anyio
async def test_job_manager_reports_cabinet_output_location_for_single_file(
    tmp_path: Path, monkeypatch
) -> None:
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    inbox.mkdir()
    cabinet.mkdir()
    source = inbox / "hello.eml"
    source.write_text("x", encoding="utf-8")

    def fake_bundle(
        path: str | Path,
        *,
        bundle_root: str | Path,
        options: object | None = None,
        source_handling: str = "move",
    ) -> BundleResult:
        src = Path(path)
        bundle = Path(bundle_root) / "hello"
        bundle.mkdir(parents=True, exist_ok=True)
        markdown = bundle / "message.md"
        markdown.write_text("ok", encoding="utf-8")
        artifact = bundle / src.name
        artifact.write_text("x", encoding="utf-8")
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

    manager = JobManager(worker_count=1, inbox_root=inbox, cabinet_root=cabinet)
    created = await manager.create_job(JobCreateRequest(mode="file", input_path=str(source)))
    terminal = await manager.wait_for_terminal(created.id, timeout=2.0)

    assert created.output_location.strategy == "cabinet"
    assert created.output_location.cabinet_path == str(cabinet.resolve())
    assert created.output_location.bundle_path == str((cabinet / "hello").resolve())
    assert terminal.output_location.bundle_path == str((cabinet / "hello").resolve())


@pytest.mark.anyio
async def test_job_manager_uses_delete_eml_as_delete_source_override(
    tmp_path: Path, monkeypatch
) -> None:
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    inbox.mkdir()
    cabinet.mkdir()
    source = tmp_path / "outside.eml"
    source.write_text("x", encoding="utf-8")
    captured: list[str] = []

    def fake_bundle(
        path: str | Path,
        *,
        bundle_root: str | Path,
        options: object | None = None,
        source_handling: str = "move",
    ) -> BundleResult:
        captured.append(source_handling)
        src = Path(path)
        bundle = Path(bundle_root) / "outside"
        bundle.mkdir(parents=True, exist_ok=True)
        markdown = bundle / "message.md"
        markdown.write_text("ok", encoding="utf-8")
        if source_handling == "delete" and src.exists():
            src.unlink()
        return BundleResult(
            source=src,
            bundle=bundle,
            markdown=markdown,
            source_artifact=None,
            attachments=[],
            success=True,
            error=None,
            dry_run=False,
        )

    import dead_letter.backend.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "core_convert_to_bundle", fake_bundle)

    manager = JobManager(worker_count=1, inbox_root=inbox, cabinet_root=cabinet)
    created = await manager.create_job(
        JobCreateRequest(mode="file", input_path=str(source), options={"delete_eml": True})
    )
    await manager.wait_for_terminal(created.id, timeout=2.0)

    assert captured == ["delete"]


@pytest.mark.anyio
async def test_job_manager_rejects_sources_inside_cabinet(tmp_path: Path) -> None:
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    inbox.mkdir()
    cabinet.mkdir()
    source = cabinet / "nested.eml"
    source.write_text("x", encoding="utf-8")

    manager = JobManager(worker_count=1, inbox_root=inbox, cabinet_root=cabinet)

    with pytest.raises(ValueError, match="Cabinet"):
        await manager.create_job(JobCreateRequest(mode="file", input_path=str(source)))


@pytest.mark.anyio
async def test_job_manager_directory_job_uses_cabinet_root_and_tracks_errors(
    tmp_path: Path, monkeypatch
) -> None:
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    root = tmp_path / "manual"
    inbox.mkdir()
    cabinet.mkdir()
    root.mkdir()
    (root / "a.eml").write_text("x", encoding="utf-8")
    (root / "b.eml").write_text("x", encoding="utf-8")

    def fake_bundle(
        path: str | Path,
        *,
        bundle_root: str | Path,
        options: object | None = None,
        source_handling: str = "move",
    ) -> BundleResult:
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

        bundle = Path(bundle_root) / src.stem
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

    manager = JobManager(worker_count=1, inbox_root=inbox, cabinet_root=cabinet)
    created = await manager.create_job(JobCreateRequest(mode="directory", input_path=str(root)))
    terminal = await manager.wait_for_terminal(created.id, timeout=2.0)

    assert terminal.status == "completed_with_errors"
    assert terminal.output_location.strategy == "cabinet"
    assert terminal.output_location.cabinet_path == str(cabinet.resolve())
    assert terminal.output_location.bundle_path is None
    assert terminal.summary.written == 1
    assert terminal.summary.errors == 1
    assert len(terminal.errors) == 1


@pytest.mark.anyio
async def test_job_manager_keeps_original_cabinet_root_when_settings_change_mid_job(
    tmp_path: Path, monkeypatch
) -> None:
    inbox = tmp_path / "Inbox"
    cabinet = tmp_path / "Cabinet"
    replacement_cabinet = tmp_path / "Cabinet-2"
    root = tmp_path / "manual"
    inbox.mkdir()
    cabinet.mkdir()
    replacement_cabinet.mkdir()
    root.mkdir()
    (root / "a.eml").write_text("x", encoding="utf-8")
    (root / "b.eml").write_text("x", encoding="utf-8")
    bundle_roots: list[str] = []

    def fake_bundle(
        path: str | Path,
        *,
        bundle_root: str | Path,
        options: object | None = None,
        source_handling: str = "move",
    ) -> BundleResult:
        _ = (options, source_handling)
        bundle_roots.append(str(Path(bundle_root).resolve()))
        time.sleep(0.05)
        src = Path(path)
        bundle = Path(bundle_root) / src.stem
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

    monkeypatch.setattr(jobs_mod, "run_bundle_conversion", fake_bundle)

    manager = JobManager(worker_count=1, inbox_root=inbox, cabinet_root=cabinet)
    created = await manager.create_job(JobCreateRequest(mode="directory", input_path=str(root)))

    await asyncio.sleep(0.01)
    manager.update_roots(inbox_root=inbox, cabinet_root=replacement_cabinet)
    terminal = await manager.wait_for_terminal(created.id, timeout=2.0)

    assert bundle_roots == [str(cabinet.resolve()), str(cabinet.resolve())]
    assert terminal.output_location.cabinet_path == str(cabinet.resolve())
