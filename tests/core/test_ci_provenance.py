"""Provenance regression tests use tiny synthetic Git repositories only."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/ci_provenance.py"
SPEC = importlib.util.spec_from_file_location("ci_provenance", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "src/dead_letter/core/mbox.py"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("# synthetic source\n", encoding="utf-8")
        ci.git(self.root, "init")
        ci.git(self.root, "config", "user.name", "Provenance test")
        ci.git(self.root, "config", "user.email", "provenance@example.test")
        ci.git(self.root, "config", "core.autocrlf", "false")
        ci.git(self.root, "add", ".")
        ci.git(self.root, "-c", "commit.gpgsign=false", "commit", "-m", "fixture")
        self.head = ci.git(self.root, "rev-parse", "HEAD").strip()
        self.origin = patch.object(ci, "import_origin", return_value=str(self.source))
        self.origin.start()
        self.addCleanup(self.origin.stop)

    def read(self, **env):
        return ci.snapshot(self.root, env)

    def test_clean_checkout_and_bounded_hash(self):
        report = self.read(GITHUB_SHA=self.head)
        self.assertEqual(report["errors"], [])
        item = report["files"][0]
        self.assertEqual(item["path"], "src/dead_letter/core/mbox.py")
        self.assertEqual(item["sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(len(item["index_blob"]), 40)
        self.assertFalse(report["traceback_test"]["tracked"])
        self.assertFalse(report["traceback_test"]["exists"])

    def test_wrong_checkout_fails(self):
        self.assertIn("Checked-out HEAD differs from GITHUB_SHA", self.read(GITHUB_SHA="0" * 40)["errors"])

    def test_dirty_source_fails(self):
        self.source.write_text("# changed\n", encoding="utf-8")
        report = self.read()
        self.assertTrue(report["checkout"]["tracked_dirty"])
        self.assertIn("Tracked checkout files are modified", report["errors"])

    def test_untracked_output_does_not_fail(self):
        (self.root / "output.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.read()["errors"], [])

    def test_missing_tracked_source_fails(self):
        self.source.unlink()
        self.assertEqual(self.read()["files"][0]["status"], "missing")
        self.assertTrue(self.read()["errors"])

    def test_wrong_installed_module_fails(self):
        with patch.object(ci, "import_origin", return_value=str(self.root / "site-packages/mbox.py")):
            self.assertIn("dead_letter.core.mbox does not resolve to this checkout", self.read()["errors"])

    def test_untracked_parser_fails(self):
        ci.git(self.root, "rm", "--cached", "src/dead_letter/core/mbox.py")
        self.assertIn("Parser source is not tracked by this checkout", self.read()["errors"])

    def test_missing_module_fails(self):
        with patch.object(ci, "import_origin", return_value=None):
            self.assertTrue(self.read()["errors"])

    def test_pr_head_is_not_confused_with_synthetic_merge(self):
        event = self.root / "event.json"
        event.write_text(json.dumps({"pull_request": {
            "number": 118, "head": {"sha": "a" * 40}, "base": {"sha": "b" * 40},
            "body": "PRIVATE_SENTINEL", "title": "PRIVATE_SENTINEL",
        }}), encoding="utf-8")
        report = self.read(GITHUB_SHA=self.head, GITHUB_EVENT_PATH=str(event), GITHUB_TOKEN="SECRET_SENTINEL")
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["pull_request"]["head_sha"], "a" * 40)
        serialized = json.dumps(report)
        self.assertNotIn("PRIVATE_SENTINEL", serialized)
        self.assertNotIn("SECRET_SENTINEL", serialized)
        self.assertNotIn("GITHUB_EVENT_PATH", report["ci"])

    def test_symlink_mode_never_reads_target(self):
        with patch.object(Path, "open", side_effect=AssertionError("must not read")):
            item = ci.fingerprint(self.root, "secret.py", "120000", "a" * 40)
        self.assertEqual(item["status"], "not_regular")

    def test_source_contents_not_printed(self):
        self.source.write_text("PRIVATE_SOURCE_SENTINEL", encoding="utf-8")
        self.assertNotIn("PRIVATE_SOURCE_SENTINEL", json.dumps(self.read()))

    def test_repo_subdirectory_refused(self):
        with self.assertRaisesRegex(ValueError, "checkout root"):
            ci.snapshot(self.source.parent, {})

    def test_cli_failure_does_not_print_command_stderr(self):
        with patch.object(ci, "snapshot", side_effect=subprocess.CalledProcessError(1, "git", stderr="SECRET")):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(ci.main(["--root", str(self.root)]), 1)
        report = json.loads(output.getvalue())
        self.assertTrue(report["errors"])
        self.assertNotIn("SECRET", output.getvalue())

    def test_cli_with_real_git_and_resolver(self):
        for directory in (self.source.parent.parent, self.source.parent):
            (directory / "__init__.py").write_text(
                "raise AssertionError('initializer executed')\n", encoding="utf-8",
            )
        env = {**os.environ, "PYTHONPATH": str(self.root / "src"),
               "GITHUB_SHA": self.head, "GITHUB_TOKEN": "SECRET_SENTINEL"}
        env.pop("GITHUB_EVENT_PATH", None)
        for sha, expected_status in ((self.head, 0), ("0" * 40, 1)):
            env["GITHUB_SHA"] = sha
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(self.root)],
                env=env, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, expected_status, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(bool(report["errors"]), bool(expected_status))
            self.assertNotIn("SECRET_SENTINEL", result.stdout)

    def test_import_resolution_does_not_execute_initializers(self):
        # Exercise the real resolver against a synthetic package, not production imports.
        self.origin.stop()
        package = self.source.parent.parent
        for directory in (package, self.source.parent):
            (directory / "__init__.py").write_text("raise AssertionError('initializer executed')\n", encoding="utf-8")
        with patch.dict(sys.modules):
            for name in list(sys.modules):
                if name == "dead_letter" or name.startswith("dead_letter."):
                    del sys.modules[name]
            with patch.object(sys, "path", [str(self.root / "src"), *sys.path]):
                self.assertEqual(Path(ci.import_origin()).resolve(), self.source)


if __name__ == "__main__":
    unittest.main()
