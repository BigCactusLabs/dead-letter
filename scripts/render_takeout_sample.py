"""Regenerate the synthetic Google Takeout sample and its expected Obsidian vault.

Maintainer tool, not run by CI or the test suite. It builds a small synthetic
Takeout-shaped download (one ZIP holding `Takeout/Mail/All mail Including Spam
and Trash.mbox`), installs the named dead-letter release from PyPI into a
temporary virtual environment, converts the ZIP there with real commands, and
copies the input, the vault, the conversion report and a capture record into
the output directory.

    python scripts/render_takeout_sample.py              # writes docs/recipes/takeout-sample/
    python scripts/render_takeout_sample.py --out /tmp/sample --keep-work

The messages reuse the synthetic Cedar Works inbox from docs/recipes/fixtures/
and add three more synthetic records. Nothing here is real mail. Requires uv.
Exit 0: written; 1: a check failed; 2: a tool is missing. The repository
fixtures are never moved or modified.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

from render_demo import DemoError, install_release, require, run, sha

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "docs" / "recipes" / "fixtures" / "inbox"
DEFAULT_OUT = ROOT / "docs" / "recipes" / "takeout-sample"
DEFAULT_VERSION = "0.4.10"
# Resolve dependencies as of the day after 0.4.10 reached PyPI (2026-10-29).
DEFAULT_EXCLUDE_NEWER = "2026-10-30T00:00:00Z"

ZIP_NAME = "takeout-20260921T120000Z-001.zip"
MBOX_MEMBER = "Takeout/Mail/All mail Including Spam and Trash.mbox"
BROWSER_MEMBER = "Takeout/archive_browser.html"
ZIP_TIME = (2026, 9, 21, 12, 0, 0)
VAULT_OUTPUT = "vault/Email/Gmail"
CONVERT = (
    f"dead-letter convert {ZIP_NAME} --output {VAULT_OUTPUT} --mbox-bundles --report"
)

ARCHIVE_BROWSER = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>Synthetic Takeout sample</title>
<p>This file stands in for the archive browser page in a Google Takeout
download. The sample is synthetic and contains no real mail.</p>
</html>
"""

JULES = """From: Jules Rivera <jules@northstar.example>
To: Leo Patel <leo@northstar.example>
Date: Tue, 15 Sep 2026 09:45:00 -0400
Subject: Loading window for Cedar Works
Message-ID: <loading-window-question@northstar.example>
MIME-Version: 1.0
Content-Type: text/plain; charset="utf-8"

Can Dock 3 take the Cedar Works delivery on Thursday morning?
"""

CONFIRM = """From: Northstar Operations <ops@northstar.example>
To: Mara Chen <mara@cedarworks.example>
Date: Tue, 15 Sep 2026 11:10:00 -0400
Subject: Re: Cedar Works order 4821 - revised delivery
Message-ID: <order-4821-confirm@northstar.example>
In-Reply-To: <order-4821@cedarworks.example>
References: <order-4821@cedarworks.example>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="northstar-4821"

--northstar-4821
Content-Type: text/plain; charset="utf-8"

Hi Mara,

Confirmed: order 4821 arrives Thursday, 17 September, in the 08:00-10:00
loading window at Dock 3. The dock map is attached.

Thanks,
Northstar Operations

On Tue, 15 Sep 2026 at 09:20, Mara Chen <mara@cedarworks.example> wrote:
> Please move order 4821 to Thursday, 17 September. The revised quantities are
> in the attached CSV.

--northstar-4821
Content-Type: application/pdf; name="dock-3-map.pdf"
Content-Disposition: attachment; filename="dock-3-map.pdf"
Content-Transfer-Encoding: base64

{pdf}
--northstar-4821--
"""

BULLETIN = """From: Northstar Freight Bulletin <bulletin@northstar.example>
To: Operations <ops@northstar.example>
Date: Mon, 21 Sep 2026 07:00:00 -0400
Subject: September freight bulletin
Message-ID: <bulletin-2026-09@northstar.example>
MIME-Version: 1.0
Content-Type: text/html; charset="utf-8"

<html><body>
<h1>September freight bulletin</h1>
<p>Dock 3 resurfacing is scheduled for <strong>5 October</strong>.
Deliveries move to Dock 1 that day.</p>
<ul>
<li>Weekend route cut-off: Friday 14:00</li>
<li>New pallet labels start on 1 October</li>
</ul>
</body></html>
"""


def tiny_pdf(text: str) -> bytes:
    """A one-page PDF whose only content is `text`, with a valid xref table."""
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def wrapped_base64(data: bytes) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return "\n".join(encoded[i : i + 76] for i in range(0, len(encoded), 76))


def with_headers(
    message: str, headers: list[tuple[str, str]], after: str | None = None
) -> str:
    """Add headers at the top (Takeout's position) or after the named header."""
    lines = [f"{name}: {value}" for name, value in headers]
    if after is None:
        return "\n".join(lines) + "\n" + message
    head, sep, body = message.partition("\n\n")
    out = []
    for line in head.split("\n"):
        out.append(line)
        if line.lower().startswith(after.lower() + ":"):
            out.extend(lines)
    require(len(out) == len(head.split("\n")) + len(lines), f"no {after} header")
    return "\n".join(out) + sep + body


def records() -> list[tuple[str, str]]:
    """(Gmail message id, RFC 5322 message) pairs in archive order."""
    order = (INBOX / "clients" / "order-update.eml").read_text(encoding="utf-8")
    loading = (INBOX / "ops" / "loading-window.eml").read_text(encoding="utf-8")
    loading = with_headers(
        loading,
        [
            ("In-Reply-To", "<loading-window-question@northstar.example>"),
            ("References", "<loading-window-question@northstar.example>"),
        ],
        after="Message-ID",
    )
    confirm = CONFIRM.replace(
        "{pdf}", wrapped_base64(tiny_pdf("Dock 3 unloading bay - north gate"))
    )
    order_thread, dock_thread, bulletin_thread = (
        "1843002176537310123",
        "1843003741251981456",
        "1843529316205730987",
    )
    plan = [
        (
            "1843002176537310123",
            order_thread,
            "Inbox,Important,Opened,Clients/Cedar Works",
            order,
        ),
        ("1843003741251981456", dock_thread, "Archived,Opened,Ops", JULES),
        ("1843004999160247291", dock_thread, "Inbox,Opened,Ops", loading),
        ("1843008121766529004", order_thread, "Sent,Clients/Cedar Works", confirm),
        (
            "1843529316205730987",
            bulletin_thread,
            "Archived,Category Updates,Opened",
            BULLETIN,
        ),
    ]
    return [
        (
            gmail_id,
            with_headers(message, [("X-GM-THRID", thread), ("X-Gmail-Labels", labels)]),
        )
        for gmail_id, thread, labels, message in plan
    ]


def mbox_bytes() -> bytes:
    parts = []
    for gmail_id, message in records():
        date = re.search(r"^Date: (.+)$", message, re.MULTILINE)
        if date is None:
            raise DemoError("record without Date")
        stamp = parsedate_to_datetime(date.group(1)).astimezone(timezone.utc)
        require(
            not re.search(r"^>*From ", message, re.MULTILINE),
            "a body line needs From-quoting",
        )
        envelope = f"From {gmail_id}@xxx {stamp.strftime('%a %b %d %H:%M:%S +0000 %Y')}"
        parts.append(f"{envelope}\n{message.rstrip(chr(10))}\n\n")
    return "".join(parts).encode("utf-8")


def write_zip(path: Path, members: dict[str, bytes]) -> None:
    """A byte-stable ZIP: fixed timestamps, permissions and member order."""
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            archive.writestr(info, data, compresslevel=9)


def files(root: Path) -> list[str]:
    return sorted(
        p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
    )


def convert(bin_dir: Path, work: Path) -> dict:
    done = subprocess.run(
        [str(bin_dir / "dead-letter"), *CONVERT.split()[1:]],
        cwd=work,
        text=True,
        capture_output=True,
        timeout=600,
    )
    return {
        "command": CONVERT,
        "exit": done.returncode,
        "stdout": done.stdout,
        "stderr": done.stderr,
    }


def capture(bin_dir: Path, work: Path) -> dict:
    members = {
        MBOX_MEMBER: mbox_bytes(),
        BROWSER_MEMBER: ARCHIVE_BROWSER.encode("utf-8"),
    }
    for name, data in members.items():
        (work / name).parent.mkdir(parents=True, exist_ok=True)
        (work / name).write_bytes(data)
    write_zip(work / ZIP_NAME, members)
    before = sha(work / ZIP_NAME)

    first = convert(bin_dir, work)
    require(
        first["exit"] == 0, f"conversion failed: {first['stderr'] or first['stdout']}"
    )
    require(sha(work / ZIP_NAME) == before, "the input archive changed")
    vault_files = files(work / "vault")
    report = json.loads((work / VAULT_OUTPUT / ".dead-letter-report.json").read_text())
    require(
        report["generator"]["version"] == bin_dir_version(bin_dir), "report version"
    )
    require(report["summary"]["errors"] == 0, "conversion reported errors")

    # A second, ordinary run into a copy of the same vault: it shows what a
    # rerun does, without touching the vault that is kept.
    rerun_root = work / "rerun"
    shutil.copytree(work / "vault", rerun_root / "vault")
    shutil.copyfile(work / ZIP_NAME, rerun_root / ZIP_NAME)
    second = convert(bin_dir, rerun_root)
    require(second["exit"] == 0, "rerun failed")
    rerun_dirs = sorted(
        p.name for p in (rerun_root / VAULT_OUTPUT).iterdir() if p.is_dir()
    )
    rerun_report = json.loads(
        (rerun_root / VAULT_OUTPUT / ".dead-letter-report.json").read_text()
    )
    return {
        "input": {
            "zip": ZIP_NAME,
            "zip_sha256": before,
            "zip_sha256_after_conversion": sha(work / ZIP_NAME),
            "members": {name: sha(work / name) for name in members},
        },
        "conversion": first,
        "vault_files": vault_files,
        "rerun_into_same_output": {
            **second,
            "bundle_directories": rerun_dirs,
            "report_outputs": [entry["output"] for entry in rerun_report["results"]],
        },
    }


def bin_dir_version(bin_dir: Path) -> str:
    return run(
        [
            str(bin_dir / "python"),
            "-I",
            "-c",
            "import dead_letter; print(dead_letter.__version__)",
        ],
        cwd=bin_dir,
    ).strip()


def publish(work: Path, out: Path) -> None:
    for name in ("vault", "Takeout"):
        target = out / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(work / name, target)
    shutil.copyfile(work / ZIP_NAME, out / ZIP_NAME)
    report = out / VAULT_OUTPUT / ".dead-letter-report.json"
    report.write_text(
        report.read_text(encoding="utf-8").replace(str(work), "<work>"),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--version", default=DEFAULT_VERSION, help="released dead-letter version"
    )
    parser.add_argument(
        "--exclude-newer", default=DEFAULT_EXCLUDE_NEWER, help="uv resolution cutoff"
    )
    parser.add_argument(
        "--python", default="3.12", help="Python request passed to uv venv"
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="sample directory"
    )
    parser.add_argument(
        "--keep-work",
        action="store_true",
        help="keep and print the temporary directory",
    )
    args = parser.parse_args()

    if not shutil.which("uv"):
        print("missing tool: uv", file=sys.stderr)
        return 2

    temp = Path(tempfile.mkdtemp(prefix="dead-letter-takeout-"))
    try:
        work = temp / "work"
        work.mkdir()
        bin_dir, release = install_release(
            work, args.version, args.exclude_newer, args.python
        )
        data = capture(bin_dir, work)
        args.out.mkdir(parents=True, exist_ok=True)
        publish(work, args.out)
        record = {
            "generated_utc": datetime.now(timezone.utc).date().isoformat(),
            "release": release,
            "host": {"platform": platform.platform()},
            **data,
        }
        text = json.dumps(record, indent=2) + "\n"
        (args.out / "capture.json").write_text(
            text.replace(str(work), "<work>"), encoding="utf-8"
        )
        print(json.dumps(data["vault_files"], indent=2))
    except (
        DemoError,
        OSError,
        subprocess.SubprocessError,
        KeyError,
        ValueError,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    finally:
        if args.keep_work:
            print(f"work directory kept: {temp}", file=sys.stderr)
        else:
            shutil.rmtree(temp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
