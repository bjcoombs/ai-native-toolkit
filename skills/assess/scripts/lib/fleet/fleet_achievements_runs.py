"""The earlier runs the achievements engine replays, read from the run history (issue #704).

``earlier_runs`` reads the ``<date>[.<n>]`` entries under the history ``<org>``
directory (``lib/fleet/fleet_history.py``) and keeps each entry that:

- has a readable ``run.json`` (or, only when it has none, the copy of the stamp in its
  ``fleet-data.json``) at this toolkit's stamp version (an entry from before
  the 2.0.0 rename is upgraded, ``lib/fleet/fleet_legacy.py``) that ``fleet_compare.comparability``
  accepts against this run: same plugin major version, maturity-model version, window,
  output mode, scan selection, path hashing, ``--fleet-config`` and ``--platform``;
- has a ``fleet-data.json`` at a version in ``READABLE_FLEET_DATA_VERSIONS`` (entries
  from before #703 have none and are passed over; version 1 differs from 2 only in
  roll-up fields the engine does not read, #772) which is well formed (``well_formed``: every field the
  engine reads decodes, so one malformed entry is passed over rather than costing
  every later run its achievements).

**Re-score (#771).** An entry whose only refusal is the maturity-model version is not
passed over when the caller gives this run's model and key function: its ``repos.jsonl``
scans are re-scored under this run's model (``lib/fleet/fleet_rescore.py``) and it replays as
``HistoryRun(..., rescored_from=<its version>, stored=<its fleet-data.json>)``. It is passed
over (today's behaviour, never an error) when ``repos.jsonl`` does not read as fleet rows
with scans or a stored repository key does not join under this run's key function. A
``repos.jsonl`` written by a hashed run holds hashed ids (#909), which the key keeps as
they are; such an entry is passed over when none of its ids is one of this run's (another
salt).

An entry is dated by its ``run.json`` ``scan_date``, not by the copy of the stamp
inside ``fleet-data.json``: a run copied into the history with a re-dated ``run.json``
is replayed on that date. Only an entry with no ``run.json`` is read through that copy.
One run per scan date (``fleet_history.one_per_date``); dates on or after this run's
are dropped, since this run comes from memory, so a same-day rerun gives the same
bytes. An unreadable entry is passed over, never an error. An earlier
``achievements.json`` is never read: the engine recomputes from run data every time.

`earlier_runs` reads the history entries the engine replays: `run.json` passing
`fleet_compare.comparability` against this run and a `fleet-data.json` at a version in
`READABLE_FLEET_DATA_VERSIONS` (1 to 4: version 1's roll-up is not read, #772;
version 3 only adds `opportunities`, #746; version 4 only adds the per-repository Step
fields, #944, #945; every version from 1 to `FLEET_DATA_VERSION`, so a bump keeps the one before it),
one per date before this run's (`fleet_history.one_per_date`). An entry is dated by its
`run.json` `scan_date`, not the stamp copy in `fleet-data.json`, which is read only when
the entry has no `run.json` (#780). Unreadable entries are passed over. With the run's
model and key function (`model=`, `key=`), an entry whose only refusal is the
maturity-model version is re-scored from its `repos.jsonl` (`fleet_view.read_repos`,
`fleet_rescore.rescore`) and returned as a `HistoryRun` with `rescored_from` (its stored
version) and `stored` (its `fleet-data.json`) set (#771).
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from fractions import Fraction
from pathlib import Path
from typing import Any, NamedTuple, cast

from lib.counts_only import is_hashed
from lib.fleet.fleet_achievements import HistoryRun, subjects
from lib.fleet.fleet_compare import comparability
from lib.fleet.fleet_data import FLEET_DATA_FILE, FLEET_DATA_VERSION, FleetData
from lib.fleet.fleet_history import one_per_date
from lib.fleet.fleet_legacy import upgrade
from lib.fleet.fleet_rescore import rescore
from lib.fleet.fleet_stamp import FLEET_STAMP_VERSION, RUN_FILE, FleetStamp
from lib.fleet.fleet_view import FleetViewError, read_repos
from lib.levels import MaturityModel

_TOP_KEYS = ("run", "model", "views", "repos")
# The fleet-data.json versions whose cells and units the engine reads alike: 1 (#703) carried
# the mean-share roll-up #772 removed, which the engine no longer reads; 3 (#746) only adds
# ``opportunities``, which the engine does not read; 4 (#944, #945) only adds the repository
# ``step``, ``next_step``, ``tally``, ``exclusions`` and ``gate_blockers`` and the unit ``step``;
# the per-tag ``tags`` (#1073) are additive too, and an entry without them reads as not measured
# (``fleet_data_tags.member_tags``). Every version from 1 up: a FLEET_DATA_VERSION bump can never
# drop the one before it (#1072), since the range follows the constant.
READABLE_FLEET_DATA_VERSIONS = frozenset(range(1, FLEET_DATA_VERSION + 1))


def _stamp(entry: Path, data: Mapping[str, Any]) -> Any:
    """The entry's ``run.json``; only when the entry has none, the copy of it in ``fleet-data.json``."""
    path = entry / RUN_FILE
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    run = data.get("run")
    return {**run, "fleet_stamp_version": FLEET_STAMP_VERSION} if isinstance(run, dict) else None


class Rescorer(NamedTuple):
    """This run's maturity model and repository key function: what a re-score needs."""

    model: MaturityModel
    key: Callable[[str], str]
    ids: frozenset[str] | None = None  # this run's repository ids as its output files write them


def _version_only(stamp: FleetStamp, current: FleetStamp) -> bool:
    """Whether the maturity-model version is the only difference ``comparability`` refuses."""
    return not comparability({**stamp, "levels_version": current["levels_version"]}, current)[0]


def _rescored(entry: Path, data: FleetData, stamp: FleetStamp, current: FleetStamp,
              rescorer: Rescorer | None) -> HistoryRun | None:
    """The entry re-scored under this run's model from its ``repos.jsonl``; None to pass it over."""
    if rescorer is None or rescorer.model["version"] != current["levels_version"] \
            or not _version_only(stamp, current):
        return None
    try:
        rows = read_repos(entry)
    except (FleetViewError, OSError):
        return None
    stored = {row["repo"] for row in rows}
    if rescorer.ids is not None and any(is_hashed(r) for r in stored) and not stored & rescorer.ids:
        return None  # ids hashed at the output boundary under another salt (#909): no join
    try:
        again = rescore(data, rows, rescorer.model, rescorer.key)
    except (KeyError, TypeError, AttributeError, ValueError, IndexError):
        return None  # a stored row an older scanner wrote that the evaluator cannot read
    if again is None:
        return None
    return HistoryRun(stamp["scan_date"], again, stamp["levels_version"] or "none", data)


def _read(entry: Path, date: str, current: FleetStamp, rescorer: Rescorer | None = None) -> HistoryRun | None:
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
        if stamp.get("scan_date") != date:
            return None
        refused = bool(comparability(cast(FleetStamp, stamp), current)[0])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    version = data.get("fleet_data_version")  # an int only: a list or dict is unhashable, True == 1
    if type(version) is not int or version not in READABLE_FLEET_DATA_VERSIONS \
            or not all(k in data for k in _TOP_KEYS) or not well_formed(cast(FleetData, data)):
        return None
    if refused:
        return _rescored(entry, cast(FleetData, data), cast(FleetStamp, stamp), current, rescorer)
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
        subjects(data, rules)
        if any(repo.get("step") is not None and type(repo.get("step")) is not int for repo in data["repos"]):
            return False
    except (KeyError, TypeError, AttributeError, ValueError, IndexError):
        return False
    return True


def earlier_runs(org_dir: Path, current: FleetStamp, repos: frozenset[str], *,
                 model: MaturityModel | None = None,
                 key: Callable[[str], str] | None = None) -> list[HistoryRun]:
    """Earlier comparable runs with a ``fleet-data.json``, one per date before this run's, oldest first.

    With ``model`` (this run's) and ``key`` (this run's repository key function), an entry whose
    only difference is the maturity-model version is re-scored (``lib/fleet/fleet_rescore.py``)."""
    rescorer = Rescorer(model, key, repos) if model is not None and key is not None else None
    return [run for _date, run in one_per_date(org_dir, current["scan_date"], repos,
                                               lambda entry, date: _read(entry, date, current, rescorer))]
