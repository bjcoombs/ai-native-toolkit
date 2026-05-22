"""Smoke test: verify the test harness runs and lib is importable."""
from __future__ import annotations

from lib import __version__


def test_lib_importable() -> None:
    assert __version__ == "0.1.0"


def test_fixtures_dir_exists(fixtures_dir):
    # fixtures dir doesn't need contents yet; just verify the fixture works
    assert fixtures_dir.parent.name == "tests"
