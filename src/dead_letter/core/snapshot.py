"""Read-only, text-only snapshots from the existing MIME/normalization pipeline.

No analysis provider is imported here. Snapshots are local evidence, not semantic
judgments; apparent authors, headers and attribution remain untrusted claims.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path

from dead_letter.core._pipeline import HtmlMarkdownFailure, _build_pipeline_snapshot
from dead_letter.core.mime import parse_eml_bytes
from dead_letter.core.types import ConvertOptions, ThreadMode, ZoneKind

SNAPSHOT_VERSION = "core-eml-snapshot-v1"
MAX_SOURCE_BYTES = 100_000_000
_RUNTIME_PACKAGES = (
    "dead-letter", "mail-parser", "mail-parser-reply", "html-to-markdown",
    "nh3", "selectolax", "pyyaml",
)
_AUTHORED_KINDS = frozenset({
    ZoneKind.BODY, ZoneKind.SIGNATURE_CANDIDATE, ZoneKind.DISCLAIMER_CANDIDATE,
    ZoneKind.SIGNATURE, ZoneKind.DISCLAIMER,
})


class SnapshotError(ValueError):
    """Safe fixed error codes; never include paths, headers or parser messages."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SnapshotZone:
    """An immutable normalized zone, before any rendered-body quote fallback.

    IDs are one-based positions in this snapshot, not raw MIME byte offsets.
    Unknown quoted/forwarded authors and dates stay None.
    """

    id: str
    kind: str
    text: str = field(repr=False)
    source_kind: str
    author: str | None = field(default=None, repr=False)
    sent_at: str | None = field(default=None, repr=False)
    attribution_subject: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class EmailSnapshot:
    """Detached evidence without raw MIME, HTML bodies or attachment payloads.

    Full diagnostics and source paths remain local and are hidden from repr.
    Dictionary accessors return new objects rather than exposing mutable state.
    """

    source: Path = field(repr=False)
    source_sha256: str
    source_size_bytes: int
    subject: str = field(repr=False)
    sender: str = field(repr=False)
    sent_at: str | None = field(repr=False)
    to: tuple[str, ...] = field(repr=False)
    cc: tuple[str, ...] = field(repr=False)
    zones: tuple[SnapshotZone, ...] = field(repr=False)
    attachment_count: int
    reply_headers_present: bool
    diagnostics_json: str = field(repr=False)
    normalization_json: str = field(repr=False)

    @property
    def diagnostics(self) -> dict:
        return json.loads(self.diagnostics_json)

    @property
    def normalization(self) -> dict:
        return json.loads(self.normalization_json)

    @property
    def normalization_version(self) -> str:
        # Include installed parser/normalizer versions, not just this helper's
        # name: a dependency upgrade can change evidence for identical EML bytes.
        fingerprint = hashlib.sha256(self.normalization_json.encode("utf-8")).hexdigest()
        return f"{SNAPSHOT_VERSION}:{fingerprint}"


def _options() -> ConvertOptions:
    return ConvertOptions(
        dry_run=True,
        thread_mode=ThreadMode.STRUCTURED,
        strip_signature_images=True,
        strip_tracking_pixels=True,
        no_calendar_summary=True,
        # Signatures/disclaimers may contain real requests. Keep their text.
        # Defaults also exclude raw HTML, all-headers output and inline data URIs.
    )


def _normalization_json(options: ConvertOptions) -> str:
    versions = {}
    for package in _RUNTIME_PACKAGES:
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return json.dumps({
        "snapshot_version": SNAPSHOT_VERSION,
        "options": asdict(options),
        "runtime_packages": versions,
        "python": f"{platform.python_implementation()}-{platform.python_version()}",
        "timestamp_policy": "source_date_header_without_inferred_timezone",
        "zone_order": "pipeline_order_not_verified_chronology",
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _read_source(path: str | Path, max_source_bytes: int) -> tuple[Path, bytes]:
    if type(max_source_bytes) is not int or not 1 <= max_source_bytes <= MAX_SOURCE_BYTES:
        raise SnapshotError("invalid_source_byte_limit")
    source = Path(path).expanduser().resolve()
    if source.suffix.lower() != ".eml":
        raise SnapshotError("expected_eml_file")
    # Nonblocking open avoids hanging on a FIFO named .eml on POSIX. Validate
    # the opened descriptor, not merely a pre-open path stat that can race.
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(source, flags)
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise SnapshotError("expected_regular_eml_file")
        if file_stat.st_size > max_source_bytes:
            raise SnapshotError("source_byte_limit_exceeded")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(max_source_bytes + 1)
        if len(raw) > max_source_bytes:
            raise SnapshotError("source_byte_limit_exceeded")
        return source, raw
    finally:
        os.close(descriptor)


def read_snapshot(
    path: str | Path,
    *,
    max_source_bytes: int = MAX_SOURCE_BYTES,
) -> EmailSnapshot:
    """Read a single EML once; normalize those exact bytes without writing files.

    Uses the existing parser, image filtering, HTML/plain zoning, attribution,
    cleanup and diagnostic paths. Does not use convert/bundle writer entrypoints.
    Signature text is retained; attachment/calendar contents are not evidence.
    No URLs, external parent messages, OCR or provider credentials are accessed.
    """
    try:
        source, raw = _read_source(path, max_source_bytes)
        source_sha256 = hashlib.sha256(raw).hexdigest()
        source_size_bytes = len(raw)
        parsed = parse_eml_bytes(
            raw, source=source, include_attachment_payloads=False,
            include_inline_data_uris=False,
        )
        del raw
        options = _options()
        _result, parsed, _rendered, diagnostics, threaded = _build_pipeline_snapshot(
            source, options, include_attachment_payloads=False, parsed=parsed,
        )
        headers = {name.lower(): value for name, value in parsed.headers.items()}
        # Preserve original date/offset evidence. Do not manufacture a timezone
        # from a parser-normalized datetime or from the processing environment.
        sent_at = headers.get("date") or None
        sender = headers.get("from") or parsed.sender
        zones = tuple(
            SnapshotZone(
                id=f"zone-{index:04d}", kind=zone.kind.value, text=zone.content,
                source_kind=zone.source_kind,
                author=sender if zone.kind in _AUTHORED_KINDS
                else zone.metadata.get("attribution_from"),
                sent_at=sent_at if zone.kind in _AUTHORED_KINDS
                else zone.metadata.get("attribution_date"),
                attribution_subject=zone.metadata.get("attribution_subject"),
            )
            for index, zone in enumerate(threaded.zones, start=1)
        )
        local_diagnostics = diagnostics or {}
        attachment_count = local_diagnostics.get("attachments", {}).get(
            "referenced", len(parsed.attachments),
        )
        return EmailSnapshot(
            source=source, source_sha256=source_sha256, source_size_bytes=source_size_bytes,
            subject=parsed.subject, sender=sender, sent_at=sent_at,
            to=(headers["to"],) if headers.get("to") else (),
            cc=(headers["cc"],) if headers.get("cc") else (),
            zones=zones, attachment_count=attachment_count,
            reply_headers_present=bool(headers.get("in-reply-to") or headers.get("references")),
            diagnostics_json=json.dumps(local_diagnostics, ensure_ascii=False, allow_nan=False),
            normalization_json=_normalization_json(options),
        )
    except SnapshotError:
        raise
    except FileNotFoundError:
        raise SnapshotError("source_not_found") from None
    except PermissionError:
        raise SnapshotError("source_not_readable") from None
    except IsADirectoryError:
        raise SnapshotError("expected_regular_eml_file") from None
    except HtmlMarkdownFailure:
        raise SnapshotError("html_markdown_failed") from None
    except OSError:
        raise SnapshotError("source_read_failed") from None
    except Exception:
        # Parsing malformed/untrusted MIME must not surface the parser's body-
        # bearing exception text. KeyboardInterrupt/SystemExit still propagate.
        raise SnapshotError("normalization_failed") from None
