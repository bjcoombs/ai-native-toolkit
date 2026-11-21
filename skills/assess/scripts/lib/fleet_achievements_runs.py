"""The earlier runs the achievements engine replays, read from the run history (issue #704).

``earlier_runs`` reads the ``<date>[.<n>]`` entries under the history ``<org>``
directory (``lib/fleet_history.py``) and keeps each entry that:

- has a readable ``run.json`` at this toolkit's stamp version (an entry from before
  the 2.0.0 rename is upgraded, ``lib/fleet_legacy.py``) that ``fleet_compare.comparability``
  accepts against this run: same plugin major version, maturity-model version, window,
  output mode, scan selection, path hashing, ``--fleet-config`` and ``--platform``;
- has a ``fleet-data.json`` at ``FLEET_DATA_VERSION`` (entries from before #703 have
  none and are passed over) whose run date matches the entry's.

One run per scan date (``fleet_history.one_per_date``); dates on or after this run's
are dropped, since this run comes from memory, so a same-day rerun gives the same
bytes. An unreadable entry is passed over, never an error. An earlier
``achievements.json`` is never read: the engine recomputes from run data every time.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from lib.fleet_achievements import HistoryRun
from lib.fleet_compare import comparability
from lib.fleet_data import FLEET_DATA_FILE, FLEET_DATA_VERSION, FleetData
from lib.fleet_history import one_per_date
from lib.fleet_legacy import upgrade
from lib.fleet_stamp import FLEET_STAMP_VERSION, RUN_FILE, FleetStamp

_TOP_KEYS = ("run", "model", "views", "repos")


def _read(entry: Path, date: str, current: FleetStamp) -> HistoryRun | None:
    try:
        stamp: Any = json.loads((entry / RUN_FILE).read_text(encoding="utf-8"))
        if not isinstance(stamp, dict) or stamp.get("fleet_stamp_version") != FLEET_STAMP_VERSION:
            return None
        stamp = upgrade(stamp)
        if stamp.get("scan_date") != date or comparability(cast(FleetStamp, stamp), current)[0]:
            return None
        data: Any = json.loads((entry / FLEET_DATA_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("fleet_data_version") != FLEET_DATA_VERSION \
            or not all(k in data for k in _TOP_KEYS) or data["run"].get("scan_date") != date:
        return None
    return HistoryRun(date, cast(FleetData, data))


def earlier_runs(org_dir: Path, current: FleetStamp, repos: frozenset[str]) -> list[HistoryRun]:
    """Earlier comparable runs with a ``fleet-data.json``, one per date before this run's, oldest first."""
    return [run for _date, run in one_per_date(org_dir, current["scan_date"], repos,
                                               lambda entry, date: _read(entry, date, current))]
