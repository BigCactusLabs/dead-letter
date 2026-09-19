"""Synthetic release evidence only: no public credentials, mail, or publications."""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import release
import release_evidence as e
import release_status as s

VERSION = "1.2.3"
NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)
SHA = "a" * 40
PYPI = f"https://pypi.org/pypi/dead-letter/{VERSION}/json"
GITHUB = f"{e.API}{e.REPO}/releases/tags/v{VERSION}"
TOKEN = "https://ghcr.io/token?service=ghcr.io&scope=repository%3Abigcactuslabs%2Fdead-letter%3Apull"
OCI = f"https://ghcr.io/v2/bigcactuslabs/dead-letter/manifests/{VERSION}"


def file_entry(name, kind="sdist", sha="b" * 64, age=1):
    return {"filename": name, "packagetype": kind, "url": "https://files.pythonhosted.org/packages/fixture/" + name,
            "digests": {"sha256": sha}, "yanked": False, "upload_time_iso_8601": (NOW - timedelta(days=age)).isoformat()}


def python_release(version=VERSION):
    return {"info": {"name": "dead-letter", "version": version}, "urls": [
        file_entry(f"dead_letter-{version}.tar.gz"), file_entry(f"dead_letter-{version}-py3-none-any.whl", "bdist_wheel", "c" * 64)]}


def formula_text(version=VERSION):
    return f'''class DeadLetter < Formula
  include Language::Python::Virtualenv
  url "https://files.pythonhosted.org/packages/fixture/dead_letter-{version}.tar.gz"
  sha256 "{'b' * 64}"
  depends_on arch: :arm64
  depends_on "python@3.14"
  resource "six" do
    url "https://files.pythonhosted.org/packages/fixture/six-1.17.0-py3-none-any.whl"
    sha256 "{'d' * 64}"
  end
  def install
    rm bin/"dead-letter-mcp"
    rm bin/"dead-letter-ui"
  end
end
'''


class MemoryClient(e.Client):
    def __init__(self):
        self.responses = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        value = self.responses.get(url, e.Missing("not found"))
        if isinstance(value, Exception):
            raise value
        if isinstance(value, tuple):
            return value
        return json.dumps(value).encode(), {}

    def add_file(self, repo, path, text, sha=SHA):
        body = text.encode()
        blob = hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest()
        self.responses[f"{e.API}{repo}/contents/{path}?ref={sha}"] = {
            "type": "file", "encoding": "base64", "sha": blob, "size": len(body), "content": base64.b64encode(body).decode()}


def fixture(pin=VERSION, tap_version=VERSION):
    client = MemoryClient()
    r = client.responses
    r[PYPI] = python_release()
    r[f"https://pypi.org/pypi/dead-letter/{pin}/json"] = python_release(pin)
    r[f"https://pypi.org/pypi/dead-letter/{tap_version}/json"] = python_release(tap_version)
    checksums = {f["filename"]: f["digests"]["sha256"] for f in r[PYPI]["urls"]}
    oci = json.dumps({"schemaVersion": 2, "manifests": [{"platform": {"os": "linux", "architecture": arch}}
                                                               for arch in ("amd64", "arm64")]}).encode()
    image_digest = "sha256:" + hashlib.sha256(oci).hexdigest()
    r[TOKEN] = {"token": "SYNTHETIC_TOKEN"}
    r[OCI] = (oci, {"docker-content-digest": image_digest})
    bundle_name = f"dead-letter-mcp-{VERSION}.mcpb"
    manifest_name = f"dead-letter-server-{VERSION}.json"
    bundle = b"synthetic bundle bytes, never executed"
    bundle_url = f"https://github.com/{e.REPO}/releases/download/v{VERSION}/{bundle_name}"
    server = {"name": e.SERVER, "version": VERSION, "packages": [
        {"registryType": "pypi", "identifier": "dead-letter", "version": VERSION,
         "runtimeArguments": [{"name": "--from", "value": f"dead-letter[mcp]=={VERSION}"}]},
        {"registryType": "mcpb", "identifier": bundle_url, "version": VERSION, "fileSha256": hashlib.sha256(bundle).hexdigest()},
        {"registryType": "oci", "identifier": e.IMAGE + "@" + image_digest}]}
    assets = {bundle_name: bundle, manifest_name: json.dumps(server).encode()}
    for name, body in list(assets.items()):
        assets[name + ".sha256"] = f"{hashlib.sha256(body).hexdigest()}  {name}\n".encode()
    r[GITHUB] = {"tag_name": "v" + VERSION, "draft": False, "prerelease": False, "immutable": False, "assets": []}
    for name, body in assets.items():
        url = f"https://github.com/{e.REPO}/releases/download/v{VERSION}/{name}"
        r[GITHUB]["assets"].append({"name": name, "browser_download_url": url, "size": len(body), "state": "uploaded"})
        r[url] = (body, {})
    r[s.REGISTRY + VERSION] = {"server": deepcopy(server), "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active"}}}
    for repo, ref in ((e.MARKETPLACE, "heads/main"), (e.TAP, "heads/main"), (e.REPO, "heads/release"), (e.REPO, "tags/plugin-v9.0.0")):
        r[f"{e.API}{repo}/git/ref/{ref}"] = {"object": {"sha": SHA, "type": "commit"}}
    marketplace = {"plugins": [{"name": "dead-letter", "version": "9.0.0", "source": {
        "source": "git-subdir", "url": f"https://github.com/{e.REPO}.git", "path": "plugin", "ref": "plugin-v9.0.0", "sha": SHA}}]}
    client.add_file(e.MARKETPLACE, ".claude-plugin/marketplace.json", json.dumps(marketplace))
    client.add_file(e.REPO, "plugin/.claude-plugin/plugin.json", json.dumps({"version": "9.0.0"}))
    client.add_file(e.REPO, "plugin/.mcp.json", json.dumps({"mcpServers": {"dead-letter": {"args": ["--from", f"dead-letter[mcp]=={pin}"]}}}))
    client.add_file(e.TAP, "Formula/dead-letter.rb", formula_text(tap_version))
    return client, checksums, image_digest


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.client, self.checksums, self.image = fixture()

    def report(self, **kwargs):
        return s.collect(VERSION, client=self.client, now=NOW, checksums=self.checksums, **kwargs)

    def test_all_channels_verified_and_no_latest(self):
        report = self.report()
        self.assertEqual(report["counts"]["verified"], 6)
        self.assertEqual(report["exit_code"], 0)
        self.assertTrue(all("latest" not in url for url in self.client.calls))
        self.assertNotIn("SYNTHETIC_TOKEN", json.dumps(report))

    def test_missing_build_evidence_never_self_attests(self):
        report = s.collect(VERSION, client=self.client, now=NOW)
        self.assertEqual(report["channels"]["pypi"]["status"], "unable-to-verify")
        self.assertEqual(report["channels"]["homebrew"]["status"], "unable-to-verify")
        self.assertEqual(report["exit_code"], 1)

    def test_network_failure_is_unknown_not_missing_and_does_not_stop_other_channels(self):
        self.client.responses[PYPI] = e.Unavailable("HTTP 503")
        report = self.report()
        self.assertEqual(report["channels"]["pypi"]["status"], "unable-to-verify")
        self.assertEqual(report["channels"]["github-release"]["status"], "verified")
        self.assertEqual(len(report["channels"]), 6)

    def test_pypi_checksum_conflict(self):
        self.client.responses[PYPI]["urls"][0]["digests"]["sha256"] = "d" * 64
        self.assertEqual(self.report()["channels"]["pypi"]["status"], "conflicting")

    def test_old_partial_release_needs_new_version(self):
        files = self.client.responses[PYPI]["urls"]
        files.pop()
        files[0]["upload_time_iso_8601"] = (NOW - timedelta(days=15)).isoformat()
        row = self.report()["channels"]["pypi"]
        self.assertEqual(row["status"], "missing")
        self.assertTrue(row["evidence"]["needs_new_version"])
        self.assertIn("Needs new version", row["next_action"])

    def test_recent_or_unknown_age_is_not_promise_of_retry(self):
        self.client.responses[PYPI]["urls"].pop()
        for date in ((NOW - timedelta(days=14)).isoformat(), "malformed"):
            self.client.responses[PYPI]["urls"][0]["upload_time_iso_8601"] = date
            row = self.report()["channels"]["pypi"]
            self.assertEqual(row["status"], "missing")
            self.assertFalse(row["evidence"]["needs_new_version"])
            self.assertIn("not verified", row["next_action"])

    def test_pypi_yanked_extra_and_malformed(self):
        original = deepcopy(self.client.responses[PYPI])
        for mutate, expected in ((lambda d: d["urls"][0].update(yanked=True), "conflicting"),
                                 (lambda d: d["urls"].append(file_entry("surprise.whl", "bdist_wheel")), "conflicting"),
                                 (lambda d: d.update(urls=None), "unable-to-verify")):
            self.client.responses[PYPI] = deepcopy(original)
            mutate(self.client.responses[PYPI])
            self.assertEqual(self.report()["channels"]["pypi"]["status"], expected)

    def test_github_missing_assets_and_immutable_recovery(self):
        self.client.responses[GITHUB]["assets"].pop()
        self.client.responses[GITHUB]["immutable"] = True
        row = self.report()["channels"]["github-release"]
        self.assertEqual(row["status"], "missing")
        self.assertIn("immutable", row["next_action"])

    def test_asset_tamper_blocks_registry_verification(self):
        asset = self.client.responses[GITHUB]["assets"][0]
        body, headers = self.client.responses[asset["browser_download_url"]]
        self.client.responses[asset["browser_download_url"]] = (b"x" * len(body), headers)
        report = self.report()
        self.assertEqual(report["channels"]["github-release"]["status"], "conflicting")
        self.assertEqual(report["channels"]["mcp-registry"]["status"], "unable-to-verify")

    def test_asset_url_cannot_redirect_to_arbitrary_host(self):
        self.client.responses[GITHUB]["assets"][0]["browser_download_url"] = "https://example.test/secret"
        self.assertEqual(self.report()["channels"]["github-release"]["status"], "conflicting")
        self.assertFalse(any("example.test" in url for url in self.client.calls))

    def test_registry_checks_exact_packages_and_active_state(self):
        self.client.responses[s.REGISTRY + VERSION]["server"]["packages"][1]["fileSha256"] = "e" * 64
        self.assertEqual(self.report()["channels"]["mcp-registry"]["status"], "conflicting")

    def test_oci_ledger_disagreement(self):
        self.assertEqual(self.report(expected_oci="sha256:" + "b" * 64)["channels"]["ghcr"]["status"], "conflicting")

    def test_ghcr_token_404_is_unknown_but_manifest_404_is_missing(self):
        self.client.responses[TOKEN] = e.Missing("404")
        self.assertEqual(self.report()["channels"]["ghcr"]["status"], "unable-to-verify")
        self.client.responses[TOKEN] = {"token": "fixture"}
        self.client.responses[OCI] = e.Missing("404")
        self.assertEqual(self.report()["channels"]["ghcr"]["status"], "missing")

    def test_ghcr_tag_header_tamper(self):
        body, headers = self.client.responses[OCI]
        self.client.responses[OCI] = (body, {"docker-content-digest": "sha256:" + "e" * 64})
        self.assertEqual(self.report()["channels"]["ghcr"]["status"], "conflicting")

    def test_independent_plugin_version_is_valid(self):
        row = self.report()["channels"]["plugin-marketplace"]
        self.assertEqual(row["status"], "verified")
        self.assertEqual(row["evidence"]["plugin_version"], "9.0.0")

    def test_different_existing_plugin_pin_and_tap_are_deferred(self):
        self.client, _, _ = fixture(pin="1.2.2", tap_version="1.2.1")
        report = self.report()
        self.assertEqual(report["channels"]["plugin-marketplace"]["status"], "deferred")
        self.assertEqual(report["channels"]["homebrew"]["status"], "deferred")
        self.assertEqual(report["exit_code"], 0)

    def test_nonexistent_plugin_pin_is_not_deferred(self):
        self.client, _, _ = fixture(pin="1.2.2")
        self.client.responses["https://pypi.org/pypi/dead-letter/1.2.2/json"] = e.Missing("404")
        self.assertEqual(self.report()["channels"]["plugin-marketplace"]["status"], "conflicting")

    def test_plugin_branch_disagreement(self):
        self.client.responses[f"{e.API}{e.REPO}/git/ref/heads/release"]["object"]["sha"] = "b" * 40
        self.assertEqual(self.report()["channels"]["plugin-marketplace"]["status"], "conflicting")

    def test_annotated_plugin_tag_is_peeled(self):
        self.client.responses[f"{e.API}{e.REPO}/git/ref/tags/plugin-v9.0.0"]["object"] = {"sha": "b" * 40, "type": "tag"}
        self.client.responses[f"{e.API}{e.REPO}/git/tags/{'b' * 40}"] = {"object": {"sha": SHA, "type": "commit"}}
        self.assertEqual(self.report()["channels"]["plugin-marketplace"]["status"], "verified")

    def test_formula_never_confuses_dependency_version_for_root(self):
        text = formula_text().replace('  url "', '# root removed\n  # url "', 1)
        with self.assertRaises(e.Unavailable):
            e.formula(text)

    def test_formula_optional_resource_and_wrong_explicit_version_rejected(self):
        for text in (formula_text().replace('resource "six"', 'resource "mcp"'), formula_text().replace("  depends_on arch", '  version "9.0.0"\n  depends_on arch')):
            with self.assertRaises(e.Conflict):
                e.formula(text)

    def test_status_cli_uses_no_checkout_metadata_or_write_commands(self):
        report = self.report()
        with patch.object(s, "collect", return_value=report), patch.object(release, "read_sources", side_effect=AssertionError("local source read")), patch.object(release, "upload_assets", side_effect=AssertionError("write")), patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(release.main(["--root", "/missing-checkout", "status", "--version", VERSION, "--json"]), 0)
            self.assertEqual(json.loads(out.getvalue()), report)


class EvidenceTests(unittest.TestCase):
    def test_checksums_are_version_bound_and_reject_unsafe_duplicate_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "SHA256SUMS"
            text = f"{'b' * 64}  dead_letter-{VERSION}-py3-none-any.whl\n{'c' * 64}  dead_letter-{VERSION}.tar.gz\n"
            path.write_text(text)
            self.assertEqual(len(e.read_checksums(path, VERSION)), 2)
            for bad in (text + text, text.replace("dead_letter-", "../dead_letter-"), text.replace("b", "0"), text.replace(VERSION, "1.2.2")):
                path.write_text(bad)
                with self.assertRaises(ValueError):
                    e.read_checksums(path, VERSION)

    def test_safe_urls_and_credential_redirect_boundaries(self):
        for url in ("http://pypi.org/x", "https://user:password@pypi.org/x", "https://evil.test/", 'https://files.pythonhosted.org/evil"url', "https://pypi.org:444/x"):
            with self.assertRaises(e.Unavailable):
                e.safe_url(url)
        redirect = e.SafeRedirects()
        req = Request("https://api.github.com/repos/test", headers={"Authorization": "Bearer SECRET"})
        with self.assertRaises(e.Unavailable):
            redirect.redirect_request(req, None, 302, "", {}, "https://github.com/test")
        req = Request("https://github.com/fixture")
        forwarded = redirect.redirect_request(req, None, 302, "", {}, "https://release-assets.githubusercontent.com/fixture")
        self.assertFalse(forwarded.has_header("Authorization"))

    def test_network_errors_are_cached_and_never_retried(self):
        client = e.Client(token="SECRET")
        for error, expected in ((HTTPError(PYPI, 404, "", {}, None), e.Missing),
                                (HTTPError(PYPI, 429, "", {}, None), e.Unavailable),
                                (URLError("SECRET"), e.Unavailable)):
            client.errors.clear()
            client.opener = Mock()
            client.opener.open.side_effect = error
            for _ in range(2):
                with self.assertRaises(expected) as caught:
                    client.get(PYPI)
                self.assertNotIn("SECRET", str(caught.exception))
            self.assertEqual(client.opener.open.call_count, 1)
            request = client.opener.open.call_args.args[0]
            self.assertEqual(request.get_method(), "GET")
            self.assertFalse(request.has_header("Authorization"))

    def test_github_token_only_on_api_host(self):
        client = e.Client(token="SECRET")
        response = Mock()
        response.status, response.headers = 200, {}
        response.read.return_value = b"{}"
        client.opener = Mock()
        client.opener.open.return_value.__enter__ = Mock(return_value=response)
        client.opener.open.return_value.__exit__ = Mock(return_value=False)
        client.get(GITHUB)
        self.assertEqual(client.opener.open.call_args.args[0].get_header("Authorization"), "Bearer SECRET")
        with self.assertRaises(e.Unavailable):
            client.get(PYPI, bearer="SECRET")

    def test_file_blob_mismatch_and_malformed_json(self):
        client, _, _ = fixture()
        key = f"{e.API}{e.TAP}/contents/Formula/dead-letter.rb?ref={SHA}"
        client.responses[key]["sha"] = "b" * 40
        with self.assertRaises(e.Conflict):
            client.file(e.TAP, "Formula/dead-letter.rb", SHA)
        for text in ("[]", "invalid", "null"):
            with self.assertRaises(e.Unavailable):
                e.object_json(text)

    def test_sdist_preparation_needs_matching_full_build(self):
        client, sums, _ = fixture()
        self.assertEqual(e.released_sdist(client, VERSION, sums)["packagetype"], "sdist")
        sums[next(iter(sums))] = "d" * 64
        with self.assertRaises(e.Conflict):
            e.released_sdist(client, VERSION, sums)


if __name__ == "__main__":
    unittest.main()


class GuardedEvidenceTests(unittest.TestCase):
    def test_unable_to_verify_records_failure_class(self):
        def broken():
            raise AttributeError("'list' object has no attribute 'get'")
        outcome = s.guarded(broken)
        self.assertEqual(outcome["status"], "unable-to-verify")
        self.assertEqual(outcome["evidence"]["error_type"], "AttributeError")
        self.assertIn("no attribute", outcome["evidence"]["error"])

