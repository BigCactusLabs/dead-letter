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
        self.assertEqual(data["jobs"]["publish"]["needs"], "preflight")
        scripts = "\n".join(step.get("run", "") for step in guard["steps"])
        self.assertIn('release.py check --tag "$RELEASE_TAG"', scripts)
        self.assertIn("git merge-base --is-ancestor HEAD origin/main", scripts)
        self.assertIn("uv sync --extra dev --locked", scripts)

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
