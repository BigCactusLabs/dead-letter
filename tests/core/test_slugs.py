from __future__ import annotations

from dead_letter.core.slugs import slugify_subject


def test_slugify_subject_basic() -> None:
    assert slugify_subject("Quarterly Report Ready") == "quarterly-report-ready"


def test_slugify_subject_strips_reply_prefixes() -> None:
    assert slugify_subject("Re: Fwd: Project Update") == "project-update"


def test_slugify_subject_uses_fallback_for_empty_values() -> None:
    assert slugify_subject("   ") == "email"


def test_slugify_subject_transliterates_unicode() -> None:
    assert slugify_subject("Caf\u00e9 d\u00e9j\u00e0 vu") == "cafe-deja-vu"
