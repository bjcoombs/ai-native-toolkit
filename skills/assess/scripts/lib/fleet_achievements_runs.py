"""The earlier runs the achievements engine replays, read from the run history (issue #704).

``earlier_runs`` reads the ``<date>[.<n>]`` entries under the history ``<org>``
directory (``lib/fleet_history.py``) and keeps each entry that:

- has a readable ``run.json`` (or, only when it has none, the copy of the stamp in its
  ``fleet-data.json``) at this toolkit's stamp version (an entry from before
  the 2.0.0 rename is upgraded, ``lib/fleet_legacy.py``) that ``fleet_compare.comparability``
  accepts against this run: same plugin major version, maturity-model version, window,
  output mode, scan selection, path hashing, ``--fleet-config`` and ``--platform``;
- has a ``fleet-data.json`` at a version in ``READABLE_FLEET_DATA_VERSIONS`` (entries
  from before #703 have none and are passed over; version 1 differs from 2 only in
  roll-up fields the engine does not read, #772) which is well formed (``well_formed``: every field the
  engine reads decodes, so one malformed entry is passed over rather than costing
  every later run its achievements).

An entry is dated by its ``run.json`` ``scan_date``, not by the copy of the stamp
inside ``fleet-data.json``: a run copied into the history with a re-dated ``run.json``
is replayed on that date. Only an entry with no ``run.json`` is read through that copy.
One run per scan date (``fleet_history.one_per_date``); dates on or after this run's
are dropped, since this run comes from memory, so a same-day rerun gives the same
bytes. An unreadable entry is passed over, never an error. An earlier
``achievements.json`` is never read: the engine recomputes from run data every time.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

from lib.fleet_achievements import HistoryRun, subjects
from lib.fleet_compare import comparability
from lib.fleet_data import FLEET_DATA_FILE, FLEET_DATA_VERSION, FleetData
from lib.fleet_history import one_per_date
from lib.fleet_legacy import upgrade
from lib.fleet_stamp import FLEET_STAMP_VERSION, RUN_FILE, FleetStamp

_TOP_KEYS = ("run", "model", "views", "repos")
# The fleet-data.json versions whose cells and units the engine reads alike: 1 (#703) carried
# the mean-share roll-up #772 removed, which the engine no longer reads.
READABLE_FLEET_DATA_VERSIONS = frozenset({1, FLEET_DATA_VERSION})


def _stamp(entry: Path, data: Mapping[str, Any]) -> Any:
    """The entry's ``run.json``; only when the entry has none, the copy of it in ``fleet-data.json``."""
    path = entry / RUN_FILE
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    run = data.get("run")
    return {**run, "fleet_stamp_version": FLEET_STAMP_VERSION} if isinstance(run, dict) else None


def _read(entry: Path, date: str, current: FleetStamp) -> HistoryRun | None:
    """The entry as a replayed run dated ``date``, or None to pass it over.

    The entry's date is its own ``run.json`` ``scan_date`` (it must match the entry name);
    the copy inside ``fleet-data.json`` is read only when there is no ``run.json``, so an
    entry whose stamp was re-dated (a run copied into the history) is replayed on the
    stamp's date."""
    try:
        data: Any = json.loads((entry / FLEET_DATA_FILE).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        stamp: Any = _stamp(entry, data)
        if not isinstance(stamp, dict) or stamp.get("fleet_stamp_version") != FLEET_STAMP_VERSION:
            return None
        stamp = upgrade(stamp)
        if stamp.get("scan_date") != date or comparability(cast(FleetStamp, stamp), current)[0]:
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if data.get("fleet_data_version") not in READABLE_FLEET_DATA_VERSIONS \
            or not all(k in data for k in _TOP_KEYS) or not well_formed(cast(FleetData, data)):
        return None
    return HistoryRun(date, cast(FleetData, data))


def well_formed(data: FleetData) -> bool:
    """Whether the engine can read ``data``: every field ``fleet_achievements.subjects`` and the
    replay touch, decoded once here, so a malformed entry is passed over instead of costing
    every later run its achievements."""
    try:
        if not isinstance(data["run"], dict) or not isinstance(data["run"]["scan_date"], str):
            return False
        tops: dict[str, int] = {}
        for repo in data["repos"]:
            if not isinstance(repo["key"], str) or not isinstance(repo["units"], list):
                return False
            for cell in repo["rows"]:
                if type(cell["top_level"]) is not int or not isinstance(cell["row"], str) \
                        or (cell["level"] is not None and type(cell["level"]) is not int) \
                        or not isinstance(cell["status"], str) \
                        or not (cell["signal"] is None or isinstance(cell["signal"], str)):
                    return False
                tops[cell["row"]] = cell["top_level"]
        rules = {row: (Fraction(1), top) for row, top in tops.items()}
        for sub in subjects(data, rules).values():
            shares = [state.share for state in sub.rows.values()]
            if any(share is not None and not isinstance(share, (int, float)) for share in shares):
                return False
    except (KeyError, TypeError, AttributeError, ValueError, IndexError):
        return False
    return True


def earlier_runs(org_dir: Path, current: FleetStamp, repos: frozenset[str]) -> list[HistoryRun]:
    """Earlier comparable runs with a ``fleet-data.json``, one per date before this run's, oldest first."""
    return [run for _date, run in one_per_date(org_dir, current["scan_date"], repos,
                                               lambda entry, date: _read(entry, date, current))]
