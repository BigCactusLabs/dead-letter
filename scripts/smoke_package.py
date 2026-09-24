"""Install one exact built distribution into a clean venv outside the checkout."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from package_artifacts import validate, verify_checksums

ROOT = Path(__file__).resolve().parents[1]
SDK_TESTS = ("test_typesafe_provider.py", "test_analysis_contracts.py", "test_analysis_eml.py")


def isolated_environment() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT", "UV_PROJECT_ENVIRONMENT", "UV_WORKING_DIRECTORY"):
        env.pop(key, None)
    for key in tuple(env):
        if key.startswith("TYPESAFE_"):
            env.pop(key)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return env


def sdk_contracts(python: Path, work: Path, env: dict[str, str]) -> None:
    tests = work / "tests" / "backend"
    tests.mkdir(parents=True)
    for name in SDK_TESTS:
        shutil.copy2(ROOT / "tests" / "backend" / name, tests / name)
    fixture = tests / "fixtures" / "analysis_cases.json"
    fixture.parent.mkdir()
    shutil.copy2(ROOT / "tests" / "backend" / "fixtures" / fixture.name, fixture)
    subprocess.run(["uv", "--no-config", "pip", "install", "--python", str(python), "--no-sources", "pytest>=9.0.3"],
                   cwd=work, env=env, check=True, timeout=600)
    # This check precedes pytest's importorskip guards and checks the actual
    # venv import, so an absent or wrong SDK cannot become a green skip.
    subprocess.run([str(python), "-I", str(ROOT / "scripts/package_probe.py"), "--check-typesafe-only"],
                   cwd=work, env=env, check=True, timeout=30)
    subprocess.run([str(python), "-I", "-m", "pytest", "-q", "--import-mode=importlib",
                    *[str(tests / name) for name in SDK_TESTS]],
                   cwd=work, env=env, check=True, timeout=300)


def smoke(artifact: Path, version: str, extra: str) -> None:
    artifact = artifact.resolve()
    with tempfile.TemporaryDirectory(prefix="dead-letter-installed-") as temporary:
        root = Path(temporary).resolve()
        if root.is_relative_to(ROOT):
            raise ValueError("set TMPDIR outside the checkout before testing packaged installs")
        work = root / "empty-cwd"
        work.mkdir()
        venv = root / "venv"
        env = isolated_environment()
        python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        subprocess.run(["uv", "--no-config", "venv", "--python", sys.executable, str(venv)], cwd=work, env=env, check=True, timeout=120)
        requirement = f"dead-letter{f'[{extra}]' if extra != 'core' else ''} @ {artifact.as_uri()}"
        subprocess.run(["uv", "--no-config", "pip", "install", "--python", str(python), "--no-sources", requirement], cwd=work, env=env, check=True, timeout=600)
        subprocess.run([str(python), "-I", str(ROOT / "scripts/package_probe.py"), "--extra", extra,
                        "--version", version, "--artifact-url", artifact.as_uri()],
                       cwd=work, env=env, check=True, timeout=180)
        if extra == "typesafe":
            sdk_contracts(python, work, env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--checksums", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--profile", choices=("core", "cli", "mcp", "ui", "benchmark", "sdist", "typesafe", "sdist-typesafe"), required=True)
    args = parser.parse_args(argv)
    files = validate(args.dist_dir, args.version)
    verify_checksums(files, args.checksums)
    artifact = next(p for p in files if p.name.endswith(".tar.gz" if args.profile.startswith("sdist") else ".whl"))
    smoke(artifact, args.version, "core" if args.profile == "sdist" else "typesafe" if args.profile == "sdist-typesafe" else args.profile)
    # Detect accidental mutation during the smoke too.
    verify_checksums(files, args.checksums)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
