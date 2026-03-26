"""Slug utilities for deterministic output file naming."""

from __future__ import annotations

import re
import unicodedata

_REPLY_PREFIX_RE = re.compile(r"^(?:(?:re|fw|fwd)\s*:\s*)+", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def slugify_subject(subject: str | None, *, fallback: str = "email") -> str:
    """Convert a subject string into a stable filesystem-safe slug."""
    if subject is None:
        return fallback

    cleaned = _REPLY_PREFIX_RE.sub("", subject).strip().lower()
    if not cleaned:
        return fallback

    ascii_text = unicodedata.normalize("NFKD", cleaned).encode("ascii", "ignore").decode("ascii")
    slug = _NON_ALNUM_RE.sub("-", ascii_text).strip("-")
    return slug or fallback
