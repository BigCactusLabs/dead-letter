"""Browser MBOX uploads: bounded staging, pre-body rejection and job hand-off."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import socket
import threading
import time
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import pytest
import uvicorn
from httpx import ASGITransport, AsyncClient
from starlette.datastructures import Headers

from dead_letter.backend import api as api_mod
from dead_letter.backend import mbox_jobs, uploads
from dead_letter.backend.api import create_app
from dead_letter.backend.filesystem import FilesystemBrowser
from dead_letter.backend.jobs import JobManager
from dead_letter.backend.mbox_jobs import MboxBusyError
from dead_letter.backend.schemas import MboxJobCreateRequest
from dead_letter.core.mbox_import import convert_mbox

from .helpers import csrf_headers

POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"
MESSAGE = b"From: alice@example.test\nSubject: Synthetic\n\nHello\n\n"
ARCHIVE = POSTMARK + MESSAGE + POSTMARK + MESSAGE.replace(b"Hello", b"Second")
BASE_URL = "http://127.0.0.1:8765"
_DiskUsage = namedtuple("_DiskUsage", "total used free")
_NO_SPACE_ERRNOS = [errno.ENOSPC, *([errno.EDQUOT] if hasattr(errno, "EDQUOT") else [])]


def _app(root: Path, *, configured: bool = True):
    root.mkdir(parents=True, exist_ok=True)
    manager = JobManager(inbox_root=root / "Inbox", cabinet_root=root / "Cabinet")
    app = create_app(
        browser=FilesystemBrowser(root=root),
        manager=manager,
        settings_path=root / "settings.json",
    )
    if configured:
        app.state.settings.save(inbox_path=root / "Inbox", cabinet_path=root / "Cabinet")
    return app


def _headers(
    app,
    length: int,
    *,
    name: str | None = "All mail.mbox",
    declared: object = None,
    content_type: str | None = "application/octet-stream",
    csrf: bool = True,
) -> dict[str, str]:
    headers = {"host": "127.0.0.1:8765", "content-length": str(length)}
    if csrf:
        headers["x-dead-letter-csrf"] = app.state.csrf_token
    if content_type is not None:
        headers["content-type"] = content_type
    if name is not None:
        headers["x-dead-letter-filename"] = quote(name)
    if declared is not False:
        headers["x-dead-letter-upload-length"] = str(length if declared is None else declared)
    return headers


class _Body:
    """ASGI receive serving body chunks, then ending, disconnecting or stalling."""

    def __init__(self, chunks: list[bytes], *, then: str = "end") -> None:
        self._chunks = list(chunks)
        self._then = then
        self._ended = False
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if self._chunks:
            chunk = self._chunks.pop(0)
            more = bool(self._chunks) or self._then != "end"
            self._ended = not more
            return {"type": "http.request", "body": chunk, "more_body": more}
        if self._then == "end" and not self._ended:
            self._ended = True
            return {"type": "http.request", "body": b"", "more_body": False}
        if self._then == "disconnect":
            return {"type": "http.disconnect"}
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


async def _post(app, headers: dict[str, str], body: _Body, *, query: str = ""):
    messages: list[dict] = []

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/mbox/uploads",
        "raw_path": b"/api/mbox/uploads",
        "query_string": query.encode(),
        "root_path": "",
        "headers": [(key.encode("latin-1"), value.encode("latin-1")) for key, value in headers.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8765),
    }
    await app(scope, body, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    response_headers = {key.decode("latin-1"): value.decode("latin-1") for key, value in start["headers"]}
    return start["status"], response_headers, json.loads(payload)


def _staged(root: Path) -> list[str]:
    staging = root / "Inbox" / uploads.MBOX_UPLOAD_STAGING_DIRNAME
    return sorted(path.name for path in staging.iterdir()) if staging.exists() else []


async def _assert_slot_free(app) -> None:
    manager = app.state.job_manager
    reservation = await manager.reserve_mbox_slot()
    manager.release_mbox_slot(reservation)


@pytest.mark.anyio
async def test_upload_runs_the_mbox_job_and_retains_the_staged_archive(tmp_path: Path) -> None:
    app = _app(tmp_path)
    # Same basename as the upload, since bundles record the source archive name.
    reference = tmp_path / "reference-source" / "All mail Including Spam and Trash.mbox"
    reference.parent.mkdir()
    reference.write_bytes(ARCHIVE)
    expected_root = tmp_path / "reference"
    expected = list(convert_mbox(reference, output=expected_root, bundles=True))
    staging = tmp_path / "Inbox" / "mbox-uploads"

    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
        headers = {
            **await csrf_headers(client),
            "Content-Type": "application/octet-stream",
            "X-Dead-Letter-Filename": quote("All mail Including Spam and Trash.mbox"),
            "X-Dead-Letter-Upload-Length": str(len(ARCHIVE)),
        }
        create = await client.post(
            "/api/mbox/uploads",
            content=ARCHIVE,
            headers=headers,
            params={"options": json.dumps({"thread_mode": "structured", "timeout_seconds": 12.5})},
        )
        assert create.status_code == 202, create.text
        assert "connection" not in create.headers
        created = create.json()
        assert set(created) == {"id", "status", "output_location"}
        terminal = await app.state.job_manager.wait_for_terminal(created["id"], timeout=20)
        snapshot = (await client.get(f"/api/jobs/{created['id']}")).json()

        second = await client.post("/api/mbox/uploads", content=ARCHIVE, headers=headers)
        assert second.status_code == 202, second.text
        await app.state.job_manager.wait_for_terminal(second.json()["id"], timeout=20)

    staged = staging / "All mail Including Spam and Trash.mbox"
    assert terminal.status == "succeeded"
    assert snapshot["kind"] == "mbox"
    assert snapshot["origin"] == "upload"
    assert snapshot["mbox"]["source_path"] == str(staged.resolve())
    assert snapshot["mbox"]["bytes_total"] == len(ARCHIVE)
    assert snapshot["summary"] == {"written": 2, "skipped": 0, "errors": 0}
    # The staged copy is the job's retained source; a second upload never replaces it.
    assert _staged(tmp_path) == [
        "All mail Including Spam and Trash-2.mbox",
        "All mail Including Spam and Trash.mbox",
    ]
    assert staged.read_bytes() == ARCHIVE
    destination = Path(created["output_location"]["job_path"])
    assert destination.parent == tmp_path / "Cabinet"
    report = json.loads(Path(snapshot["report_path"]).read_text())
    assert report["mbox_options"]["source_handling"] == "copy"
    assert report["mbox_options"]["timeout_seconds"] == 12.5
    assert [row["success"] for row in report["results"]] == [row.success for row in expected]
    for artifact in expected_root.rglob("*"):
        if artifact.is_file():
            assert (destination / artifact.relative_to(expected_root)).read_bytes() == artifact.read_bytes()
    await _assert_slot_free(app)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("name", "staged_name"),
    [
        ("Takeout.MBOX", "Takeout.mbox"),
        ("../../escape.mbox", "escape.mbox"),
        ("C:\\Users\\me\\Mail.mbox", "Mail.mbox"),
        (".mbox", "upload.mbox"),
        ("..hidden.mbox", "hidden.mbox"),
        ("a:b?<c>.mbox", "a_b__c_.mbox"),
        ("Courrier reçu.mbox", "Courrier reçu.mbox"),
    ],
)
async def test_upload_names_are_reduced_to_a_basename_in_the_staging_folder(
    tmp_path: Path, monkeypatch, name: str, staged_name: str
) -> None:
    monkeypatch.setattr(mbox_jobs, "convert_mbox", lambda *args, **kwargs: (row for row in ()))
    app = _app(tmp_path)

    status, _, payload = await _post(app, _headers(app, len(ARCHIVE), name=name), _Body([ARCHIVE]))

    assert status == 202, payload
    assert _staged(tmp_path) == [staged_name]
    assert not (tmp_path / "escape.mbox").exists()
    await app.state.job_manager.wait_for_terminal(payload["id"])


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("overrides", "query", "status", "code"),
    [
        ({"configured": False}, "", 409, "invalid_request"),
        ({"csrf": False}, "", 403, "csrf_validation_failed"),
        ({"chunked": True}, "", 411, "mbox_upload_length_required"),
        ({"no_length": True}, "", 411, "mbox_upload_length_required"),
        ({"declared": 7}, "", 400, "mbox_upload_length_mismatch"),
        ({"declared": False}, "", 400, "mbox_upload_length_mismatch"),
        ({"declared": "1e3"}, "", 400, "mbox_upload_length_mismatch"),
        # Past Python's int() digit limit, and longer than 20 digits but equal in value.
        ({"declared": "9" * 4301}, "", 400, "mbox_upload_length_mismatch"),
        ({"declared": str(len(ARCHIVE)).zfill(21)}, "", 400, "mbox_upload_length_mismatch"),
        ({"raw_content_length": "9" * 4301, "declared": "9" * 4301}, "", 400, "mbox_upload_length_mismatch"),
        ({"name": None}, "", 400, "invalid_request"),
        ({"name": "archive.rar"}, "", 400, "invalid_request"),
        ({"name": "All mail.mbox.gz"}, "", 400, "invalid_request"),
        ({"raw_name": "%FF%FE.mbox"}, "", 400, "invalid_request"),
        ({"raw_name": "line%0Abreak.mbox"}, "", 400, "invalid_request"),
        ({"content_type": "multipart/form-data; boundary=x"}, "", 415, "mbox_upload_unsupported_media_type"),
        ({"content_type": None}, "", 415, "mbox_upload_unsupported_media_type"),
        ({}, "options=" + quote('{"timeout_seconds": 0}'), 400, "invalid_request"),
        ({}, "options=" + quote('{"timeout_seconds": "30"}'), 400, "invalid_request"),
        ({}, "options=" + quote('{"resume": true}'), 400, "invalid_request"),
        ({}, "options=not-json", 400, "invalid_request"),
        ({}, "options=%7B%7D&options=%7B%7D", 400, "invalid_request"),
        ({}, "thread_mode=structured", 400, "invalid_request"),
    ],
)
async def test_rejections_before_the_body_read_nothing_and_close(
    tmp_path: Path, overrides: dict, query: str, status: int, code: str
) -> None:
    app = _app(tmp_path, configured=overrides.pop("configured", True))
    headers = _headers(
        app,
        len(ARCHIVE),
        name=overrides.get("name", "All mail.mbox"),
        declared=overrides.get("declared"),
        content_type=overrides.get("content_type", "application/octet-stream"),
        csrf=overrides.get("csrf", True),
    )
    if "raw_name" in overrides:
        headers["x-dead-letter-filename"] = overrides["raw_name"]
    if "raw_content_length" in overrides:
        headers["content-length"] = overrides["raw_content_length"]
    if overrides.get("chunked"):
        del headers["content-length"]
        headers["transfer-encoding"] = "chunked"
    if overrides.get("no_length"):
        del headers["content-length"]
    body = _Body([ARCHIVE])

    got_status, response_headers, payload = await _post(app, headers, body, query=query)

    assert (got_status, payload["errors"][0]["code"]) == (status, code)
    assert body.calls == 0
    assert response_headers.get("connection") == "close"
    assert _staged(tmp_path) == []
    await _assert_slot_free(app)


@pytest.mark.anyio
async def test_declared_length_over_the_archive_cap_is_rejected_before_staging(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(mbox_jobs, "MAX_MBOX_ARCHIVE_BYTES", len(ARCHIVE) - 1)
    app = _app(tmp_path)
    body = _Body([ARCHIVE])

    status, headers, payload = await _post(app, _headers(app, len(ARCHIVE)), body)

    assert (status, payload["errors"][0]["code"]) == (413, "mbox_too_large")
    assert headers["connection"] == "close"
    assert body.calls == 0
    assert _staged(tmp_path) == []
    await _assert_slot_free(app)


@pytest.mark.anyio
async def test_busy_archive_slot_rejects_upload_without_leaking_the_slot(tmp_path: Path) -> None:
    app = _app(tmp_path)
    manager = app.state.job_manager
    held = await manager.reserve_mbox_slot()
    body = _Body([ARCHIVE])

    status, headers, payload = await _post(app, _headers(app, len(ARCHIVE)), body)

    assert (status, payload["errors"][0]["code"]) == (409, "mbox_busy")
    assert headers["connection"] == "close"
    assert body.calls == 0
    with pytest.raises(MboxBusyError):
        await manager.reserve_mbox_slot()
    manager.release_mbox_slot(held)
    manager.release_mbox_slot(held)  # idempotent: a second release frees nothing extra
    await _assert_slot_free(app)
    second = await manager.reserve_mbox_slot()
    with pytest.raises(MboxBusyError):
        await manager.reserve_mbox_slot()
    manager.release_mbox_slot(second)


@pytest.mark.anyio
@pytest.mark.parametrize("shortfall", [1, 0])
async def test_disk_preflight_keeps_the_reserve_free(tmp_path: Path, monkeypatch, shortfall: int) -> None:
    monkeypatch.setattr(mbox_jobs, "convert_mbox", lambda *args, **kwargs: (row for row in ()))
    free = uploads.MBOX_UPLOAD_DISK_RESERVE_BYTES + len(ARCHIVE) - shortfall
    monkeypatch.setattr(uploads.shutil, "disk_usage", lambda path: _DiskUsage(free * 2, free, free))
    app = _app(tmp_path)
    body = _Body([ARCHIVE])

    status, headers, payload = await _post(app, _headers(app, len(ARCHIVE)), body)

    if shortfall:
        assert (status, payload["errors"][0]["code"]) == (507, "mbox_upload_insufficient_storage")
        assert headers["connection"] == "close"
        assert body.calls == 0
        assert _staged(tmp_path) == []
    else:
        assert status == 202, payload
        await app.state.job_manager.wait_for_terminal(payload["id"])
    await _assert_slot_free(app)


@pytest.mark.anyio
async def test_free_space_is_rechecked_while_the_body_arrives(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(uploads, "_SPACE_RECHECK_BYTES", 16)
    monkeypatch.setattr(uploads, "_WRITE_BATCH_BYTES", 8)
    readings = iter([10 * uploads.MBOX_UPLOAD_DISK_RESERVE_BYTES, 0])
    monkeypatch.setattr(uploads.shutil, "disk_usage", lambda path: _DiskUsage(0, 0, next(readings)))
    app = _app(tmp_path)
    chunks = [ARCHIVE[index : index + 8] for index in range(0, len(ARCHIVE), 8)]

    status, headers, payload = await _post(app, _headers(app, len(ARCHIVE)), _Body(chunks))

    assert (status, payload["errors"][0]["code"]) == (507, "mbox_upload_insufficient_storage")
    assert headers["connection"] == "close"
    assert _staged(tmp_path) == []
    await _assert_slot_free(app)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error_number", "status", "code"),
    [
        (errno.ENOSPC, 507, "mbox_upload_insufficient_storage"),
        *([(errno.EDQUOT, 507, "mbox_upload_insufficient_storage")] if hasattr(errno, "EDQUOT") else []),
        (errno.EIO, 500, "backend_error"),
    ],
)
async def test_write_failures_remove_the_part_file_and_release_the_slot(
    tmp_path: Path, monkeypatch, error_number: int, status: int, code: str
) -> None:
    monkeypatch.setattr(uploads, "_WRITE_BATCH_BYTES", 8)
    writes: list[int] = []

    def failing_write(handle, data):
        writes.append(len(data))
        if len(writes) == 2:
            raise OSError(error_number, "synthetic write failure")
        handle.write(data)

    monkeypatch.setattr(uploads, "_write_chunk", failing_write)
    app = _app(tmp_path)
    chunks = [ARCHIVE[index : index + 8] for index in range(0, len(ARCHIVE), 8)]

    got_status, headers, payload = await _post(app, _headers(app, len(ARCHIVE)), _Body(chunks))

    assert (got_status, payload["errors"][0]["code"]) == (status, code)
    assert headers["connection"] == "close"
    assert _staged(tmp_path) == []
    await _assert_slot_free(app)


@pytest.mark.anyio
@pytest.mark.parametrize("error_number", _NO_SPACE_ERRNOS)
@pytest.mark.parametrize("step", ["staging-folder", "part-file", "name-claim", "rename"])
async def test_storage_exhaustion_outside_the_writes_is_507_and_cleans_up(
    tmp_path: Path, monkeypatch, step: str, error_number: int
) -> None:
    app = _app(tmp_path)
    staging_name = uploads.MBOX_UPLOAD_STAGING_DIRNAME
    real_mkdir, real_open, real_replace = Path.mkdir, Path.open, os.replace

    def full() -> OSError:
        return OSError(error_number, "synthetic storage exhaustion")

    def mkdir(self, *args, **kwargs):
        if step == "staging-folder" and self.name == staging_name:
            raise full()
        return real_mkdir(self, *args, **kwargs)

    def open_(self, mode="r", *args, **kwargs):
        if mode == "xb" and self.parent.name == staging_name:
            if (step, self.suffix) in {("part-file", ".part"), ("name-claim", ".mbox")}:
                raise full()
        return real_open(self, mode, *args, **kwargs)

    def replace(src, dst, *args, **kwargs):
        if step == "rename" and Path(dst).suffix == ".mbox":
            raise full()
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(Path, "open", open_)
    monkeypatch.setattr(uploads.os, "replace", replace)
    body = _Body([ARCHIVE])

    status, headers, payload = await _post(app, _headers(app, len(ARCHIVE)), body)

    assert (status, payload["errors"][0]["code"]) == (507, "mbox_upload_insufficient_storage")
    assert headers["connection"] == "close"
    assert (body.calls == 0) == (step in {"staging-folder", "part-file"})
    assert _staged(tmp_path) == []  # no part file and no claimed name left behind
    await _assert_slot_free(app)


@pytest.mark.anyio
@pytest.mark.parametrize("entry", ["symlink-outside", "symlink-inside", "dangling-symlink", "file"])
async def test_staging_entry_must_be_a_real_folder_inside_the_inbox(tmp_path: Path, entry: str) -> None:
    app = _app(tmp_path)
    inbox = tmp_path / "Inbox"
    inbox.mkdir(exist_ok=True)
    staging = inbox / uploads.MBOX_UPLOAD_STAGING_DIRNAME
    outside = tmp_path / "elsewhere"
    inside = inbox / "real-folder"
    outside.mkdir()
    inside.mkdir()
    try:
        if entry == "symlink-outside":
            staging.symlink_to(outside, target_is_directory=True)
        elif entry == "symlink-inside":
            staging.symlink_to(inside, target_is_directory=True)
        elif entry == "dangling-symlink":
            staging.symlink_to(tmp_path / "missing", target_is_directory=True)
        else:
            staging.write_bytes(b"not a folder")
    except OSError as exc:
        pytest.skip(f"cannot create a symlink here: {exc}")
    body = _Body([ARCHIVE])

    status, headers, payload = await _post(app, _headers(app, len(ARCHIVE)), body)

    error = payload["errors"][0]
    assert (status, error["code"], error["path"]) == (403, "invalid_request", uploads.MBOX_UPLOAD_STAGING_DIRNAME)
    assert headers["connection"] == "close"
    assert body.calls == 0
    assert list(outside.iterdir()) == []
    assert list(inside.iterdir()) == []
    assert not (tmp_path / "missing").exists()
    await _assert_slot_free(app)


def test_length_headers_are_bounded_before_conversion() -> None:
    padded = "0" * 19 + "5"
    assert uploads._parse_length(Headers({"content-length": padded, "x-dead-letter-upload-length": "5"})) == 5
    for too_long in ("0" * 20 + "5", "9" * 4301):
        for header in ("content-length", "x-dead-letter-upload-length"):
            fields = {"content-length": "5", "x-dead-letter-upload-length": "5", header: too_long}
            with pytest.raises(uploads.UploadRejected) as rejected:
                uploads._parse_length(Headers(fields))
            assert (rejected.value.status_code, rejected.value.code) == (400, "mbox_upload_length_mismatch")


@pytest.mark.anyio
async def test_client_disconnect_mid_body_cleans_up(tmp_path: Path) -> None:
    app = _app(tmp_path)
    body = _Body([ARCHIVE[:10]], then="disconnect")

    status, _, payload = await _post(app, _headers(app, len(ARCHIVE)), body)

    assert (status, payload["errors"][0]["code"]) == (400, "mbox_upload_incomplete")
    assert body.calls == 2
    assert _staged(tmp_path) == []
    await _assert_slot_free(app)


@pytest.mark.anyio
async def test_stalled_body_times_out_through_the_full_middleware_stack(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(uploads, "MBOX_UPLOAD_IDLE_TIMEOUT_SECONDS", 0.05)
    app = _app(tmp_path)
    body = _Body([ARCHIVE[:10]], then="stall")

    status, headers, payload = await asyncio.wait_for(
        _post(app, _headers(app, len(ARCHIVE)), body), timeout=5
    )

    assert (status, payload["errors"][0]["code"]) == (408, "mbox_upload_timeout")
    assert headers["connection"] == "close"
    assert _staged(tmp_path) == []
    await _assert_slot_free(app)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("chunks", "declared", "status", "code"),
    [
        ([ARCHIVE, b"extra"], len(ARCHIVE), 413, "mbox_upload_too_long"),
        ([ARCHIVE[:-1]], len(ARCHIVE), 400, "mbox_upload_incomplete"),
    ],
)
async def test_actual_byte_count_must_equal_the_declared_length(
    tmp_path: Path, chunks: list[bytes], declared: int, status: int, code: str
) -> None:
    app = _app(tmp_path)

    got_status, headers, payload = await _post(app, _headers(app, declared), _Body(chunks))

    assert (got_status, payload["errors"][0]["code"]) == (status, code)
    assert headers["connection"] == "close"
    assert _staged(tmp_path) == []
    await _assert_slot_free(app)


@pytest.mark.anyio
async def test_lying_content_length_cannot_exceed_the_cap(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mbox_jobs, "MAX_MBOX_ARCHIVE_BYTES", len(ARCHIVE))
    app = _app(tmp_path)

    async def overlong():
        yield ARCHIVE
        yield b"more than the cap"

    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
        response = await client.post(
            "/api/mbox/uploads",
            content=overlong(),
            headers={
                **await csrf_headers(client),
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(ARCHIVE)),
                "X-Dead-Letter-Filename": "mail.mbox",
                "X-Dead-Letter-Upload-Length": str(len(ARCHIVE)),
            },
        )

    assert response.status_code == 413
    assert response.json()["errors"][0]["code"] == "mbox_upload_too_long"
    assert _staged(tmp_path) == []
    await _assert_slot_free(app)


@pytest.mark.anyio
async def test_job_admission_failure_removes_the_staged_file(tmp_path: Path, monkeypatch) -> None:
    def refuse(*args, **kwargs):
        raise ValueError("synthetic admission failure")

    monkeypatch.setattr("dead_letter.backend.jobs.admit_staged_mbox_source", refuse)
    app = _app(tmp_path)

    status, headers, payload = await _post(app, _headers(app, len(ARCHIVE)), _Body([ARCHIVE]))

    assert (status, payload["errors"][0]["code"]) == (400, "invalid_request")
    assert headers["connection"] == "close"
    assert _staged(tmp_path) == []
    await _assert_slot_free(app)


def _close_and_refuse_task(coro):
    coro.close()
    raise RuntimeError("synthetic task creation failure")


def _refuse_tracking(task, job_id, *, mbox_record=None):
    raise RuntimeError("synthetic task tracking failure")


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["create_task", "track_task"])
@pytest.mark.parametrize("flow", ["upload", "local-path"])
async def test_job_start_failure_after_the_slot_transfer_releases_it_once(
    tmp_path: Path, monkeypatch, flow: str, failure: str
) -> None:
    manager = JobManager(inbox_root=tmp_path / "Inbox", cabinet_root=tmp_path / "Cabinet")
    staging = tmp_path / "Inbox" / uploads.MBOX_UPLOAD_STAGING_DIRNAME
    staging.mkdir(parents=True)
    (staging / "mail.mbox").write_bytes(ARCHIVE)
    reservation = await manager.reserve_mbox_slot() if flow == "upload" else None
    if failure == "track_task":
        monkeypatch.setattr(manager, "_track_task", _refuse_tracking)

    async def start():
        if reservation is not None:
            return await manager.create_staged_mbox_job(
                MboxJobCreateRequest(input_path=str(staging / "mail.mbox")),
                staging_root=staging,
                reservation=reservation,
            )
        return await manager.create_mbox_job(
            MboxJobCreateRequest(input_path=f"{uploads.MBOX_UPLOAD_STAGING_DIRNAME}/mail.mbox"),
            source_root=tmp_path / "Inbox",
        )

    with pytest.raises(RuntimeError, match="synthetic task"):
        if failure == "create_task":
            with patch.object(asyncio, "create_task", _close_and_refuse_task):
                await start()
        else:
            await start()

    assert manager._jobs == {}
    if reservation is not None:
        assert reservation.held is False  # the job took the slot, then gave it back
        manager.release_mbox_slot(reservation)  # frees nothing a second time
    held = await manager.reserve_mbox_slot()
    with pytest.raises(MboxBusyError):
        await manager.reserve_mbox_slot()
    manager.release_mbox_slot(held)
    await asyncio.wait_for(manager.shutdown_mbox_jobs(), timeout=5)
    assert manager._active_mbox_jobs == 0
    assert not (tmp_path / "Cabinet").exists()


@pytest.mark.anyio
async def test_upload_whose_job_task_cannot_start_removes_the_staged_file(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path)
    manager = app.state.job_manager
    monkeypatch.setattr(manager, "_track_task", _refuse_tracking)

    status, headers, payload = await _post(app, _headers(app, len(ARCHIVE)), _Body([ARCHIVE]))

    assert (status, payload["errors"][0]["code"]) == (500, "backend_error")
    assert headers["connection"] == "close"
    assert _staged(tmp_path) == []
    assert manager._jobs == {}
    await _assert_slot_free(app)


@pytest.mark.anyio
async def test_body_limit_backstop_rejects_oversized_requests_but_not_uploads(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(api_mod, "_MAX_API_BODY_BYTES", 1024)
    monkeypatch.setattr(mbox_jobs, "convert_mbox", lambda *args, **kwargs: (row for row in ()))
    app = _app(tmp_path)
    archive = ARCHIVE * 40
    assert len(archive) > 1024

    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
        headers = await csrf_headers(client)
        oversized = await client.post(
            "/api/import",
            files={"file": ("big.eml", b"Subject: x\n\n" + b"a" * 2048, "message/rfc822")},
            headers=headers,
        )
        small = await client.post(
            "/api/import",
            files={"file": ("small.eml", b"Subject: x\n\nhello\n", "message/rfc822")},
            headers=headers,
        )
        upload = await client.post(
            "/api/mbox/uploads",
            content=archive,
            headers={
                **headers,
                "Content-Type": "application/octet-stream",
                "X-Dead-Letter-Filename": "mail.mbox",
                "X-Dead-Letter-Upload-Length": str(len(archive)),
            },
        )

    assert oversized.status_code == 413
    assert oversized.headers["connection"] == "close"
    assert not (tmp_path / "Inbox" / "big.eml").exists()
    assert small.status_code == 202, small.text
    assert upload.status_code == 202, upload.text
    await app.state.job_manager.wait_for_terminal(upload.json()["id"])


def test_backstop_admits_the_largest_documented_import() -> None:
    # A full batch (100 MiB in 100 parts) plus generous multipart framing per part.
    framing_per_part = 16 * 1024
    largest = api_mod._MAX_IMPORT_BATCH_BYTES + api_mod._MAX_IMPORT_BATCH_FILES * framing_per_part
    assert largest < api_mod._MAX_API_BODY_BYTES
    assert api_mod._MAX_IMPORT_FILE_BYTES + framing_per_part < api_mod._MAX_API_BODY_BYTES


@pytest.mark.anyio
async def test_fully_read_requests_keep_the_connection_open(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
        rejected = await client.post("/api/jobs", json={"mode": "file", "input_path": "missing.eml"})
        accepted_shape = await client.post(
            "/api/jobs", json={"mode": "file", "input_path": "missing.eml"}, headers=await csrf_headers(client)
        )

    assert rejected.status_code == 403
    assert rejected.headers["connection"] == "close"
    assert accepted_shape.status_code == 400
    assert "connection" not in accepted_shape.headers


def test_wire_rejection_closes_without_asking_for_the_body(tmp_path: Path) -> None:
    """A real server answers an Expect request early and then closes the socket."""
    app = _app(tmp_path, configured=False)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert time.monotonic() < deadline, "server did not start"
            time.sleep(0.01)
        length = 16 * 1024**3
        request = (
            "POST /api/mbox/uploads HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"X-Dead-Letter-CSRF: {app.state.csrf_token}\r\n"
            "Content-Type: application/octet-stream\r\n"
            "X-Dead-Letter-Filename: mail.mbox\r\n"
            f"X-Dead-Letter-Upload-Length: {length}\r\n"
            f"Content-Length: {length}\r\n"
            "Expect: 100-continue\r\n\r\n"
        )
        with socket.create_connection(("127.0.0.1", port), timeout=10) as client:
            client.sendall(request.encode("ascii"))
            received = b""
            while chunk := client.recv(65536):
                received += chunk
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()

    head, _, body = received.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 409 ")
    assert b"100 Continue" not in received
    assert b"connection: close" in head.lower()
    assert json.loads(body)["errors"][0]["path"] == "settings"
