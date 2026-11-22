"""``OUT/level.json``: each repository's verified tally and Step in a fleet run (#744).

``fleet --levels FILE`` evaluates the model over every scanned repository's
``scans`` with ``lib.level_tally.level_result``, the same function ``/assess``
runs over its run context, so one commit gives one result in both. Keyed by the
repository id of ``repos.jsonl``; a failed scan has no signals and no entry.
The envelope carries the method identity (the model version, the toolkit commit
and the plugin version) and nothing that changes between two runs of the same
commits, so a rerun writes identical bytes. Without ``--levels`` the file is
removed, never left from an earlier run. ``commits`` holds, under the same ids, the
commit each result measured and whether the checkout was dirty (#907; a dirty one is
scored from its committed tree, ``lib/fleet.py`` ``committed_tree``). Each result applies
the owner exclusions of the repository's own ``.assess/config.toml`` (#910), read from its
``owner_exclusions`` scan as ``/assess`` reads them from its run context (``repo_level``;
``fleet-data.json`` copies the Step fields from the same function, #944). A ``--scans``
subset without ``owner_exclusions`` applies none. Fleet-wide exclusions through
``--fleet-config`` wait on the method stamp (#952). Schema:
``skills/fleet/references/level.schema.json`` (``lib/fleet_level_schema.py``).
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TypedDict

from lib.fleet import STATUS_OK, FleetRow, RepoCommit
from lib.levels import MaturityModel
from lib.level_result_types import LevelResult
from lib.level_tally import LEVEL_RESULT_VERSION, dumps_level, level_result, run_exclusions

LEVEL_FILE = "level.json"


class FleetLevels(TypedDict):
    level_result_version: int
    levels_version: str
    toolkit_commit: str | None
    plugin_version: str
    repos: dict[str, LevelResult]
    commits: dict[str, RepoCommit]  # the same ids: the commit each result measured (#907)


def repo_level(model: MaturityModel, row: FleetRow) -> LevelResult:
    """One scanned repository's level result, with the owner's exclusions from its scans."""
    scans = row.get("scans") or {}
    return level_result(model, scans, run_exclusions(scans))


def fleet_levels(rows: Iterable[FleetRow], model: MaturityModel, *, toolkit_commit: str | None,
                 plugin_version: str) -> FleetLevels:
    """One result per scanned repository, sorted by id."""
    scanned = sorted((row for row in rows if row["status"] == STATUS_OK), key=lambda row: row["repo"])
    return {
        "level_result_version": LEVEL_RESULT_VERSION,
        "levels_version": model["version"],
        "toolkit_commit": toolkit_commit,
        "plugin_version": plugin_version,
        "repos": {row["repo"]: repo_level(model, row) for row in scanned},
        "commits": {row["repo"]: {"head_sha": row.get("head_sha"), "dirty": row.get("dirty")}
                    for row in scanned},
    }


def write_fleet_levels(out_dir: Path, rows: Iterable[FleetRow], model: MaturityModel | None, *,
                       toolkit_commit: str | None, plugin_version: str) -> Path | None:
    """Write ``level.json``, or remove a stale one when the run has no model."""
    path = out_dir / LEVEL_FILE
    if model is None:
        path.unlink(missing_ok=True)
        return None
    levels = fleet_levels(rows, model, toolkit_commit=toolkit_commit, plugin_version=plugin_version)
    path.write_text(dumps_level(levels), encoding="utf-8", newline="\n")
    return path
