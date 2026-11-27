"""Compare two fleet runs: what moved in levels, and what moved in the measured set (issue #552).

A run is a directory holding ``repos.jsonl``, ``groups.jsonl`` and the run
stamp ``run.json`` (an ``--out`` directory or a history entry). ``load_run``
reads one; ``compare`` is a pure function of two loaded runs.

Comparability rests on the stamped versions, never the plugin's major version
(#867; the ``/assess`` snapshot trend keeps its own rule in
``diff_reliability._diff_is_reliable``). A comparison is refused
(``comparable: false`` with every reason, and no deltas) when the runs differ
in run stamp version, maturity-model version, window, output mode, scan selection, path hashing or
``--fleet-config`` (its ``fleet_config_sha256``; a stamp from before #706 reads as
no fleet config) or ``--platform`` (``platform``; a stamp from before #714 reads
as ``false``) or in the rules of the maturity model (``rules_sha256``, #952, when both
stamps record one; a stamp from before #952 compares with a note), or when
the earlier run's files are at another envelope version (``RunVersionError``
from ``load_run`` becomes such a reason in ``not_comparable``).

Measured-set movement is reported apart from level movement. Per group:

- ``added`` / ``removed``: repositories present on one side only;
- ``no_longer_scanned`` / ``newly_scanned``: ``ok`` on one side, ``failed``
  on the other;
- ``no_longer_active`` / ``newly_active``: ``ok`` on both sides, ``active``
  true on one side and false on the other (#569's per-row flag).

Each maturity model row of each group carries its level then and now
(``{status, level}``, plus ``floor`` on an unknown row with a confirmed
floor (#697), or null when the group or row is absent) and, when it moved, a
``cause``. A row moves only when its status or level changes: a floor that
appeared or changed (an entry written before #697 has none) is not a move.

- ``level``: the measured set is identical, so the change is real; an org
  row present on both sides is always ``level``, because it reads the group's
  settings (``settings_changed`` says whether they moved), not its repositories;
- ``set``: the group or row is on one side only, or (team rows) the set moved
  and no repository present on both sides changed a signal;
- ``set_and_level``: (team rows) the set moved and so did the evidence; the
  change cannot be split.

With a scanner diff (``lib/fleet/fleet_scanner_diff.py``, #774) a moved team row may instead
carry ``scanner_changed`` or ``toolkit_changed_unknown_scope``, and a row unknown on one
side for a signal that run's scans lack is ``set`` with ``absent_signals``.

Per repository scanned on both sides, ``signals`` lists every leaf of the
``scans`` map whose value changed (dotted path, value then and now; a side
where the leaf is absent omits its key). A pure rename is read under its new path on both
sides first (``lib.signal_aliases.renamed``, #970), so it is no leaf change. When the two rows'
``signals_schema_version`` differ, the lower side reads each signal whose meaning changed
since as absent (``aligned``, ``lib/signal_meaning.py``, #867); a moved team row reading one
is ``set`` with ``absent_signals``. Lists compare whole. Output has
sorted keys and no absolute path, so comparing the same two runs again gives
the same bytes. With hashed paths on both sides, hashes compare only under one
``--hash-salt``; ``notes`` says so.

Compares two fleet runs (issue #552), driven by `fleet.py --compare` and
`skills/fleet/scripts/fleet_compare.py`. `load_run` reads a run directory (raising
`RunVersionError` for another envelope version or a run with no `run.json`, which callers
turn into a not-comparable result). `comparability` checks the stamped versions, never the
plugin major (#867): run stamp version, maturity-model version, window, output mode, scan selection, path hashing and the
`--fleet-config` hash (`fleet_config_sha256`, read with `.get`: a pre-#706 stamp is no config) and
`platform` (a pre-#714 stamp is false), and the rules hash (`rules_sha256`, #952,
`lib/fleet/fleet_method.py`: a reason only when both stamps recorded one, and the practice
packs, #1273: a stamp without `packs` names none; an unrecorded side
or a collector revision change is a note, printed by `comparison_lines` too); any reason
gives `comparable: false` with no deltas. `compare` reports the measured-set movement
(`set_changes`: added, removed, no longer / newly scanned, no longer / newly active from
the row's `active`) apart from level movement: each group's maturity model rows then and now with
a `cause` of `level` (set unchanged), `set` (the group or row on one side, or only a
team row's set moved) or `set_and_level` (a team row's set and evidence moved), plus the #774 causes below. An org
row reads the group's settings, not its repositories, so on both sides it is `level`. `signal_changes` lists the changed leaves of each repository's `scans` map, read through `aligned` (#867:
the lower `signals_schema_version` side reads each signal `lib/signal_meaning.py` lists as changed since as absent).
`write_comparison` writes `compare.json` atomically; `clear_stale_comparison` removes
only a regular `compare.json` carrying `fleet_compare_version`. `view_check` is the gate
`heatmap.py --compare` uses for two stamped run directories (the same refusal reasons,
plus the groups whose set moved), so the level-only view and `compare.json` share one rule.
A level state carries `floor` when the row has one (#697); `moved` still compares status and level only, so a floor appearing against an entry written before #697 is not a move.
`compare` takes an optional `scanner` (`fleet_scanner_diff.scanner_diff` of the two stamps,
passed by both CLIs) and re-attributes moved team rows through `fleet_scanner_diff.attribute`;
`FleetRun.row_signals` carries the signals each row's `fleet-data.json` cells name (#774), and
`FleetRun.steps` each repository's stored Step from the run's `level.json` (`stored_steps`, #941;
None for a run without one), which the Step movement grid reads (`lib/fleet/fleet_step_movement.py`).
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

from lib.fleet.runner import FLEET_ROW_VERSION, REPOS_FILE, STATUS_OK, FleetRow
from lib.fleet.fleet_data import FLEET_DATA_FILE
from lib.fleet.fleet_level import LEVEL_FILE
from lib.fleet.fleet_groups import FLEET_GROUP_VERSION, GROUPS_FILE, GroupRow, RowLevel
from lib.fleet.fleet_legacy import stamp_value, upgrade
from lib.fleet.fleet_method import METHOD_PREFIXES, method_notes, rule_reasons
from lib.scoring.levels import floor_of
from lib.signal_aliases import renamed
from lib.signal_meaning import held, reads, stale_paths, without
from lib.fleet.fleet_view import FleetViewError, load_groups, read_repos
from lib.fleet.fleet_stamp import FLEET_STAMP_VERSION, RUN_FILE, FleetStamp
from lib.fleet.fleet_scanner_diff import (
    ScannerCause,
    ScannerDiff,
    absent_signals,
    attribute,
    changed_blocks,
    producers,
    row_signals,
    unavailable_note,
    UNAVAILABLE_PREFIX,
)

FLEET_COMPARE_VERSION = 2  # 2: levels_version replaced rubric_version (#670)
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
    # row id -> the signals its fleet-data.json cells name (#774); None without that file
    row_signals: Mapping[str, frozenset[str]] | None = None
    # repository id -> its stored Step from the run's level.json (#941); None without that file
    steps: Mapping[str, int | None] | None = None


class LevelState(TypedDict):
    status: str  # "level" | "unknown"
    level: int | None
    floor: NotRequired[int]  # an unknown row's confirmed floor (#697); absent otherwise


class RowMove(TypedDict):
    id: str
    scope: str
    then: LevelState | None
    now: LevelState | None
    moved: bool
    cause: str | None  # None when not moved
    scanner: NotRequired[list[ScannerCause]]  # cause scanner_changed: the blocks and changed modules (#774)
    absent_signals: NotRequired[list[str]]  # cause set: signals one run's scans lack (#774)
    set_moved: NotRequired[bool]  # a #774 cause replaced set_and_level: the measured set moved too


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
    levels_version: str | None
    plugin_version: str
    toolkit_commit: str | None


class Comparison(TypedDict):
    fleet_compare_version: int
    comparable: bool
    reasons: list[str]  # why not comparable; empty when comparable
    notes: list[str]
    earlier: StampSummary | None  # None when the earlier stamp could not be read
    later: StampSummary | None  # None when the later run could not be read at this version
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


# The stamp keys a comparison reads, with the types they may hold.
_STAMP_FIELDS: tuple[tuple[str, tuple[type, ...]], ...] = (
    ("scan_date", (str,)), ("window", (str,)), ("plugin_version", (str,)),
    ("levels_version", (str, type(None))), ("toolkit_commit", (str, type(None))),
    ("counts_only", (bool,)), ("paths_hashed", (bool,)), ("scans", (list, type(None))),
)


def _check_stamp(stamp: dict[str, Any]) -> None:
    """Raise ``FleetViewError`` when a current-version stamp lacks a key a comparison reads."""
    for key, types in _STAMP_FIELDS:
        if key not in stamp or not isinstance(stamp[key], types):
            raise FleetViewError(f"{RUN_FILE}: missing or malformed {key!r}")


def load_run(run_dir: Path) -> FleetRun:
    """One run's stamp, repository rows and group rows.

    Raises ``RunVersionError`` when a file is at another envelope version (or
    there is no ``run.json``: a run from before #569), ``FleetViewError`` when
    a file is missing or malformed (including a stamp without the keys a
    comparison reads).
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
    stamp = upgrade(stamp)  # a run written before the 2.0.0 rename
    if stamp.get("fleet_stamp_version") != FLEET_STAMP_VERSION:
        raise RunVersionError(f"run stamp version {stamp.get('fleet_stamp_version')!r}, "
                              f"this toolkit reads {FLEET_STAMP_VERSION}")
    _check_stamp(stamp)
    for name, key, version in ((REPOS_FILE, "fleet_row_version", FLEET_ROW_VERSION),
                               (GROUPS_FILE, "fleet_group_version", FLEET_GROUP_VERSION)):
        found = _first_version(run_dir / name, key)
        if found is not None and found != version:
            raise RunVersionError(f"{name} {key} {found!r}, this toolkit reads {version}")
    return FleetRun(cast(FleetStamp, stamp), read_repos(run_dir), load_groups(run_dir),
                    _row_signals(run_dir / FLEET_DATA_FILE), stored_steps(run_dir / LEVEL_FILE))


def stored_steps(path: Path) -> dict[str, int | None] | None:
    """Each repository's Step as a run's ``level.json`` stored it (#941); None when the file is
    absent, unreadable or not that shape (a run kept before #941, or one without ``--levels``)."""
    try:
        repos = json.loads(path.read_text(encoding="utf-8")).get("repos")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(repos, dict):
        return None
    return {k: v.get("step") if isinstance(v, dict) and isinstance(v.get("step"), int) else None
            for k, v in repos.items() if isinstance(k, str)}


def _row_signals(path: Path) -> dict[str, frozenset[str]] | None:
    """The row signals of a run's ``fleet-data.json``; None when it is absent or unreadable."""
    try:
        return row_signals(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


# --- comparability ---------------------------------------------------------


def comparability(earlier: FleetStamp, later: FleetStamp) -> tuple[list[str], list[str]]:
    """``(reasons, notes)``: every reason the runs are not comparable, and caveats."""
    reasons: list[str] = []
    # #867: the stamped versions decide, never the plugin's major version (a proxy for them)
    for key, label in (("fleet_stamp_version", "run stamp version"),
                       ("levels_version", "maturity-model version"), ("window", "window"),
                       ("counts_only", "counts-only mode"), ("scans", "scan selection"),
                       ("paths_hashed", "path hashing"), ("fleet_config_sha256", "fleet config"),
                       ("platform", "platform pass"), ("managed_settings", "managed settings read")):
        then, now = stamp_value(earlier, key), stamp_value(later, key)
        if then != now:
            reasons.append(f"{label} changed {then!r}->{now!r}")
    # the rules hash (#952): only when both recorded one; then the practice packs (#1273)
    reasons += rule_reasons(earlier, later)
    notes = [HASH_NOTE] if earlier.get("paths_hashed") and later.get("paths_hashed") else []
    return reasons, notes + method_notes(earlier, later)


def _summary(stamp: FleetStamp) -> StampSummary:
    return {"scan_date": stamp["scan_date"], "window": stamp["window"],
            "levels_version": stamp["levels_version"], "plugin_version": stamp["plugin_version"],
            "toolkit_commit": stamp["toolkit_commit"]}


def _empty_set() -> SetChanges:
    return {"added": [], "removed": [], "no_longer_scanned": [], "newly_scanned": [],
            "no_longer_active": [], "newly_active": []}


def not_comparable(later: FleetStamp | None, reasons: list[str], earlier: FleetStamp | None = None) -> Comparison:
    """A refusal: the reasons, the stamps that could be read, no deltas."""
    return {"fleet_compare_version": FLEET_COMPARE_VERSION, "comparable": False, "reasons": reasons,
            "notes": [], "earlier": _summary(earlier) if earlier is not None else None,
            "later": _summary(later) if later is not None else None, "movement": False,
            "repositories": _empty_set(),
            "groups": [], "repos": []}


# --- deltas ----------------------------------------------------------------


_ABSENT = object()


def _leaves(value: Any, prefix: str) -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict) and (value or not prefix):  # an empty scans map has no leaves
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
    return any(changes.values())


def _levels(group: GroupRow | None) -> dict[str, tuple[str, LevelState]]:
    if group is None:
        return {}
    return {row["id"]: (row["scope"], _state(row)) for row in group["levels"]}


def _state(row: RowLevel) -> LevelState:
    state: LevelState = {"status": row["status"], "level": row["level"]}
    floor = floor_of(row)
    if floor is not None:
        state["floor"] = floor
    return state


def _bare(state: LevelState | None) -> tuple[str, int | None] | None:
    return None if state is None else (state["status"], state["level"])


def _cause(scope: str, present_both: bool, set_moved: bool, evidence_moved: bool) -> str:
    """Why a moved row moved. An org row reads the group's settings, not its
    repositories, so on both sides it is always ``level``; it is ``set`` only
    when the group or row is on one side."""
    if not present_both:
        return CAUSE_SET
    if scope == "org" or not set_moved:
        return CAUSE_LEVEL
    return CAUSE_SET_AND_LEVEL if evidence_moved else CAUSE_SET


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
        # A floor appearing (an entry written before #697 has none) is not a move.
        moved = _bare(then) != _bare(now)
        cause = _cause(scope, then is not None and now is not None, set_moved,
                       evidence_moved) if moved else None
        rows.append({"id": row_id, "scope": scope, "then": then, "now": now, "moved": moved, "cause": cause})
    return {"group": name, "set_changed": set_moved, "set": moved_set,
            "settings_changed": settings_moved, "rows": rows}


def compare(earlier: FleetRun, later: FleetRun, scanner: ScannerDiff | None = None,
            producer_map: Mapping[str, frozenset[str]] | None = None) -> Comparison:
    """The comparison of two loaded runs; ``comparable: false`` with reasons and no deltas when refused.

    ``scanner`` (``lib.fleet.fleet_scanner_diff.scanner_diff`` of the two stamps) turns on the
    scanner attribution of #774; without it a move is never tagged ``scanner_changed``.
    A signal one run's scans lack is measured-set movement either way."""
    reasons, notes = comparability(earlier.stamp, later.stamp)
    if reasons:
        return not_comparable(later.stamp, reasons, earlier.stamp)
    then_repos = {row["repo"]: row for row in earlier.repos}
    now_repos = {row["repo"]: row for row in later.repos}
    repos: list[RepoSignals] = []
    stale: dict[str, list[str]] = {}
    for repo in sorted(set(then_repos) & set(now_repos)):
        if then_repos[repo]["status"] == STATUS_OK and now_repos[repo]["status"] == STATUS_OK:
            then_scans, now_scans, stale[repo] = aligned(then_repos[repo], now_repos[repo])
            changes = signal_changes(then_scans, now_scans)
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
    _attribute(groups, earlier, later, then_by, now_by, repos, scanner, producer_map, stale)
    if scanner is not None and scanner["status"] == "unavailable":
        notes = [*notes, unavailable_note(scanner)]
    overall = set_changes(then_repos, now_repos)
    movement = _any(overall) or bool(repos) or any(row["moved"] for g in groups for row in g["rows"])
    return {"fleet_compare_version": FLEET_COMPARE_VERSION, "comparable": True, "reasons": [],
            "notes": notes, "earlier": _summary(earlier.stamp), "later": _summary(later.stamp),
            "movement": movement, "repositories": overall, "groups": groups, "repos": repos}


def aligned(then: FleetRow, now: FleetRow) -> tuple[Any, Any, list[str]]:
    """Two ok rows' scans read under one schema (#867): ``(then, now, stale)``.

    Renames are read under the new path (``renamed``). The side at the lower
    ``signals_schema_version`` reads each signal whose meaning changed since as absent
    (``lib/signal_meaning.py``); at a version this toolkit does not know, the earlier side
    reads every signal as absent. ``stale`` lists the paths dropped that the side held."""
    then_scans, now_scans = renamed(then["scans"]), renamed(now["scans"])
    old, new = then.get("signals_schema_version"), now.get("signals_schema_version")
    paths = stale_paths(old, new)
    later_older = paths is not None and type(old) is int and type(new) is int and new < old
    older = now_scans if later_older else then_scans
    if paths is None:
        dropped = sorted(older) if isinstance(older, dict) else []
        stripped: Any = {}
    else:
        dropped = sorted(p for p in paths if held(older, p))
        stripped = without(older, paths)
    return (then_scans, stripped, dropped) if later_older else (stripped, now_scans, dropped)


def _attribute(groups: list[GroupChange], earlier: FleetRun, later: FleetRun,
               then_by: dict[str, dict[str, FleetRow]], now_by: dict[str, dict[str, FleetRow]],
               repos: list[RepoSignals], scanner: ScannerDiff | None,
               producer_map: Mapping[str, frozenset[str]] | None,
               stale: Mapping[str, list[str]]) -> None:
    """Re-attribute moved team rows to an absent signal or a scanner change (``lib/fleet/fleet_scanner_diff.py``).

    A signal whose meaning changed (``stale``, #867) that a row reads counts as absent."""
    blocks: dict[str, list[str]] = {}
    if scanner is not None and scanner["status"] == "changed":
        blocks = changed_blocks(scanner, producers() if producer_map is None else producer_map)
    moved_signals = {entry["repo"]: {c["path"].split(".")[0] for c in entry["signals"]} for entry in repos}
    sides = {g["group"]: g for g in earlier.groups}, {g["group"]: g for g in later.groups}
    for group in groups:
        name = group["group"]
        both = sorted(r for r in set(then_by.get(name, {})) & set(now_by.get(name, {}))
                      if then_by[name][r]["status"] == STATUS_OK and now_by[name][r]["status"] == STATUS_OK)
        then_scans = [then_by[name][r]["scans"] for r in both]
        now_scans = [now_by[name][r]["scans"] for r in both]
        moved_blocks = {b for r in both for b in moved_signals.get(r, set())}
        for row in group["rows"]:
            if not row["moved"] or row["scope"] == "org" or row["cause"] is None:
                continue
            absent: list[str] = []
            for side, (mine, other) in ((0, (then_scans, now_scans)), (1, (now_scans, then_scans))):
                state = row["then"] if side == 0 else row["now"]
                stored = sides[side].get(name)
                if state is not None and state["status"] == "unknown" and stored is not None:
                    missing = [s for lv in stored["levels"] if lv["id"] == row["id"] for s in lv["missing_signals"]]
                    absent += absent_signals(missing, mine, other)
            absent += _stale_reads(row["id"], earlier, later, [stale.get(r, []) for r in both])
            reads = _row_blocks(row["id"], earlier, later)
            result = attribute(row["cause"], absent=absent, diff=scanner, blocks_changed=blocks,
                               row_blocks=reads, moved_blocks=moved_blocks)
            row["cause"] = result["cause"]
            if "scanner" in result:
                row["scanner"] = result["scanner"]
            if "absent_signals" in result:
                row["absent_signals"] = result["absent_signals"]
            if result.get("set_moved"):
                row["set_moved"] = True


def _stale_reads(row_id: str, earlier: FleetRun, later: FleetRun, stale: list[list[str]]) -> list[str]:
    """The stale paths a row reads, from either run's cell signals; every one when neither has them."""
    found = [run.row_signals.get(row_id, frozenset()) for run in (earlier, later) if run.row_signals is not None]
    signals = frozenset().union(*found) if found and any(found) else None
    return sorted({p for paths in stale for p in paths if signals is None or reads(p, signals)})


def _row_blocks(row_id: str, earlier: FleetRun, later: FleetRun) -> frozenset[str] | None:
    """The blocks a row reads, from either run's cell signals; None when neither run has them."""
    found = [run.row_signals.get(row_id, frozenset()) for run in (earlier, later) if run.row_signals is not None]
    if not found or not any(found):
        return None
    return frozenset(signal.split(".")[0] for signals in found for signal in signals)


def view_check(now: Path, then: Path) -> tuple[list[str], list[str]]:
    """For a level-only view of two runs (``heatmap.py --compare``): ``(reasons, set_moved)``.

    ``reasons`` says why the runs are not comparable (this module's rule: maturity model
    version, window, output mode, scans, path hashing, fleet config, platform pass, run stamp version,
    file versions, a run with no ``run.json``); ``set_moved`` names the groups
    whose measured set moved, so a level change there is not read as a real
    one. Both are empty when either path is not a run directory (a bare
    ``groups.jsonl``, or a directory holding only one): the view then compares
    levels only. A pair of run directories where one lacks ``run.json`` is
    refused, as ``fleet_compare.py`` refuses it. A malformed run raises
    ``FleetViewError`` (the caller's exit 2), as in ``fleet_compare.py``.
    """
    def run_dir(path: Path) -> bool:
        return path.is_dir() and ((path / RUN_FILE).is_file() or (path / REPOS_FILE).is_file())

    if not (run_dir(now) and run_dir(then)):
        return [], []
    try:
        earlier, later = load_run(then), load_run(now)
    except RunVersionError as exc:
        return [str(exc)], []
    result = compare(earlier, later)
    if not result["comparable"]:
        return result["reasons"], []
    return [], [group["group"] for group in result["groups"] if group["set_changed"]]


def dumps_comparison(comparison: Comparison) -> str:
    return json.dumps(comparison, sort_keys=True, indent=2) + "\n"


def comparison_lines(comparison: Comparison) -> list[str]:
    """A short human summary for stderr."""
    if not comparison["comparable"]:
        return ["not comparable: " + "; ".join(comparison["reasons"])]
    if not comparison["movement"]:
        return ["no movement", *_scanner_notes(comparison)]
    sets = comparison["repositories"]
    named = (("added", sets["added"]), ("removed", sets["removed"]),
             ("no longer scanned", sets["no_longer_scanned"]), ("newly scanned", sets["newly_scanned"]),
             ("no longer active", sets["no_longer_active"]), ("newly active", sets["newly_active"]))
    lines = [f"{label}: {', '.join(repos)}" for label, repos in named if repos]
    if comparison["repos"]:
        lines.append(f"signals changed in: {', '.join(r['repo'] for r in comparison['repos'])}")
    for group in comparison["groups"]:
        for row in group["rows"]:
            if row["moved"]:
                lines.append(f"{group['group']} {row['id']}: {_show(row['then'])} -> {_show(row['now'])} "
                             f"({row['cause']}){_why(row)}")
    return [*lines, *_scanner_notes(comparison)]


def _scanner_notes(comparison: Comparison) -> list[str]:
    return [note for note in comparison["notes"] if note.startswith((UNAVAILABLE_PREFIX, *METHOD_PREFIXES))]


def _why(row: RowMove) -> str:
    """What a #774 cause rests on: the changed producers, or the signals one run lacks."""
    also = "; the measured set of repositories moved too" if row.get("set_moved") else ""
    if "scanner" in row:
        named = "; ".join(f"{c['block']} <- {', '.join(c['modules'])}" for c in row["scanner"])
        return f": the scanner changed ({named}); not read as a repository move{also}"
    if "absent_signals" in row:
        return (f": {', '.join(row['absent_signals'])} absent from one run's scans; "
                f"measured-set movement, not a level move{also}")
    return f": not read as a repository move{also}" if also else ""


def _show(state: LevelState | None) -> str:
    if state is None:
        return "absent"
    if state["level"] is not None:
        return str(state["level"])
    floor = floor_of(state)
    return "unknown" if floor is None else f"unknown, at least {floor}"


def write_comparison(out_dir: Path, comparison: Comparison) -> Path:
    """Write ``<out_dir>/compare.json`` through a temp file and ``os.replace``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    return write_comparison_file(out_dir / COMPARE_FILE, comparison)


def write_comparison_file(target: Path, comparison: Comparison) -> Path:
    """Write ``comparison`` to ``target`` through a temp file beside it and ``os.replace``."""
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
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
