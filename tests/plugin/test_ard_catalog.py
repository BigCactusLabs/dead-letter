"""Structure tests for the Agentic Resource Discovery (ARD) catalog.

`.well-known/ard.json` advertises the MCP server and the portable Agent Skill
to ARD-aware crawlers. Both entries carry a `version`, which makes this file a
release version sync point alongside `server.json` (see AGENTS.md).
"""

import json
from pathlib import Path
import re

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ARD_PATH = REPO_ROOT / ".well-known" / "ard.json"
SERVER_JSON_PATH = REPO_ROOT / "server.json"

# urn:air:<domain>:<owner>:<repo>:<resource>
IDENTIFIER_PATTERN = re.compile(r"^urn:air:[^:]+:[^:]+:[^:]+:[^:]+$")

# The four discovery intents this catalog is meant to answer (issue #109).
REQUIRED_INTENTS = [
    "convert exported email to markdown",
    "read an eml file",
    "prepare email for RAG",
    "build a local email archive",
]


def _load_entries() -> list[dict]:
    catalog = json.loads(ARD_PATH.read_text(encoding="utf-8"))
    entries = catalog["entries"]
    assert isinstance(entries, list), "`entries` must be a list"
    return entries


def _entry_by_suffix(suffix: str) -> dict:
    matches = [e for e in _load_entries() if e["identifier"].endswith(suffix)]
    assert len(matches) == 1, f"expected exactly one {suffix!r} entry"
    return matches[0]


def test_catalog_parses_and_has_two_entries():
    entries = _load_entries()
    assert len(entries) == 2, (
        "the catalog advertises exactly two resources: the MCP server and the "
        "portable Agent Skill."
    )


@pytest.mark.parametrize("index", [0, 1])
def test_entry_has_required_fields(index):
    entry = _load_entries()[index]
    assert IDENTIFIER_PATTERN.match(entry["identifier"]), (
        f"identifier {entry['identifier']!r} must be a urn:air: identifier"
    )
    assert entry["displayName"], "displayName is required"
    assert entry["type"], "type is required"
    assert entry["description"], "description is required"
    assert ("url" in entry) != ("data" in entry), (
        "an ARD entry carries exactly one of `url` or `data`"
    )


@pytest.mark.parametrize("index", [0, 1])
def test_entry_has_between_two_and_five_representative_queries(index):
    queries = _load_entries()[index]["representativeQueries"]
    assert isinstance(queries, list)
    assert 2 <= len(queries) <= 5, (
        "ARD allows 2 to 5 representativeQueries per entry"
    )
    assert all(q.strip() for q in queries), "representativeQueries must be non-empty"


def test_skill_entry_points_at_a_file_that_exists():
    entry = _entry_by_suffix(":skill")
    url = entry["url"]
    assert url.endswith("skills/dead-letter/SKILL.md"), (
        "the skill entry must point at the portable skill file"
    )
    assert (REPO_ROOT / "skills" / "dead-letter" / "SKILL.md").is_file(), (
        "the skill entry advertises a file that is not in the repository"
    )


def test_mcp_entry_points_at_server_json():
    entry = _entry_by_suffix(":mcp-server")
    assert entry["url"].endswith("server.json"), (
        "the MCP entry must point at the registry server card"
    )


@pytest.mark.parametrize("index", [0, 1])
def test_entry_version_matches_server_json(index):
    """ard.json is a version sync point; a release bumps both entries."""
    expected = json.loads(SERVER_JSON_PATH.read_text(encoding="utf-8"))["version"]
    entry = _load_entries()[index]
    assert entry["version"] == expected, (
        f"{entry['identifier']} is at {entry['version']} but server.json is at "
        f"{expected}. Bump .well-known/ard.json during release prep "
        "(docs/reference/publishing.md)."
    )


@pytest.mark.parametrize("intent", REQUIRED_INTENTS)
def test_required_intents_are_advertised(intent):
    queries = [
        q.lower() for entry in _load_entries() for q in entry["representativeQueries"]
    ]
    assert any(intent.lower() in q for q in queries), (
        f"no representativeQuery covers the {intent!r} intent"
    )
