"""Forwarded-message markers shared by the HTML and plain-text segmenters.

Sources for the marker strings (Gmail, Thunderbird/Yahoo, Apple Mail) are
recorded in docs/reference/v4-runtime-contracts.md under forwarded content.
"""

from __future__ import annotations

import re

# Localized Thunderbird/Yahoo separator labels, written between dash runs.
# Add a locale by appending one entry.
_DASHED_FORWARD_LABELS: tuple[str, ...] = (
    r"Forwarded Message",
    r"Weitergeleitete Nachricht",
    r"Mensaje reenviado",
    r"Message transféré",
    r"Message transmis",
    r"Messaggio inoltrato",
    r"Doorgestuurd bericht",
    r"Mensagem encaminhada",
    r"Mensagem reencaminhada",
    r"Vidarebefordrat meddelande",
    r"Videresendt (?:meddelelse|melding)",
    r"Přeposlaná zpráva",
    r"Továbbított üzenet",
    r"Edelleenlähetetty viesti",
    r"Välitetty viesti / Fwd\.Msg",
    r"Proslijeđena poruka",
    r"Treść przekazanej wiadomości",
    r"Przekazana wiadomość",
    r"Wiadomość przesłana dalej",
    r"Mesaj redirecționat",
    r"Перенаправленное сообщение",
    r"Пересылаемое сообщение",
    r"Переслане повідомлення",
    r"Перенаправлене повідомлення",
    r"Preposlaná správa(?: --- Forwarded Message)?",
    r"İletilen İleti",
    r"İletilmiş Mesaj",
    r"メッセージを転送",
    r"转发的消息",
    r"轉寄郵件",
)

# Localized Apple Mail introductions, each followed by a colon.
# Add a locale by appending one entry.
_APPLE_FORWARD_LABELS: tuple[str, ...] = (
    r"Begin forwarded message",
    r"Anfang der weitergeleiteten Nachricht",
    r"Inicio del mensaje reenviado",
    r"Début du message réexpédié",
    r"Début du message transféré",
    r"Inizio messaggio inoltrato",
    r"Begin doorgestuurd bericht",
    r"Início da mensagem reencaminhada",
    r"Início da mensagem encaminhada",
    r"Začátek přeposílané zprávy",
    r"Start på videresendt besked",
    r"Välitetty viesti alkaa",
    r"Započni proslijeđenu poruku",
    r"Továbbított levél kezdete",
    r"Videresendt melding",
    r"Początek przekazywanej wiadomości",
    r"Începe mesajul redirecționat",
    r"Начало переадресованного сообщения",
    r"Začiatok preposlanej správy",
    r"Vidarebefordrat mejl",
    r"İleti başlangıcı",
    r"Початок листа, що пересилається",
    r"転送されたメッセージ",
)

# One regex fragment per client family, each matched case-insensitively as a
# whole line. Both the Gmail ``gmail_quote`` classifier and the plain-text
# splitter use these.
FORWARD_MARKER_PATTERNS: tuple[str, ...] = (
    # Gmail keeps this English separator in every UI language checked.
    r"-{2,}[ \t]*Forwarded message[ \t]*-{2,}",
    r"-{3,10}[ \t]*(?:" + "|".join(_DASHED_FORWARD_LABELS) + r")[ \t]*-{3,10}",
    r"(?:" + "|".join(_APPLE_FORWARD_LABELS) + r")[ \t]?:",
)

_MARKER_ALTERNATION = "|".join(f"(?:{pattern})" for pattern in FORWARD_MARKER_PATTERNS)

# A marker on its own unindented line, optionally behind ``>`` quote markers
# (Apple Mail quotes its plain-text forwards). Match against text passed
# through ``normalize_marker_text``. The quote group is possessive: no marker
# starts with ``>``, a space or a tab, so backtracking into it cannot produce a
# match; making it possessive skips that backtracking on long ``>`` prefixes.
FORWARD_MARKER_RE = re.compile(
    rf"(?im)^(?P<quote>(?:>[ \t]?)++)?(?:{_MARKER_ALTERNATION})[ \t\r]*$"
)

# A header-like line ("From:", "Von:", "De :", "发件人：", "| Subject: |", "**From:**")
# that must follow a plain-text separator, so prose that merely contains a
# separator-shaped line is not split as a forward.
_HEADER_LIKE_LINE_RE = re.compile(
    r"^(?:\|[ \t]*)?\*{0,2}[^\W\d_][\w.-]*(?: [\w.()-]+)?\*{0,2}[ \t\u00a0]?[:\uff1a]"
)
_QUOTE_PREFIX_RE = re.compile(r"^(?:>[ \t]?)+")

# Trailing Markdown hard-break syntax that html_to_markdown emits for ``<br>``.
_HARD_BREAK_RE = re.compile(r"(?:\s|\\)+$")

# Forwarded-header labels per field, across clients and locales. Sources and
# confidence: docs/reference/v4-runtime-contracts.md under forwarded content
# (crisp-oss/email-forward-parser fixtures, Thunderbird l10n, Proton
# WebClients locales, mail-parser-reply Outlook tables). Short words such as
# "A", "An", "Da" and "De" are only read inside a header block that follows a
# forward separator. Add a label by appending it to its field.
_FORWARD_HEADER_LABELS: dict[str, tuple[str, ...]] = {
    "from": (
        "From", "De", "Von", "Da", "Mittente", "Van",
        "差出人", "送信者", "送信元", "发件人", "寄件者", "從",
    ),
    "date": (
        "Date", "Sent", "Envoyé", "Datum", "Gesendet", "Fecha", "Enviado", "Data",
        "Inviato", "Verzonden", "送信日時", "日時", "日付", "发送时间", "日期",
    ),
    "subject": (
        "Subject", "Objet", "Sujet", "Betreff", "Asunto", "Oggetto", "Assunto",
        "Onderwerp", "件名", "主题", "主旨",
    ),
    "to": (
        "To", "À", "Pour", "An", "Para", "A", "Aan", "宛先", "送信先", "收件人", "到",
    ),
    "cc": ("Cc", "Copie à", "Kopie", "Kopie (CC)", "抄送", "副本"),
    "bcc": ("Bcc",),
    "reply-to": ("Reply-To",),
}
_LABEL_FIELDS = {
    label.casefold(): field
    for field, labels in _FORWARD_HEADER_LABELS.items()
    for label in labels
}
# Fields folded into a forward section heading; other header lines stay in
# the forwarded content.
_HEADING_FIELDS = frozenset({"from", "date", "subject"})
# A ``**text**`` / ``__text__`` wrapper, not a delimiter inside a word such as
# ``deploy__prod``.
_EMPHASIS_RE = re.compile(r"(?<![\w*])(\*\*|__)(?=\S)(.+?)(?<=\S)\1(?![\w*])")
_DOUBLE_ANGLE_RE = re.compile(r"<<([^<>]*)>>")

# A label, optionally bold, then an ASCII or full-width colon; French
# clients put a space or no-break space before the colon ("De :").
_FORWARD_HEADER_LINE_RE = re.compile(
    r"^\*{0,2}(?P<label>"
    + "|".join(re.escape(label) for label in sorted(_LABEL_FIELDS, key=len, reverse=True))
    + r")\*{0,2}[ \t\u00a0]?[:\uff1a]\*{0,2}[ \t\u00a0]*(?P<value>.*)$",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_marker_text(text: str) -> str:
    """Replace no-break spaces with spaces; string length is unchanged."""
    return text.replace(" ", " ")


def is_forward_marker_line(line: str) -> bool:
    """Return True when ``line`` is a forward marker, ignoring hard-break syntax."""
    candidate = _HARD_BREAK_RE.sub("", normalize_marker_text(line).strip())
    # HTML inline emphasis can surround the separator in the Markdown
    # projection. Strip only a balanced outer pair, never internal marks.
    for delimiter in ("**", "__", "*", "_"):
        if candidate.startswith(delimiter) and candidate.endswith(delimiter):
            candidate = candidate[len(delimiter):-len(delimiter)].strip()
            break
    return FORWARD_MARKER_RE.fullmatch(candidate) is not None


def split_forward_marker(text: str) -> tuple[str | None, str]:
    """Split a leading marker line from ``text``.

    Returns ``(marker, rest)``; ``marker`` is None when the first non-empty
    line is not a forward marker.
    """
    stripped = text.lstrip()
    first_line, _, rest = stripped.partition("\n")
    if not is_forward_marker_line(first_line):
        return None, text
    return _HARD_BREAK_RE.sub("", first_line.strip()), rest


def followed_by_header_line(text: str, end: int, *, quoted: bool) -> bool:
    """True when the first non-blank line after ``end`` looks like a header.

    Reads line by line from ``end`` and stops at that line, so a check costs
    only the text up to it, not the rest of ``text``.
    """
    start = end
    while start <= len(text):
        stop = text.find("\n", start)
        if stop == -1:
            stop = len(text)
        line = text[start:stop]
        if quoted:
            line = _QUOTE_PREFIX_RE.sub("", line.lstrip())
        candidate = line.strip()
        if candidate:
            return _HEADER_LIKE_LINE_RE.match(candidate) is not None
        start = stop + 1
    return False


def _clean_text(value: str, *, markdown: bool) -> str:
    """Unwrap Markdown emphasis from a parsed heading field.

    Plain-text values keep literal ``**``/``__``: the header line is removed
    from the section body, so the heading is the only copy of the value.
    """
    if markdown:
        value = _EMPHASIS_RE.sub(r"\2", value)
    return _WHITESPACE_RE.sub(" ", value).strip()


def _clean_sender(value: str, *, markdown: bool) -> str:
    """Unwrap Markdown emphasis and doubled angle brackets from a parsed sender."""
    if markdown:
        value = _DOUBLE_ANGLE_RE.sub(r"<\1>", value)
    return _clean_text(value, markdown=markdown)


def parse_forward_headers(
    text: str, *, markdown: bool = True
) -> tuple[dict[str, str], str] | None:
    """Parse the header block at the top of a forward.

    The block is the run of header lines before the first blank line; in the
    Apple Mail layout (bold labels, blank-separated) it runs to the first
    line that is not a bold-label header instead. The first From, Date (or Sent) and Subject lines
    become ``fields`` for the section heading and are removed from ``rest``;
    To/Cc/Bcc/Reply-To and repeated labels stay in ``rest``. Returns None
    when no From line is found, so the caller keeps the text unchanged.
    ``markdown`` is False for plain-text sources, whose values stay literal.
    """
    lines = text.split("\n")
    fields: dict[str, str] = {}
    consumed: set[int] = set()
    apple_layout: bool | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        candidate = _HARD_BREAK_RE.sub("", line.strip())
        if not candidate:
            if apple_layout is None or apple_layout:
                continue
            end = index
            break
        match = _FORWARD_HEADER_LINE_RE.match(candidate)
        if apple_layout and not candidate.startswith("**"):
            # Apple Mail header labels are bold; a plain "Date: ..." line
            # after them is forwarded body text.
            match = None
        if match is None:
            end = index
            break
        if apple_layout is None:
            apple_layout = candidate.startswith("**")
        field = _LABEL_FIELDS.get(match.group("label").casefold())
        key = field if field in _HEADING_FIELDS else None
        value = _WHITESPACE_RE.sub(" ", match.group("value")).strip()
        if key is not None and value and key not in fields:
            fields[key] = value
            consumed.add(index)
    if "from" not in fields:
        return None
    fields["from"] = _clean_sender(fields["from"], markdown=markdown)
    if "subject" in fields:
        fields["subject"] = _clean_text(fields["subject"], markdown=markdown)
    kept = [
        line
        for index, line in enumerate(lines[:end])
        if index not in consumed and (line.strip() or not apple_layout)
    ]
    if apple_layout and kept:
        kept.append("")
    return fields, "\n".join([*kept, *lines[end:]]).strip("\n")
