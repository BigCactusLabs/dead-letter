"""dead-letter: Convert .eml email exports to Markdown with YAML front matter."""

from dead_letter.core import (
    BundleResult,
    ConvertOptions,
    ConvertResult,
    ThreadMode,
    ThreadOrder,
    convert,
    convert_dir,
    convert_to_bundle,
)

__all__ = [
    "__version__",
    "BundleResult",
    "ConvertOptions",
    "ConvertResult",
    "ThreadMode",
    "ThreadOrder",
    "convert",
    "convert_dir",
    "convert_to_bundle",
]
__version__ = "0.2.0"
