from __future__ import annotations

from pathlib import Path


def _doc_files(repo_root: Path) -> list[Path]:
    files = [repo_root / "README.md"]
    files.extend(
        path
        for path in (repo_root / "docs").rglob("*.md")
        if not path.is_relative_to(repo_root / "docs" / "internal")
    )
    return sorted(files)


def test_docs_link_check_workflow_configuration() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (repo_root / ".github" / "workflows" / "docs-link-check.yml").read_text(encoding="utf-8")

    assert "failIfEmpty: false" in workflow
    assert "--exclude-path 'docs/internal/**'" in workflow

    # Doc files should be discoverable by the link checker
    doc_files = _doc_files(repo_root)
    assert len(doc_files) > 0
