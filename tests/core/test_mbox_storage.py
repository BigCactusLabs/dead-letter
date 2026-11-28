"""Storage errno survives record isolation and even a failed worker receipt."""

from __future__ import annotations

import errno
import hashlib
import sys
from pathlib import Path

import pytest

from dead_letter.core import mbox_import
from dead_letter.core import mbox_isolation as isolation
from dead_letter.core.mbox import MboxRecord
from dead_letter.core.mbox_import import convert_mbox
from dead_letter.core.types import ConvertOptions

POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"
MESSAGE = b"Subject: Storage failure\n\nSynthetic body\n"
QUOTA_ERRNO = getattr(errno, "EDQUOT", None)
QUOTA_SKIP = pytest.mark.skipif(QUOTA_ERRNO is None, reason="EDQUOT is unavailable on this platform")


@pytest.mark.parametrize("bundles", [False, True])
@pytest.mark.parametrize("error_number, expected", [
    (errno.ENOSPC, "mbox_storage_error"),
    pytest.param(QUOTA_ERRNO, "mbox_storage_error", marks=QUOTA_SKIP),
    (errno.EIO, "conversion_error"),
    (None, "conversion_error"),
])
def test_storage_errno_is_distinct_and_incomplete_output_is_removed(
    tmp_path, monkeypatch, bundles, error_number, expected,
):
    source = tmp_path / "mail.mbox"
    raw = (POSTMARK + MESSAGE) * 3
    source.write_bytes(raw)
    root = tmp_path / "out"
    original = mbox_import.serialize_markdown
    calls = 0

    def fail_second(rendered):
        nonlocal calls
        calls += 1
        if calls == 2:
            # This happens after the destination is created. Text alone must
            # never turn an unrelated OSError into a storage exhaustion signal.
            raise OSError(error_number, "PRIVATE BODY ENOSPC EDQUOT no space left on device")
        return original(rendered)

    monkeypatch.setattr(mbox_import, "serialize_markdown", fail_second)
    rows = list(convert_mbox(source, output=root, bundles=bundles))

    assert [row.success for row in rows] == [True, False, True]
    assert rows[1].error["code"] == expected
    assert "PRIVATE" not in str(rows[1].error)
    assert rows[1].output is None
    assert set(root.iterdir()) == {
        row.output.parent if bundles else row.output for row in (rows[0], rows[2])
    }
    assert source.read_bytes() == raw


# Patch only a test subprocess's failing operation; use the real worker main,
# record conversion, receipt writer and parent supervision/publication path.
_FAULT_WORKER = r'''
import sys
from pathlib import Path
from dead_letter import _mbox_worker as worker
from dead_letter.core import mbox_import

request, mode, number = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
sys.argv = [sys.argv[0], str(request), *sys.argv[4:]]

if mode in ("conversion", "unexpected-exit"):
    def fail_conversion(*args, **kwargs):
        if mode == "unexpected-exit":
            raise SystemExit(worker.EXIT_STORAGE_ERROR)
        raise OSError(number, "PRIVATE CONVERSION DETAIL")
    mbox_import.serialize_markdown = fail_conversion
else:
    original_write = Path.write_bytes
    def fail_receipt(path, data):
        if path.name == "receipt.json":
            raise OSError(number, "PRIVATE RECEIPT DETAIL")
        return original_write(path, data)
    Path.write_bytes = fail_receipt

raise SystemExit(worker.main())
'''


@pytest.mark.parametrize("budgeted", [
    False,
    pytest.param(True, marks=pytest.mark.skipif(
        sys.platform not in {"linux", "darwin"}, reason="CPU budgets require supported POSIX platform",
    )),
])
@pytest.mark.parametrize("mode, error_number, expected", [
    ("conversion", errno.ENOSPC, "mbox_storage_error"),
    ("conversion", errno.EIO, "conversion_error"),
    ("receipt", errno.ENOSPC, "mbox_storage_error"),
    pytest.param("receipt", QUOTA_ERRNO, "mbox_storage_error", marks=QUOTA_SKIP),
    ("receipt", errno.EIO, "mbox_worker_crashed"),
    ("unexpected-exit", 0, "mbox_worker_crashed"),
])
def test_real_worker_reports_storage_errno_without_publishing(
    tmp_path, monkeypatch, capfd, budgeted, mode, error_number, expected,
):
    requests = []

    def command(request, *budget_args):
        requests.append(request)
        return [sys.executable, "-I", "-c", _FAULT_WORKER,
                str(request), mode, str(error_number), *budget_args]

    monkeypatch.setattr(isolation, "_worker_command", command)
    source = tmp_path / "input.eml"
    source.write_bytes(MESSAGE)
    record = MboxRecord(1, 0, len(POSTMARK), len(POSTMARK + MESSAGE),
                        hashlib.sha256(MESSAGE).hexdigest(), source)
    root = tmp_path / "out"
    root.mkdir()
    prior_output = root / "keep.md"
    prior_output.write_text("previous result", encoding="utf-8")

    result = isolation.convert_record_isolated(
        record, Path("archive.mbox"), root, ConvertOptions(), bundles=False,
        unescape="preserve", timeout=15,
        budgets=isolation.WorkerBudgets(cpu_seconds=5) if budgeted else None,
    )

    assert not result.success and result.error["code"] == expected
    assert result.output is None and result.mbox["index"] == 1
    assert "PRIVATE" not in str(result.error)
    assert capfd.readouterr() == ("", "")
    assert requests and all(not request.parent.exists() for request in requests)
    assert list(root.iterdir()) == [prior_output]
    assert prior_output.read_text(encoding="utf-8") == "previous result"
    assert source.read_bytes() == MESSAGE
