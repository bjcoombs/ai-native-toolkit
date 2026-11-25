"""Fleet group roll-up: repository rows to one row per group (issue #568).

Reads the ``repos.jsonl`` rows ``lib/fleet/runner.py`` builds and writes one
``groups.jsonl`` row per group (a row's ``group``), carrying:

- repository counts (rows, scanned, failed);
- the distinct shared CI sources and marketplaces the group's repositories
  reference (``shared_tooling.distinct_ids``, git-tracked files only), null
  when no repository's ``shared_tooling`` block is available (every scan
  failed, degraded or was left out by ``--scans``), never a false 0;
  ``shared_tooling_repos`` says how many repositories the counts rest on;
- ``agent_building``: scanned repositories per agent-building class
  (``lib/fleet/fleet_agent_building.group_counts``, #689), null when no repository's
  block is available; additive, so rows written before it lack the key;
- the group settings joined from ghreport's settings fragments (#565);
- each maturity model row's whole level, or ``unknown``, with the aggregation rule used,
  evaluated by ``lib/levels.py`` (#563), and the row's ``top_level`` (its number of
  levels, #684), so a history entry can be read as a share of top level without the
  maturity model file. There is no second threshold config. ``top_level`` is
  additive: rows written before #684 lack it, and readers fall back.
- an ``unknown`` row's ``floor`` (#697): the level its confirmed criteria reach,
  when at least 1, so "level 1 confirmed, level 2 not measured" is not read as
  no data. Absent otherwise, never null; rows written before #697 lack it.
- a team row's ``full_run_only`` (#697): the missing signals only a full
  ``/assess`` run measures, each one unavailable in every repository missing it
  because its block carries a ``lib.signals.FULL_RUN_ONLY_REASONS`` code
  (``not_registered``, ``network``). A failed scan or a mistyped value is a
  plain gap, so such a signal is not listed, and so is one in a
  ``lib.signals.PLATFORM_SCANS`` block, which ``fleet --platform`` measures
  (``platform_signals``). Only on an ``unknown`` row (a known
  row's missing signals do not decide its level); absent when empty.

Maturity model inputs:

- ``team`` rows run against each repository's ``scans`` map, the namespace
  ``lib/level_signals.py`` validates (``nested_instructions.available``). A
  failed repository joins the roll-up with no signals, so its level is unknown
  and a ``min`` team row never reads better than the evidence allows.
- ``org`` rows run against the group's ghreport fragment (#565), the
  ``level_signals.ORG_PATHS`` namespace: ``scope``, ``provider`` and
  ``<setting>.value`` / ``.reason`` (``two_factor.value`` is ``"on"`` or
  ``"off"``). A value of ``"unknown"`` becomes an unavailable block, so the
  maturity model reports the row unknown, never a false 0. A group with no matching
  fragment has no settings: every org row is unknown.

Settings join: a fragment's ``scope`` matches a group exactly or, failing
that, the nearest ancestor (a GitLab top-level group's settings cover its
subgroups); ``settings_scope`` names the fragment used.

Counts-only output (the default) reduces each setting to a token: a value that
is already a short token (``on``, ``off``, ``full``) is kept, any other
(ruleset or push-rule names) becomes ``set``; reasons are dropped. Evaluation
always sees the full values. Rows are sorted by group with sorted keys, so a
rerun over the same inputs is byte-identical.

The group roll-up behind `groups.jsonl` (issue #568). `roll_up` groups `FleetRow`s by
`group` (sorted) and `group_row` builds each `GroupRow`: repository counts, distinct
shared CI sources and marketplaces through `shared_tooling.distinct_ids` (scanned rows
only; null with no available block, `shared_tooling_repos` counting the evidence), the agent-building class counts (`fleet_agent_building.group_counts`, #689; `NotRequired`, absent in older rows), the joined settings, and each maturity model row's level through `levels.evaluate` with its
`top_level` (the row's number of levels, #684, so a history entry reads as a share of top
level without the model file; additive and `NotRequired`, absent in older rows), with
no threshold config of its own. `load_settings` reads ghreport settings fragments
(#565) from a file, an array or a directory (a render dir's `settings/`), keyed by
`scope`, and raises `SettingsError` on a directory with no `*.json` fragment file;
`settings_for` joins a group to the exact scope or the nearest ancestor;
`normalise_settings` keeps each `{value, reason}` or, for `unknown`, turns it into
`{available: false, reason}` so `levels.resolve_signal` reads it unavailable and the
org row is unknown, never 0; a fragment's `teams` list (#703, read by `fleet_units`) is not a
setting (`NOT_SETTINGS`). `reduce_settings` is the counts-only form (token values,
else `set`; a value's reason `null`, an unavailable one's `unknown`). Maturity model data follows
`level_signals`' namespaces: `repo_signals` is a
repository's `scans` map (a failed repository's is empty, so it is an unknown
repository, never a passing one) and `settings_signals` the normalised settings plus
the fragment's `scope` and `provider`. The CLI loads the maturity model with
`level_signals.signal_path_validator()`.
`write_groups` writes atomically with sorted keys.
Since #697 an `unknown` level row carries an optional `floor` (the confirmed low bound, when at least 1) and `full_run_only` (`full_run_only_signals`: missing signals whose block is unavailable for a `signals.FULL_RUN_ONLY_REASONS` code in every repository missing them, leaving out `signals.PLATFORM_SCANS` blocks, which `fleet --platform` measures: `platform_signals` names those for next steps' `PLATFORM_HINT`, including a `signals.PLATFORM_ONLY_SCANS` block absent from the row, #779); both keys are absent otherwise, `status` stays `level` or `unknown`, and `fleet_group_version` is unchanged.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from lib.counts_only.counts_only_rules import TOKEN_RE
from lib.fleet.runner import STATUS_OK, FleetRow
from lib.fleet.fleet_agent_building import AgentBuildingCounts, group_counts
from lib.counts_only.content_free import reason_code
from lib.levels import Aggregation, Evaluation, MaturityModel, RowResult, evaluate
from lib.run_context_types import JsonDict
from lib.ci.shared_tooling import distinct_ids
from lib.signals import FULL_RUN_ONLY_REASONS, NETWORK_REASON, PLATFORM_ONLY_SCANS, PLATFORM_SCANS

# Not bumped for the 2.0.0 maturity-model key renames (#670): readers reject any other
# version, so lib/fleet/fleet_legacy.py maps the old keys and earlier history lines still load.
FLEET_GROUP_VERSION = 1
GROUPS_FILE = "groups.jsonl"
SETTINGS_META_KEYS = frozenset({"scope", "provider"})
# A fragment's GitHub team list (#703) places repositories in the units tree
# (lib/fleet/fleet_units.py); it is not a setting.
NOT_SETTINGS = SETTINGS_META_KEYS | {"teams"}
UNKNOWN = "unknown"
SET = "set"


class SettingsError(ValueError):
    """The settings input cannot be read; the message names the file and the fault."""


class RowLevel(TypedDict):
    id: str
    scope: str  # "org" | "team"
    status: str  # "level" | "unknown"
    level: int | None  # whole level; None when unknown
    floor: NotRequired[int]  # unknown rows: the confirmed low bound, when at least 1 (#697)
    full_run_only: NotRequired[list[str]]  # missing signals only a full /assess measures (#697)
    top_level: NotRequired[int]  # the row's number of levels; absent in rows before #684
    aggregation: Aggregation | None  # the team rule used; None for org rows
    missing_signals: list[str]
    unknown_repos: list[str]  # team rows: repositories whose own level is undetermined


class GroupRow(TypedDict):
    fleet_group_version: int
    group: str
    repos: int
    scanned: int  # rows with status ok
    failed: int
    shared_tooling_repos: int  # repositories with an available shared_tooling block
    distinct_ci_sources: int | None  # None: no repository gave shared_tooling evidence
    distinct_marketplaces: int | None
    agent_building: NotRequired[AgentBuildingCounts | None]  # absent in rows before #689
    settings_scope: str | None  # the fragment joined; None when none matched
    settings: JsonDict | None  # normalised (and, counts-only, reduced) settings
    levels_version: str | None  # None without a maturity model
    levels: list[RowLevel]


# --- settings input --------------------------------------------------------


def _fragments_in(path: Path) -> list[Path]:
    if path.is_dir():
        nested = path / "settings"  # a ghreport --render-dir
        folder = nested if nested.is_dir() else path
        return sorted(folder.glob("*.json"))
    return [path]


def load_settings(path: Path) -> dict[str, JsonDict]:
    """ghreport settings fragments by scope, from a file (object or array) or a directory."""
    if not path.exists():
        raise SettingsError(f"settings {path}: not found")
    files = _fragments_in(path)
    if not files:
        raise SettingsError(f"settings {path}: no fragment files (*.json)")
    by_scope: dict[str, JsonDict] = {}
    for file in files:
        try:
            data: Any = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SettingsError(f"settings {file.name}: unreadable ({exc})") from exc
        for fragment in data if isinstance(data, list) else [data]:
            scope = fragment.get("scope") if isinstance(fragment, dict) else None
            if not isinstance(scope, str) or not scope:
                raise SettingsError(f"settings {file.name}: a fragment has no 'scope'")
            if scope in by_scope:
                raise SettingsError(f"settings {file.name}: scope {scope!r} given twice")
            by_scope[scope] = fragment
    return by_scope


def normalise_settings(fragment: Mapping[str, Any]) -> JsonDict:
    """Each setting as ``{value, reason}``, or ``{available: false, reason}`` when unknown or malformed."""
    out: JsonDict = {}
    for key in sorted(k for k in fragment if k not in NOT_SETTINGS):
        raw = fragment[key]
        value = raw.get("value") if isinstance(raw, dict) else None
        if value is None or value == UNKNOWN:
            reason = raw.get("reason") if isinstance(raw, dict) else None
            out[key] = {"available": False, "reason": str(reason) if reason else UNKNOWN}
        else:
            out[key] = {"value": value, "reason": raw.get("reason")}
    return out


def reduce_settings(settings: JsonDict) -> JsonDict:
    """The counts-only form: token values kept, any other value ``set``.

    Reasons are dropped: ``null`` beside a value, ``unknown`` on an unavailable setting.
    """
    out: JsonDict = {}
    for key, block in settings.items():
        if block.get("available") is False:
            out[key] = {"available": False, "reason": UNKNOWN}
            continue
        value = block["value"]
        token = isinstance(value, (bool, int)) or (isinstance(value, str) and TOKEN_RE.match(value))
        out[key] = {"value": value if token else SET, "reason": None}
    return out


def settings_for(group: str, by_scope: Mapping[str, JsonDict]) -> tuple[str | None, JsonDict | None]:
    """The fragment for ``group``: an exact scope match, else the nearest ancestor's."""
    parts = group.split("/")
    for depth in range(len(parts), 0, -1):
        scope = "/".join(parts[:depth])
        if scope in by_scope:
            return scope, normalise_settings(by_scope[scope])
    return None, None


def settings_signals(fragment: Mapping[str, Any] | None, settings: JsonDict | None) -> JsonDict | None:
    """The org-row data: the normalised settings plus the fragment's ``scope`` and ``provider``."""
    if fragment is None or settings is None:
        return None
    meta = {key: fragment[key] for key in sorted(SETTINGS_META_KEYS) if key in fragment}
    return {**settings, **meta}


# --- roll-up ---------------------------------------------------------------


def repo_signals(row: FleetRow) -> JsonDict:
    """A repository's maturity model data: its ``scans`` map, empty for a failed row."""
    return dict(row["scans"] or {})


def _unavailable_code(data: Mapping[str, Any], signal: str) -> str | None:
    """The reason code of the signal's block when it is unavailable; None otherwise."""
    block = data.get(signal.split(".", 1)[0])
    if not isinstance(block, dict) or block.get("available") is not False:
        return None
    return reason_code(block.get("reason"))


def full_run_only(data: Mapping[str, Any], signal: str) -> bool:
    """The signal's block is unavailable for a reason only a full ``/assess`` run lifts.

    A ``PLATFORM_SCANS`` block is never full-run-only: ``fleet --platform`` measures it."""
    return (signal.split(".", 1)[0] not in PLATFORM_SCANS
            and _unavailable_code(data, signal) in FULL_RUN_ONLY_REASONS)


def platform_only(data: Mapping[str, Any], signal: str) -> bool:
    """The signal's block is a ``PLATFORM_SCANS`` one left unmeasured for want of ``--platform``:
    it reads ``network``, or it is a ``PLATFORM_ONLY_SCANS`` block absent from a row that has
    scans (#779); a failed row (no scans) claims no platform gap."""
    name = signal.split(".", 1)[0]
    if data and name in PLATFORM_ONLY_SCANS and name not in data:
        return True
    return name in PLATFORM_SCANS and _unavailable_code(data, signal) == reason_code(NETWORK_REASON)


def _missing_everywhere(evaluations: Mapping[str, Evaluation], signals: Mapping[str, Mapping[str, Any]],
                        test: Callable[[Mapping[str, Any], str], bool]) -> list[str]:
    missing: dict[str, list[str]] = {}
    for name, evaluation in evaluations.items():
        for signal in evaluation["missing_signals"]:
            missing.setdefault(signal, []).append(name)
    return sorted(signal for signal, names in missing.items()
                  if all(test(signals.get(name, {}), signal) for name in names))


def full_run_only_signals(evaluations: Mapping[str, Evaluation],
                          signals: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """The missing signals that are full-run-only in every repository missing them."""
    return _missing_everywhere(evaluations, signals, full_run_only)


def platform_signals(evaluations: Mapping[str, Evaluation],
                     signals: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """The missing signals ``fleet --platform`` would measure, in every repository missing them."""
    return _missing_everywhere(evaluations, signals, platform_only)


def _row_level(result: RowResult, top: int, signals: Mapping[str, JsonDict]) -> RowLevel:
    entry: RowLevel = {
        "id": result["id"], "scope": result["scope"], "status": result["status"],
        "level": result["level"], "top_level": top, "aggregation": result.get("aggregation"),
        "missing_signals": result["missing_signals"], "unknown_repos": result.get("unknown_repos", []),
    }
    if "floor" in result:
        entry["floor"] = result["floor"]
    only = full_run_only_signals(result.get("repos", {}), signals) if result["status"] != "level" else []
    if only:
        entry["full_run_only"] = only
    return entry


def _row_levels(maturity_model: MaturityModel, group: str, settings: JsonDict | None,
                   rows: Sequence[FleetRow]) -> list[RowLevel]:
    signals = {row["repo"]: repo_signals(row) for row in rows}
    result = evaluate(maturity_model, {"name": group, "settings": settings, "repos": signals})
    tops = {row["id"]: len(row["levels"]) for row in maturity_model["rows"]}
    return [_row_level(r, tops[r["id"]], signals) for r in result["rows"]]


def group_row(group: str, rows: Sequence[FleetRow], maturity_model: MaturityModel | None,
              by_scope: Mapping[str, JsonDict], counts_only: bool = True) -> GroupRow:
    """One group's roll-up row."""
    scope, settings = settings_for(group, by_scope)
    org_data = settings_signals(by_scope[scope] if scope else None, settings)
    ok = [row for row in rows if row["status"] == STATUS_OK]
    blocks = [row["scans"]["shared_tooling"] for row in ok
              if row["scans"] and isinstance(row["scans"].get("shared_tooling"), dict)]
    ids = distinct_ids(blocks)
    # Repositories whose shared_tooling block is evidence; with none, the counts are
    # unknown (null), never a false 0.
    evidence = sum(1 for block in blocks if block.get("available"))
    shown = settings if settings is None or not counts_only else reduce_settings(settings)
    return {
        "fleet_group_version": FLEET_GROUP_VERSION,
        "group": group,
        "repos": len(rows),
        "scanned": len(ok),
        "failed": len(rows) - len(ok),
        "shared_tooling_repos": evidence,
        "distinct_ci_sources": len(ids["ci_sources"]) if evidence else None,
        "distinct_marketplaces": len(ids["marketplaces"]) if evidence else None,
        "agent_building": group_counts(rows),
        "settings_scope": scope,
        "settings": shown,
        "levels_version": maturity_model["version"] if maturity_model else None,
        "levels": _row_levels(maturity_model, group, org_data, rows) if maturity_model else [],
    }


def roll_up(rows: Iterable[FleetRow], maturity_model: MaturityModel | None, by_scope: Mapping[str, JsonDict],
            counts_only: bool = True) -> list[GroupRow]:
    """One row per group present in ``rows``, sorted by group."""
    groups: dict[str, list[FleetRow]] = {}
    for row in rows:
        groups.setdefault(row["group"], []).append(row)
    return [group_row(name, sorted(members, key=lambda r: r["repo"]), maturity_model, by_scope, counts_only)
            for name, members in sorted(groups.items())]


def write_groups(out_dir: Path, rows: Iterable[GroupRow]) -> Path:
    """Write ``<out_dir>/groups.jsonl`` through a temp file and ``os.replace``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / GROUPS_FILE
    text = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{GROUPS_FILE}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target
