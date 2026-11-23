"""Run-context shapes for the per-class file-size block, ``stats_summary.loc_by_class`` (#820).

A sibling of ``lib/run_context_types.py``, which is at its line budget. Built by
``lib/class_sizes.py``.
"""
from __future__ import annotations

from typing import TypedDict


class ClassLoc(TypedDict):
    """One path class's scored files and their size distribution in lines.

    ``p50``, ``p95`` and ``max`` are null when ``files_scored`` is 0: a
    percentile of no files is not a measurement.
    """

    files_scored: int
    p50: float | None
    p95: float | None
    max: float | None


class LocByClass(TypedDict):
    """``stats_summary.loc_by_class``: one ``ClassLoc`` per ``lib.path_classes`` class."""

    source: ClassLoc
    test: ClassLoc
    fixture: ClassLoc
    example: ClassLoc
    vendored: ClassLoc
    generated: ClassLoc
