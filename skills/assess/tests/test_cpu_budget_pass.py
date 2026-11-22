"""``cpu_budget`` tests never run inside a pytest-xdist worker (#930).

A busy worker on the sibling hardware thread roughly doubles a test's CPU
seconds, so an absolute CPU-time budget skips in the parallel pool and runs in
the serial ``-p no:xdist -m cpu_budget --no-cov`` pass instead (see
``CPU_BUDGET_SKIP`` in conftest.py). This test carries the marker itself, so
it fails the moment one runs in a worker.
"""
from __future__ import annotations

import os

import pytest


@pytest.mark.cpu_budget
def test_a_cpu_budget_test_runs_outside_the_xdist_pool() -> None:
    assert "PYTEST_XDIST_WORKER" not in os.environ
