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
  is inside the window (lib/history/fleet_activity.py). The window runs back from
  ``scan_date`` (UTC, whole days, months by calendar), so the count is fixed
  for a day.

With ``--fleet-config`` (#706) the stamp records ``fleet_config_sha256``, the
sha256 of the validated file's canonical form (``lib/fleet_config.py``), and
``fleet_config_entries``, the entry count per section; both are null without
one. Never a pattern or a value. The hash is a comparability key. A stamp
written before #706 lacks both keys and reads as "no fleet config", so the stamp
version stays 1.

``platform`` (#714) is true when ``--platform`` ran the platform pass; it is a
comparability key. A stamp written before #714 lacks it and reads as false
(``lib/fleet/fleet_legacy.py`` ``STAMP_DEFAULTS``), so the stamp version stays 1.

``coverage`` (#745) holds the ``--discovered`` reconciliation counts
(``lib/fleet/fleet_coverage.py``): how many discoverable repositories were assessed and
each gap, counts only. It is null without a listing, and in a stamp written before
#745. It is no comparability key, so the stamp version stays 1.

``rules_sha256`` and ``collectors`` (#952, ``lib/fleet/fleet_method.py``) name the method:
the sha256 of the rules of the maturity model the run scored with (None without
``--levels``) and every core collector's ``{id, revision}``. A stamp written before #952
lacks both and reads as unrecorded (None), never as the same rules, so the stamp version
stays 1. A rules difference is a rule reason, re-scored like a ``levels_version`` one.

``packs`` (#1273, ``lib/fleet/fleet_method.py``) names each practice pack the run evaluated,
``{name, version, sha256}`` in pass order. It is written only when a pack was passed, so a
run without one writes the stamp it wrote before; a stamp without it reads as no pack, and a
packs difference is a rule reason. The stamp version stays 1.

``not_cloned_includes`` (#1355, ``lib/fleet/fleet_includes.py``) lists each CI include project
not cloned under the root: ``project``, ``kind`` and how many repositories include it. The
path is clear only under ``--full``, the hashed include id under hashing and null in plain
counts-only output. It is written only when the run found one and is no comparability key, so
the stamp version stays 1.

The stamp holds no absolute path, salt, timing or input mode, so a root run,
a list run and any ``--jobs`` value over unchanged checkouts on the same day
write a byte-identical ``run.json`` (with ``--discovered``, a list run that leaves
out a checkout counts it in ``coverage``, so its stamp differs there).

The fleet run stamp `<out>/run.json` (issue #569): `FleetStamp` carries the toolkit
commit, plugin version, UTC scan date, effective window, maturity-model version, output mode
(`counts_only`, `paths_hashed`, sorted `scans`) and `repositories` counts (`selected`,
`scanned_ok` = ok rows, `failed`, `qualifying` = ok rows with `active` true), plus
`fleet_config_sha256` and `fleet_config_entries` from `--fleet-config` (#706; null
without one, absent before #706 and read as null, so the stamp version stays 1), and
`platform` (#714: `--platform` ran the platform pass; absent before #714 and read as
false through `fleet_legacy.stamp_value`), and `coverage` (#745: the `--discovered`
counts from `fleet_coverage.py`; null without a listing, absent before #745, not a
comparability key), and `rules_sha256` and `collectors` (#952: the rules hash and core
collector revisions from `fleet_method.method_keys(model)`; absent before #952, unrecorded), and
`not_cloned_includes` (#1355: the CI include projects not cloned, from `fleet_includes.py`;
written only when non-empty, the path named only under `--full`). No path,
salt, timing or input mode, so a
root run, a list run and any `--jobs` give the same bytes on the same day (with
`--discovered`, `coverage` counts the checkouts a list run left out). `write_stamp`
writes atomically with sorted keys.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import TypedDict

from lib.fleet.runner import STATUS_OK, FleetRow
from lib.fleet.fleet_coverage import Coverage
from lib.fleet.fleet_includes import NotClonedInclude, not_cloned_includes
from lib.fleet.fleet_method import CollectorRevision, method_keys
from lib.scoring.levels import MaturityModel
from lib.packs import PackIdentity
from lib.fleet_config import FleetConfig, FleetConfigEntries, config_sha256, section_entries

# Bumped when the stamp's keys change, except for the 2.0.0 maturity-model key renames
# (#670): readers reject any other version, so lib/fleet/fleet_legacy.py maps the old keys
# instead and earlier history entries still match.
FLEET_STAMP_VERSION = 1
RUN_FILE = "run.json"


class RepositoryCounts(TypedDict):
    selected: int
    scanned_ok: int
    failed: int
    qualifying: int


class _LaterStampKeys(TypedDict, total=False):
    """Keys every stamp is written with that an earlier stamp may lack, so run.schema.json
    does not require them (``NotRequired`` is not seen under postponed annotations)."""

    coverage: Coverage | None  # None without --discovered (unknown); absent before #745
    managed_settings: bool  # --managed-settings read this machine's managed settings; absent before #1153
    rules_sha256: str | None  # the rules scored with (lib/fleet/fleet_method.py); None without --levels or before #952
    collectors: list[CollectorRevision] | None  # every core collector's revision; None before #952
    packs: list[PackIdentity]  # each --pack's name, version and sha256 (#1273); absent: no pack
    not_cloned_includes: list[NotClonedInclude]  # CI include projects not cloned (#1355); absent: none


class FleetStamp(_LaterStampKeys):
    fleet_stamp_version: int
    toolkit_commit: str | None  # None for an install that is not a git checkout
    plugin_version: str
    scan_date: str  # UTC, YYYY-MM-DD
    window: str  # the effective --window, e.g. "6mo"
    levels_version: str | None  # None without --levels
    counts_only: bool
    paths_hashed: bool  # never the salt itself
    scans: list[str] | None  # requested scans, sorted; None for all
    fleet_config_sha256: str | None  # None without --fleet-config; absent before #706
    fleet_config_entries: FleetConfigEntries | None
    platform: bool  # --platform ran the platform pass; absent before #714 (reads as False)
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
    scans: list[str] | None, fleet_config: FleetConfig | None = None, platform: bool = False,
    coverage: Coverage | None = None, managed_settings: bool = False, model: MaturityModel | None = None,
    packs: list[PackIdentity] | None = None,
) -> FleetStamp:
    """The run stamp; ``model`` is the maturity model the run scored with (before any demotion),
    ``packs`` the identities of the run's practice packs (none: the key is not written)."""
    method = method_keys(model)
    stamp: FleetStamp = {
        "fleet_stamp_version": FLEET_STAMP_VERSION,
        "toolkit_commit": toolkit_commit,
        "plugin_version": plugin_version,
        "scan_date": today.isoformat(),
        "window": window,
        "levels_version": levels_version,
        "counts_only": counts_only,
        "paths_hashed": paths_hashed,
        "scans": sorted(scans) if scans is not None else None,
        "fleet_config_sha256": config_sha256(fleet_config) if fleet_config is not None else None,
        "fleet_config_entries": section_entries(fleet_config) if fleet_config is not None else None,
        "platform": platform,
        "repositories": count_repositories(rows),
        "coverage": coverage,
        "managed_settings": managed_settings,
        "rules_sha256": method["rules_sha256"],
        "collectors": method["collectors"],
    }
    if packs:  # absent without a pack, so such a run writes the stamp it wrote before #1273
        stamp["packs"] = list(packs)
    includes = not_cloned_includes(rows, counts_only=counts_only, paths_hashed=paths_hashed)
    if includes:  # absent without one, so such a run writes the stamp it wrote before #1355
        stamp["not_cloned_includes"] = includes
    return stamp


def dumps_stamp(stamp: FleetStamp) -> str:
    return json.dumps(stamp, sort_keys=True, indent=2) + "\n"


def write_stamp(out_dir: Path, stamp: FleetStamp) -> Path:
    """Write ``<out_dir>/run.json`` through a temp file and ``os.replace``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / RUN_FILE
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{RUN_FILE}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(dumps_stamp(stamp))
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target
