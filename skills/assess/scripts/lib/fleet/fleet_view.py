"""Shared reader for the views built on one fleet run's ``groups.jsonl`` (issue #553).

The leadership heat map (#570) and the per-team next-steps pages (#571) render
from the same run output with no rescan. This module is the one place that
reads that output back, and holds what both views need:

- ``read_jsonl`` reads one JSONL file, every line a JSON object at the expected
  envelope version; any fault raises ``FleetViewError`` naming ``file:line``,
  so a view never renders from a half-read or foreign file;
- ``read_repos`` gives the ``repos.jsonl`` rows sorted by repository id;
- ``load_groups`` reads ``groups.jsonl`` (a file or the run's ``--out``
  directory), checks the fields a view reads, refuses a duplicate group and
  returns the rows sorted by group;
- ``level_columns`` gives the maturity model row ids in maturity model order, the same for
  every group (a run evaluates one maturity model), refusing a run with no maturity model;
- ``run_levels_version`` is the single ``levels_version`` the run carries;
- ``check_levels`` matches an optional maturity model file (row titles and top levels,
  which ``groups.jsonl`` does not carry) to the run: same version, same rows;
- ``compare_levels`` pairs each cell with an earlier run's level (#552 shape
  pending: an earlier ``groups.jsonl`` is the comparison input), refusing a
  comparison across maturity-model versions with a note rather than a misleading delta.

Pure stdlib, no orchestrator import, deterministic: no set iteration reaches
output order.

The shared reader for the views built on one fleet run's output (#553): the
heat map (#570) and the per-team pages (#571) read the run through it and never rescan.
`read_jsonl` reads one file, each line a JSON object at the expected envelope version,
and raises `FleetViewError` naming `file:line` otherwise; `read_repos` returns the
`repos.jsonl` rows sorted by repository id (a row needs `repo` and `group` ids).
`load_groups` takes the file or the run directory, checks the maturity model entry fields a view
reads (`FleetViewError` names the line), refuses a duplicate
group and sorts by group; an empty file (no repository scanned) is `[]`, and the heat
map exits 2 on it while the team pages write an empty index. `level_columns` gives the maturity model row ids in maturity model order (the
same for every group; a run without `--levels` is refused), `run_levels_version` the one
version, `check_levels` matches a maturity model file (titles, top levels) to the run (a run
without `--levels` is refused as such, before any version check), and
`compare_levels` pairs each cell with an earlier run's `groups.jsonl` (`Change`: `new`,
`delta` or `was`), marking the comparison not comparable across maturity-model versions (a run
without a maturity model is named as having none, not as maturity model `None`).
`floor` and `full_run_only` (#697) are optional: an older row without them loads; a present `floor` must be a whole number of at least 1 on an unknown row.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypedDict, cast

from lib.fleet.runner import FLEET_ROW_VERSION, REPOS_FILE, FleetRow
from lib.fleet.fleet_groups import FLEET_GROUP_VERSION, GROUPS_FILE, GroupRow, RowLevel
from lib.fleet.fleet_legacy import upgrade
from lib.scoring.levels import MaturityModel

STATUS_LEVEL = "level"
STATUS_UNKNOWN = "unknown"
_ROW_FIELDS = ("id", "status", "level", "missing_signals", "unknown_repos")


class FleetViewError(ValueError):
    """The run output cannot be rendered; the message names the file and the fault."""


class Change(TypedDict):
    """One cell against the earlier run: ``kind`` is ``new``, ``delta`` or ``was``."""

    kind: str
    then: int | None  # the earlier level; None when it was unknown or absent
    delta: int | None  # now - then when both are known levels


class Comparison(TypedDict):
    comparable: bool
    note: str | None  # why the change is not shown, when not comparable
    changes: dict[str, dict[str, Change]]  # group -> maturity model row id -> change
    removed_groups: list[str]  # groups only the earlier run has


def groups_file(path: Path) -> Path:
    """``path`` itself, or ``path/groups.jsonl`` when it is a run directory."""
    return path / GROUPS_FILE if path.is_dir() else path


def repos_file(path: Path) -> Path:
    """``path`` itself, or ``path/repos.jsonl`` when it is a run directory."""
    return path / REPOS_FILE if path.is_dir() else path


def read_jsonl(path: Path, version_key: str, version: int) -> list[tuple[int, dict[str, Any]]]:
    """Every non-blank line of ``path`` as (line number, JSON object at envelope ``version``)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise FleetViewError(f"{path.name}: cannot read ({exc})") from exc
    rows: list[tuple[int, dict[str, Any]]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise FleetViewError(f"{path.name}:{number}: not JSON ({exc})") from exc
        if not isinstance(row, dict):
            raise FleetViewError(f"{path.name}:{number}: not a JSON object")
        if row.get(version_key) != version:
            raise FleetViewError(
                f"{path.name}:{number}: {version_key} is {row.get(version_key)!r}, expected {version}")
        rows.append((number, row))
    return rows


def read_repos(path: Path) -> list[FleetRow]:
    """The ``repos.jsonl`` rows of a run (the file or its directory), sorted by repository id."""
    file = repos_file(path)
    rows = read_jsonl(file, "fleet_row_version", FLEET_ROW_VERSION)
    for number, row in rows:
        if not isinstance(row.get("repo"), str) or not isinstance(row.get("group"), str):
            raise FleetViewError(f"{file.name}:{number}: no repo or group id")
    return sorted((cast(FleetRow, row) for _number, row in rows), key=lambda r: r["repo"])


def _check_row(data: dict[str, Any], where: str) -> GroupRow:
    data = upgrade(data)  # a line written before the 2.0.0 rename
    if not isinstance(data.get("group"), str) or not isinstance(data.get("levels"), list):
        raise FleetViewError(f"{where}: needs a string 'group' and a 'levels' list")
    if "levels_version" not in data or not isinstance(data["levels_version"], (str, type(None))):
        raise FleetViewError(f"{where}: needs 'levels_version' (a string or null)")
    for entry in data["levels"]:
        if not isinstance(entry, dict) or any(key not in entry for key in _ROW_FIELDS):
            raise FleetViewError(f"{where}: a maturity model entry lacks one of {', '.join(_ROW_FIELDS)}")
        lists = (entry["missing_signals"], entry["unknown_repos"])
        if not isinstance(entry["id"], str) or not all(
                isinstance(items, list) and all(isinstance(i, str) for i in items) for items in lists):
            raise FleetViewError(f"{where}: maturity model row {entry['id']!r} has a malformed id or signal list")
        known = entry["status"] == STATUS_LEVEL and type(entry["level"]) is int  # bool is not a level
        if not known and entry["status"] != STATUS_UNKNOWN:
            raise FleetViewError(f"{where}: maturity model row {entry['id']!r} has no whole level")
        # Optional since #697: absent in older rows, so only a present value is checked.
        if "floor" in entry and (known or type(entry["floor"]) is not int or entry["floor"] < 1):
            raise FleetViewError(f"{where}: maturity model row {entry['id']!r} has a malformed floor")
        only = entry.get("full_run_only", [])
        if not isinstance(only, list) or not all(isinstance(i, str) for i in only):
            raise FleetViewError(f"{where}: maturity model row {entry['id']!r} has a malformed full_run_only list")
    row: GroupRow = data  # type: ignore[assignment]  # checked above for the fields views read
    return row


def load_groups(path: Path) -> list[GroupRow]:
    """The run's group rows, sorted by group; raise ``FleetViewError`` on any fault.

    An empty file (a run where no repository could be scanned) gives ``[]``: a
    view decides what an empty run means for it."""
    file = groups_file(path)
    rows: dict[str, GroupRow] = {}
    for number, data in read_jsonl(file, "fleet_group_version", FLEET_GROUP_VERSION):
        where = f"{file.name}:{number}"
        row = _check_row(data, where)
        if row["group"] in rows:
            raise FleetViewError(f"{where}: group {row['group']!r} appears twice")
        rows[row["group"]] = row
    return [rows[name] for name in sorted(rows)]


def level_columns(groups: Sequence[GroupRow]) -> list[str]:
    """Maturity model row ids in maturity model order; every group must carry the same rows."""
    if not groups:
        raise FleetViewError("the run has no groups (no repository was scanned): nothing to draw")
    columns = [entry["id"] for entry in groups[0]["levels"]]
    if not columns:
        raise FleetViewError("the run has no maturity model levels: rerun fleet.py with --levels")
    for group in groups[1:]:
        if [entry["id"] for entry in group["levels"]] != columns:
            raise FleetViewError(f"group {group['group']!r} carries different maturity model rows")
    return columns


def run_levels_version(groups: Sequence[GroupRow]) -> str | None:
    """The run's maturity-model version; groups disagreeing is a malformed run."""
    versions = sorted({str(group["levels_version"]) for group in groups})
    if len(versions) > 1:
        raise FleetViewError(f"groups carry different maturity-model versions: {', '.join(versions)}")
    return groups[0]["levels_version"] if groups else None


def check_levels(maturity_model: MaturityModel, groups: Sequence[GroupRow]) -> None:
    """Refuse a maturity model file that is not the one the run evaluated."""
    version = run_levels_version(groups)
    if version is None:
        raise FleetViewError(f"the run has no maturity model levels (fleet.py ran without --levels), so "
                             f"maturity model file version {maturity_model['version']!r} cannot apply: rerun fleet.py "
                             "with --levels")
    if maturity_model["version"] != version:
        raise FleetViewError(f"maturity model file is version {maturity_model['version']!r}, the run used {version!r}")
    if [row["id"] for row in maturity_model["rows"]] != level_columns(groups):
        raise FleetViewError("maturity model file rows differ from the run's maturity model rows")


def cell(group: GroupRow, row_id: str) -> RowLevel:
    """The group's level entry for one maturity model row."""
    for entry in group["levels"]:
        if entry["id"] == row_id:
            return entry
    raise FleetViewError(f"group {group['group']!r} has no maturity model row {row_id!r}")


def _change(now: RowLevel, then: RowLevel | None) -> Change:
    if then is None:
        return {"kind": "new", "then": None, "delta": None}
    then_level = then["level"] if then["status"] == STATUS_LEVEL else None
    now_level = now["level"] if now["status"] == STATUS_LEVEL else None
    if then_level is not None and now_level is not None:
        return {"kind": "delta", "then": then_level, "delta": now_level - then_level}
    if then_level is None and now_level is None:
        return {"kind": "delta", "then": None, "delta": 0}
    return {"kind": "was", "then": then_level, "delta": None}


def _incomparable_note(then_version: str | None, now_version: str | None) -> str:
    """Why two runs' levels are not compared; a run without --levels is named as having none."""
    if then_version is None:
        return f"change not shown: the earlier run had no maturity model, this run used maturity model {now_version}"
    if now_version is None:
        return f"change not shown: the earlier run used maturity model {then_version}, this run had no maturity model"
    return f"change not shown: the earlier run used maturity model {then_version}, this run {now_version}"


def compare_levels(now: Sequence[GroupRow], then: Sequence[GroupRow]) -> Comparison:
    """Each current cell against the earlier run; not comparable across maturity-model versions."""
    removed = sorted({g["group"] for g in then} - {g["group"] for g in now})
    now_version, then_version = run_levels_version(now), run_levels_version(then)
    if then and now_version != then_version:
        return {"comparable": False, "removed_groups": removed, "changes": {},
                "note": _incomparable_note(then_version, now_version)}
    earlier = {g["group"]: {e["id"]: e for e in g["levels"]} for g in then}
    changes: dict[str, dict[str, Change]] = {}
    for group in now:
        rows = earlier.get(group["group"])
        changes[group["group"]] = {
            entry["id"]: _change(entry, None if rows is None else rows.get(entry["id"]))
            for entry in group["levels"]
        }
    return {"comparable": True, "note": None, "changes": changes, "removed_groups": removed}
