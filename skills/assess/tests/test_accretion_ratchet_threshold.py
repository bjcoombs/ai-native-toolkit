"""Regression: the caller's --deletion-threshold is the cut actually applied.

`_build_accretion_file` once hard-coded the module constant
(DELETION_FRACTION_THRESHOLD = 0.15) for its deletion-fraction filter, while
`scan_accretion_ratchet` re-filtered on, and reported, the caller's
`deletion_threshold`. The effective cut was min(0.15, deletion_threshold), so any
caller-supplied threshold above 0.15 was silently ignored - the API contradicted
its reported `deletion_fraction_threshold`. This pins the contract: a file at
deletion fraction 0.20 is admitted under `--deletion-threshold 0.30` and dropped
under the 0.15 default.

The comprehensive suite is owned by a separate task; this is the focused
regression only, in a minimally-named module to avoid colliding with it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.accretion_ratchet import (  # noqa: E402
    _FileHistory,
    _build_accretion_file,
)


def _history_at_fraction_020() -> _FileHistory:
    """A monotonically-growing file whose deletion fraction is exactly 0.20.

    Three appending commits (multi-commit gate cleared), running net-delta never
    falls back (monotonic), 80 additions to 20 deletions => 20/100 = 0.20.
    """
    return _FileHistory(
        additions=80,
        deletions=20,
        commit_count=3,
        first_time=1_000,
        last_time=1_000 + 86_400,
        net_sequence=[20, 40, 60],
    )


def test_threshold_above_fraction_admits_file() -> None:
    """--deletion-threshold 0.30 admits a 0.20-fraction file (old code dropped it)."""
    result = _build_accretion_file("grower.py", _history_at_fraction_020(), 0.30)
    assert result is not None
    assert result.path == "grower.py"
    assert result.deletion_fraction == 0.20


def test_default_threshold_drops_same_file() -> None:
    """At the 0.15 default the same file is correctly excluded (0.20 >= 0.15)."""
    result = _build_accretion_file("grower.py", _history_at_fraction_020(), 0.15)
    assert result is None
