"""Test redirect validation offline; the live check runs in docs CI."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import quote

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = {
    "command": "uvx",
    "args": ["--python", "3.12", "--from", "dead-letter[mcp]", "dead-letter-mcp"],
}
LOCATION = "vscode:mcp/install?" + quote(json.dumps({"name": "dead-letter", **LAUNCHER}))


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("check_install_redirect", ROOT / "scripts/check_install_redirect.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_exact_redirect_payload(module):
    module.validate_location(LOCATION, LAUNCHER)


@pytest.mark.parametrize("location", [
    "", "https://example.invalid/install", "cursor:mcp/install?{}",
    "vscode://other/mcp/install?{}", "vscode:mcp/delete?{}",
    LOCATION + "#fragment", "vscode:mcp/install?not-json",
    "vscode:mcp/install?%257B%257D", "vscode:mcp/install?{}",
    "vscode:mcp/install?" + quote(json.dumps({"name": "dead-letter", "command": "sh", "args": ["-c", "changed"]})),
])
def test_changed_or_invalid_destination_is_rejected(module, location):
    with pytest.raises(ValueError):
        module.validate_location(location, LAUNCHER)


def configure(module, monkeypatch, outcomes):
    requests, sleeps = [], []
    outcomes = iter(outcomes)

    def open_request(request, **kwargs):
        requests.append((request, kwargs))
        raise next(outcomes)

    monkeypatch.setattr(module, "build_opener", lambda *args: SimpleNamespace(open=open_request))
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    return requests, sleeps


def http(status, location=LOCATION):
    return HTTPError("https://vscode.dev/redirect", status, "test", {"Location": location}, None)


def test_live_response_headers_are_checked_without_following(module, monkeypatch):
    requests, sleeps = configure(module, monkeypatch, [http(302)])
    module.check(LAUNCHER)
    assert len(requests) == 1 and sleeps == []
    assert requests[0][1]["timeout"] == 20
    assert requests[0][0].full_url.startswith("https://vscode.dev/redirect?")
    assert module.NoRedirect().redirect_request(None, None, 302, None, {}, LOCATION) is None


def test_rate_limit_and_network_retries_are_bounded(module, monkeypatch):
    requests, sleeps = configure(module, monkeypatch, [http(429), URLError("transient"), http(302)])
    module.check(LAUNCHER)
    assert len(requests) == 3 and sleeps == [2, 4]


@pytest.mark.parametrize("status", [200, 401, 403, 404])
def test_other_http_status_is_not_accepted(module, monkeypatch, status):
    requests, sleeps = configure(module, monkeypatch, [http(status)])
    with pytest.raises(ValueError, match="received HTTP"):
        module.check(LAUNCHER)
    assert len(requests) == 1 and sleeps == []


def test_bad_redirect_is_not_retried_or_treated_as_success(module, monkeypatch):
    requests, sleeps = configure(module, monkeypatch, [http(302, "https://example.invalid")])
    with pytest.raises(ValueError):
        module.check(LAUNCHER)
    assert len(requests) == 1 and sleeps == []


def test_repeated_throttling_fails(module, monkeypatch):
    requests, sleeps = configure(module, monkeypatch, [http(429) for _ in range(3)])
    with pytest.raises(ValueError, match="HTTP 429"):
        module.check(LAUNCHER)
    assert len(requests) == 3 and sleeps == [2, 4]
