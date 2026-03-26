from __future__ import annotations

from pathlib import Path

import yaml

from dead_letter.core import ConvertOptions, convert


def _front_matter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    end = text.find("\n---\n", 4)
    assert end != -1
    return yaml.safe_load(text[4:end])


def test_front_matter_includes_required_keys(copy_fixture) -> None:
    source = copy_fixture("plain_text.eml")

    result = convert(source)

    assert result.output is not None
    front = _front_matter(result.output)
    assert {"source", "subject", "sender", "date", "attachments"}.issubset(front.keys())


def test_include_all_headers_adds_headers_block(copy_fixture) -> None:
    source = copy_fixture("plain_text.eml")

    result = convert(source, options=ConvertOptions(include_all_headers=True))

    assert result.output is not None
    front = _front_matter(result.output)
    assert "headers" in front
    assert isinstance(front["headers"], dict)
    assert "Subject" in front["headers"]


def test_include_raw_html_adds_raw_html_for_html_messages(copy_fixture) -> None:
    source = copy_fixture("html_only.eml")

    result = convert(source, options=ConvertOptions(include_raw_html=True))

    assert result.output is not None
    front = _front_matter(result.output)
    assert "raw_html" in front
    assert isinstance(front["raw_html"], str)
    assert "<" in front["raw_html"]


def test_calendar_summary_toggle(copy_fixture) -> None:
    source = copy_fixture("calendar_invite.eml")

    with_summary = convert(source)
    assert with_summary.output is not None
    front_with = _front_matter(with_summary.output)
    assert "calendar" in front_with
    assert isinstance(front_with["calendar"], list)

    source2 = copy_fixture("calendar_invite.eml", "calendar_2.eml")
    without_summary = convert(source2, options=ConvertOptions(no_calendar_summary=True))
    assert without_summary.output is not None
    front_without = _front_matter(without_summary.output)
    assert "calendar" not in front_without
