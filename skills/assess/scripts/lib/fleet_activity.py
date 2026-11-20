"""Whether a fleet repository is active inside the history window (issue #569).

"Active" means the committer date of the checkout's ``HEAD`` falls inside the
window. It is read locally from git, never from a platform API. The window
counts back from the run's UTC scan date in whole days, with months and years
by calendar (clamped to the month end), so the answer is fixed for a day and a
rerun on the same day is byte-identical.

This is close to, but not the same as, ``ghsync --active-since``: ghsync reads
the platform's activity and head-commit dates and counts 30-day months and
365-day years back from the current time, so the two cutoffs can differ by a
few days. #601 tracks unifying the window grammar. Each ``ok`` fleet row carries it as
``active``; the run stamp counts those rows as ``qualifying``.
"""
from __future__ import annotations

import calendar
import os
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from lib.signals import parse_window


def window_start(window: str, today: date) -> date:
    """The first day inside ``window`` counting back from ``today`` (months and years by calendar).

    A window reaching back before year 1 (``3000y``, ``999999999d``) starts at
    ``date.min``: every dated ``HEAD`` is inside it.
    """
    parse_window(window)  # the one window grammar (lib/signals.py); raises ValueError
    unit = "mo" if window.endswith("mo") else window[-1]
    n = int(window[: -len(unit)])
    try:
        if unit in ("d", "w"):
            return today - timedelta(days=n * (7 if unit == "w" else 1))
        months = n * (12 if unit == "y" else 1)
        year, month = divmod(today.year * 12 + (today.month - 1) - months, 12)
        month += 1
        return date(year, month, min(today.day, calendar.monthrange(year, month)[1]))
    except (ValueError, OverflowError):  # before date.min, or past timedelta's range
        return date.min


def head_commit_date(checkout: Path) -> date | None:
    """The UTC date of the checkout's ``HEAD`` commit, or None when it cannot be read."""
    try:
        done = subprocess.run(
            ["git", "-C", str(checkout), "log", "-1", "--format=%ct", "HEAD"],
            capture_output=True, text=True, check=True, timeout=30,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
        return datetime.fromtimestamp(int(done.stdout.strip()), tz=timezone.utc).date()
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired, ValueError):
        return None


def is_active(checkout: Path, since: date) -> bool:
    """True when ``HEAD`` was committed on or after ``since``; an unreadable ``HEAD`` is not active."""
    committed = head_commit_date(checkout)
    return committed is not None and committed >= since
