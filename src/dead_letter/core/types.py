"""Shared contracts for dead-letter core conversion pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class ZoneKind(StrEnum):
    """Semantic zone categories used by the thread/zoning stage."""

    BODY = "body"
    QUOTED = "quoted"
    FORWARD_HEADER = "forward_header"
    FORWARDED_BODY = "forwarded_body"
    SIGNATURE_CANDIDATE = "signature_candidate"
    DISCLAIMER_CANDIDATE = "disclaimer_candidate"
    SIGNATURE = "signature"
    DISCLAIMER = "disclaimer"
    DECORATIVE = "decorative"


class StrippedImageCategory(StrEnum):
    """Category of image stripped during pre-sanitization filtering."""

    SIGNATURE_IMAGE = "signature_image"
    TRACKING_PIXEL = "tracking_pixel"


@dataclass(slots=True)
class StrippedImage:
    """Record of an image stripped during pre-sanitization filtering."""

    category: StrippedImageCategory
    reason: str
    reference: str


@dataclass(slots=True)
class Zone:
    """A segment of message content after zoning analysis."""

    kind: ZoneKind
    content: str
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Normalize to a detached mapping to avoid accidental external mutation.
        self.metadata = dict(self.metadata)


@dataclass(slots=True)
class ConvertOptions:
    """Shared conversion options used by convert/convert_dir and pipeline stages."""

    strip_signatures: bool = False
    strip_disclaimers: bool = False
    strip_quoted_headers: bool = False
    strip_signature_images: bool = False
    strip_tracking_pixels: bool = False
    embed_inline_images: bool = False
    include_all_headers: bool = False
    include_raw_html: bool = False
    no_calendar_summary: bool = False
    allow_fallback_on_html_error: bool = False
    allow_html_repair_on_panic: bool = False
    delete_eml: bool = False
    dry_run: bool = False
    report: bool = False


@dataclass(slots=True)
class AttachmentPart:
    """A decoded MIME attachment or inline asset available for bundle output."""

    filename: str
    content_type: str
    payload: bytes
    content_id: str | None = None
    disposition: str = "attachment"

    def __post_init__(self) -> None:
        self.payload = bytes(self.payload)
        self.content_id = self.content_id or None


@dataclass(slots=True)
class BodyCandidate:
    """An internal candidate body extracted from the MIME tree."""

    kind: str
    content: str
    source_part_id: str
    is_primary: bool = False
    related_inline_assets: list[str] = field(default_factory=list)
    quality_hints: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.related_inline_assets = list(self.related_inline_assets)
        self.quality_hints = dict(self.quality_hints)


@dataclass(slots=True)
class PartDefect:
    """A defect detected while parsing or selecting MIME content."""

    part_id: str
    code: str
    message: str
    severity: str


@dataclass(slots=True)
class ParsedEmail:
    """Result of MIME parsing before content normalization."""

    source: Path
    subject: str
    sender: str
    date: str | None
    text_body: str
    html_body: str | None
    headers: dict[str, str]
    attachments: list[str]
    attachment_parts: list[AttachmentPart] = field(default_factory=list)
    inline_cid_to_filename: dict[str, str] = field(default_factory=dict)
    inline_cid_to_data_uri: dict[str, str] = field(default_factory=dict)
    calendar_parts: list[str] = field(default_factory=list)
    body_candidates: list[BodyCandidate] = field(default_factory=list)
    selected_body_kind: str | None = None
    defects: list[PartDefect] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.source = self.source.resolve()
        self.headers = dict(self.headers)
        self.attachments = list(self.attachments)
        self.attachment_parts = list(self.attachment_parts)
        self.inline_cid_to_filename = dict(self.inline_cid_to_filename)
        self.inline_cid_to_data_uri = dict(self.inline_cid_to_data_uri)
        self.calendar_parts = list(self.calendar_parts)
        self.body_candidates = list(self.body_candidates)
        self.defects = list(self.defects)


@dataclass(slots=True)
class NormalizedContent:
    """Normalized textual representation consumed by thread/zoning logic."""

    plain_text: str
    markdown_from_html: str | None
    raw_html: str | None


@dataclass(slots=True)
class ThreadedContent:
    """Output of thread splitting/zoning stage."""

    zones: list[Zone]

    def __post_init__(self) -> None:
        self.zones = list(self.zones)


@dataclass(slots=True)
class MimeModel:
    """Internal normalized MIME model used for body selection and diagnostics."""

    parts: list[str]
    defects: list[PartDefect]
    body_candidates: list[BodyCandidate]
    selected_body_id: str | None = None

    def __post_init__(self) -> None:
        self.parts = list(self.parts)
        self.defects = list(self.defects)
        self.body_candidates = list(self.body_candidates)


@dataclass(slots=True)
class ConversationZone:
    """Internal typed zone with source and confidence metadata."""

    kind: ZoneKind
    content: str
    source_kind: str
    client_hint: str | None = None
    confidence: float = 1.0
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.metadata = dict(self.metadata)


@dataclass(slots=True)
class ConversationTrace:
    """Internal trace of body selection and degradation decisions."""

    selected_body_kind: str
    rules_triggered: list[str] = field(default_factory=list)
    fallback_used: str | None = None
    defects: list[PartDefect] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.rules_triggered = list(self.rules_triggered)
        self.defects = list(self.defects)
        self.notes = list(self.notes)


@dataclass(slots=True)
class RenderedMarkdown:
    """Final markdown output before writing to disk."""

    front_matter: dict[str, Any]
    body: str

    def __post_init__(self) -> None:
        self.front_matter = dict(self.front_matter)


@dataclass(slots=True)
class ConvertResult:
    """Public conversion result contract returned by convert/convert_dir."""

    source: Path
    output: Path | None
    subject: str
    sender: str
    date: str | None
    attachments: list[str]
    success: bool
    error: str | None
    dry_run: bool
    error_code: str | None = None
    plain_text_fallback_available: bool | None = None
    html_repair_available: bool | None = None

    def __post_init__(self) -> None:
        self.source = self.source.resolve()
        self.output = None if self.output is None else self.output.resolve()
        self.attachments = list(self.attachments)


@dataclass(slots=True)
class BundleResult:
    """Result contract for bundle-oriented conversion output."""

    source: Path
    bundle: Path | None
    markdown: Path | None
    source_artifact: Path | None
    attachments: list[Path]
    success: bool
    error: str | None
    dry_run: bool
    error_code: str | None = None
    plain_text_fallback_available: bool | None = None
    html_repair_available: bool | None = None

    def __post_init__(self) -> None:
        self.source = self.source.resolve()
        self.bundle = None if self.bundle is None else self.bundle.resolve()
        self.markdown = None if self.markdown is None else self.markdown.resolve()
        self.source_artifact = None if self.source_artifact is None else self.source_artifact.resolve()
        self.attachments = [Path(path).resolve() for path in self.attachments]
