"""Exercise tap preparation with real local Git and fake brew/pip/remote writes."""
from __future__ import annotations

from copy import deepcopy
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
SDIST = {"url": BASE_URL + f"dead_letter-{VERSION}.tar.gz", "digests": {"sha256": "b" * 64}}
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
        with patch.object(h, "run", side_effect=self.fake_run), patch.object(h.platform, "system", return_value="Darwin"), patch.object(h.platform, "machine", return_value="arm64"):
            return h.prepare_tap(self.tap, self.output, self.recipe, client=self.client)

    def test_default_plan_has_no_execution_and_no_publish(self):
        with patch.object(h, "run", side_effect=AssertionError("must not run")):
            recipe = h.plan(VERSION, SDIST)
        self.assertIn("--write-only", recipe["commands"]["bump"])
        self.assertIn("--python-package-name=dead-letter", recipe["commands"]["bump"])
        self.assertIn("--package-name=dead-letter", recipe["commands"]["fallback"])
        self.assertNotIn("--commit", recipe["commands"]["bump"])
        self.assertNotIn("--python-extra-packages", json.dumps(recipe))

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
        with patch.object(h.platform, "system", return_value="Linux"):
            with self.assertRaises(Unavailable):
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

    def test_resource_versions_are_exact_for_sdist_and_wheels(self):
        for filename, expected in (("python_dateutil-2.9.0.post0.tar.gz", "2.9.0.post0"),
                                   ("python_dateutil-2.9.0.post0-py2.py3-none-any.whl", "2.9.0.post0")):
            self.assertEqual(h.resource_version({"name": "python-dateutil", "url": BASE_URL + filename}), expected)
        with self.assertRaises(Unavailable):
            h.resource_version({"name": "six", "url": BASE_URL + "arbitrary.tar.gz"})


if __name__ == "__main__":
    unittest.main()
