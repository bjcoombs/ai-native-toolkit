"""Shapes of the doc-drift map and the review strip (``lib/assess_diagrams_review.py``, #943).

Kept apart from ``lib/run_context_types_diagrams.py``, which #942 extends in parallel.
"""
from __future__ import annotations

from typing import TypedDict


class DriftBar(TypedDict):
    """One mapped doc as the doc-drift map draws it."""

    path: str  # the doc, repo-relative (hashed under counts-only hashing)
    subject: str  # its first subject root, "" when the row lists none
    more_roots: int  # subject roots beyond the first
    ratio: float  # ``doc_staleness`` ``ratio``: the bar length
    behind: bool  # ``doc_complexity_join.is_stale``: above the threshold, or a newer source
