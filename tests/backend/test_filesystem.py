from __future__ import annotations

from pathlib import Path

import pytest

from dead_letter.backend.filesystem import FilesystemBrowser


@pytest.fixture
def sample_tree(tmp_path: Path) -> Path:
    root = tmp_path
    (root / "mail").mkdir()
    (root / "mail" / "hello.eml").write_text("hello", encoding="utf-8")
    (root / "mail" / "notes.txt").write_text("notes", encoding="utf-8")
    (root / "mail" / "nested").mkdir()
    (root / "mail" / "nested" / "deep.eml").write_text("deep", encoding="utf-8")
    (root / ".hidden").write_text("secret", encoding="utf-8")
    return root


def test_list_dir_returns_relative_and_absolute_paths(sample_tree: Path) -> None:
    browser = FilesystemBrowser(root=sample_tree)

    entries = browser.list_dir("mail")

    by_name = {entry.name: entry for entry in entries}
    assert by_name["hello.eml"].path == "mail/hello.eml"
    assert Path(by_name["hello.eml"].input_path) == (sample_tree / "mail" / "hello.eml").resolve()
    assert by_name["nested"].path == "mail/nested"
    assert by_name["nested"].type == "directory"
    assert ".hidden" not in by_name


def test_list_dir_filters_files_and_keeps_directories(sample_tree: Path) -> None:
    browser = FilesystemBrowser(root=sample_tree)

    entries = browser.list_dir("mail", filter_ext=".eml")

    names = {entry.name for entry in entries}
    assert names == {"hello.eml", "nested"}


def test_resolve_relative_path_rejects_escape_and_absolute(sample_tree: Path) -> None:
    browser = FilesystemBrowser(root=sample_tree)

    with pytest.raises(PermissionError):
        browser.resolve_relative("../../etc/passwd")
    with pytest.raises(PermissionError):
        browser.resolve_relative("/etc/passwd")

def test_list_dir_skips_entries_that_escape_root(sample_tree: Path) -> None:
    browser = FilesystemBrowser(root=sample_tree)
    outside = (sample_tree.parent / "outside.eml").resolve()
    outside.write_text("secret", encoding="utf-8")
    (sample_tree / "mail" / "escape.eml").symlink_to(outside)

    entries = browser.list_dir("mail")

    names = {entry.name for entry in entries}
    assert "hello.eml" in names
    assert "escape.eml" not in names


def test_list_dir_preserves_in_root_symlink_paths(sample_tree: Path) -> None:
    browser = FilesystemBrowser(root=sample_tree)
    target_dir = sample_tree / "mail" / "nested"
    symlink_dir = sample_tree / "mail" / "alias"
    symlink_dir.symlink_to(target_dir, target_is_directory=True)

    entries = browser.list_dir("mail")

    by_name = {entry.name: entry for entry in entries}
    assert by_name["alias"].path == "mail/alias"
    assert Path(by_name["alias"].input_path) == target_dir.resolve()

    nested_entries = browser.list_dir("mail/alias")
    assert nested_entries[0].path == "mail/alias/deep.eml"
