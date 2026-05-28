#!/usr/bin/env python3
"""Deterministically generate the benchmark email corpus.

This script *is* the corpus disclosure. Every ``.eml`` in this directory is
produced here, so a skeptic can read exactly how each fixture was built and
verify nothing is cherry-picked or hand-tuned to flatter dead-letter.

The corpus is synthetic-but-representative. We do not claim these are real
emails. We claim their *structural overhead* mirrors what real clients emit:

* Outlook's HTML is genuinely this bloated (MSO conditional comments,
  ``mso-`` inline styles, ``<o:p>`` tags, Word-export cruft).
* Gmail wraps quoted replies in ``gmail_quote`` divs with inline styles.
* Attachments are base64-encoded into the MIME body, inflating raw size by
  ~33% over the binary regardless of content.

Token cost driven by MIME/HTML/base64 structure is deterministic and content
independent, which is why synthetic fixtures are honest for the *cost* axis.
We do NOT use them to claim anything about parse quality on real-world mess.

Run:  python benchmarks/fixtures/generate.py
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Fixed clock so regeneration is byte-stable.
EPOCH = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)


def _dt(offset_hours: int) -> datetime:
    return EPOCH + timedelta(hours=offset_hours)


def _fake_binary(size: int, seed: int) -> bytes:
    """Deterministic pseudo-binary payload of a given size (no PII, no RNG)."""
    out = bytearray(b"%PDF-1.7\n")  # plausible header; content is irrelevant to size
    x = (seed * 2654435761) & 0xFFFFFFFF
    while len(out) < size:
        x = (1103515245 * x + 12345) & 0xFFFFFFFF
        out.append(x >> 16 & 0xFF)
    return bytes(out[:size])


# --------------------------------------------------------------------------
# Body templates
# --------------------------------------------------------------------------


def _outlook_html(messages: list[tuple[str, str, str, str]]) -> str:
    """Render an Outlook-style HTML thread. ``messages`` is newest-first:
    (from, to, sent, body)."""
    head = (
        "<html xmlns:v=\"urn:schemas-microsoft-com:vml\" "
        "xmlns:o=\"urn:schemas-microsoft-com:office:office\" "
        "xmlns:w=\"urn:schemas-microsoft-com:office:word\" "
        "xmlns:m=\"http://schemas.microsoft.com/office/2004/12/omml\" "
        "xmlns=\"http://www.w3.org/TR/REC-html40\">\n"
        "<head>\n"
        "<meta http-equiv=\"Content-Type\" content=\"text/html; charset=us-ascii\">\n"
        "<meta name=\"Generator\" content=\"Microsoft Word 15 (filtered medium)\">\n"
        "<style><!--\n"
        "/* Font Definitions */\n"
        "@font-face {font-family:\"Cambria Math\"; panose-1:2 4 5 3 5 4 6 3 2 4;}\n"
        "@font-face {font-family:Calibri; panose-1:2 15 5 2 2 2 4 3 2 4;}\n"
        "/* Style Definitions */\n"
        "p.MsoNormal, li.MsoNormal, div.MsoNormal\n"
        "  {margin:0in; font-size:11.0pt; font-family:\"Calibri\",sans-serif;}\n"
        "a:link, span.MsoHyperlink {mso-style-priority:99; color:#0563C1; "
        "text-decoration:underline;}\n"
        "span.EmailStyle19 {mso-style-type:personal-reply; "
        "font-family:\"Calibri\",sans-serif; color:windowtext;}\n"
        ".MsoChpDefault {mso-style-type:export-only; font-size:10.0pt;}\n"
        "@page WordSection1 {size:8.5in 11.0in; margin:1.0in 1.0in 1.0in 1.0in;}\n"
        "div.WordSection1 {page:WordSection1;}\n"
        "--></style>\n"
        "<!--[if gte mso 9]><xml><o:shapedefaults v:ext=\"edit\" spidmax=\"1026\"/>"
        "</xml><![endif]-->\n"
        "<!--[if gte mso 9]><xml><o:shapelayout v:ext=\"edit\">"
        "<o:idmap v:ext=\"edit\" data=\"1\"/></o:shapelayout></xml><![endif]-->\n"
        "</head>\n"
        "<body lang=\"EN-US\" link=\"#0563C1\" vlink=\"#954F72\" style='word-wrap:break-word'>\n"
        "<div class=WordSection1>\n"
    )
    parts: list[str] = []
    newest = messages[0]
    parts.append(
        f"<p class=MsoNormal>{newest[3]}<o:p></o:p></p>\n"
        "<p class=MsoNormal><o:p>&nbsp;</o:p></p>\n"
        "<p class=MsoNormal>Thanks,<o:p></o:p></p>\n"
        "<p class=MsoNormal>Dana<o:p></o:p></p>\n"
        "<p class=MsoNormal><o:p>&nbsp;</o:p></p>\n"
    )
    for frm, to, sent, body in messages[1:]:
        parts.append(
            "<div style='border:none;border-top:solid #E1E1E1 1.0pt;"
            "padding:3.0pt 0in 0in 0in'>\n"
            "<p class=MsoNormal style='margin-bottom:12.0pt'>"
            f"<b>From:</b> {frm}<br>\n"
            f"<b>Sent:</b> {sent}<br>\n"
            f"<b>To:</b> {to}<br>\n"
            "<b>Subject:</b> RE: Q1 vendor SOW &amp; budget sign-off<o:p></o:p></p>\n"
            "</div>\n"
            f"<p class=MsoNormal>{body}<o:p></o:p></p>\n"
            "<p class=MsoNormal><o:p>&nbsp;</o:p></p>\n"
        )
    tail = "</div>\n</body>\n</html>\n"
    return head + "".join(parts) + tail


def _outlook_plain(messages: list[tuple[str, str, str, str]]) -> str:
    """Degraded text/plain alternative, as Outlook actually emits it."""
    lines = [messages[0][3], "", "Thanks,", "Dana", ""]
    for frm, to, sent, body in messages[1:]:
        lines += [
            "________________________________",
            f"From: {frm}",
            f"Sent: {sent}",
            f"To: {to}",
            "Subject: RE: Q1 vendor SOW & budget sign-off",
            "",
            body,
            "",
        ]
    return "\n".join(lines)


def _gmail_html(messages: list[tuple[str, str]]) -> str:
    """Gmail-style nested quote thread. ``messages`` newest-first: (attr, body)."""
    inner = ""
    for attr, body in reversed(messages[1:]):
        inner = (
            "<div class=\"gmail_quote gmail_quote_container\">"
            f"<div dir=\"ltr\" class=\"gmail_attr\">{attr}</div>"
            "<blockquote class=\"gmail_quote\" style=\"margin:0px 0px 0px 0.8ex;"
            "border-left:1px solid rgb(204,204,204);padding-left:1ex\">"
            f"<div dir=\"ltr\">{body}</div>{inner}</blockquote></div>"
        )
    top = messages[0]
    return (
        "<div dir=\"ltr\">"
        f"<div dir=\"ltr\">{top[1]}</div>"
        "<div><br></div>"
        "<div>Best,<br>Sam</div>"
        f"{inner}"
        "</div>"
    )


def _tracking_pixel() -> str:
    return (
        "<img src=\"https://track.example-mail.net/open?"
        "id=8c1f2a9e4b7d&u=42198&c=campaign-q1\" width=\"1\" height=\"1\" "
        "border=\"0\" alt=\"\" style=\"display:block\">"
    )


def _newsletter_html() -> str:
    blurbs = [
        "Q1 revenue came in 8% ahead of plan, driven by the new self-serve tier.",
        "The mobile redesign ships to all users next Tuesday after a clean beta.",
        "We are hiring two senior backend engineers; referrals get a bonus.",
        "Customer NPS climbed to 61 this quarter, up from 54 in Q4.",
        "The status page now reports per-region latency in real time.",
        "Save the date: the annual user conference returns September 15-16.",
    ]
    rows = "".join(
        "<tr><td style=\"padding:18px 24px;font-family:Helvetica,Arial,sans-serif;"
        "font-size:15px;line-height:24px;color:#2b2b2b;\">"
        f"<strong style=\"color:#c0392b;\">Update {i}.</strong> {blurb} "
        "<a href=\"https://example-mail.net/click?l=read-more&amp;i="
        f"{i}&amp;u=42198\" style=\"color:#0563C1;\">Read more &raquo;</a></td></tr>"
        for i, blurb in enumerate(blurbs, 1)
    )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width\">"
        "<style>body{margin:0;padding:0;background:#f4f4f4;}"
        ".btn{background:#c0392b;color:#fff !important;padding:12px 28px;"
        "border-radius:4px;text-decoration:none;display:inline-block;}</style></head>"
        "<body><center><table role=\"presentation\" width=\"600\" "
        "cellpadding=\"0\" cellspacing=\"0\" style=\"background:#ffffff;\">"
        "<tr><td style=\"background:#1a1a2e;padding:28px;text-align:center;\">"
        "<img src=\"https://example-mail.net/assets/logo-wide.png\" width=\"180\" "
        "alt=\"Acme Weekly\"></td></tr>"
        f"{rows}"
        "<tr><td style=\"padding:24px;text-align:center;\">"
        "<a class=\"btn\" href=\"https://example-mail.net/click?l=cta&amp;u=42198\">"
        "View the full report</a></td></tr>"
        "<tr><td style=\"padding:18px 24px;font-size:11px;color:#999;"
        "font-family:Helvetica,Arial,sans-serif;\">You are receiving this because "
        "you subscribed. <a href=\"https://example-mail.net/unsub?u=42198\">"
        "Unsubscribe</a> &middot; 123 Market St, Springfield.</td></tr>"
        f"</table></center>{_tracking_pixel()}</body></html>"
    )


# --------------------------------------------------------------------------
# Fixture specs
# --------------------------------------------------------------------------


@dataclass
class Fixture:
    name: str                       # filename stem; prefix before "__" is category
    build: Callable[[], EmailMessage]


def _base(msg: EmailMessage, subject: str, frm: str, to: str, when: datetime) -> None:
    msg["Subject"] = subject
    msg["From"] = frm
    msg["To"] = to
    msg["Date"] = format_datetime(when)
    msg["Message-ID"] = make_msgid(domain="example.com")


def _outlook_thread(n: int) -> EmailMessage:
    people = [
        ("Dana Whitfield <dana.whitfield@example.com>",
         "Marcus Lee <marcus.lee@vendor.example>"),
        ("Marcus Lee <marcus.lee@vendor.example>",
         "Dana Whitfield <dana.whitfield@example.com>"),
    ]
    bodies = [
        "Confirming the revised SOW and the budget line for sign-off before "
        "quarter close. Can you send the final figures across today so I can "
        "route them to procurement first thing tomorrow?",
        "Thanks Dana &#8212; attaching the updated figures now. The day-rate "
        "line moved up 4% but the total is still under the approved ceiling.",
        "Appreciate the quick turnaround. Looping in finance so they can review "
        "the ceiling before we commit to the signature window.",
        "Finance is good with the numbers pending the final signed SOW. No "
        "objections on the 4% day-rate change.",
        "Great &#8212; I will route for signature this afternoon and should "
        "have it back, fully executed, by end of day.",
    ]
    msgs = []
    for i in range(n):
        frm, to = people[i % 2]
        msgs.append((frm, to, format_datetime(_dt(-i * 18)), bodies[i % len(bodies)]))
    msg = EmailMessage()
    _base(msg, "RE: Q1 vendor SOW & budget sign-off",
          people[0][0], people[0][1], _dt(0))
    msg.set_content(_outlook_plain(msgs))
    msg.add_alternative(_outlook_html(msgs), subtype="html")
    return msg


def _gmail_thread(n: int) -> EmailMessage:
    bodies = [
        "Pushed the fix to staging — can you sanity check the webhook retries "
        "when you get a sec? Mostly worried about the backoff jitter.",
        "Looks good on my end, retries back off correctly now and the jitter "
        "spread looks healthy across the sample.",
        "One edge case though: the dead-letter queue still double-counts on the "
        "final retry attempt before it gives up.",
        "Good catch — patching the counter now, will redeploy to staging in "
        "about ten minutes and ping you to re-test.",
    ]
    attrs = [
        "On Mon, Mar 2, 2026 at 9:00 AM Sam Ortiz &lt;sam@example.com&gt; wrote:",
        "On Mon, Mar 2, 2026 at 8:30 AM Priya Nair &lt;priya@example.com&gt; wrote:",
        "On Mon, Mar 2, 2026 at 8:00 AM Sam Ortiz &lt;sam@example.com&gt; wrote:",
    ]
    msgs = [(attrs[i % len(attrs)], bodies[i % len(bodies)]) for i in range(n)]
    msg = EmailMessage()
    _base(msg, "Re: webhook retry + DLQ double-count",
          "Sam Ortiz <sam@example.com>", "Priya Nair <priya@example.com>", _dt(0))
    plain = "\n\n".join(body.replace("&#8212;", "—") for _, body in msgs)
    msg.set_content(plain)
    msg.add_alternative(_gmail_html(msgs), subtype="html")
    return msg


def _newsletter() -> EmailMessage:
    msg = EmailMessage()
    _base(msg, "Acme Weekly: 6 updates you might have missed",
          "Acme Weekly <newsletter@example-mail.net>",
          "subscriber@example.com", _dt(0))
    msg.set_content("Acme Weekly. View this email in your browser. "
                    "Unsubscribe: https://example-mail.net/unsub?u=42198")
    msg.add_alternative(_newsletter_html(), subtype="html")
    return msg


def _html_only_alert() -> EmailMessage:
    """HTML-only system alert with no plain-text alternative (common in the wild)."""
    msg = EmailMessage()
    _base(msg, "[Alert] Build #4821 failed on main",
          "CI Bot <ci@example.com>", "dev-team@example.com", _dt(0))
    html = (
        "<html><body style=\"font-family:Arial,sans-serif;color:#222;\">"
        "<table width=\"600\" style=\"border:1px solid #e1e1e1;\">"
        "<tr><td style=\"background:#c0392b;color:#fff;padding:16px;font-size:18px;\">"
        "Build failed</td></tr>"
        "<tr><td style=\"padding:16px;\"><p>Pipeline <b>main</b> failed at stage "
        "<code>integration-tests</code>.</p>"
        "<ul><li>Commit: <a href=\"https://example.com/c/9f2a1\">9f2a1</a></li>"
        "<li>Duration: 7m 12s</li><li>Failed: 3 / 412</li></ul>"
        "<p><a href=\"https://example.com/builds/4821\" "
        "style=\"background:#0563C1;color:#fff;padding:10px 20px;"
        "text-decoration:none;border-radius:3px;\">View logs</a></p></td></tr>"
        f"</table>{_tracking_pixel()}</body></html>"
    )
    # No set_content (plain) call -> HTML-only.
    msg["Content-Type"] = "text/html; charset=utf-8"
    msg.set_payload(html)
    return msg


def _with_pdf(size_kb: int, seed: int) -> EmailMessage:
    msg = _outlook_thread(2)
    del msg["Subject"]
    msg["Subject"] = "Q1 vendor SOW — signed copy attached"
    payload = _fake_binary(size_kb * 1024, seed)
    msg.add_attachment(
        payload, maintype="application", subtype="pdf",
        filename="Q1_vendor_SOW_signed.pdf",
    )
    return msg


def _with_inline_images(seed: int) -> EmailMessage:
    msg = EmailMessage()
    _base(msg, "Brand refresh — logo proofs for review",
          "Jordan Park <jordan@agency.example>",
          "Dana Whitfield <dana.whitfield@example.com>", _dt(0))
    cid1 = make_msgid(domain="agency.example")[1:-1]
    cid2 = make_msgid(domain="agency.example")[1:-1]
    html = (
        "<html><body style=\"font-family:Helvetica,Arial,sans-serif;\">"
        "<p>Hi Dana, two proofs below — let me know which direction you prefer.</p>"
        f"<p><img src=\"cid:{cid1}\" width=\"320\" alt=\"Proof A\"></p>"
        f"<p><img src=\"cid:{cid2}\" width=\"320\" alt=\"Proof B\"></p>"
        "<p>Happy to iterate. Best, Jordan</p>"
        f"{_tracking_pixel()}</body></html>"
    )
    msg.set_content("Hi Dana, two proofs attached — let me know which you prefer. "
                    "Best, Jordan")
    msg.add_alternative(html, subtype="html")
    # Inline images attach to the HTML alternative part (multipart/related),
    # matching how real clients nest cid-referenced images.
    html_part = msg.get_payload(1)
    assert isinstance(html_part, EmailMessage)
    html_part.add_related(_fake_binary(60 * 1024, seed), maintype="image",
                          subtype="png", cid=f"<{cid1}>", filename="proof_a.png")
    html_part.add_related(_fake_binary(72 * 1024, seed + 1), maintype="image",
                          subtype="png", cid=f"<{cid2}>", filename="proof_b.png")
    return msg


def _plain_text(thread: bool) -> EmailMessage:
    msg = EmailMessage()
    if thread:
        _base(msg, "Re: lunch + standup moved to 10",
              "Alex Rivera <alex@example.com>", "team@example.com", _dt(0))
        body = (
            "Works for me, see you at 10.\n\n"
            "On Mon, Mar 2 at 8:45 AM, Robin Cho wrote:\n"
            "> Standup is moving to 10 today, room 4B.\n"
            "> Grabbing lunch after if anyone wants in.\n\n"
            ">> Heads up the projector in 4B is flaky, bring the dongle.\n"
        )
    else:
        _base(msg, "parking validation reminder",
              "Facilities <facilities@example.com>", "all-staff@example.com",
              _dt(0))
        body = (
            "Reminder: get your parking ticket validated at the front desk "
            "before 6pm. Unvalidated tickets are charged at the daily rate.\n\n"
            "Thanks,\nFacilities\n"
        )
    msg.set_content(body)
    return msg


FIXTURES: list[Fixture] = [
    Fixture("outlook-html__sow-thread-3", lambda: _outlook_thread(3)),
    Fixture("outlook-html__sow-thread-5", lambda: _outlook_thread(5)),
    Fixture("gmail-html__webhook-thread-3", lambda: _gmail_thread(3)),
    Fixture("gmail-html__webhook-thread-4", lambda: _gmail_thread(4)),
    Fixture("newsletter__acme-weekly", _newsletter),
    Fixture("html-only__ci-alert", _html_only_alert),
    Fixture("attachment__sow-pdf-small", lambda: _with_pdf(40, 11)),
    Fixture("attachment__sow-pdf-large", lambda: _with_pdf(180, 23)),
    Fixture("attachment__logo-proofs", lambda: _with_inline_images(31)),
    Fixture("plaintext__standup-thread", lambda: _plain_text(thread=True)),
    Fixture("plaintext__parking-notice", lambda: _plain_text(thread=False)),
]


def main() -> None:
    written = []
    for fx in FIXTURES:
        msg = fx.build()
        path = HERE / f"{fx.name}.eml"
        path.write_bytes(msg.as_bytes())
        written.append((path.name, path.stat().st_size))
    width = max(len(n) for n, _ in written)
    print(f"Wrote {len(written)} fixtures to {HERE}:")
    for name, size in written:
        print(f"  {name.ljust(width)}  {size:>8,} bytes")


if __name__ == "__main__":
    main()
