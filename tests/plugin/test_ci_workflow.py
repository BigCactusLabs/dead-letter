"""Confirm the CI workflow runs the plugin test suite."""

from pathlib import Path
import re

CI_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def test_ci_includes_plugin_tests_step():
    text = CI_PATH.read_text(encoding="utf-8")
    assert "tests/plugin" in text, (
        "ci.yml must include a step that runs `pytest tests/plugin`."
    )


def test_ci_pins_claude_code_plugin_validator():
    text = CI_PATH.read_text(encoding="utf-8")

    assert "npx --yes @anthropic-ai/claude-code@2.1.145 plugin validate plugin/" in text
    unpinned_global_install = re.compile(
        r"npm\s+(?:install|i)\s+-g\s+@anthropic-ai/claude-code(?:\s|$)"
    )
    assert not unpinned_global_install.search(text)
