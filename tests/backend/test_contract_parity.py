from __future__ import annotations

from dataclasses import fields
from pathlib import Path
import re
from typing import get_args

from dead_letter.backend.schemas import JobOptions, JobStatus
from dead_letter.core.types import ConvertOptions


def test_backend_option_fields_match_core_convert_options() -> None:
    backend_options = set(JobOptions.model_fields.keys())
    core_options = {field.name for field in fields(ConvertOptions)}

    assert backend_options == core_options


def test_job_status_enum_matches_contract() -> None:
    assert get_args(JobStatus) == (
        "queued",
        "running",
        "succeeded",
        "completed_with_errors",
        "failed",
        "cancelled",
    )


def test_docs_do_not_reference_removed_contract_fields() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    doc_files = [
        repo_root / "README.md",
        repo_root / "docs/reference/v4-runtime-contracts.md",
        repo_root / "docs/reference/frontend-state-model.md",
        repo_root / "src/dead_letter/frontend/style-guide.html",
    ]
    legacy_patterns = [
        re.compile(r"\bstrip_decorative\b"),
        re.compile(r"\bembed_mode\b"),
        re.compile(r"\boutput_path\b"),
        re.compile(r"/api/ingest\b"),
        re.compile(r"\bmanaged_root\b"),
        re.compile(r"\badjacent_to_source\b"),
        re.compile(r"\bdead-letter-exports\b"),
        re.compile(r"\bdeleteStagedAfterSuccess\b"),
        re.compile(r"`me`"),
        re.compile(r'"me"\s*:'),
        re.compile(r"--me\b"),
    ]

    offenders: list[str] = []
    for doc_file in doc_files:
        content = doc_file.read_text(encoding="utf-8")
        for pattern in legacy_patterns:
            for match in pattern.finditer(content):
                line_no = content.count("\n", 0, match.start()) + 1
                offenders.append(f"{doc_file.relative_to(repo_root)}:{line_no}: {pattern.pattern}")

    assert offenders == []


def test_canonical_docs_do_not_reference_pre_restructure_source_paths() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    doc_files = [
        repo_root / "README.md",
        repo_root / "docs/reference/v4-runtime-contracts.md",
        repo_root / "docs/reference/frontend-state-model.md",
        repo_root / "docs/reference/quality-diagnostics.md",
        repo_root / "docs/brand/style-guide.md",
    ]
    stale_patterns = [
        re.compile(r"\bsrc/frontend\b"),
        re.compile(r"\bsrc/backend\b"),
        re.compile(r"\bsrc/core\b"),
    ]

    offenders: list[str] = []
    for doc_file in doc_files:
        content = doc_file.read_text(encoding="utf-8")
        for pattern in stale_patterns:
            for match in pattern.finditer(content):
                line_no = content.count("\n", 0, match.start()) + 1
                offenders.append(f"{doc_file.relative_to(repo_root)}:{line_no}: {pattern.pattern}")

    assert offenders == []
