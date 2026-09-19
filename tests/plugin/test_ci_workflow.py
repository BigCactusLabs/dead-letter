"""Confirm CI delegates plugin checks to the same commands used locally."""
from pathlib import Path
import re
import sys
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
CI_PATH = ROOT / ".github/workflows/ci.yml"
with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
    import verify


def plugin_commands():
    jobs = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))["jobs"]
    assert "needs" not in jobs["plugin"], "plugin CI must run independently"
    step = next(step for step in jobs["plugin"]["steps"] if step.get("run") == "python scripts/verify.py full --suite plugin")
    assert step.get("env", {}).get("GH_TOKEN") == "${{ github.token }}"
    return dict(verify.source_commands("full", ["plugin"]))


def test_ci_includes_plugin_tests_step():
    assert plugin_commands()["plugin-tests"] == ["uv", "run", "--locked", "--no-sync", "pytest", "-q", "tests/plugin"]


def test_ci_includes_agent_skill_validation_step():
    assert plugin_commands()["agent-skills"] == ["gh", "skill", "publish", "--dry-run"]


def test_ci_pins_claude_code_plugin_validator():
    commands = plugin_commands()
    assert commands["plugin-schema"] == ["npx", "--yes", "@anthropic-ai/claude-code@2.1.145", "plugin", "validate", "plugin/"]
    text = CI_PATH.read_text(encoding="utf-8") + "\n" + "\n".join(" ".join(command) for command in commands.values())
    unpinned_global_install = re.compile(r"npm\s+(?:install|i)\s+-g\s+@anthropic-ai/claude-code(?:\s|$)")
    assert not unpinned_global_install.search(text)
