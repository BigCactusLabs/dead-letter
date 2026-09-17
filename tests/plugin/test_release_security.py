"""Publication-job safeguards; runs in the existing plugin CI suite."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _workflow():
    return yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))


def test_pypi_publish_does_not_restore_a_shared_uv_cache():
    job = _workflow()["jobs"]["publish"]
    uv_steps = [step for step in job["steps"] if step.get("uses", "").startswith("astral-sh/setup-uv@")]
    assert uv_steps, "Publication must explicitly configure its uv cache policy"
    for step in uv_steps:
        assert step.get("with", {}).get("enable-cache") in (False, "false")
    assert not any(step.get("uses", "").startswith("actions/cache") for step in job["steps"])


def test_release_checkouts_do_not_persist_credentials():
    for name in ("publish", "publish-mcp"):
        job = _workflow()["jobs"][name]
        assert job["permissions"]["contents"] == "read"
        assert job["permissions"]["id-token"] == "write"
        checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
        assert checkout["with"]["persist-credentials"] in (False, "false")


def test_release_version_is_data_not_interpolated_shell_source():
    job = _workflow()["jobs"]["publish-mcp"]
    for step in job["steps"]:
        assert "${{ steps.version.outputs.version }}" not in step.get("run", "")
    for name in ("Wait for PyPI to serve the release", "Stamp server.json with the release version"):
        step = next(step for step in job["steps"] if step.get("name") == name)
        assert step["env"]["RELEASE_VERSION"] == "${{ steps.version.outputs.version }}"
        assert 'v="$RELEASE_VERSION"' in step["run"]


def test_registry_publication_stays_after_pypi():
    assert _workflow()["jobs"]["publish-mcp"]["needs"] == "publish"
