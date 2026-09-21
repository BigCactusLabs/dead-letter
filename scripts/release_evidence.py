"""Bounded, GET-only release evidence and shared metadata parsers (stdlib only)."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPO = "BigCactusLabs/dead-letter"
TAP = "BigCactusLabs/homebrew-tap"
MARKETPLACE = "BigCactusLabs/bigcactuslabs-plugins"
API = "https://api.github.com/repos/"
SERVER = "io.github.BigCactusLabs/dead-letter"
IMAGE = "ghcr.io/bigcactuslabs/dead-letter"
HOSTS = frozenset({"api.github.com", "github.com", "release-assets.githubusercontent.com",
                   "pypi.org", "files.pythonhosted.org", "ghcr.io", "registry.modelcontextprotocol.io"})
MAX_BYTES = 64 * 1024 * 1024
HEX = re.compile(r"[0-9a-f]{64}")
SEMVER = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
RESOURCE = re.compile(r'^  resource "([A-Za-z0-9_.-]+)" do\n(.*?)^  end\n', re.M | re.S)
OPTIONAL = frozenset({"mcp", "mcp-types", "fastapi", "uvicorn", "watchfiles", "httpx", "tiktoken",
                      "pytest", "pytest-cov", "ruff", "pyright"})


class Unavailable(ValueError):
    """Evidence could not be read or interpreted; this is not absence."""


class Missing(Unavailable):
    """An authoritative exact resource returned HTTP 404."""


class Conflict(ValueError):
    """Valid evidence contradicts the requested release contract."""


def stable(value: str) -> str:
    if not isinstance(value, str) or not SEMVER.fullmatch(value):
        raise ValueError("expected a stable X.Y.Z version")
    return value


def digest(value: str) -> str:
    if not isinstance(value, str) or not HEX.fullmatch(value) or value == "0" * 64:
        raise Conflict("missing, zero, or invalid SHA-256")
    return value


def safe_url(url: str, hosts=HOSTS) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username
            or parsed.password or parsed.port not in (None, 443) or parsed.fragment
            or any(c.isspace() or ord(c) < 32 or c in '\\"<>' for c in url)):
        raise Unavailable("unsupported evidence URL")
    return url


class SafeRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl)
        # Release downloads redirect to GitHub's asset host. API requests with
        # credentials never redirect, even to another allowlisted host.
        if req.has_header("Authorization"):
            raise Unavailable("authenticated evidence request redirected")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    """No retries, disk cache, subprocesses, or credential forwarding to assets."""
    def __init__(self, token: str | None = None):
        self.token = token if token is not None else os.environ.get("GH_TOKEN", os.environ.get("GITHUB_TOKEN", ""))
        self.opener = build_opener(SafeRedirects())
        self.cache: dict[tuple, tuple[bytes, dict]] = {}
        self.errors: dict[tuple, Unavailable] = {}

    def get(self, url: str, *, accept: str = "application/json", bearer: str = "") -> tuple[bytes, dict]:
        safe_url(url)
        host = urlsplit(url).hostname
        if bearer and host != "ghcr.io":
            raise Unavailable("registry credentials are restricted to GHCR")
        key = (url, accept, bool(bearer))
        if key in self.errors:
            raise self.errors[key]
        if key in self.cache:
            return self.cache[key]
        headers = {"User-Agent": "dead-letter-release-status/1", "Accept": accept}
        if host == "api.github.com" and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        elif bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        try:
            with self.opener.open(Request(url, headers=headers, method="GET"), timeout=15) as response:
                if response.status != 200:
                    raise Unavailable(f"unexpected HTTP {response.status}")
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise Unavailable("evidence response exceeds size limit")
                result = (body, {k.lower(): v for k, v in response.headers.items()})
        except HTTPError as exc:
            code = exc.code
            exc.close()
            failure = Missing("exact resource returned HTTP 404") if code == 404 else Unavailable(f"HTTP {code}; not evidence of absence")
            self.errors[key] = failure
            raise failure from None
        except (URLError, OSError):
            failure = Unavailable("network request failed; not evidence of absence")
            self.errors[key] = failure
            raise failure from None
        self.cache[key] = result
        return result

    def json(self, url: str) -> dict:
        return object_json(self.get(url)[0])

    def ref(self, repo: str, ref: str) -> str:
        obj = self.json(f"{API}{repo}/git/ref/{quote(ref, safe='/')}")["object"]
        for _ in range(5):
            sha = obj["sha"]
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise Unavailable("invalid Git object SHA")
            if obj["type"] == "commit":
                return sha
            if obj["type"] != "tag":
                raise Unavailable("Git ref does not resolve to a commit")
            obj = self.json(f"{API}{repo}/git/tags/{sha}")["object"]
        raise Unavailable("annotated tag nesting exceeds limit")

    def file(self, repo: str, path: str, sha: str) -> str:
        document = self.json(f"{API}{repo}/contents/{quote(path, safe='/')}?ref={sha}")
        if document.get("type") != "file" or document.get("encoding") != "base64":
            raise Unavailable("expected a regular Git file")
        try:
            body = base64.b64decode("".join(document["content"].split()), validate=True)
        except (ValueError, TypeError):
            raise Unavailable("invalid Git file encoding") from None
        actual = hashlib.sha1(f"blob {len(body)}\0".encode() + body, usedforsecurity=False).hexdigest()
        if document.get("sha") != actual or document.get("size") != len(body):
            raise Conflict("Git file content does not match its blob evidence")
        return body.decode("utf-8")


def object_json(body: bytes | str) -> dict:
    try:
        obj = json.loads(body)
    except (ValueError, UnicodeError):
        raise Unavailable("invalid JSON evidence") from None
    if not isinstance(obj, dict):
        raise Unavailable("expected JSON object evidence")
    return obj


def read_checksums(path: Path, version: str) -> dict[str, str]:
    stable(version)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8192:
        raise ValueError("build checksums must be a bounded, regular file")
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match or match[2] in values:
            raise ValueError("invalid or duplicate recorded build checksum")
        values[match[2]] = digest(match[1])
    wheel = rf"dead_letter-{re.escape(version)}-py3-none-any\.whl"
    sdist = rf"dead_letter-{re.escape(version)}\.tar\.gz"
    if len(values) != 2 or sum(bool(re.fullmatch(wheel, n)) for n in values) != 1 or sum(bool(re.fullmatch(sdist, n)) for n in values) != 1:
        raise ValueError("checksums must identify the target version's wheel/sdist pair")
    return values


def pypi_files(document: dict, version: str, name: str = "dead-letter") -> list[dict]:
    info = document["info"]
    if normalized(info["name"]) != normalized(name) or info["version"] != version:
        raise Conflict("PyPI name/version does not match the exact request")
    files = document["urls"]
    if not isinstance(files, list) or any(not isinstance(f, dict) for f in files):
        raise Unavailable("invalid PyPI file list")
    seen = set()
    for item in files:
        filename = item["filename"]
        if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9_.+-]+", filename) or filename in seen:
            raise Conflict("unsafe or duplicate PyPI filename")
        seen.add(filename)
        safe_url(item["url"], {"files.pythonhosted.org"})
        if Path(urlsplit(item["url"]).path).name != filename:
            raise Conflict("PyPI URL and filename disagree")
        if type(item.get("yanked")) is not bool:
            raise Unavailable("PyPI yanked state is not explicitly reported")
        digest(item["digests"]["sha256"])
    return files


def released_sdist(client: Client, version: str, checksums: dict[str, str]) -> dict:
    """Preparation requires the whole published pair to match original evidence."""
    files = pypi_files(client.json(f"https://pypi.org/pypi/dead-letter/{stable(version)}/json"), version)
    if {f["filename"] for f in files} != set(checksums):
        raise Conflict("published wheel/sdist set differs from recorded build")
    for item in files:
        if item.get("yanked") or item["digests"]["sha256"] != checksums[item["filename"]]:
            raise Conflict("published files are yanked or differ from recorded build")
    matches = [item for item in files if item.get("packagetype") == "sdist" and item["filename"].endswith(".tar.gz")]
    if len(matches) != 1:
        raise Conflict("expected exactly one released sdist")
    return matches[0]


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def formula(text: str) -> dict:
    """Read this tap's simple literal Ruby layout without ever executing Ruby."""
    if not text.startswith("class DeadLetter < Formula\n"):
        raise Unavailable("unrecognized formula class/layout")
    def field(body, key, indent):
        matches = re.findall(rf'^{indent}{key} "([^"\n]+)"$', body, re.M)
        if len(matches) != 1 or "#{" in matches[0]:
            raise Unavailable(f"formula needs one literal {key}")
        return matches[0]
    url = safe_url(field(text, "url", "  "), {"files.pythonhosted.org"})
    match = re.fullmatch(r"dead[-_]letter-(\d+\.\d+\.\d+)\.tar\.gz", Path(urlsplit(url).path).name)
    if not match:
        raise Unavailable("formula URL does not identify a stable dead-letter sdist")
    version = stable(match[1])
    explicit = re.findall(r'^  version "([^"\n]+)"$', text, re.M)
    if explicit and explicit != [version]:
        raise Conflict("formula explicit version disagrees with sdist URL")
    resources = []
    for block in RESOURCE.finditer(text):
        resources.append({"name": normalized(block[1]), "url": safe_url(field(block[2], "url", "    "), {"files.pythonhosted.org"}),
                          "sha256": digest(field(block[2], "sha256", "    "))})
    if len(resources) != len(re.findall(r"^\s*resource\s", text, re.M)) or not resources:
        raise Unavailable("unrecognized or empty formula resource layout")
    names = [r["name"] for r in resources]
    if len(set(names)) != len(names) or set(names) & (OPTIONAL | {"dead-letter"}):
        raise Conflict("duplicate resources or non-core packages in formula")
    if 'rm bin/"dead-letter-mcp"' not in text or 'rm bin/"dead-letter-ui"' not in text:
        raise Conflict("formula must remove optional MCP/UI entrypoints")
    return {"version": version, "url": url, "sha256": digest(field(text, "sha256", "  ")), "resources": resources}
