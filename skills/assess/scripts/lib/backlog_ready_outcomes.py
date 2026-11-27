"""Backlog kept agent-ready, level 2: three outcome metrics from the issue tracker (#1323).

Level 1 (``lib.backlog_ready``) reads intake configuration, which shows intent. Level 2 reads
whether issues are answered, triaged and closed at the pace they open, from the platform
(``lib.hosting.issue_outcomes``, GitHub and GitLab), against thresholds the team sets in
``[backlog_ready]`` (``assess_config.load_backlog_ready_thresholds``). The tendency it compensates
for is unactioned intent at the tracker: a ready label and a form nobody acts on.

Metric definitions follow CHAOSS (Time to First Response; Issues New and Issues Closed). Over a
window of ``window_days`` (default 90) ending at the run time:

- ``first_response_median_days``: the median, over issues opened in the window that got a
  response, of the days from opening to the first comment, label event or close by an account
  that is neither the author nor a bot (``issue_outcomes`` says which events each host gives).
  Issues with no response are counted in ``outcomes.no_response_count``, never dropped; issues
  whose response the read could not settle are ``outcomes.response_unread``. Met when at most
  ``first_response_max_days`` (default 2).
- ``untriaged_after_7d_share``: among open issues older than 7 days, the share with no label or
  an untriaged label (``[backlog_ready] untriaged_labels``, else ``backlog_ready.UNTRIAGED_LABELS``).
  Met when at most ``untriaged_max_share`` (default 0.10).
- ``close_open_ratio_90d``: issues closed in the window over issues opened in it. Met when at
  least ``close_open_min_ratio`` (default 0.9).

Each metric is null with a ``<metric>_reason`` code when it is not measured: the read's code
(``no_network``, ``no_platform_auth``, ``unsupported_host``, ``platform_error``), or
``empty_window`` when its denominator is 0. One exception keeps an ignored tracker from reading
not measured: when issues opened in the window got no response at all and each of them has waited
longer than ``first_response_max_days``, the median is null with ``no_response`` and
``first_response_met`` is false, since any later response is already past the threshold. Each
``<x>_met`` bool is null whenever its metric is not measured: not measured is never unmet.

Block fields this module adds to ``backlog_ready``::

    {"first_response_median_days": float | None, "untriaged_after_7d_share": float | None,
     "close_open_ratio_90d": float | None, "<metric>_reason": str (beside a null metric),
     "first_response_met": bool | None, "untriaged_met": bool | None, "close_open_met": bool | None,
     "outcomes": {"available": True, "window_days": int, "opened": int, "closed": int,
                  "responded": int, "no_response_count": int, "response_unread": int,
                  "open_after_7d": int, "untriaged_after_7d": int, "truncated": bool,
                  "thresholds_from_config": bool}
                 | {"available": False, "reason": str}}

Numbers and reason codes only: no issue title, body, number, URL or login. ``truncated`` is true
when a page cap was hit; the counts read are still measured, and the report says so.

`scan_backlog_ready_outcomes(repo_root, backlog_outcomes)` is the ``backlog_ready`` registry
callable: `lib.backlog_ready.scan_backlog_ready` plus `outcome_fields(read, thresholds,
from_config, untriaged)`. The level 2 criteria read the three `_met` bools (all `platform`
evidence, graded verified). Tests: `tests/test_backlog_ready_platform.py`.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, NotRequired, TypedDict

from lib.assess_config import BacklogThresholds, load_backlog_ready_labels, load_backlog_ready_thresholds
from lib.backlog_ready import READY_LABELS, UNTRIAGED_LABELS, BacklogReadyBlock, scan_backlog_ready
from lib.hosting.issue_outcomes import IssueRead, OutcomeRead, PLATFORM_ERROR

EMPTY_WINDOW = "empty_window"
NO_RESPONSE = "no_response"
METRICS = ("first_response_median_days", "untriaged_after_7d_share", "close_open_ratio_90d")
_DAY = 86400.0


class BacklogOutcomesCounts(TypedDict, total=False):
    """``backlog_ready.outcomes`` (see the module doc)."""

    available: bool
    reason: str
    window_days: int
    opened: int
    closed: int
    responded: int
    no_response_count: int
    response_unread: int
    open_after_7d: int
    untriaged_after_7d: int
    truncated: bool
    thresholds_from_config: bool


class BacklogReadyOutcomesBlock(BacklogReadyBlock):
    """``run-context.json`` ``backlog_ready`` with the level 2 fields (see the module doc)."""

    first_response_median_days: float | None
    first_response_median_days_reason: NotRequired[str]
    untriaged_after_7d_share: float | None
    untriaged_after_7d_share_reason: NotRequired[str]
    close_open_ratio_90d: float | None
    close_open_ratio_90d_reason: NotRequired[str]
    first_response_met: bool | None
    untriaged_met: bool | None
    close_open_met: bool | None
    outcomes: BacklogOutcomesCounts


def _days(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / _DAY


def _unmeasured(code: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for metric in METRICS:
        fields[metric] = None
        fields[f"{metric}_reason"] = code
    fields.update(first_response_met=None, untriaged_met=None, close_open_met=None,
                  outcomes={"available": False, "reason": code})
    return fields


def outcome_fields(read: OutcomeRead, thresholds: BacklogThresholds, from_config: bool,
                   untriaged: tuple[str, ...]) -> dict[str, Any]:
    """The level 2 fields of the ``backlog_ready`` block (see the module doc)."""
    if not isinstance(read, IssueRead):
        code = read.get("reason") if isinstance(read, dict) else None
        return _unmeasured(code if isinstance(code, str) and code else PLATFORM_ERROR)
    opened = [i for i in read.recent if i.created >= read.start]
    closed = sum(1 for i in read.recent if i.closed is not None and i.closed >= read.start)
    waits = [_days(i.created, i.first_response) for i in opened if i.response_read and i.first_response is not None]
    silent = [i for i in opened if i.response_read and i.first_response is None]
    unread = sum(1 for i in opened if not i.response_read)
    marks = {label.lower() for label in untriaged}
    untriaged_n = sum(1 for i in read.open_old if not i.labels or marks & set(i.labels))
    fields: dict[str, Any] = {}

    if waits:
        fields["first_response_median_days"] = round(median(waits), 2)
        fields["first_response_met"] = median(waits) <= thresholds.first_response_max_days
    elif silent and not unread and all(_days(i.created, read.now) > thresholds.first_response_max_days
                                       for i in silent):
        fields.update(first_response_median_days=None, first_response_median_days_reason=NO_RESPONSE,
                      first_response_met=False)
    else:
        fields.update(first_response_median_days=None, first_response_median_days_reason=EMPTY_WINDOW,
                      first_response_met=None)

    if read.open_old:
        share = untriaged_n / len(read.open_old)
        fields["untriaged_after_7d_share"] = round(share, 4)
        fields["untriaged_met"] = share <= thresholds.untriaged_max_share
    else:
        fields.update(untriaged_after_7d_share=None, untriaged_after_7d_share_reason=EMPTY_WINDOW,
                      untriaged_met=None)

    if opened:
        ratio = closed / len(opened)
        fields["close_open_ratio_90d"] = round(ratio, 4)
        fields["close_open_met"] = ratio >= thresholds.close_open_min_ratio
    else:
        fields.update(close_open_ratio_90d=None, close_open_ratio_90d_reason=EMPTY_WINDOW,
                      close_open_met=None)

    fields["outcomes"] = {
        "available": True, "window_days": thresholds.window_days, "opened": len(opened),
        "closed": closed, "responded": len(waits), "no_response_count": len(silent),
        "response_unread": unread, "open_after_7d": len(read.open_old),
        "untriaged_after_7d": untriaged_n, "truncated": read.truncated,
        "thresholds_from_config": from_config,
    }
    return fields


def scan_backlog_ready_outcomes(repo_root: Path, backlog_outcomes: OutcomeRead) -> BacklogReadyOutcomesBlock:
    """The ``backlog_ready`` block: level 1 from committed files, level 2 from the tracker read."""
    block = scan_backlog_ready(repo_root)
    thresholds, from_config = load_backlog_ready_thresholds(repo_root)
    untriaged, _, _ = load_backlog_ready_labels(repo_root, UNTRIAGED_LABELS, READY_LABELS)
    out: dict[str, Any] = {**block, **outcome_fields(backlog_outcomes, thresholds, from_config, untriaged)}
    return out  # type: ignore[return-value]
