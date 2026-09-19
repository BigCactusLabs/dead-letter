"""Release guard regressions; stdlib-only so they also run without app extras."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_tools", ROOT / "scripts/release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def sources() -> dict[str, str]:
    version = "0.3.1"
    return {
        "pyproject.toml": '[build-system]\nrequires = ["wheel"]\n\n[project]\nname = "dead-letter"\nversion = "0.3.1"\n\n[tool.example]\nvalue = "untouched"\n',
        "uv.lock": 'version = 1\n\n[[package]]\nname = "dead-letter"\nversion = "0.3.1"\nsource = { editable = "." }\n\n[[package]]\nname = "dependency"\nversion = "0.3.1"\nsource = { registry = "https://pypi.org/simple" }\n',
        "src/dead_letter/__init__.py": 'from unavailable_module import never_import_this\n__version__ = "0.3.1"\n',
        "server.json": release.encode({"name": release.SERVER, "version": version, "packages": [
            {"registryType": "pypi", "version": version, "runtimeArguments": [{"name": "--from", "value": f"dead-letter[mcp]=={version}"}]},
            {"registryType": "mcpb", "version": version, "identifier": f"{release.ASSET_ROOT}/v{version}/dead-letter-mcp-{version}.mcpb", "fileSha256": "0" * 64}]}),
        "mcpb/manifest.json": release.encode({"version": version, "tools": [{"name": "convert_eml"}]}),
        "mcpb/pyproject.toml": '[project]\nname = "dead-letter-mcpb"\nversion = "0.3.1"\ndependencies = ["dead-letter[mcp]==0.3.1"]\n\n[tool.uv]\npackage = false\n',
        ".well-known/ard.json": release.encode({"entries": [{"version": version, "type": "mcp"}, {"version": version, "type": "skill"}]}),
        "plugin/.claude-plugin/plugin.json": release.encode({"name": "dead-letter", "version": version}),
        "plugin/.mcp.json": release.encode({"mcpServers": {"dead-letter": {"command": "uvx", "args": ["--python", "3.12", "--from", f"dead-letter[mcp]=={version}", "dead-letter-mcp"]}}}),
    }


class ReleaseMetadataTests(unittest.TestCase):
    def test_check_is_offline_and_never_imports_package(self):
        with patch.object(release.urllib.request, "urlopen", side_effect=AssertionError("network")):
            self.assertEqual(release.check(sources()), {"package": "0.3.1", "plugin": "0.3.1", "plugin_pin": "0.3.1"})

    def test_every_package_sync_surface_rejects_drift(self):
        for name in release.FILES[:7]:
            with self.subTest(name=name):
                data = sources()
                data[name] = data[name].replace("0.3.1", "0.3.2")
                with self.assertRaises(ValueError):
                    release.check(data)

    def test_plugin_versions_are_independent(self):
        data = sources()
        data["plugin/.claude-plugin/plugin.json"] = data["plugin/.claude-plugin/plugin.json"].replace("0.3.1", "0.4.0")
        data["plugin/.mcp.json"] = data["plugin/.mcp.json"].replace("0.3.1", "0.3.0")
        self.assertEqual(release.check(data)["plugin_pin"], "0.3.0")
        release.check_tag(release.check(data), "plugin-v0.4.0", plugin=True)

    def test_plugin_pin_cannot_be_floating_or_a_range(self):
        for pin in ("dead-letter[mcp]", "dead-letter[mcp]>=0.3.1", "dead-letter[mcp]==0.3.*", "dead-letter[mcp]==0.3.1;evil"):
            with self.subTest(pin=pin):
                data = sources()
                data["plugin/.mcp.json"] = data["plugin/.mcp.json"].replace("dead-letter[mcp]==0.3.1", pin)
                with self.assertRaises(ValueError):
                    release.check(data)

    def test_tag_namespaces_and_prereleases_cannot_cross_publish(self):
        values = release.check(sources())
        for tag in ("plugin-v0.3.1", "v0.3.2", "v0.3.1rc1", "v0.3.1+local", "v00.3.1", "v0.3.1\n", "main"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.check_tag(values, tag)
        with self.assertRaises(ValueError):
            release.check_tag(values, "v0.3.1", plugin=True)
        release.check_tag(values, "v0.3.1")

    def test_url_drift_and_publish_time_artifacts_rejected_in_source(self):
        for mutate in (
            lambda server: server["packages"][1].update(identifier="https://example.invalid/bundle"),
            lambda server: server["packages"][1].update(fileSha256="a" * 64),
            lambda server: server["packages"].append({"registryType": "oci"}),
            lambda server: server["packages"].append(server["packages"][0]),
        ):
            data = sources()
            server = json.loads(data["server.json"])
            mutate(server)
            data["server.json"] = release.encode(server)
            with self.assertRaises(ValueError):
                release.check(data)

    def test_prepare_synchronizes_fields_without_changing_inputs(self):
        before = sources()
        unchanged = dict(before)
        after = release.prepare(before, "0.4.0")
        self.assertEqual(before, unchanged)
        self.assertEqual(release.check(after), {"package": "0.4.0", "plugin": "0.4.0", "plugin_pin": "0.4.0"})
        self.assertIn('name = "dependency"\nversion = "0.3.1"', after["uv.lock"])
        self.assertIn('[tool.example]\nvalue = "untouched"', after["pyproject.toml"])
        self.assertEqual(json.loads(after["mcpb/manifest.json"])["tools"], [{"name": "convert_eml"}])

    def test_prepare_can_defer_plugin_adoption(self):
        before = sources()
        after = release.prepare(before, "0.4.0", keep_plugin=True)
        self.assertEqual(after["plugin/.mcp.json"], before["plugin/.mcp.json"])
        self.assertEqual(release.check(after)["plugin"], "0.3.1")

    def test_prepare_respects_plugin_ahead_of_package(self):
        before = sources()
        before["plugin/.claude-plugin/plugin.json"] = before["plugin/.claude-plugin/plugin.json"].replace("0.3.1", "1.0.0")
        with self.assertRaises(ValueError):
            release.prepare(before, "0.3.2")
        after = release.prepare(before, "0.3.2", plugin_version="1.0.1")
        self.assertEqual(release.check(after)["plugin"], "1.0.1")
        self.assertEqual(release.check(after)["plugin_pin"], "0.3.2")

    def test_no_version_reuse_or_rollback(self):
        for version in ("0.3.1", "0.3.0", "0.2.9"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                release.prepare(sources(), version)

    def test_patch_is_git_apply_compatible_and_complete(self):
        before = sources()
        after = release.prepare(before, "0.4.0")
        diff = release.patch(before, after)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, content in before.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(content, encoding="utf-8")
            subprocess.run(["git", "init", "-q", directory], check=True, capture_output=True)
            subprocess.run(["git", "apply", "--check", "-"], input=diff, text=True, cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "apply", "-"], input=diff, text=True, cwd=root, check=True, capture_output=True)
            self.assertEqual(release.read_sources(root), after)

    def test_tag_check_requires_dated_changelog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, content in sources().items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(content, encoding="utf-8")
            (root / "CHANGELOG.md").write_text("## [Unreleased]\n", encoding="utf-8")
            with patch("sys.stderr"), patch("sys.stdout"):
                self.assertEqual(release.main(["--root", directory, "check", "--tag", "v0.3.1"]), 1)
                (root / "CHANGELOG.md").write_text("## [0.3.1] - 2026-09-19\n", encoding="utf-8")
                self.assertEqual(release.main(["--root", directory, "check", "--tag", "v0.3.1"]), 0)


class PyPIReadinessTests(unittest.TestCase):
    def test_json_only_is_not_installable(self):
        with patch.object(release, "fetch_json", side_effect=[{"info": {"version": "0.3.1"}, "urls": [{"yanked": False}]}, {"versions": ["0.3.0"]}]):
            self.assertFalse(release.pypi_ready("0.3.1"))

    def test_both_indexes_and_unyanked_distribution_required(self):
        for yanked, expected in ((False, True), (True, False)):
            with self.subTest(yanked=yanked), patch.object(release, "fetch_json", side_effect=[{"info": {"version": "0.3.1"}, "urls": [{"yanked": yanked}]}, {"versions": ["0.3.1"]}]):
                self.assertEqual(release.pypi_ready("0.3.1"), expected)

    def test_wait_is_bounded_and_network_failure_is_not_success(self):
        with patch.object(release, "pypi_ready", side_effect=OSError("offline")) as probe, patch.object(release.time, "sleep") as sleep:
            with self.assertRaises(ValueError):
                release.wait_pypi("0.3.1", attempts=3, interval=0)
            self.assertEqual(probe.call_count, 3)
            self.assertEqual(sleep.call_count, 2)

    def test_eventual_readiness_stops_retries(self):
        with patch.object(release, "pypi_ready", side_effect=[False, True]) as probe, patch.object(release.time, "sleep"), patch("sys.stdout"):
            release.wait_pypi("0.3.1", attempts=3, interval=0)
            self.assertEqual(probe.call_count, 2)


class ReleaseAssetTests(unittest.TestCase):
    def exercise(self, existing: bytes | None, local: bytes = b"artifact"):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "dead-letter-server-0.3.1.json"
        path.write_bytes(local)
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if command[2] == "view":
                assets = [{"name": path.name}] if existing is not None else []
                return subprocess.CompletedProcess(command, 0, json.dumps({"assets": assets}))
            if command[2] == "download":
                (Path(command[command.index("--dir") + 1]) / path.name).write_bytes(existing)
            return subprocess.CompletedProcess(command, 0)

        return path, calls, run

    def test_new_asset_is_uploaded_without_clobber(self):
        path, calls, run = self.exercise(None)
        with patch.object(release.subprocess, "run", side_effect=run):
            release.upload_assets("v0.3.1", [path])
        self.assertEqual([c[2] for c in calls], ["view", "upload"])
        self.assertNotIn("--clobber", calls[-1])

    def test_identical_asset_is_not_uploaded_again(self):
        path, calls, run = self.exercise(b"artifact")
        with patch.object(release.subprocess, "run", side_effect=run):
            release.upload_assets("v0.3.1", [path])
        self.assertEqual([c[2] for c in calls], ["view", "download"])

    def test_different_asset_fails_before_any_write(self):
        path, calls, run = self.exercise(b"published bytes")
        with patch.object(release.subprocess, "run", side_effect=run), self.assertRaises(ValueError):
            release.upload_assets("v0.3.1", [path])
        self.assertNotIn("upload", [c[2] for c in calls])

    def test_asset_read_failure_is_not_treated_as_absence(self):
        path, calls, run = self.exercise(None)
        with patch.object(release.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "gh")), self.assertRaises(subprocess.CalledProcessError):
            release.upload_assets("v0.3.1", [path])

    def test_plugin_tag_cannot_upload_package_assets(self):
        path, calls, run = self.exercise(None)
        with patch.object(release.subprocess, "run", side_effect=run), self.assertRaises(ValueError):
            release.upload_assets("plugin-v0.3.1", [path])
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
