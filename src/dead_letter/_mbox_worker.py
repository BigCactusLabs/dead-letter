"""Internal single-message worker. Not a public command or a security sandbox."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _disable_core_dumps() -> None:
    # Do this before importing the MIME/native libraries. Best effort: OS crash
    # collectors and externally configured dump services remain host policy.
    if os.name == "posix":
        import resource
        try:
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except (OSError, ValueError):
            pass


def run(request_path: Path) -> int:
    _disable_core_dumps()
    from dead_letter.core.mbox import MboxRecord
    from dead_letter.core.mbox_import import _convert_record
    from dead_letter.core.mbox_isolation import MAX_RECEIPT_BYTES
    from dead_letter.core.report import _sanitize_value
    from dead_letter.core.types import ConvertOptions

    # Only the parent creates requests in a fresh private directory. There are
    # no email-controlled flags, environment expansion, commands, or endpoints.
    request = json.loads(request_path.read_text(encoding="utf-8"))
    fields = request["record"]
    fields["path"] = Path(fields["path"])
    record = MboxRecord(**fields)
    options = ConvertOptions(**request["options"])
    if options.delete_eml:
        raise ValueError("A worker must never delete its input")
    result = _convert_record(
        record, Path(request["archive_name"]), request_path.parent / "artifacts", options,
        bundles=request["bundles"], unescape=request["unescape"],
    )
    # A receipt carries only status/diagnostics, never a serialized MIME object
    # or a path for the parent to follow. Exit without a receipt on oversize.
    receipt = {
        "schema_version": 1, "index": record.index, "sha256": record.sha256,
        "success": result.success, "diagnostics": result.diagnostics,
        "error_code": result.error["code"] if result.error else None,
    }
    raw = json.dumps(_sanitize_value(receipt), ensure_ascii=True, allow_nan=False).encode("utf-8")
    if len(raw) > MAX_RECEIPT_BYTES:
        return 0
    (request_path.parent / "receipt.json").write_bytes(raw)
    return 0


def main() -> int:
    try:
        if len(sys.argv) != 2:
            return 2
        return run(Path(sys.argv[1]))
    except Exception:
        # The parent emits only a fixed safe error code; no private traceback.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
