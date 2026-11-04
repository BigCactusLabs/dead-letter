"""Reusable demo assets and captions stay within the limits of where they are used.

`scripts/render_demo.py` writes the media and `capture.json`; `captions.json`
is reviewed copy. Limits come from the surfaces in `docs/recipes/demo/README.md`:
a 1280×640 link card under 1 MB (GitHub social preview), a static square icon
of at most 512 px and 256 KiB, and the caption lengths below.
"""

import json
from pathlib import Path
import struct

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "docs" / "recipes" / "demo"
LOGO = ROOT / "docs" / "brand" / "production" / "readme-logo.png"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

CAPTION_LIMITS = {
    "title": 70,
    "one_liner": 100,
    "card_description": 200,
    "demo_caption": 200,
    "social_alt": 420,
    "demo_alt": 420,
}


def png_chunks(path: Path) -> list[tuple[bytes, bytes]]:
    data = path.read_bytes()
    assert data[:8] == PNG_SIGNATURE, f"{path.name} is not a PNG"
    chunks, offset = [], 8
    while offset < len(data):
        length, kind = struct.unpack(">I4s", data[offset : offset + 8])
        chunks.append((kind, data[offset + 8 : offset + 8 + length]))
        offset += 12 + length
        if kind == b"IEND":
            break
    assert chunks[0][0] == b"IHDR", f"{path.name} does not start with IHDR"
    return chunks


def png_size(path: Path) -> tuple[int, int]:
    return struct.unpack(">II", png_chunks(path)[0][1][:8])


def gif_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:10]
    assert head[:6] in (b"GIF87a", b"GIF89a"), f"{path.name} is not a GIF"
    return struct.unpack("<HH", head[6:10])


def captions() -> dict:
    return json.loads((DEMO / "captions.json").read_text(encoding="utf-8"))


def capture() -> dict:
    return json.loads((DEMO / "capture.json").read_text(encoding="utf-8"))


def test_social_card_is_a_github_social_preview_png():
    card = DEMO / "dead-letter-social-1280x640.png"
    assert png_size(card) == (1280, 640)
    assert card.stat().st_size < 1_000_000


def test_gif_and_still_keep_their_dimensions():
    gif = DEMO / "dead-letter-demo.gif"
    assert gif_size(gif) == (800, 450)
    assert gif.stat().st_size < 5_000_000
    assert png_size(DEMO / "dead-letter-demo.png") == (1600, 900)


def test_readme_logo_fits_the_strictest_icon_field():
    assert png_size(LOGO) == (512, 512)
    assert LOGO.stat().st_size <= 262_144
    assert b"acTL" not in [kind for kind, _ in png_chunks(LOGO)], "logo is an APNG"


def test_every_caption_is_non_empty():
    for key, value in captions().items():
        assert isinstance(value, str) and value.strip(), f"{key} is empty"


@pytest.mark.parametrize(("key", "limit"), CAPTION_LIMITS.items())
def test_caption_fits_its_surface_limit(key, limit):
    value = captions()[key]
    assert len(value) <= limit, f"{key} is {len(value)} characters, limit {limit}"


def test_caption_version_matches_the_captured_release():
    version = captions()["version"]
    assert capture()["release"]["package"] == f"dead-letter=={version}"
    assert version in captions()["demo_caption"]


def test_capture_record_matches_every_asset_on_disk():
    assets = capture()["assets"]
    assert "social" in assets
    for kind, asset in assets.items():
        path = DEMO / asset["file"]
        assert path.is_file(), f"{kind}: {asset['file']} is missing"
        assert path.stat().st_size == asset["bytes"], f"{kind}: size changed"
