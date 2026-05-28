#!/usr/bin/env python3
"""Token-cost benchmark for dead-letter.

Measures, per email, how many LLM tokens each representation costs:

* **raw**         — the ``.eml`` bytes verbatim (the "just paste the file" ceiling;
                    nobody should do this, but people do).
* **naive-plain** — stdlib ``email`` ``get_body()`` preferring text/plain. The
                    charitable baseline: clean text when a good plain part exists,
                    a useless stub when it doesn't, and attachments always dropped.
* **naive-html**  — the text/html part with tags crudely regex-stripped. What you
                    reach for when the plain part is missing or junk.
* **dead-letter** — the real ``convert()`` output (``default`` preset).

It also records **attachment retention**: whether the attachment filename survives
into each representation. Token count alone understates dead-letter's value — the
naive baselines are sometimes cheaper precisely because they silently drop content.

Tokenizer counts are model-relative. We disclose the encoding in every report.

Usage:
    uv run python benchmarks/run.py                  # human-readable + Markdown table
    uv run python benchmarks/run.py --encoding cl100k_base
    uv run python benchmarks/run.py --markdown-only  # just the table (for the README)
"""

from __future__ import annotations

import argparse
import html
import re
import statistics
import sys
import tempfile
from dataclasses import dataclass
from email import message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path

# dead-letter core lives in src/; make it importable when run from the repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from dead_letter.core import ConvertOptions, ThreadMode, convert  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# `default` preset stripping, but STRUCTURED thread mode so the dead-letter
# artifact carries the *same information* as the naive baselines (full thread,
# not just the latest reply). This is the fair, apples-to-apples comparison.
# The shipping `LATEST` default would score even lower by dropping quoted
# history — a real saving, but not a same-information one, so we don't claim it.
DEFAULT_OPTIONS = ConvertOptions(
    strip_signatures=True,
    strip_tracking_pixels=True,
    strip_signature_images=True,
    allow_fallback_on_html_error=True,
    allow_html_repair_on_panic=True,
    thread_mode=ThreadMode.STRUCTURED,
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")


# --------------------------------------------------------------------------
# Tokenizer
# --------------------------------------------------------------------------

def get_encoder(encoding: str):
    try:
        import tiktoken  # pyright: ignore[reportMissingImports]
    except ModuleNotFoundError:
        sys.exit(
            "tiktoken is required. Install the benchmark extra:\n"
            "    uv pip install -e '.[benchmark]'"
        )
    return tiktoken.get_encoding(encoding)


def count(enc, text: str) -> int:
    return len(enc.encode(text, disallowed_special=()))


# --------------------------------------------------------------------------
# Baselines
# --------------------------------------------------------------------------

def raw_text(path: Path) -> str:
    # Decode permissively; raw bytes are what a "paste the file" user feeds in.
    return path.read_bytes().decode("utf-8", errors="replace")


def _parse(path: Path) -> EmailMessage:
    return message_from_bytes(path.read_bytes(), policy=policy.default)  # type: ignore[return-value]


def naive_plain(msg: EmailMessage) -> str:
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is None:
        return ""
    content = body.get_content()
    if body.get_content_subtype() == "html":
        content = _strip_html(content)
    return content.strip()


def naive_html(msg: EmailMessage) -> str:
    body = msg.get_body(preferencelist=("html", "plain"))
    if body is None:
        return ""
    content = body.get_content()
    if body.get_content_subtype() == "html":
        content = _strip_html(content)
    return content.strip()


def _strip_html(markup: str) -> str:
    # Deliberately naive: drop scripts/styles, strip tags, unescape, collapse ws.
    markup = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", markup,
                    flags=re.DOTALL | re.IGNORECASE)
    text = _TAG_RE.sub(" ", markup)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = _BLANKLINES_RE.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines())


def attachment_names(msg: EmailMessage) -> list[str]:
    names = []
    for part in msg.iter_attachments():
        name = part.get_filename()
        if name:
            names.append(name)
    return names


def dead_letter_markdown(path: Path, tmp: Path) -> tuple[str, bool, str]:
    result = convert(path, output=tmp, options=DEFAULT_OPTIONS)
    if not result.success or result.output is None:
        return "", False, result.error or "conversion failed"
    return Path(result.output).read_text(encoding="utf-8"), True, ""


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------

@dataclass
class Row:
    name: str
    category: str
    raw: int
    naive_plain: int
    naive_html: int
    dead_letter: int
    attachments: int            # count of real attachments in the source
    att_kept_dl: bool           # attachment name present in dead-letter output
    att_kept_naive: bool        # attachment name present in either naive baseline
    error: str = ""


def measure(path: Path, enc, tmp: Path) -> Row:
    category = path.stem.split("__", 1)[0]
    msg = _parse(path)
    np_text = naive_plain(msg)
    nh_text = naive_html(msg)
    dl_text, ok, err = dead_letter_markdown(path, tmp)

    names = attachment_names(msg)
    att_kept_dl = bool(names) and all(n in dl_text for n in names)
    att_kept_naive = bool(names) and all(
        (n in np_text) or (n in nh_text) for n in names
    )

    return Row(
        name=path.stem,
        category=category,
        raw=count(enc, raw_text(path)),
        naive_plain=count(enc, np_text),
        naive_html=count(enc, nh_text),
        dead_letter=count(enc, dl_text) if ok else -1,
        attachments=len(names),
        att_kept_dl=att_kept_dl,
        att_kept_naive=att_kept_naive,
        error=err,
    )


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def _med(values: list[int]) -> int:
    return int(statistics.median(values)) if values else 0


def _pct_reduction(frm: int, to: int) -> str:
    if frm <= 0:
        return "—"
    return f"{(1 - to / frm) * 100:.0f}%"


def markdown_table(rows: list[Row], encoding: str) -> str:
    cats: dict[str, list[Row]] = {}
    for r in rows:
        cats.setdefault(r.category, []).append(r)

    out: list[str] = []
    out.append("| Category | N | Raw `.eml` | Naive (plain) | Naive (HTML) | "
               "dead-letter | vs raw | Attachments kept |")
    out.append("|---|---:|---:|---:|---:|---:|---:|:--:|")

    all_raw, all_np, all_nh, all_dl = [], [], [], []
    for cat in sorted(cats):
        rs = [r for r in cats[cat] if r.dead_letter >= 0]
        if not rs:
            continue
        raw_m = _med([r.raw for r in rs])
        np_m = _med([r.naive_plain for r in rs])
        nh_m = _med([r.naive_html for r in rs])
        dl_m = _med([r.dead_letter for r in rs])
        all_raw += [r.raw for r in rs]
        all_np += [r.naive_plain for r in rs]
        all_nh += [r.naive_html for r in rs]
        all_dl += [r.dead_letter for r in rs]

        att_total = sum(1 for r in rs if r.attachments)
        if att_total:
            kept = sum(1 for r in rs if r.attachments and r.att_kept_dl)
            att_cell = f"{kept}/{att_total}"
        else:
            att_cell = "n/a"

        out.append(
            f"| {cat} | {len(rs)} | {raw_m:,} | {np_m:,} | {nh_m:,} | "
            f"{dl_m:,} | {_pct_reduction(raw_m, dl_m)} | {att_cell} |"
        )

    out.append(
        f"| **all** | **{len(all_dl)}** | **{_med(all_raw):,}** | "
        f"**{_med(all_np):,}** | **{_med(all_nh):,}** | **{_med(all_dl):,}** | "
        f"**{_pct_reduction(_med(all_raw), _med(all_dl))}** | — |"
    )

    note = (
        f"\n_Tokenizer: `{encoding}` (tiktoken). Counts are model-relative; "
        "absolute numbers shift across tokenizers but the ratios hold. "
        "Medians shown per category. Corpus is synthetic-but-representative — "
        "see `benchmarks/fixtures/generate.py`._"
    )
    return "\n".join(out) + "\n" + note


def human_report(rows: list[Row]) -> str:
    lines = ["", "Per-email detail:", ""]
    w = max(len(r.name) for r in rows)
    lines.append(f"  {'fixture'.ljust(w)}   raw   nv-pl  nv-htm   dead-letter  att")
    for r in sorted(rows, key=lambda x: (x.category, x.name)):
        if r.dead_letter < 0:
            lines.append(f"  {r.name.ljust(w)}  ERROR: {r.error}")
            continue
        att = "—"
        if r.attachments:
            att = "kept" if r.att_kept_dl else "LOST"
        lines.append(
            f"  {r.name.ljust(w)}  {r.raw:>6,} {r.naive_plain:>6,} "
            f"{r.naive_html:>6,}  {r.dead_letter:>10,}  {att}"
        )
    # Retention headline.
    with_att = [r for r in rows if r.attachments]
    if with_att:
        dl_keep = sum(1 for r in with_att if r.att_kept_dl)
        nv_keep = sum(1 for r in with_att if r.att_kept_naive)
        lines += [
            "",
            f"Attachment retention: dead-letter {dl_keep}/{len(with_att)}, "
            f"naive {nv_keep}/{len(with_att)}.",
        ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--encoding", default="o200k_base",
                    help="tiktoken encoding (default: o200k_base, GPT-4o-class)")
    ap.add_argument("--fixtures", type=Path, default=FIXTURES_DIR)
    ap.add_argument("--markdown-only", action="store_true",
                    help="print only the Markdown table (for pasting into the README)")
    args = ap.parse_args()

    enc = get_encoder(args.encoding)
    paths = sorted(args.fixtures.glob("*.eml"))
    if not paths:
        sys.exit(f"No .eml fixtures in {args.fixtures}. Run generate.py first.")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rows = [measure(p, enc, tmp) for p in paths]

    table = markdown_table(rows, args.encoding)
    if args.markdown_only:
        print(table)
        return

    print(f"dead-letter token-cost benchmark  ({len(rows)} emails, {args.encoding})")
    print("=" * 72)
    print(table)
    print(human_report(rows))


if __name__ == "__main__":
    main()
