"""Confirm the CI workflow runs the plugin test suite."""

from pathlib import Path

CI_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def test_ci_includes_plugin_tests_step():
    text = CI_PATH.read_text(encoding="utf-8")
    assert "tests/plugin" in text, (
        "ci.yml must include a step that runs `pytest tests/plugin`."
    )
