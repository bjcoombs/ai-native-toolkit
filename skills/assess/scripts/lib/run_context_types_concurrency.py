"""The run context's ``concurrency`` block (``lib/history/concurrency.py``, #985).

Kept in its own module because ``run_context_types.py`` sits at its size ceiling.
Tests: ``tests/test_concurrency.py``.
"""
from __future__ import annotations

from typing import Literal, TypedDict


class HotFile(TypedDict):
    """A file at or above the touch-share threshold. Full output only: counts-only drops the list."""

    path: str
    merges: int  # sampled first-parent commits that touched it
    touch_share: float  # merges / the block's merges
    generated: bool  # a linguist-generated attribute, a generated name or a generator header
    fan_in: int  # distinct top-level folders of the other files those merges changed


class ConcurrencyBlock(TypedDict):
    """How often the same files change together across merges: the parallel-change pressure."""

    available: Literal[True]
    window: str
    since: str | None
    informational: Literal[True]
    merge_sample: int  # MERGE_SAMPLE: the newest first-parent commits read
    merges: int
    threshold: float  # TOUCH_SHARE_THRESHOLD
    files_touched: int
    files_above_threshold: int
    max_touch_share: float
    median_files_per_merge: float
    generated_files_touched: int
    generated_files_above_threshold: int  # generated churn: generated files at the threshold
    generated_diff_share: float | None  # generated file touches / all file touches
    generated_merge_share: float  # merges that touched a generated file
    max_fan_in: int | None
    fan_in_null_reason: str | None  # "none_above_threshold" when max_fan_in is null
    hot_files: list[HotFile]  # the top TOP_FILES by touch share; dropped in counts-only output
    limits: list[str]


class ConcurrencyUnavailable(TypedDict):
    available: Literal[False]
    reason: str
