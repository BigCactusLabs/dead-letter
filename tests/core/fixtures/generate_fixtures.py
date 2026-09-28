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
        "gmail_forward_single.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Fwd: Vendor Note
            Date: Thu, 05 Mar 2026 11:00:00 +0000
            Message-ID: <gmail-forward-single-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/html; charset=utf-8

            <div dir="ltr">FYI, see the vendor note below.</div><br>
            <div class="gmail_quote gmail_quote_container"><div dir="ltr" class="gmail_attr">---------- Forwarded message ---------<br>
            From: <strong class="gmail_sendername" dir="auto">Vendor</strong> <span dir="auto">&lt;vendor@example.net&gt;</span><br>
            Date: Thu, Mar 5, 2026 at 8:00 AM<br>Subject: Vendor Note<br>To: Alice &lt;alice@example.com&gt;<br></div><br><br>
            <div dir="ltr">Please review the attached quote.</div>
            </div>
        """,
        "gmail_forward_french.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Fwd: Stage
            Date: Thu, 05 Mar 2026 11:02:00 +0000
            Message-ID: <gmail-forward-french-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/html; charset=utf-8

            <div dir="ltr">Pour info.</div><br>
            <div class="gmail_quote gmail_quote_container"><div dir="ltr" class="gmail_attr">---------- Forwarded message ---------<br>
            De : <b class="gmail_sendername" dir="auto">Expéditeur</b> <span dir="auto">&lt;sender@example.fr&gt;</span><br>
            Date: jeu. 5 mars 2026 à 08:00<br>Subject: Stage<br>To: Alice &lt;alice@example.com&gt;<br></div><br><br>
            <div dir="ltr">Le programme du stage est joint.</div>
            </div>
        """,
        "thunderbird_forward.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Fwd: Minutes
            Date: Thu, 05 Mar 2026 11:30:00 +0000
            Message-ID: <thunderbird-forward-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/html; charset=utf-8

            <html><body><p>Minutes attached below.</p>
            <div class="moz-forward-container"><br><br>-------- Forwarded Message --------
            <table class="moz-email-headers-table" border="0" cellpadding="0" cellspacing="0">
            <tbody><tr><th valign="BASELINE" nowrap="nowrap" align="RIGHT">Subject: </th><td>Minutes</td></tr>
            <tr><th valign="BASELINE" nowrap="nowrap" align="RIGHT">Date: </th><td>Wed, 4 Mar 2026 09:00:00 +0000</td></tr>
            <tr><th valign="BASELINE" nowrap="nowrap" align="RIGHT">From: </th><td>Erin &lt;erin@example.com&gt;</td></tr>
            </tbody></table><br><p>Minutes from the Wednesday meeting.</p></div>
            </body></html>
        """,
        "apple_mail_forward.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Fwd: Receipt
            Date: Thu, 05 Mar 2026 11:35:00 +0000
            Message-ID: <apple-forward-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/html; charset=utf-8

            <html><body style="line-break:after-white-space">See below.<br id="lineBreakAtBeginningOfMessage"><div><br>
            <blockquote type="cite"><div>Begin forwarded message:</div><br class="Apple-interchange-newline">
            <div style="margin:0px"><span><b>From: </b></span><span>Shop &lt;shop@example.com&gt;<br></span></div>
            <div style="margin:0px"><span><b>Subject: </b></span><span><b>Receipt</b><br></span></div>
            <br><div>Thank you for your order.</div></blockquote></div></body></html>
        """,
        "gmail_forward_body_headers.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Fwd: Budget
            Date: Thu, 05 Mar 2026 11:40:00 +0000
            Message-ID: <gmail-forward-body-headers-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/html; charset=utf-8

            <div>Memo</div><div class="gmail_quote gmail_quote_container"><div dir="ltr" class="gmail_attr">---------- Forwarded message ---------<br>
            From: <strong class="gmail_sendername" dir="auto">Dave</strong> <span dir="auto">&lt;<a href="mailto:dave@example.com">dave@example.com</a>&gt;</span><br>
            Date: Wed, Mar 4, 2026 at 10:00 AM<br>Subject: Budget<br>To: &lt;alice@example.com&gt;<br>Cc: Carol &lt;carol@example.com&gt;<br></div><br><br>
            <div dir="ltr">To: All staff<br>Date: Friday is a holiday<br>From: HR<br><br>Office closed.</div></div>
        """,
        "gmail_forward_empty_body.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Fwd: Budget
            Date: Thu, 05 Mar 2026 11:45:00 +0000
            Message-ID: <gmail-forward-empty-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/html; charset=utf-8

            <div dir="ltr">Empty fwd</div><div class="gmail_quote gmail_quote_container"><div dir="ltr" class="gmail_attr">---------- Forwarded message ---------<br>
            From: <strong class="gmail_sendername" dir="auto">Dave</strong> <span dir="auto">&lt;<a href="mailto:dave@example.com">dave@example.com</a>&gt;</span><br>
            Date: Wed, Mar 4, 2026 at 10:00 AM<br>Subject: Budget<br>To: &lt;alice@example.com&gt;<br>Cc: Carol &lt;carol@example.com&gt;<br></div><br><br></div>
        """,
        "plain_forward_empty_body.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Fwd: Invoice
            Date: Thu, 05 Mar 2026 11:50:00 +0000
            Message-ID: <plain-forward-empty-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/plain; charset=utf-8

            FYI

            ---------- Forwarded message ---------
            From: Dave <dave@example.com>
            Date: Wed, Mar 4, 2026
            Subject: Invoice
            To: alice@example.com
            Cc: Carol <carol@example.com>
        """,
        "gmail_reply_unclassed_blockquote.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Re: Probe
            Date: Thu, 05 Mar 2026 11:55:00 +0000
            Message-ID: <gmail-reply-unclassed-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/html; charset=utf-8

            <div dir="ltr">Thanks!</div><br><div class="gmail_quote"><div dir="ltr" class="gmail_attr">On Wed, Mar 4, 2026 at 9:00 AM Bob &lt;bob@example.com&gt; wrote:<br></div>
            <blockquote style="margin:0 0 0 .8ex">OLD REPLY HISTORY</blockquote></div>
        """,
        "gmail_reply_wrapped_blockquote.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Re: Probe
            Date: Thu, 05 Mar 2026 12:00:00 +0000
            Message-ID: <gmail-reply-wrapped-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/html; charset=utf-8

            <div dir="ltr">Thanks!</div><br><div class="gmail_quote"><div dir="ltr" class="gmail_attr">On Wed, Mar 4, 2026 at 9:00 AM Bob &lt;bob@example.com&gt; wrote:<br></div>
            <div><blockquote class="gmail_quote">OLD REPLY HISTORY</blockquote></div></div>
        """,
        "gmail_extra_reply.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Re: Probe
            Date: Thu, 05 Mar 2026 12:05:00 +0000
            Message-ID: <gmail-extra-reply-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/html; charset=utf-8

            <div dir="ltr">Thanks!<div class="gmail_extra"><br><div class="gmail_quote">On Wed, Mar 4, 2026 at 9:00 AM Bob <span>&lt;bob@example.com&gt;</span> wrote:<br>
            <blockquote class="gmail_quote">OLD REPLY HISTORY</blockquote></div></div></div>
        """,
        "gmail_reply_attr_after_br.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Re: Probe
            Date: Thu, 05 Mar 2026 12:10:00 +0000
            Message-ID: <gmail-reply-br-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/html; charset=utf-8

            <div dir="ltr">Thanks!</div><div class="gmail_quote"><br><div dir="ltr" class="gmail_attr">On Wed, Mar 4, 2026 at 9:00 AM Bob &lt;bob@example.com&gt; wrote:<br></div>
            <blockquote class="gmail_quote">OLD REPLY HISTORY</blockquote></div>
        """,
        "plain_outlook_reply_localized_marker.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: AW: Protokoll
            Date: Thu, 05 Mar 2026 12:15:00 +0000
            Message-ID: <plain-outlook-localized-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/plain; charset=utf-8

            Danke!

            ________________________________
            From: Bob <bob@example.com>
            Sent: Wednesday, March 4, 2026 9:00 AM
            To: Alice <alice@example.com>
            Subject: WG: Protokoll

            OLD REPLY HISTORY LINE

            -------- Weitergeleitete Nachricht --------
            Betreff: Protokoll
            Datum: Tue, 3 Mar 2026
            Von: Erin <erin@example.com>

            Erin protocol text
        """,
        "plain_marker_in_prose.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Re: Probe
            Date: Thu, 05 Mar 2026 12:20:00 +0000
            Message-ID: <plain-marker-prose-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/plain; charset=utf-8

            Hello team,
            please note: the subject line in the old system said
            ----- Mensaje reenviado -----
            and nothing else. Then:

            On Wed, Mar 4, 2026 at 9:00 AM Bob <bob@example.com> wrote:
            > OLD REPLY HISTORY
        """,
        "plain_indented_marker_in_reply.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Re: Probe
            Date: Thu, 05 Mar 2026 12:25:00 +0000
            Message-ID: <plain-indented-marker-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/plain; charset=utf-8

            Thanks

            On Wed, Mar 4, 2026 at 9:00 AM Bob <bob@example.com> wrote:
            > hi
                ---------- Forwarded message ---------
            > more
        """,
        "gmail_forwards_sequential.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Fwd: digest
            Date: Thu, 05 Mar 2026 11:05:00 +0000
            Message-ID: <gmail-forwards-sequential-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/html; charset=utf-8

            <div dir="ltr">Digest of notes below.</div>
            <div class="gmail_quote"><div class="gmail_attr">---------- Forwarded message ---------<br>
            From: Person One &lt;one@example.com&gt;<br>Date: Mon, Mar 2, 2026 at 9:00 AM<br>Subject: Note one<br>To: team@example.com<br></div><br>
            <div>Body from person one.</div></div>
            <div class="gmail_quote"><div class="gmail_attr">---------- Forwarded message ---------<br>
            From: Person Two &lt;two@example.com&gt;<br>Date: Tue, Mar 3, 2026 at 9:00 AM<br>Subject: Note two<br>To: team@example.com<br></div><br>
            <div>Body from person two.</div></div>
            <div class="gmail_quote"><div class="gmail_attr">---------- Forwarded message ---------<br>
            From: Person Three &lt;three@example.com&gt;<br>Date: Wed, Mar 4, 2026 at 9:00 AM<br>Subject: Note three<br>To: team@example.com<br></div><br>
            <div>Body from person three.</div></div>
        """,
        "gmail_forwards_nested.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Fwd: Fwd: Fwd: chain
            Date: Thu, 05 Mar 2026 11:10:00 +0000
            Message-ID: <gmail-forwards-nested-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/html; charset=utf-8

            <div dir="ltr">Passing this chain along.</div>
            <div class="gmail_quote"><div class="gmail_attr">---------- Forwarded message ---------<br>
            From: Person One &lt;one@example.com&gt;<br>Date: Wed, Mar 4, 2026 at 9:00 AM<br>Subject: Fwd: Fwd: chain<br>To: alice@example.com<br></div><br>
            <div>Body from person one.</div><br>
            <div class="gmail_quote"><div class="gmail_attr">---------- Forwarded message ---------<br>
            From: Person Two &lt;two@example.com&gt;<br>Date: Tue, Mar 3, 2026 at 9:00 AM<br>Subject: Fwd: chain<br>To: one@example.com<br></div><br>
            <div>Body from person two.</div><br>
            <div class="gmail_quote"><div class="gmail_attr">---------- Forwarded message ---------<br>
            From: Person Three &lt;three@example.com&gt;<br>Date: Mon, Mar 2, 2026 at 9:00 AM<br>Subject: chain<br>To: two@example.com<br></div><br>
            <div>Body from person three.</div></div></div></div>
        """,
        "gmail_forward_with_reply_quote.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Fwd: Re: Budget
            Date: Thu, 05 Mar 2026 11:15:00 +0000
            Message-ID: <gmail-forward-reply-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/html; charset=utf-8

            <div dir="ltr">See the budget thread below.</div>
            <div class="gmail_quote"><div class="gmail_attr">---------- Forwarded message ---------<br>
            From: Dave &lt;dave@example.com&gt;<br>Date: Wed, Mar 4, 2026 at 10:00 AM<br>Subject: Re: Budget<br>To: alice@example.com<br></div><br>
            <div dir="ltr">Dave approves the budget.</div><br>
            <div class="gmail_quote"><div dir="ltr" class="gmail_attr">On Tue, Mar 3, 2026 at 9:00 AM Carol &lt;carol@example.com&gt; wrote:<br></div>
            <blockquote class="gmail_quote"><div>Carol asks about the budget.</div></blockquote></div></div>
        """,
        "gmail_reply_with_forward.eml": """
            From: Bob <bob@example.com>
            To: Alice <alice@example.com>
            Subject: Re: Fwd: Vendor Note
            Date: Thu, 05 Mar 2026 11:20:00 +0000
            Message-ID: <gmail-reply-forward-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/html; charset=utf-8

            <div dir="ltr">Thanks, got it.</div><br>
            <div class="gmail_quote"><div dir="ltr" class="gmail_attr">On Thu, Mar 5, 2026 at 11:00 AM Alice &lt;alice@example.com&gt; wrote:<br></div>
            <blockquote class="gmail_quote"><div>Forwarding this vendor note.</div>
            <div class="gmail_quote"><div class="gmail_attr">---------- Forwarded message ---------<br>
            From: Vendor &lt;vendor@example.net&gt;<br>Date: Thu, Mar 5, 2026 at 8:00 AM<br>Subject: Vendor Note<br></div><br>
            <div>Vendor quote body.</div></div></blockquote></div>
        """,
        "plain_forwards_multiple.eml": """
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Subject: Fwd: notes
            Date: Thu, 05 Mar 2026 11:25:00 +0000
            Message-ID: <plain-forwards-multiple-1@example.com>
            MIME-Version: 1.0
            Content-Type: text/plain; charset=utf-8

            Digest of notes below.

            ---------- Forwarded message ---------
            From: Person One <one@example.com>
            Date: Mon, Mar 2, 2026 at 9:00 AM
            Subject: Note one
            To: team@example.com

            Body from person one.

            -------- Forwarded message --------
            From: Person Two <two@example.com>
            Date: Tue, Mar 3, 2026 at 9:00 AM
            Subject: Note two

            Body from person two.

            Begin forwarded message:

            From: Person Three <three@example.com>
            Subject: Note three
            Date: March 4, 2026 at 9:00:00 AM EST
            To: team@example.com

            Body from person three.
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
