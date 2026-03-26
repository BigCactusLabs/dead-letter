"""Pydantic API contracts for dead-letter backend."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

JobStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "completed_with_errors",
    "failed",
    "cancelled",
]

ErrorStage = Literal["validation", "backend", "core"]
DiagnosticState = Literal["normal", "degraded", "review_recommended"]
SelectedBody = Literal["html", "plain"]
SegmentationPath = Literal["html", "plain_fallback"]
ClientHint = Literal["gmail", "outlook", "generic"] | None
ConfidenceLevel = Literal["high", "medium", "low"]
WarningSeverity = Literal["warning"]


class ErrorItem(BaseModel):
    path: str | None = None
    code: str
    message: str
    stage: ErrorStage


class Progress(BaseModel):
    total: int = Field(default=0, ge=0)
    completed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    current: str | None = None


class Summary(BaseModel):
    written: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    errors: int = Field(default=0, ge=0)


class DiagnosticWarning(BaseModel):
    code: str
    message: str
    severity: WarningSeverity = "warning"


class StrippedImageInfo(BaseModel):
    category: Literal["signature_image", "tracking_pixel"]
    reason: str
    reference: str


class QualityDiagnostics(BaseModel):
    state: DiagnosticState
    selected_body: SelectedBody
    segmentation_path: SegmentationPath
    client_hint: ClientHint = None
    confidence: ConfidenceLevel
    fallback_used: Literal[
        "plain_text_reply_parser",
        "html_failure_plain_text_fallback",
        "html_markdown_panic_repaired",
    ] | None = None
    warnings: list[DiagnosticWarning] = Field(default_factory=list)
    stripped_images: list[StrippedImageInfo] = Field(default_factory=list)


class JobOptions(BaseModel):
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

    model_config = ConfigDict(extra="ignore")


class JobCreateRequest(BaseModel):
    mode: Literal["file", "directory"]
    input_path: str
    options: JobOptions = Field(default_factory=JobOptions)

    model_config = ConfigDict(extra="forbid")


class OutputLocation(BaseModel):
    strategy: Literal["cabinet"]
    cabinet_path: str
    bundle_path: str | None = None


class JobCreateResponse(BaseModel):
    id: str
    status: JobStatus
    output_location: OutputLocation


class JobRetryRequest(BaseModel):
    action: Literal["retry_with_html_fallback", "retry_with_html_repair"]

    model_config = ConfigDict(extra="forbid")


class JobStatusResponse(BaseModel):
    id: str
    status: JobStatus
    origin: str = "manual"
    output_location: OutputLocation
    cancel_requested: bool
    progress: Progress
    summary: Summary
    errors: list[ErrorItem]
    recovery_actions: list[dict[str, str]] = Field(default_factory=list)
    diagnostics: QualityDiagnostics | None = None
    report_path: str | None = None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class AggregateTotals(BaseModel):
    jobs_completed: int = Field(default=0, ge=0)
    total_written: int = Field(default=0, ge=0)
    total_skipped: int = Field(default=0, ge=0)
    total_errors: int = Field(default=0, ge=0)


class JobHistoryResponse(BaseModel):
    jobs: list[JobStatusResponse]
    totals: AggregateTotals


class ImportStartResponse(BaseModel):
    imported_path: str
    id: str
    status: JobStatus
    output_location: OutputLocation


class BatchImportStartResponse(BaseModel):
    imported_paths: list[str]
    id: str
    status: JobStatus
    output_location: OutputLocation


class JobCancelResponse(BaseModel):
    id: str
    status: JobStatus
    accepted: bool


class FsEntryResponse(BaseModel):
    name: str
    path: str
    input_path: str
    type: Literal["file", "directory"]
    size: int = Field(ge=0)
    modified: str


class FsListResponse(BaseModel):
    path: str
    entries: list[FsEntryResponse]


class WatchStartRequest(BaseModel):
    path: str
    options: JobOptions = Field(default_factory=JobOptions)

    model_config = ConfigDict(extra="forbid")


class WatchStatusResponse(BaseModel):
    active: bool
    path: str | None = None
    files_detected: int = Field(default=0, ge=0)
    jobs_created: int = Field(default=0, ge=0)
    failed_events: int = Field(default=0, ge=0)
    last_error: ErrorItem | None = None
    latest_job_id: str | None = None
    latest_job_status: JobStatus | None = None


class SettingsResponse(BaseModel):
    configured: bool
    inbox_path: str | None = None
    cabinet_path: str | None = None


class SettingsUpdateRequest(BaseModel):
    inbox_path: str
    cabinet_path: str

    model_config = ConfigDict(extra="forbid")


class OpenFolderResponse(BaseModel):
    path: str
