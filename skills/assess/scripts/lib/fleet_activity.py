"""Whether a fleet repository is active inside the history window (issue #569).

"Active" means the committer date of the checkout's ``HEAD`` falls inside the
window. It is read locally from git, never from a platform API. The window
counts back from the run's UTC scan date in whole days, with months and years
by calendar (clamped to the month end), so the answer is fixed for a day and a
rerun on the same day is byte-identical.

This is close to, but not the same as, ``ghsync --active-since``: ghsync reads
the platform's activity and head-commit dates and counts 30-day months and
365-day years back from the current time, so the two cutoffs can differ by a
few days. The window grammar itself is the shared one in lib/history_window.py
(#601). Each ``ok`` fleet row carries it as ``active``; the run stamp counts
those rows as ``qualifying``.

Whether a fleet repository is active (issue #569): `is_active` compares the checkout's
`HEAD` committer date (`head_commit_date`, UTC, read
from git with optional locks off) with `window_start(window, today)`, which counts back
in whole days, months and years by calendar (clamped to month end), validating the
window with `history_window.split_window`; a window reaching back before year 1 starts at
`date.min`. Close to `ghsync --active-since`, not identical:
ghsync counts 30-day months from now on platform dates. `scan_target` sets
each ok row's `active` from `ScanOptions.active_since`, so it is read before
`--delete-after` can remove the clone. An unreadable `HEAD` is not active.
"""
from __future__ import annotations

import calendar
import os
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from lib.history_window import split_window


def window_start(window: str, today: date) -> date:
    """The first day inside ``window`` counting back from ``today`` (months and years by calendar).

    A window reaching back before year 1 (``3000y``, ``999999999d``) starts at
    ``date.min``: every dated ``HEAD`` is inside it.
    """
    n, unit = split_window(window)  # the one window grammar (lib/history_window.py); raises ValueError
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
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=True, timeout=30,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
        return datetime.fromtimestamp(int(done.stdout.strip()), tz=timezone.utc).date()
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired, ValueError):
        return None


def is_active(checkout: Path, since: date) -> bool:
    """True when ``HEAD`` was committed on or after ``since``; an unreadable ``HEAD`` is not active."""
    committed = head_commit_date(checkout)
    return committed is not None and committed >= since
