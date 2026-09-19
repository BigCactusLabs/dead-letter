"""Offline contracts for generated client setup; desktop UX is not simulated."""

from __future__ import annotations

import base64
import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("generate_client_installs", ROOT / "scripts/generate_client_installs.py")
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


@pytest.fixture
def server():
    return json.loads((ROOT / "server.json").read_text(encoding="utf-8"))


def test_public_launcher_is_published_package_not_staged_pin(server):
    original = copy.deepcopy(server)
    assert generator.public_launcher(server) == {
        "command": "uvx",
        "args": ["--python", "3.12", "--from", "dead-letter[mcp]", "dead-letter-mcp"],
    }
    assert server == original


def test_future_release_preparation_does_not_advance_public_links(server):
    before = generator.public_launcher(server)
    server["version"] = "99.0.0"
    pypi = next(p for p in server["packages"] if p["registryType"] == "pypi")
    pypi["version"] = "99.0.0"
    pypi["runtimeArguments"][1]["value"] = "dead-letter[mcp]==99.0.0"
    assert generator.public_launcher(server) == before


def test_pypi_selection_is_not_positional(server):
    before = generator.public_launcher(server)
    server["packages"].reverse()
    server["packages"].insert(0, {"registryType": "oci", "identifier": "example.invalid/image"})
    assert generator.public_launcher(server) == before


@pytest.mark.parametrize("field,value", [
    ("runtimeHint", "sh"), ("identifier", "other-package"),
    ("transport", {"type": "streamable-http"}),
    ("registryBaseUrl", "https://example.invalid"), ("version", "0.0.1"),
])
def test_invalid_launch_contract_requires_review(server, field, value):
    next(p for p in server["packages"] if p["registryType"] == "pypi")[field] = value
    with pytest.raises(ValueError):
        generator.public_launcher(server)


@pytest.mark.parametrize("count", [0, 2])
def test_missing_or_ambiguous_pypi_package_fails(server, count):
    pypi = next(p for p in server["packages"] if p["registryType"] == "pypi")
    server["packages"] = [copy.deepcopy(pypi) for _ in range(count)]
    with pytest.raises(ValueError):
        generator.public_launcher(server)


@pytest.mark.parametrize("change", ["pin", "python", "extra-argument", "entrypoint", "variables", "identity"])
def test_runtime_contract_drift_fails_closed(server, change):
    pypi = next(p for p in server["packages"] if p["registryType"] == "pypi")
    if change == "pin":
        pypi["runtimeArguments"][1]["value"] = "dead-letter[mcp]>=0.2.0"
    elif change == "python":
        pypi["runtimeArguments"][0]["value"] = "3.11"
    elif change == "extra-argument":
        pypi["runtimeArguments"].append({"type": "named", "name": "--index", "value": "unreviewed"})
    elif change == "entrypoint":
        pypi["packageArguments"][0]["value"] = "dead-letter-ui"
    elif change == "variables":
        pypi["runtimeArguments"][0]["variables"] = {"python": {}}
    else:
        server["name"] = "io.github.someone-else/dead-letter"
    with pytest.raises(ValueError):
        generator.public_launcher(server)


def test_vscode_https_redirect_round_trips_full_payload(server):
    launcher = generator.public_launcher(server)
    outer = urlsplit(generator.install_links(launcher)["vscode"])
    assert outer.scheme == "https" and outer.netloc == "vscode.dev"
    assert outer.path == "/redirect"
    deep = parse_qs(outer.query)["url"][0]
    assert deep.startswith("vscode:mcp/install?")
    assert json.loads(unquote(urlsplit(deep).query)) == {"name": "dead-letter", **launcher}


def test_cursor_payload_is_base64_json_not_encoded_json_or_wrapper(server):
    launcher = generator.public_launcher(server)
    link = urlsplit(generator.install_links(launcher)["cursor"])
    assert link.scheme == "https" and link.netloc == "cursor.com"
    assert link.path == "/en/install-mcp"
    query = parse_qs(link.query)
    assert query["name"] == ["dead-letter"]
    payload = base64.b64decode(query["config"][0], validate=True).decode("utf-8")
    assert json.loads(payload) == {"type": "stdio", **launcher}
    assert not payload.startswith("%7B")
    assert "mcpServers" not in json.loads(payload)


def test_link_encoding_handles_reserved_characters_and_unicode():
    launcher = {"command": "uvx", "args": ["a+b&c=?# café 📨"]}
    links = generator.install_links(launcher)
    query = parse_qs(urlsplit(links["cursor"]).query)
    assert json.loads(base64.b64decode(query["config"][0]))["args"] == launcher["args"]
    deep = parse_qs(urlsplit(links["vscode"]).query)["url"][0]
    assert json.loads(unquote(urlsplit(deep).query))["args"] == launcher["args"]


def test_readme_update_is_idempotent_and_preserves_surrounding_content(server):
    text = f"before\n{generator.BEGIN}\nold\n{generator.END}\nafter\n"
    links = generator.install_links(generator.public_launcher(server))
    updated = generator.replace_link_block(text, links)
    assert updated.startswith("before\n") and updated.endswith("\nafter\n")
    assert generator.replace_link_block(updated, links) == updated


@pytest.mark.parametrize("text", ["no markers", generator.BEGIN, generator.END + generator.BEGIN, generator.BEGIN * 2 + generator.END])
def test_broken_readme_markers_fail_without_replacing_document(text):
    with pytest.raises(ValueError):
        generator.replace_link_block(text, {"vscode": "a", "cursor": "b"})


def test_all_generated_files_are_current():
    for name, expected in generator.artifacts(ROOT).items():
        assert (ROOT / name).read_text(encoding="utf-8") == expected, name


def test_clients_use_the_same_command_and_preserve_approval_defaults(server):
    expected = generator.public_launcher(server)
    for filename, wrapper in (("vscode", "servers"), ("cursor", "mcpServers"), ("cline", "mcpServers")):
        config = json.loads((ROOT / f"examples/mcp/{filename}.json").read_text())
        assert set(config) == {wrapper}
        entry = config[wrapper]["dead-letter"]
        assert entry["command"] == expected["command"] and entry["args"] == expected["args"]
        assert "env" not in entry and "url" not in entry
    cline = json.loads((ROOT / "examples/mcp/cline.json").read_text())["mcpServers"]["dead-letter"]
    assert cline["autoApprove"] == [] and cline["disabled"] is False


def test_cline_submission_matches_current_metadata_and_declares_license(server):
    entry = json.loads((ROOT / "docs/project/submissions/cline/entry.json").read_text())
    launcher = generator.public_launcher(server)
    assert entry["install"]["args"] == ["dead-letter", "--", launcher["command"], *launcher["args"]]
    assert entry["repo"] == server["repository"]["url"]
    assert entry["tagline"] == server["description"] and len(entry["tagline"]) <= 120
    assert entry["license"] == "PolyForm-Noncommercial-1.0.0"
    assert entry["verified"] is False and entry["featured"] is False
    assert entry["tags"] == ["productivity", "data", "memory"]
    assert "hosted email-processing service" in entry["description"]


def test_check_is_read_only_and_reports_stale_output(tmp_path, monkeypatch, capsys):
    (tmp_path / "example.json").write_text("unchanged")
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    monkeypatch.setattr(generator, "artifacts", lambda: {"example.json": "new"})
    monkeypatch.setattr(sys, "argv", ["generate_client_installs.py", "--check"])
    with pytest.raises(SystemExit) as failure:
        generator.main()
    assert failure.value.code == 1
    assert "Stale generated files" in capsys.readouterr().err
    assert (tmp_path / "example.json").read_text() == "unchanged"


def test_check_command_executes_successfully():
    subprocess.run([sys.executable, str(ROOT / "scripts/generate_client_installs.py"), "--check"], check=True, timeout=10)
