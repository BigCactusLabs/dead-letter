"""Reviewed core-only tap preparation; no package publication or automatic merge.

Default: print a plan. --write invokes Homebrew's write-only resource generator
in an explicitly selected clean tap branch. --open-pr is a separate, explicit
remote write accepting only the reviewed patch hash from that preparation.
"""
from __future__ import annotations

from collections.abc import Mapping
import difflib
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import unicodedata
from urllib.parse import quote, urlsplit

from release_evidence import (RESOURCE, TAP, Client, Conflict, Unavailable, formula,
                              normalized, pypi_files, released_sdist, stable)

FORMULA = "Formula/dead-letter.rb"
BREW_NAME = "BigCactusLabs/tap/dead-letter"
REMOTE = f"https://github.com/{TAP}.git"
HOMEBREW_UPLOAD_DELAY = timedelta(hours=24)
BREW_ERROR_DETAIL_LIMIT = 240


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _upload_times(value: object) -> tuple[datetime, datetime]:
    if not isinstance(value, str) or not value.strip():
        raise Unavailable("PyPI sdist upload_time_iso_8601 is missing; Homebrew eligibility cannot be determined")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise Unavailable("PyPI sdist upload_time_iso_8601 is malformed; Homebrew eligibility cannot be determined") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise Unavailable("PyPI sdist upload_time_iso_8601 must include a timezone; Homebrew eligibility cannot be determined")
    try:
        uploaded = parsed.astimezone(timezone.utc)
        return uploaded, uploaded + HOMEBREW_UPLOAD_DELAY
    except OverflowError:
        raise Unavailable("PyPI sdist upload_time_iso_8601 is out of range; Homebrew eligibility cannot be determined") from None


def _brew_retry_time(recipe: dict) -> datetime:
    _, retry = _upload_times(recipe.get("sdist_upload_time_utc"))
    if recipe.get("homebrew_earliest_prepare_utc") != _utc_text(retry):
        raise Unavailable("Homebrew eligibility time does not match the verified sdist upload time")
    return retry


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _brew_error_detail(stderr: str, env: Mapping[str, str]) -> str | None:
    detail = ""
    for line in reversed(stderr.splitlines()):
        line = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))", "", line)
        detail = "".join(char for char in line if not unicodedata.category(char).startswith("C")).strip()
        if detail:
            break
    secret_keys = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY", "APIKEY", "CREDENTIAL")
    for key, value in env.items():
        if value and any(marker in key.upper() for marker in secret_keys):
            detail = detail.replace(value, "[REDACTED]")
    detail = re.sub(r"(?i)(https?://)[^\s/@]+(?::[^\s/@]*)?@", r"\1[REDACTED]@", detail)
    detail = re.sub(
        r"(?i)\b(?:github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9_]+|xox[baprs]-[A-Za-z0-9-]+|"
        r"npm_[A-Za-z0-9]+|pypi-[A-Za-z0-9_-]+|AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{8,})\b",
        "[REDACTED]", detail)
    detail = re.sub(r"(?i)\b((?:Bearer|Basic)\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", detail)
    detail = re.sub(r"(?i)([?&](?:access_token|token|api[_-]?key|password)=)[^\s&#]+", r"\1[REDACTED]", detail)
    detail = re.sub(r"(?i)\b((?:access[_-]?token|api[_-]?key|password|secret)\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]", detail)
    if not detail:
        return None
    return detail[:BREW_ERROR_DETAIL_LIMIT]


def run(command: list[str], *, cwd: Path, public: bool = True) -> str:
    env = dict(os.environ)
    if public:
        for key in list(env):
            if key in {"GH_TOKEN", "GITHUB_TOKEN", "HOMEBREW_GITHUB_API_TOKEN"} or key.startswith("PIP_"):
                env.pop(key, None)
        env.update(HOMEBREW_NO_AUTO_UPDATE="1", HOMEBREW_NO_ANALYTICS="1", PIP_CONFIG_FILE=os.devnull)
    completed = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, timeout=600, check=False)
    if completed.returncode:
        name = Path(command[0]).name
        context = f"{name} {command[1]} failed (exit {completed.returncode})"
        # Authenticated and non-brew output stays hidden to protect credentials.
        detail = _brew_error_detail(completed.stderr, os.environ) if public and name == "brew" else None
        raise Unavailable(f"{context}: {detail}" if detail else f"{context}; inspect locally")
    return completed.stdout


def plan(version: str, sdist: dict) -> dict:
    branch = f"prepare/dead-letter-{stable(version)}"
    uploaded, earliest = _upload_times(sdist.get("upload_time_iso_8601"))
    return {"schema_version": 1, "version": version, "branch": branch, "formula": BREW_NAME,
            "sdist_url": sdist["url"], "sdist_sha256": sdist["digests"]["sha256"],
            "sdist_upload_time_utc": _utc_text(uploaded),
            "homebrew_earliest_prepare_utc": _utc_text(earliest),
            "commands": {
                "branch": ["git", "switch", "-c", branch],
                "bump": ["brew", "bump-formula-pr", "--write-only", "--no-browse", "--python-package-name=dead-letter",
                         f"--version={version}", f"--url={sdist['url']}", f"--sha256={sdist['digests']['sha256']}", BREW_NAME],
                "fallback": ["brew", "update-python-resources", "--package-name=dead-letter", f"--version={version}", BREW_NAME],
            }, "note": "Plan only. --write regenerates resources, selects matching Apple-silicon wheels, and produces a review packet. No merge or publication."}


def tap_state(tap: Path, version: str, *, clean: bool) -> str:
    if tap.is_symlink() or not tap.is_dir():
        raise ValueError("--tap must be an existing regular checkout directory")
    root = Path(run(["git", "rev-parse", "--show-toplevel"], cwd=tap).strip()).resolve()
    if root != tap.resolve():
        raise ValueError("--tap must identify the checkout root")
    allowed = {REMOTE, REMOTE.removesuffix(".git"), f"git@github.com:{TAP}.git"}
    # A separate remote.origin.pushurl is what `git push origin` actually uses.
    for flag in ((), ("--push",)):
        if run(["git", "remote", "get-url", *flag, "origin"], cwd=tap).strip() not in allowed:
            raise ValueError("tap origin fetch and push URLs must both be BigCactusLabs/homebrew-tap")
    branch = run(["git", "branch", "--show-current"], cwd=tap).strip()
    if branch != f"prepare/dead-letter-{stable(version)}":
        raise ValueError(f"create/switch to prepare/dead-letter-{version} before editing; main is never modified")
    if clean and run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=tap).strip():
        raise ValueError("tap must have no staged, unstaged, or untracked changes")
    path = tap / FORMULA
    if path.is_symlink() or not path.is_file() or path.resolve().parent != (tap.resolve() / "Formula"):
        raise ValueError("formula must be a regular file inside the tap")
    return branch


def resource_version(item: dict) -> str:
    basename = Path(urlsplit(item["url"]).path).name
    prefix = "[-_.]+".join(re.escape(part) for part in item["name"].split("-"))
    match = re.fullmatch(prefix + r"-([0-9][A-Za-z0-9.!+]*)(?:\.tar\.gz|\.zip|\.tar\.bz2|-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+\.whl)", basename, re.I)
    if not match:
        raise Unavailable("resource URL does not identify an exact Python distribution version")
    return match[1]


def skeleton(text: str) -> str:
    """Only root release fields and simple resource blocks may change."""
    text = RESOURCE.sub("", text)
    text = re.sub(r'^  (?:url|sha256|version) "[^"\n]+"\n|^  revision \d+\n', "", text, flags=re.M)
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip())


def wheel_resources(client: Client, items: list[dict], wheels: Path) -> dict[str, dict]:
    selected = {}
    for path in wheels.iterdir():
        if path.is_symlink() or not path.is_file() or path.suffix != ".whl":
            raise Conflict("pip produced a non-wheel resource")
        name = normalized(path.name.split("-", 1)[0])
        matches = [i for i in items if i["name"] == name]
        if len(matches) != 1 or name in selected:
            raise Conflict("downloaded wheel set differs from generated core resources")
        version = resource_version(matches[0])
        doc = client.json(f"https://pypi.org/pypi/{quote(name, safe='')}/{quote(version, safe='')}/json")
        published = [f for f in pypi_files(doc, version, name) if f["filename"] == path.name]
        if len(published) != 1 or published[0].get("yanked"):
            raise Conflict("downloaded wheel is not an unyanked PyPI resource")
        with path.open("rb") as stream:
            sha = hashlib.file_digest(stream, "sha256").hexdigest()
        if sha != published[0]["digests"]["sha256"]:
            raise Conflict("downloaded wheel checksum differs from PyPI")
        selected[name] = {"url": published[0]["url"], "sha256": sha}
    if set(selected) != {i["name"] for i in items}:
        raise Conflict("a generated resource has no compatible wheel")
    return selected


def prepare_tap(tap: Path, output: Path, recipe: dict, *, client: Client) -> dict:
    version = recipe["version"]
    retry = _brew_retry_time(recipe)
    if _utc_now() < retry:
        raise Unavailable(f"dead-letter {version} is too new for Homebrew's 24-hour PyPI cutoff; retry at {_utc_text(retry)}")
    tap_state(tap, version, clean=True)
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise Unavailable("this tap's wheel layout requires a native Apple-silicon Mac for --write")
    if output.exists() or output.is_symlink() or output.resolve().is_relative_to(tap.resolve()):
        raise ValueError("use a new review directory outside the tap")
    installed = Path(run(["brew", "--repo", "BigCactusLabs/tap"], cwd=tap).strip()).resolve()
    if installed != tap.resolve():
        raise ValueError("--tap must match the installed BigCactusLabs/tap checkout")
    path = tap / FORMULA
    before = path.read_text(encoding="utf-8")
    old = formula(before)
    if tuple(map(int, version.split("."))) <= tuple(map(int, old["version"].split("."))):
        raise ValueError("target must be newer than the tap; no same-version rewrite or rollback")
    if not all(i["url"].endswith(".whl") for i in old["resources"]):
        raise Unavailable("review a new preparation policy for non-wheel tap layouts")
    python_matches = re.findall(r'^  depends_on "python@(\d+\.\d+)"$', before, re.M)
    if len(python_matches) != 1 or "depends_on arch: :arm64" not in before:
        raise Unavailable("unrecognized tap Python/architecture contract")
    python_version = python_matches[0]
    prefix = Path(run(["brew", "--prefix", "python@" + python_version], cwd=tap).strip())
    python = str(prefix / "bin" / ("python" + python_version))
    identity = json.loads(run([python, "-I", "-c", "import json,platform,sys; print(json.dumps([list(sys.version_info[:2]),platform.machine()]))"], cwd=tap))
    if identity != [[int(x) for x in python_version.split(".")], "arm64"]:
        raise Unavailable("Homebrew interpreter is not the declared native arm64 Python")
    base = run(["git", "rev-parse", "HEAD"], cwd=tap).strip()
    owned = before
    try:
        run(recipe["commands"]["bump"], cwd=tap)
        generated = path.read_text(encoding="utf-8")
        owned = generated
        if RESOURCE.findall(generated) == RESOURCE.findall(before):
            run(recipe["commands"]["fallback"], cwd=tap)
            generated = path.read_text(encoding="utf-8")
            owned = generated
        data = formula(generated)
        if (data["version"] != version or data["url"] != recipe["sdist_url"]
                or data["sha256"] != recipe["sdist_sha256"] or skeleton(generated) != skeleton(before)):
            raise Conflict("Homebrew changed fields outside the approved version/resources layout")
        # Homebrew resolves the dependency graph, not us. pip selects compatible
        # wheels for those exact versions under the tap's native interpreter;
        # --only-binary/--no-deps prohibits running sdist build hooks here.
        with tempfile.TemporaryDirectory(prefix="dead-letter-brew-wheels-") as temporary:
            wheels = Path(temporary)
            pins = [f"{item['name']}=={resource_version(item)}" for item in data["resources"]]
            run([python, "-I", "-m", "pip", "--isolated", "download", "--no-cache-dir", "--no-deps", "--only-binary=:all:",
                 "--index-url=https://pypi.org/simple", "--dest", str(wheels), *pins], cwd=tap)
            selected = wheel_resources(client, data["resources"], wheels)
        def block(match):
            item = selected[normalized(match[1])]
            return f'  resource "{match[1]}" do\n    url "{item["url"]}"\n    sha256 "{item["sha256"]}"\n  end\n'
        final = RESOURCE.sub(block, generated)
        formula(final)
        if path.is_symlink() or path.read_text(encoding="utf-8") != owned:
            raise Conflict("formula changed concurrently; leave it for explicit reconciliation")
        path.write_text(final, encoding="utf-8", newline="\n")
        owned = final
        run(["brew", "style", BREW_NAME], cwd=tap)
        changed = run(["git", "diff", "--name-only"], cwd=tap).splitlines()
        if changed != [FORMULA] or run(["git", "diff", "--cached", "--name-only"], cwd=tap).strip() or run(["git", "ls-files", "--others", "--exclude-standard"], cwd=tap).strip():
            raise Conflict("preparation changed files outside the formula; inspect the tap")
        patch = "".join(difflib.unified_diff(before.splitlines(keepends=True), final.splitlines(keepends=True),
                                            fromfile="a/" + FORMULA, tofile="b/" + FORMULA))
        patch_hash = hashlib.sha256(patch.encode()).hexdigest()
        packet = {**recipe, "base_commit": base, "diff_sha256": patch_hash,
                  "formula_sha256": hashlib.sha256(final.encode()).hexdigest(), "status": "prepared-not-tested",
                  "validation_required": ["brew install --build-from-source", "brew test", "native Apple-silicon synthetic conversion"]}
        output.mkdir(parents=True, exist_ok=False)
        (output / "formula.patch").write_text(patch, encoding="utf-8", newline="\n")
        (output / "preparation.json").write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")
        (output / "pr-body.md").write_text(pr_body(packet), encoding="utf-8")
        return packet
    except BaseException:
        # Restore only the formula this invocation owned; never reset the tap or
        # delete unrelated files. An interrupted/failed run may leave a partial
        # review packet, which must be inspected rather than silently reused.
        if not path.is_symlink() and path.read_text(encoding="utf-8") == owned:
            path.write_text(before, encoding="utf-8", newline="\n")
        raise


def pr_body(packet: dict) -> str:
    return f'''## Reviewed dead-letter {packet["version"]} preparation

Released sdist: {packet["sdist_url"]}
SHA-256: `{packet["sdist_sha256"]}` (cross-checked against original Python build evidence).
Reviewed diff SHA-256: `{packet["diff_sha256"]}`.

Homebrew regenerated the core resource graph. Compatible wheels were selected
with the tap's native Python and checked against PyPI. No UI/MCP/benchmark extras
were requested. Existing architecture, Python, install, and test logic remain.

## Maintainer acceptance (not performed by preparation)

- [ ] Review the formula diff and all regenerated resource versions/checksums.
- [ ] `brew install --build-from-source BigCactusLabs/tap/dead-letter` (or reinstall).
- [ ] `brew test BigCactusLabs/tap/dead-letter`.
- [ ] Convert a synthetic `.eml` on native Apple silicon; check Markdown and source preservation.
- [ ] Verify `dead-letter-mcp` and `dead-letter-ui` were not installed by this formula.
- [ ] Rerun `release.py status` with original build evidence after a manual merge.

Preparation is not install validation. Merge remains manual; no release, tag,
marketplace update, bottle publication, or auto-merge is requested.
'''


def open_pr(tap: Path, output: Path, recipe: dict, reviewed: str) -> dict:
    """Explicit second phase: publish only the previously reviewed formula diff."""
    branch = tap_state(tap, recipe["version"], clean=False)
    packet = json.loads((output / "preparation.json").read_text(encoding="utf-8"))
    patch = (output / "formula.patch").read_bytes()
    actual = hashlib.sha256(patch).hexdigest()
    if reviewed != actual or packet.get("diff_sha256") != actual:
        raise ValueError("--reviewed-diff-sha256 must match the exact preparation packet")
    if any(packet.get(k) != recipe[k] for k in ("version", "sdist_url", "sdist_sha256", "branch")):
        raise Conflict("review packet no longer matches the verified PyPI release")
    if run(["git", "rev-parse", "HEAD"], cwd=tap).strip() != packet["base_commit"]:
        raise Conflict("tap HEAD changed since preparation; review again")
    if run(["git", "diff", "--cached", "--name-only"], cwd=tap).strip() or run(["git", "ls-files", "--others", "--exclude-standard"], cwd=tap).strip():
        raise Conflict("staged or untracked tap files require review")
    if run(["git", "diff", "--name-only"], cwd=tap).splitlines() != [FORMULA]:
        raise Conflict("only the prepared formula may be dirty")
    # The reviewed patch and hash cover bytes only; a mode change is unreviewed.
    if run(["git", "diff", "--summary", "--", FORMULA], cwd=tap).strip():
        raise Conflict("formula mode change after review requires a new preparation")
    current = (tap / FORMULA).read_bytes()
    parsed = formula(current.decode("utf-8"))
    if (parsed["version"] != recipe["version"] or parsed["url"] != recipe["sdist_url"]
            or parsed["sha256"] != recipe["sdist_sha256"] or not all(r["url"].endswith(".whl") for r in parsed["resources"])):
        raise Conflict("reviewed formula does not match the approved core-only release")
    if hashlib.sha256(current).hexdigest() != packet["formula_sha256"]:
        raise Conflict("formula changed after review")
    # Reconstruct the diff against the recorded base, not merely a caller-
    # editable hash field, before staging any bytes.
    before = run(["git", "show", "HEAD:" + FORMULA], cwd=tap)
    if skeleton(before) != skeleton(current.decode("utf-8")):
        raise Conflict("reviewed formula changes the tap installation contract")
    expected = "".join(difflib.unified_diff(before.splitlines(keepends=True), current.decode().splitlines(keepends=True),
                                          fromfile="a/" + FORMULA, tofile="b/" + FORMULA)).encode()
    if expected != patch:
        raise Conflict("reviewed patch does not describe the current formula diff")
    run(["git", "add", "--", FORMULA], cwd=tap)
    run(["git", "commit", "-m", f"chore: prepare dead-letter {recipe['version']}"], cwd=tap)
    run(["git", "-c", "credential.helper=", "-c", "credential.https://github.com.helper=!gh auth git-credential",
         "push", "origin", f"HEAD:refs/heads/{branch}"], cwd=tap, public=False)
    # Do not trust an edited Markdown packet as a command or an auto-merge
    # directive. Regenerate the fixed unchecked acceptance checklist.
    with tempfile.TemporaryDirectory(prefix="dead-letter-brew-pr-") as temporary:
        body = Path(temporary) / "body.md"
        body.write_text(pr_body(packet), encoding="utf-8")
        url = run(["gh", "pr", "create", "--draft", "--repo", TAP, "--base", "main", "--head", branch,
                   "--title", f"chore: prepare dead-letter {recipe['version']}", "--body-file", str(body)], cwd=tap, public=False).strip()
    return {"status": "draft-pr-opened", "url": url, "diff_sha256": actual, "merge": "manual"}


def execute(args) -> int:
    from release_evidence import read_checksums
    client = Client()
    recipe = plan(args.version, released_sdist(client, args.version, read_checksums(args.checksums, args.version)))
    if args.write or args.open_pr:
        if args.tap is None or args.output_dir is None:
            raise ValueError("--write/--open-pr requires --tap and --output-dir")
        if args.open_pr:
            if not args.reviewed_diff_sha256:
                raise ValueError("--open-pr requires --reviewed-diff-sha256 from the reviewed packet")
            recipe = open_pr(args.tap.resolve(), args.output_dir.resolve(), recipe, args.reviewed_diff_sha256)
        else:
            recipe = prepare_tap(args.tap, args.output_dir, recipe, client=client)
    elif args.reviewed_diff_sha256:
        raise ValueError("--reviewed-diff-sha256 applies only to --open-pr")
    print(json.dumps(recipe, indent=2))
    return 0
