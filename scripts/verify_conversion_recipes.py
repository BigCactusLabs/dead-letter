"""Smoke the five documented recipes against an installed dead-letter 0.4.0.

Pass an isolated released interpreter with --python. This script uses only
synthetic mail and the installed CLI/Python API/real MCP stdio entry points.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from smoke_mcpb import EXPECTED_TOOLS, SmokeFailure, StdioClient, result_text


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "docs" / "recipes" / "fixtures" / "inbox"
CLIENT = Path("clients/order-update.eml")
OPS = Path("ops/loading-window.eml")
CLIENT_MD = Path("clients/cedar-works-order-4821-revised-delivery.md")
OPS_MD = Path("ops/loading-window-for-cedar-works.md")
CSV = b"sku,quantity\nnotebook,24\npen,48\n"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(
    command: list[str], *, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command, cwd=cwd, env=env, text=True, capture_output=True, timeout=60
    )
    require(
        completed.returncode == 0,
        f"{command[0]} failed: {completed.stderr or completed.stdout}",
    )
    return completed


def stop(process: subprocess.Popen[str]) -> None:
    if process.stdin and not process.stdin.closed:
        process.stdin.close()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def check(python: Path) -> dict[str, object]:
    bin_dir = python.absolute().parent
    cli = bin_dir / "dead-letter"
    server = bin_dir / "dead-letter-mcp"
    require(
        python.is_file() and cli.is_file() and server.is_file(),
        "released interpreter/entry points missing",
    )
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    installed = run(
        [
            str(python),
            "-c",
            "import dead_letter; print(dead_letter.__version__); print(dead_letter.__file__)",
        ],
        cwd=Path(tempfile.gettempdir()),
        env=env,
    ).stdout.splitlines()
    require(installed[0] == "0.4.0", f"expected released 0.4.0, found {installed[0]}")
    require(
        not Path(installed[1]).resolve().is_relative_to(ROOT),
        "import resolved to checkout",
    )

    with tempfile.TemporaryDirectory(prefix="dead-letter-recipes-") as temp:
        work = Path(temp)
        inbox = work / "inbox"
        shutil.copytree(FIXTURES, inbox)
        before = {path: sha(inbox / path) for path in (CLIENT, OPS)}

        vault = work / "vault" / "Email"
        run(
            [str(cli), "convert", str(inbox), "--output", str(vault)], cwd=work, env=env
        )
        require(
            {p.relative_to(vault) for p in vault.rglob("*.md")} == {CLIENT_MD, OPS_MD},
            "Obsidian output did not mirror both input subfolders",
        )
        require(
            "subject: Cedar Works order 4821" in (vault / CLIENT_MD).read_text(),
            "Obsidian YAML subject missing",
        )

        rag = work / "rag-ready"
        run(
            [
                str(cli),
                "convert",
                str(inbox),
                "--output",
                str(rag),
                "--thread-mode",
                "structured",
            ],
            cwd=work,
            env=env,
        )
        ops_text = (rag / OPS_MD).read_text()
        require(
            "thread_messages: 1" in ops_text and "## Earlier message" in ops_text,
            "structured reply history missing",
        )
        require(
            "attachments:" in (rag / CLIENT_MD).read_text(),
            "attachment metadata missing",
        )
        require(
            not list(rag.rglob("*.csv")),
            "flat Markdown conversion unexpectedly extracted attachment bytes",
        )

        audit = work / "audit"
        run(
            [
                str(cli),
                "convert",
                str(inbox / CLIENT),
                "--output",
                str(audit),
                "--report",
            ],
            cwd=work,
            env=env,
        )
        report = json.loads((audit / ".dead-letter-report.json").read_text())
        require(
            report["schema_version"] == 1 and report["generator"]["version"] == "0.4.0",
            "report version mismatch",
        )
        require(
            report["summary"] == {"total": 1, "written": 1, "skipped": 0, "errors": 0},
            "report counts mismatch",
        )
        require(
            report["results"][0]["source"] == CLIENT.name
            and report["results"][0]["success"],
            "report result mismatch",
        )
        require(
            "diagnostics" not in report["results"][0],
            "CLI report unexpectedly includes diagnostics",
        )

        cabinet = work / "Cabinet"
        code = (
            "import json,sys; from dead_letter import convert_to_bundle; "
            "r=convert_to_bundle(sys.argv[1],bundle_root=sys.argv[2],source_handling='copy'); "
            "print(json.dumps({'success':r.success,'bundle':str(r.bundle)}))"
        )
        bundle_result = json.loads(
            run(
                [str(python), "-c", code, str(inbox / CLIENT), str(cabinet)],
                cwd=work,
                env=env,
            ).stdout
        )
        bundle = cabinet / "order-update"
        require(
            bundle_result["success"]
            and Path(bundle_result["bundle"]).resolve() == bundle.resolve(),
            f"Python bundle result mismatch: {bundle_result!r} vs {bundle!s}",
        )
        require(
            (bundle / "message.md").is_file() and (bundle / CLIENT.name).is_file(),
            "Cabinet message/source missing",
        )
        require(
            (bundle / "attachments" / "order-4821.csv").read_bytes() == CSV,
            "Cabinet attachment bytes mismatch",
        )
        require(
            sha(bundle / CLIENT.name) == before[CLIENT], "Cabinet source copy differs"
        )
        require(
            "attachments/order-4821.csv" in (bundle / "message.md").read_text(),
            "Cabinet attachment link missing",
        )

        process = subprocess.Popen(
            [str(server)],
            cwd=work,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            mcp = StdioClient(process)
            initialized = mcp.request(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "dead-letter-recipe-smoke", "version": "1"},
                },
            )
            require(bool(initialized.get("serverInfo")), "MCP initialize failed")
            mcp.notify("notifications/initialized")
            names = {item["name"] for item in mcp.request("tools/list")["tools"]}
            require(names == EXPECTED_TOOLS, f"unexpected MCP tools: {sorted(names)}")

            def call(name: str, arguments: dict[str, str]) -> str:
                result = mcp.request(
                    "tools/call", {"name": name, "arguments": arguments}
                )
                require(
                    not result.get("isError"), f"{name} failed: {result_text(result)}"
                )
                return result_text(result)

            markdown = call("convert_eml", {"eml_path": str(inbox / CLIENT)})
            require(
                markdown.startswith("---\n") and "order 4821" in markdown,
                "MCP returned no converted Markdown",
            )
            mcp_bundle = json.loads(
                call(
                    "convert_eml_to_bundle",
                    {
                        "eml_path": str(inbox / CLIENT),
                        "bundle_root": str(work / "mcp-cabinet"),
                        "source_handling": "copy",
                    },
                )
            )
            require(
                Path(mcp_bundle["markdown_path"]).is_file(),
                "MCP bundle markdown missing",
            )
            require(
                (Path(mcp_bundle["bundle_path"]) / CLIENT.name).is_file(),
                "MCP bundle source copy missing",
            )
            require(
                [Path(p).read_bytes() for p in mcp_bundle["attachment_paths"]] == [CSV],
                "MCP bundle attachment bytes mismatch",
            )
            require(
                mcp_bundle["diagnostics"]["attachments"]
                == {"referenced": 1, "retained": 1},
                "MCP bundle attachment diagnostics mismatch",
            )
            mcp_dir = work / "mcp-output"
            summary = json.loads(
                call(
                    "convert_directory",
                    {
                        "directory": str(inbox),
                        "output_directory": str(mcp_dir),
                    },
                )
            )
            require(
                (summary["total"], summary["successes"], summary["failures"])
                == (2, 2, 0),
                "MCP directory counts mismatch",
            )
            require(
                {p.relative_to(mcp_dir) for p in mcp_dir.rglob("*.md")}
                == {CLIENT_MD, OPS_MD},
                "MCP directory layout mismatch",
            )
            diagnostics = json.loads(
                call("get_diagnostics", {"eml_path": str(inbox / CLIENT)})
            )
            require(
                diagnostics["attachments"] == {"referenced": 1, "retained": 1},
                "MCP diagnostics attachment counts mismatch",
            )
            require(
                diagnostics["state"] in {"normal", "degraded", "review_recommended"},
                "MCP diagnostics state missing",
            )
        finally:
            stop(process)
            if process.stderr:
                process.stderr.close()

        require(
            {path: sha(inbox / path) for path in (CLIENT, OPS)} == before,
            "source mail changed",
        )
        return {
            "release": installed[0],
            "source": installed[1],
            "cli": "folder, structured RAG, report passed",
            "python": "copy-only Cabinet and attachment bytes passed",
            "mcp": "stdio handshake, four tools, calls and diagnostics passed",
            "source_sha256": {str(path): digest for path, digest in before.items()},
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python", type=Path, required=True, help="isolated released interpreter"
    )
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.python), indent=2))
    except (
        SmokeFailure,
        OSError,
        subprocess.TimeoutExpired,
        KeyError,
        ValueError,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
