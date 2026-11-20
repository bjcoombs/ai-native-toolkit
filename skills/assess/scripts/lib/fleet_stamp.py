"""The fleet run stamp, ``<out>/run.json`` (issue #569).

One JSON object per run, beside ``repos.jsonl``, saying what produced it: the
toolkit commit and plugin version, the scan date, the history window, the
maturity-model version, the output mode and the repository counts. Comparisons
(#552) refuse or flag runs whose stamps disagree on the maturity model or window.

Repository counts:

- ``selected``: targets the run was given (every row);
- ``scanned_ok``: rows with ``status`` ``ok``;
- ``failed``: rows with ``status`` ``failed``;
- ``qualifying``: ok rows with ``active`` true: the ``HEAD`` committer date
  is inside the window (lib/fleet_activity.py). The window runs back from
  ``scan_date`` (UTC, whole days, months by calendar), so the count is fixed
  for a day.

The stamp holds no absolute path, salt, timing or input mode, so a root run,
a list run and any ``--jobs`` value over unchanged checkouts on the same day
write a byte-identical ``run.json``.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import TypedDict

from lib.fleet import STATUS_OK, FleetRow

# Bumped when the stamp's keys change, except for the 2.0.0 maturity-model key renames
# (#670): readers reject any other version, so lib/fleet_legacy.py maps the old keys
# instead and earlier history entries still match.
FLEET_STAMP_VERSION = 1
RUN_FILE = "run.json"


class RepositoryCounts(TypedDict):
    selected: int
    scanned_ok: int
    failed: int
    qualifying: int


class FleetStamp(TypedDict):
    fleet_stamp_version: int
    toolkit_commit: str | None  # None for an install that is not a git checkout
    plugin_version: str
    scan_date: str  # UTC, YYYY-MM-DD
    window: str  # the effective --window, e.g. "6mo"
    levels_version: str | None  # None without --levels
    counts_only: bool
    paths_hashed: bool  # never the salt itself
    scans: list[str] | None  # requested scans, sorted; None for all
    repositories: RepositoryCounts


def count_repositories(rows: list[FleetRow]) -> RepositoryCounts:
    scanned = [row for row in rows if row["status"] == STATUS_OK]
    return {
        "selected": len(rows),
        "scanned_ok": len(scanned),
        "failed": len(rows) - len(scanned),
        "qualifying": sum(1 for row in scanned if row["active"] is True),
    }


def build_stamp(
    rows: list[FleetRow], *, toolkit_commit: str | None, plugin_version: str,
    today: date, window: str, levels_version: str | None, counts_only: bool, paths_hashed: bool,
    scans: list[str] | None,
) -> FleetStamp:
    return {
        "fleet_stamp_version": FLEET_STAMP_VERSION,
        "toolkit_commit": toolkit_commit,
        "plugin_version": plugin_version,
        "scan_date": today.isoformat(),
        "window": window,
        "levels_version": levels_version,
        "counts_only": counts_only,
        "paths_hashed": paths_hashed,
        "scans": sorted(scans) if scans is not None else None,
        "repositories": count_repositories(rows),
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
