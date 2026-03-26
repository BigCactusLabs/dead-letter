"""Public conversion API orchestration for dead-letter core."""

from __future__ import annotations

import re
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from dead_letter.core.calendar import summarize_calendar_parts
from dead_letter.core.html_conversation import segment_html_conversation
from dead_letter.core.html import html_has_italic_nodes, html_to_markdown, unwrap_italic_tags
from dead_letter.core.image_filter import filter_images
from dead_letter.core.mime import parse_eml
from dead_letter.core.render import render_markdown, serialize_markdown
from dead_letter.core.sanitize import sanitize_html
from dead_letter.core.slugs import slugify_subject
from dead_letter.core.threads import build_zones
from dead_letter.core.types import (
    AttachmentPart,
    BundleResult,
    ConvertOptions,
    ConvertResult,
    ParsedEmail,
    RenderedMarkdown,
    StrippedImage,
    ThreadedContent,
    Zone,
)
from dead_letter.core.zone_cleanup import cleanup_zones

_MAX_COLLISION_INDEX = 10_000


class HtmlMarkdownFailure(RuntimeError):
    """Structured HTML-to-Markdown conversion failure."""

    def __init__(
        self,
        message: str,
        *,
        plain_text_fallback_available: bool,
        html_repair_available: bool,
    ) -> None:
        super().__init__(message)
        self.plain_text_fallback_available = plain_text_fallback_available
        self.html_repair_available = html_repair_available


def _validate_source(path: str | Path) -> Path:
    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(str(source))
    if source.suffix.lower() != ".eml":
        raise ValueError(f"Expected a .eml file: {source}")
    return source


def _slug_for_output(subject: str, source: Path) -> str:
    # Prefer normalized subject; fall back to source stem for empty subjects.
    if subject.strip():
        return slugify_subject(subject)
    return slugify_subject(source.stem)


def _resolve_output_target(source: Path, subject: str, output: str | Path | None) -> Path:
    slug = _slug_for_output(subject, source)

    if output is None:
        return source.parent / f"{slug}.md"

    output_path = Path(output).expanduser()
    if output_path.suffix.lower() == ".md":
        return output_path
    return output_path / f"{slug}.md"


def _collision_safe_target(target: Path) -> Path:
    candidate = target
    stem = target.stem
    suffix = target.suffix
    index = 2

    while candidate.exists():
        if index > _MAX_COLLISION_INDEX:
            raise RuntimeError(f"collision-safe output naming exhausted for {target}")
        candidate = target.with_name(f"{stem}-{index}{suffix}")
        index += 1

    return candidate


def _collision_safe_bundle_dir(target: Path) -> Path:
    candidate = target
    index = 2

    while candidate.exists():
        if index > _MAX_COLLISION_INDEX:
            raise RuntimeError(f"collision-safe output naming exhausted for {target}")
        candidate = target.with_name(f"{target.name}-{index}")
        index += 1

    return candidate


def _rewrite_inline_image_refs(
    markdown: str,
    parsed: ParsedEmail,
    *,
    embed_inline_images: bool,
    stripped_cids: set[str] | None = None,
) -> str:
    updated = markdown
    _stripped = stripped_cids or set()
    if embed_inline_images:
        for cid, data_uri in parsed.inline_cid_to_data_uri.items():
            if cid not in _stripped:
                updated = updated.replace(f"(cid:{cid})", f"({data_uri})")
    for cid in _stripped:
        updated = re.sub(rf"!\[[^\]]*\]\(cid:{re.escape(cid)}\)\s*", "", updated)
    return updated


def _threaded_content_from_conversation(
    parsed: ParsedEmail,
    options: ConvertOptions,
    *,
    filtered_html_body: str | None = None,
    stripped_cids: set[str] | None = None,
) -> tuple[ThreadedContent | None, str | None, dict[str, str] | None]:
    html_body = filtered_html_body if filtered_html_body is not None else parsed.html_body
    if not html_body:
        return None, None, None

    conversation = segment_html_conversation(html_body)
    if not conversation.rules_triggered:
        return None, None, None

    cleaned = cleanup_zones(conversation.zones, options)
    zones: list[Zone] = []
    for zone in cleaned:
        rendered = zone.content.strip()
        if zone.source_kind == "html":
            rendered = html_to_markdown(zone.content, include_raw_html=False).markdown
            rendered = _rewrite_inline_image_refs(
                rendered,
                parsed,
                embed_inline_images=options.embed_inline_images,
                stripped_cids=stripped_cids,
            )

        rendered = rendered.strip()
        if not rendered:
            continue

        metadata = dict(zone.metadata)
        if zone.client_hint:
            metadata["client_hint"] = zone.client_hint
        zones.append(Zone(kind=zone.kind, content=rendered, metadata=metadata))

    if not zones:
        return None, None, None

    raw_html = sanitize_html(html_body) if options.include_raw_html else None
    return (
        ThreadedContent(zones=zones),
        raw_html,
        {
            "client_hint": conversation.client_hint or "generic",
            "confidence": "high",
            "segmentation_path": "html",
        },
    )


def _defect_warnings(parsed: ParsedEmail) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    for defect in parsed.defects:
        warnings.append(
            {
                "code": defect.code,
                "message": defect.message,
                "severity": "warning",
            }
        )
    return warnings


def _client_hint_from_quote_patterns(patterns: set[str]) -> str:
    if "gmail" in patterns:
        return "gmail"
    if "outlook" in patterns:
        return "outlook"
    return "generic"


def _quality_state(*, confidence: str, warnings: list[dict[str, str]]) -> str:
    if confidence == "low":
        return "review_recommended"
    if warnings:
        return "degraded"
    return "normal"


def _build_diagnostics_summary(
    parsed: ParsedEmail,
    *,
    segmentation_path: str,
    client_hint: str | None,
    confidence: str,
    fallback_used: str | None = None,
    warnings: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    summary_warnings = list(warnings or [])
    summary_warnings.extend(_defect_warnings(parsed))
    return {
        "state": _quality_state(confidence=confidence, warnings=summary_warnings),
        "selected_body": parsed.selected_body_kind or "plain",
        "segmentation_path": segmentation_path,
        "client_hint": client_hint,
        "confidence": confidence,
        "fallback_used": fallback_used,
        "warnings": summary_warnings,
    }


def _html_failure_warning(message: str) -> dict[str, str]:
    return {
        "code": "html_markdown_failed",
        "message": message,
        "severity": "warning",
    }


def _html_repair_warning(message: str) -> dict[str, str]:
    return {
        "code": "html_markdown_repaired",
        "message": message,
        "severity": "warning",
    }


def _convert_error_metadata(exc: Exception) -> tuple[str | None, bool | None, bool | None]:
    if isinstance(exc, HtmlMarkdownFailure):
        return "html_markdown_failed", exc.plain_text_fallback_available, exc.html_repair_available
    return None, None, None


def _build_rendered_markdown(
    source: Path,
    options: ConvertOptions,
) -> tuple[ConvertResult, ParsedEmail, RenderedMarkdown, dict[str, Any] | None]:
    parsed = parse_eml(source)

    stripped_images: list[StrippedImage] = []
    filtered_html_body = parsed.html_body
    if parsed.html_body and (options.strip_signature_images or options.strip_tracking_pixels):
        filtered_html_body, stripped_images = filter_images(
            parsed.html_body,
            strip_signature_images=options.strip_signature_images,
            strip_tracking_pixels=options.strip_tracking_pixels,
        )
    stripped_cids = {
        s.reference.removeprefix("cid:")
        for s in stripped_images
        if s.reference.startswith("cid:")
    }

    raw_html: str | None = None
    threaded: ThreadedContent | None = None
    diagnostics: dict[str, Any] | None = None
    html_failure_message: str | None = None

    if parsed.selected_body_kind == "html":
        try:
            threaded, raw_html, html_context = _threaded_content_from_conversation(
                parsed, options, filtered_html_body=filtered_html_body, stripped_cids=stripped_cids,
            )
        except RuntimeError as exc:
            html_context = None
            html_failure_message = str(exc)
        if html_context is not None:
            diagnostics = _build_diagnostics_summary(
                parsed,
                segmentation_path=html_context["segmentation_path"],
                client_hint=html_context["client_hint"],
                confidence=html_context["confidence"],
            )

    if threaded is None:
        quote_patterns: set[str] = set()
        text_for_threading = parsed.text_body
        segmentation_path = "plain_fallback"
        client_hint = "generic"
        confidence = "medium"
        fallback_used = "plain_text_reply_parser" if parsed.selected_body_kind != "html" else None
        diagnostics_warnings: list[dict[str, str]] = []
        html_repair_available = False
        repaired_html_after_panic = False

        if filtered_html_body:
            html_result = None
            html_repair_available = html_has_italic_nodes(filtered_html_body)
            if html_failure_message is None:
                try:
                    html_result = html_to_markdown(filtered_html_body, include_raw_html=options.include_raw_html)
                except RuntimeError as exc:
                    html_failure_message = str(exc)
            if html_failure_message is not None and options.allow_html_repair_on_panic and html_repair_available:
                original_failure_message = html_failure_message
                try:
                    html_result = html_to_markdown(
                        unwrap_italic_tags(filtered_html_body),
                        include_raw_html=options.include_raw_html,
                    )
                except RuntimeError:
                    pass
                else:
                    html_failure_message = None
                    repaired_html_after_panic = True
                    diagnostics_warnings.append(_html_repair_warning(original_failure_message))

            if html_failure_message is not None:
                plain_text_available = bool(parsed.text_body.strip())
                if not options.allow_fallback_on_html_error or not plain_text_available:
                    raise HtmlMarkdownFailure(
                        html_failure_message,
                        plain_text_fallback_available=plain_text_available,
                        html_repair_available=html_repair_available,
                    )
                raw_html = sanitize_html(filtered_html_body) if options.include_raw_html else None
                client_hint = "generic"
                confidence = "low"
                fallback_used = "html_failure_plain_text_fallback"
                diagnostics_warnings.append(_html_failure_warning(html_failure_message))
            elif html_result is not None:
                if html_result.markdown.strip():
                    text_for_threading = html_result.markdown
                    quote_patterns = html_result.quote_patterns
                    raw_html = html_result.raw_html
                    text_for_threading = _rewrite_inline_image_refs(
                        text_for_threading,
                        parsed,
                        embed_inline_images=options.embed_inline_images,
                        stripped_cids=stripped_cids,
                    )
                    if quote_patterns:
                        segmentation_path = "plain_fallback"
                        client_hint = _client_hint_from_quote_patterns(quote_patterns)
                        confidence = "medium" if repaired_html_after_panic else "low"
                        fallback_used = (
                            "html_markdown_panic_repaired" if repaired_html_after_panic else "plain_text_reply_parser"
                        )
                    else:
                        segmentation_path = "html"
                        client_hint = "generic"
                        confidence = "medium"
                        fallback_used = "html_markdown_panic_repaired" if repaired_html_after_panic else None

        threaded = build_zones(text_for_threading, quote_patterns=quote_patterns, options=options)
        if diagnostics is None:
            diagnostics = _build_diagnostics_summary(
                parsed,
                segmentation_path=segmentation_path,
                client_hint=client_hint,
                confidence=confidence,
                fallback_used=fallback_used,
                warnings=diagnostics_warnings,
            )

    calendar_summaries: list[str] = []
    if not options.no_calendar_summary:
        calendar_summaries = summarize_calendar_parts(parsed.calendar_parts)

    rendered = render_markdown(
        parsed,
        threaded,
        calendar_summaries=calendar_summaries,
        include_all_headers=options.include_all_headers,
        include_raw_html=options.include_raw_html,
        raw_html=raw_html,
    )

    result = ConvertResult(
        source=source,
        output=None,
        subject=parsed.subject,
        sender=parsed.sender,
        date=parsed.date,
        attachments=parsed.attachments,
        success=True,
        error=None,
        dry_run=options.dry_run,
    )
    if diagnostics is not None and stripped_images:
        diagnostics["stripped_images"] = [
            {"category": s.category.value, "reason": s.reason, "reference": s.reference}
            for s in stripped_images
        ]

    return result, parsed, rendered, diagnostics


def _run_pipeline(source: Path, options: ConvertOptions) -> tuple[ConvertResult, str]:
    result, _parsed, rendered, _diagnostics = _build_rendered_markdown(source, options)
    return result, serialize_markdown(rendered)


def _bundle_slug(source: Path) -> str:
    return source.stem or "message"


def _write_attachment_parts(parts: list[AttachmentPart], target_dir: Path) -> list[Path]:
    written: list[Path] = []
    if not parts:
        return written

    target_dir.mkdir(parents=True, exist_ok=True)
    for part in parts:
        target = _collision_safe_target(target_dir / part.filename)
        target.write_bytes(part.payload)
        written.append(target)
    return written


def _iter_source_eml_files(source_dir: Path) -> list[Path]:
    files: list[Path] = []

    for candidate in source_dir.rglob("*"):
        if candidate.suffix.lower() != ".eml" or not candidate.is_file():
            continue
        try:
            if not candidate.resolve().is_relative_to(source_dir):
                continue
        except OSError:
            continue
        files.append(candidate)

    return sorted(files)


def convert(
    path: str | Path,
    *,
    output: str | Path | None = None,
    options: ConvertOptions | None = None,
) -> ConvertResult:
    """Convert one .eml file into markdown according to the v4 contract."""
    source = _validate_source(path)
    opts = options or ConvertOptions()
    if opts.dry_run and opts.delete_eml:
        opts = replace(opts, delete_eml=False)
    target: Path | None = None

    try:
        result, markdown_document = _run_pipeline(source, opts)

        target = _resolve_output_target(source, result.subject, output)
        target = _collision_safe_target(target)

        if not opts.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(markdown_document, encoding="utf-8")
            if opts.delete_eml:
                source.unlink()
            result.output = target

        return result
    except (OSError, ValueError, UnicodeDecodeError, RuntimeError) as exc:
        if target is not None and target.exists():
            try:
                target.unlink()
            except OSError:
                pass
        error_code, plain_text_fallback_available, html_repair_available = _convert_error_metadata(exc)
        return ConvertResult(
            source=source,
            output=None,
            subject="",
            sender="unknown",
            date=None,
            attachments=[],
            success=False,
            error=str(exc),
            error_code=error_code,
            plain_text_fallback_available=plain_text_fallback_available,
            html_repair_available=html_repair_available,
            dry_run=opts.dry_run,
        )


def convert_to_bundle(
    path: str | Path,
    *,
    bundle_root: str | Path,
    options: ConvertOptions | None = None,
    source_handling: Literal["move", "copy", "delete"] = "move",
) -> BundleResult:
    result, _diagnostics = convert_to_bundle_with_diagnostics(
        path,
        bundle_root=bundle_root,
        options=options,
        source_handling=source_handling,
    )
    return result


def convert_to_bundle_with_diagnostics(
    path: str | Path,
    *,
    bundle_root: str | Path,
    options: ConvertOptions | None = None,
    source_handling: Literal["move", "copy", "delete"] = "move",
) -> tuple[BundleResult, dict[str, Any] | None]:
    """Convert one .eml file into a self-contained bundle directory."""
    source = _validate_source(path)
    opts = options or ConvertOptions()
    if opts.dry_run and opts.delete_eml:
        opts = replace(opts, delete_eml=False)
    if source_handling not in {"move", "copy", "delete"}:
        raise ValueError(f"unsupported source_handling: {source_handling}")

    bundle_dir: Path | None = None
    markdown_target: Path | None = None
    source_artifact: Path | None = None
    attachment_paths: list[Path] = []
    diagnostics: dict[str, Any] | None = None

    try:
        result, parsed, rendered, diagnostics = _build_rendered_markdown(source, opts)

        root = Path(bundle_root).expanduser()
        bundle_dir = _collision_safe_bundle_dir(root / _bundle_slug(source))
        markdown_target = bundle_dir / "message.md"

        if not opts.dry_run:
            bundle_dir.mkdir(parents=True, exist_ok=False)
            attachment_paths = _write_attachment_parts(parsed.attachment_parts, bundle_dir / "attachments")
            if attachment_paths:
                rendered.front_matter["attachment_files"] = [
                    path.relative_to(bundle_dir).as_posix() for path in attachment_paths
                ]

            markdown_target.write_text(serialize_markdown(rendered), encoding="utf-8")

            if source_handling == "copy":
                source_artifact = bundle_dir / source.name
                shutil.copy2(source, source_artifact)
            elif source_handling == "move":
                source_artifact = bundle_dir / source.name
                shutil.move(str(source), str(source_artifact))
            else:
                source.unlink()
                source_artifact = None

        return (
            BundleResult(
                source=source,
                bundle=bundle_dir,
                markdown=markdown_target,
                source_artifact=source_artifact,
                attachments=attachment_paths,
                success=result.success,
                error=result.error,
                error_code=result.error_code,
                plain_text_fallback_available=result.plain_text_fallback_available,
                html_repair_available=result.html_repair_available,
                dry_run=opts.dry_run,
            ),
            diagnostics,
        )
    except (OSError, ValueError, UnicodeDecodeError, RuntimeError) as exc:
        if bundle_dir is not None and bundle_dir.exists():
            shutil.rmtree(bundle_dir, ignore_errors=True)
        error_code, plain_text_fallback_available, html_repair_available = _convert_error_metadata(exc)
        return (
            BundleResult(
                source=source,
                bundle=None,
                markdown=None,
                source_artifact=None,
                attachments=[],
                success=False,
                error=str(exc),
                error_code=error_code,
                plain_text_fallback_available=plain_text_fallback_available,
                html_repair_available=html_repair_available,
                dry_run=opts.dry_run,
            ),
            diagnostics,
        )


def convert_dir(
    directory: str | Path,
    *,
    output: str | Path | None = None,
    options: ConvertOptions | None = None,
) -> list[ConvertResult]:
    """Convert all .eml files in a directory tree with per-file result reporting."""
    source_dir = Path(directory).expanduser().resolve()
    if not source_dir.exists():
        raise FileNotFoundError(str(source_dir))
    if not source_dir.is_dir():
        raise ValueError(f"Expected a directory: {source_dir}")

    output_root = Path(output).expanduser() if output is not None else None
    opts = options or ConvertOptions()

    results: list[ConvertResult] = []
    files = _iter_source_eml_files(source_dir)

    for file_path in files:
        file_output: str | Path | None
        if output_root is None:
            file_output = None
        else:
            relative_parent = file_path.relative_to(source_dir).parent
            file_output = output_root / relative_parent

        result = convert(
            file_path,
            output=file_output,
            options=opts,
        )
        results.append(result)

    return results
