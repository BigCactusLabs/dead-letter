"""Offline regressions: synthetic archives/processes, no package install or network."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
    import package_artifacts as artifacts
    import smoke_package as smoke
    import verify

VERSION = "0.3.1"
README = (artifacts.MARKER + '\n<img src="https://example.invalid/logo.png" alt="dead-letter">\n'
          '[Guide](https://example.invalid/guide) [Section](#section)\n')


def payload(readme=README, version=VERSION, content_type="text/markdown"):
    return (f"Metadata-Version: 2.4\nName: dead-letter\nVersion: {version}\n"
            f"Description-Content-Type: {content_type}\n\n{readme}").encode("utf-8")


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.dist = self.root / "dist"
        self.dist.mkdir()
        self.wheel = self.dist / f"dead_letter-{VERSION}-py3-none-any.whl"
        self.sdist = self.dist / f"dead_letter-{VERSION}.tar.gz"
        self.manifest = self.root / "SHA256SUMS"
        self.write()

    def write(self, wheel=None, sdist=None):
        with zipfile.ZipFile(self.wheel, "w") as archive:
            archive.writestr(f"dead_letter-{VERSION}.dist-info/METADATA", payload() if wheel is None else wheel)
        raw = payload() if sdist is None else sdist
        with tarfile.open(self.sdist, "w:gz") as archive:
            member = tarfile.TarInfo(f"dead_letter-{VERSION}/PKG-INFO")
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))

    def test_wheel_and_sdist_readme_metadata(self):
        self.assertEqual(set(artifacts.validate(self.dist, VERSION)), {self.wheel, self.sdist})

    def test_unicode_readme_is_preserved(self):
        self.write(payload(README + "fidelity → Markdown café\n"), payload(README + "fidelity → Markdown café\n"))
        artifacts.validate(self.dist, VERSION)

    def test_wrong_metadata_version(self):
        self.write(wheel=payload(version="9.9.9"))
        with self.assertRaisesRegex(artifacts.ArtifactError, "name/version"):
            artifacts.validate(self.dist, VERSION)

    def test_filename_version_cannot_disagree(self):
        self.wheel.rename(self.dist / "dead_letter-9.9.9-py3-none-any.whl")
        with self.assertRaisesRegex(artifacts.ArtifactError, "filename"):
            artifacts.validate(self.dist, VERSION)

    def test_wrong_content_type(self):
        self.write(wheel=payload(content_type="text/plain"))
        with self.assertRaisesRegex(artifacts.ArtifactError, "content type"):
            artifacts.validate(self.dist, VERSION)

    def test_missing_content_type(self):
        self.write(wheel=payload().replace(b"Description-Content-Type: text/markdown\n", b""))
        with self.assertRaisesRegex(artifacts.ArtifactError, "exactly one"):
            artifacts.validate(self.dist, VERSION)

    def test_duplicate_metadata_headers(self):
        self.write(wheel=payload().replace(b"Name: dead-letter\n", b"Name: dead-letter\nName: dead-letter\n"))
        with self.assertRaisesRegex(artifacts.ArtifactError, "exactly one"):
            artifacts.validate(self.dist, VERSION)

    def test_marker_is_required_in_distribution_not_just_source(self):
        self.write(wheel=payload(README.replace(artifacts.MARKER, "")))
        with self.assertRaisesRegex(artifacts.ArtifactError, "ownership marker"):
            artifacts.validate(self.dist, VERSION)

    def test_logo_must_be_absolute_https(self):
        for url in ("docs/logo.png", "//example.invalid/logo.png", "http://example.invalid/logo.png"):
            with self.subTest(url=url), self.assertRaisesRegex(artifacts.ArtifactError, "logo"):
                artifacts.check_readme(README.replace("https://example.invalid/logo.png", url))

    def test_relative_links_rejected_in_all_repo_styles(self):
        for link in ("[x](./guide.md)", "[x](guide.md)", "![x](images/x.png)",
                     "[guide]: ../guide.md", '<a href="guide.md">x</a>'):
            with self.subTest(link=link), self.assertRaisesRegex(artifacts.ArtifactError, "link"):
                artifacts.check_readme(README + "\n" + link)

    def test_https_mailto_and_fragment_links_allowed(self):
        artifacts.check_readme(README + "[contact](mailto:hello@example.invalid)\n")

    def test_readme_mismatch_between_distributions(self):
        self.write(sdist=payload(README + "Other description\n"))
        with self.assertRaisesRegex(artifacts.ArtifactError, "disagree"):
            artifacts.validate(self.dist, VERSION)

    def test_missing_distribution(self):
        self.wheel.unlink()
        with self.assertRaisesRegex(artifacts.ArtifactError, "exactly one wheel"):
            artifacts.validate(self.dist, VERSION)

    def test_extra_distribution_rejected(self):
        (self.dist / "old.whl").write_bytes(b"stale")
        with self.assertRaisesRegex(artifacts.ArtifactError, "stale/extra"):
            artifacts.validate(self.dist, VERSION)

    def test_symlink_distribution_rejected(self):
        target = self.root / "real.whl"
        self.wheel.rename(target)
        try:
            self.wheel.symlink_to(target)
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaisesRegex(artifacts.ArtifactError, "non-symlink"):
            artifacts.validate(self.dist, VERSION)

    def test_nested_sdist_metadata_is_not_root_metadata(self):
        with tarfile.open(self.sdist, "w:gz") as archive:
            data = payload()
            member = tarfile.TarInfo("dead_letter/src/dead_letter.egg-info/PKG-INFO")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        with self.assertRaisesRegex(artifacts.ArtifactError, "root PKG-INFO"):
            artifacts.validate(self.dist, VERSION)

    def test_metadata_size_limit(self):
        with patch.object(artifacts, "MAX_METADATA", 8):
            with self.assertRaisesRegex(artifacts.ArtifactError, "bounded"):
                artifacts.validate(self.dist, VERSION)

    def test_exact_checksums_roundtrip(self):
        files = artifacts.validate(self.dist, VERSION)
        self.manifest.write_text(artifacts.checksums(files), encoding="utf-8")
        artifacts.verify_checksums(files, self.manifest)

    def test_tampered_distribution_fails(self):
        files = artifacts.validate(self.dist, VERSION)
        self.manifest.write_text(artifacts.checksums(files), encoding="utf-8")
        self.wheel.write_bytes(self.wheel.read_bytes() + b"tampered")
        with self.assertRaisesRegex(artifacts.ArtifactError, "recorded build"):
            artifacts.verify_checksums(files, self.manifest)

    def test_partial_duplicate_extra_and_unsafe_manifest_fail(self):
        files = artifacts.validate(self.dist, VERSION)
        original = artifacts.checksums(files)
        for content in (original.splitlines()[0] + "\n", original + original, original.replace("dead_letter", "../dead_letter"), ""):
            with self.subTest(content=content):
                self.manifest.write_text(content, encoding="utf-8")
                with self.assertRaises(artifacts.ArtifactError):
                    artifacts.verify_checksums(files, self.manifest)


class IsolationTests(unittest.TestCase):
    def test_host_python_paths_and_project_overrides_removed(self):
        variables = {key: "HOST" for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT", "UV_PROJECT_ENVIRONMENT", "UV_WORKING_DIRECTORY")}
        variables.update(TYPESAFE_API_KEY="PRIVATE_KEY", TYPESAFE_BASE_URL="https://private.example")
        with patch.dict(os.environ, variables):
            env = smoke.isolated_environment()
        for key in variables:
            self.assertNotIn(key, env)
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")
        self.assertEqual(env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"], "1")

    def test_noneditable_direct_artifact_outside_checkout_and_isolated_python(self):
        artifact = Path(tempfile.gettempdir()) / "test artifact.whl"
        with patch.object(smoke.subprocess, "run") as run:
            smoke.smoke(artifact, VERSION, "ui")
        self.assertEqual(run.call_count, 3)
        create, install, probe = run.call_args_list
        for call in (create, install, probe):
            self.assertFalse(call.kwargs["cwd"].is_relative_to(ROOT))
            self.assertEqual(call.kwargs["cwd"].name, "empty-cwd")
            self.assertNotIn("PYTHONPATH", call.kwargs["env"])
        self.assertIn("--no-config", install.args[0])
        self.assertIn("--no-sources", install.args[0])
        self.assertNotIn("--editable", install.args[0])
        self.assertIn(f"dead-letter[ui] @ {artifact.resolve().as_uri()}", install.args[0])
        self.assertEqual(probe.args[0][1], "-I")
        self.assertTrue(Path(probe.args[0][2]).is_absolute())

    def test_typesafe_contracts_run_after_installed_probe(self):
        artifact = Path(tempfile.gettempdir()) / "test artifact.whl"
        with patch.object(smoke.subprocess, "run") as run, patch.object(smoke, "sdk_contracts") as contracts:
            smoke.smoke(artifact, VERSION, "typesafe")
        self.assertEqual(run.call_count, 3)
        self.assertIn("dead-letter[typesafe]", run.call_args_list[1].args[0][-1])
        self.assertEqual(run.call_args_list[2].args[0][1], "-I")
        contracts.assert_called_once()

    def test_sdist_typesafe_selects_sdist_and_extra(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            wheel = directory / "package.whl"
            sdist = directory / "package.tar.gz"
            with patch.object(smoke, "validate", return_value=[wheel, sdist]), \
                 patch.object(smoke, "verify_checksums"), patch.object(smoke, "smoke") as installed:
                smoke.main(["--dist-dir", str(directory), "--checksums", str(directory / "SHA256SUMS"),
                            "--version", VERSION, "--profile", "sdist-typesafe"])
        installed.assert_called_once_with(sdist, VERSION, "typesafe")


class RunnerTests(unittest.TestCase):
    def test_missing_executable_is_unavailable_not_failure(self):
        with patch.object(verify.shutil, "which", return_value=None), patch.object(verify.subprocess, "run") as run:
            result = verify.run_check("node", ["node", "--version"])
        self.assertEqual(result["status"], "could-not-run")
        run.assert_not_called()

    def test_nonzero_check_is_failure(self):
        with patch.object(verify.shutil, "which", return_value="/bin/tool"), patch.object(verify.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
            self.assertEqual(verify.run_check("test", ["tool"])["status"], "failed")

    def test_timeout_is_failure(self):
        with patch.object(verify.shutil, "which", return_value="/bin/tool"), patch.object(verify.subprocess, "run", side_effect=subprocess.TimeoutExpired("tool", 1)):
            self.assertEqual(verify.run_check("test", ["tool"])["status"], "failed")

    def test_quick_continues_after_failure(self):
        with patch.object(verify, "run_check", side_effect=lambda name, cmd: {"name": name, "status": "failed" if name == "core" else "passed"}):
            results = verify.source_checks("quick", None)
        self.assertEqual([r["name"] for r in results], ["core", "backend", "frontend-syntax"])
        self.assertEqual(results[-1]["status"], "passed")

    def test_plugin_scope_has_tests_schema_and_skill(self):
        self.assertEqual([name for name, _ in verify.source_commands("full", ["plugin"])], ["plugin-tests", "plugin-schema", "agent-skills"])

    def test_full_covers_documented_commands(self):
        names = {name for name, _ in verify.source_commands("full")}
        self.assertEqual(names, {"core", "backend", "plugin-tests", "plugin-schema", "agent-skills", "frontend-tests", "frontend-syntax", "release-metadata"})

    def test_stdout_is_single_json_with_exit_codes(self):
        for statuses, expected in ((["passed"], 0), (["could-not-run"], 2), (["failed", "could-not-run"], 1)):
            with self.subTest(statuses=statuses), contextlib.redirect_stdout(io.StringIO()) as output:
                code = verify.summarize("quick", [{"name": str(i), "status": s} for i, s in enumerate(statuses)])
            document = json.loads(output.getvalue())
            self.assertEqual(code, expected)
            self.assertEqual(document["exit_code"], expected)
            self.assertEqual(sum(document["counts"].values()), len(statuses))

    def test_preexisting_artifacts_never_rebuild_or_rerecord(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "p.whl").touch()
            (directory / "p.tar.gz").touch()
            with patch.object(verify.shutil, "which", return_value="/bin/uv"), patch.object(verify, "run_check", side_effect=lambda name, command: {"name": name, "command": command, "status": "passed"}) as run:
                results = verify.packaging_checks(directory, directory / "SHA256SUMS", build=False, version=VERSION)
        self.assertEqual(len(results), 10)
        self.assertNotIn("build", [r["name"] for r in results])
        self.assertIn("verify", run.call_args_list[0].args[1])
        self.assertNotIn("record", run.call_args_list[0].args[1])
        self.assertEqual([r["name"] for r in results[2:]], [f"installed-{p}" for p in verify.PROFILES])

    def test_invalid_artifacts_never_installed(self):
        with patch.object(verify, "run_check", return_value={"name": "package-metadata", "status": "failed"}) as run:
            results = verify.packaging_checks(Path("unused"), Path("unused"), build=False, version=VERSION)
        self.assertEqual(run.call_count, 1)
        self.assertTrue(all(r["status"] == "could-not-run" for r in results[1:]))

    def test_missing_gh_skill_support_is_unavailable(self):
        with patch.object(verify, "source_commands", return_value=[("agent-skills", ["gh", "skill", "publish", "--dry-run"])]), patch.object(verify.shutil, "which", return_value="gh"), patch.object(verify.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
            result = verify.source_checks("full", ["plugin"])
        self.assertEqual(result[0]["status"], "could-not-run")

    def test_bad_arguments_fail_before_any_checks(self):
        with patch.object(verify, "source_checks") as run, contextlib.redirect_stderr(io.StringIO()):
            for args in (["quick", "--dist-dir", "dist"], ["packaging", "--suite", "core"], ["full", "--dist-dir", "dist", "--checksums", "sums"]):
                with self.assertRaises(SystemExit):
                    verify.main(args)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
