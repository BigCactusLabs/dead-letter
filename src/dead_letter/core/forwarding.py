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
)

# One regex fragment per client family, each matched case-insensitively as a
# whole line. Both the Gmail ``gmail_quote`` classifier and the plain-text
# splitter use these.
FORWARD_MARKER_PATTERNS: tuple[str, ...] = (
    # Gmail keeps this English separator in every UI language checked.
    r"-{2,}\s*Forwarded message\s*-{2,}",
    r"-{3,10}\s*(?:" + "|".join(_DASHED_FORWARD_LABELS) + r")\s*-{3,10}",
    r"(?:" + "|".join(_APPLE_FORWARD_LABELS) + r")\s?:",
)

_MARKER_ALTERNATION = "|".join(f"(?:{pattern})" for pattern in FORWARD_MARKER_PATTERNS)

# A marker on its own line anywhere in a text, optionally behind ``>`` quote
# markers (Apple Mail quotes its plain-text forwards). Match against text
# passed through ``normalize_marker_text``.
FORWARD_MARKER_RE = re.compile(
    rf"(?im)^[ \t]*(?P<quote>(?:>[ \t]?)+)?(?:{_MARKER_ALTERNATION})[ \t\r]*$"
)

# Trailing Markdown hard-break syntax that html_to_markdown emits for ``<br>``.
_HARD_BREAK_RE = re.compile(r"(?:\s|\\)+$")

_FORWARD_HEADER_LINE_RE = re.compile(
    r"^\*{0,2}(?P<label>From|Date|Sent|Subject|To|Cc|Bcc|Reply-To):\*{0,2}[ \t]*(?P<value>.*)$",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_marker_text(text: str) -> str:
    """Replace no-break spaces with spaces; string length is unchanged."""
    return text.replace(" ", " ")


def is_forward_marker_line(line: str) -> bool:
    """Return True when ``line`` is a forward marker, ignoring hard-break syntax."""
    candidate = _HARD_BREAK_RE.sub("", normalize_marker_text(line).strip())
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


def parse_forward_headers(text: str) -> tuple[dict[str, str], str] | None:
    """Parse the leading ``From:``/``Date:``/``Subject:`` block of a forward.

    Returns ``(fields, rest)`` with lower-cased labels when a ``From:`` line
    is present, otherwise None so the caller keeps the header lines as text.
    """
    lines = text.lstrip("\n").split("\n")
    fields: dict[str, str] = {}
    consumed = 0
    for index, line in enumerate(lines):
        candidate = _HARD_BREAK_RE.sub("", line.strip())
        if not candidate:
            # Apple Mail separates header lines with blank lines.
            continue
        match = _FORWARD_HEADER_LINE_RE.match(candidate)
        if match is None:
            break
        value = _WHITESPACE_RE.sub(" ", match.group("value")).strip()
        label = match.group("label").lower()
        if value and label not in fields:
            fields[label] = value
        consumed = index + 1
    if "from" not in fields:
        return None
    return fields, "\n".join(lines[consumed:]).lstrip("\n")
