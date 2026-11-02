"""Compatibility and killable cost checks for the English reply reader."""

import inspect
import random
import subprocess
import sys
import textwrap
import warnings

import pytest
from mailparser_reply import EmailReplyParser
from mailparser_reply.parser import EmailMessage


_UPSTREAM_HEADER_PATTERN = (
    r"^(?!On[.\s]*On\s(.+?\s?.+?)\swrote:)((?:> ?)*On\s(?:.+?\s?.+?)\s?wrote:)$|"
    r"((?:(?:^|\n|\n(?:> ?)*)[* ]*(?:From|Sent|To|Subject|Date|Cc|Organization):"
    r"[ *]*(?:\s{,2}).*){2,}(?:\n.*){,1})|(^-{5,} ?Original Message ?-{5,}$)"
)


def test_upstream_reply_parser_fingerprint():
    reason = "mail-parser-reply changed: re-derive the linear override before upgrading"
    assert EmailMessage(text="", languages=["en"]).HEADER_REGEX.pattern == _UPSTREAM_HEADER_PATTERN, reason
    method = getattr(EmailMessage, "_process_signatures_disclaimers", None)
    assert method is not None, reason
    assert str(inspect.signature(method)) == "(self, text: str) -> Tuple[List[str], str]", reason
    assert EmailReplyParser().languages == ["en"], reason


def test_bounded_reader_matches_upstream_generated_corpus():
    from dead_letter.core.text_conversation import _BoundedEmailMessage, _LINEAR_HEADER_RE

    tokens = [
        "On", "ON", "oN", " ", "  ", "\n", "\n\n", "wrote:", "WrOtE:",
        "x", "ab", ">", "> ", "> >", ".", "*", "* ", "From:", "SENT:",
        "To:", "Subject:", "Date:", "Cc:", "Organization:", "\t", "\r\n",
        "\u2028", "\x85", "\x0b", "\x1c", "\u00a0", "On On ", "_" * 32,
        "-----Original Message-----", "------ Original Message -----", "CAUTION:", "Thanks,",
    ]
    rng = random.Random(268)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        corpus = [
            "Hi\n\nOn Tue Alice wrote:\n> earlier",
            "Hi\nOn x y\nwrote:\nearlier",
            "Hi\nOn On x y wrote:\nearlier",
            "Hi\nFrom: Alice\nSent: Monday\nTo: Bob\nSubject: X\nBody",
            "Hi\nFrom:**\nSENT: \nTo:x\nBody",
            "Hi\n\nThanks,\nAlice\nCAUTION: this mail is private",
        ]
        corpus.extend(
            "".join(rng.choice(tokens) for _ in range(rng.randint(1, 80)))
            for _ in range(3000)
        )
        for text in corpus:
            upstream = EmailReplyParser().read(text)
            bounded = _BoundedEmailMessage(
                text=text, languages=["en"], _header_regex=_LINEAR_HEADER_RE,
            ).read()
            assert [(r.content, r.headers) for r in bounded.replies] == [
                (r.content, r.headers) for r in upstream.replies
            ], repr(text)


_CONVERSION_SCRIPT = textwrap.dedent("""\
    from email.message import EmailMessage
    from pathlib import Path
    import sys
    import time

    from dead_letter import convert
    from dead_letter.core.types import ConvertOptions, ThreadMode

    directory = Path(sys.argv[1])
    shape = sys.argv[2]
    candidates = {
        'on_line': 'On ' + 'x' * 32000,
        'on_words': 'Hi\\n\\nOn ' + 'x y ' * 8000,
        'on_lines': '\\n'.join('On ' + 'x' * 4096 for _ in range(20)),
        'from_stars': 'padding ' * 2000 + '\\nFrom: ' + '*' * 16000 + 'x',
        'disclaimers': 'CAUTION:.' * 8000,
        'newlines': 'before blank run' + '\\n' * 32000 + 'after blank run',
    }
    if shape == 'html_pre':
        body = (
            '<html><body><p>AUTHORED_START</p><div id="divRplyFwdMsg">'
            '<b>From:</b> A &lt;a@example.test&gt;<br><b>Sent:</b> Monday<br>'
            '<b>To:</b> B<br><b>Subject:</b> synthetic</div><div><pre>'
            + '\\n' * 32000 + 'AUTHORED_END</pre></div></body></html>'
        )
        subtype = 'html'
    else:
        candidate = candidates[shape]
        body = 'AUTHORED_START\\n' + candidate + '\\nAUTHORED_END'
        subtype = 'plain'
    message = EmailMessage()
    message['From'] = 'synthetic@example.test'
    message['Subject'] = 'Reply parser cost regression'
    message.set_content(body, subtype=subtype)
    source, output = directory / (shape + '.eml'), directory / (shape + '.md')
    source.write_bytes(bytes(message))
    started = time.perf_counter()
    result = convert(source, output=output, options=ConvertOptions(thread_mode=ThreadMode.STRUCTURED))
    elapsed = time.perf_counter() - started
    assert result.success, result.error
    rendered = output.read_text()
    for sentinel in ('AUTHORED_START', 'AUTHORED_END'):
        assert rendered.count(sentinel) == 1, sentinel
    if shape != 'html_pre':
        # Markdown escapes and blank-line normalization do not remove authored text.
        compact = lambda value: ''.join(value.replace('\\\\', '').split())
        assert compact(candidate) in compact(rendered), shape
    print(f'{shape}: {elapsed:.6f}s; content preserved', flush=True)
    if len(sys.argv) < 4:
        assert elapsed < 2, elapsed
""")


@pytest.mark.parametrize("shape", [
    "on_line", "on_words", "on_lines", "from_stars", "disclaimers", "newlines", "html_pre",
])
def test_reply_parser_conversion_cost_in_killable_process(tmp_path, shape):
    result = subprocess.run(
        [sys.executable, "-c", _CONVERSION_SCRIPT, str(tmp_path), shape],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "content preserved" in result.stdout
