"""Render the recipe demo (MP4, GIF, still PNG) from a released dead-letter.

Maintainer tool, not run by CI or the test suite. It installs the named release
from PyPI into a temporary virtual environment, copies the synthetic recipe
inbox into a temporary work directory, runs real shell commands and one real
MCP stdio call there, and renders only captured output and file content.

    python scripts/render_demo.py                # writes docs/recipes/demo/
    python scripts/render_demo.py --out /tmp/demo --keep-work

Requires bash, find, sort, cat, ls, uv, node with the `playwright` package and
its Chromium, and ffmpeg. Exit 0: rendered; 1: a check failed; 2: a tool is
missing. The repository fixtures are never moved or modified.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile

from smoke_mcpb import SmokeFailure, StdioClient, result_text

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "docs" / "recipes" / "fixtures" / "inbox"
FAVICON = ROOT / "docs" / "brand" / "production" / "favicon-32x32.png"
DEFAULT_OUT = ROOT / "docs" / "recipes" / "demo"
DEFAULT_VERSION = "0.4.10"
# Resolve dependencies as of the day after 0.4.10 reached PyPI (2026-10-29).
DEFAULT_EXCLUDE_NEWER = "2026-10-30T00:00:00Z"
WIDTH, HEIGHT = 800, 450  # CSS pixels: GIF at 1x, MP4 and still at 2x.
STEM = "dead-letter-demo"
CLIENT = "inbox/clients/order-update.eml"
SCENE_SECONDS = (5, 5, 7, 5, 5)

# Brume tokens and font stacks from docs/brand/style-guide.md.
CSS = """
:root {
  --bg: #0d1117; --panel: #161b22; --line: #30363d; --ink: #e6edf3;
  --muted: #8b949e; --faint: #484f58; --accent: #5870a8; --accent-2: #6b9db5;
  --counter: #d8a050; --ok: #4d9960;
  --sans: Inter, "Geist", system-ui, -apple-system, sans-serif;
  --mono: "IBM Plex Mono", "Source Code Pro", "Fira Mono", Menlo, Consolas, monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: 800px; height: 450px; overflow: hidden; background: var(--bg); color: var(--ink); }
body { font-family: var(--sans); display: flex; flex-direction: column; padding: 12px 24px 10px; }
.top { display: flex; align-items: center; gap: 8px; }
.top img { width: 18px; height: 18px; }
h1 { font-size: 19px; font-weight: 600; letter-spacing: -0.01em; }
.chip { margin-left: auto; font-family: var(--mono); font-size: 12px; color: var(--counter); border: 1px solid var(--line); padding: 1px 6px; }
.step { font-family: var(--mono); font-size: 13px; color: var(--muted); }
.sub { font-size: 13.5px; color: var(--muted); margin-top: 3px; padding-bottom: 8px; border-bottom: 1px solid var(--line); }
.rows { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; flex: 1; min-height: 0; }
.row { display: flex; gap: 10px; min-height: 0; }
.row:last-child { flex: 1; }
.pane { background: var(--panel); border: 1px solid var(--line); padding: 7px 12px; min-width: 0; overflow: hidden; }
.label { font-family: var(--mono); font-size: 12px; color: var(--accent-2); margin-bottom: 3px; }
pre { font-family: var(--mono); font-size: 13.6px; line-height: 17px; white-space: pre; }
pre.big { font-size: 15px; line-height: 19px; }
.cmd { color: var(--ink); font-weight: 600; }
.ps { color: var(--muted); }
.k { color: var(--accent-2); }
.b { color: var(--counter); }
.n { color: var(--counter); }
.t { color: var(--ok); }
.dim { color: var(--muted); }
.gap { color: var(--muted); font-style: italic; }
.exit { color: var(--ok); font-size: 12px; }
.progress { display: flex; gap: 4px; margin-top: 8px; }
.progress span { flex: 1; height: 3px; background: var(--line); }
.progress span.on { background: var(--accent); }
"""


class DemoError(Exception):
    """Raised when the release or its captured output is not as expected."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DemoError(message)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    done = subprocess.run(
        argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=600
    )
    require(
        done.returncode == 0,
        f"{argv[0]} failed ({done.returncode}): {done.stderr or done.stdout}",
    )
    return done.stdout


class Shell:
    """Runs displayed commands verbatim with bash; output is stdout+stderr."""

    def __init__(self, work: Path, env: dict[str, str]) -> None:
        self.work, self.env, self.log = work, env, []

    def __call__(self, command: str) -> dict:
        done = subprocess.run(
            ["bash", "-c", command],
            cwd=self.work,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=120,
        )
        record = {"command": command, "exit": done.returncode, "output": done.stdout}
        self.log.append(record)
        require(done.returncode == 0, f"command failed: {command}\n{done.stdout}")
        return record


# ---------------------------------------------------------------- rendering


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def gap(count: int) -> str:
    return f'<span class="gap">… {count} line{"s" if count != 1 else ""} not shown …</span>'


def excerpt(lines: list[str], keep: list[tuple[int, int]], paint) -> str:
    """Render 0-based half-open line ranges, marking each skipped run."""
    out, cursor = [], 0
    for start, end in keep:
        if start > cursor:
            out.append(gap(start - cursor))
        out.extend(paint(line) for line in lines[start:end])
        cursor = end
    if cursor < len(lines):
        out.append(gap(len(lines) - cursor))
    return "\n".join(out)


def paint_mime(line: str) -> str:
    if line.startswith("--"):
        return f'<span class="b">{esc(line)}</span>'
    match = re.match(r"^([A-Za-z-]+):(.*)$", line)
    if match:
        return f'<span class="k">{esc(match[1])}:</span>{esc(match[2])}'
    if re.fullmatch(r"[A-Za-z0-9+/=]{16,}", line):
        return f'<span class="b">{esc(line)}</span>'
    return esc(line)


def paint_md(line: str) -> str:
    if line == "---":
        return f'<span class="dim">{esc(line)}</span>'
    match = re.match(r"^([a-z_]+):(.*)$", line)
    if match:
        return f'<span class="k">{esc(match[1])}:</span>{esc(match[2])}'
    return esc(line)


def paint_json(line: str) -> str:
    def value(text: str) -> str:
        bare = text.rstrip(",")
        tail = text[len(bare) :]
        if re.fullmatch(r"-?\d+(\.\d+)?", bare):
            return f'<span class="n">{esc(bare)}</span>{tail}'
        if bare in ("true", "false", "null"):
            return f'<span class="t">{bare}</span>{tail}'
        return esc(text)

    match = re.match(r'^(\s*)("[^"]+")(:\s*)(.*)$', line)
    if match:
        return f'{match[1]}<span class="k">{esc(match[2])}</span>{match[3]}{value(match[4])}'
    return esc(line)


def prompt(record: dict, body: str | None = None) -> str:
    lines = record["command"].split("\n")
    shown = [f'<span class="ps">$</span> <span class="cmd">{esc(lines[0])}</span>']
    shown += [f'<span class="cmd">{esc(line)}</span>' for line in lines[1:]]
    output = record["output"].rstrip("\n") if body is None else body
    if output:
        shown.append(output if body is not None else esc(output))
    return "\n".join(shown)


def pane(
    body: str, *, label: str | None = None, width: str = "auto", big: bool = False
) -> dict:
    return {"body": body, "label": label, "width": width, "big": big}


def page(
    step: int, title: str, sub: str, rows: list[list[dict]], icon: str, version: str
) -> str:
    bars = "".join(
        f'<span class="{"on" if i <= step else ""}"></span>' for i in range(1, 6)
    )
    rows_html = "".join(
        '<div class="row">'
        + "".join(
            f'<div class="pane" style="flex:1 1 {p["width"]}">'
            + (f'<div class="label">{esc(p["label"])}</div>' if p["label"] else "")
            + f'<pre class="{"big" if p["big"] else ""}">{p["body"]}</pre></div>'
            for p in row
        )
        + "</div>"
        for row in rows
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<div class="top"><img src="data:image/png;base64,{icon}" alt=""><h1>{esc(title)}</h1>
<span class="chip">dead-letter {esc(version)} · PyPI</span><span class="step">{step}/5</span></div>
<div class="sub">{esc(sub)}</div>
<div class="rows">{rows_html}</div>
<div class="progress">{bars}</div></body></html>"""


RENDER_JS = r"""
const { chromium } = require('playwright');
const jobs = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
(async () => {
  const browser = await chromium.launch();
  const problems = [];
  for (const scale of [1, 2]) {
    const context = await browser.newContext({ viewport: { width: %d, height: %d }, deviceScaleFactor: scale });
    const page = await context.newPage();
    for (const job of jobs) {
      await page.goto('file://' + job.html);
      await page.evaluate(() => document.fonts.ready);
      if (scale === 1) {
        const overflow = await page.evaluate(() => [...document.querySelectorAll('.pane, body')]
          .filter(el => el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1)
          .map(el => (el.querySelector('.label') || el).textContent.slice(0, 40)));
        if (overflow.length) problems.push(job.html + ': ' + overflow.join(', '));
      }
      await page.screenshot({ path: job.html.replace(/\.html$/, `@${scale}x.png`) });
    }
    await context.close();
  }
  await browser.close();
  if (problems.length) { console.error('overflow:\n' + problems.join('\n')); process.exit(1); }
})().catch(err => { console.error(err); process.exit(1); });
""" % (WIDTH, HEIGHT)


# ---------------------------------------------------------------- capture


def install_release(
    work: Path, version: str, exclude_newer: str, python: str
) -> tuple[Path, dict]:
    venv = work / "venv"
    run(["uv", "venv", "--quiet", "--python", python, str(venv)], cwd=work)
    bin_dir = venv / ("Scripts" if os.name == "nt" else "bin")
    interpreter = bin_dir / "python"
    run(
        [
            "uv",
            "pip",
            "install",
            "--quiet",
            "--python",
            str(interpreter),
            "--exclude-newer",
            exclude_newer,
            f"dead-letter=={version}",
        ],
        cwd=work,
    )
    probe = run(
        [
            str(interpreter),
            "-I",
            "-c",
            "import dead_letter, platform; print(dead_letter.__version__); "
            "print(dead_letter.__file__); print(platform.python_version())",
        ],
        cwd=work,
    ).splitlines()
    require(probe[0] == version, f"expected dead-letter {version}, imported {probe[0]}")
    require(
        not Path(probe[1]).resolve().is_relative_to(ROOT),
        "import resolved to the checkout",
    )
    freeze = run(
        ["uv", "pip", "freeze", "--python", str(interpreter)], cwd=work
    ).splitlines()
    return bin_dir, {
        "package": f"dead-letter=={version}",
        "python": probe[2],
        "uv": run(["uv", "--version"], cwd=work).strip(),
        "exclude_newer": exclude_newer,
        "resolved": freeze,
    }


def call_diagnostics(bin_dir: Path, work: Path, env: dict[str, str]) -> dict:
    process = subprocess.Popen(
        [str(bin_dir / "dead-letter-mcp")],
        cwd=work,
        env=env,
        text=True,
        encoding="utf-8",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        client = StdioClient(process)
        client.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "dead-letter-demo", "version": "1"},
            },
        )
        client.notify("notifications/initialized")
        result = client.request(
            "tools/call",
            {
                "name": "get_diagnostics",
                "arguments": {"eml_path": str(work / CLIENT)},
            },
        )
        require(
            not result.get("isError"), f"get_diagnostics failed: {result_text(result)}"
        )
        return {
            "tool": "get_diagnostics",
            "eml_path": f"<work>/{CLIENT}",
            "text": result_text(result),
        }
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def capture(work: Path, bin_dir: Path) -> dict:
    shutil.copytree(FIXTURES, work / "inbox")
    sources = sorted(
        p.relative_to(work).as_posix() for p in (work / "inbox").rglob("*.eml")
    )
    before = {name: sha(work / name) for name in sources}
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME"}
    }
    env.update(
        PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}", LC_ALL="C", COLUMNS="80"
    )
    sh = Shell(work, env)

    raw = sh(f"cat {CLIENT}")
    inputs = sh("find inbox -type f | sort")
    convert = sh("dead-letter convert inbox --output vault")
    tree = sh("find vault -type f | sort")
    markdown = sh("cat vault/clients/cedar-works-order-4821-revised-delivery.md")
    bundle = sh(
        "python - <<'PY'\n"
        "from dead_letter import convert_to_bundle\n"
        f'r = convert_to_bundle("{CLIENT}",\n'
        '                      bundle_root="Cabinet", source_handling="copy")\n'
        "print(r.success, r.bundle.name, len(r.attachments))\n"
        "PY"
    )
    cabinet = sh("find Cabinet -type f | sort")
    csv = sh("cat Cabinet/order-update/attachments/order-4821.csv")
    kept = sh("ls inbox/clients")
    report_run = sh(f"dead-letter convert {CLIENT} --output audit --report")
    report = sh("cat audit/.dead-letter-report.json")
    diagnostics = call_diagnostics(bin_dir, work, env)

    require(inputs["output"].split() == sources, "fixture inbox changed")
    require(
        convert["output"] == "" and report_run["output"] == "",
        "conversion printed unexpected output",
    )
    require(
        tree["output"].split()
        == [
            "vault/clients/cedar-works-order-4821-revised-delivery.md",
            "vault/ops/loading-window-for-cedar-works.md",
        ],
        "folder conversion layout changed",
    )
    require(
        markdown["output"].startswith("---\n")
        and "subject: Cedar Works order 4821" in markdown["output"],
        "Markdown front matter missing",
    )
    require(bundle["output"].strip() == "True order-update 1", "bundle result changed")
    require(
        csv["output"] == "sku,quantity\nnotebook,24\npen,48\n",
        "decoded CSV bytes changed",
    )
    require(
        sha(work / "Cabinet/order-update/order-update.eml") == before[CLIENT],
        "bundled source differs",
    )
    require(
        kept["output"].split() == ["order-update.eml"],
        "copy-only bundle moved the source",
    )
    parsed = json.loads(report["output"])
    require(
        parsed["summary"] == {"total": 1, "written": 1, "skipped": 0, "errors": 0},
        "report summary changed",
    )
    parsed_diag = json.loads(diagnostics["text"])
    require(
        parsed_diag["attachments"] == {"referenced": 1, "retained": 1},
        "diagnostics attachment counts changed",
    )
    require(
        {name: sha(work / name) for name in sources} == before, "source mail changed"
    )
    return {
        "commands": sh.log,
        "mcp": diagnostics,
        "source_sha256": before,
        "scenes": dict(
            raw=raw,
            inputs=inputs,
            convert=convert,
            tree=tree,
            markdown=markdown,
            bundle=bundle,
            cabinet=cabinet,
            csv=csv,
            kept=kept,
            report_run=report_run,
            report=report,
        ),
    }


def block_range(lines: list[str], key: str) -> tuple[int, int]:
    """Half-open range of a top-level JSON member that spans several lines."""
    start = next(i for i, line in enumerate(lines) if line.startswith(f'  "{key}"'))
    end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("  }"))
    return start, end + 1


def scenes(data: dict, version: str, icon: str) -> list[str]:
    s = data["scenes"]
    raw_lines = s["raw"]["output"].rstrip("\n").split("\n")
    # Show the headers and both MIME part headers; the plain-text body reappears in scene 3.
    plain = next(
        i
        for i, line in enumerate(raw_lines)
        if line.startswith("Content-Type: text/plain")
    )
    attach_start = next(
        i for i in range(plain + 1, len(raw_lines)) if raw_lines[i].startswith("--")
    )
    raw_body = prompt(
        s["raw"],
        excerpt(
            raw_lines, [(0, plain + 1), (attach_start, len(raw_lines))], paint_mime
        ),
    )

    convert_body = "\n\n".join(
        [
            prompt(s["inputs"]),
            prompt(
                s["convert"],
                f'<span class="exit">exit {s["convert"]["exit"]} · no terminal output</span>',
            ),
            prompt(s["tree"]),
        ]
    )

    md_lines = s["markdown"]["output"].rstrip("\n").split("\n")
    md_body = prompt(s["markdown"], "\n".join(paint_md(line) for line in md_lines))

    bundle_body = "\n\n".join(
        [
            prompt(s["bundle"]),
            prompt(s["cabinet"]),
            prompt(s["csv"]),
            prompt(s["kept"]),
        ]
    )

    report_lines = s["report"]["output"].rstrip("\n").split("\n")
    report_body = prompt(
        s["report"],
        excerpt(
            report_lines,
            [
                (0, block_range(report_lines, "generator")[1]),
                block_range(report_lines, "summary"),
            ],
            paint_json,
        ),
    )
    diag_lines = data["mcp"]["text"].rstrip("\n").split("\n")
    diag_body = "\n".join(paint_json(line) for line in diag_lines)
    run_line = prompt(
        s["report_run"], f'<span class="exit">exit {s["report_run"]["exit"]}</span>'
    )

    return [
        page(
            1,
            "Messy input: a raw .eml file",
            "MIME headers, multipart boundaries and a base64 CSV attachment (synthetic fixture)",
            [[pane(raw_body)]],
            icon,
            version,
        ),
        page(
            2,
            "Convert a folder with the released CLI",
            "Two messages in clients/ and ops/; the subfolders are mirrored in the output",
            [[pane(convert_body, big=True)]],
            icon,
            version,
        ),
        page(
            3,
            "Markdown with YAML front matter",
            "Headers become metadata, the date is normalized to UTC, the body stays readable",
            [[pane(md_body, big=True)]],
            icon,
            version,
        ),
        page(
            4,
            "Copy-only Cabinet bundle with the decoded CSV",
            'Python API with source_handling="copy": the source .eml stays in inbox/',
            [[pane(bundle_body)]],
            icon,
            version,
        ),
        page(
            5,
            "Report and quality diagnostics",
            "CLI --report records the run; MCP get_diagnostics inspects the same message",
            [
                [pane(run_line)],
                [
                    pane(report_body, width="300px"),
                    pane(
                        diag_body,
                        label="MCP get_diagnostics(eml_path=…/order-update.eml)",
                        width="400px",
                    ),
                ],
            ],
            icon,
            version,
        ),
    ]


# ---------------------------------------------------------------- encode


def concat_list(frames: list[Path], path: Path) -> Path:
    lines = []
    for frame, seconds in zip(frames, SCENE_SECONDS):
        lines += [f"file '{frame.as_posix()}'", f"duration {seconds}"]
    # The concat demuxer honours the last duration only when the final file repeats.
    lines.append(f"file '{frames[-1].as_posix()}'")
    path.write_text("\n".join(lines) + "\n")
    return path


def encode(
    frames_1x: list[Path], frames_2x: list[Path], temp: Path, out: Path
) -> dict[str, Path]:
    ffmpeg = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    mp4, gif, png = out / f"{STEM}.mp4", out / f"{STEM}.gif", out / f"{STEM}.png"
    run(
        ffmpeg
        + [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list(frames_2x, temp / "hi.txt")),
            "-vf",
            "fps=30,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "18",
            "-tune",
            "stillimage",
            "-movflags",
            "+faststart",
            str(mp4),
        ],
        cwd=temp,
    )
    run(
        ffmpeg
        + [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list(frames_1x, temp / "lo.txt")),
            "-filter_complex",
            "fps=4,split[a][b];[a]palettegen=max_colors=256:stats_mode=full[p];[b][p]paletteuse=dither=none",
            "-loop",
            "0",
            str(gif),
        ],
        cwd=temp,
    )
    shutil.copyfile(frames_2x[2], png)
    return {"mp4": mp4, "gif": gif, "png": png}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--version", default=DEFAULT_VERSION, help="released dead-letter version"
    )
    parser.add_argument(
        "--exclude-newer", default=DEFAULT_EXCLUDE_NEWER, help="uv resolution cutoff"
    )
    parser.add_argument(
        "--python", default="3.12", help="Python request passed to uv venv"
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="asset directory")
    parser.add_argument(
        "--keep-work",
        action="store_true",
        help="keep and print the temporary directory",
    )
    args = parser.parse_args()

    missing = [
        tool
        for tool in ("bash", "find", "sort", "uv", "node", "ffmpeg")
        if not shutil.which(tool)
    ]
    if missing:
        print(f"missing tools: {', '.join(missing)}", file=sys.stderr)
        return 2
    if subprocess.run(
        ["node", "-e", "require('playwright')"], capture_output=True
    ).returncode:
        print("node cannot load the playwright package", file=sys.stderr)
        return 2

    temp = Path(tempfile.mkdtemp(prefix="dead-letter-demo-"))
    try:
        work = temp / "work"
        work.mkdir()
        bin_dir, release = install_release(
            work, args.version, args.exclude_newer, args.python
        )
        data = capture(work, bin_dir)
        icon = base64.b64encode(FAVICON.read_bytes()).decode()
        pages = scenes(data, args.version, icon)
        jobs = []
        for index, markup in enumerate(pages, 1):
            target = temp / f"scene{index}.html"
            target.write_text(markup, encoding="utf-8")
            jobs.append({"html": str(target)})
        (temp / "jobs.json").write_text(json.dumps(jobs))
        (temp / "render.js").write_text(RENDER_JS)
        run(["node", str(temp / "render.js"), str(temp / "jobs.json")], cwd=temp)
        args.out.mkdir(parents=True, exist_ok=True)
        assets = encode(
            [temp / f"scene{i}@1x.png" for i in range(1, 6)],
            [temp / f"scene{i}@2x.png" for i in range(1, 6)],
            temp,
            args.out,
        )

        def fonts(name: str) -> str:
            if not shutil.which("fc-match"):
                return "unknown (fc-match unavailable)"
            return run(["fc-match", name], cwd=temp).strip()

        record = {
            "release": release,
            "host": {
                "platform": platform.platform(),
                "node": run(["node", "--version"], cwd=temp).strip(),
                "ffmpeg": run(["ffmpeg", "-version"], cwd=temp).splitlines()[0],
                "monospace_fallback": fonts("monospace"),
                "sans": fonts("Inter"),
            },
            "scene_seconds": list(SCENE_SECONDS),
            "source_sha256": data["source_sha256"],
            "commands": data["commands"],
            "mcp": data["mcp"],
            "assets": {
                kind: {"file": path.name, "bytes": path.stat().st_size}
                for kind, path in assets.items()
            },
        }
        text = json.dumps(record, indent=2) + "\n"
        (args.out / "capture.json").write_text(
            text.replace(str(work), "<work>"), encoding="utf-8"
        )
        print(json.dumps(record["assets"], indent=2))
    except (
        DemoError,
        SmokeFailure,
        OSError,
        subprocess.SubprocessError,
        KeyError,
        ValueError,
        StopIteration,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    finally:
        if args.keep_work:
            print(f"work directory kept: {temp}", file=sys.stderr)
        else:
            shutil.rmtree(temp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
