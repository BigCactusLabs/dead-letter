from __future__ import annotations

import threading
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


def test_convert_truncates_long_subject_output_name(tmp_path: Path) -> None:
    source = tmp_path / "long-subject.eml"
    subject = "a" * 99 + " " + "b" * 300
    source.write_text(
        f"From: a@b\nSubject: {subject}\n\nLong subject body\n",
        encoding="utf-8",
    )

    result = convert(source, output=tmp_path / "out")

    assert result.success is True
    assert result.output is not None
    assert result.output.stem == "a" * 99
    assert len(result.output.stem) <= 100
    assert not result.output.stem.endswith("-")
    assert result.output.exists()


def test_convert_uses_collision_safe_suffix(copy_fixture, tmp_path: Path) -> None:
    source = copy_fixture("plain_text.eml")
    output_dir = tmp_path / "rendered"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "plain-text-fixture.md").write_text("existing", encoding="utf-8")

    result = convert(source, output=output_dir)

    assert result.output == output_dir / "plain-text-fixture-2.md"
    assert result.output.exists()


def test_convert_concurrent_same_slug_uses_distinct_outputs(tmp_path: Path, monkeypatch) -> None:
    source_one = tmp_path / "one.eml"
    source_two = tmp_path / "two.eml"
    output_dir = tmp_path / "out"
    source_one.write_text("From: a@b\nSubject: same\n\nAlpha body\n", encoding="utf-8")
    source_two.write_text("From: c@d\nSubject: same\n\nBeta body\n", encoding="utf-8")

    import dead_letter.core._pipeline as pipeline

    barrier = threading.Barrier(2)
    original = pipeline._collision_safe_target

    def synchronized_target(target: Path) -> Path:
        candidate = original(target)
        barrier.wait(timeout=1)
        return candidate

    monkeypatch.setattr(pipeline, "_collision_safe_target", synchronized_target)

    results: list[ConvertResult] = []
    failures: list[BaseException] = []

    def worker(source: Path) -> None:
        try:
            results.append(convert(source, output=output_dir))
        except BaseException as exc:  # pragma: no cover - defensive worker capture
            failures.append(exc)

    threads = [
        threading.Thread(target=worker, args=(source_one,)),
        threading.Thread(target=worker, args=(source_two,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert len(results) == 2
    assert sorted(path.name for path in output_dir.iterdir()) == ["same-2.md", "same.md"]
    assert {result.output.name for result in results if result.output is not None} == {"same.md", "same-2.md"}

    contents = [path.read_text(encoding="utf-8") for path in output_dir.iterdir()]
    assert sum("Alpha body" in content for content in contents) == 1
    assert sum("Beta body" in content for content in contents) == 1


def test_convert_dir_mirrors_structure_under_output_root(copy_fixture, tmp_path: Path) -> None:
    input_root = tmp_path / "in"
    output_root = tmp_path / "out"

    copy_fixture("plain_text.eml", "in/a/b/plain_text.eml")
    copy_fixture("html_only.eml", "in/c/html_only.eml")

    results = convert_dir(input_root, output=output_root)

    assert len(results) == 2
    assert (output_root / "a" / "b" / "plain-text-fixture.md").exists()
    assert (output_root / "c" / "html-only-fixture.md").exists()


def test_convert_dir_continues_after_middle_write_failure(tmp_path: Path, monkeypatch) -> None:
    input_root = tmp_path / "in"
    output_root = tmp_path / "out"
    input_root.mkdir()
    for name in ("a-ok", "b-bad", "c-ok"):
        (input_root / f"{name}.eml").write_text(
            f"From: a@b\nSubject: {name}\n\n{name} body\n",
            encoding="utf-8",
        )

    import dead_letter.core._pipeline as pipeline

    original_open = pipeline._open_collision_safe_output

    def fail_middle_output(target: Path):
        if target.name == "b-bad.md":
            raise OSError("cannot write b-bad")
        return original_open(target)

    monkeypatch.setattr(pipeline, "_open_collision_safe_output", fail_middle_output)

    results = convert_dir(input_root, output=output_root)

    assert [result.source.name for result in results] == ["a-ok.eml", "b-bad.eml", "c-ok.eml"]
    assert [result.success for result in results] == [True, False, True]
    assert results[1].error == "cannot write b-bad"
    assert (output_root / "a-ok.md").exists()
    assert (output_root / "c-ok.md").exists()


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


def test_convert_dir_deduplicates_in_tree_symlink_aliases(
    tmp_path: Path, monkeypatch
) -> None:
    input_root = tmp_path / "in"
    input_root.mkdir(parents=True, exist_ok=True)
    source = input_root / "inside.eml"
    source.write_text("placeholder", encoding="utf-8")
    (input_root / "alias.eml").symlink_to(source)

    seen: list[Path] = []

    def fake_convert(path: str | Path, *, output: str | Path | None = None, options=None) -> ConvertResult:
        _ = (output, options)
        seen.append(Path(path).resolve())
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
    assert seen == [source.resolve()]


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


def test_convert_returns_failure_when_target_cleanup_stat_fails(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.eml"
    target = tmp_path / "out" / "planned.md"
    source.write_text("From: a@b\nSubject: planned\n\nBody\n", encoding="utf-8")

    import dead_letter.core._pipeline as pipeline

    original_exists = Path.exists

    def failing_target_stat(self: Path) -> bool:
        if self == target:
            raise OSError("cannot stat planned target")
        return original_exists(self)

    def failing_open(_target: Path):
        raise OSError("cannot open planned target")

    monkeypatch.setattr(Path, "exists", failing_target_stat)
    monkeypatch.setattr(pipeline, "_open_collision_safe_output", failing_open)

    result = convert(source, output=target)

    assert result.success is False
    assert result.output is None
    assert result.error == "cannot open planned target"


def test_collision_safe_target_raises_after_limit(tmp_path: Path, monkeypatch) -> None:
    import dead_letter.core._pipeline as pipeline

    monkeypatch.setattr(pipeline, "_MAX_COLLISION_INDEX", 5)

    target = tmp_path / "test.md"
    target.write_text("x", encoding="utf-8")
    for index in range(2, 6):
        (tmp_path / f"test-{index}.md").write_text("x", encoding="utf-8")

    with pytest.raises(RuntimeError, match="collision"):
        _collision_safe_target(target)


def test_convert_preserves_preexisting_targets_when_collision_naming_exhausts(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.eml"
    output_dir = tmp_path / "out"
    target = output_dir / "victim.md"
    collision_target = output_dir / "victim-2.md"
    source.write_text("From: a@b\nSubject: victim\n\nBody\n", encoding="utf-8")
    output_dir.mkdir()
    target.write_text("pre-existing victim", encoding="utf-8")
    collision_target.write_text("pre-existing collision candidate", encoding="utf-8")

    import dead_letter.core._pipeline as pipeline

    monkeypatch.setattr(pipeline, "_MAX_COLLISION_INDEX", 2)

    result = convert(source, output=target)

    assert result.success is False
    assert result.output is None
    assert "collision-safe output naming exhausted" in (result.error or "")
    assert target.read_text(encoding="utf-8") == "pre-existing victim"
    assert collision_target.read_text(encoding="utf-8") == "pre-existing collision candidate"
