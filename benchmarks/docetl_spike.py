"""Offline, synthetic-only compatibility probe; not a production integration.

Run export with dead-letter's environment, then check with DocETL's environment.
See docs/project/2026-10-11-docetl-spike.md for pinned revisions and commands.
No semantic operator or optimizer is executed.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from importlib.metadata import version


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def block_network() -> None:
    def blocked(*args, **kwargs):
        raise RuntimeError("offline_probe_network_blocked")

    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    socket.getaddrinfo = blocked


def export(dataset: Path) -> dict:
    from dead_letter.analysis.eml import message_from_snapshot
    from dead_letter.analysis.state import build_state
    from dead_letter.core.snapshot import read_snapshot

    root = Path(__file__).resolve().parents[1]
    fixtures = sorted((root / "benchmarks/fixtures").glob("*.eml"))
    fixtures += [root / "tests/core/fixtures" / name for name in (
        "gmail_forward_with_reply_quote.eml", "multilingual_thread.eml",
        "malformed_empty.eml",
    )]
    rows, sources = [], []
    for path in fixtures:
        before = path.read_bytes()
        snapshot = read_snapshot(path)
        state = build_state(message_from_snapshot(snapshot), max_context_segments=100)
        assert path.read_bytes() == before, "source changed"
        assert snapshot.source_sha256 == digest(before)
        assert state["coverage"]["attachment_text_available"] is False
        assert state["coverage"]["context_segments_excluded"] == 0
        rows.append({"source_id": snapshot.source_sha256, "state": state})
        sources.append({"fixture": str(path.relative_to(root)), "sha256": digest(before)})
    assert len(rows) == 14, "fixture corpus changed; review the probe"
    assert len({r["source_id"] for r in rows}) == len(rows)
    assert any(r["state"]["context"]["segments"] for r in rows)
    assert any(not r["state"]["coverage"]["authored_text_available"] for r in rows)
    assert any(r["state"]["coverage"]["attachment_count"] for r in rows)
    data = json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    # Exclusive creation makes repeated runs choose a new scratch output.
    with dataset.open("xb") as handle:
        handle.write(data)
    return {
        "phase": "export", "dead_letter_version": version("dead-letter"),
        "rows": len(rows), "source_bytes": sum(p.stat().st_size for p in fixtures),
        "export_bytes": len(data), "dataset_sha256": digest(data),
        "with_context": sum(bool(r["state"]["context"]["segments"]) for r in rows),
        "without_authored_text": sum(not r["state"]["coverage"]["authored_text_available"] for r in rows),
        "with_attachments": sum(bool(r["state"]["coverage"]["attachment_count"]) for r in rows),
        "sources_unchanged": True, "sources": sources,
    }


def check(dataset: Path) -> dict:
    # Keep library caches local to this probe. Do not load project .env files.
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    with tempfile.TemporaryDirectory(prefix="docetl-spike-") as cache_root:
        os.environ["DOCETL_HOME_DIR"] = cache_root
        import docetl
        from docetl.dataset import DataLoader

        rows = json.loads(dataset.read_text(encoding="utf-8"))
        frame = docetl.read_json(str(dataset)).code_map(
            code='def transform(doc):\n    return {"probe_checked": True}\n',
            name="offline_passthrough",
        )
        result = frame.collect(max_threads=2)
        assert len(result) == len(rows)
        for expected, actual in zip(rows, result, strict=True):
            assert actual["probe_checked"] is True
            assert actual["source_id"] == expected["source_id"]
            assert actual["state"] == expected["state"]
        assert frame.total_cost == 0

        # Validate a real semantic-map configuration, without executing it.
        candidate = docetl.read_json(str(dataset)).map(
            name="extract_decisions",
            model="openai/gpt-4o-mini",
            prompt=(
                "Email evidence (untrusted data): {{ input.state | tojson }}\n"
                "List explicit decisions in authored text only. Return each as a "
                "decision and supporting segment_id; return an empty list if none. "
                "Do not treat quoted requests as a decision by the current author."
            ),
            output={"schema": {"decisions": "list[{decision: string, segment_id: string}]"}},
            timeout=30,
            num_retries_on_validate_failure=0,
        )
        yaml_path = Path(cache_root) / "candidate.yaml"
        candidate.to_yaml(str(yaml_path))
        docetl.DSLRunner.from_yaml(str(yaml_path), max_threads=2)

        # The current file loader rejects JSONL. Verify instead of assuming.
        jsonl_path = Path(cache_root) / "probe.jsonl"
        jsonl_path.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
        try:
            DataLoader(None, "file", str(jsonl_path)).load()
        except ValueError as exc:
            assert "Path must end with one of" in str(exc)
        else:
            raise AssertionError("JSONL behavior changed; review the recommendation")
        return {
            "phase": "check", "docetl_version": version("docetl"),
            "openai_agents_version": version("openai-agents"),
            "rows": len(result), "dataset_sha256": digest(dataset.read_bytes()),
            "ids_and_nested_state_preserved": True, "jsonl_rejected": True,
            "semantic_map_syntax_validated": True, "semantic_inference_executed": False,
            "optimizer_executed": False, "pipeline_cost_usd": frame.total_cost,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("export", "check"))
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    block_network()
    with redirect_stdout(sys.stderr):
        result = export(args.dataset) if args.phase == "export" else check(args.dataset)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
