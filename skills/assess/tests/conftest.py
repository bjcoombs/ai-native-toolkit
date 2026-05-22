"""Shared pytest fixtures."""
from __future__ import annotations
from pathlib import Path

import pytest


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to tests/fixtures/."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def tmp_assess_dir(tmp_path: Path) -> Path:
    """A clean .assess/ directory in a temp location."""
    assess_dir = tmp_path / ".assess"
    assess_dir.mkdir()
    (assess_dir / "hotspots").mkdir()
    return assess_dir
