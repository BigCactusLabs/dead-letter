from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest
import yaml

from dead_letter.core import convert
from dead_letter.core import mbox_import
from dead_letter.core.mbox import MboxLimits
from dead_letter.core.mbox_import import convert_mbox
from dead_letter.core.types import ConvertOptions, ThreadMode

FIXTURE = Path(__file__).parent / "fixtures" / "takeout-synthetic.mbox"
POSTMARK = b"From sender@example.test Thu Jun 11 00:38:38 2020\n"
MESSAGE = b"From: alice@example.test\nSubject: Same\n\nHello\n\n"


def frontmatter(path):
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])


def test_fixture_labels_provenance_attachments_and_malformed_recovery(tmp_path):
    source = tmp_path / "takeout.mbox"
    original = FIXTURE.read_bytes()
    source.write_bytes(original)
    rows = list(convert_mbox(source, output=tmp_path / "cabinet", bundles=True))
    assert len(rows) == 3
    assert rows[0].success and rows[2].success
    metadata = frontmatter(rows[0].output)
    assert "Projects/Atelier" in metadata["gmail_labels"]
    assert "Important" in metadata["gmail_labels"]
    assert metadata["message_id"] == "<review-1@example.test>"
    assert str(metadata["gmail_thread_id"]) == "1669160939957813740"
    assert metadata["source"] == "source.eml"
    assert metadata["source_mbox"]["index"] == 1
    assert metadata["source_mbox"]["archive"] == source.name
    assert (rows[0].output.parent / "attachments" / "figures.csv").read_bytes() == b"a,b\n1,2\n"
    provenance = rows[0].mbox
    assert (rows[0].output.parent / "source.eml").read_bytes() == original[
        provenance["message_offset"]:provenance["end_offset"]
    ]
    assert source.read_bytes() == original
    # The damaged message is never silently omitted, whether MIME recovers it
    # with diagnostics or reports a conversion failure.
    assert rows[1].mbox["index"] == 2
    assert rows[1].diagnostics is not None or rows[1].error is not None
    assert rows[2].diagnostics is not None


@pytest.mark.parametrize("mode", [ThreadMode.LATEST, ThreadMode.STRUCTURED])
def test_shared_html_thread_pipeline_matches_standalone_eml(tmp_path, mode):
    existing = Path(__file__).resolve().parents[2] / "benchmarks" / "fixtures" / "gmail-html__webhook-thread-4.eml"
    raw = existing.read_bytes()
    archive = tmp_path / "thread.mbox"
    archive.write_bytes(POSTMARK + raw)
    eml = tmp_path / "thread.eml"
    eml.write_bytes(raw)
    options = ConvertOptions(thread_mode=mode)
    single = convert(eml, output=tmp_path / "single.md", options=options)
    [batch] = list(convert_mbox(archive, output=tmp_path / "batch", options=options))
    assert single.success and batch.success
    assert single.output.read_text().split("---", 2)[2] == batch.output.read_text().split("---", 2)[2]
    if mode is ThreadMode.STRUCTURED:
        assert frontmatter(batch.output)["thread_messages"] > 0
        assert frontmatter(single.output)["thread_messages"] == frontmatter(batch.output)["thread_messages"]


def test_duplicate_missing_and_hostile_subjects_never_control_paths(tmp_path):
    path = tmp_path / "mail.mbox"
    path.write_bytes(b"".join(POSTMARK + b"Subject: " + subject + b"\n\nBody\n" for subject in [
        b"duplicate", b"duplicate", b"", b"../../outside", b"CON", b"/tmp/evil",
    ]))
    root = tmp_path / "out"
    first = list(convert_mbox(path, output=root))
    contents = {r.output: r.output.read_bytes() for r in first}
    second = list(convert_mbox(path, output=root))
    assert all(r.success and r.output.parent == root for r in first + second)
    assert len({r.output for r in first + second}) == 12
    assert [r.mbox for r in first] == [r.mbox for r in second]
    assert all(p.read_bytes() == content for p, content in contents.items())


def test_pipeline_exception_isolated_and_does_not_leak_email(tmp_path, monkeypatch):
    path = tmp_path / "mail.mbox"
    path.write_bytes((POSTMARK + MESSAGE) * 3)
    original = mbox_import._build_rendered_markdown
    calls = 0

    def flaky(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("PRIVATE BODY OR SECRET TEMP PATH")
        return original(*args, **kwargs)

    monkeypatch.setattr(mbox_import, "_build_rendered_markdown", flaky)
    rows = list(convert_mbox(path, output=tmp_path / "out"))
    assert [r.success for r in rows] == [True, False, True]
    assert "PRIVATE" not in str(rows[1].error)
    assert rows[1].error["code"] == "conversion_error"


def test_dry_run_no_outputs_and_source_preserved(tmp_path):
    path = tmp_path / "mail.mbox"
    path.write_bytes(POSTMARK + MESSAGE)
    root = tmp_path / "out"
    [row] = list(convert_mbox(path, output=root, options=ConvertOptions(dry_run=True)))
    assert row.success and row.output is None
    assert not root.exists()
    assert path.read_bytes() == POSTMARK + MESSAGE
    with pytest.raises(ValueError, match="always preserved"):
        list(convert_mbox(path, options=ConvertOptions(delete_eml=True)))


def test_oversized_message_skips_pipeline_then_continues(tmp_path):
    path = tmp_path / "mail.mbox"
    path.write_bytes(POSTMARK + MESSAGE * 30 + POSTMARK + MESSAGE)
    rows = list(convert_mbox(path, output=tmp_path / "out", limits=MboxLimits(max_message_bytes=200)))
    assert [r.success for r in rows] == [False, True]
    assert rows[0].error["code"] == "mbox_message_too_large"


def test_invalid_archive_is_a_named_fatal_result(tmp_path):
    path = tmp_path / "bad.mbox"
    path.write_bytes(b"not mbox")
    [row] = list(convert_mbox(path, output=tmp_path / "out"))
    assert not row.success and row.mbox is None
    assert row.error["code"] == "mbox_archive_error"


@pytest.mark.parametrize("bundles", [False, True])
def test_cancellation_removes_only_incomplete_output(tmp_path, monkeypatch, bundles):
    path = tmp_path / "mail.mbox"
    path.write_bytes(POSTMARK + MESSAGE)
    root = tmp_path / "out"

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(mbox_import, "serialize_markdown", interrupted)
    with pytest.raises(KeyboardInterrupt), closing(convert_mbox(path, output=root, bundles=bundles)) as rows:
        next(rows)
    assert list(root.iterdir()) == []
    assert path.exists()
