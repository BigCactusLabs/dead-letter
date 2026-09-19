"""Read public discovery metadata; never publish, authenticate, or install.

A result describes this exact endpoint/snapshot, not every client or directory.
HTTP errors and malformed/incomplete responses are unknown, never absence.
Run explicitly or in PR CI; no recurring crawler or private-email telemetry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

NAME = "io.github.BigCactusLabs/dead-letter"
REPO = "https://github.com/BigCactusLabs/dead-letter"
REGISTRY = "https://registry.modelcontextprotocol.io/v0.1/servers/" + quote(NAME, safe="") + "/versions/latest"
CLINE = "https://cline.github.io/marketplace/catalog.json"
AWESOME = "punkpeye/awesome-mcp-servers"
MAX_BYTES = 8 * 1024 * 1024
ALLOWED_HOSTS = frozenset({"registry.modelcontextprotocol.io", "cline.github.io", "api.github.com", "raw.githubusercontent.com"})


class AuditError(ValueError):
    """Public evidence was unavailable or ambiguous."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url: str) -> tuple[bytes, dict]:
    """Bounded public GET. Do not inherit tokens, follow redirects, or log bodies."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise AuditError("unsupported public evidence URL")
    opener = build_opener(NoRedirects())
    for attempt in range(2):
        try:
            request = Request(url, headers={"User-Agent": "dead-letter-discovery-audit/1", "Accept": "application/json, text/plain"})
            with opener.open(request, timeout=20) as response:
                if response.status != 200:
                    raise AuditError(f"HTTP {response.status}")
                if response.geturl() != url:
                    raise AuditError("unexpected response URL")
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise AuditError("response exceeds size limit")
                return body, {
                    "url": url,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "bytes": len(body),
                }
        except HTTPError as error:
            status = error.code
            error.close()
            if attempt == 0 and status in {429, 500, 502, 503, 504}:
                time.sleep(1)
                continue
            # Even a 404 may be routing/version drift. Never infer absence.
            raise AuditError(f"HTTP {status}") from None
        except (URLError, TimeoutError, OSError):
            if attempt == 0:
                time.sleep(1)
                continue
            raise AuditError("public endpoint unavailable") from None
    raise AuditError("public endpoint unavailable")


def object_json(body: bytes) -> dict:
    try:
        result = json.loads(body)
    except (ValueError, UnicodeError):
        raise AuditError("invalid JSON") from None
    if not isinstance(result, dict):
        raise AuditError("expected JSON object")
    return result


def same_repo(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        url = urlsplit(value)
        return (
            url.scheme == "https" and url.hostname == "github.com"
            and url.port in (None, 443) and not url.username and not url.password
            and not url.query and not url.fragment
            and url.path.rstrip("/").removesuffix(".git").casefold() == "/bigcactuslabs/dead-letter"
        )
    except ValueError:
        return False


def registry_result(document: dict) -> dict:
    server = document.get("server")
    if not isinstance(server, dict) or server.get("name") != NAME:
        raise AuditError("registry identity missing or mismatched")
    repo = server.get("repository")
    if not isinstance(repo, dict) or not same_repo(repo.get("url")):
        raise AuditError("registry repository missing or mismatched")
    version = server.get("version")
    if not isinstance(version, str) or not version or len(version) > 128:
        raise AuditError("registry version missing or invalid")
    packages = server.get("packages")
    if not isinstance(packages, list) or not packages or any(not isinstance(p, dict) for p in packages):
        raise AuditError("registry packages missing or invalid")
    for package in packages:
        if not isinstance(package.get("registryType"), str) or not isinstance(package.get("identifier"), str):
            raise AuditError("invalid registry package identity")
    meta = document.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {})
    if not isinstance(meta, dict):
        raise AuditError("invalid registry status metadata")
    return {
        "status": "present", "identity": NAME, "version": version,
        "registry_status": meta.get("status", "not_reported"),
        "packages": [{key: p[key] for key in ("registryType", "identifier", "version") if key in p} for p in packages],
        "scope": "exact Official Registry latest-version endpoint; not downstream admission or runtime validation",
    }


def cline_result(document: dict) -> dict:
    entries = document.get("entries")
    counts = document.get("counts")
    if document.get("version") != 1 or not isinstance(entries, list) or not isinstance(counts, dict):
        raise AuditError("unrecognized Cline catalog")
    if type(counts.get("total")) is not int or counts["total"] != len(entries):
        raise AuditError("incomplete Cline catalog")
    seen = set()
    matches = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or entry.get("type") not in {"mcp", "plugin", "skill"}:
            raise AuditError("invalid Cline catalog entry")
        identity = (entry["type"], entry["id"])
        if identity in seen:
            raise AuditError("duplicate Cline catalog identity")
        seen.add(identity)
        if entry["type"] == "mcp" and same_repo(entry.get("repo")):
            matches.append({"id": entry["id"], "type": "mcp", "repo": REPO})
    return {
        "status": "present" if matches else "not_in_snapshot",
        "matches": matches, "entries_checked": len(entries),
        "generated_at": document.get("generatedAt"),
        "scope": "complete published Cline marketplace catalog response; not legacy marketplace or GUI discovery",
    }


def awesome_result(text: str) -> dict:
    # Reject HTML challenges and truncated/unrecognized content before reporting
    # a miss. Snapshot hash/revision establish the exact README checked.
    heading = re.search(r"(?im)^(?:#\s+|<h1\b)[^\n]*awesome mcp servers\b", text)
    if heading is None or len(text.splitlines()) < 100:
        first_line = text.splitlines()[0][:200] if text else ""
        raise AuditError(f"unrecognized or incomplete Awesome README (lines={len(text.splitlines())}, first_line={first_line!r})")
    matches = []
    category = ""
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        fence_match = re.match(r"(`{3,}|~{3,})", stripped)
        if fence_match:
            marker = fence_match[1]
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence:
            continue
        if line.startswith("##"):
            category = line.lstrip("# ")[:160]
        listing = re.match(r"^\s*[-*]\s+\[[^\]]+\]\((https://[^\s)]+)\)", line)
        if listing and same_repo(listing[1]):
            matches.append({"line": number, "category": category, "repo": REPO})
    if fence:
        raise AuditError("unterminated README code fence")
    return {
        "status": "present" if matches else "not_in_snapshot", "matches": matches,
        "scope": "exact repository links in the complete upstream README; not open submissions or other lists",
    }


def audit() -> dict:
    results = []
    for surface, url, evaluate in (
        ("official-registry", REGISTRY, registry_result),
        ("cline-marketplace", CLINE, cline_result),
        ("awesome-mcp-servers", f"https://api.github.com/repos/{AWESOME}/commits/main", None),
    ):
        evidence = []
        try:
            body, receipt = fetch(url)
            evidence.append(receipt)
            document = object_json(body)
            if evaluate:
                result = evaluate(document)
            else:
                sha = document.get("sha")
                if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
                    raise AuditError("missing immutable README source revision")
                body, receipt = fetch(f"https://raw.githubusercontent.com/{AWESOME}/{sha}/README.md")
                evidence.append(receipt)
                result = awesome_result(body.decode("utf-8"))
                result["revision"] = sha
            results.append({"surface": surface, **result, "evidence": evidence})
        except (AuditError, UnicodeError, AttributeError, TypeError) as error:
            results.append({"surface": surface, "status": "unknown", "reason": str(error) if isinstance(error, AuditError) else "unrecognized response schema", "attempted_url": url, "evidence": evidence})
    return {"schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(), "identity": NAME, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="new JSON evidence file; never overwrites existing evidence")
    args = parser.parse_args()
    report = audit()
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(rendered)
    print(rendered, end="")
    # Visibility is observation, not the application's test gate. CI separately
    # tests this parser. An absent/unknown catalog does not break conversion CI.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
