"""Whether a fleet repository is active inside the history window (issue #569).

"Active" has the ``ghsync --active-since`` meaning: the checkout's ``HEAD``
was committed inside the window. It is read locally from git, never from a
platform API. The window counts back from the run's UTC scan date in whole
days (months and years by calendar), so the answer is fixed for a day and a
rerun on the same day is byte-identical. Each ``ok`` fleet row carries it as
``active``; the run stamp counts those rows as ``qualifying``.
"""
from __future__ import annotations

import calendar
import os
import re
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_WINDOW = re.compile(r"^([1-9][0-9]*)(d|w|mo|y)$")


def window_start(window: str, today: date) -> date:
    """The first day inside ``window`` counting back from ``today`` (months and years by calendar)."""
    match = _WINDOW.match(window)
    if match is None:
        raise ValueError(f"invalid window {window!r}: expected <n>d, <n>w, <n>mo or <n>y")
    n, unit = int(match.group(1)), match.group(2)
    if unit in ("d", "w"):
        return today - timedelta(days=n * (7 if unit == "w" else 1))
    months = n * (12 if unit == "y" else 1)
    year, month = divmod(today.year * 12 + (today.month - 1) - months, 12)
    month += 1
    return date(year, month, min(today.day, calendar.monthrange(year, month)[1]))


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
