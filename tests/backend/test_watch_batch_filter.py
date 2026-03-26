from __future__ import annotations

from watchfiles import Change

from dead_letter.backend.watch import _EmlFilter


def test_eml_filter_accepts_normal_eml() -> None:
    filter_ = _EmlFilter()
    assert filter_(Change.added, "/inbox/hello.eml") is True


def test_eml_filter_rejects_non_eml() -> None:
    filter_ = _EmlFilter()
    assert filter_(Change.added, "/inbox/hello.txt") is False


def test_eml_filter_rejects_eml_inside_batch_dir() -> None:
    filter_ = _EmlFilter()
    assert filter_(Change.added, "/inbox/_batch-abc123/hello.eml") is False


def test_eml_filter_rejects_nested_batch_dir() -> None:
    filter_ = _EmlFilter()
    assert filter_(Change.added, "/inbox/_batch-abc123/sub/hello.eml") is False


def test_eml_filter_accepts_eml_in_non_batch_underscore_dir() -> None:
    """Directories not matching the reserved _batch-* prefix should pass through."""
    filter_ = _EmlFilter()
    assert filter_(Change.added, "/inbox/_archive/hello.eml") is True
