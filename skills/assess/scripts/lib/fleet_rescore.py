"""Re-score a stored run under the current maturity model, and the Recount (issue #771).

A history entry kept under an earlier maturity-model version holds cells evaluated under
that version's rules (``fleet-data.json``) and the scans behind them (``repos.jsonl``).
``rescore`` rebuilds the entry's ``FleetData`` in memory from those scans under this run's
model, so the achievements replay (``lib/fleet_achievements.py``) compares like with like
across a version bump instead of starting the history again. History files are never
rewritten.

**Re-score.** Each repository's cells come from ``fleet_data.repo_cells`` over its stored
scans (values written as counts-only output writes them, ``Privacy().value``), and every
unit's row roll-ups are recomputed over its members, and so are the Step fields of each
repository and unit (#944, ``fleet_data.step_fields``, with counts-only privacy) (a version 1 entry's ``mean_share`` and
``overall``, which #772 removed, are dropped), using the parents and placements the entry already stores. ``model`` and
``run.levels_version`` become this run's. ``rescore`` returns None when a stored repository
key does not join to a scanned row under this run's key function (another salt), or a
stored row holds no scans while ok: the caller then passes the entry over. A hashed run's
``repos.jsonl`` holds ids hashed at the output boundary (#909), which the key keeps as they
are, so they always join their own entry's keys; ``fleet_achievements_runs`` passes over
such an entry when none of its ids is one of this run's (another salt).

**Signal aliases.** ``SIGNAL_ALIASES`` maps an old signal path to its new name for pure
renames. #783 renamed two signals that read configuration text but were named as if
something ran: ``ci_checks.test_or_lint_runs`` is ``ci_checks.test_or_lint_configured``
and ``coverage_gate.enforced`` is ``coverage_gate.configured``. Before evaluation a stored
scan that holds the old path and not the new one is read under the new name too, so a
rename causes no Recount line. A change of meaning is never aliased (#774).

**Recount.** Shown on the first run under a new maturity-model version: the newest earlier
entry's stored cells (its scans under its own rules) against the same entry re-scored (the
same scans under this run's rules). Every difference is caused by the rules, so every line
is attributed to the rules (``attributed_to`` ``rules``) and says the repository did not
change; nothing in it is earned or lost. One line per (repository, team row) whose
``(status, level)`` differs, with the move's ``direction``:

==================  =================================  ==============================
direction           cause                              signal shown
==================  =================================  ==============================
``down``            Now checked                        the re-scored cell's
``up``              No longer checked                  the stored cell's
``now_measured``    Now measured                       the stored cell's
``not_measured``    Not measured under the new rules   the re-scored cell's
==================  =================================  ==============================

Recount lines are display-only: they never feed the replay, so they never move a streak,
a level reached or a best share. On the next run the newest earlier entry is at the current
version, so no Recount appears; no state is stored.

Deterministic: lines sorted by repository key then maturity-model row order.
"""
from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypedDict, cast

from lib.fleet import STATUS_OK, FleetRow
from lib.fleet_data import (
    Cell,
    DataRepo,
    DataUnit,
    FleetData,
    Privacy,
    View,
    model_rows,
    public_steps,
    repo_cells,
    row_roll_up,
    step_fields,
)
from lib.fleet_data_steps import RepoSteps, step_roll_up
from lib.fleet_maturity import SCOPE
from lib.fleet_next_steps import code, text
from lib.levels import MaturityModel

# Pure renames only: old signal path -> new signal path. #783 renamed the config-only
# signals whose names claimed a run: each reads configuration text at HEAD, meaning unchanged.
SIGNAL_ALIASES: Mapping[str, str] = {
    "ci_checks.test_or_lint_runs": "ci_checks.test_or_lint_configured",
    "coverage_gate.enforced": "coverage_gate.configured",
}

RULES = "rules"
DOWN, UP, NOW_MEASURED, NOT_MEASURED = "down", "up", "now_measured", "not_measured"
DIRECTIONS = (DOWN, UP, NOW_MEASURED, NOT_MEASURED)
CAUSES = {DOWN: "Now checked", UP: "No longer checked", NOW_MEASURED: "Now measured",
          NOT_MEASURED: "Not measured under the new rules"}


class RecountLine(TypedDict):
    repo: str  # repository key, as fleet-data.json writes it
    row: str
    from_status: str
    from_level: int | None
    to_status: str
    to_level: int | None
    direction: str  # one of DIRECTIONS
    attributed_to: str  # always "rules": the repository did not change
    cause: str
    signal: str | None
    text: str  # one Markdown line


class Recount(TypedDict):
    from_version: str  # the earlier entry's maturity-model version
    to_version: str  # this run's
    run: str  # the earlier entry re-scored
    lines: list[RecountLine]


# --- signal aliases ----------------------------------------------------------


def aliased(scans: Mapping[str, Any], aliases: Mapping[str, str] = SIGNAL_ALIASES) -> dict[str, Any]:
    """``scans`` with each old aliased path also readable under its new path (never overwriting)."""
    out: dict[str, Any] = copy.deepcopy(dict(scans))
    for old, new in sorted(aliases.items()):
        value, found = _get(out, old.split("."))
        if found and not _get(out, new.split("."))[1]:
            _put(out, new.split("."), copy.deepcopy(value))
    return out


def _get(data: Mapping[str, Any], path: Sequence[str]) -> tuple[Any, bool]:
    node: Any = data
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return None, False
        node = node[part]
    return node, True


def _put(data: dict[str, Any], path: Sequence[str], value: Any) -> None:
    node = data
    for part in path[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = node[part] = {}
        node = child
    node[path[-1]] = value


# --- re-score ----------------------------------------------------------------


def _members(view: View, repos: Sequence[DataRepo]) -> dict[str, list[str]]:
    """Each unit's repository keys at any depth, from the stored placements and parents."""
    parent = {u["key"]: u["parent"] for u in view["units"]}
    members: dict[str, list[str]] = {u["key"]: [] for u in view["units"]}
    for repo in repos:
        for placed in repo["units"]:
            current: str | None = placed["unit"] if placed["view"] == view["view"] else None
            while current is not None and current in members:
                members[current].append(repo["key"])
                current = parent[current]
    return {unit: sorted(keys) for unit, keys in members.items()}


def rescore(data: FleetData, rows: Sequence[FleetRow], model: MaturityModel,
            key: Callable[[str], str], aliases: Mapping[str, str] = SIGNAL_ALIASES) -> FleetData | None:
    """``data`` rebuilt from ``rows`` (its ``repos.jsonl``) under ``model``; None when they do not join."""
    by_key = {key(row["repo"]): row for row in rows}
    team_rows = [row for row in model["rows"] if row["scope"] == SCOPE]
    cells: dict[str, list[Cell]] = {}
    steps: dict[str, RepoSteps] = {}
    for repo in data["repos"]:
        row = by_key.get(repo["key"])
        if row is None or (row["status"] == STATUS_OK and not isinstance(row["scans"], dict)):
            return None
        scanned = cast(FleetRow, {**row, "scans": aliased(row["scans"], aliases)}) if row["scans"] else row
        cells[repo["key"]] = [{**cell, "value": Privacy().value(cell["value"])}
                              for cell in repo_cells(team_rows, scanned)]
        steps[repo["key"]] = public_steps(step_fields(model, [scanned])[scanned["repo"]], Privacy())
    repos: list[DataRepo] = [cast(DataRepo, {**{k: v for k, v in repo.items() if k != "mean_share"},
                                             "rows": cells[repo["key"]], **steps[repo["key"]]})
                             for repo in data["repos"]]
    views: list[View] = []
    for view in data["views"]:
        members = _members(view, repos)
        units: list[DataUnit] = []
        for unit in view["units"]:
            mine = members[unit["key"]]
            levels = [row_roll_up(row["id"], [(cells[k][i]["level"], cells[k][i]["top_level"]) for k in mine])
                      for i, row in enumerate(team_rows)]
            step = step_roll_up(model.get("steps", []), [steps[k] for k in mine])
            units.append(cast(DataUnit, {**{k: v for k, v in unit.items() if k != "overall"}, "levels": levels,
                                         "step": step}))
        views.append({"view": view["view"], "units": units})
    return {**data, "run": {**data["run"], "levels_version": model["version"]}, "model": model_rows(model),
            "views": views, "repos": repos}


# --- the Recount ---------------------------------------------------------------


def _level(cell: Cell) -> int | None:
    return cell["level"] if cell["status"] == "level" else None


def _direction(before: Cell, after: Cell) -> str | None:
    then, now = _level(before), _level(after)
    if then == now:
        return None
    if then is None:
        return NOW_MEASURED
    if now is None:
        return NOT_MEASURED
    return DOWN if now < then else UP


def _shown(level: int | None) -> str:
    return "unknown" if level is None else str(level)


def recount(stored: FleetData, rescored: FleetData, model: MaturityModel, run: str, from_version: str) -> Recount:
    """Every (repository, team row) whose cell the rule change alone moved, as display-only lines."""
    titles = {row["id"]: row["title"] for row in model["rows"]}
    order = {row["id"]: i for i, row in enumerate(model["rows"])}
    before = {repo["key"]: {c["row"]: c for c in repo["rows"]} for repo in stored["repos"]}
    lines: list[RecountLine] = []
    for repo in sorted(rescored["repos"], key=lambda r: r["key"]):
        for after in sorted(repo["rows"], key=lambda c: order.get(c["row"], len(order))):
            old = before.get(repo["key"], {}).get(after["row"])
            direction = _direction(old, after) if old is not None else None
            if old is None or direction is None:
                continue
            signal = after["signal"] if direction in (DOWN, NOT_MEASURED) else old["signal"]
            cause = CAUSES[direction]
            said = f"{cause}: {code(signal)}." if signal else f"{cause}."
            line = (f"{code(repo['key'])} {text(titles.get(after['row'], after['row']))}: level "
                    f"{_shown(_level(old))} -> {_shown(_level(after))} under maturity model {model['version']} "
                    f"(was {from_version}). {said} The repository did not change.")
            lines.append({"repo": repo["key"], "row": after["row"], "from_status": old["status"],
                          "from_level": old["level"], "to_status": after["status"], "to_level": after["level"],
                          "direction": direction, "attributed_to": RULES, "cause": cause, "signal": signal,
                          "text": line})
    return {"from_version": from_version, "to_version": model["version"], "run": run, "lines": lines}


def rule_unknowns(stored: FleetData, rescored: FleetData) -> frozenset[tuple[str, str]]:
    """``(repository key, row)`` cells the re-score made unknown, so the rules caused it: the stored
    cell was known, the row is new to the model (no stored cell), or the stored cell was unknown
    on another signal (the new rules read a signal the stored scans may not hold)."""
    before = {(repo["key"], c["row"]): c for repo in stored["repos"] for c in repo["rows"]}
    return frozenset((repo["key"], c["row"]) for repo in rescored["repos"] for c in repo["rows"]
                     if _level(c) is None and ((old := before.get((repo["key"], c["row"]))) is None
                                               or _level(old) is not None or old["signal"] != c["signal"]))
