import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
    smoke = importlib.import_module("smoke_client_install")


class _Launched(Exception):
    pass


def _capture_env(monkeypatch):
    seen = {}

    def fake_popen(command, *, env, **kwargs):
        seen.update(env)
        raise _Launched

    monkeypatch.setattr(smoke.subprocess, "Popen", fake_popen)
    return seen


def test_default_uses_a_temporary_uv_cache(monkeypatch):
    monkeypatch.delenv("DEAD_LETTER_SMOKE_UV_CACHE_DIR", raising=False)
    seen = _capture_env(monkeypatch)
    with pytest.raises(_Launched):
        smoke.check(["uvx", "dead-letter-mcp"], environment={"PATH": "/usr/bin"})
    assert Path(seen["UV_CACHE_DIR"]).name == "uv-cache"
    assert "dead-letter-client-smoke-" in seen["UV_CACHE_DIR"]


def test_prune_drops_resolution_state_and_keeps_downloaded_files(tmp_path):
    for name in ("environments-v2", "simple-v27", "simple-v19", "wheels-v7", "archive-v0", "sdists-v10"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "entry").write_text("x", encoding="utf-8")
    (tmp_path / "CACHEDIR.TAG").write_text("tag", encoding="utf-8")
    smoke.prune_resolution_cache(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["CACHEDIR.TAG", "archive-v0", "sdists-v10", "wheels-v7"]


def test_prune_tolerates_a_missing_cache(tmp_path):
    smoke.prune_resolution_cache(tmp_path / "not-restored")


def test_shared_cache_is_pruned_before_launch(monkeypatch, tmp_path):
    cache = tmp_path / "shared-cache"
    (cache / "environments-v2" / "stale").mkdir(parents=True)
    (cache / "wheels-v7").mkdir()
    monkeypatch.setenv("DEAD_LETTER_SMOKE_UV_CACHE_DIR", str(cache))
    seen = _capture_env(monkeypatch)
    with pytest.raises(_Launched):
        smoke.check(["uvx", "dead-letter-mcp"], environment={"PATH": "/usr/bin"})
    assert seen["UV_CACHE_DIR"] == str(cache)
    assert not (cache / "environments-v2").exists()
    assert (cache / "wheels-v7").is_dir()


def test_ci_cache_override_survives_a_stripped_environment(monkeypatch, tmp_path):
    cache = tmp_path / "shared-cache"
    monkeypatch.setenv("DEAD_LETTER_SMOKE_UV_CACHE_DIR", str(cache))
    seen = _capture_env(monkeypatch)
    # Registration harnesses pass an allowlisted environment without the override.
    with pytest.raises(_Launched):
        smoke.check(["uvx", "dead-letter-mcp"], environment={"PATH": "/usr/bin"})
    assert seen["UV_CACHE_DIR"] == str(cache)
    assert seen["UV_NO_CONFIG"] == "1"
