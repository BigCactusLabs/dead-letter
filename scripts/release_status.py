"""Read-only cross-channel release reconciliation. Never repairs or retries."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from urllib.parse import quote

from release_evidence import (API, IMAGE, MARKETPLACE, REPO, SERVER, TAP, Client, Conflict,
                              Missing, Unavailable, digest, formula, object_json, pypi_files, stable)

STATES = ("verified", "missing", "deferred", "conflicting", "unable-to-verify")
REGISTRY = "https://registry.modelcontextprotocol.io/v0.1/servers/" + quote(SERVER, safe="") + "/versions/"
MANIFEST_ACCEPT = "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json"


def result(status: str, detail: str, action: str = "None.", **evidence) -> dict:
    return {"status": status, "detail": detail, "next_action": action, "evidence": evidence}


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise Unavailable("publication timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def pypi(client: Client, version: str, recorded: dict | None, now: datetime) -> dict:
    files = pypi_files(client.json(f"https://pypi.org/pypi/dead-letter/{version}/json"), version)
    expected = set(recorded or {f"dead_letter-{version}-py3-none-any.whl", f"dead_letter-{version}.tar.gz"})
    by_name = {f["filename"]: f for f in files}
    extra = set(by_name) - expected
    if extra:
        raise Conflict("PyPI contains distributions outside the recorded build pair")
    if any(f.get("yanked") for f in files):
        raise Conflict("PyPI contains a yanked distribution; do not treat it as install-ready")
    if recorded and any(f["digests"]["sha256"] != recorded[f["filename"]] for f in files):
        raise Conflict("PyPI SHA-256 differs from original build evidence; never overwrite/rebuild to match")
    missing = sorted(expected - set(by_name))
    if missing:
        # PyPI currently exposes no authoritative closed-release flag. Age is a
        # conservative recovery rule, not a statement that an upload will work.
        try:
            times = [parse_time(f["upload_time_iso_8601"]) for f in files]
        except (ValueError, KeyError, TypeError):
            times = []
        old = bool(times) and now - min(times) > timedelta(days=14)
        return result("missing", "PyPI distribution files are missing.",
                      "Needs new version: the partial PyPI release is older than 14 days." if old else
                      "Inspect the prior upload before recovery; do not re-upload existing files. Upload eligibility is not verified.",
                      missing_files=missing, needs_new_version=old, upload_window_known=bool(times))
    if not recorded:
        return result("unable-to-verify", "PyPI files exist, but original build checksums were not supplied.",
                      "Recover SHA256SUMS from the original build artifact/ledger; rerun with --checksums. Do not derive it from PyPI.",
                      files={n: f["digests"]["sha256"] for n, f in by_name.items()})
    return result("verified", "PyPI file set and advertised SHA-256 match the recorded build.",
                  files=dict(recorded), scope="metadata/checksum reconciliation, not a fresh install")


def package_map(server: dict, version: str) -> dict:
    if server.get("name") != SERVER or server.get("version") != version:
        raise Conflict("registry manifest identity/version mismatch")
    packages = server["packages"]
    if not isinstance(packages, list) or any(not isinstance(p, dict) for p in packages):
        raise Unavailable("invalid registry package list")
    values = {p["registryType"]: p for p in packages}
    if len(values) != len(packages) or set(values) != {"pypi", "mcpb", "oci"}:
        raise Conflict("expected one PyPI, MCPB, and OCI package")
    python = values["pypi"]
    if python.get("identifier") != "dead-letter" or python.get("version") != version:
        raise Conflict("registry PyPI package mismatch")
    pins = [a.get("value") for a in python.get("runtimeArguments", []) if a.get("name") == "--from"]
    if pins != [f"dead-letter[mcp]=={version}"]:
        raise Conflict("registry PyPI runtime is not the exact release")
    return values


def oci_digest(package: dict) -> str:
    identifier = package.get("identifier", "")
    match = re.fullmatch(re.escape(IMAGE) + r"@sha256:([0-9a-f]{64})", identifier)
    if not match:
        raise Conflict("registry OCI identifier is not the exact repository digest")
    return "sha256:" + digest(match[1])


def github_release(client: Client, version: str) -> tuple[dict, dict | None]:
    release = client.json(f"{API}{REPO}/releases/tags/v{version}")
    if any(type(release.get(key)) is not bool for key in ("draft", "prerelease")):
        raise Unavailable("GitHub release publication state is not explicitly reported")
    if release.get("tag_name") != f"v{version}" or release.get("prerelease"):
        raise Conflict("GitHub release tag/prerelease state does not match the stable version")
    if release.get("draft"):
        return result("missing", "A draft exists but is not published.", "Do not publish automatically; inspect release preparation first."), None
    assets = release["assets"]
    if not isinstance(assets, list) or any(not isinstance(a, dict) for a in assets):
        raise Unavailable("invalid release asset inventory")
    names = [a["name"] for a in assets]
    if len(set(names)) != len(names):
        raise Conflict("duplicate GitHub release assets")
    bundle = f"dead-letter-mcp-{version}.mcpb"
    manifest = f"dead-letter-server-{version}.json"
    required = (bundle, bundle + ".sha256", manifest, manifest + ".sha256")
    missing = sorted(set(required) - set(names))
    if missing:
        return result("missing", "Published GitHub release assets are incomplete.",
                      "Needs new version/draft: immutable releases cannot accept missing assets." if release.get("immutable") else
                      "Recover original assets, then use release.py upload-assets after reviewing publishing.md; never --clobber.",
                      missing_assets=missing, immutable=bool(release.get("immutable"))), None
    contents = {}
    for name in required:
        asset = next(a for a in assets if a["name"] == name)
        expected_url = f"https://github.com/{REPO}/releases/download/v{version}/{name}"
        if asset.get("browser_download_url") != expected_url or asset.get("state") != "uploaded":
            raise Conflict("unexpected release asset URL/state")
        body, _ = client.get(expected_url, accept="application/octet-stream")
        if asset.get("size") != len(body):
            raise Conflict("GitHub asset size differs from downloaded bytes")
        contents[name] = body
    hashes = {}
    for name in (bundle, manifest):
        checksum = contents[name + ".sha256"].decode("ascii")
        match = re.fullmatch(r"([0-9a-f]{64})  " + re.escape(name) + r"\n?", checksum)
        if not match or digest(match[1]) != hashlib.sha256(contents[name]).hexdigest():
            raise Conflict("release asset checksum/sidecar mismatch")
        hashes[name] = match[1]
    server = object_json(contents[manifest])
    packages = package_map(server, version)
    mcpb = packages["mcpb"]
    if (mcpb.get("version") != version or mcpb.get("fileSha256") != hashes[bundle]
            or mcpb.get("identifier") != f"https://github.com/{REPO}/releases/download/v{version}/{bundle}"):
        raise Conflict("archived registry manifest does not describe the verified MCPB bytes")
    recorded_digest = oci_digest(packages["oci"])
    return result("verified", "MCPB and resolved registry manifest bytes match their sidecars.",
                  assets=hashes, oci_digest=recorded_digest, immutable=bool(release.get("immutable"))), server


def ghcr(client: Client, expected: str | None, version: str) -> dict:
    url = f"https://ghcr.io/v2/bigcactuslabs/dead-letter/manifests/{version}"
    # GHCR normally challenges anonymous manifest reads. Fetch a pull-only
    # anonymous token from the fixed GHCR endpoint, never an arbitrary realm.
    try:
        token = client.json("https://ghcr.io/token?service=ghcr.io&scope=repository%3Abigcactuslabs%2Fdead-letter%3Apull")["token"]
    except Missing:
        raise Unavailable("GHCR authorization endpoint is unavailable; not evidence of image absence") from None
    if not isinstance(token, str) or not token or len(token) > 16384:
        raise Unavailable("invalid anonymous GHCR pull token")
    body, headers = client.get(url, accept=MANIFEST_ACCEPT, bearer=token)
    observed = "sha256:" + hashlib.sha256(body).hexdigest()
    if headers.get("docker-content-digest") != observed:
        raise Conflict("GHCR digest header disagrees with manifest bytes")
    document = object_json(body)
    if document.get("schemaVersion") != 2 or not isinstance(document.get("manifests"), list):
        raise Unavailable("expected a multi-platform OCI index")
    platforms = {f"{m.get('platform', {}).get('os')}/{m.get('platform', {}).get('architecture')}" for m in document["manifests"]}
    if not {"linux/amd64", "linux/arm64"} <= platforms:
        raise Conflict("GHCR index is missing a supported platform")
    if not expected:
        return result("unable-to-verify", "GHCR tag exists but no recorded OCI digest is available.",
                      "Supply --oci-digest from the release ledger or recover the checksummed registry manifest.", digest=observed)
    if observed != expected:
        raise Conflict("GHCR tag digest differs from the recorded release digest")
    return result("verified", "Anonymous GHCR index read matches the recorded digest and both platform entries.",
                  digest=observed, scope="manifest only; image layers and runtime not re-tested")


def registry(client: Client, version: str, archived: dict | None) -> dict:
    document = client.json(REGISTRY + version)
    server = document["server"]
    packages = package_map(server, version)
    state = document.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {}).get("status")
    if state is None:
        raise Unavailable("registry active status is not reported")
    if state != "active":
        raise Conflict("exact registry record is not active")
    if archived is None:
        return result("unable-to-verify", "Exact registry record exists; verified release manifest is unavailable.",
                      "Recover/verify the archived server manifest before reconciling registry hashes.")
    if packages != package_map(archived, version):
        raise Conflict("exact registry package records differ from the checksummed release manifest")
    return result("verified", "Exact active registry version matches the archived package records.", version=version)


def plugin(client: Client, version: str) -> dict:
    marketplace_sha = client.ref(MARKETPLACE, "heads/main")
    document = object_json(client.file(MARKETPLACE, ".claude-plugin/marketplace.json", marketplace_sha))
    entries = [p for p in document["plugins"] if p.get("name") == "dead-letter"]
    if not entries:
        return result("missing", "Marketplace has no dead-letter entry.", "Review the explicit plugin release procedure.")
    if len(entries) != 1:
        raise Conflict("duplicate marketplace plugin entries")
    entry = entries[0]
    asset_version = stable(entry["version"])
    source = entry["source"]
    if (source.get("source") != "git-subdir" or source.get("url") != f"https://github.com/{REPO}.git"
            or source.get("path") != "plugin" or source.get("ref") != f"plugin-v{asset_version}"):
        raise Conflict("marketplace does not use the expected pinned plugin source")
    try:
        sha = client.ref(REPO, "tags/" + source["ref"])
    except Missing:
        raise Conflict("marketplace references a plugin tag that does not exist") from None
    if source.get("sha") != sha:
        raise Conflict("marketplace SHA disagrees with peeled plugin tag")
    branch_sha = client.ref(REPO, "heads/release")
    if branch_sha != sha:
        raise Conflict("compatibility release branch and marketplace pin disagree")
    manifest = object_json(client.file(REPO, "plugin/.claude-plugin/plugin.json", sha))
    if manifest.get("version") != asset_version:
        raise Conflict("plugin manifest version disagrees with marketplace")
    launcher = object_json(client.file(REPO, "plugin/.mcp.json", sha))
    args = launcher["mcpServers"]["dead-letter"]["args"]
    pins = [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == "--from"]
    if len(pins) != 1 or not isinstance(pins[0], str) or not pins[0].startswith("dead-letter[mcp]=="):
        raise Conflict("plugin runtime is not exactly pinned")
    try:
        pin = stable(pins[0].removeprefix("dead-letter[mcp]=="))
    except ValueError:
        raise Conflict("plugin runtime is not a supported exact stable pin") from None
    try:
        available = pypi_files(client.json(f"https://pypi.org/pypi/dead-letter/{pin}/json"), pin)
    except Missing:
        raise Conflict("plugin exact runtime pin points at a nonexistent package") from None
    if not available or all(f.get("yanked") for f in available):
        raise Conflict("plugin exact runtime pin has no installable distribution")
    status = "verified" if pin == version else "deferred"
    return result(status, "Plugin tag, marketplace, branch, and published runtime pin agree.",
                  "None." if status == "verified" else "Independent runtime adoption is deferred; review a new plugin tag only when adoption is intended.",
                  plugin_version=asset_version, runtime_pin=pin, commit=sha, marketplace_commit=marketplace_sha)


def homebrew(client: Client, version: str, recorded: dict | None) -> dict:
    sha = client.ref(TAP, "heads/main")
    data = formula(client.file(TAP, "Formula/dead-letter.rb", sha))
    current = data["version"]
    try:
        files = pypi_files(client.json(f"https://pypi.org/pypi/dead-letter/{current}/json"), current)
    except Missing:
        raise Conflict("tap formula references a nonexistent PyPI version") from None
    matches = [f for f in files if f.get("packagetype") == "sdist"]
    if len(matches) != 1 or matches[0].get("yanked") or matches[0]["url"] != data["url"] or matches[0]["digests"]["sha256"] != data["sha256"]:
        raise Conflict("tap sdist URL/SHA-256 does not match its published package")
    if current != version:
        return result("deferred", "Tap has a different valid package version; adoption is manual.",
                      "Run release.py homebrew-prepare after recovering the target build checksums; review the tap PR manually.",
                      formula_version=current, tap_commit=sha)
    if not recorded:
        return result("unable-to-verify", "Tap matches PyPI but original build checksums are unavailable.",
                      "Supply --checksums to verify the formula against original build evidence.", formula_version=current, tap_commit=sha)
    if recorded.get(matches[0]["filename"]) != data["sha256"]:
        raise Conflict("tap sdist differs from original build checksum")
    return result("verified", "Core-only tap formula matches the target PyPI sdist and recorded build.",
                  formula_version=current, tap_commit=sha, scope="formula metadata only; brew install/test not rerun")


def guarded(function, *args) -> dict:
    try:
        return function(*args)
    except Missing:
        return result("missing", "Exact channel resource returned HTTP 404.",
                      "Inspect prior publication and the channel recovery runbook; absence does not authorize a retry.")
    except Conflict as exc:
        return result("conflicting", str(exc), "Stop. Recover original evidence or prepare a new release; never replace published bytes or rewind pointers.")
    except (Unavailable, OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        return result("unable-to-verify", "Network, authorization, or malformed/incomplete evidence prevented verification.",
                      "Check endpoint access and evidence, then rerun explicitly; no retry was attempted.",
                      error_type=type(exc).__name__, error=str(exc)[:200])


def collect(version: str, *, checksums: dict | None = None, expected_oci: str | None = None,
            client: Client | None = None, now: datetime | None = None) -> dict:
    stable(version)
    if expected_oci is not None:
        if not expected_oci.startswith("sha256:"):
            raise ValueError("--oci-digest must be sha256:<64 lowercase hex characters>")
        digest(expected_oci.removeprefix("sha256:"))
    client = client or Client()
    now = now or datetime.now(timezone.utc)
    channels = {"pypi": guarded(pypi, client, version, checksums, now)}
    archived = None
    def release_check():
        nonlocal archived
        outcome, archived = github_release(client, version)
        return outcome
    channels["github-release"] = guarded(release_check)
    archived_oci = channels["github-release"]["evidence"].get("oci_digest")
    if expected_oci and archived_oci and expected_oci != archived_oci:
        channels["ghcr"] = result("conflicting", "Supplied OCI ledger digest and archived manifest disagree.", "Stop and reconcile the original release evidence.")
    else:
        channels["ghcr"] = guarded(ghcr, client, expected_oci or archived_oci, version)
    channels["mcp-registry"] = guarded(registry, client, version, archived)
    channels["plugin-marketplace"] = guarded(plugin, client, version)
    channels["homebrew"] = guarded(homebrew, client, version, checksums)
    counts = {state: sum(c["status"] == state for c in channels.values()) for state in STATES}
    return {"schema_version": 1, "version": version, "checked_at": now.isoformat(), "channels": channels,
            "counts": counts,
            "exit_code": 0 if counts["verified"] + counts["deferred"] == len(channels) else 1}


def print_report(report: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2))
        return
    print(f"dead-letter {report['version']} — {report['checked_at']}")
    for name, row in report["channels"].items():
        print(f"{name:20} {row['status']:18} {row['detail']}")
        if row["next_action"] != "None.":
            print(f"  Next: {row['next_action']}")
    print("Read-only snapshot; verified metadata does not imply a fresh client/runtime test.")
