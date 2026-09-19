"""Offline contracts for the OCI release path; real Docker checks run in CI."""

from __future__ import annotations

import copy
import csv
import importlib.util
import io
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


metadata = load_script("container_metadata")
smoke = load_script("smoke_container")
DIGEST = "sha256:" + "a" * 64
COMMIT = "b" * 40


@pytest.fixture
def server():
    return {
        "name": metadata.SERVER_NAME,
        "version": "0.2.5",
        "packages": [
            {"registryType": "pypi", "identifier": "dead-letter", "version": "0.2.5"},
            {"registryType": "mcpb", "identifier": "https://example.invalid/bundle", "fileSha256": "c" * 64},
        ],
    }


@pytest.mark.parametrize("value", ["", "latest", "a" * 64, "sha256:" + "0" * 64, "sha256:" + "A" * 64, DIGEST + "\n"])
def test_rejects_invalid_or_placeholder_digest(value):
    with pytest.raises(ValueError):
        metadata.validate_digest(value)


@pytest.mark.parametrize("value", ["latest", "v0.2.5", "0.2", "0.2.5+local", "0.2.5\ninjected=yes"])
def test_rejects_unsupported_release_tags(value):
    with pytest.raises(ValueError):
        metadata.validate_version(value)


@pytest.mark.parametrize("value", ["0.2.5", "1.0.0rc1", "1.0.0.post1"])
def test_preserves_exact_supported_version(value):
    assert metadata.validate_version(value) == value


def test_release_ref_must_match_source_version(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.5"\n')
    assert metadata.release_version(tmp_path, "refs/tags/v0.2.5", release=True) == "0.2.5"
    assert metadata.release_version(tmp_path, "refs/pull/9/merge", release=False) == "0.2.5"
    for ref in ("refs/tags/v0.2.4", "refs/tags/plugin-v0.2.5", "refs/heads/main"):
        with pytest.raises(ValueError, match="release ref"):
            metadata.release_version(tmp_path, ref, release=True)


def test_resolve_uses_index_not_child_digest(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=f"Name: example:tag\nDigest: {DIGEST}\nManifests:\n  Digest: sha256:{'d' * 64}\n")

    monkeypatch.setattr(metadata.subprocess, "run", run)
    assert metadata.resolve_image("example:tag") == f"example:tag@{DIGEST}"
    assert calls[0][1]["check"] is True
    assert calls[0][1]["timeout"] == 90


def test_resolve_fails_closed_on_missing_digest(monkeypatch):
    monkeypatch.setattr(metadata.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="unknown"))
    with pytest.raises(ValueError, match="resolve"):
        metadata.resolve_image("example:tag")


def test_oci_is_digest_pinned_and_preserves_other_packages(server):
    original = copy.deepcopy(server)
    result = metadata.with_oci(server, "0.2.5", DIGEST)
    assert server == original
    assert result["packages"][:2] == original["packages"]
    assert metadata.with_oci(result, "0.2.5", DIGEST) == result
    oci = result["packages"][2]
    assert oci["identifier"] == f"ghcr.io/bigcactuslabs/dead-letter@{DIGEST}"
    assert oci["transport"] == {"type": "stdio"}
    arguments = oci["runtimeArguments"]
    assert {"type": "positional", "value": "-i"} in arguments
    assert {"type": "named", "name": "--network", "value": "none"} in arguments
    assert {"type": "positional", "value": "--read-only"} in arguments
    mounts = [a for a in arguments if a.get("name") == "--mount"]
    assert [m["value"] for m in mounts] == [
        "type=bind,source={input_directory},target=/input,readonly",
        "type=bind,source={output_directory},target=/output",
    ]
    assert all(next(iter(m["variables"].values()))["isRequired"] for m in mounts)
    user = next(a for a in arguments if a.get("name") == "--user")
    assert user["variables"]["container_user"]["default"] == "10001:10001"
    assert "latest" not in json.dumps(oci)


def test_oci_package_omits_the_fields_the_registry_rejects():
    oci = metadata.oci_package(DIGEST)
    # The 2025-12-11 schema requires only these three package fields, and the
    # registry's own OCI validator (internal/validators/registries/oci.go)
    # rejects an OCI package that carries any of the three below.
    assert {"registryType", "identifier", "transport"} <= oci.keys()
    assert not {"version", "registryBaseUrl", "fileSha256"} & oci.keys()


def test_server_and_image_identity_are_bound_across_release_inputs():
    name = json.loads((ROOT / "server.json").read_text())["name"]
    assert metadata.SERVER_NAME == smoke.SERVER_NAME == name
    # ValidateOCI compares this label value, case-sensitively, to server.json.
    label = re.search(
        r'io\.modelcontextprotocol\.server\.name="([^"]+)"',
        (ROOT / "Dockerfile").read_text(),
    )
    assert label is not None and label[1] == name
    workflow = yaml.safe_load((ROOT / ".github/workflows/container.yml").read_text())
    assert workflow["jobs"]["publish"]["env"]["IMAGE"] == metadata.IMAGE


@pytest.mark.parametrize("change", [{"name": "wrong"}, {"version": "0.2.4"}, {"packages": []}])
def test_registry_refuses_mismatched_metadata(server, change):
    server.update(change)
    with pytest.raises(ValueError):
        metadata.with_oci(server, "0.2.5", DIGEST)


def test_atomic_registry_write(tmp_path):
    path = tmp_path / "server.json"
    path.write_text("old")
    metadata.write_json(path, {"new": True})
    assert json.loads(path.read_text()) == {"new": True}
    assert list(tmp_path.iterdir()) == [path]


def test_failed_registry_write_preserves_original(tmp_path, monkeypatch):
    path = tmp_path / "server.json"
    path.write_text("old")

    def fail(*args):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(metadata.os, "replace", fail)
    with pytest.raises(OSError):
        metadata.write_json(path, {"new": True})
    assert path.read_text() == "old"
    assert list(tmp_path.iterdir()) == [path]


def test_catalog_is_generated_from_explicit_source_commit(tmp_path):
    metadata.render_catalog(ROOT, COMMIT, tmp_path)
    config = yaml.safe_load((tmp_path / "server.yaml").read_text())
    assert config["source"]["commit"] == COMMIT
    assert config["run"]["disableNetwork"] is True
    assert config["run"]["volumes"] == [
        "{{dead-letter.input_directory}}:/input:ro",
        "{{dead-letter.output_directory}}:/output:rw",
    ]
    assert config["run"]["user"] == "{{dead-letter.container_user}}"
    assert set(config["config"]["parameters"]["required"]) == {
        "input_directory", "output_directory", "container_user",
    }
    assert not (tmp_path / "tools.json").exists()  # Do not bypass live introspection.
    assert "PolyForm Noncommercial" in (tmp_path / "readme.md").read_text()


@pytest.mark.parametrize("commit", ["main", "v0.2.5", "0" * 40, "b" * 7, COMMIT + "\n"])
def test_catalog_rejects_mutable_or_placeholder_source(tmp_path, commit):
    with pytest.raises(ValueError):
        metadata.render_catalog(ROOT, commit, tmp_path)
    assert not list(tmp_path.iterdir())


def test_image_build_contract():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "uv sync --locked --no-dev --extra mcp --no-editable" in dockerfile
    assert "--build-constraint build-constraints.txt" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert 'ENTRYPOINT ["dead-letter-mcp"]' in dockerfile
    assert "COPY . " not in dockerfile
    assert "EXPOSE " not in dockerfile
    assert "RUN pip " not in dockerfile
    assert "--no-install-project --no-build" in dockerfile
    assert "uv build --wheel --out-dir /wheels --build-constraint" in dockerfile
    assert "uv pip install --python /opt/venv/bin/python --no-deps --no-index" in dockerfile
    assert "io.modelcontextprotocol.server.name" in dockerfile
    assert "PolyForm-Noncommercial-1.0.0" in dockerfile
    ignore = [line for line in (ROOT / ".dockerignore").read_text().splitlines()
              if line and not line.startswith("#")]
    assert ignore[0] == "**"
    assert "!uv.lock" in ignore and "**/*.[eE][mM][lL]" in ignore and "**/.env*" in ignore


def test_workflow_separates_untrusted_testing_from_publication():
    workflow = yaml.safe_load((ROOT / ".github/workflows/container.yml").read_text())
    # PyYAML's YAML 1.1 resolver treats `on` as True; GitHub uses YAML 1.2.
    triggers = workflow.get("on", workflow.get(True))
    assert "pull_request" in triggers and "pull_request_target" not in triggers
    assert "workflow_dispatch" not in triggers
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    assert {row["platform"] for row in jobs["test"]["strategy"]["matrix"]["include"]} == {"linux/amd64", "linux/arm64"}
    publish = jobs["publish"]
    assert publish["permissions"]["packages"] == "write"
    assert "github.event_name == 'release'" in publish["if"]
    assert publish["needs"] == ["prepare", "test"]
    steps = publish["steps"]
    names = [s.get("name", "") for s in steps]
    assert names.index("Verify published digest and PyPI tool-schema parity on both platforms") < names.index("Promote without overwriting an existing release tag")
    assert names.index("Require anonymous access before advertising the image") < names.index("Promote without overwriting an existing release tag")
    text = (ROOT / ".github/workflows/container.yml").read_text()
    assert "--provenance=mode=max --sbom=true" in text
    assert "$IMAGE:latest" not in text
    assert "--password-stdin" in text
    for job in jobs.values():
        for step in job.get("steps", []):
            if "uses" in step:
                assert len(step["uses"].rsplit("@", 1)[1]) == 40


def response(result, request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def client_for(messages, timeout=0.5):
    raw = "".join(json.dumps(message) + "\n" for message in messages)
    process = SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO(raw))
    return smoke.StdioClient(process, timeout)


def test_stdio_roundtrip_and_notifications():
    client = client_for([{"jsonrpc": "2.0", "method": "notifications/progress"}, response({"ok": True})])
    assert client.request("ping") == {"ok": True}
    assert json.loads(client.process.stdin.getvalue())["method"] == "ping"


@pytest.mark.parametrize("message", [[], {"result": {}}, response({}, 9), response(None), {"jsonrpc": "2.0", "id": 1, "error": {"code": -32603}}])
def test_stdio_rejects_invalid_responses(message):
    with pytest.raises(smoke.SmokeFailure):
        client_for([message]).request("ping")


def test_stdio_rejects_log_pollution_and_eof():
    process = SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO("log on stdout\n"))
    with pytest.raises(smoke.SmokeFailure, match="non-JSON"):
        smoke.StdioClient(process).request("ping")
    with pytest.raises(smoke.SmokeFailure, match="closed stdout"):
        client_for([]).request("ping")


def test_notifications_cannot_extend_deadline(monkeypatch):
    client = client_for([{"jsonrpc": "2.0", "method": "notifications/progress"}] * 5, timeout=1)
    clock = iter([0, 0.1, 0.7, 1.1])
    monkeypatch.setattr(smoke.time, "monotonic", lambda: next(clock))
    with pytest.raises(smoke.SmokeFailure, match="deadline"):
        client.request("ping")


def tool(name):
    return {"name": name, "description": name, "inputSchema": {"type": "object"}}


def test_handshake_and_paginated_tool_inventory():
    tools = [tool(name) for name in sorted(smoke.EXPECTED_TOOLS)]
    client = client_for([
        response({"protocolVersion": "2025-06-18", "serverInfo": {"name": "dead-letter"}}),
        response({"tools": tools[:2], "nextCursor": "next"}, 2),
        response({"tools": tools[2:]}, 3),
    ])
    assert client.initialize() == {t["name"]: t for t in tools}
    sent = [json.loads(line) for line in client.process.stdin.getvalue().splitlines()]
    assert sent[1]["method"] == "notifications/initialized"
    assert sent[-1]["params"] == {"cursor": "next"}


@pytest.mark.parametrize("tools", [[], [tool("unknown")], [tool("convert_eml")] * 2, ["not an object"], [{"name": "convert_eml"}]])
def test_invalid_tool_inventory_fails(tools):
    client = client_for([
        response({"protocolVersion": "2025-06-18", "serverInfo": {}}),
        response({"tools": tools}, 2),
    ])
    with pytest.raises(smoke.SmokeFailure):
        client.initialize()


def test_unexpected_tool_error_fails():
    with pytest.raises(smoke.SmokeFailure, match="isError"):
        client_for([response({"isError": True})]).call("convert_eml", {})
    assert client_for([response({"isError": True})]).call("convert_eml", {}, error=True)["isError"]


def test_mount_csv_handles_spaces_and_commas(tmp_path):
    source = tmp_path / "mail, with spaces"
    value = smoke.mount(source, "/input", readonly=True)
    assert next(csv.reader([value])) == ["type=bind", f"source={source}", "target=/input", "readonly"]


def test_docker_command_limits_privileges_and_maps_host_ownership(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke.os, "getuid", lambda: 1001, raising=False)
    monkeypatch.setattr(smoke.os, "getgid", lambda: 1002, raising=False)
    command = smoke.docker_command("test-image", "linux/amd64", "test", tmp_path / "in", tmp_path / "out")
    assert command[command.index("--user") + 1] == "1001:1002"
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command and "--privileged" not in command and "-t" not in command
    assert command[-1] == "test-image"
    assert "readonly" in command[command.index("--mount") + 1]


def test_root_smoke_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke.os, "getuid", lambda: 0, raising=False)
    monkeypatch.setattr(smoke.os, "getgid", lambda: 0, raising=False)
    with pytest.raises(smoke.SmokeFailure, match="unprivileged"):
        smoke.docker_command("image", "linux/amd64", "name", tmp_path, tmp_path)


@pytest.mark.parametrize("value", ["relative.md", "/input/message.md", "/output/../escape", "/output/missing.md"])
def test_unpersisted_or_escaped_output_fails(tmp_path, value):
    with pytest.raises(smoke.SmokeFailure):
        smoke.persisted_path(value, tmp_path)


def test_real_stdio_subprocess_is_reaped():
    program = 'import sys,json\nfor line in sys.stdin:\n r=json.loads(line); print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{}}),flush=True)\n'
    with smoke.session([sys.executable, "-u", "-c", program], 5) as client:
        assert client.request("ping") == {}
        process = client.process
    assert process.poll() == 0


def test_output_symlink_escape_fails(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    private = tmp_path / "private.md"
    private.write_text("not an output")
    (output / "escape.md").symlink_to(private)
    with pytest.raises(smoke.SmokeFailure, match="symlink escapes"):
        smoke.persisted_path("/output/escape.md", output)


def test_release_registry_waits_for_live_container_and_retains_existing_stamp():
    jobs = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())["jobs"]
    assert jobs["build-container"]["needs"] == "publish"
    assert jobs["build-container"]["uses"] == "./.github/workflows/container.yml"
    assert jobs["build-container"]["with"]["publish"] is True
    assert "build-container" in jobs["publish-mcp"]["needs"]
    steps = jobs["publish-mcp"]["steps"]
    names = [step["name"] for step in steps]
    stamp = names.index("Stamp server.json with the release version and bundle hash")
    oci = names.index("Add the verified OCI package to the release metadata")
    assert stamp < oci < names.index("Publish to the MCP Registry")
    assert steps[oci]["env"]["DIGEST"] == "${{ needs.build-container.outputs.digest }}"


def test_pinned_base_image_is_not_double_suffixed(monkeypatch):
    monkeypatch.setattr(metadata.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=f"Digest: {DIGEST}\n"))
    assert metadata.resolve_image(f"example:tag@{DIGEST}") == f"example:tag@{DIGEST}"
