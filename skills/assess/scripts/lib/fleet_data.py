"""``fleet-data.json``: the versioned bundle the fleet artifact reads (issue #703, parent #692).

One JSON document per run, beside ``groups.jsonl``, holding:

- ``run``: the run stamp (``run.json``, ``lib/fleet_stamp.py``);
- ``model``: each maturity model row's id, title, scope and top level;
- ``views``: the ownership and system unit trees (``lib/fleet_units.py``), each
  unit with its kind, parent, owners, source and rolled-up levels;
- ``repos``: per repository its status, web URL, owners, its unit in each view
  and, per ``team`` row, its level with the value and reason behind it.

**Roll-up.** For each unit and each ``team`` row, over every repository below
the unit: the mean share of top level (``lib.fleet_maturity.mean_share``) over
the measured repositories, how many were measured of how many (``measured``,
``repos``: "8 of 12 measured"), and the weakest measured repository (lowest
share, then key) with its level. An unknown or failed repository counts in
``repos`` only, never as 0. ``overall`` does the same over each repository's
own mean share. ``org`` rows read a group's ghreport settings, not a
repository, so they have no per-repository cell or unit roll-up; their
levels stay in ``groups.jsonl``.

**Per-repository cell** (one per ``team`` row): ``status`` and ``level`` as
``lib.levels.evaluate_row`` gives them, plus the criterion that explains it:

- below the top level: the first unmet criterion of the next level (``signal``,
  its measured ``value``), the thing standing between the repository and a
  level-up;
- at the top level: ``signal`` and ``value`` null;
- unknown: the first criterion that could not be read (``signal``) and why
  (``reason``: ``unavailable``, ``type_mismatch``), or ``not_scanned`` for a
  failed repository.

**Privacy** (``counts_only_rules.FLEET_DATA_RULES``): ``--full`` keeps
everything. Counts-only keeps keys and tokens, writes a non-token string value
as ``set``, drops unit names and owners (``owner_count`` stays). With path
hashing every unit and repository key (and every reference to one) is hashed
under the run's salt (``lib.counts_only.hash_value``), and no name, owner or
web URL is written, so the file holds no repository, group or host name.

Deterministic: every list sorted, keys sorted, shares rounded to 4 places, no
timing. The same inputs (and, with hashing, the same ``--hash-salt``) give the
same bytes.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple, TypedDict

from lib.counts_only import hash_value
from lib.counts_only_rules import DROP, ID, PATH, TOKEN, TOKEN_RE, classify_fleet_data
from lib.fleet import STATUS_OK, FleetRow
from lib.fleet_groups import repo_signals
from lib.fleet_maturity import SCOPE, mean_share
from lib.fleet_stamp import FleetStamp, RepositoryCounts
from lib.fleet_units import Tree
from lib.levels import Evaluation, MaturityModel, Row, evaluate_row

# 1 (#703): first version. Bump on any change to the shapes below; #704 and #705 read it.
FLEET_DATA_VERSION = 1
FLEET_DATA_FILE = "fleet-data.json"
NOT_SCANNED = "not_scanned"
SET = "set"
PLACES = 4

Value = int | float | bool | str | None


class ModelRow(TypedDict):
    id: str
    title: str
    scope: str  # "team" | "org"
    top_level: int


class Weakest(TypedDict):
    repo: str  # repository key
    share: float  # its share of top level (overall: its mean share)
    level: int | None  # its level on the row; None in ``overall``


class RollUp(TypedDict):
    mean_share: float | None  # None when no repository below was measured
    measured: int
    repos: int
    weakest: Weakest | None


class RowRollUp(RollUp):
    row: str


class DataUnit(TypedDict):
    key: str  # "<view>/<unit id>", or its hash
    view: str
    parent: str | None  # the parent unit's key
    kind: str
    source: str
    name: str | None  # None in counts-only output
    owners: list[str] | None  # None in counts-only output
    owner_count: int
    repos: int  # repositories below the unit, at any depth
    levels: list[RowRollUp]
    overall: RollUp


class View(TypedDict):
    view: str
    units: list[DataUnit]


class RepoUnit(TypedDict):
    view: str
    unit: str  # the unit's key


class Cell(TypedDict):
    row: str
    status: str  # "level" | "unknown"
    level: int | None
    top_level: int
    signal: str | None  # the criterion behind the level; None at the top level or not scanned
    value: Value
    reason: str | None  # unknown cells only


class DataRepo(TypedDict):
    key: str  # the repository id, or its hash
    status: str
    reason: str | None
    active: bool | None
    web_url: str | None
    owners: list[str] | None
    owner_count: int
    units: list[RepoUnit]
    mean_share: float | None
    rows: list[Cell]


class RunDetails(TypedDict):
    """The run stamp's fields, copied one by one so a new ``run.json`` field never
    changes this document without a ``FLEET_DATA_VERSION`` bump."""

    toolkit_commit: str | None
    plugin_version: str
    scan_date: str
    window: str
    levels_version: str | None
    counts_only: bool
    paths_hashed: bool
    scans: list[str] | None
    fleet_config_sha256: str | None  # the --fleet-config comparability key (#706)
    repositories: RepositoryCounts


class FleetData(TypedDict):
    fleet_data_version: int
    run: RunDetails
    model: list[ModelRow]
    views: list[View]
    repos: list[DataRepo]


class Privacy(NamedTuple):
    """How strings are written: ``full`` keeps all; else counts-only, hashed when ``salt`` is set."""

    full: bool = False
    salt: str | None = None

    def key(self, value: str) -> str:
        return value if self.salt is None else hash_value(value, self.salt)

    def text(self, pattern: str, value: str | None) -> str | None:
        if value is None or self.full:
            return value
        cls = classify_fleet_data(pattern)
        if cls == PATH:
            return self.key(value)
        if cls == ID:
            return None if self.salt is not None else value
        if cls == TOKEN:
            return value if TOKEN_RE.match(value) else None
        assert cls == DROP
        return None

    def names(self, pattern: str, values: Sequence[str]) -> list[str] | None:
        return list(values) if self.full else None

    def value(self, value: Value) -> Value:
        if self.full or not isinstance(value, str):
            return value
        return value if TOKEN_RE.match(value) else SET


# --- per-repository cells ----------------------------------------------------


def _scalar(value: object) -> Value:
    return value if value is None or isinstance(value, (bool, int, float, str)) else None


def _explain(row: Row, ev: Evaluation) -> tuple[str | None, Value, str | None]:
    """``(signal, value, reason)`` behind an evaluation (see the module docstring)."""
    if ev["level"] is not None:
        nxt = next((lv for lv in ev["levels"] if lv["level"] == ev["level"] + 1), None)
        if nxt is None or not nxt["unmet"]:
            return None, None, None
        outcome = next((o for o in nxt["unmet"] if o["status"] == "unmet"), nxt["unmet"][0])
        return outcome["signal"], _scalar(outcome["actual"]), None
    for level in ev["levels"]:
        for outcome in level["unmet"]:
            if outcome["status"] == "unknown":
                return outcome["signal"], None, outcome["reason"] or "unavailable"
    return None, None, "unavailable"  # pragma: no cover - unknown always has an unknown criterion


def repo_cells(rows: Sequence[Row], repo: FleetRow) -> list[Cell]:
    """One cell per team row for one repository."""
    cells: list[Cell] = []
    for row in rows:
        top = len(row["levels"])
        if repo["status"] != STATUS_OK:
            cells.append({"row": row["id"], "status": "unknown", "level": None, "top_level": top,
                          "signal": None, "value": None, "reason": NOT_SCANNED})
            continue
        ev = evaluate_row(row, repo_signals(repo))
        signal, value, reason = _explain(row, ev)
        cells.append({"row": row["id"], "status": ev["status"], "level": ev["level"], "top_level": top,
                      "signal": signal, "value": value, "reason": reason})
    return cells


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, PLACES)


# --- roll-up -----------------------------------------------------------------


Member = tuple[str, int | None, int]  # (repository key, level or None, top level)


def row_roll_up(row_id: str, members: Sequence[Member]) -> RowRollUp:
    """One team row over the repositories below a unit: ``mean_share`` of their levels."""
    known = [(key, level, top) for key, level, top in members if level is not None and top > 0]
    weakest = min(known, key=lambda m: (m[1] / m[2], m[0])) if known else None
    return {
        "row": row_id,
        "mean_share": _round(mean_share((level, top) for _, level, top in members)),
        "measured": len(known),
        "repos": len(members),
        "weakest": ({"repo": weakest[0], "share": round(weakest[1] / weakest[2], PLACES), "level": weakest[1]}
                    if weakest is not None else None),
    }


def overall_roll_up(members: Sequence[tuple[str, float | None]]) -> RollUp:
    """Each repository's own mean share, averaged over those with one."""
    known = [(key, share) for key, share in members if share is not None]
    weakest = min(known, key=lambda m: (m[1], m[0])) if known else None
    return {
        "mean_share": _round(sum(s for _, s in known) / len(known)) if known else None,
        "measured": len(known),
        "repos": len(members),
        "weakest": ({"repo": weakest[0], "share": round(weakest[1], PLACES), "level": None}
                    if weakest is not None else None),
    }


def _below(tree: Tree) -> dict[str, list[str]]:
    """Each unit's repositories at any depth, by unit id."""
    parents = {unit["id"]: unit["parent"] for unit in tree["units"]}
    below: dict[str, list[str]] = {uid: [] for uid in parents}
    for repo, uid in tree["placement"].items():
        current: str | None = uid
        while current is not None:
            below[current].append(repo)
            current = parents[current]
    return below


def unit_key(view: str, uid: str) -> str:
    return f"{view}/{uid}"


def build_view(tree: Tree, team_rows: Sequence[Row], cells: Mapping[str, list[Cell]],
               shares: Mapping[str, float | None], privacy: Privacy) -> View:
    below = _below(tree)
    view = tree["view"]
    units: list[DataUnit] = []
    for unit in tree["units"]:
        repos = sorted(r for r in below[unit["id"]] if r in cells)  # placed but not a row: not counted
        keys = {repo: privacy.key(repo) for repo in repos}
        levels = [row_roll_up(row["id"], [(keys[r], cells[r][i]["level"], cells[r][i]["top_level"])
                                          for r in repos])
                  for i, row in enumerate(team_rows)]
        parent = unit["parent"]
        units.append({
            "key": privacy.key(unit_key(view, unit["id"])),
            "view": view,
            "parent": privacy.key(unit_key(view, parent)) if parent is not None else None,
            "kind": unit["kind"],
            "source": unit["source"],
            "name": privacy.text("views.units.name", unit["name"]),
            "owners": privacy.names("views.units.owners", unit["owners"]),
            "owner_count": len(unit["owners"]),
            "repos": len(repos),
            "levels": levels,
            "overall": overall_roll_up([(keys[r], shares[r]) for r in repos]),
        })
    return {"view": view, "units": sorted(units, key=lambda u: u["key"])}


def run_details(stamp: FleetStamp) -> RunDetails:
    counts = stamp["repositories"]
    return {
        "toolkit_commit": stamp["toolkit_commit"], "plugin_version": stamp["plugin_version"],
        "scan_date": stamp["scan_date"], "window": stamp["window"], "levels_version": stamp["levels_version"],
        "counts_only": stamp["counts_only"], "paths_hashed": stamp["paths_hashed"],
        "scans": list(stamp["scans"]) if stamp["scans"] is not None else None,
        "fleet_config_sha256": stamp.get("fleet_config_sha256"),
        "repositories": {"selected": counts["selected"], "scanned_ok": counts["scanned_ok"],
                         "failed": counts["failed"], "qualifying": counts["qualifying"]},
    }


def model_rows(maturity_model: MaturityModel | None) -> list[ModelRow]:
    if maturity_model is None:
        return []
    return [{"id": row["id"], "title": row["title"], "scope": row["scope"], "top_level": len(row["levels"])}
            for row in maturity_model["rows"]]


def build_fleet_data(stamp: FleetStamp, rows: Sequence[FleetRow], maturity_model: MaturityModel | None,
                     trees: Sequence[Tree], *, owners: Mapping[str, list[str]] | None = None,
                     web_urls: Mapping[str, str | None] | None = None,
                     privacy: Privacy = Privacy()) -> FleetData:
    """The whole document for one run."""
    team_rows = [row for row in (maturity_model["rows"] if maturity_model else []) if row["scope"] == SCOPE]
    cells = {repo["repo"]: repo_cells(team_rows, repo) for repo in rows}
    shares = {name: _round(mean_share((c["level"], c["top_level"]) for c in repo_cells_))
              for name, repo_cells_ in cells.items()}
    owners = owners or {}
    web_urls = web_urls or {}
    repos: list[DataRepo] = []
    for repo in rows:
        name = repo["repo"]
        repo_owners = owners.get(name, [])
        repos.append({
            "key": privacy.key(name),
            "status": repo["status"],
            "reason": privacy.text("repos.reason", repo["reason"]),
            "active": repo["active"],
            "web_url": privacy.text("repos.web_url", web_urls.get(name)),
            "owners": privacy.names("repos.owners", repo_owners),
            "owner_count": len(repo_owners),
            "units": [{"view": tree["view"], "unit": privacy.key(unit_key(tree["view"], tree["placement"][name]))}
                      for tree in trees if name in tree["placement"]],
            "mean_share": shares[name],
            "rows": [{**cell, "value": privacy.value(cell["value"])} for cell in cells[name]],
        })
    return {
        "fleet_data_version": FLEET_DATA_VERSION,
        "run": run_details(stamp),
        "model": model_rows(maturity_model),
        "views": [build_view(tree, team_rows, cells, shares, privacy) for tree in trees],
        "repos": sorted(repos, key=lambda r: r["key"]),
    }


def dumps_fleet_data(data: FleetData) -> str:
    """Canonical bytes: sorted keys, compact, no NaN, trailing newline."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"


def write_fleet_data(out_dir: Path, data: FleetData) -> Path:
    """Write ``<out_dir>/fleet-data.json`` through a temp file and ``os.replace``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / FLEET_DATA_FILE
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{FLEET_DATA_FILE}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(dumps_fleet_data(data))
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def privacy_for(full: bool, hash_salt: str | None) -> Privacy:
    return Privacy(full=full, salt=None if full else hash_salt)
