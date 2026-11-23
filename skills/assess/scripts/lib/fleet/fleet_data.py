"""``fleet-data.json``: the versioned bundle the fleet artifact reads (issue #703, parent #692).

One JSON document per run, beside ``groups.jsonl``, holding:

- ``run``: the run stamp (``run.json``, ``lib/fleet/fleet_stamp.py``);
- ``model``: each maturity model row's id, title, scope and top level;
- ``views``: the ownership and system unit trees (``lib/fleet/fleet_units.py``), each
  unit with its kind, parent, owners, source and rolled-up levels;
- ``repos``: per repository its status, web URL, owners, its unit in each view
  and, per ``team`` row, its level with the value and reason behind it;
- ``opportunities``: what is in place, the biggest lift and the shared-template
  leverage (``lib/fleet/fleet_opportunities.py``, #746); null without a maturity model.

**Roll-up** (#772, option A: no average and no repository singled out). For each
unit and each ``team`` row, over every repository below the unit: how many were
measured of how many (``measured``, ``repos``: "8 of 12 measured") and the lowest
level a measured repository holds on the row (``lowest_level``; None when none was
measured). An unknown or failed repository counts in ``repos`` only,
never as 0. ``org`` rows read a group's ghreport settings, not a repository, so
they have no per-repository cell or unit roll-up; their levels stay in
``groups.jsonl``.

**Steps** (#944, ``lib/fleet/fleet_data_steps.py``): each repository carries ``step``,
``next_step`` and ``tally`` exactly as ``level.json`` gives them (``lib.fleet.fleet_level.repo_level``)
the owner's ``exclusions`` and ``gate_blockers``, every Step gate that does not hold (#945,
which a Step target above the next Step reads); each unit carries a ``step`` roll-up: the lowest Step over
its measured repositories, "N of M measured", and for the next Step how many repositories
each gate holds back (a Step reads its gates only, #1083).

**Tags** (#1073, ``lib/fleet/fleet_data_tags.py``): each repository carries ``tags``, the
headline's per-tag strip (#844) over the same level result, so it equals the ``tags`` of the
repository's own headline; null when not measured. Each unit carries a ``tags`` roll-up:
"N of M measured" and each tag's counts summed over its measured repositories. Tag names
come from the fixed truth-tag list and the rest are counts. The document carries
``tag_profile`` (#940, ``lib/fleet/fleet_tag_profile.py``): per truth tag the median and the
25th and 75th percentiles of the measured repositories' share met, read from those same
per-repository tallies, with the repositories measured; null stats when none is measured.

**Per-repository cell** (one per ``team`` row): ``status`` and ``level`` as
``lib.levels.evaluate_row`` gives them, plus the criterion that explains it:

- below the top level: the first unmet criterion of the next level (``signal``,
  its measured ``value``), the thing standing between the repository and a
  level-up;
- at the top level: ``signal`` and ``value`` null;
- unknown: the first criterion that could not be read (``signal``) and why
  (``reason``: ``unavailable``, ``type_mismatch``), or ``not_scanned`` for a
  failed repository.

Beside ``signal`` the cell carries ``why`` (#1128): the check manifest's why for that
criterion at its level (``lib.level_tally.criterion_why``, the text the level result's
conditions carry), so a fleet view explains a step without reading the checks TOML. Null
when there is no ``signal`` or the manifest has no entry for it (a caller's own model),
never invented. It is the toolkit's own text, never a repository's, so counts-only keeps
it (``FLEET_DATA_KEPT``).

**Unit rows** (#866, ``lib/fleet/fleet_data_units.py``): a repository scanned with
``--monorepo-units`` carries ``unit_rows``, one per monorepo unit: its kind (``skill``, ``agents``,
``commands`` or ``package``, #1098), its Step, the Step above, the
first gate of that Step that does not hold, and its per-tag tally, so the page can expand
the repository into its units. The unit's folder is written in ``--full`` only, or as its
salted hash under ``--hash-paths``; null in plain counts-only. ``unit_rows`` is null for a
repository not scanned by unit.

**Privacy** (``counts_only_rules.FLEET_DATA_RULES``): ``--full`` keeps
everything. Counts-only keeps keys and tokens, writes a non-token string value
as ``set``, drops unit names and owners (``owner_count`` stays). With path
hashing every unit and repository key (and every reference to one) is hashed
under the run's salt (``lib.counts_only.content_free.hash_value``), and no name, owner or
web URL is written, so the file holds no repository, group or host name.

Deterministic: every list sorted, keys sorted, no timing. The same inputs (and,
with hashing, the same ``--hash-salt``) give the same bytes.

`fleet-data.json`, the versioned bundle the fleet artifact reads (#703, `FLEET_DATA_VERSION`;
#704 and #705 build on it). `build_fleet_data` joins the run stamp, the maturity model rows,
the unit trees and the repository rows. `repo_cells` evaluates each `team` row per
repository (`levels.evaluate_row`) with the criterion behind the level (`signal`, `value`,
or `reason` when unknown; `not_scanned` for a failed row) and its manifest `why` (#1128). `row_roll_up` gives each unit,
per team row, `measured` of `repos` and `lowest_level`, the lowest known level below it. Version 2 (#772) holds no average and names no
repository: no `mean_share`, `weakest` or `overall`. `org` rows stay in `groups.jsonl`.
`Privacy` applies `counts_only_rules.FLEET_DATA_RULES`: full keeps all; counts-only drops
names and owners and writes a non-token value as `set`; with a salt every unit and
repository key is `counts_only.hash_value`d and no name, owner or web URL is written;
`Privacy.key` keeps an id that is already hashed (`counts_only.is_hashed`), so the re-score
joins a stored `repos.jsonl` row hashed at the output boundary (#909).
`write_fleet_data` writes sorted, compact JSON through a temp file and `os.replace`.
Version 3 (#746) adds `opportunities` (`fleet_opportunities.build_opportunities`, null
without a model). `step_fields` gives each repository its Step fields (`fleet_data_steps`)
from `fleet_level.repo_level`; `public_steps` writes the exclusions' strings under `Privacy`
(the owner's reason only in `--full`). Additive and unreleased under the run 4 version freeze (#944).
`level_results` evaluates each scanned repository once (`fleet_level.repo_level`), for both
`step_fields` and the per-tag tally (`fleet_data_tags.repo_tags`, unit `tag_roll_up`, #1073).
`unit_rows` (#866, `fleet_data_units`): each repository scanned with `--monorepo-units` lists its
monorepo units, each with its Step, the Step above, the first gate blocking it and its
per-tag tally; `public_unit_rows` writes the unit's folder only in `--full` or hashed
(`hashed_path`: null in plain counts-only), the rest as tokens. Null when not run by unit.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple, NotRequired, TypedDict

from lib.counts_only.content_free import hash_value, is_hashed
from lib.counts_only.counts_only_rules import DROP, HASHED_PATH, ID, PATH, TOKEN, TOKEN_RE, classify_fleet_data
from lib.fleet.runner import STATUS_OK, FleetRow
from lib.fleet.fleet_data_steps import (
    NO_STEPS,
    GateBlocker,
    RepoExclusion,
    RepoSteps,
    StepRollUp,
    repo_steps,
    step_roll_up,
)
from lib.fleet.fleet_data_tags import TagRollUp, repo_tags, tag_roll_up
from lib.fleet.fleet_data_units import UnitRow, unit_rows
from lib.fleet.fleet_tag_profile import FleetTagRow, tag_profile
from lib.fleet.fleet_groups import repo_signals
from lib.fleet.fleet_level import repo_level
from lib.level_tally import criterion_why
from lib.fleet.fleet_maturity import SCOPE
from lib.fleet.fleet_opportunities import Opportunities, build_opportunities
from lib.fleet.fleet_stamp import FleetStamp, RepositoryCounts
from lib.fleet.fleet_units import Tree
from lib.level_result_types import LevelResult, NextStep, Tally, TagCell
from lib.levels import Evaluation, MaturityModel, Row, StepDef, evaluate_row

# 1 (#703): first version. 2 (#772): the roll-up drops ``mean_share``, ``weakest`` and
# ``overall`` for ``lowest_level``; repositories drop ``mean_share``. Cells are unchanged.
# 3 (#746): ``opportunities``. Nothing else changed, so readers of 2 read 3.
# 4 (3.0.0 release): two changes since 3.
# - repositories gain ``step``, ``next_step`` and ``tally`` (as ``level.json`` gives them,
#   owner exclusions applied) and ``exclusions``; units gain a ``step`` roll-up (#944).
#   Additive: no existing key moves, so readers of 3 read the new version.
# - repositories gain ``gate_blockers`` (#945): every Step gate that does not hold, as the level
#   result's blocker for it, so a Step target above the next Step reads its missing gates.
#   Additive.
# Bump on any change to the shapes below; #704 and #705 read it.
FLEET_DATA_VERSION = 4
FLEET_DATA_FILE = "fleet-data.json"
NOT_SCANNED = "not_scanned"
SET = "set"
PLACES = 4  # share rounding for readers of the cells (the achievements engine)

Value = int | float | bool | str | None


class ModelRow(TypedDict):
    id: str
    title: str
    scope: str  # "team" | "org"
    top_level: int


class RowRollUp(TypedDict):
    row: str
    measured: int  # repositories below with a known level on the row
    repos: int  # every repository below, measured or not
    lowest_level: int | None  # the lowest known level; None when none was measured


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
    step: StepRollUp  # #944
    tags: TagRollUp  # #1073: the per-tag tally over the repositories below


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
    why: str | None  # the check manifest's why for ``signal`` at its level (#1128); None when none


class DataRepo(TypedDict):
    key: str  # the repository id, or its hash
    status: str
    reason: str | None
    active: bool | None
    web_url: str | None
    owners: list[str] | None
    owner_count: int
    units: list[RepoUnit]
    rows: list[Cell]
    step: int | None  # #944: as level.json; None when not measured or the model has no Steps
    next_step: NextStep | None
    tally: Tally | None
    exclusions: list[RepoExclusion]  # the owner's, listed and never counted
    gate_blockers: list[GateBlocker]  # #945: every Step gate that does not hold, as tokens
    tags: list[TagCell] | None  # #1073: the headline's per-tag strip; None when not measured
    unit_rows: list[UnitRow] | None  # #866: one row per monorepo unit; None when not run by unit


# The stamp fields RunDetails leaves out: the stamp's own envelope version, the
# --fleet-config section counts (the sha256 is the comparability key) and the --discovered
# coverage counts (#745; run.json and report.md carry them). A test fails when FleetStamp
# gains a field that is neither copied below nor listed here.
RUN_EXCLUDED = frozenset({"fleet_stamp_version", "fleet_config_entries", "coverage"})


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
    platform: bool  # --platform ran the platform pass (#714); False for a stamp without it
    repositories: RepositoryCounts


class FleetData(TypedDict):
    fleet_data_version: int
    run: RunDetails
    model: list[ModelRow]
    views: list[View]
    repos: list[DataRepo]
    opportunities: Opportunities | None
    # #940: the fleet's per-tag roll-up (lib/fleet/fleet_tag_profile.py); None without a model.
    # NotRequired: a stored entry written before it reads as not measured, never as zeros.
    tag_profile: NotRequired[list[FleetTagRow] | None]


class Privacy(NamedTuple):
    """How strings are written: ``full`` keeps all; else counts-only, hashed when ``salt`` is set."""

    full: bool = False
    salt: str | None = None

    def key(self, value: str) -> str:
        """The repository or unit key; an id the outputs already hashed (a stored
        ``repos.jsonl`` row the re-score joins, #909) is kept as it is."""
        return value if self.salt is None or is_hashed(value) else hash_value(value, self.salt)

    def text(self, pattern: str, value: str | None) -> str | None:
        if value is None or self.full:
            return value
        cls = classify_fleet_data(pattern)
        if cls == PATH:
            return self.key(value)
        if cls == HASHED_PATH:
            return None if self.salt is None else self.key(value)
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


def _explain(row: Row, ev: Evaluation) -> tuple[str | None, Value, str | None, str | None]:
    """``(signal, value, reason, why)`` behind an evaluation (see the module docstring)."""
    if ev["level"] is not None:
        nxt = next((lv for lv in ev["levels"] if lv["level"] == ev["level"] + 1), None)
        if nxt is None or not nxt["unmet"]:
            return None, None, None, None
        outcome = next((o for o in nxt["unmet"] if o["status"] == "unmet"), nxt["unmet"][0])
        return (outcome["signal"], _scalar(outcome["actual"]), None,
                criterion_why(row["id"], nxt["level"], outcome["signal"]))
    for level in ev["levels"]:
        for outcome in level["unmet"]:
            if outcome["status"] == "unknown":
                return (outcome["signal"], None, outcome["reason"] or "unavailable",
                        criterion_why(row["id"], level["level"], outcome["signal"]))
    return None, None, "unavailable", None  # pragma: no cover - unknown always has an unknown criterion


def repo_cells(rows: Sequence[Row], repo: FleetRow) -> list[Cell]:
    """One cell per team row for one repository."""
    cells: list[Cell] = []
    for row in rows:
        top = len(row["levels"])
        if repo["status"] != STATUS_OK:
            cells.append({"row": row["id"], "status": "unknown", "level": None, "top_level": top,
                          "signal": None, "value": None, "reason": NOT_SCANNED, "why": None})
            continue
        ev = evaluate_row(row, repo_signals(repo))
        signal, value, reason, why = _explain(row, ev)
        cells.append({"row": row["id"], "status": ev["status"], "level": ev["level"], "top_level": top,
                      "signal": signal, "value": value, "reason": reason, "why": why})
    return cells


# --- roll-up -----------------------------------------------------------------


Member = tuple[int | None, int]  # (level or None, top level) of one repository below the unit


def row_roll_up(row_id: str, members: Sequence[Member]) -> RowRollUp:
    """One team row over the repositories below a unit: measured of all, and the lowest level."""
    known = [level for level, top in members if level is not None and top > 0]
    return {"row": row_id, "measured": len(known), "repos": len(members),
            "lowest_level": min(known) if known else None}


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
               privacy: Privacy, steps: Sequence[StepDef] = (),
               repo_step: Mapping[str, RepoSteps] | None = None,
               tags: Mapping[str, list[TagCell] | None] | None = None) -> View:
    below = _below(tree)
    view = tree["view"]
    units: list[DataUnit] = []
    for unit in tree["units"]:
        repos = sorted(r for r in below[unit["id"]] if r in cells)  # placed but not a row: not counted
        levels = [row_roll_up(row["id"], [(cells[r][i]["level"], cells[r][i]["top_level"]) for r in repos])
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
            "step": step_roll_up(steps, [(repo_step or {}).get(r, NO_STEPS) for r in repos]),
            "tags": tag_roll_up([(tags or {}).get(r) for r in repos]),
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
        "platform": stamp.get("platform", False),
        "repositories": {"selected": counts["selected"], "scanned_ok": counts["scanned_ok"],
                         "failed": counts["failed"], "qualifying": counts["qualifying"]},
    }


def model_rows(maturity_model: MaturityModel | None) -> list[ModelRow]:
    if maturity_model is None:
        return []
    return [{"id": row["id"], "title": row["title"], "scope": row["scope"], "top_level": len(row["levels"])}
            for row in maturity_model["rows"]]


def level_results(maturity_model: MaturityModel | None, rows: Sequence[FleetRow]) -> dict[str, LevelResult | None]:
    """Each repository's level result by id; None when failed or without a model."""
    return {row["repo"]: repo_level(maturity_model, row) if maturity_model is not None
            and row["status"] == STATUS_OK else None for row in rows}


def step_fields(maturity_model: MaturityModel | None, rows: Sequence[FleetRow],
                results: Mapping[str, LevelResult | None] | None = None) -> dict[str, RepoSteps]:
    """Each repository's Step fields by id: its level result, or none when failed or without a model."""
    steps = maturity_model.get("steps", []) if maturity_model is not None else []
    results = level_results(maturity_model, rows) if results is None else results
    return {row["repo"]: repo_steps(results[row["repo"]], row["scans"], steps) for row in rows}


def tag_fields(maturity_model: MaturityModel | None,
               results: Mapping[str, LevelResult | None]) -> dict[str, list[TagCell] | None]:
    """Each repository's per-tag tally by id (#1073); None when it was not measured."""
    return {repo: repo_tags(result, maturity_model) for repo, result in results.items()}


def public_steps(fields: RepoSteps, privacy: Privacy) -> RepoSteps:
    """``fields`` with the exclusions' strings written under ``privacy``."""
    exclusions: list[RepoExclusion] = [
        {"id": e["id"], "kind": privacy.text("repos.exclusions.kind", e["kind"]) or "other",
         "source": privacy.text("repos.exclusions.source", e["source"]) or "repo",
         "reason": privacy.text("repos.exclusions.reason", e["reason"])}
        for e in fields["exclusions"]]
    blockers: list[GateBlocker] = [
        {"gate": privacy.text("repos.gate_blockers.gate", b["gate"]) or "",
         "state": privacy.text("repos.gate_blockers.state", b["state"]) or "",
         "disposition": privacy.text("repos.gate_blockers.disposition", b["disposition"])}
        for b in fields["gate_blockers"]]
    return {**fields, "exclusions": exclusions, "gate_blockers": blockers}


def public_tags(cells: list[TagCell] | None, privacy: Privacy) -> list[TagCell] | None:
    """``cells`` with each tag written under ``privacy``: a token from the fixed list (#1073)."""
    if cells is None:
        return None
    return [{**cell, "tag": privacy.text("repos.tags.tag", cell["tag"]) or ""} for cell in cells]


def public_unit_rows(rows: list[UnitRow] | None, privacy: Privacy) -> list[UnitRow] | None:
    """``rows`` with each string written under ``privacy`` (#866): the unit's folder only in
    ``--full`` or hashed, the blocker and tags as tokens."""
    if rows is None:
        return None
    out: list[UnitRow] = []
    for row in rows:
        b = row["next_blocker"]
        blocker: GateBlocker | None = None if b is None else {
            "gate": privacy.text("repos.unit_rows.next_blocker.gate", b["gate"]) or "",
            "state": privacy.text("repos.unit_rows.next_blocker.state", b["state"]) or "",
            "disposition": privacy.text("repos.unit_rows.next_blocker.disposition", b["disposition"])}
        out.append({**row, "key": privacy.text("repos.unit_rows.key", row["key"]),
                    "kind": row["kind"], "next_blocker": blocker,
                    "tags": [{**c, "tag": privacy.text("repos.unit_rows.tags.tag", c["tag"]) or ""}
                             for c in row["tags"]]})
    return out


def build_fleet_data(stamp: FleetStamp, rows: Sequence[FleetRow], maturity_model: MaturityModel | None,
                     trees: Sequence[Tree], *, owners: Mapping[str, list[str]] | None = None,
                     web_urls: Mapping[str, str | None] | None = None,
                     privacy: Privacy = Privacy()) -> FleetData:
    """The whole document for one run."""
    team_rows = [row for row in (maturity_model["rows"] if maturity_model else []) if row["scope"] == SCOPE]
    cells = {repo["repo"]: repo_cells(team_rows, repo) for repo in rows}
    results = level_results(maturity_model, rows)
    repo_step = step_fields(maturity_model, rows, results)
    repo_tag = {repo: public_tags(cells, privacy) for repo, cells in tag_fields(maturity_model, results).items()}
    steps = maturity_model.get("steps", []) if maturity_model else []
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
            "rows": [{**cell, "value": privacy.value(cell["value"])} for cell in cells[name]],
            **public_steps(repo_step[name], privacy),
            "tags": repo_tag[name],
            "unit_rows": public_unit_rows(unit_rows(maturity_model, repo["scans"])
                                          if repo["status"] == STATUS_OK else None, privacy),
        })
    return {
        "fleet_data_version": FLEET_DATA_VERSION,
        "run": run_details(stamp),
        "model": model_rows(maturity_model),
        "views": [build_view(tree, team_rows, cells, privacy, steps, repo_step, repo_tag) for tree in trees],
        "repos": sorted(repos, key=lambda r: r["key"]),
        "opportunities": build_opportunities(maturity_model, rows, privacy.key),
        "tag_profile": tag_profile(maturity_model, repo_tag.values()),
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
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(dumps_fleet_data(data))
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def privacy_for(full: bool, hash_salt: str | None) -> Privacy:
    return Privacy(full=full, salt=None if full else hash_salt)
