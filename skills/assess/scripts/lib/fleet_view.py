"""Shared reader for the views built on one fleet run's ``groups.jsonl`` (issue #553).

The leadership heat map (#570) and the per-team next-steps pages (#571) render
from the same run output with no rescan. This module holds what both need:

- ``load_groups`` reads ``groups.jsonl`` (a file or the run's ``--out``
  directory), checks the envelope version and the fields a view reads, refuses
  a duplicate group and returns the rows sorted by group;
- ``rubric_columns`` gives the rubric row ids in rubric order, the same for
  every group (a run evaluates one rubric), refusing a run with no rubric;
- ``run_rubric_version`` is the single ``rubric_version`` the run carries;
- ``check_rubric`` matches an optional rubric file (row titles and top levels,
  which ``groups.jsonl`` does not carry) to the run: same version, same rows;
- ``compare_levels`` pairs each cell with an earlier run's level (#552 shape
  pending: an earlier ``groups.jsonl`` is the comparison input), refusing a
  comparison across rubric versions with a note rather than a misleading delta.

Pure stdlib, no orchestrator import, deterministic: no set iteration reaches
output order.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypedDict

from lib.fleet_groups import FLEET_GROUP_VERSION, GROUPS_FILE, GroupRow, RubricRowLevel
from lib.rubric import Rubric

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
    changes: dict[str, dict[str, Change]]  # group -> rubric row id -> change
    removed_groups: list[str]  # groups only the earlier run has


def groups_file(path: Path) -> Path:
    """``path`` itself, or ``path/groups.jsonl`` when it is a run directory."""
    return path / GROUPS_FILE if path.is_dir() else path


def _check_row(data: Any, where: str) -> GroupRow:
    if not isinstance(data, dict):
        raise FleetViewError(f"{where}: not a JSON object")
    if data.get("fleet_group_version") != FLEET_GROUP_VERSION:
        raise FleetViewError(
            f"{where}: fleet_group_version {data.get('fleet_group_version')!r}, "
            f"this renderer reads {FLEET_GROUP_VERSION}")
    if not isinstance(data.get("group"), str) or not isinstance(data.get("rubric"), list):
        raise FleetViewError(f"{where}: needs a string 'group' and a 'rubric' list")
    for entry in data["rubric"]:
        if not isinstance(entry, dict) or any(key not in entry for key in _ROW_FIELDS):
            raise FleetViewError(f"{where}: a rubric entry lacks one of {', '.join(_ROW_FIELDS)}")
        known = entry["status"] == STATUS_LEVEL and isinstance(entry["level"], int)
        if not known and entry["status"] != STATUS_UNKNOWN:
            raise FleetViewError(f"{where}: rubric row {entry['id']!r} has no whole level")
    row: GroupRow = data  # type: ignore[assignment]  # checked above for the fields views read
    return row


def load_groups(path: Path) -> list[GroupRow]:
    """The run's group rows, sorted by group; raise ``FleetViewError`` on any fault."""
    file = groups_file(path)
    try:
        text = file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise FleetViewError(f"cannot read {file}: {exc}") from exc
    rows: dict[str, GroupRow] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        where = f"{file.name} line {number}"
        try:
            data = json.loads(line)
        except ValueError as exc:
            raise FleetViewError(f"{where}: invalid JSON ({exc})") from exc
        row = _check_row(data, where)
        if row["group"] in rows:
            raise FleetViewError(f"{where}: group {row['group']!r} appears twice")
        rows[row["group"]] = row
    if not rows:
        raise FleetViewError(f"{file.name}: no group rows")
    return [rows[name] for name in sorted(rows)]


def rubric_columns(groups: Sequence[GroupRow]) -> list[str]:
    """Rubric row ids in rubric order; every group must carry the same rows."""
    columns = [entry["id"] for entry in groups[0]["rubric"]]
    if not columns:
        raise FleetViewError("the run has no rubric levels: rerun fleet.py with --rubric")
    for group in groups[1:]:
        if [entry["id"] for entry in group["rubric"]] != columns:
            raise FleetViewError(f"group {group['group']!r} carries different rubric rows")
    return columns


def run_rubric_version(groups: Sequence[GroupRow]) -> str | None:
    """The run's rubric version; groups disagreeing is a malformed run."""
    versions = sorted({str(group["rubric_version"]) for group in groups})
    if len(versions) > 1:
        raise FleetViewError(f"groups carry different rubric versions: {', '.join(versions)}")
    return groups[0]["rubric_version"]


def check_rubric(rubric: Rubric, groups: Sequence[GroupRow]) -> None:
    """Refuse a rubric file that is not the one the run evaluated."""
    version = run_rubric_version(groups)
    if rubric["version"] != version:
        raise FleetViewError(f"rubric file is version {rubric['version']!r}, the run used {version!r}")
    if [row["id"] for row in rubric["rows"]] != rubric_columns(groups):
        raise FleetViewError("rubric file rows differ from the run's rubric rows")


def cell(group: GroupRow, row_id: str) -> RubricRowLevel:
    """The group's level entry for one rubric row."""
    for entry in group["rubric"]:
        if entry["id"] == row_id:
            return entry
    raise FleetViewError(f"group {group['group']!r} has no rubric row {row_id!r}")


def _change(now: RubricRowLevel, then: RubricRowLevel | None) -> Change:
    if then is None:
        return {"kind": "new", "then": None, "delta": None}
    then_level = then["level"] if then["status"] == STATUS_LEVEL else None
    now_level = now["level"] if now["status"] == STATUS_LEVEL else None
    if then_level is not None and now_level is not None:
        return {"kind": "delta", "then": then_level, "delta": now_level - then_level}
    if then_level is None and now_level is None:
        return {"kind": "delta", "then": None, "delta": 0}
    return {"kind": "was", "then": then_level, "delta": None}


def compare_levels(now: Sequence[GroupRow], then: Sequence[GroupRow]) -> Comparison:
    """Each current cell against the earlier run; not comparable across rubric versions."""
    removed = sorted({g["group"] for g in then} - {g["group"] for g in now})
    now_version, then_version = run_rubric_version(now), run_rubric_version(then)
    if now_version != then_version:
        return {"comparable": False, "removed_groups": removed, "changes": {},
                "note": f"change not shown: the earlier run used rubric {then_version}, "
                        f"this run {now_version}"}
    earlier = {g["group"]: {e["id"]: e for e in g["rubric"]} for g in then}
    changes: dict[str, dict[str, Change]] = {}
    for group in now:
        rows = earlier.get(group["group"])
        changes[group["group"]] = {
            entry["id"]: _change(entry, None if rows is None else rows.get(entry["id"]))
            for entry in group["rubric"]
        }
    return {"comparable": True, "note": None, "changes": changes, "removed_groups": removed}
