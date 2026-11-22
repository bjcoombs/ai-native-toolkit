"""``no_cover`` pauses coverage in a pytest-xdist worker too (#930).

CI runs this suite under ``-n auto --cov``. pytest-cov starts two coverage
controllers in each xdist worker and its ``no_cover`` hook pauses only one, so
without the conftest hook the other keeps tracing, and a CPU-time budget such
as ``test_dart_scanner_pathological_input_is_fast`` runs about four times slower
than it does serially.
"""
from __future__ import annotations

import pytest


@pytest.mark.no_cover
def test_no_cover_leaves_no_coverage_running() -> None:
    coverage = pytest.importorskip("coverage")
    assert coverage.Coverage.current() is None


def test_coverage_resumes_after_a_no_cover_test(request: pytest.FixtureRequest) -> None:
    coverage = pytest.importorskip("coverage")
    if not request.config.pluginmanager.hasplugin("_cov") or request.config.getoption("no_cov"):
        pytest.skip("runs only under pytest-cov")
    assert coverage.Coverage.current() is not None


def test_the_no_cover_fixture_pauses_coverage_too(no_cover: None) -> None:
    """pytest-cov pauses for its ``no_cover`` fixture as well as the marker."""
    coverage = pytest.importorskip("coverage")
    assert coverage.Coverage.current() is None
