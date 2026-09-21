"""Check the live HTTPS-to-VS-Code install redirect without opening an app.

A generic HTTP link checker cannot follow the final custom-protocol URL.
Validate its exact command payload instead; no software is installed here.
"""

from __future__ import annotations

import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from generate_client_installs import ROOT, install_links, public_launcher

REDIRECTS = {301, 302, 303, 307, 308}
RETRYABLE = {429, 500, 502, 503, 504}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_location(location: str, launcher: dict) -> None:
    """Reject other apps, endpoints, commands, and altered configuration."""
    parts = urlsplit(location)
    if (parts.scheme, parts.netloc, parts.path, parts.fragment) != (
        "vscode", "", "mcp/install", "",
    ):
        raise ValueError("install redirect does not target vscode:mcp/install")
    value = json.loads(unquote(parts.query))
    if value != {"name": "dead-letter", **launcher}:
        raise ValueError("install redirect changed the canonical command payload")


def check(launcher: dict) -> None:
    opener = build_opener(NoRedirect())
    request = Request(
        install_links(launcher)["vscode"],
        headers={"User-Agent": "dead-letter-install-link-check/1"},
    )
    for attempt in range(3):
        try:
            try:
                response = opener.open(request, timeout=20)
            except HTTPError as error:
                # urllib also rejects a non-HTTP Location before calling
                # redirect_request. Its HTTPError still contains the headers.
                response = error
            with response:
                status = response.code
                location = response.headers.get("Location", "")
            if status in REDIRECTS:
                validate_location(location, launcher)
                return
            if status not in RETRYABLE or attempt == 2:
                raise ValueError(f"expected an app redirect, received HTTP {status}")
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** (attempt + 1))
    raise AssertionError("retry loop exhausted without a result")


def main() -> int:
    try:
        server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
        check(public_launcher(server))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"FAIL: install redirect: {error}", file=sys.stderr)
        return 1
    print("PASS: live VS Code HTTPS redirect preserves the exact MCP command; no app opened")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
