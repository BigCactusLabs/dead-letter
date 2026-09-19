"""Workflow guardrails; credentials are synthetic and no push is performed."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"


def workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def all_steps(name: str):
    for job in workflow(name)["jobs"].values():
        yield from job.get("steps", [])


def push_commands() -> list[list[str]]:
    commands = []
    for step in all_steps("plugin-release.yml"):
        for line in step.get("run", "").splitlines():
            if not re.match(r"^\s*git\s.*\bpush\b", line):
                continue
            words = shlex.split(line, comments=True)
            if words and words[0] == "git" and "push" in words:
                commands.append(words)
    return commands


class WorkflowSecurityTests(unittest.TestCase):
    def test_all_checkouts_remove_persisted_credentials(self):
        checked = 0
        for path in WORKFLOWS.glob("*.y*ml"):
            for step in all_steps(path.name):
                if step.get("uses", "").startswith("actions/checkout@"):
                    checked += 1
                    with self.subTest(path=path.name, step=step.get("name")):
                        self.assertIs(step.get("with", {}).get("persist-credentials"), False)
        self.assertGreater(checked, 0)

    def test_remote_actions_remain_sha_pinned(self):
        for path in WORKFLOWS.glob("*.y*ml"):
            for step in all_steps(path.name):
                uses = step.get("uses", "")
                if uses and not uses.startswith(("./", "$/")):
                    with self.subTest(path=path.name, uses=uses):
                        self.assertRegex(uses, r"^[^@]+@[0-9a-f]{40}$")

    def test_reviewed_pin_comments_match_exact_versions(self):
        # Only these previously mislabeled pins are constrained. Updating an
        # action SHA is allowed; zizmor validates the replacement against GitHub.
        pins = {
            "3d3c42e5aac5ba805825da76410c181273ba90b1": "v7.0.1",
            "820762786026740c76f36085b0efc47a31fe5020": "v7.0.0",
            "ba38be9e461d3875417946c167d0b5f3d385a247": "v1.14.1",
        }
        for path in WORKFLOWS.glob("*.y*ml"):
            for line in path.read_text().splitlines():
                for sha, version in pins.items():
                    if "uses:" in line and "@" + sha in line:
                        self.assertEqual(line.split("#", 1)[1].strip(), version, str(path))

    def test_release_jobs_disable_implicit_dependency_caches(self):
        jobs = list(workflow("release.yml")["jobs"].values())
        jobs.append(workflow("container.yml")["jobs"]["publish"])
        for job in jobs:
            for step in job.get("steps", []):
                uses = step.get("uses", "")
                options = step.get("with", {})
                if uses.startswith("astral-sh/setup-uv@"):
                    self.assertIs(options.get("enable-cache"), False)
                if uses.startswith("actions/setup-node@"):
                    self.assertIs(options.get("package-manager-cache"), False)
                self.assertFalse(uses.startswith("actions/cache@"))

    def test_lint_runs_on_pr_and_development_pushes(self):
        data = workflow("workflow-lint.yml")
        # PyYAML's YAML 1.1 loader treats the unquoted Actions key `on` as True.
        events = data.get("on", data.get(True))
        self.assertIn("pull_request", events)
        self.assertEqual(set(events["push"]["branches"]), {"main", "feat/**"})
        self.assertNotIn("paths", events["pull_request"] or {})
        self.assertNotIn("pull_request_target", events)
        self.assertNotIn("workflow_run", events)

    def test_lint_has_only_read_permission_and_no_publication_environment(self):
        data = workflow("workflow-lint.yml")
        self.assertEqual(data["permissions"], {"contents": "read"})
        job = data["jobs"]["workflow-lint"]
        self.assertNotIn("environment", job)
        self.assertNotIn("permissions", job)
        self.assertLessEqual(job["timeout-minutes"], 10)
        self.assertNotIn("secrets.", (WORKFLOWS / "workflow-lint.yml").read_text())

    def test_actionlint_is_pinned_verified_before_execution_and_requires_shellcheck(self):
        steps = list(all_steps("workflow-lint.yml"))
        install = next(s for s in steps if s.get("id") == "actionlint")
        self.assertEqual(install["env"]["ACTIONLINT_VERSION"], "1.7.12")
        self.assertEqual(install["env"]["ACTIONLINT_SHA256"],
                         "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8")
        script = install["run"]
        self.assertLess(script.index("sha256sum --check --strict"), script.index("tar -xzf"))
        self.assertIn("command -v shellcheck", script)
        self.assertIn('"$tools/actionlint" -version', script)
        self.assertNotIn("releases/latest", script)
        lint = next(s for s in steps if s.get("run") == "actionlint -color")
        self.assertIn("failure()", lint["if"])
        self.assertIn("steps.actionlint.outcome == 'success'", lint["if"])
        self.assertNotIn("continue-on-error", lint)

    def test_zizmor_is_independent_regular_and_enforcing(self):
        step = next(s for s in all_steps("workflow-lint.yml") if "zizmor==" in s.get("run", ""))
        self.assertEqual(shlex.split(step["run"]), [
            "uv", "tool", "run", "--from", "zizmor==1.30.1", "zizmor", "--no-progress",
            "--persona=regular", ".github/workflows",
        ])
        self.assertEqual(step["if"], "success() || failure()")
        self.assertNotIn("continue-on-error", step)
        self.assertEqual(step["env"]["GH_TOKEN"], "${{ github.token }}")

    def test_only_justified_job_level_reuse_ignore_is_allowed(self):
        ignores = []
        for path in WORKFLOWS.glob("*.y*ml"):
            for line in path.read_text().splitlines():
                if "zizmor: ignore" in line:
                    ignores.append((path.name, line))
                    self.assertRegex(line, r"# zizmor: ignore\[[a-z-]+\] \S.+")
        self.assertEqual(len(ignores), 1)
        name, line = ignores[0]
        self.assertEqual(name, "release.yml")
        self.assertIn("uses: ./.github/workflows/container.yml # zizmor: ignore[self-repository]", line)
        job = workflow(name)["jobs"]["build-container"]
        self.assertEqual(job["uses"], "./.github/workflows/container.yml")
        self.assertNotIn("steps", job)
        # A future central suppression file requires explicit review of this test.
        for path in (ROOT / "zizmor.yml", ROOT / "zizmor.yaml", ROOT / ".github/zizmor.yml", ROOT / ".github/zizmor.yaml"):
            self.assertFalse(path.exists(), str(path))

    def test_source_checkout_uses_read_token_not_release_pat(self):
        data = workflow("plugin-release.yml")
        self.assertEqual(data["permissions"], {"contents": "read"})
        job = data["jobs"]["advance-release"]
        self.assertNotIn("env", job)
        checkout = next(s for s in job["steps"] if s.get("name") == "Checkout")
        self.assertNotIn("token", checkout["with"])
        self.assertIs(checkout["with"]["persist-credentials"], False)

    def test_both_plugin_pushes_have_ephemeral_helper_and_step_token(self):
        commands = push_commands()
        self.assertEqual(len(commands), 2)
        for command in commands:
            self.assertEqual(command[:6], ["git", "-c", "credential.helper=", "-c",
                                          "credential.helper=!gh auth git-credential", "push"])
            self.assertEqual(command[6], "origin")
            self.assertEqual(len(command), 8)
            self.assertFalse(command[-1].startswith("+"))
        self.assertEqual(commands[-1][-1], "$PLUGIN_SHA:refs/heads/release")
        for step in all_steps("plugin-release.yml"):
            if "credential.helper=" in step.get("run", ""):
                self.assertEqual(step["env"]["GH_TOKEN"], "${{ secrets.RELEASE_PAT }}")
        text = (WORKFLOWS / "plugin-release.yml").read_text()
        self.assertNotIn("gh auth setup-git", text)
        self.assertNotIn("http.extraheader", text)

    def test_dependency_pr_action_supplies_its_own_authentication(self):
        steps = list(all_steps("dependency-refresh.yml"))
        writer = next(s for s in steps if s.get("name") == "Create pull request")
        self.assertTrue(writer["uses"].startswith("peter-evans/create-pull-request@"))
        self.assertNotIn("git push", "\n".join(s.get("run", "") for s in steps))
        self.assertEqual(writer["with"]["add-paths"].strip(), "uv.lock")


@unittest.skipUnless(shutil.which("git") and os.name != "nt", "POSIX Git credential helper test")
class CredentialHelperTests(unittest.TestCase):
    def exercise(self, *, real_gh: bool):
        # Execute the actual workflow's helper prefix, substituting credential
        # protocol operations for `push`; these commands never contact a remote.
        command = push_commands()[-1]
        prefix = command[:command.index("push")]
        with tempfile.TemporaryDirectory(prefix="dead-letter-credentials-") as temporary:
            root = Path(temporary)
            env = {"PATH": os.environ.get("PATH", ""), "HOME": str(root),
                   "XDG_CONFIG_HOME": str(root / "config"), "GH_CONFIG_DIR": str(root / "gh"),
                   "GH_TOKEN": "synthetic-not-a-real-token", "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_CONFIG_GLOBAL": str(root / "gitconfig"), "GIT_TERMINAL_PROMPT": "0"}
            if not real_gh:
                helper = root / "gh"
                env["GH_CONFIG_DIR"] = str(root / "gh-config")
                helper.write_text("#!/bin/sh\n"
                                  '[ "$1 $2" = "auth git-credential" ] || exit 1\n'
                                  '[ "$3" = get ] || exit 0\n'
                                  'printf "username=x-access-token\\npassword=%s\\n" "$GH_TOKEN"\n')
                helper.chmod(0o700)
                env["PATH"] = str(root) + os.pathsep + env["PATH"]
            filled = subprocess.run(prefix + ["credential", "fill"], cwd=root, env=env,
                                    input="protocol=https\nhost=github.com\n\n", text=True,
                                    capture_output=True, timeout=15, check=True)
            self.assertIn("username=x-access-token", filled.stdout)
            self.assertIn("password=" + env["GH_TOKEN"], filled.stdout)
            subprocess.run(prefix + ["credential", "approve"], cwd=root, env=env,
                           input=filled.stdout + "\n", text=True, capture_output=True,
                           timeout=15, check=True)
            self.assertFalse((root / "gitconfig").exists())
            self.assertFalse((root / ".git-credentials").exists())
            for path in root.rglob("*"):
                if path.is_file():
                    self.assertNotIn(env["GH_TOKEN"].encode(), path.read_bytes())

    def test_git_helper_protocol_with_synthetic_cli(self):
        self.exercise(real_gh=False)

    @unittest.skipUnless(shutil.which("gh"), "GitHub CLI unavailable locally; exercised in CI")
    def test_actual_gh_uses_environment_token_without_persistence(self):
        self.exercise(real_gh=True)


if __name__ == "__main__":
    unittest.main()
