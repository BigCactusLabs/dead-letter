from __future__ import annotations

from dead_letter.core.quotes import detect_quote_patterns


def test_detect_quote_patterns_gmail() -> None:
    html = '<div>reply</div><div class="gmail_quote">quoted</div>'

    patterns = detect_quote_patterns(html)

    assert "gmail" in patterns


def test_detect_quote_patterns_outlook() -> None:
    html = '<div id="divRplyFwdMsg">original</div><span id="OLK_SRC_BODY_SECTION">legacy</span>'

    patterns = detect_quote_patterns(html)

    assert "outlook" in patterns


def test_detect_quote_patterns_type_cite_marks_mail_clients() -> None:
    html = '<blockquote type="cite">old</blockquote>'

    patterns = detect_quote_patterns(html)

    assert "thunderbird" in patterns
    assert "apple_mail" in patterns


def test_detect_quote_patterns_yahoo_and_generic_rules() -> None:
    html = '<div class="yahoo_quoted">y</div><blockquote>q</blockquote><p>On Thu someone wrote:</p>'

    patterns = detect_quote_patterns(html)

    assert "yahoo" in patterns
    assert "generic" in patterns
