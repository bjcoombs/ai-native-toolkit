"""Compare two fleet runs: what moved in levels, and what moved in the measured set (issue #552).

A run is a directory holding ``repos.jsonl``, ``groups.jsonl`` and the run
stamp ``run.json`` (an ``--out`` directory or a history entry). ``load_run``
reads one; ``compare`` is a pure function of two loaded runs.

Comparability follows ``diff_reliability._diff_is_reliable``: the plugin's
major version and the stamp version must match. A comparison is also refused
(``comparable: false`` with every reason, and no deltas) when the runs differ
in rubric version, window, output mode, scan selection or path hashing, or when
the earlier run's files are at another envelope version (``RunVersionError``
from ``load_run`` becomes such a reason in ``not_comparable``).

Measured-set movement is reported apart from level movement. Per group:

- ``added`` / ``removed``: repositories present on one side only;
- ``no_longer_scanned`` / ``newly_scanned``: ``ok`` on one side, ``failed``
  on the other;
- ``no_longer_active`` / ``newly_active``: ``ok`` on both sides, ``active``
  true on one side and false on the other (#569's per-row flag).

Each rubric row of each group carries its level then and now
(``{status, level}``, or null when the group or row is absent) and, when it
moved, a ``cause``:

- ``level``: the measured set is identical, so the change is real;
- ``set``: the set moved and nothing else did (no signal change in the
  repositories present on both sides for a team row, no settings change for
  an org row);
- ``set_and_level``: the set moved and so did the evidence; the change cannot
  be split.

Per repository scanned on both sides, ``signals`` lists every leaf of the
``scans`` map whose value changed (dotted path, value then and now; a side
where the leaf is absent omits its key). Lists compare whole. Output has
sorted keys and no absolute path, so comparing the same two runs again gives
the same bytes. With hashed paths on both sides, hashes compare only under one
``--hash-salt``; ``notes`` says so.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict, cast

from lib.diff_reliability import _diff_is_reliable
from lib.fleet import FLEET_ROW_VERSION, REPOS_FILE, STATUS_OK, FleetRow
from lib.fleet_groups import FLEET_GROUP_VERSION, GROUPS_FILE, GroupRow
from lib.fleet_view import FleetViewError, load_groups, read_repos
from lib.fleet_stamp import FLEET_STAMP_VERSION, RUN_FILE, FleetStamp

FLEET_COMPARE_VERSION = 1
COMPARE_FILE = "compare.json"

CAUSE_LEVEL = "level"
CAUSE_SET = "set"
CAUSE_SET_AND_LEVEL = "set_and_level"

HASH_NOTE = "both runs hash paths: hashed values compare only when both used the same --hash-salt"


class RunVersionError(ValueError):
    """A run's files are at an envelope version this code does not read."""


@dataclass(frozen=True)
class FleetRun:
    stamp: FleetStamp
    repos: list[FleetRow]
    groups: list[GroupRow]


class LevelState(TypedDict):
    status: str  # "level" | "unknown"
    level: int | None


class RowMove(TypedDict):
    id: str
    scope: str
    then: LevelState | None
    now: LevelState | None
    moved: bool
    cause: str | None  # None when not moved


class SetChanges(TypedDict):
    added: list[str]
    removed: list[str]
    no_longer_scanned: list[str]
    newly_scanned: list[str]
    no_longer_active: list[str]
    newly_active: list[str]


class GroupChange(TypedDict):
    group: str
    set_changed: bool
    set: SetChanges
    settings_changed: bool
    rows: list[RowMove]


class SignalChange(TypedDict, total=False):
    path: str  # always present
    then: Any  # absent when the leaf is absent in the earlier run
    now: Any  # absent when the leaf is absent in the later run


class RepoSignals(TypedDict):
    repo: str
    signals: list[SignalChange]


class StampSummary(TypedDict):
    scan_date: str
    window: str
    rubric_version: str | None
    plugin_version: str
    toolkit_commit: str | None


class Comparison(TypedDict):
    fleet_compare_version: int
    comparable: bool
    reasons: list[str]  # why not comparable; empty when comparable
    notes: list[str]
    earlier: StampSummary | None  # None when the earlier stamp could not be read
    later: StampSummary
    movement: bool  # any set, level or signal movement
    repositories: SetChanges
    groups: list[GroupChange]
    repos: list[RepoSignals]


# --- loading ---------------------------------------------------------------


def _first_version(path: Path, key: str) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FleetViewError(f"{path.name}: cannot read ({exc.strerror})") from exc
    for line in text.splitlines():
        if line.strip():
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise FleetViewError(f"{path.name}: not JSON ({exc})") from exc
            return row.get(key) if isinstance(row, dict) else None
    return None


def load_run(run_dir: Path) -> FleetRun:
    """One run's stamp, repository rows and group rows.

    Raises ``RunVersionError`` when a file is at another envelope version (or
    there is no ``run.json``: a run from before #569), ``FleetViewError`` when
    a file is missing or malformed.
    """
    stamp_path = run_dir / RUN_FILE
    if not stamp_path.is_file():
        if (run_dir / REPOS_FILE).is_file():
            raise RunVersionError(f"{run_dir.name}: no {RUN_FILE} (a run from before the run stamp)")
        raise FleetViewError(f"{run_dir}: not a fleet run (no {RUN_FILE} or {REPOS_FILE})")
    try:
        stamp: Any = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FleetViewError(f"{RUN_FILE}: cannot read ({exc})") from exc
    if not isinstance(stamp, dict):
        raise FleetViewError(f"{RUN_FILE}: not a JSON object")
    if stamp.get("fleet_stamp_version") != FLEET_STAMP_VERSION:
        raise RunVersionError(f"run stamp version {stamp.get('fleet_stamp_version')!r}, "
                              f"this toolkit reads {FLEET_STAMP_VERSION}")
    for name, key, version in ((REPOS_FILE, "fleet_row_version", FLEET_ROW_VERSION),
                               (GROUPS_FILE, "fleet_group_version", FLEET_GROUP_VERSION)):
        found = _first_version(run_dir / name, key)
        if found is not None and found != version:
            raise RunVersionError(f"{name} {key} {found!r}, this toolkit reads {version}")
    return FleetRun(cast(FleetStamp, stamp), read_repos(run_dir), load_groups(run_dir))


# --- comparability ---------------------------------------------------------


def comparability(earlier: FleetStamp, later: FleetStamp) -> tuple[list[str], list[str]]:
    """``(reasons, notes)``: every reason the runs are not comparable, and caveats."""
    reasons: list[str] = []
    reliable, note = _diff_is_reliable(
        earlier.get("plugin_version"), later.get("plugin_version"),
        earlier.get("fleet_stamp_version"), later.get("fleet_stamp_version"))
    if not reliable and note:
        reasons.append(note)
    for key, label in (("rubric_version", "rubric version"), ("window", "window"),
                       ("counts_only", "counts-only mode"), ("scans", "scan selection"),
                       ("paths_hashed", "path hashing")):
        if earlier.get(key) != later.get(key):
            reasons.append(f"{label} changed {earlier.get(key)!r}->{later.get(key)!r}")
    notes = [HASH_NOTE] if earlier.get("paths_hashed") and later.get("paths_hashed") else []
    return reasons, notes


def _summary(stamp: FleetStamp) -> StampSummary:
    return {"scan_date": stamp["scan_date"], "window": stamp["window"],
            "rubric_version": stamp["rubric_version"], "plugin_version": stamp["plugin_version"],
            "toolkit_commit": stamp["toolkit_commit"]}


def _empty_set() -> SetChanges:
    return {"added": [], "removed": [], "no_longer_scanned": [], "newly_scanned": [],
            "no_longer_active": [], "newly_active": []}


def not_comparable(later: FleetStamp, reasons: list[str], earlier: FleetStamp | None = None) -> Comparison:
    """A refusal: the reasons, both stamps, no deltas."""
    return {"fleet_compare_version": FLEET_COMPARE_VERSION, "comparable": False, "reasons": reasons,
            "notes": [], "earlier": _summary(earlier) if earlier is not None else None,
            "later": _summary(later), "movement": False, "repositories": _empty_set(),
            "groups": [], "repos": []}


# --- deltas ----------------------------------------------------------------


_ABSENT = object()


def _leaves(value: Any, prefix: str) -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict) and value:
        for key in sorted(value):
            yield from _leaves(value[key], f"{prefix}.{key}" if prefix else str(key))
    else:
        yield prefix, value


def signal_changes(then: Any, now: Any) -> list[SignalChange]:
    """Every leaf path of two ``scans`` maps whose value differs, sorted by path."""
    before = dict(_leaves(then or {}, ""))
    after = dict(_leaves(now or {}, ""))
    changes: list[SignalChange] = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path, _ABSENT), after.get(path, _ABSENT)
        if old == new:
            continue
        change: SignalChange = {"path": path}
        if old is not _ABSENT:
            change["then"] = old
        if new is not _ABSENT:
            change["now"] = new
        changes.append(change)
    return changes


def set_changes(then: dict[str, FleetRow], now: dict[str, FleetRow]) -> SetChanges:
    """How the measured set moved between two ``{repo: row}`` maps."""
    changes = _empty_set()
    changes["added"] = sorted(set(now) - set(then))
    changes["removed"] = sorted(set(then) - set(now))
    for repo in sorted(set(then) & set(now)):
        was_ok, is_ok = then[repo]["status"] == STATUS_OK, now[repo]["status"] == STATUS_OK
        if was_ok and not is_ok:
            changes["no_longer_scanned"].append(repo)
        elif is_ok and not was_ok:
            changes["newly_scanned"].append(repo)
        elif was_ok and is_ok and then[repo].get("active") is True and now[repo].get("active") is False:
            changes["no_longer_active"].append(repo)
        elif was_ok and is_ok and then[repo].get("active") is False and now[repo].get("active") is True:
            changes["newly_active"].append(repo)
    return changes


def _any(changes: SetChanges) -> bool:
    return any(changes[key] for key in changes)  # type: ignore[literal-required]


def _levels(group: GroupRow | None) -> dict[str, tuple[str, LevelState]]:
    if group is None:
        return {}
    return {row["id"]: (row["scope"], {"status": row["status"], "level": row["level"]})
            for row in group["rubric"]}


def _cause(scope: str, present_both: bool, set_moved: bool, evidence_moved: bool,
           settings_moved: bool) -> str:
    if not present_both:
        return CAUSE_SET
    moved_inputs = settings_moved if scope == "org" else evidence_moved
    if scope == "org":
        set_moved = False  # org rows read the group's settings, not its repositories
    if set_moved:
        return CAUSE_SET_AND_LEVEL if moved_inputs else CAUSE_SET
    return CAUSE_LEVEL


def _by_group(rows: Iterable[FleetRow]) -> dict[str, dict[str, FleetRow]]:
    out: dict[str, dict[str, FleetRow]] = {}
    for row in rows:
        out.setdefault(row["group"], {})[row["repo"]] = row
    return out


def group_change(name: str, then_rows: dict[str, FleetRow], now_rows: dict[str, FleetRow],
                 then_group: GroupRow | None, now_group: GroupRow | None,
                 changed_repos: set[str]) -> GroupChange:
    moved_set = set_changes(then_rows, now_rows)
    set_moved = _any(moved_set)
    evidence_moved = any(repo in changed_repos for repo in set(then_rows) & set(now_rows))
    settings_moved = (then_group is not None and now_group is not None
                      and (then_group["settings"], then_group["settings_scope"])
                      != (now_group["settings"], now_group["settings_scope"]))
    then_levels, now_levels = _levels(then_group), _levels(now_group)
    rows: list[RowMove] = []
    for row_id in sorted(set(then_levels) | set(now_levels)):
        scope = (now_levels.get(row_id) or then_levels[row_id])[0]
        then = then_levels[row_id][1] if row_id in then_levels else None
        now = now_levels[row_id][1] if row_id in now_levels else None
        moved = then != now
        cause = _cause(scope, then is not None and now is not None, set_moved, evidence_moved,
                       settings_moved) if moved else None
        rows.append({"id": row_id, "scope": scope, "then": then, "now": now, "moved": moved, "cause": cause})
    return {"group": name, "set_changed": set_moved, "set": moved_set,
            "settings_changed": settings_moved, "rows": rows}


def compare(earlier: FleetRun, later: FleetRun) -> Comparison:
    """The comparison of two loaded runs; ``comparable: false`` with reasons and no deltas when refused."""
    reasons, notes = comparability(earlier.stamp, later.stamp)
    if reasons:
        return not_comparable(later.stamp, reasons, earlier.stamp)
    then_repos = {row["repo"]: row for row in earlier.repos}
    now_repos = {row["repo"]: row for row in later.repos}
    repos: list[RepoSignals] = []
    for repo in sorted(set(then_repos) & set(now_repos)):
        if then_repos[repo]["status"] == STATUS_OK and now_repos[repo]["status"] == STATUS_OK:
            changes = signal_changes(then_repos[repo]["scans"], now_repos[repo]["scans"])
            if changes:
                repos.append({"repo": repo, "signals": changes})
    changed = {entry["repo"] for entry in repos}
    then_by, now_by = _by_group(earlier.repos), _by_group(later.repos)
    then_groups = {g["group"]: g for g in earlier.groups}
    now_groups = {g["group"]: g for g in later.groups}
    groups = [
        group_change(name, then_by.get(name, {}), now_by.get(name, {}), then_groups.get(name),
                     now_groups.get(name), changed)
        for name in sorted(set(then_by) | set(now_by) | set(then_groups) | set(now_groups))
    ]
    overall = set_changes(then_repos, now_repos)
    movement = _any(overall) or bool(repos) or any(row["moved"] for g in groups for row in g["rows"])
    return {"fleet_compare_version": FLEET_COMPARE_VERSION, "comparable": True, "reasons": [],
            "notes": notes, "earlier": _summary(earlier.stamp), "later": _summary(later.stamp),
            "movement": movement, "repositories": overall, "groups": groups, "repos": repos}


def view_check(now: Path, then: Path) -> tuple[list[str], list[str]]:
    """For a level-only view of two runs (``heatmap.py --compare``): ``(reasons, set_moved)``.

    ``reasons`` says why the runs are not comparable (this module's rule: rubric
    version, window, output mode, scans, path hashing, plugin major version,
    file versions); ``set_moved`` names the groups whose measured set moved, so
    a level change there is not read as a real one. Both are empty when either
    path is not a stamped run directory (a bare ``groups.jsonl``): the view then
    compares levels only.
    """
    if not ((now / RUN_FILE).is_file() and (then / RUN_FILE).is_file()):
        return [], []
    try:
        later, earlier = load_run(now), load_run(then)
    except (RunVersionError, FleetViewError) as exc:
        return [str(exc)], []
    result = compare(earlier, later)
    if not result["comparable"]:
        return result["reasons"], []
    return [], [group["group"] for group in result["groups"] if group["set_changed"]]


def dumps_comparison(comparison: Comparison) -> str:
    return json.dumps(comparison, sort_keys=True, indent=2) + "\n"


def summary_lines(comparison: Comparison) -> list[str]:
    """A short human summary for stderr."""
    if not comparison["comparable"]:
        return ["not comparable: " + "; ".join(comparison["reasons"])]
    if not comparison["movement"]:
        return ["no movement"]
    sets = comparison["repositories"]
    lines = [f"{key.replace('_', ' ')}: {', '.join(sets[key])}"  # type: ignore[literal-required]
             for key in sets if sets[key]]  # type: ignore[literal-required]
    if comparison["repos"]:
        lines.append(f"signals changed in: {', '.join(r['repo'] for r in comparison['repos'])}")
    for group in comparison["groups"]:
        for row in group["rows"]:
            if row["moved"]:
                lines.append(f"{group['group']} {row['id']}: {_show(row['then'])} -> {_show(row['now'])} "
                             f"({row['cause']})")
    return lines


def _show(state: LevelState | None) -> str:
    if state is None:
        return "absent"
    return "unknown" if state["level"] is None else str(state["level"])


def write_comparison(out_dir: Path, comparison: Comparison) -> Path:
    """Write ``<out_dir>/compare.json`` through a temp file and ``os.replace``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / COMPARE_FILE
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{COMPARE_FILE}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(dumps_comparison(comparison))
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def clear_stale_comparison(out_dir: Path) -> bool:
    """Remove ``<out_dir>/compare.json`` left by an earlier run, only when this tool wrote it.

    A regular file (not a symlink) whose JSON carries ``fleet_compare_version``;
    anything else at that name is left alone. True when one was removed.
    """
    path = out_dir / COMPARE_FILE
    if path.is_symlink() or not path.is_file():
        return False
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict) or "fleet_compare_version" not in data:
        return False
    path.unlink(missing_ok=True)
    return True
