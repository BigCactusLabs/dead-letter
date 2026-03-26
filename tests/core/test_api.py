from __future__ import annotations

from pathlib import Path

import pytest

from dead_letter.core import ConvertOptions, convert, convert_dir
from dead_letter.core.types import ConvertResult


def test_core_api_exports_convert_functions() -> None:
    assert callable(convert)
    assert callable(convert_dir)


def test_convert_success_returns_contract(copy_fixture) -> None:
    source = copy_fixture("plain_text.eml")

    result = convert(source)

    assert isinstance(result, ConvertResult)
    assert result.success is True
    assert result.error is None
    assert result.output is not None
    assert result.output.exists()


def test_convert_raises_for_missing_or_non_eml(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        convert(tmp_path / "missing.eml")

    not_eml = tmp_path / "note.txt"
    not_eml.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        convert(not_eml)


def test_convert_handles_internal_errors_without_raise(copy_fixture, monkeypatch) -> None:
    source = copy_fixture("plain_text.eml")

    import dead_letter.core._pipeline as pipeline

    def boom(_path: Path) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "parse_eml", boom)
    result = convert(source)

    assert result.success is False
    assert result.error is not None
    assert "boom" in result.error


def test_convert_propagates_programming_errors(copy_fixture, monkeypatch) -> None:
    source = copy_fixture("plain_text.eml")

    import dead_letter.core._pipeline as pipeline

    def boom(_path: Path) -> object:
        raise TypeError("unexpected type bug")

    monkeypatch.setattr(pipeline, "parse_eml", boom)

    with pytest.raises(TypeError, match="unexpected type bug"):
        convert(source)


def test_convert_dir_returns_results_for_all_eml(copy_fixture, tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir(parents=True, exist_ok=True)

    copy_fixture("plain_text.eml", "in/a/plain_text.eml")
    copy_fixture("html_only.eml", "in/b/html_only.eml")

    results = convert_dir(input_dir)

    assert len(results) == 2
    assert all(isinstance(item, ConvertResult) for item in results)
    assert {item.source.name for item in results} == {"plain_text.eml", "html_only.eml"}


def test_convert_keeps_cid_placeholders_by_default(copy_fixture) -> None:
    source = copy_fixture("with_inline_cid.eml")

    result = convert(source)

    assert result.output is not None
    document = result.output.read_text(encoding="utf-8")
    assert "(cid:image1)" in document
    assert "data:image/png;base64" not in document


def test_convert_embed_inline_images_rewrites_cid_to_data_uri(copy_fixture) -> None:
    source = copy_fixture("with_inline_cid.eml")

    result = convert(source, options=ConvertOptions(embed_inline_images=True))

    assert result.output is not None
    document = result.output.read_text(encoding="utf-8")
    assert "(cid:image1)" not in document
    assert "data:image/png;base64," in document
