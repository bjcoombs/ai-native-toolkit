"""File size per path class, so the L4 percentile reads production source alone (#820).

``loc_by_class`` takes ``(repository-relative path, lines)`` for every scored
file, sorts each into its ``lib.path_classes`` class and returns
``stats_summary.loc_by_class``: per class, ``files_scored`` and the ``p50``,
``p95`` and ``max`` of its line counts. A class with no file reads 0 files and
null percentiles, never 0 lines: unknown reads null. Layer 4 level 2 reads
``loc_by_class.source.p95``, so many small test fixtures cannot hide a large
source file and large tests cannot fail a repository whose source passes. The
test-set figure is ``loc_by_class.test``; every class's ``files_scored``
shows the denominator. The scored set has already lost vendored and
generated files to the scans' own excludes, so those two classes usually read
0 files.

Both producers call it: the full run's sidecar (``complexity-treemap.py``
``write_stats``) and the headless block (``lib.run.headless_layers.stats_summary``).
``percentile`` is numpy's default (linear) rule, so the two agree. Tests:
``tests/test_class_sizes.py``.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import PurePath

from lib.path_classes import PATH_CLASSES, PathClass, path_class
from lib.run_context_types_size import ClassLoc, LocByClass


def percentile(values: Sequence[float], q: float) -> float:
    """``numpy.percentile``'s default (linear) interpolation; 0.0 when empty."""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = (len(ordered) - 1) * q / 100.0
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return float(ordered[low] + (ordered[high] - ordered[low]) * (rank - low))


def class_loc(values: Sequence[float]) -> ClassLoc:
    """``files_scored`` and the distribution of ``values``; null percentiles when empty."""
    if not values:
        return {"files_scored": 0, "p50": None, "p95": None, "max": None}
    return {"files_scored": len(values), "p50": percentile(values, 50),
            "p95": percentile(values, 95), "max": float(max(values))}


def loc_by_class(rows: Iterable[tuple[str | PurePath, float]]) -> LocByClass:
    """``stats_summary.loc_by_class`` from ``(relative path, lines)`` rows."""
    split: dict[PathClass, list[float]] = {name: [] for name in PATH_CLASSES}
    for rel, lines in rows:
        split[path_class(rel)].append(float(lines))
    return {
        "source": class_loc(split["source"]),
        "test": class_loc(split["test"]),
        "fixture": class_loc(split["fixture"]),
        "example": class_loc(split["example"]),
        "vendored": class_loc(split["vendored"]),
        "generated": class_loc(split["generated"]),
    }
