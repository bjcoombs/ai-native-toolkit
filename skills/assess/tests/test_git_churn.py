"""Contract tests for the churn-degeneracy detector (issue #172).

`churn_is_degenerate` is the single source of truth that lets every downstream
consumer (doc->complexity join, keyhole summary, treemap) tell a meaningless
churn signal from a real one. A degenerate window - every file ~1 commit, the
fingerprint of a shallow clone / fresh import / squashed or extracted history -
must be flagged so a precise doc->code association can no longer stamp a
high-confidence finding onto pure extraction artifact.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.git_churn import churn_is_degenerate  # noqa: E402


def test_all_ones_is_degenerate() -> None:
    """The canonical artifact: every file shows exactly one commit."""
    assert churn_is_degenerate([1] * 1979) is True


def test_single_outlier_among_ones_still_degenerate() -> None:
    """One genuinely-churned file among thousands of single-commit files does
    not rescue the signal - p95 (not max) reports on the flat bulk."""
    counts = [1] * 1978 + [50]
    assert churn_is_degenerate(counts) is True


def test_zero_commit_files_are_ignored() -> None:
    """Idle files (0 commits in the window) don't count toward the distribution:
    degeneracy is the shape of the activity among files that actually moved."""
    counts = [0] * 500 + [1] * 10
    assert churn_is_degenerate(counts) is True


def test_genuine_variance_is_not_degenerate() -> None:
    """A real history with a spread of commits-per-file carries signal."""
    counts = list(range(1, 60))  # 1..59, p95 well above 1
    assert churn_is_degenerate(counts) is False


def test_uniform_high_count_is_not_degenerate() -> None:
    """Flat-but-active (every file committed several times) still has p95 > 1, so
    it is not the single-commit artifact this guards against."""
    assert churn_is_degenerate([5] * 100) is False


def test_too_few_active_files_is_not_called() -> None:
    """Below the minimum active-file count the distribution is too small to judge
    - a tiny utility repo where each file shows one commit is not the
    shallow-clone artifact, so we don't flatten its churn signal."""
    assert churn_is_degenerate([1, 1, 1]) is False
    assert churn_is_degenerate([]) is False


def test_at_minimum_active_files_all_ones_is_degenerate() -> None:
    """Exactly at the threshold, an all-ones distribution is degenerate."""
    assert churn_is_degenerate([1, 1, 1, 1, 1]) is True


def test_accepts_any_iterable() -> None:
    """Consumers pass a generator of churn-map values; the detector consumes it
    once without requiring a materialised list."""
    assert churn_is_degenerate(c for c in [1, 1, 1, 1, 1, 1]) is True
