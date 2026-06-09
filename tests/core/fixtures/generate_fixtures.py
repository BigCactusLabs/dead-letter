from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def normalize(text: str) -> str:
    text = dedent(text).strip("\n") + "\n"
    return text.replace("\n", "\r\n")


def write_text(path: Path, content: str) -> None:
    path.write_text(normalize(content), encoding="utf-8")


def main() -> int:
    root = Path(__file__).parent

    fixtures: dict[str, str] = {
        "plain_text.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Plain Text Fixture
            Date: Thu, 05 Mar 2026 09:00:00 +0000
            Message-ID: <plain-text-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/plain; charset=utf-8

            Hello Bob,

            This is a plain text fixture.

            Regards,
            Alice
        """,
        "html_only.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: HTML Only Fixture
            Date: Thu, 05 Mar 2026 09:05:00 +0000
            Message-ID: <html-only-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/html; charset=utf-8

            <html><body><p><strong>Hello Bob</strong></p><p>This is HTML only.</p></body></html>
        """,
        "threaded.eml": """
            From: Bob <bob@example.com>
            To: Alice <alice@example.com>
            Subject: Re: Threaded Fixture
            Date: Thu, 05 Mar 2026 09:10:00 +0000
            Message-ID: <threaded-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/plain; charset=utf-8

            Thanks Alice.

            On Thu, Mar 5, 2026 at 9:00 AM Alice <alice@example.com> wrote:
            > Hello Bob,
            > This is a plain text fixture.
        """,
        "reply_chain.eml": """
            From: Carol <carol@example.com>
            To: Team <team@example.com>
            Subject: Re: Project Update
            Date: Thu, 05 Mar 2026 10:00:00 +0000
            Message-ID: <reply-chain-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/plain; charset=utf-8

            Reply level 2.

            On Thu, Mar 5, 2026 at 9:55 AM Bob <bob@example.com> wrote:
            > Reply level 1.
            >
            > On Thu, Mar 5, 2026 at 9:50 AM Alice <alice@example.com> wrote:
            > > Original thread start.
        """,
        "forwarded.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Fwd: Vendor Note
            Date: Thu, 05 Mar 2026 10:10:00 +0000
            Message-ID: <forwarded-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/plain; charset=utf-8

            ---------- Forwarded message ----------
            From: Vendor <vendor@example.net>
            Date: Thu, Mar 5, 2026 at 8:00 AM
            Subject: Vendor Note
            To: Alice <alice@example.com>

            Please review the attached quote.
        """,
        "gmail_quote.eml": """
            From: Dave <dave@example.com>
            To: Erin <erin@example.com>
            Subject: Re: Gmail Quote Fixture
            Date: Thu, 05 Mar 2026 10:20:00 +0000
            Message-ID: <gmail-quote-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/html; charset=utf-8

            <div>Latest response</div><div class=\"gmail_quote\">On prior mail wrote: ...</div>
        """,
        "outlook_quote.eml": """
            From: Frank <frank@example.com>
            To: Grace <grace@example.com>
            Subject: RE: Outlook Quote Fixture
            Date: Thu, 05 Mar 2026 10:25:00 +0000
            Message-ID: <outlook-quote-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/html; charset=utf-8

            <html><body><div>Top reply</div><div id=\"divRplyFwdMsg\">Original message content</div></body></html>
        """,
        "with_attachment.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Fixture With Attachment
            Date: Thu, 05 Mar 2026 10:30:00 +0000
            Message-ID: <attachment-1@example.com>
            MIME-Version: 1.0
            Content-Type: multipart/mixed; boundary=\"mix-1\"

            --mix-1
            Content-Type: text/plain; charset=utf-8

            See attached.
            --mix-1
            Content-Type: text/plain; name=\"agenda.txt\"
            Content-Disposition: attachment; filename=\"agenda.txt\"
            Content-Transfer-Encoding: base64

            VGVhbSBhZ2VuZGEKLSBJdGVtIDEKLSBJdGVtIDIK
            --mix-1--
        """,
        "with_inline_cid.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Fixture With Inline Image
            Date: Thu, 05 Mar 2026 10:35:00 +0000
            Message-ID: <inline-1@example.com>
            MIME-Version: 1.0
            Content-Type: multipart/related; boundary=\"rel-1\"

            --rel-1
            Content-Type: text/html; charset=utf-8

            <html><body><p>Inline image:</p><img src=\"cid:image1\" alt=\"logo\" /></body></html>
            --rel-1
            Content-Type: image/png; name=\"logo.png\"
            Content-Transfer-Encoding: base64
            Content-ID: <image1>
            Content-Disposition: inline; filename=\"logo.png\"

            iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Zk8kAAAAASUVORK5CYII=
            --rel-1--
        """,
        "outlook_attachment_with_cid.eml": """
            From: Outlook User <outlook.user@example.com>
            To: Recipient <recipient@example.com>
            Subject: Movement Report
            Date: Thu, 05 Mar 2026 11:00:00 +0000
            Message-ID: <outlook-cid-attach-1@namprd10.prod.outlook.com>
            MIME-Version: 1.0
            Content-Type: multipart/mixed; boundary=\"mix-out\"

            --mix-out
            Content-Type: multipart/related; boundary=\"rel-out\"

            --rel-out
            Content-Type: text/html; charset=utf-8

            <html><body><p>Please find attached the report.</p><img src=\"cid:sig-logo\" alt=\"logo\" /></body></html>
            --rel-out
            Content-Type: image/png; name=\"logo.png\"
            Content-Transfer-Encoding: base64
            Content-ID: <sig-logo>
            Content-Disposition: inline; filename=\"logo.png\"

            iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Zk8kAAAAASUVORK5CYII=
            --rel-out--
            --mix-out
            Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet; name=\"report.xlsx\"
            Content-Disposition: attachment; filename=\"report.xlsx\"
            Content-Transfer-Encoding: base64
            Content-ID: <7DF6285D@namprd10.prod.outlook.com>

            UEsDBGRlYWQtbGV0dGVyIHJlZ3Jlc3Npb24geGxzeCBwYXlsb2FkAAECA/8=
            --mix-out--
        """,
        "gmail_3_message_thread.eml": """
            From: Carol <carol@example.com>
            To: Team <team@example.com>
            Subject: Re: Project Update
            Date: Thu, 05 Mar 2026 10:00:00 +0000
            Message-ID: <gmail-3msg-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/plain; charset=utf-8

            Reply level 2 from Carol.

            On Thu, Mar 5, 2026 at 9:55 AM Bob <bob@example.com> wrote:
            > Reply level 1 from Bob.
            >
            > On Thu, Mar 5, 2026 at 9:50 AM Alice <alice@example.com> wrote:
            > > Original message from Alice.
        """,
        "outlook_dom_segmented_thread.eml": """
            From: Carol <carol@example.com>
            To: Team <team@example.com>
            Subject: RE: project status
            Date: Thu, 05 Mar 2026 10:30:00 +0000
            Message-ID: <outlook-dom-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/html; charset=utf-8

            <div>Carol latest reply.</div>
            <div id="divRplyFwdMsg">
              <hr>
              <p><b>From:</b> Bob &lt;bob@example.com&gt;<br>
              <b>Sent:</b> Wednesday, March 4, 2026 9:55 AM<br>
              <b>To:</b> Team &lt;team@example.com&gt;<br>
              <b>Subject:</b> RE: project status</p>
              <p>Bob reply text.</p>
              <p><b>From:</b> Alice &lt;alice@example.com&gt;<br>
              <b>Sent:</b> Tuesday, March 3, 2026 9:50 AM<br>
              <b>To:</b> Team &lt;team@example.com&gt;<br>
              <b>Subject:</b> project status</p>
              <p>Alice original.</p>
            </div>
        """,
        "generic_html_thread.eml": """
            From: Carol <carol@example.com>
            To: Team <team@example.com>
            Subject: Re: generic HTML thread
            Date: Thu, 05 Mar 2026 10:35:00 +0000
            Message-ID: <generic-html-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/html; charset=utf-8

            <div>Carol latest reply.</div>
            <blockquote>
            <div>On Wed Bob wrote:</div>
            <div>Bob reply text.</div>
            </blockquote>
        """,
        "mixed_attribution_thread.eml": """
            From: Carol <carol@example.com>
            To: Team <team@example.com>
            Subject: Re: mixed attribution
            Date: Thu, 05 Mar 2026 10:40:00 +0000
            Message-ID: <mixed-attrib-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/plain; charset=utf-8

            Carol latest reply.

            On Thu, Mar 5, 2026 at 9:55 AM Bob <bob@example.com> wrote:
            > Bob's reply.
            >
            > Something garbled here — no recognizable attribution form.
            > > Alice's text further nested.
        """,
        "multilingual_thread.eml": """
            From: Carol <carol@example.com>
            To: Team <team@example.com>
            Subject: Re: international thread
            Date: Thu, 05 Mar 2026 10:45:00 +0000
            Message-ID: <multi-lang-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/plain; charset=utf-8

            Carol latest reply.

            Am Mittwoch, 4. März 2026 um 09:55 schrieb Bob <bob@example.com>:
            > Bob reply.
            >
            > Le mar. 3 mars 2026 à 09:50, Alice <alice@example.com> a écrit :
            > > Alice original.
        """,
        "outlook_with_cc_thread.eml": """
            From: Carol <carol@example.com>
            To: Team <team@example.com>
            Subject: RE: project status
            Date: Thu, 05 Mar 2026 10:32:00 +0000
            Message-ID: <outlook-cc-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/html; charset=utf-8

            <div>Carol latest reply.</div>
            <div id="divRplyFwdMsg">
              <hr>
              <p><b>From:</b> Bob &lt;bob@example.com&gt;<br>
              <b>Sent:</b> Wednesday, March 4, 2026 9:55 AM<br>
              <b>To:</b> Team &lt;team@example.com&gt;<br>
              <b>Cc:</b> Dana &lt;dana@example.com&gt;<br>
              <b>Subject:</b> RE: project status</p>
              <p>Bob reply text with cc.</p>
            </div>
        """,
        "empty_quoted_attribution.eml": """
            From: Carol <carol@example.com>
            To: Team <team@example.com>
            Subject: Re: empty body after attribution
            Date: Thu, 05 Mar 2026 10:50:00 +0000
            Message-ID: <empty-attrib-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/plain; charset=utf-8

            Carol latest reply.

            On Thu, Mar 5, 2026 at 9:55 AM Bob <bob@example.com> wrote:
        """,
        "calendar_invite.eml": """
            From: Organizer <organizer@example.com>
            To: Attendee <attendee@example.com>
            Subject: Calendar Invite Fixture
            Date: Thu, 05 Mar 2026 10:40:00 +0000
            Message-ID: <calendar-1@example.com>
            MIME-Version: 1.0
            Content-Type: multipart/mixed; boundary=\"cal-1\"

            --cal-1
            Content-Type: text/plain; charset=utf-8

            Meeting invite attached.
            --cal-1
            Content-Type: text/calendar; method=REQUEST; name=\"invite.ics\"
            Content-Disposition: attachment; filename=\"invite.ics\"

            BEGIN:VCALENDAR
            VERSION:2.0
            PRODID:-//dead-letter//fixtures//EN
            BEGIN:VEVENT
            UID:fixture-event-1
            DTSTART:20260306T140000Z
            DTEND:20260306T143000Z
            SUMMARY:Fixture Meeting
            END:VEVENT
            END:VCALENDAR
            --cal-1--
        """,
    }

    for name, content in fixtures.items():
        write_text(root / name, content)

    (root / "malformed_empty.eml").write_bytes(b"")

    non_utf8 = (
        "From: Legacy <legacy@example.com>\r\n"
        "To: Bob <bob@example.com>\r\n"
        "Subject: Legacy charset\r\n"
        "Date: Thu, 05 Mar 2026 10:45:00 +0000\r\n"
        "Message-ID: <legacy-1@example.com>\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: text/plain; charset=iso-8859-1\r\n"
        "\r\n"
    ).encode("ascii") + b"Caf\xe9 in legacy charset\r\n"
    (root / "non_utf8_iso8859.eml").write_bytes(non_utf8)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
