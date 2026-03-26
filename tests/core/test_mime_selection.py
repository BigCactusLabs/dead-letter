from __future__ import annotations

from dead_letter.core.mime_selection import build_mime_model, select_body_candidate
from dead_letter.core.types import PartDefect


def test_select_body_candidate_prefers_html_over_plain_when_available() -> None:
    model = build_mime_model(
        text_body="plain body",
        html_bodies=["<p>html body</p>"],
        defects=[],
    )

    selected = select_body_candidate(model)

    assert selected.kind == "html"
    assert selected.content == "<p>html body</p>"
    assert model.selected_body_id == "html-1"


def test_select_body_candidate_falls_back_to_plain_when_html_missing() -> None:
    model = build_mime_model(
        text_body="plain body",
        html_bodies=[],
        defects=[PartDefect(part_id="root", code="missing_html", message="missing", severity="warning")],
    )

    selected = select_body_candidate(model)

    assert selected.kind == "plain"
    assert selected.content == "plain body"
    assert model.selected_body_id == "plain-1"


def test_select_body_candidate_prefers_last_html_alternative() -> None:
    model = build_mime_model(
        text_body="plain body",
        html_bodies=["<p>older html</p>", "<p>preferred html</p>"],
        defects=[],
    )

    selected = select_body_candidate(model)

    assert selected.kind == "html"
    assert selected.content == "<p>preferred html</p>"
    assert model.selected_body_id == "html-2"
