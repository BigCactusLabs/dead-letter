"""The public-doc inventory must cover new distribution guides, not fixtures."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml


def test_docs_link_check_workflow_configuration(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load(
        (repo_root / ".github/workflows/docs-link-check.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["link-check"]["steps"]
    inventory = next(step["run"] for step in steps if step.get("name") == "Inventory maintained Markdown")
    checker = next(step for step in steps if step.get("name") == "Check markdown links")
    assert checker["with"]["failIfEmpty"] is True
    assert "--files-from" in checker["with"]["args"]
    assert "--exclude-path 'docs/internal/**'" not in checker["with"]["args"]

    # Exercise the actual inventory command rather than duplicating its glob.
    # Use a fixture checkout so documentation additions don't need a new test.
    included = [
        "README.md", "AGENTS.md", "CONTRIBUTING.md", "llms-install.md",
        "docs/reference/distribution.md", "docs/project/audit.md",
        "plugin/README.md", "plugin/skills/context/SKILL.md",
        "skills/dead-letter/SKILL.md", "mcpb/README.md", "docker/readme.md",
        ".github/PULL_REQUEST_TEMPLATE.md", "benchmarks/README.md",
    ]
    excluded = ["tests/core/fixtures/message.md", "benchmarks/fixtures/output.md"]
    for name in included + excluded:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Fixture\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    (tmp_path / "untracked.md").write_text("not part of the repository", encoding="utf-8")
    env = {**os.environ, "RUNNER_TEMP": str(tmp_path)}
    subprocess.run(["bash", "-e", "-c", inventory], cwd=tmp_path, env=env, check=True, capture_output=True)
    discovered = (tmp_path / "docs-files.txt").read_text(encoding="utf-8").splitlines()
    assert set(discovered) == set(included)
