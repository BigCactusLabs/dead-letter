from __future__ import annotations

from pathlib import Path
import time

import dead_letter.core.html as html_mod
from dead_letter.core._pipeline import _build_rendered_markdown
from dead_letter.core import convert, convert_to_bundle
from dead_letter.core.render import serialize_markdown
from dead_letter.core.types import ConvertOptions, ParsedEmail

FIXTURES = Path("tests/core/fixtures")


def _write_html_email(path: Path, html: str) -> Path:
    path.write_text(
        "\n".join(
            [
                "From: Test <test@example.com>",
                "To: Example <example@example.com>",
                "Subject: HTML Quote Fixture",
                "Date: Thu, 05 Mar 2026 10:20:00 +0000",
                "MIME-Version: 1.0",
                "Content-Type: text/html; charset=utf-8",
                "",
                html,
            ]
        ),
        encoding="utf-8",
    )
    return path


def _write_plain_email(path: Path, plain: str) -> Path:
    path.write_text(
        "\n".join(
            [
                "From: Test <test@example.com>",
                "To: Example <example@example.com>",
                "Subject: Plain Fixture",
                "Date: Thu, 05 Mar 2026 10:20:00 +0000",
                "MIME-Version: 1.0",
                "Content-Type: text/plain; charset=utf-8",
                "",
                plain,
            ]
        ),
        encoding="utf-8",
    )
    return path


def _write_multipart_email(path: Path, plain: str, html: str) -> Path:
    boundary = "dead-letter-boundary"
    path.write_text(
        "\n".join(
            [
                "From: Test <test@example.com>",
                "To: Example <example@example.com>",
                "Subject: HTML Fallback Fixture",
                "Date: Thu, 05 Mar 2026 10:20:00 +0000",
                "MIME-Version: 1.0",
                f'Content-Type: multipart/alternative; boundary="{boundary}"',
                "",
                f"--{boundary}",
                'Content-Type: text/plain; charset="utf-8"',
                "",
                plain,
                f"--{boundary}",
                'Content-Type: text/html; charset="utf-8"',
                "",
                html,
                f"--{boundary}--",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _html_markdown_panic_fixture() -> str:
    return (
        "<div>"
        "<p><span>&nbsp; &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</span>"
        '<img src="cid:ii_19cb59e9425692e331"><span></span></p>'
        "<p><i><span>Whole Foods Market remains committed to supply chain transparency and ethical sourcing.\n"
        "</span></i><span></span></p>"
        "</div>"
    )


def test_convert_gmail_quote_keeps_latest_reply_only(tmp_path: Path) -> None:
    result = convert(FIXTURES / "gmail_quote.eml", output=tmp_path)

    assert result.success is True
    assert result.output is not None

    body = result.output.read_text(encoding="utf-8")
    assert "Latest response" in body
    assert "On prior mail wrote" not in body


def test_large_prose_html_default_conversion_avoids_quote_regex_backtracking(tmp_path: Path) -> None:
    phrase = "The team met on Tuesday to review the plan and agreed on the next steps. "
    prose = (phrase * ((512 * 1024 // len(phrase)) + 1))[: 512 * 1024]
    source = _write_html_email(tmp_path / "large_prose.eml", f"<html><body>{prose}</body></html>")

    started = time.perf_counter()
    result = convert(source, output=tmp_path / "out.md")

    elapsed = time.perf_counter() - started
    assert result.success
    assert elapsed < 3


def test_convert_outlook_quote_keeps_top_reply_only(tmp_path: Path) -> None:
    result = convert(FIXTURES / "outlook_quote.eml", output=tmp_path)

    assert result.success is True
    assert result.output is not None

    body = result.output.read_text(encoding="utf-8")
    assert "Top reply" in body
    assert "Original message content" not in body


def test_convert_outlook_nested_quote_keeps_top_reply_only(tmp_path: Path) -> None:
    source = _write_html_email(
        tmp_path / "outlook_nested_quote.eml",
        "<html><body>"
        "<div>Top reply</div>"
        '<table><tr><td><div id="divRplyFwdMsg">From: Person</div></td></tr></table>'
        "<table><tr><td>Older thread line 1</td></tr></table>"
        "<table><tr><td>Older thread line 2</td></tr></table>"
        "</body></html>",
    )

    result = convert(source, output=tmp_path / "out")

    assert result.success is True
    assert result.output is not None

    body = result.output.read_text(encoding="utf-8")
    assert "Top reply" in body
    assert "Older thread line 1" not in body
    assert "Older thread line 2" not in body


def test_ambiguous_boundary_prefers_preserving_content(tmp_path: Path) -> None:
    result = convert(FIXTURES / "reply_chain.eml", output=tmp_path)

    assert result.success is True
    assert result.output is not None

    body = result.output.read_text(encoding="utf-8")
    assert "Reply level 2." in body


def test_convert_forwarded_email_preserves_forwarded_message_content(tmp_path: Path) -> None:
    result = convert(FIXTURES / "forwarded.eml", output=tmp_path)

    assert result.success is True
    assert result.output is not None

    body = result.output.read_text(encoding="utf-8")
    assert "Vendor &lt;vendor@example.net&gt;" in body
    assert "Please review the attached quote." in body


def test_convert_gmail_html_path_preserves_markdown_formatting(tmp_path: Path) -> None:
    source = _write_html_email(
        tmp_path / "rich_gmail.eml",
        '<div><strong>Hello</strong></div><ul><li>One</li><li>Two</li></ul>'
        '<div class="gmail_quote"><p>Quoted</p></div>',
    )

    result = convert(source, output=tmp_path / "out")

    assert result.success is True
    assert result.output is not None

    body = result.output.read_text(encoding="utf-8")
    assert "**Hello**" in body
    assert "- One" in body
    assert "- Two" in body
    assert "Quoted" not in body


def test_plain_text_path_reports_plain_text_reply_fallback() -> None:
    _result, _parsed, _rendered, diagnostics = _build_rendered_markdown(
        FIXTURES / "plain_text.eml",
        ConvertOptions(),
    )

    assert diagnostics is not None
    assert diagnostics["segmentation_path"] == "plain_fallback"
    assert diagnostics["fallback_used"] == "plain_text_reply_parser"


def test_plain_text_conversion_escapes_html_like_payloads(tmp_path: Path) -> None:
    source = _write_plain_email(
        tmp_path / "plain_payload.eml",
        "<script>alert(1)</script>\n<img src=x onerror=alert(2)>",
    )

    result = convert(source, output=tmp_path / "out")

    assert result.success is True
    assert result.output is not None
    text = result.output.read_text(encoding="utf-8")
    assert "<script>" not in text
    assert "<img" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "&lt;img src=x onerror=alert(2)&gt;" in text


def test_plain_text_conversion_preserves_markdown_code_regions(tmp_path: Path) -> None:
    source = _write_plain_email(
        tmp_path / "plain_code_payload.eml",
        (
            "Inline `<div>` stays literal.\n\n"
            "```html\n"
            "<script>alert('in code')</script>\n"
            "<div>fixture</div>\n"
            "```\n\n"
            "Outside code is still <img src=x onerror=alert(2)>."
        ),
    )

    result = convert(source, output=tmp_path / "out")

    assert result.success is True
    assert result.output is not None
    text = result.output.read_text(encoding="utf-8")
    assert "`<div>`" in text
    assert "```html\n<script>alert('in code')</script>\n<div>fixture</div>\n```" in text
    assert "&lt;img src=x onerror=alert(2)&gt;" in text
    assert "<img" not in text


def test_attachment_reference_without_attachments_emits_warning(tmp_path: Path) -> None:
    source = _write_plain_email(
        tmp_path / "missing_attachments.eml",
        "Please find attached the signed agreement.",
    )

    _result, _parsed, _rendered, diagnostics = _build_rendered_markdown(source, ConvertOptions())

    assert diagnostics is not None
    assert diagnostics["state"] == "degraded"
    assert any(
        warning["code"] == "attachment_reference_without_attachments"
        for warning in diagnostics["warnings"]
    )


def test_attachment_reference_without_attachments_does_not_warn_when_attachment_present() -> None:
    _result, _parsed, _rendered, diagnostics = _build_rendered_markdown(
        FIXTURES / "with_attachment.eml",
        ConvertOptions(),
    )

    assert diagnostics is not None
    assert not any(
        warning["code"] == "attachment_reference_without_attachments"
        for warning in diagnostics["warnings"]
    )


def test_html_quote_pattern_fallback_marks_review_recommended(tmp_path: Path) -> None:
    source = _write_html_email(
        tmp_path / "cite_quote.eml",
        "<div>Latest response</div><blockquote type=\"cite\">Older message</blockquote>",
    )

    _result, _parsed, _rendered, diagnostics = _build_rendered_markdown(source, ConvertOptions())

    assert diagnostics is not None
    assert diagnostics["state"] == "review_recommended"
    assert diagnostics["segmentation_path"] == "plain_fallback"
    assert diagnostics["fallback_used"] == "plain_text_reply_parser"


def test_front_blockquote_uses_high_confidence_html_segmentation(tmp_path: Path) -> None:
    source = _write_html_email(
        tmp_path / "front_quote.eml",
        (
            "<div>Latest Front response</div>"
            '<blockquote type="cite" class="front-blockquote">Older Front message</blockquote>'
        ),
    )

    _result, _parsed, rendered, diagnostics = _build_rendered_markdown(source, ConvertOptions())

    assert diagnostics is not None
    assert diagnostics["state"] == "normal"
    assert diagnostics["segmentation_path"] == "html"
    assert diagnostics["client_hint"] == "front"
    assert diagnostics["confidence"] == "high"
    assert diagnostics["fallback_used"] is None
    body = serialize_markdown(rendered)
    assert "Latest Front response" in body
    assert "Older Front message" not in body


def test_front_blockquote_preserves_authored_trailing_content(tmp_path: Path) -> None:
    source = _write_html_email(
        tmp_path / "front_quote_with_trailing_content.eml",
        (
            "<div>Latest Front response</div>"
            '<blockquote type="cite" class="front-blockquote">Older Front message</blockquote>'
            "<div>Author note after quote</div>"
        ),
    )

    _result, _parsed, rendered, diagnostics = _build_rendered_markdown(source, ConvertOptions())

    assert diagnostics is not None
    assert diagnostics["state"] == "normal"
    assert diagnostics["segmentation_path"] == "html"
    assert diagnostics["client_hint"] == "front"
    body = serialize_markdown(rendered)
    assert "Latest Front response" in body
    assert "Author note after quote" in body
    assert "Older Front message" not in body


def test_front_blockquote_handles_nested_signature_disclaimer_and_images(tmp_path: Path) -> None:
    source = _write_html_email(
        tmp_path / "front_nested_signature_disclaimer_images.eml",
        (
            "<div>Latest Front response</div>"
            '<div class="front-signature">'
            '<img src="https://survey.frontapp.com/assets/star.png" alt="Survey">'
            "</div>"
            '<img alt="Sent from Front" style="width:1px;height:1px" '
            'src="https://app.frontapp.com/api/1/noauth/seen/msg.gif">'
            "<br>"
            '<blockquote type="cite" class="fa-731mdn fanx2yle front-blockquote">'
            "On November 30, 2023 someone wrote:"
            '<div id="divRplyFwdMsg">Nested Outlook quote header</div>'
            "<div>Older disclaimer text</div>"
            '<img src="cid:inline-photo" alt="Inline quoted image">'
            '<blockquote type="cite" class="front-blockquote">Nested Front quote</blockquote>'
            "</blockquote>"
        ),
    )

    _result, _parsed, rendered, diagnostics = _build_rendered_markdown(
        source,
        ConvertOptions(strip_signature_images=True, strip_tracking_pixels=True),
    )

    assert diagnostics is not None
    assert diagnostics["state"] == "normal"
    assert diagnostics["segmentation_path"] == "html"
    assert diagnostics["client_hint"] == "front"
    stripped_reasons = {item["reason"] for item in diagnostics["stripped_images"]}
    assert "front_signature_wrapper" in stripped_reasons
    assert stripped_reasons & {"dimension_heuristic", "structural_boundary_extension"}
    body = serialize_markdown(rendered)
    assert "Latest Front response" in body
    assert "Nested Outlook quote header" not in body
    assert "Older disclaimer text" not in body
    assert "Nested Front quote" not in body
    assert "Inline quoted image" not in body


def test_html_conversion_error_hard_fails_by_default(tmp_path: Path, monkeypatch) -> None:
    source = _write_multipart_email(
        tmp_path / "html_failure.eml",
        "Plain fallback body",
        "<div>Rich HTML body</div>",
    )

    def raising_html_to_markdown(*_args, **_kwargs):
        raise RuntimeError("html-to-markdown panic during conversion: byte index is out of bounds")

    monkeypatch.setattr("dead_letter.core._pipeline.html_to_markdown", raising_html_to_markdown)

    result = convert(source, output=tmp_path / "out")

    assert result.success is False
    assert result.output is None
    assert result.error is not None
    assert result.error_code == "html_markdown_failed"
    assert result.plain_text_fallback_available is True
    assert "html-to-markdown panic during conversion" in result.error


def test_html_conversion_error_can_fallback_to_plain_text_when_enabled(
    tmp_path: Path, monkeypatch
) -> None:
    source = _write_multipart_email(
        tmp_path / "html_failure_fallback.eml",
        "Plain fallback body",
        "<div>Rich HTML body</div>",
    )

    def raising_html_to_markdown(*_args, **_kwargs):
        raise RuntimeError("html-to-markdown panic during conversion: byte index is out of bounds")

    monkeypatch.setattr("dead_letter.core._pipeline.html_to_markdown", raising_html_to_markdown)

    result, _parsed, rendered, diagnostics = _build_rendered_markdown(
        source,
        ConvertOptions(allow_fallback_on_html_error=True),
    )

    assert result.success is True
    assert "Plain fallback body" in serialize_markdown(rendered)
    assert diagnostics is not None
    assert diagnostics["segmentation_path"] == "plain_fallback"
    assert diagnostics["fallback_used"] == "html_failure_plain_text_fallback"
    assert diagnostics["warnings"][0]["code"] == "html_markdown_failed"


def test_html_conversion_error_override_still_fails_without_plain_text(tmp_path: Path, monkeypatch) -> None:
    source = _write_html_email(
        tmp_path / "html_only_failure.eml",
        "<div>Rich HTML body</div>",
    )

    def raising_html_to_markdown(*_args, **_kwargs):
        raise RuntimeError("html-to-markdown panic during conversion: byte index is out of bounds")

    monkeypatch.setattr("dead_letter.core._pipeline.html_to_markdown", raising_html_to_markdown)
    monkeypatch.setattr(
        "dead_letter.core._pipeline.parse_eml",
        lambda _source, **_kwargs: ParsedEmail(
            source=source,
            subject="HTML only",
            sender="test@example.com",
            date="2026-03-05T10:20:00+00:00",
            text_body="",
            html_body="<div>Rich HTML body</div>",
            headers={},
            attachments=[],
            selected_body_kind="html",
        ),
    )

    result = convert(
        source,
        output=tmp_path / "out",
        options=ConvertOptions(allow_fallback_on_html_error=True),
    )

    assert result.success is False
    assert result.error is not None
    assert result.error_code == "html_markdown_failed"
    assert result.plain_text_fallback_available is False
    assert "html-to-markdown panic during conversion" in result.error


def test_html_conversion_error_reports_html_repair_available_for_known_panic_fixture(
    tmp_path: Path, monkeypatch
) -> None:
    source = _write_multipart_email(
        tmp_path / "html_repair_available.eml",
        "Plain fallback body",
        _html_markdown_panic_fixture(),
    )

    def raising_on_italic_html(html: str, *, include_raw_html: bool = False):
        if "<i>" in html:
            raise RuntimeError("html-to-markdown panic during conversion: byte index is out of bounds")
        return html_mod.html_to_markdown(html, include_raw_html=include_raw_html)

    monkeypatch.setattr("dead_letter.core._pipeline.html_to_markdown", raising_on_italic_html)

    result = convert(source, output=tmp_path / "out")

    assert result.success is False
    assert result.error_code == "html_markdown_failed"
    assert result.plain_text_fallback_available is True
    assert result.html_repair_available is True


def test_html_conversion_error_can_retry_with_html_repair_when_enabled(tmp_path: Path, monkeypatch) -> None:
    source = _write_html_email(
        tmp_path / "html_repair_enabled.eml",
        _html_markdown_panic_fixture(),
    )

    def raising_on_italic_html(html: str, *, include_raw_html: bool = False):
        if "<i>" in html:
            raise RuntimeError("html-to-markdown panic during conversion: byte index is out of bounds")
        return html_mod.html_to_markdown(html, include_raw_html=include_raw_html)

    monkeypatch.setattr("dead_letter.core._pipeline.html_to_markdown", raising_on_italic_html)

    result, _parsed, rendered, diagnostics = _build_rendered_markdown(
        source,
        ConvertOptions(allow_html_repair_on_panic=True),
    )

    assert result.success is True
    body = serialize_markdown(rendered)
    assert "Whole Foods Market remains committed" in body
    assert "*Whole Foods Market remains committed" not in body
    assert diagnostics is not None
    assert diagnostics["selected_body"] == "html"
    assert diagnostics["segmentation_path"] == "html"
    assert diagnostics["fallback_used"] == "html_markdown_panic_repaired"
    assert diagnostics["warnings"][0]["code"] == "html_markdown_repaired"


def test_html_repair_retry_still_runs_when_conversation_segmentation_panics_first(
    tmp_path: Path, monkeypatch
) -> None:
    source = _write_html_email(
        tmp_path / "html_repair_after_segmentation_failure.eml",
        _html_markdown_panic_fixture(),
    )

    monkeypatch.setattr(
        "dead_letter.core._pipeline._threaded_content_from_conversation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("html-to-markdown panic during conversion: byte index is out of bounds")
        ),
    )

    result, _parsed, rendered, diagnostics = _build_rendered_markdown(
        source,
        ConvertOptions(allow_html_repair_on_panic=True),
    )

    assert result.success is True
    body = serialize_markdown(rendered)
    assert "Whole Foods Market remains committed" in body
    assert diagnostics is not None
    assert diagnostics["fallback_used"] == "html_markdown_panic_repaired"


def test_strip_signatures_works_on_html_conversation_path(tmp_path: Path) -> None:
    """Regression: strip_signatures must work on HTML emails going through the HTML conversation path.

    Previously cleanup_zones ran plaintext regexes on raw HTML content before the
    HTML-to-markdown conversion, so the patterns never matched HTML tags.
    """
    html = (
        "<div>Thanks for the update.</div>"
        "<div>-- </div>"
        "<div>Alice Smith</div>"
        "<div>Senior Engineer</div>"
        '<div class="gmail_quote">'
        "<div>On Mon, Mar 30, 2026 at 10:00 AM Bob wrote:</div>"
        "<div>Here is the latest report.</div>"
        "</div>"
    )
    source = _write_html_email(tmp_path / "sig.eml", html)
    result, _parsed, rendered, _diag = _build_rendered_markdown(
        source, ConvertOptions(strip_signatures=True)
    )
    assert result.success
    body = serialize_markdown(rendered)
    assert "Thanks for the update" in body
    assert "Alice Smith" not in body
    assert "Senior Engineer" not in body


def test_empty_html_body_preserves_plain_text_without_html_quote_patterns(tmp_path: Path) -> None:
    """Regression: when HTML body is empty, plain text body must be used without HTML quote patterns."""
    plain_text = "Hello team\n\nThis is the plain text body.\n"
    html_body = "<html><body>   </body></html>"

    eml = _write_multipart_email(tmp_path / "test.eml", plain_text, html_body)
    result = convert_to_bundle(eml, bundle_root=tmp_path / "output", options=ConvertOptions())
    assert result.success
    md = result.markdown.read_text()
    assert "Hello team" in md
    assert "plain text body" in md


from dead_letter.core.types import ThreadMode


def test_gmail_quote_same_line_structured_does_not_backtrack(tmp_path: Path) -> None:
    source = tmp_path / "gmail_quote_same_line.eml"
    # write_bytes, not write_text: the byte count below is part of the reported
    # repro and must not shift with a platform's newline translation.
    source.write_bytes(
        b"From: Bob <bob@example.com>\n"
        b"To: Alice <alice@example.com>\n"
        b"Subject: Re: Launch plan\n"
        b"Date: Thu, 5 Mar 2026 11:00:00 +0000\n"
        b"MIME-Version: 1.0\n"
        b'Content-Type: text/html; charset="utf-8"\n'
        b"\n"
        b"<html><body>\n"
        b"<div>Sounds good to me.</div>\n"
        b'<div class="gmail_quote">On Thu, Mar 5, 2026 at 10:23 AM Alice Smith wrote: '
        b"I think we should ship it Friday.</div>\n"
        b"</body></html>\n"
    )
    assert source.stat().st_size == 354

    started = time.perf_counter()
    result = convert(
        source,
        output=tmp_path / "out.md",
        options=ConvertOptions(thread_mode=ThreadMode.STRUCTURED),
    )

    elapsed = time.perf_counter() - started
    assert result.success
    assert elapsed < 2


def test_gmail_3_message_thread_structured(tmp_path) -> None:
    fixture = FIXTURES / "gmail_3_message_thread.eml"
    output = tmp_path / "out.md"
    result = convert(
        fixture,
        output=output,
        options=ConvertOptions(thread_mode=ThreadMode.STRUCTURED),
    )

    assert result.success
    text = output.read_text(encoding="utf-8")
    parts = text.split("---\n", 2)
    assert len(parts) == 3
    body = parts[2]

    assert "Reply level 2 from Carol." in body
    assert "## From Bob" in body
    assert "## From Alice" in body
    leaked = [line for line in body.splitlines() if line.startswith(">")]
    assert not leaked, f"residual quote markers in body: {leaked!r}"
    assert "thread_messages: 2" in text


def test_gmail_3_message_thread_latest_byte_identical_to_baseline(tmp_path) -> None:
    fixture = FIXTURES / "gmail_3_message_thread.eml"
    baseline_out = tmp_path / "baseline.md"
    latest_out = tmp_path / "latest.md"

    convert(fixture, output=baseline_out, options=ConvertOptions())
    convert(fixture, output=latest_out, options=ConvertOptions(thread_mode=ThreadMode.LATEST))

    assert baseline_out.read_text() == latest_out.read_text()


def test_outlook_dom_segmented_thread_structured(tmp_path) -> None:
    fixture = FIXTURES / "outlook_dom_segmented_thread.eml"
    output = tmp_path / "out.md"
    result = convert(
        fixture,
        output=output,
        options=ConvertOptions(thread_mode=ThreadMode.STRUCTURED),
    )

    assert result.success
    text = output.read_text(encoding="utf-8")
    parts = text.split("---\n", 2)
    assert len(parts) == 3, "expected YAML front matter delimited by ---"
    body = parts[2]

    assert "thread_messages: 2" in text
    assert "Carol latest reply" in body
    assert "## From Bob" in body
    assert "## From Alice" in body
    assert "---" not in body, "stray --- in body suggests broken Outlook projection"
    assert "**From:**" not in body


def test_generic_html_thread_via_plain_fallback_structured(tmp_path) -> None:
    fixture = FIXTURES / "generic_html_thread.eml"
    output = tmp_path / "out.md"
    result = convert(
        fixture,
        output=output,
        options=ConvertOptions(thread_mode=ThreadMode.STRUCTURED),
    )

    assert result.success
    text = output.read_text(encoding="utf-8")
    assert "Carol latest reply" in text


def test_mixed_attribution_thread_renders_earlier_message_for_unparseable(tmp_path) -> None:
    fixture = FIXTURES / "mixed_attribution_thread.eml"
    output = tmp_path / "out.md"
    convert(
        fixture,
        output=output,
        options=ConvertOptions(thread_mode=ThreadMode.STRUCTURED),
    )

    text = output.read_text(encoding="utf-8")
    assert "## Earlier message" in text or "## From Bob" in text


def test_multilingual_thread_structured(tmp_path) -> None:
    fixture = FIXTURES / "multilingual_thread.eml"
    output = tmp_path / "out.md"
    convert(
        fixture,
        output=output,
        options=ConvertOptions(thread_mode=ThreadMode.STRUCTURED),
    )

    text = output.read_text(encoding="utf-8")
    assert "Bob" in text


def test_outlook_with_cc_thread_parses_attribution(tmp_path) -> None:
    fixture = FIXTURES / "outlook_with_cc_thread.eml"
    output = tmp_path / "out.md"
    result = convert(
        fixture,
        output=output,
        options=ConvertOptions(thread_mode=ThreadMode.STRUCTURED),
    )

    assert result.success
    text = output.read_text(encoding="utf-8")
    parts = text.split("---\n", 2)
    assert len(parts) == 3
    body = parts[2]

    assert "## From Bob" in body
    assert "**From:**" not in body
    assert "**Cc:**" not in body
    assert "**Subject:**" not in body
    assert "Bob reply text with cc." in body


def test_empty_quoted_attribution_skips_empty_section(tmp_path) -> None:
    fixture = FIXTURES / "empty_quoted_attribution.eml"
    output = tmp_path / "out.md"
    convert(
        fixture,
        output=output,
        options=ConvertOptions(thread_mode=ThreadMode.STRUCTURED),
    )

    text = output.read_text(encoding="utf-8")
    assert "Carol latest reply" in text
    assert "thread_messages: 0" in text or "thread_messages:" not in text
