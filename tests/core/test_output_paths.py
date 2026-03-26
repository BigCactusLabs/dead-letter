from __future__ import annotations

from pathlib import Path

import pytest

from dead_letter.core import ConvertOptions, ConvertResult, convert, convert_dir
from dead_letter.core._pipeline import _collision_safe_target


def test_convert_writes_sibling_when_output_none(copy_fixture) -> None:
    source = copy_fixture("plain_text.eml", "in/plain_text.eml")

    result = convert(source)

    assert result.output == source.parent / "plain-text-fixture.md"
    assert result.output.exists()


def test_convert_uses_exact_file_when_output_is_md(copy_fixture, tmp_path: Path) -> None:
    source = copy_fixture("plain_text.eml")
    target = tmp_path / "out" / "exact.md"

    result = convert(source, output=target)

    assert result.output == target
    assert target.exists()


def test_convert_treats_non_md_output_as_directory(copy_fixture, tmp_path: Path) -> None:
    source = copy_fixture("plain_text.eml")
    output_dir = tmp_path / "rendered"

    result = convert(source, output=output_dir)

    assert result.output == output_dir / "plain-text-fixture.md"
    assert result.output.exists()


def test_convert_uses_collision_safe_suffix(copy_fixture, tmp_path: Path) -> None:
    source = copy_fixture("plain_text.eml")
    output_dir = tmp_path / "rendered"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "plain-text-fixture.md").write_text("existing", encoding="utf-8")

    result = convert(source, output=output_dir)

    assert result.output == output_dir / "plain-text-fixture-2.md"
    assert result.output.exists()


def test_convert_dir_mirrors_structure_under_output_root(copy_fixture, tmp_path: Path) -> None:
    input_root = tmp_path / "in"
    output_root = tmp_path / "out"

    copy_fixture("plain_text.eml", "in/a/b/plain_text.eml")
    copy_fixture("html_only.eml", "in/c/html_only.eml")

    results = convert_dir(input_root, output=output_root)

    assert len(results) == 2
    assert (output_root / "a" / "b" / "plain-text-fixture.md").exists()
    assert (output_root / "c" / "html-only-fixture.md").exists()


def test_convert_dir_includes_uppercase_eml_files(tmp_path: Path, monkeypatch) -> None:
    input_root = tmp_path / "in"
    input_root.mkdir(parents=True, exist_ok=True)
    (input_root / "a.eml").write_text("placeholder", encoding="utf-8")
    (input_root / "B.EML").write_text("placeholder", encoding="utf-8")

    seen: list[str] = []

    def fake_convert(path: str | Path, *, output: str | Path | None = None, options=None) -> ConvertResult:
        _ = (output, options)
        seen.append(Path(path).name)
        return ConvertResult(
            source=Path(path),
            output=None,
            subject="",
            sender="",
            date=None,
            attachments=[],
            success=True,
            error=None,
            dry_run=False,
        )

    import dead_letter.core._pipeline as pipeline

    monkeypatch.setattr(pipeline, "convert", fake_convert)

    results = convert_dir(input_root)

    assert len(results) == 2
    assert sorted(seen) == ["B.EML", "a.eml"]


def test_convert_dir_skips_symlinked_eml_files_resolving_outside_root(
    tmp_path: Path, monkeypatch
) -> None:
    input_root = tmp_path / "in"
    input_root.mkdir(parents=True, exist_ok=True)
    (input_root / "inside.eml").write_text("placeholder", encoding="utf-8")
    outside = tmp_path / "outside.eml"
    outside.write_text("secret", encoding="utf-8")
    (input_root / "link.eml").symlink_to(outside)

    seen: list[str] = []

    def fake_convert(path: str | Path, *, output: str | Path | None = None, options=None) -> ConvertResult:
        _ = (output, options)
        seen.append(Path(path).name)
        return ConvertResult(
            source=Path(path),
            output=None,
            subject="",
            sender="",
            date=None,
            attachments=[],
            success=True,
            error=None,
            dry_run=False,
        )

    import dead_letter.core._pipeline as pipeline

    monkeypatch.setattr(pipeline, "convert", fake_convert)

    results = convert_dir(input_root)

    assert len(results) == 1
    assert seen == ["inside.eml"]


def test_dry_run_writes_nothing_and_disables_delete(copy_fixture) -> None:
    source = copy_fixture("plain_text.eml", "in/plain_text.eml")
    expected = source.parent / "plain-text-fixture.md"

    result = convert(source, options=ConvertOptions(dry_run=True, delete_eml=True))

    assert result.success is True
    assert result.dry_run is True
    assert result.output is None
    assert source.exists()
    assert not expected.exists()


def test_delete_eml_deletes_only_after_successful_write(copy_fixture, tmp_path: Path) -> None:
    source = copy_fixture("plain_text.eml", "in/plain_text.eml")
    output_dir = tmp_path / "out"

    result = convert(source, output=output_dir, options=ConvertOptions(delete_eml=True))

    assert result.success is True
    assert result.output is not None
    assert result.output.exists()
    assert not source.exists()


def test_delete_eml_rolls_back_written_markdown_when_source_delete_fails(
    copy_fixture, tmp_path: Path, monkeypatch
) -> None:
    source = copy_fixture("plain_text.eml", "in/plain_text.eml")
    output_dir = tmp_path / "out"
    expected = output_dir / "plain-text-fixture.md"
    original_unlink = Path.unlink

    def failing_unlink(self: Path, *args, **kwargs) -> None:
        if self.resolve() == source.resolve():
            raise PermissionError("cannot delete source")
        original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_unlink)

    result = convert(source, output=output_dir, options=ConvertOptions(delete_eml=True))

    assert result.success is False
    assert result.output is None
    assert result.error == "cannot delete source"
    assert source.exists()
    assert not expected.exists()


def test_collision_safe_target_raises_after_limit(tmp_path: Path, monkeypatch) -> None:
    import dead_letter.core._pipeline as pipeline

    monkeypatch.setattr(pipeline, "_MAX_COLLISION_INDEX", 5)

    target = tmp_path / "test.md"
    target.write_text("x", encoding="utf-8")
    for index in range(2, 6):
        (tmp_path / f"test-{index}.md").write_text("x", encoding="utf-8")

    with pytest.raises(RuntimeError, match="collision"):
        _collision_safe_target(target)
