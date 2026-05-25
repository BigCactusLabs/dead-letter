from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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


def test_quote_detection_does_not_require_html_to_markdown_visitor(tmp_path: Path, monkeypatch) -> None:
    fake_package = tmp_path / "html_to_markdown"
    fake_package.mkdir()
    (fake_package / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "html_to_markdown", raising=False)

    quotes_path = Path(__file__).parents[2] / "src" / "dead_letter" / "core" / "quotes.py"
    spec = importlib.util.spec_from_file_location("dead_letter_quotes_without_visitor", quotes_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    patterns = module.detect_quote_patterns('<div class="gmail_quote">Quoted</div>')

    assert "gmail" in patterns
