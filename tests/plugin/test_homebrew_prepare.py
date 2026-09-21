"""Exercise tap preparation with real local Git and fake brew/pip/remote writes."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import homebrew_prepare as h
from release_evidence import Conflict, Unavailable, formula

VERSION = "1.2.3"
BODY = b"synthetic wheel; never imported or installed"
WHEEL_HASH = hashlib.sha256(BODY).hexdigest()
WHEEL = "six-1.17.0-py3-none-any.whl"
BASE_URL = "https://files.pythonhosted.org/packages/fixture/"
NOW = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)
SDIST = {"url": BASE_URL + f"dead_letter-{VERSION}.tar.gz", "digests": {"sha256": "b" * 64},
         "upload_time_iso_8601": (NOW - timedelta(days=2)).isoformat()}
TEXT = f'''class DeadLetter < Formula
  include Language::Python::Virtualenv
  url "{BASE_URL}dead_letter-1.2.2.tar.gz"
  sha256 "{'a' * 64}"
  depends_on arch: :arm64
  depends_on "python@3.14"
  resource "six" do
    url "{BASE_URL}{WHEEL}"
    sha256 "{WHEEL_HASH}"
  end
  def install
    resources.each do |r|
      r.stage do
        venv.pip_install Dir["*.whl"].first || Pathname.pwd
      end
    end
    rm bin/"dead-letter-mcp"
    rm bin/"dead-letter-ui"
  end
end
'''


class PublicMetadata:
    def __init__(self):
        self.document = {"info": {"name": "six", "version": "1.17.0"}, "urls": [
            {"filename": WHEEL, "url": BASE_URL + WHEEL, "digests": {"sha256": WHEEL_HASH}, "yanked": False}]}

    def json(self, url):
        if url != "https://pypi.org/pypi/six/1.17.0/json":
            raise AssertionError("unexpected public request: " + url)
        return deepcopy(self.document)


class HomebrewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.tap = self.root / "tap"
        self.tap.mkdir()
        self.output = self.root / "review"
        self.path = self.tap / h.FORMULA
        self.path.parent.mkdir()
        self.path.write_text(TEXT, encoding="utf-8")
        for args in (("init", "-b", "prepare/dead-letter-1.2.3"),
                     ("config", "user.name", "Synthetic Release Test"),
                     ("config", "user.email", "release@example.test"),
                     ("config", "core.autocrlf", "false"),
                     ("config", "commit.gpgsign", "false"),
                     ("remote", "add", "origin", h.REMOTE),
                     ("add", "."), ("commit", "-m", "synthetic baseline")):
            subprocess.run(["git", *args], cwd=self.tap, check=True, capture_output=True)
        self.recipe = h.plan(VERSION, SDIST)
        self.client = PublicMetadata()
        self.calls = []
        self.real_run = h.run
        self.fallback = False
        self.fail_style = False
        self.extra_file = False
        self.change_install = False

    def fake_run(self, command, *, cwd, public=True):
        self.calls.append((command, public))
        if command[:2] == ["brew", "--repo"]:
            return str(self.tap)
        if command[:2] == ["brew", "--prefix"]:
            return str(self.root / "python-prefix")
        if command[:2] == ["brew", "bump-formula-pr"]:
            text = TEXT.replace("dead_letter-1.2.2.tar.gz", f"dead_letter-{VERSION}.tar.gz").replace('  sha256 "' + "a" * 64, '  sha256 "' + "b" * 64)
            if not self.fallback:
                text = text.replace(WHEEL, "six-1.17.0.tar.gz")
            if self.change_install:
                text = text.replace("venv.pip_install", "unsafe_install")
            self.path.write_text(text)
            if self.extra_file:
                (self.tap / "unexpected.txt").write_text("do not delete")
            return ""
        if command[:2] == ["brew", "update-python-resources"]:
            self.path.write_text(self.path.read_text().replace(WHEEL, "six-1.17.0.tar.gz"))
            return ""
        if "-c" in command and Path(command[0]).name == "python3.14":
            return json.dumps([[3, 14], "arm64"])
        if "download" in command and Path(command[0]).name == "python3.14":
            self.assertIn("--no-deps", command)
            self.assertIn("--only-binary=:all:", command)
            self.assertIn("--isolated", command)
            self.assertIn("six==1.17.0", command)
            (Path(command[command.index("--dest") + 1]) / WHEEL).write_bytes(BODY)
            return ""
        if command[:2] == ["brew", "style"]:
            if self.fail_style:
                raise Unavailable("style failed")
            return ""
        if command[0] == "git" and "push" in command:
            self.assertFalse(public)
            self.assertIn("credential.https://github.com.helper=!gh auth git-credential", command)
            self.assertNotIn("--force", command)
            return ""
        if command[:3] == ["gh", "pr", "create"]:
            self.assertFalse(public)
            self.assertIn("--draft", command)
            self.assertIn(h.TAP, command)
            return "https://github.com/BigCactusLabs/homebrew-tap/pull/999\n"
        if command[0] == "git":
            return self.real_run(command, cwd=cwd, public=public)
        raise AssertionError("unexpected command: " + repr(command))

    def prepare(self):
        with patch.object(h, "_utc_now", return_value=NOW), patch.object(h, "run", side_effect=self.fake_run), patch.object(h.platform, "system", return_value="Darwin"), patch.object(h.platform, "machine", return_value="arm64"):
            return h.prepare_tap(self.tap, self.output, self.recipe, client=self.client)

    def test_default_plan_has_no_execution_and_no_publish(self):
        with patch.object(h, "run", side_effect=AssertionError("must not run")):
            recipe = h.plan(VERSION, SDIST)
        self.assertIn("--write-only", recipe["commands"]["bump"])
        self.assertIn("--python-package-name=dead-letter", recipe["commands"]["bump"])
        self.assertIn("--package-name=dead-letter", recipe["commands"]["fallback"])
        self.assertNotIn("--commit", recipe["commands"]["bump"])
        self.assertNotIn("--python-extra-packages", json.dumps(recipe))
        self.assertEqual(recipe["sdist_upload_time_utc"], "2026-09-19T16:00:00Z")
        self.assertEqual(recipe["homebrew_earliest_prepare_utc"], "2026-09-20T16:00:00Z")

    def test_fresh_plan_works_but_prepare_refuses_before_subprocess_or_write(self):
        fresh = deepcopy(SDIST)
        fresh["upload_time_iso_8601"] = (NOW - timedelta(hours=23, minutes=59)).isoformat()
        recipe = h.plan(VERSION, fresh)
        self.assertEqual(recipe["homebrew_earliest_prepare_utc"], "2026-09-21T16:01:00Z")
        with patch.object(h, "_utc_now", return_value=NOW), patch.object(h, "run", side_effect=AssertionError("must not run")):
            with self.assertRaisesRegex(Unavailable, rf"{VERSION}.*24-hour.*2026-09-21T16:01:00Z"):
                h.prepare_tap(self.tap, self.output, recipe, client=self.client)
        self.assertEqual(self.path.read_text(), TEXT)
        self.assertFalse(self.output.exists())

    def test_exact_boundary_and_older_uploads_can_prepare(self):
        for age in (timedelta(hours=24), timedelta(days=3)):
            with self.subTest(age=age):
                recipe = h.plan(VERSION, {**SDIST, "upload_time_iso_8601": (NOW - age).isoformat()})
                with patch.object(h, "_utc_now", return_value=NOW), patch.object(h, "tap_state", return_value="branch"), \
                        patch.object(h.platform, "system", return_value="Linux"), patch.object(h, "run", side_effect=AssertionError("must not run")):
                    with self.assertRaisesRegex(Unavailable, "native Apple-silicon"):
                        h.prepare_tap(self.tap, self.output, recipe, client=self.client)

    def test_offset_upload_time_is_normalized_to_utc(self):
        recipe = h.plan(VERSION, {**SDIST, "upload_time_iso_8601": "2026-09-20T13:00:00-04:00"})
        self.assertEqual(recipe["sdist_upload_time_utc"], "2026-09-20T17:00:00Z")
        self.assertEqual(recipe["homebrew_earliest_prepare_utc"], "2026-09-21T17:00:00Z")

    def test_bad_upload_times_fail_as_unavailable(self):
        for label, value, message in (("missing", None, "missing"), ("malformed", "not-a-time", "malformed"),
                                      ("naive", "2026-09-20T12:00:00", "timezone")):
            with self.subTest(label=label), self.assertRaisesRegex(Unavailable, message):
                sdist = deepcopy(SDIST)
                if value is None:
                    sdist.pop("upload_time_iso_8601")
                else:
                    sdist["upload_time_iso_8601"] = value
                h.plan(VERSION, sdist)

    def test_extreme_upload_times_fail_before_subprocess_or_write(self):
        for value in ("9999-12-31T00:00:00Z", "0001-01-01T00:00:00+01:00"):
            with self.subTest(value=value), patch.object(h, "run", side_effect=AssertionError("must not run")):
                with self.assertRaisesRegex(Unavailable, "out of range"):
                    h.plan(VERSION, {**SDIST, "upload_time_iso_8601": value})
                recipe = {**self.recipe, "sdist_upload_time_utc": value}
                with self.assertRaisesRegex(Unavailable, "out of range"):
                    h.prepare_tap(self.tap, self.output, recipe, client=self.client)
                self.assertEqual(self.path.read_text(), TEXT)
                self.assertFalse(self.output.exists())

    def test_prepare_rejects_missing_or_inconsistent_recipe_time_before_subprocess(self):
        for key in ("sdist_upload_time_utc", "homebrew_earliest_prepare_utc"):
            with self.subTest(key=key):
                recipe = deepcopy(self.recipe)
                recipe.pop(key)
                with patch.object(h, "run", side_effect=AssertionError("must not run")):
                    with self.assertRaises(Unavailable):
                        h.prepare_tap(self.tap, self.output, recipe, client=self.client)
        self.assertEqual(self.path.read_text(), TEXT)
        self.assertFalse(self.output.exists())

    def test_public_brew_failure_reports_only_sanitized_final_stderr_line(self):
        completed = subprocess.CompletedProcess([], 7, "ignored stdout", "first line\n\x1b[31mfinal problem\x1b[0m\n")
        with patch.object(h.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(Unavailable, r"brew style failed \(exit 7\): final problem$"):
                h.run(["brew", "style"], cwd=self.tap)

    def test_brew_failure_ignores_trailing_control_only_lines(self):
        completed = subprocess.CompletedProcess([], 7, "", "first line\nfinal problem\n\x1b[0m\n\x00\x07\n")
        with patch.object(h.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(Unavailable, r"brew style failed \(exit 7\): final problem$"):
                h.run(["brew", "style"], cwd=self.tap)

    def test_brew_failure_empty_stderr_uses_local_fallback(self):
        completed = subprocess.CompletedProcess([], 2, "secret stdout", "\n")
        with patch.object(h.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(Unavailable, r"brew style failed \(exit 2\); inspect locally$"):
                h.run(["brew", "style"], cwd=self.tap)

    def test_brew_failure_detail_is_bounded_control_free_and_redacted(self):
        secret = "known-secret-value"
        stderr = ("old\n\x00failure https://alice:password@example.test/x?token=visible "
                  "ghp_abcdefghijklmnopqrstuvwxyz xoxb-123456-secret api_key=also-visible "
                  "Authorization: Basic dXNlcjpwYXNz Bearer bearer-value " + secret + " " + "x" * 500)
        completed = subprocess.CompletedProcess([], 3, "", stderr)
        with patch.dict(h.os.environ, {"SERVICE_PASSWORD": secret}), patch.object(h.subprocess, "run", return_value=completed):
            with self.assertRaises(Unavailable) as caught:
                h.run(["brew", "style"], cwd=self.tap)
        message = str(caught.exception)
        detail = message.split(": ", 1)[1]
        self.assertLessEqual(len(detail), h.BREW_ERROR_DETAIL_LIMIT)
        self.assertFalse(any(ord(char) < 32 or ord(char) == 127 for char in detail))
        for leaked in (secret, "alice", "password", "visible", "ghp_abcdefghijklmnopqrstuvwxyz", "xoxb-123456-secret", "also-visible", "dXNlcjpwYXNz", "bearer-value"):
            self.assertNotIn(leaked, detail)

    def test_authenticated_and_non_brew_failures_hide_stderr(self):
        completed = subprocess.CompletedProcess([], 4, "", "credential must stay hidden")
        with patch.object(h.subprocess, "run", return_value=completed):
            for command, public in ((["brew", "style"], False), (["git", "status"], True)):
                with self.subTest(command=command, public=public), self.assertRaises(Unavailable) as caught:
                    h.run(command, cwd=self.tap, public=public)
                self.assertIn("inspect locally", str(caught.exception))
                self.assertNotIn("credential", str(caught.exception))

    def test_preparation_restores_wheel_layout_and_writes_review_packet_only(self):
        packet = self.prepare()
        data = formula(self.path.read_text())
        self.assertEqual(data["version"], VERSION)
        self.assertTrue(data["resources"][0]["url"].endswith(".whl"))
        self.assertEqual(h.skeleton(self.path.read_text()), h.skeleton(TEXT))
        self.assertEqual(packet["status"], "prepared-not-tested")
        self.assertEqual(packet["diff_sha256"], hashlib.sha256((self.output / "formula.patch").read_bytes()).hexdigest())
        self.assertFalse(any("push" in c or "commit" in c or c[:2] == ["gh", "pr"] for c, _ in self.calls))
        self.assertIn("- [ ]", (self.output / "pr-body.md").read_text())

    def test_resource_update_fallback_runs_when_bump_does_not_regenerate(self):
        self.fallback = True
        self.prepare()
        self.assertTrue(any(c[:2] == ["brew", "update-python-resources"] for c, _ in self.calls))

    def test_native_platform_required_before_brew_writes(self):
        with patch.object(h, "_utc_now", return_value=NOW), patch.object(h.platform, "system", return_value="Linux"):
            with self.assertRaisesRegex(Unavailable, "native Apple-silicon"):
                h.prepare_tap(self.tap, self.output, self.recipe, client=self.client)
        self.assertEqual(self.path.read_text(), TEXT)

    def test_dirty_tap_refused(self):
        (self.tap / "local-work.txt").write_text("unrelated")
        with self.assertRaisesRegex(ValueError, "no staged"):
            self.prepare()
        self.assertFalse(any(c[0] == "brew" for c, _ in self.calls))

    def test_main_branch_and_other_remote_refused(self):
        subprocess.run(["git", "branch", "-m", "main"], cwd=self.tap, check=True)
        with self.assertRaisesRegex(ValueError, "before editing"):
            h.tap_state(self.tap, VERSION, clean=True)
        subprocess.run(["git", "branch", "-m", "prepare/dead-letter-1.2.3"], cwd=self.tap, check=True)
        subprocess.run(["git", "remote", "set-url", "origin", "https://example.test/wrong"], cwd=self.tap, check=True)
        with self.assertRaisesRegex(ValueError, "origin"):
            h.tap_state(self.tap, VERSION, clean=True)

    def test_separate_push_url_to_other_repository_refused(self):
        subprocess.run(["git", "remote", "set-url", "--push", "origin", "https://example.test/wrong"], cwd=self.tap, check=True)
        with self.assertRaisesRegex(ValueError, "push"):
            h.tap_state(self.tap, VERSION, clean=True)

    def test_style_or_layout_failure_restores_only_formula(self):
        self.fail_style = True
        with self.assertRaises(Unavailable):
            self.prepare()
        self.assertEqual(self.path.read_text(), TEXT)
        self.fail_style = False
        self.change_install = True
        with self.assertRaisesRegex(Conflict, "outside"):
            self.prepare()
        self.assertEqual(self.path.read_text(), TEXT)

    def test_extra_file_is_not_deleted_or_silently_committed(self):
        self.extra_file = True
        with self.assertRaises(Conflict):
            self.prepare()
        self.assertEqual(self.path.read_text(), TEXT)
        self.assertTrue((self.tap / "unexpected.txt").exists())

    def test_same_version_and_review_directory_reuse_refused(self):
        self.path.write_text(TEXT.replace("dead_letter-1.2.2.tar.gz", f"dead_letter-{VERSION}.tar.gz"))
        subprocess.run(["git", "commit", "-am", "synthetic same version"], cwd=self.tap, check=True, capture_output=True)
        with self.assertRaisesRegex(ValueError, "newer"):
            self.prepare()

    def test_wheel_checksum_or_yank_failure_stops_before_output(self):
        self.client.document["urls"][0]["digests"]["sha256"] = "c" * 64
        with self.assertRaisesRegex(Conflict, "checksum"):
            self.prepare()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.path.read_text(), TEXT)

    def test_explicit_open_pr_only_after_exact_patch_review(self):
        packet = self.prepare()
        self.calls.clear()
        with patch.object(h, "run", side_effect=self.fake_run):
            with self.assertRaisesRegex(ValueError, "exact preparation"):
                h.open_pr(self.tap, self.output, self.recipe, "0" * 64)
            self.assertFalse(any("push" in c or "commit" in c for c, _ in self.calls))
            result = h.open_pr(self.tap, self.output, self.recipe, packet["diff_sha256"])
        self.assertEqual(result["status"], "draft-pr-opened")
        self.assertEqual(result["merge"], "manual")
        self.assertFalse(any(c[:3] == ["gh", "pr", "merge"] for c, _ in self.calls))

    def test_formula_edit_after_review_prevents_any_remote_write(self):
        packet = self.prepare()
        self.path.write_text(self.path.read_text() + "# later edit\n")
        self.calls.clear()
        with patch.object(h, "run", side_effect=self.fake_run):
            with self.assertRaisesRegex(Conflict, "changed after review"):
                h.open_pr(self.tap, self.output, self.recipe, packet["diff_sha256"])
        self.assertFalse(any("push" in c or "commit" in c for c, _ in self.calls))

    def test_formula_mode_change_after_review_prevents_any_remote_write(self):
        packet = self.prepare()
        self.path.chmod(self.path.stat().st_mode | 0o111)
        self.calls.clear()
        with patch.object(h, "run", side_effect=self.fake_run):
            with self.assertRaisesRegex(Conflict, "mode change"):
                h.open_pr(self.tap, self.output, self.recipe, packet["diff_sha256"])
        self.assertFalse(any("push" in c or "commit" in c or "add" in c for c, _ in self.calls))

    def test_resource_versions_are_exact_for_sdist_and_wheels(self):
        for filename, expected in (("python_dateutil-2.9.0.post0.tar.gz", "2.9.0.post0"),
                                   ("python_dateutil-2.9.0.post0-py2.py3-none-any.whl", "2.9.0.post0")):
            self.assertEqual(h.resource_version({"name": "python-dateutil", "url": BASE_URL + filename}), expected)
        with self.assertRaises(Unavailable):
            h.resource_version({"name": "six", "url": BASE_URL + "arbitrary.tar.gz"})


if __name__ == "__main__":
    unittest.main()
