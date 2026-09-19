"""Inspect built distribution metadata and record/verify exact release bytes.

Stdlib only; no extraction, package imports, network requests, or publication.
"""
from __future__ import annotations

import argparse
from email import policy
from email.parser import BytesParser
import hashlib
from html.parser import HTMLParser
from pathlib import Path
import re
import tarfile
from urllib.parse import urlsplit
import zipfile

MARKER = "<!-- mcp-name: io.github.BigCactusLabs/dead-letter -->"
MAX_METADATA = 2 * 1024 * 1024


class ArtifactError(ValueError):
    """A distribution or its checksum evidence violates the release contract."""


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []
        self.logos: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        self.urls.extend(value for key, value in attrs if key in {"src", "href"} and value)
        if tag == "img" and values.get("alt") == "dead-letter":
            self.logos.append(values.get("src") or "")


def check_readme(text: str) -> None:
    if MARKER not in text:
        raise ArtifactError("distribution README is missing the MCP ownership marker")
    links = Links()
    links.feed(text)
    if not links.logos or any(urlsplit(url).scheme != "https" or not urlsplit(url).netloc for url in links.logos):
        raise ArtifactError("distribution README needs an absolute HTTPS logo URL")
    # This repository's hand-written README uses inline links. Check reference
    # definitions as well so switching Markdown syntax cannot bypass the gate.
    urls = links.urls + re.findall(r"\]\(\s*<?([^\s)>]+)", text)
    urls += re.findall(r"^\s{0,3}\[[^\]]+\]:\s*<?([^\s>]+)", text, re.MULTILINE)
    for url in urls:
        if url.startswith("#"):
            continue
        parsed = urlsplit(url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            continue
        if parsed.scheme == "mailto" and parsed.path:
            continue
        raise ArtifactError(f"distribution README has a relative/unsupported link: {url}")


def distribution_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise ArtifactError(f"distribution directory does not exist: {directory}")
    files = sorted(p for p in directory.iterdir() if p.name != ".gitignore")
    wheels = [p for p in files if p.suffix == ".whl"]
    sdists = [p for p in files if p.name.endswith(".tar.gz")]
    if len(files) != 2 or len(wheels) != 1 or len(sdists) != 1:
        raise ArtifactError("expected exactly one wheel and one .tar.gz sdist, with no stale/extra files")
    if any(p.is_symlink() or not p.is_file() for p in files):
        raise ArtifactError("distributions must be regular, non-symlink files")
    return files


def metadata(path: Path):
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            candidates = [p for p in archive.infolist() if re.fullmatch(r"[^/]+\.dist-info/METADATA", p.filename)]
            if len(candidates) != 1 or candidates[0].file_size > MAX_METADATA:
                raise ArtifactError("wheel must contain exactly one bounded METADATA file")
            raw = archive.read(candidates[0])
    else:
        with tarfile.open(path, "r:gz") as archive:
            candidates = [p for p in archive if re.fullmatch(r"[^/]+/PKG-INFO", p.name)]
            if len(candidates) != 1 or not candidates[0].isfile() or candidates[0].size > MAX_METADATA:
                raise ArtifactError("sdist must contain exactly one regular bounded root PKG-INFO")
            stream = archive.extractfile(candidates[0])
            if stream is None:
                raise ArtifactError("cannot read sdist metadata")
            with stream:
                raw = stream.read(MAX_METADATA + 1)
    return BytesParser(policy=policy.default).parsebytes(raw)


def validate(directory: Path, version: str) -> list[Path]:
    files = distribution_files(directory)
    descriptions = []
    for path in files:
        data = metadata(path)
        for key in ("Name", "Version", "Description-Content-Type"):
            if len(data.get_all(key, [])) != 1:
                raise ArtifactError(f"{path.name}: expected exactly one {key} header")
        if data["Name"] != "dead-letter" or data["Version"] != version:
            raise ArtifactError(f"{path.name}: incorrect package name/version")
        if str(data["Description-Content-Type"]).split(";", 1)[0].strip() != "text/markdown":
            raise ArtifactError(f"{path.name}: README content type must be text/markdown")
        if not re.fullmatch(rf"dead[-_]letter-{re.escape(version)}(?:-[^/]+\.whl|\.tar\.gz)", path.name):
            raise ArtifactError(f"{path.name}: filename does not match the metadata version")
        text = data.get_payload(decode=True)
        if not isinstance(text, bytes):
            raise ArtifactError(f"{path.name}: missing README payload")
        description = text.decode("utf-8")
        check_readme(description)
        descriptions.append(description)
    if descriptions[0] != descriptions[1]:
        raise ArtifactError("wheel and sdist README metadata disagree")
    return files


def checksums(files: list[Path]) -> str:
    lines = []
    for path in sorted(files):
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        lines.append(f"{digest}  {path.name}\n")
    return "".join(lines)


def verify_checksums(files: list[Path], manifest: Path) -> None:
    # Exact canonical comparison rejects missing, duplicate, additional, unsafe
    # path entries and changed bytes rather than accepting a matching subset.
    if manifest.is_symlink() or not manifest.is_file():
        raise ArtifactError("checksum manifest must be a regular, non-symlink file")
    if manifest.read_text(encoding="utf-8") != checksums(files):
        raise ArtifactError("distribution checksums differ from the recorded build")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("record", "verify"))
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--checksums", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        files = validate(args.dist_dir, args.version)
        if args.operation == "record":
            if args.checksums.exists() or args.checksums.is_symlink():
                verify_checksums(files, args.checksums)
            else:
                args.checksums.parent.mkdir(parents=True, exist_ok=True)
                with args.checksums.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(checksums(files))
        else:
            verify_checksums(files, args.checksums)
    except (ArtifactError, OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"FAIL: {exc}\n")
    print(checksums(files), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
