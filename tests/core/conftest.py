"""Shared pytest fixtures for dead_letter.core tests."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture
def fixture_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def copy_fixture(tmp_path: Path, fixture_dir: Path) -> Callable[[str, str | Path | None], Path]:
    def _copy(name: str, destination: str | Path | None = None) -> Path:
        source = fixture_dir / name
        target = tmp_path / (Path(destination) if destination is not None else Path(name))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return target

    return _copy
