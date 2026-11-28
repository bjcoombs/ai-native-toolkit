"""Run-context shapes for the ``review_rules`` block (#856).

A sibling of ``lib/run_context_types.py``, which is at its line budget. Built by
``lib/ci/review_rules.py``.
"""
from __future__ import annotations

from typing import NotRequired, TypedDict


class ReviewRule(TypedDict):
    """One committed file that keeps a merge or review rule as code.

    ``arm`` is one of ``lib.ci.review_rules.ARMS``; ``path`` is the file, relative to the
    repository root (for ``review_rubric``, the rubric directory a workflow names).
    """

    arm: str
    path: str


class ReviewRulesBlock(TypedDict):
    """``review_rules``: merge and review rules kept as code, read offline.

    Evidence only: no condition reads it. ``lib.scoring.condition_evidence`` attaches ``arms``
    to the review condition when that condition is met, and the committed merge queue fills
    ``review_reality.merge_queue`` when the platform rules could not be read.
    """

    available: bool
    reason: NotRequired[str]   # git_tracking_unknown, or scan_failed when the reader raised
    present: bool | None       # an arm credited; None when nothing is and a file was unsettled
    arms: list[str]            # each credited arm once, in ARMS order
    rules: list[ReviewRule]    # one per credited file, in ARMS order, then path
    catch_all_owners: bool     # OWNERS files read, all naming the same single owner (#785 rule)
    unsettled: list[str]       # files the reader could not settle, in path order
