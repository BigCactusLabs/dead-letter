"""Discovery evidence must not mistake errors, namesakes, or partial data for absence."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("discovery_audit", ROOT / "scripts/audit_mcp_discovery.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def registry():
    return {"server": {"name": audit.NAME, "repository": {"url": audit.REPO}, "version": "0.2.5", "packages": [{"registryType": "pypi", "identifier": "dead-letter", "version": "0.2.5"}]}, "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active"}}}


def catalog(entries):
    return {"version": 1, "entries": entries, "counts": {"total": len(entries)}, "generatedAt": "2026-09-18T00:00:00Z"}


def test_registry_identity_version_status_and_packages_are_reported():
    result = audit.registry_result(registry())
    assert result["status"] == "present" and result["version"] == "0.2.5"
    assert result["registry_status"] == "active"
    assert result["packages"][0]["identifier"] == "dead-letter"


@pytest.mark.parametrize("change", [{"name": "other/dead-letter"}, {"version": ""}, {"repository": {"url": audit.REPO + "-evil"}}, {"packages": []}, {"packages": ["bad"]}])
def test_registry_schema_or_identity_mismatch_is_not_a_match(change):
    document = registry()
    document["server"].update(change)
    with pytest.raises(audit.AuditError):
        audit.registry_result(document)


@pytest.mark.parametrize("value", [audit.REPO, audit.REPO.lower() + "/", audit.REPO + ".git"])
def test_exact_repository_normalization(value):
    assert audit.same_repo(value)


@pytest.mark.parametrize("value", [None, "http://github.com/BigCactusLabs/dead-letter", audit.REPO + "/issues", audit.REPO + "-fake", audit.REPO + "?ref=foo", audit.REPO + "#readme", "https://github.com.evil/BigCactusLabs/dead-letter", "https://user@github.com/BigCactusLabs/dead-letter", "https://github.com:bad/BigCactusLabs/dead-letter"])
def test_namesakes_and_noncanonical_links_do_not_match(value):
    assert not audit.same_repo(value)


def test_cline_requires_exact_repo_not_display_name_and_only_checks_mcp():
    entries = [{"id": "dead-letter", "type": "mcp", "repo": "https://github.com/other/dead-letter"}, {"id": "dl-skill", "type": "skill", "repo": audit.REPO}]
    assert audit.cline_result(catalog(entries))["status"] == "not_in_snapshot"
    entries.append({"id": "renamed-entry", "type": "mcp", "repo": audit.REPO})
    result = audit.cline_result(catalog(entries))
    assert result["status"] == "present" and result["matches"][0]["id"] == "renamed-entry"


@pytest.mark.parametrize("document", [{}, {"version": 1, "entries": []}, {"version": 2, "entries": [], "counts": {"total": 0}}, {"version": 1, "entries": [], "counts": {"total": 1}}, {"version": 1, "entries": [], "counts": {"total": False}}, catalog([None]), catalog([{"id": "x", "type": "mcp"}] * 2)])
def test_partial_invalid_catalogs_are_unknown_not_absent(document):
    with pytest.raises(audit.AuditError):
        audit.cline_result(document)


def readme(extra=""):
    return "# Awesome MCP Servers\n" + "\n" * 100 + "## Email\n" + extra


def test_awesome_reads_link_targets_not_unrelated_mentions_or_fences():
    text = readme(f"A mention: {audit.REPO}\n```markdown\n- [example]({audit.REPO})\n```\n- [namesake]({audit.REPO}-fake)\n")
    assert audit.awesome_result(text)["status"] == "not_in_snapshot"
    result = audit.awesome_result(text + f"- [BigCactusLabs/dead-letter]({audit.REPO}) - Local email.\n")
    assert result["status"] == "present" and result["matches"][0]["category"] == "Email"


@pytest.mark.parametrize("text", ["<html>Access denied</html>", "# Awesome MCP Servers\ntruncated", readme("```markdown\nunfinished")])
def test_unrecognized_or_truncated_readme_never_means_absent(text):
    with pytest.raises(audit.AuditError):
        audit.awesome_result(text)


@pytest.mark.parametrize("body", [b"null", b"[]", b"<html>login</html>", b"\xff"])
def test_invalid_json(body):
    with pytest.raises(audit.AuditError):
        audit.object_json(body)


@pytest.mark.parametrize("url", ["http://api.github.com/repos/x/y", "https://evil.example/", "https://token@api.github.com/repos/x/y", "https://api.github.com:444/repos/x/y"])
def test_fetch_rejects_unreviewed_destinations(url):
    with pytest.raises(audit.AuditError):
        audit.fetch(url)


def test_redirect_is_never_followed():
    assert audit.NoRedirects().redirect_request(None, None, 302, "", {}, "https://evil.example") is None


@pytest.mark.parametrize("status,expected_attempts", [(404, 1), (401, 1), (302, 1), (429, 2), (503, 2)])
def test_http_errors_bounded_and_never_report_absence(monkeypatch, status, expected_attempts):
    calls = []
    def open_request(*args, **kwargs):
        calls.append(1)
        raise HTTPError(audit.REGISTRY, status, "no body", {}, None)
    monkeypatch.setattr(audit, "build_opener", lambda *a: SimpleNamespace(open=open_request))
    monkeypatch.setattr(audit.time, "sleep", lambda _: None)
    with pytest.raises(audit.AuditError, match=f"HTTP {status}"):
        audit.fetch(audit.REGISTRY)
    assert len(calls) == expected_attempts


def test_response_has_bounded_read_and_content_receipt(monkeypatch):
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def geturl(self): return audit.REGISTRY
        def read(self, count):
            assert count == audit.MAX_BYTES + 1
            return b'{}'
    def open_request(request, **kwargs):
        assert not request.has_header("Authorization")
        assert kwargs["timeout"] == 20
        return Response()
    monkeypatch.setattr(audit, "build_opener", lambda *a: SimpleNamespace(open=open_request))
    body, receipt = audit.fetch(audit.REGISTRY)
    assert receipt["bytes"] == 2 and len(receipt["sha256"]) == 64
    assert receipt["url"] == audit.REGISTRY and body == b'{}'


def test_audit_preserves_partial_success_and_uses_immutable_readme(monkeypatch):
    seen = []
    def fetch(url):
        seen.append(url)
        if url == audit.REGISTRY:
            raise audit.AuditError("HTTP 429")
        if url == audit.CLINE:
            body = json.dumps(catalog([])).encode()
        elif "api.github.com" in url:
            body = json.dumps({"sha": "a" * 40}).encode()
        else:
            assert "/" + "a" * 40 + "/README.md" in url
            body = readme(f"- [dead-letter]({audit.REPO})\n").encode()
        return body, {"url": url, "sha256": "b" * 64}
    monkeypatch.setattr(audit, "fetch", fetch)
    result = audit.audit()
    assert [r["status"] for r in result["results"]] == ["unknown", "not_in_snapshot", "present"]
    assert result["results"][0]["reason"] == "HTTP 429"
    assert len(seen) == 4 and len(result["results"][2]["evidence"]) == 2


@pytest.mark.parametrize("title", ["# Awesome MCP servers", '# <img src="logo.svg"> Awesome MCP Servers', '<h1 align="center">Awesome MCP Servers</h1>'])
def test_awesome_recognizes_decorated_or_html_titles(title):
    text = readme(f"- [dead-letter]({audit.REPO})\n").replace("# Awesome MCP Servers", title)
    assert audit.awesome_result(text)["status"] == "present"
