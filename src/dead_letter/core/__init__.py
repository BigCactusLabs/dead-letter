"""Core conversion package for dead-letter."""

from dead_letter.core._pipeline import convert, convert_dir, convert_to_bundle
from dead_letter.core.types import (
    BundleResult,
    ConvertOptions,
    ConvertResult,
    ThreadMode,
    ThreadOrder,
)

__all__ = [
    "BundleResult",
    "ConvertOptions",
    "ConvertResult",
    "ThreadMode",
    "ThreadOrder",
    "convert",
    "convert_dir",
    "convert_to_bundle",
]
