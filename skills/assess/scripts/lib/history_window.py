"""The one history-window grammar (issue #601).

A window is ``<n>d``, ``<n>w``, ``<n>mo`` or ``<n>y`` with ``n`` a positive
integer without leading zeros: ``90d``, ``12w``, ``6mo``, ``1y``. The headless
runner's ``--window`` (``signals``), the ``ai_usage`` scan's
``history_window``, the fleet activity cutoff (``fleet_activity``) and the
churn-label normaliser (``git_churn.window_from_churn_label``) all read windows
through this module, so the grammar is defined once.

``anchored_since`` turns a window into the absolute ``git log --since=@<epoch>``
bound counted back from the assessed commit's committer time (issue #773), so a
window never resolves against the clock of whoever runs the scan.
"""
from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta, timezone
from typing import Literal

WindowUnit = Literal["d", "w", "mo", "y"]

_COUNT = r"[1-9][0-9]*"
_UNIT = r"(?:d|w|mo|y)"

# The window grammar as a regex fragment (no anchors, no capture groups), for
# callers that find a window inside a longer string.
WINDOW_PATTERN = _COUNT + _UNIT

_WINDOW = re.compile(rf"({_COUNT})({_UNIT})")
_UNITS: dict[str, WindowUnit] = {"d": "d", "w": "w", "mo": "mo", "y": "y"}


def split_window(window: str) -> tuple[int, WindowUnit]:
    """``(count, unit)`` for a window such as ``"6mo"`` -> ``(6, "mo")``.

    Raises ``ValueError`` on anything outside the grammar; callers re-raise
    with their own message.
    """
    match = _WINDOW.fullmatch(window)  # "$" would accept a trailing newline
    if match is None:
        raise ValueError(f"not a <n>d|w|mo|y window: {window!r}")
    return int(match.group(1)), _UNITS[match.group(2)]


# git reads a bare number as an epoch only from nine digits (1973-03-03T09:46:40Z);
# a smaller ``@<n>`` matches nothing, so a window starting earlier is all history.
_MIN_GIT_EPOCH = 100_000_000


def anchored_since(window: str | None, anchor_epoch: int) -> str | None:
    """``"@<epoch>"``: the start of ``window`` counted back from ``anchor_epoch``.

    Days and weeks count back exactly; months and years by calendar, clamped to
    the month end (``6mo`` from 2020-08-31 starts 2020-02-29), keeping the time
    of day. None (all history) for a None window, or for a start git cannot
    read as an epoch (before 1973-03-03, or before year 1). Raises
    ``ValueError`` on a window outside the grammar.
    """
    if window is None:
        return None
    n, unit = split_window(window)
    anchor = datetime.fromtimestamp(anchor_epoch, tz=timezone.utc)
    try:
        if unit in ("d", "w"):
            start = anchor - timedelta(days=n * (7 if unit == "w" else 1))
        else:
            months = n * (12 if unit == "y" else 1)
            year, month = divmod(anchor.year * 12 + (anchor.month - 1) - months, 12)
            month += 1
            start = anchor.replace(year=year, month=month,
                                   day=min(anchor.day, calendar.monthrange(year, month)[1]))
    except (ValueError, OverflowError):  # before year 1
        return None
    epoch = int(start.timestamp())
    return f"@{epoch}" if epoch >= _MIN_GIT_EPOCH else None
