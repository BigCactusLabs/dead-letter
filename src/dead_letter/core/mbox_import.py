"""Streaming MBOX conversion through the existing EML pipeline."""

from __future__ import annotations

import json
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
from dead_letter.core.mbox_isolation import MboxBudgetError, WorkerBudgets
from dead_letter.core.render import serialize_markdown
from dead_letter.core.types import ConvertOptions


@dataclass(slots=True)
class MboxConversion:
    """Detached result: never retains MIME payloads or temporary paths."""

    source: str
    output: Path | None
    success: bool
    mbox: dict[str, Any] | None = None
    diagnostics: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    recovery: dict[str, Any] | None = None


def _convert_record(
    record: MboxRecord,
    source: Path,
    root: Path,
    options: ConvertOptions,
    *,
    bundles: bool,
    unescape: UnescapeMode,
    archive: dict[str, Any] | None = None,
    reraise: tuple[type[BaseException], ...] = (),
) -> MboxConversion:
    locator = f"{source.name}#message-{record.index:08d}"
    provenance = {**record.provenance(source), "unescape": unescape}
    if archive is not None:
        provenance["archive"] = archive["container_basename"]
        provenance["container"] = dict(archive)
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
        if not isinstance(exc, Exception) or isinstance(exc, reraise):
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
    timeout_seconds: float | None = None,
    memory_limit_mib: int | None = None,
    cpu_seconds: int | None = None,
    max_output_mib: int | None = None,
    resume: bool = False,
) -> Iterator[MboxConversion]:
    """Lazily convert an immutable exported mailbox; never delete the archive.

    A result with ``mbox is None`` is a fatal archive-level failure, not another
    message. Otherwise failures are isolated to that record. Consume results as
    an iterator, not ``list(convert_mbox(...))``, to keep memory bounded. Close
    the iterator with ``contextlib.closing`` when cancelling/ending early.
    A positive ``timeout_seconds`` opts into a fresh subprocess per admitted
    message, including dry runs. This is a worker wall-time budget, not a memory
    limit, security sandbox, or deadline for framing/final output publication.

    ``memory_limit_mib``, ``cpu_seconds`` and ``max_output_mib`` opt into
    per-worker resource limits and require ``timeout_seconds``. A budget this
    platform cannot enforce raises ``MboxBudgetError`` before any conversion.
    They are resource limits, not filesystem or network isolation.

    ``resume=True`` opts into a source/options-bound journal for Markdown or
    bundles. Reuse verifies every output hash; conflicts stop without overwriting.
    Flat publication needs hard links; bundles need exclusive directory rename.
    Dry runs and compressed input are unsupported. Results include ``recovery``.
    See docs/reference/mbox-resume.md for filesystem and durability limits.
    """
    yield from _convert_mbox(
        path, output=output, options=options, limits=limits, unescape=unescape,
        bundles=bundles, timeout_seconds=timeout_seconds,
        memory_limit_mib=memory_limit_mib, cpu_seconds=cpu_seconds, max_output_mib=max_output_mib,
        resume=resume,
    )


def _worker_budgets(
    timeout_seconds: float | None, memory_limit_mib: int | None,
    cpu_seconds: int | None, max_output_mib: int | None,
) -> WorkerBudgets | None:
    """Validate worker-mode options before any conversion; ``None`` means in-process."""
    budget_values = (memory_limit_mib, cpu_seconds, max_output_mib)
    if timeout_seconds is None and all(value is None for value in budget_values):
        return None
    from dead_letter.core.mbox_isolation import validate_budgets, validate_timeout
    validate_timeout(timeout_seconds)
    budgets = WorkerBudgets(*budget_values)
    validate_budgets(budgets, worker_mode=timeout_seconds is not None)
    return budgets


def _convert_mbox(
    path: str | Path,
    *,
    output: str | Path | None = None,
    options: ConvertOptions | None = None,
    limits: MboxLimits | None = None,
    unescape: UnescapeMode = "preserve",
    bundles: bool = False,
    timeout_seconds: float | None = None,
    memory_limit_mib: int | None = None,
    cpu_seconds: int | None = None,
    max_output_mib: int | None = None,
    archive: dict[str, Any] | None = None,
    resume: bool = False,
) -> Iterator[MboxConversion]:
    # Both public importers use this pipeline; only the provenance differs.
    budgets = _worker_budgets(timeout_seconds, memory_limit_mib, cpu_seconds, max_output_mib)
    if budgets is not None:
        from dead_letter.core.mbox_isolation import convert_record_isolated
    source = Path(path).expanduser().resolve()
    opts = options or ConvertOptions()
    if opts.delete_eml:
        raise ValueError("--delete-eml is not supported for MBOX; the archive is always preserved")
    if unescape not in {"preserve", "mboxrd", "mboxo", "mboxcl", "mboxcl2"}:
        raise ValueError(f"Unsupported MBOX unescape mode: {unescape}")
    root = Path(output).expanduser().resolve() if output is not None else source.with_suffix(".markdown")
    if root == source or root.suffix.lower() == ".md" or (root.exists() and not root.is_dir()):
        raise ValueError("MBOX output must be a directory distinct from the source")
    label = Path(archive["member_name"]) if archive is not None else source
    provenance_options = {"archive": archive} if archive is not None else {}
    opts = replace(opts, delete_eml=False)
    if type(resume) is not bool:
        raise ValueError("MBOX resume must be a boolean")
    if resume:
        if type(bundles) is not bool:
            raise ValueError("MBOX resume bundles must be a boolean")
        if opts.dry_run or archive is not None:
            raise ValueError("MBOX resume does not support dry runs or compressed input")
        if source.suffix.lower() != ".mbox" or not source.is_file():
            raise ValueError("MBOX resume requires an existing flat .mbox file")
        from dead_letter.core.mbox_resume import convert_mbox_resumable
        yield from convert_mbox_resumable(
            source, root, opts, limits or MboxLimits(), unescape=unescape,
            timeout_seconds=timeout_seconds, budgets=budgets, bundles=bundles,
        )
        return
    try:
        with closing(iter_mbox(source, limits=limits, unescape=unescape)) as records:
            for record in records:
                if timeout_seconds is not None and record.path is not None:
                    yield convert_record_isolated(
                        record, label, root, opts, bundles=bundles,
                        unescape=unescape, timeout=timeout_seconds, budgets=budgets,
                        **provenance_options,
                    )
                else:
                    yield _convert_record(record, label, root, opts, bundles=bundles, unescape=unescape, **provenance_options)
    except MboxBudgetError as exc:
        # A worker could not apply a requested budget: abort the whole import.
        yield MboxConversion(
            label.name, None, False,
            error={"code": exc.code, "stage": "worker", "message": exc.message},
        )
    except (MboxFormatError, OSError, ValueError) as exc:
        message = str(exc).replace(str(source), label.name)
        if archive is not None:
            message = json.dumps(message[:512], ensure_ascii=True)
        yield MboxConversion(
            label.name, None, False,
            error={"code": "mbox_archive_error", "stage": "mbox",
                   "message": message},
        )
