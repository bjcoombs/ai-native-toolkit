"""The fleet run stamp, ``<out>/run.json`` (issue #569).

One JSON object per run, beside ``repos.jsonl``, saying what produced it: the
toolkit commit and plugin version, the scan date, the history window, the
rubric version, the output mode and the repository counts. Comparisons
(#552) refuse or flag runs whose stamps disagree on the rubric or window.

Repository counts:

- ``selected``: targets the run was given (every row);
- ``scanned``: rows with ``status`` ``ok``;
- ``failed``: rows with ``status`` ``failed``;
- ``qualifying``: scanned repositories whose checkout ``HEAD`` was committed
  inside the window, the ``ghsync --active-since`` meaning. The window runs
  back from ``scan_date`` (UTC, whole days), so the count is fixed for a day.

The stamp holds no absolute path, salt, timing or input mode, so a root run,
a list run and any ``--jobs`` value over unchanged checkouts on the same day
write a byte-identical ``run.json``. ``ActivityProbe`` is the ``run_fleet``
``after`` hook that reads each checkout's ``HEAD`` date in the worker, before
``--delete-after`` can remove it.
"""
from __future__ import annotations

import calendar
import json
import os
import re
import subprocess
import tempfile
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict

from lib.fleet import STATUS_OK, FleetRow, Target

# Bumped when the stamp's keys change.
FLEET_STAMP_VERSION = 1
RUN_FILE = "run.json"
_WINDOW = re.compile(r"^([1-9][0-9]*)(d|w|mo|y)$")


class RepositoryCounts(TypedDict):
    selected: int
    scanned: int
    failed: int
    qualifying: int


class FleetStamp(TypedDict):
    fleet_stamp_version: int
    toolkit_commit: str | None  # None for an install that is not a git checkout
    plugin_version: str
    scan_date: str  # UTC, YYYY-MM-DD
    window: str  # the effective --window, e.g. "6mo"
    rubric_version: str | None  # None without --rubric
    counts_only: bool
    paths_hashed: bool  # never the salt itself
    scans: list[str] | None  # requested scans, sorted; None for all
    repositories: RepositoryCounts


def window_start(window: str, today: date) -> date:
    """The first day inside ``window`` counting back from ``today`` (months and years by calendar)."""
    match = _WINDOW.match(window)
    if match is None:
        raise ValueError(f"invalid window {window!r}: expected <n>d, <n>w, <n>mo or <n>y")
    n, unit = int(match.group(1)), match.group(2)
    if unit in ("d", "w"):
        return today - timedelta(days=n * (7 if unit == "w" else 1))
    months = n * (12 if unit == "y" else 1)
    total = today.year * 12 + (today.month - 1) - months
    year, month = divmod(total, 12)
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


class ActivityProbe:
    """``run_fleet`` ``after`` hook: records which ok rows' checkouts were committed inside the window."""

    def __init__(self, window: str, today: date) -> None:
        self.start = window_start(window, today)
        self.active: set[str] = set()
        self._lock = threading.Lock()

    def __call__(self, target: Target, row: FleetRow) -> None:
        if row["status"] != STATUS_OK:
            return
        committed = head_commit_date(target.checkout)
        if committed is not None and committed >= self.start:
            with self._lock:
                self.active.add(target.repo)


def count_repositories(rows: list[FleetRow], active: set[str]) -> RepositoryCounts:
    scanned = [row for row in rows if row["status"] == STATUS_OK]
    return {
        "selected": len(rows),
        "scanned": len(scanned),
        "failed": len(rows) - len(scanned),
        "qualifying": sum(1 for row in scanned if row["repo"] in active),
    }


def build_stamp(
    rows: list[FleetRow], *, active: set[str], toolkit_commit: str | None, plugin_version: str,
    today: date, window: str, rubric_version: str | None, counts_only: bool, paths_hashed: bool,
    scans: list[str] | None,
) -> FleetStamp:
    return {
        "fleet_stamp_version": FLEET_STAMP_VERSION,
        "toolkit_commit": toolkit_commit,
        "plugin_version": plugin_version,
        "scan_date": today.isoformat(),
        "window": window,
        "rubric_version": rubric_version,
        "counts_only": counts_only,
        "paths_hashed": paths_hashed,
        "scans": sorted(scans) if scans is not None else None,
        "repositories": count_repositories(rows, active),
    }


def dumps_stamp(stamp: FleetStamp) -> str:
    return json.dumps(stamp, sort_keys=True, indent=2) + "\n"


def write_stamp(out_dir: Path, stamp: FleetStamp) -> Path:
    """Write ``<out_dir>/run.json`` through a temp file and ``os.replace``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / RUN_FILE
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{RUN_FILE}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(dumps_stamp(stamp))
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target
