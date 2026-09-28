"""Core conversion package for dead-letter."""

from dead_letter.core._pipeline import convert, convert_dir, convert_to_bundle
from dead_letter.core.mbox_archive import ArchiveLimits, convert_mbox_archive
from dead_letter.core.mbox_import import convert_mbox
from dead_letter.core.types import (
    BundleResult,
    ConvertOptions,
    ConvertResult,
    ThreadMode,
    ThreadOrder,
)

__all__ = [
    "ArchiveLimits",
    "convert_mbox",
    "convert_mbox_archive",
    "BundleResult",
    "ConvertOptions",
    "ConvertResult",
    "ThreadMode",
    "ThreadOrder",
    "convert",
    "convert_dir",
    "convert_to_bundle",
]
