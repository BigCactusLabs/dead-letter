"""Release topology contracts; live credentials/publications are never exercised."""
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


class ReleaseWorkflowTests(unittest.TestCase):
    def test_package_publication_depends_on_read_only_preflight(self):
        data = workflow("release.yml")
        self.assertEqual(data["permissions"], {"contents": "read"})
        guard = data["jobs"]["preflight"]
        self.assertIn("startsWith(github.event.release.tag_name, 'v')", guard["if"])
        self.assertIn("!github.event.release.prerelease", guard["if"])
        self.assertNotIn("environment", guard)
        self.assertNotIn("id-token", guard.get("permissions", {}))
        self.assertEqual(data["jobs"]["build"]["needs"], "preflight")
        self.assertEqual(data["jobs"]["test-package"]["needs"], "build")
        self.assertEqual(data["jobs"]["publish"]["needs"], "test-package")
        scripts = "\n".join(step.get("run", "") for step in guard["steps"])
        self.assertIn('release.py check --tag "$RELEASE_TAG"', scripts)
        self.assertIn("git merge-base --is-ancestor HEAD origin/main", scripts)
        self.assertIn("uv sync --extra dev --locked", scripts)
        self.assertIn("python scripts/verify.py full", scripts)

    def test_privileged_publication_steps_keep_their_guards(self):
        data = workflow("release.yml")
        publish = data["jobs"]["publish"]
        self.assertEqual(publish["environment"], "release")
        self.assertEqual(publish["permissions"], {"contents": "read", "id-token": "write"})
        pypi = next(step for step in publish["steps"] if step.get("name") == "Publish to PyPI")
        self.assertTrue(pypi["with"]["attestations"])
        registry = data["jobs"]["publish-mcp"]
        self.assertEqual(registry["permissions"]["id-token"], "write")
        install = next(step for step in registry["steps"] if step.get("name") == "Install mcp-publisher")
        self.assertRegex(install["env"]["MCP_PUBLISHER_VERSION"], r"^v\d+\.\d+\.\d+$")
        self.assertRegex(install["env"]["MCP_PUBLISHER_SHA256"], r"^[0-9a-f]{64}$")
        self.assertIn("sha256sum --check --strict", install["run"])
        self.assertNotIn("releases/latest", install["run"])

    def test_python_build_is_once_and_has_no_publication_credentials(self):
        data = workflow("release.yml")
        jobs = data["jobs"]
        builds = [(name, step) for name, job in jobs.items() for step in job.get("steps", []) if "uv build" in step.get("run", "")]
        self.assertEqual([name for name, _ in builds], ["build"])
        for name in ("preflight", "build", "test-package"):
            self.assertNotIn("environment", jobs[name])
            self.assertEqual(jobs[name].get("permissions", data["permissions"]), {"contents": "read"})
        build_steps = jobs["build"]["steps"]
        record = next(i for i, step in enumerate(build_steps) if "package_artifacts.py record" in step.get("run", ""))
        upload = next(i for i, step in enumerate(build_steps) if step.get("uses", "").startswith("actions/upload-artifact@"))
        self.assertLess(record, upload)
        self.assertEqual(build_steps[upload]["with"]["if-no-files-found"], "error")
        self.assertNotIn("overwrite", build_steps[upload]["with"])

    def test_test_and_publish_download_identical_artifact(self):
        jobs = workflow("release.yml")["jobs"]
        upload = next(step for step in jobs["build"]["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
        for name in ("test-package", "publish"):
            download = next(step for step in jobs[name]["steps"] if step.get("uses", "").startswith("actions/download-artifact@"))
            self.assertEqual(download["with"]["name"], upload["with"]["name"])
            self.assertEqual(download["with"]["path"], upload["with"]["path"])
        smoke = "\n".join(step.get("run", "") for step in jobs["test-package"]["steps"])
        self.assertIn("verify.py packaging --dist-dir build/package/dist --checksums build/package/SHA256SUMS", smoke)
        self.assertNotIn("uv sync", smoke)
        self.assertNotIn("uv build", smoke)

    def test_publisher_only_downloads_verifies_and_uploads(self):
        steps = workflow("release.yml")["jobs"]["publish"]["steps"]
        self.assertEqual(len(steps), 3)
        self.assertTrue(steps[0]["uses"].startswith("actions/download-artifact@"))
        self.assertIn("sha256sum --check --strict ../SHA256SUMS", steps[1]["run"])
        self.assertTrue(steps[2]["uses"].startswith("pypa/gh-action-pypi-publish@"))
        self.assertEqual(steps[2]["with"]["packages-dir"], "build/package/dist/")
        self.assertFalse(any("checkout@" in step.get("uses", "") for step in steps))

    def test_summary_records_build_evidence_and_test_outcome(self):
        summary = workflow("release.yml")["jobs"]["summary"]
        self.assertIn("build", summary["needs"])
        self.assertIn("test-package", summary["needs"])
        self.assertEqual(summary["steps"][0]["env"]["CHECKSUMS"], "${{ needs.build.outputs.checksums }}")
        self.assertEqual(summary["permissions"], {})

    def test_source_ci_suites_cannot_hide_each_other(self):
        jobs = workflow("ci.yml")["jobs"]
        for name in ("core", "backend", "plugin", "frontend"):
            self.assertNotIn("needs", jobs[name])
            scripts = "\n".join(step.get("run", "") for step in jobs[name]["steps"])
            self.assertIn(f"python scripts/verify.py full --suite {name}", scripts)
            if name != "frontend":
                self.assertIn("scripts/ci_provenance.py", scripts)
        self.assertNotIn("needs", jobs["packaging"])
        self.assertIn("python scripts/verify.py packaging", "\n".join(step.get("run", "") for step in jobs["packaging"]["steps"]))
        self.assertEqual(jobs["test"]["if"], "always()")
        self.assertEqual(set(jobs["test"]["needs"]), {"core", "backend", "plugin", "frontend", "packaging"})
        self.assertEqual(jobs["mcpb"]["strategy"]["matrix"]["os"], ["ubuntu-latest", "macos-latest", "windows-latest"])

    def test_package_assets_are_reused_and_never_clobbered(self):
        data = workflow("release.yml")
        steps = data["jobs"]["build-mcpb"]["steps"]
        scripts = "\n".join(step.get("run", "") for step in steps)
        self.assertIn("gh release download", scripts)
        self.assertIn('sha256sum "$asset" | cmp - "$asset.sha256"', scripts)
        self.assertIn("release.py upload-assets", scripts)
        self.assertNotIn("--clobber", scripts)

    def test_resolved_manifest_archived_before_registry_publication(self):
        steps = workflow("release.yml")["jobs"]["publish-mcp"]["steps"]
        names = [step["name"] for step in steps]
        self.assertLess(names.index("Add the verified OCI package to the release metadata"), names.index("Archive the resolved registry manifest and checksum"))
        self.assertLess(names.index("Archive the resolved registry manifest and checksum"), names.index("Publish to the MCP Registry"))

    def test_plugin_validation_and_availability_precede_marketplace(self):
        data = workflow("plugin-release.yml")
        self.assertFalse(data["concurrency"]["cancel-in-progress"])
        steps = data["jobs"]["advance-release"]["steps"]
        names = [step["name"] for step in steps]
        before = names.index("Validate plugin tag and target before any pointer update")
        wait = names.index("Wait for both PyPI indexes to serve the pinned package")
        publish = names.index("Publish plugin pointer through the marketplace")
        self.assertLess(before, wait)
        self.assertLess(wait, publish)
        self.assertIn('git merge-base --is-ancestor origin/release "$sha"', steps[before]["run"])
        self.assertEqual(steps[publish]["env"]["PLUGIN_SHA"], "${{ steps.release.outputs.sha }}")
        self.assertIn('release.py wait-pypi "$MCP_VERSION"', steps[wait]["run"])

    def test_docs_inventory_covers_all_distribution_guides_not_fixtures(self):
        steps = workflow("docs-link-check.yml")["jobs"]["link-check"]["steps"]
        inventory = next(step["run"] for step in steps if step.get("name") == "Inventory maintained Markdown")
        for name in ("plugin", "skills", "mcpb", "docker", ".github", "benchmarks/README.md"):
            self.assertIn(name, inventory)
        checker = next(step for step in steps if step.get("name") == "Check markdown links")
        self.assertIn("--files-from", checker["with"]["args"])
        self.assertTrue(checker["with"]["failIfEmpty"])


if __name__ == "__main__":
    unittest.main()
