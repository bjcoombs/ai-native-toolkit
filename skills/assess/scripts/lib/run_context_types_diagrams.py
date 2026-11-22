"""Shapes of the /assess diagrams (``lib/assess_diagrams.py``, #942).

Kept apart from ``lib/run_context_types.py``, which is at its file-size ceiling.
"""
from __future__ import annotations

from typing import TypedDict


class AssessDiagram(TypedDict):
    """One diagram the run draws into ``.assess/``, or why it was not drawn."""

    file: str  # the SVG file name in the artifact directory
    title: str  # the report heading
    in_place: str  # the section's opening line: what this run already has (#743)
    caption: str  # one line under the diagram: what to take from it
    svg: str | None  # None when not drawn
    skipped: str | None  # the one-line reason, when not drawn


class WaffleSquare(TypedDict):
    """One condition of the level result, as the evidence waffle draws it."""

    id: str  # the condition id, ``<row id>.<level>``
    row: str
    level: int
    category: str  # one of ``assess_diagrams.CATEGORIES``
    reason: str | None  # the not-measured reason code, else None
