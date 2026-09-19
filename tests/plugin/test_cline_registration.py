"""Offline tests for the isolated CLI harness; actual Cline runs in CI."""
import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cline_registration", ROOT / "scripts/smoke_cline_registration.py")
cline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cline)
LAUNCHER = {"command": "uvx", "args": ["--python", "3.12", "--from", "dead-letter[mcp]", "dead-letter-mcp"]}
BEFORE = {"mcpServers": {"preserve-me": {"disabled": True, "autoApprove": []}}, "auditSentinel": "preserve"}


def saved():
    value = copy.deepcopy(BEFORE)
    value["mcpServers"]["dead-letter"] = {"transport": {"type": "stdio", **LAUNCHER}}
    return value


def test_uses_persisted_transport_and_preserves_existing_settings():
    assert cline.validate_registration(BEFORE, saved(), LAUNCHER) == ["uvx", *LAUNCHER["args"]]


@pytest.mark.parametrize("change", ["approval", "deleted_sentinel", "changed_sentinel", "extra_server", "flat_schema", "remote", "other_command", "new_setting"])
def test_configuration_drift_fails(change):
    after = saved()
    if change == "approval": after["mcpServers"]["dead-letter"]["autoApprove"] = ["convert_eml"]
    elif change == "deleted_sentinel": del after["mcpServers"]["preserve-me"]
    elif change == "changed_sentinel": after["auditSentinel"] = "lost"
    elif change == "extra_server": after["mcpServers"]["extra"] = {}
    elif change == "flat_schema": after["mcpServers"]["dead-letter"] = LAUNCHER
    elif change == "remote": after["mcpServers"]["dead-letter"]["transport"] = {"type": "http", "url": "https://example.invalid"}
    elif change == "other_command": after["mcpServers"]["dead-letter"]["transport"] = {"type": "stdio", "command": "sh", "args": []}
    else: after["new_setting"] = True
    with pytest.raises(cline.RegistrationFailure):
        cline.validate_registration(BEFORE, after, LAUNCHER)


def test_environment_has_no_inherited_credentials_profiles_or_execution_hooks(tmp_path):
    inherited = {"PATH": "/bin", "SYSTEMROOT": "C:\\Windows", "GITHUB_TOKEN": "secret", "OPENAI_API_KEY": "secret", "TYPESAFE_API_KEY": "secret", "NODE_OPTIONS": "--require /private/hook", "CLINE_DIR": "/private/cline", "NPM_TOKEN": "secret", "NPM_CONFIG_REGISTRY": "https://private.invalid", "HOME": "/private/home"}
    env = cline.isolated_environment(tmp_path, inherited)
    assert env["PATH"] == "/bin" and env["SYSTEMROOT"] == "C:\\Windows"
    assert not {"GITHUB_TOKEN", "OPENAI_API_KEY", "TYPESAFE_API_KEY", "NODE_OPTIONS", "NPM_TOKEN"}.intersection(env)
    assert "secret" not in str(env) and "/private" not in str(env)
    assert Path(env["CLINE_MCP_SETTINGS_PATH"]).parent == tmp_path
    assert Path(env["HOME"]).is_relative_to(tmp_path)
    assert env["NPM_CONFIG_REGISTRY"] == "https://registry.npmjs.org"
    assert Path(env["NPM_CONFIG_USERCONFIG"]).read_text() == ""


def test_runner_is_noninteractive_bounded_and_reports_errors(monkeypatch, tmp_path):
    def run(command, **kwargs):
        assert kwargs["stdin"] == cline.subprocess.DEVNULL
        assert kwargs["timeout"] == 180 and kwargs["cwd"] == tmp_path
        assert kwargs.get("shell", False) is False
        return SimpleNamespace(returncode=1, stderr="invalid options")
    monkeypatch.setattr(cline.subprocess, "run", run)
    with pytest.raises(cline.RegistrationFailure, match="invalid options"):
        cline.run(["npx", "cline", "mcp", "install"], tmp_path, {})


@pytest.mark.parametrize("protocol_fails", [False, True])
def test_orchestration_uses_saved_launcher_and_uninstalls_even_after_failure(tmp_path, monkeypatch, protocol_fails):
    import json
    import sys
    (tmp_path / "server.json").write_text("{}")
    candidate = tmp_path / "docs/project/submissions/cline/entry.json"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(json.dumps({"install": {"args": ["dead-letter", "--", "uvx", *LAUNCHER["args"]]}}))
    monkeypatch.setattr(cline, "ROOT", tmp_path)
    monkeypatch.setattr(cline.shutil, "which", lambda _: "/fake/npx")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    commands = []
    def run(command, directory, env):
        assert "OPENAI_API_KEY" not in env
        assert f"--package={cline.CLINE_PACKAGE}@{cline.CLINE_VERSION}" in command
        commands.append(command)
        settings = Path(env["CLINE_MCP_SETTINGS_PATH"])
        if command[-1] == "--version": return cline.CLINE_VERSION
        value = json.loads(settings.read_text())
        if "install" in command:
            assert command[-len(LAUNCHER['args']):] == LAUNCHER['args']
            assert "--yes" in command
            value["mcpServers"]["dead-letter"] = {"transport": {"type": "stdio", **LAUNCHER}}
        else:
            assert command[-3:] == ["mcp", "uninstall", "dead-letter"]
            del value["mcpServers"]["dead-letter"]
        settings.write_text(json.dumps(value))
        return "done"
    def protocol(*, command, environment):
        assert command == ["uvx", *LAUNCHER["args"]]
        assert "OPENAI_API_KEY" not in environment
        if protocol_fails: raise RuntimeError("synthetic protocol failure")
    monkeypatch.setitem(sys.modules, "generate_client_installs", SimpleNamespace(public_launcher=lambda _: LAUNCHER))
    monkeypatch.setitem(sys.modules, "smoke_client_install", SimpleNamespace(check=protocol))
    monkeypatch.setattr(cline, "run", run)
    if protocol_fails:
        with pytest.raises(RuntimeError, match="synthetic protocol"):
            cline.check()
    else:
        assert cline.check()["registration"] == "passed"
    assert len(commands) == 3
    assert commands[-1][-3:] == ["mcp", "uninstall", "dead-letter"]
