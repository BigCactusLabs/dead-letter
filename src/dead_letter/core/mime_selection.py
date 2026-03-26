"""Internal MIME body candidate selection helpers."""

from __future__ import annotations

from dead_letter.core.types import BodyCandidate, MimeModel, PartDefect


def build_mime_model(
    *,
    text_body: str,
    html_bodies: list[str],
    defects: list[PartDefect],
) -> MimeModel:
    """Build an internal MIME model from normalized plain/html body inputs."""
    candidates: list[BodyCandidate] = []

    for index, html in enumerate(html_bodies, start=1):
        if html.strip():
            candidates.append(
                BodyCandidate(
                    kind="html",
                    content=html,
                    source_part_id=f"html-{index}",
                )
            )

    if text_body.strip():
        candidates.append(
            BodyCandidate(
                kind="plain",
                content=text_body,
                source_part_id="plain-1",
            )
        )

    model = MimeModel(
        parts=[candidate.source_part_id for candidate in candidates],
        defects=defects,
        body_candidates=candidates,
    )
    if candidates:
        model.selected_body_id = select_body_candidate(model).source_part_id
    return model


def select_body_candidate(model: MimeModel) -> BodyCandidate:
    """Choose the best available body candidate from the MIME model."""
    for candidate in reversed(model.body_candidates):
        if candidate.kind == "html" and candidate.content.strip():
            model.selected_body_id = candidate.source_part_id
            return candidate

    for candidate in reversed(model.body_candidates):
        if candidate.kind == "plain" and candidate.content.strip():
            model.selected_body_id = candidate.source_part_id
            return candidate

    raise ValueError("no usable body candidate")
