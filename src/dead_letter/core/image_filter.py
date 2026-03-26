"""Pre-sanitization image filtering for signature images and tracking pixels."""

from __future__ import annotations

import re

from selectolax.parser import HTMLParser

from dead_letter.core.types import StrippedImage, StrippedImageCategory

_RE_DIMENSION_ATTR = re.compile(r"^[01]$")
_RE_CSS_DIMENSION = re.compile(
    r"(?<![\w-])(?:width|height)\s*:\s*([01])(?:px)?\s*(?:!important\s*)?(?:;|$)",
    re.IGNORECASE,
)
_RE_CSS_HIDDEN = re.compile(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", re.IGNORECASE)

_SIGNATURE_WRAPPER_SELECTORS = {
    "gmail_signature_wrapper": [
        '[data-smartmail="gmail_signature"]',
        ".gmail_signature",
    ],
    "thunderbird_signature_wrapper": [
        ".moz-signature",
        ".moz-txt-sig",
    ],
    "apple_mail_signature_wrapper": [
        ".Apple-string-attachment",
    ],
}

_GMAIL_MAIL_SIG_PATTERN = "googleusercontent.com/mail-sig/"

_BLOCK_TAGS = frozenset({
    "div", "p", "table", "section", "article", "main", "header", "footer", "nav",
    "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "blockquote",
})

_SIGNATURE_FILENAME_PATTERNS: set[str] = {
    "logo", "banner", "signature", "spacer", "pixel", "separator",
    "facebook", "linkedin", "twitter", "instagram", "youtube", "tiktok", "github",
    "icon", "badge",
}


def filter_images(
    html: str,
    *,
    strip_signature_images: bool,
    strip_tracking_pixels: bool,
) -> tuple[str, list[StrippedImage]]:
    """Remove noise images from raw HTML before sanitization.

    Returns the cleaned HTML and a list of stripped image records for diagnostics.
    """
    if not strip_signature_images and not strip_tracking_pixels:
        return html, []

    tree = HTMLParser(html)
    stripped: list[StrippedImage] = []
    to_remove: list = []

    # Layer 1: strip images inside signature wrappers and decompose immediately.
    # Decomposing before the main loop ensures these nodes don't appear in
    # subsequent tree.css("img") calls (selectolax returns different Python
    # objects per query, so id()-based dedup doesn't work).
    if strip_signature_images:
        for reason, selectors in _SIGNATURE_WRAPPER_SELECTORS.items():
            for selector in selectors:
                for container in tree.css(selector):
                    for img in container.css("img"):
                        stripped.append(StrippedImage(
                            category=StrippedImageCategory.SIGNATURE_IMAGE,
                            reason=reason,
                            reference=img.attributes.get("src", "") or "",
                        ))
                        img.decompose()
                    # Layer 4: strip images that are subsequent siblings of the wrapper.
                    sibling = container.next
                    while sibling is not None:
                        if sibling.tag == "img":
                            stripped.append(StrippedImage(
                                category=StrippedImageCategory.SIGNATURE_IMAGE,
                                reason="structural_boundary_extension",
                                reference=sibling.attributes.get("src", "") or "",
                            ))
                            next_sib = sibling.next
                            sibling.decompose()
                            sibling = next_sib
                            continue
                        if sibling.tag and sibling.tag != "-text":
                            if sibling.tag in _BLOCK_TAGS:
                                text_content = sibling.text(separator="", strip=True)
                                if text_content:
                                    break
                            for nested_img in sibling.css("img"):
                                stripped.append(StrippedImage(
                                    category=StrippedImageCategory.SIGNATURE_IMAGE,
                                    reason="structural_boundary_extension",
                                    reference=nested_img.attributes.get("src", "") or "",
                                ))
                                nested_img.decompose()
                        sibling = sibling.next

    for img in tree.css("img"):

        src = img.attributes.get("src", "") or ""
        alt = img.attributes.get("alt", "") or ""

        if strip_signature_images:
            reason = _detect_signature_image(src, alt)
            if reason is not None:
                stripped.append(StrippedImage(
                    category=StrippedImageCategory.SIGNATURE_IMAGE,
                    reason=reason,
                    reference=src,
                ))
                to_remove.append(img)
                continue

        if strip_tracking_pixels:
            reason = _detect_tracking_pixel(img, src)
            if reason is not None:
                stripped.append(StrippedImage(
                    category=StrippedImageCategory.TRACKING_PIXEL,
                    reason=reason,
                    reference=src,
                ))
                to_remove.append(img)
                continue

    for node in to_remove:
        node.decompose()

    body = tree.body
    return (body.html if body else tree.html) or "", stripped


def _detect_signature_image(src: str, alt: str) -> str | None:
    """Return detection reason if img is a signature image (Layers 2-3), else None."""
    # Layer 2: Gmail proxy URL.
    if _GMAIL_MAIL_SIG_PATTERN in src:
        return "gmail_mail_sig_url"

    # Layer 3: Filename pattern matching.
    check_text = f"{src} {alt}".lower()
    for pattern in _SIGNATURE_FILENAME_PATTERNS:
        if pattern in check_text:
            return f"filename_pattern:{pattern}"

    return None


def _detect_tracking_pixel(img: object, src: str) -> str | None:
    """Return detection reason if img is a tracking pixel, else None."""
    # Safeguard: never strip CID references via tracking pixel detection.
    if src.startswith("cid:"):
        return None

    # Heuristic 1: dimension check (HTML attributes).
    width_attr = img.attributes.get("width", "") or ""
    height_attr = img.attributes.get("height", "") or ""
    width_match = bool(width_attr and _RE_DIMENSION_ATTR.match(width_attr))
    height_match = bool(height_attr and _RE_DIMENSION_ATTR.match(height_attr))
    if width_match and height_match:
        return "dimension_heuristic"
    if (width_match and not height_attr) or (height_match and not width_attr):
        return "dimension_heuristic"

    # Heuristic 1: dimension check (inline CSS).
    style = img.attributes.get("style", "") or ""
    if style:
        css_dims = _RE_CSS_DIMENSION.findall(style)
        if len(css_dims) >= 2:
            return "dimension_heuristic"
        if css_dims:
            other_attr = width_attr or height_attr
            if other_attr and _RE_DIMENSION_ATTR.match(other_attr):
                return "dimension_heuristic"

    # Heuristic 2: hidden images.
    if style and _RE_CSS_HIDDEN.search(style):
        return "hidden_image"

    return None
