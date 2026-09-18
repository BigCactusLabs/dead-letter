"""Streaming MBOX conversion through the existing EML pipeline."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from dead_letter.core._pipeline import (
    _build_rendered_markdown,
    _collision_safe_bundle_dir,
    _convert_error_metadata,
    _open_collision_safe_output,
    _write_attachment_parts,
)
from dead_letter.core.mbox import MboxFormatError, MboxLimits, MboxRecord, UnescapeMode, iter_mbox
from dead_letter.core.render import serialize_markdown
from dead_letter.core.types import ConvertOptions


@dataclass(slots=True)
class MboxConversion:
    """Detached result: never retains MIME payloads or temporary paths."""

    source: str
    output: Path | None
    success: bool
    mbox: dict[str, str | int] | None = None
    diagnostics: dict[str, Any] | None = None
    error: dict[str, str] | None = None


def _convert_record(
    record: MboxRecord,
    source: Path,
    root: Path,
    options: ConvertOptions,
    *,
    bundles: bool,
    unescape: UnescapeMode,
) -> MboxConversion:
    locator = f"{source.name}#message-{record.index:08d}"
    provenance = {**record.provenance(source), "unescape": unescape}
    if record.path is None:
        return MboxConversion(
            locator, None, False, provenance,
            error={"code": record.error_code or "mbox_invalid_message",
                   "message": "Stored message is empty or exceeds configured resource limits",
                   "stage": "mbox"},
        )
    stem = f"{record.index:08d}-{record.sha256[:16]}"
    created: Path | None = None
    bundle: Path | None = None
    try:
        _result, parsed, rendered, diagnostics = _build_rendered_markdown(
            record.path, options, include_attachment_payloads=bundles,
        )
        # Source provenance describes the archive, never our ephemeral EML path.
        rendered.front_matter["source"] = "source.eml" if bundles else locator
        rendered.front_matter["source_mbox"] = provenance
        headers = {key.lower(): value for key, value in parsed.headers.items()}
        for header, field in (
            ("x-gmail-labels", "gmail_labels"),
            ("message-id", "message_id"),
            ("x-gm-thrid", "gmail_thread_id"),
        ):
            if header in headers:
                # Preserve the entire decoded value, not a lossy comma split.
                rendered.front_matter[field] = headers[header]
        if options.dry_run:
            return MboxConversion(locator, None, True, provenance, diagnostics)
        if bundles:
            bundle = _collision_safe_bundle_dir(root / stem)
            attachments = _write_attachment_parts(parsed.attachment_parts, bundle / "attachments")
            if attachments:
                rendered.front_matter["attachment_files"] = [
                    path.relative_to(bundle).as_posix() for path in attachments
                ]
            shutil.copyfile(record.path, bundle / "source.eml")
            created = bundle / "message.md"
            created.write_text(serialize_markdown(rendered), encoding="utf-8")
        else:
            handle, created = _open_collision_safe_output(root / f"{stem}.md")
            with handle:
                handle.write(serialize_markdown(rendered))
        return MboxConversion(locator, created.resolve(), True, provenance, diagnostics)
    except BaseException as exc:
        # Fault isolation at the record boundary. KeyboardInterrupt/SystemExit
        # deliberately propagate. Never include raw email or temp paths in errors.
        if bundle is not None:
            shutil.rmtree(bundle, ignore_errors=True)
        elif created is not None:
            try:
                created.unlink(missing_ok=True)
            except OSError:
                pass
        if not isinstance(exc, Exception):
            raise
        code, _fallback, _repair = _convert_error_metadata(exc)
        return MboxConversion(
            locator, None, False, provenance,
            error={"code": code or "conversion_error", "stage": "core",
                   "message": f"Message conversion failed ({type(exc).__name__})"},
        )


def convert_mbox(
    path: str | Path,
    *,
    output: str | Path | None = None,
    options: ConvertOptions | None = None,
    limits: MboxLimits | None = None,
    unescape: UnescapeMode = "preserve",
    bundles: bool = False,
) -> Iterator[MboxConversion]:
    """Lazily convert an immutable exported mailbox; never delete the archive.

    A result with ``mbox is None`` is a fatal archive-level failure, not another
    message. Otherwise failures are isolated to that record. Consume results as
    an iterator, not ``list(convert_mbox(...))``, to keep memory bounded. Close
    the iterator with ``contextlib.closing`` when cancelling/ending early.
    """
    source = Path(path).expanduser().resolve()
    opts = options or ConvertOptions()
    if opts.delete_eml:
        raise ValueError("--delete-eml is not supported for MBOX; the archive is always preserved")
    if unescape not in {"preserve", "mboxrd", "mboxo"}:
        raise ValueError(f"Unsupported MBOX unescape mode: {unescape}")
    root = Path(output).expanduser().resolve() if output is not None else source.with_suffix(".markdown")
    if root == source or root.suffix.lower() == ".md" or (root.exists() and not root.is_dir()):
        raise ValueError("MBOX output must be a directory distinct from the source")
    opts = replace(opts, delete_eml=False)
    try:
        with closing(iter_mbox(source, limits=limits, unescape=unescape)) as records:
            for record in records:
                yield _convert_record(record, source, root, opts, bundles=bundles, unescape=unescape)
    except (MboxFormatError, OSError, ValueError) as exc:
        yield MboxConversion(
            source.name, None, False,
            error={"code": "mbox_archive_error", "stage": "mbox",
                   "message": str(exc).replace(str(source), source.name)},
        )
