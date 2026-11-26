"""Fleet opportunities: the changes that would lift the most repositories (issue #746, parent #740).

Computed from one run's rows and the maturity model, with no model call and no history, and
written as ``opportunities`` in ``fleet-data.json`` (``lib/fleet/fleet_data.py``). ``report.md``
renders it as its "Opportunities" section (``opportunities_section``). Every insight is
phrased as an opportunity: what is already in place first, then the change that unlocks the
next level. No team or repository is ranked, numbered or compared, and no insight names a
person (the inputs are scan blocks and repository ids only).

- ``in_place``: per ``team`` row, how many measured repositories already hold its top level,
  by the level result's rule (#1470): a repository counts only when its own level result
  (``lib.fleet.fleet_level.repo_level``, the badge's source) lists the row in ``in_place`` with
  ``top``, so every condition is counted and met. A declared or config-only condition (an
  instruction file) never makes a row count, here or in the repository's badge.
- ``biggest_lift``: the single next-level criterion whose completion would lift the most
  repositories. A repository counts toward a criterion when its row level is known, it is
  below the top level, and that criterion is the only one unmet at its next level (status
  ``unmet``; an ``unknown`` criterion never counts, so a repository the run could not see
  is never promised a lift). Ties: maturity-model row order, then level, then signal. The
  condition's Step gate (``[[steps]]`` in the model) is named when it is one.
- ``shared_templates``: for each shared CI source (``shared_tooling.ci_source_ids``)
  that at least ``MIN_INCLUDERS`` repositories include, the repositories it would lift if
  it ran tests or a check: those whose only unmet next-level criterion reads a
  ``ci_checks.`` signal, grouped by row and level. Sorted by source, then row order.
- ``step_lifts`` (#941, ``lib/fleet/fleet_step_lifts.py``): the single conditions that would
  take the most repositories to their next Step, from the rows' level results; counts only,
  no repository named. Empty when no ``results`` are passed.

Recent progress (units that reached a new level since the previous run) is display only:
the report section reads it from this run's ``level_up`` achievements
(``lib/fleet/fleet_achievements.py``), which come from the run history. It is never written into
``fleet-data.json``, the achievements engine's own input, and never feeds a level.

Privacy follows ``fleet-data.json``: repository keys are written with the run's
``Privacy.key`` (in clear in counts-only output, hashed under ``--hash-paths``). A shared-source
id is written as the scan wrote it: in clear in counts-only output, already hashed by the
scan under the run's salt with ``--hash-paths`` (so it joins ``repos.jsonl``'s
``ci_source_ids``);
``text`` lines hold counts and maturity-model titles only. Deterministic: lists sorted.

The `opportunities` object of `fleet-data.json` (#746, parent #740), from one run's rows and
the maturity model with no history. `in_place`: per team row, measured repositories at its
top level, read from each repository's level result (`in_place` entries with `top`, #1470),
never recomputed. `_sole_unmet` re-evaluates each row (`levels.evaluate_row`) and keeps a
repository only when its level is known and exactly one criterion, status `unmet`, stands
before its next level (unknown never counts). `biggest_lift` is the (row, level, signal)
holding the most such repositories (ties: row order, level, signal), worded by
`next_step_guidance.guidance`, with its Step gate (`[[steps]]`) when it is one and its
manifest `why` (`fleet_why.criterion_why`, #1128; null when the manifest has none).
`shared_templates`: per `shared_tooling.ci_source_ids` id with at least `MIN_INCLUDERS`
includers, the includers whose sole unmet criterion reads a `ci_checks.` signal, by row and
level. Repository keys go through the run's `Privacy.key`; a source id is kept as the scan
wrote it (clear, or the scan's salted hash, which joins `repos.jsonl`), and its `kind` comes
from `ci_includes` (`FLEET_DATA_RULES` classes both ids `path`); `text` holds counts and
model titles only. No
rank or position field. Tests: `tests/test_fleet_opportunities.py`.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, NotRequired, TypedDict

from lib.fleet.runner import STATUS_OK, FleetRow
from lib.fleet.fleet_groups import repo_signals
from lib.fleet.fleet_level import repo_level
from lib.fleet.fleet_maturity import SCOPE
from lib.fleet.fleet_step_lifts import StepLift, step_lifts
from lib.fleet.fleet_why import criterion_why
from lib.scoring.level_result_types import LevelResult
from lib.scoring.levels import Criterion, MaturityModel, Row, evaluate_row
from lib.scoring.next_step_guidance import guidance

TITLE = "Opportunities"
CI_PREFIX = "ci_checks."
MIN_INCLUDERS = 2  # a CI source fewer repositories include is not shared
PER_LIST = 20  # repository names per report line; the rest are counted


class InPlace(TypedDict):
    row: str
    at_top: int  # measured repositories whose level result holds the row at its top level (#1470)
    measured: int  # repositories with a known level on the row


class Lift(TypedDict):
    row: str
    level: int  # the level the repositories would reach
    signal: str  # the criterion to complete
    repo_count: int
    repos: list[str]  # repository keys, sorted
    gate: str | None  # the Step gate this condition is, when it is one
    step: int | None  # the Step that gate is first needed for
    change: str | None
    done_when: str | None
    why: str | None  # the check manifest's why for the criterion (#1128); None when none
    text: str


class TemplateLift(TypedDict):
    source: str  # the shared_tooling id, e.g. "gitlab_project:grp/ci" (hashed under --hash-paths)
    kind: str  # the id's kind prefix, e.g. "gitlab_project"
    includers: int  # repositories that include the source
    row: str
    level: int
    signal: str
    repo_count: int
    repos: list[str]  # repository keys, sorted
    text: str


class Opportunities(TypedDict):
    measured: int  # repositories scanned ok
    in_place: list[InPlace]
    biggest_lift: Lift | None
    shared_templates: list[TemplateLift]
    # #941: the single conditions that take the most repositories to their next Step
    # (lib/fleet/fleet_step_lifts.py); NotRequired: an entry stored before it has none
    step_lifts: NotRequired[list[StepLift]]


Key = Callable[[str], str]
# (row id, level, signal, index of the criterion in that level): the index tells apart two
# criteria of one level on the same signal (a range check), so each keeps its own wording
Candidate = tuple[str, int, str, int]


def _sole_unmet(row: Row, scans: Mapping[str, Any]) -> Candidate | None:
    """The criterion when it is the one unmet criterion between a measured repository and its
    next level on ``row``; None otherwise. Taken from the evaluation outcome, never looked up
    by signal alone."""
    ev = evaluate_row(row, dict(scans))
    level = ev["level"]
    if ev["status"] != "level" or level is None or level >= len(row["levels"]):
        return None
    nxt = next((lv for lv in ev["levels"] if lv["level"] == level + 1), None)
    if nxt is None or len(nxt["unmet"]) != 1 or nxt["unmet"][0]["status"] != "unmet":
        return None
    out = nxt["unmet"][0]
    criteria = _criteria(row, level + 1)
    index = next(i for i, c in enumerate(criteria)
                 if (c["signal"], c["op"], c["value"]) == (out["signal"], out["op"], out["value"]))
    return row["id"], level + 1, out["signal"], index


def _criteria(row: Row, level: int) -> list[Criterion]:
    return next(lv["criteria"] for lv in row["levels"] if lv["level"] == level)


def _gate(model: MaturityModel, row: str, level: int) -> tuple[str | None, int | None]:
    for step in sorted(model.get("steps", []), key=lambda s: s["step"]):
        for gate in step["gates"]:
            if gate["row"] == row and gate["level"] == level:
                return gate["id"], step["step"]
    return None, None


def _plural(n: int) -> str:
    return "repository" if n == 1 else "repositories"


def _in_place(team_rows: Sequence[Row], ok: Sequence[FleetRow],
              results: Mapping[str, LevelResult | None]) -> list[InPlace]:
    """Per team row, the measured repositories and those whose level result holds the row at
    its top level (``in_place`` with ``top``: every condition counted and met, #1470), so the
    fleet counts a practice in place by the same rule as the repository's own badge."""
    top: dict[str, set[str]] = {}
    for r in ok:
        result = results.get(r["repo"])
        for entry in result.get("in_place", []) if result is not None else []:
            if entry["top"]:
                top.setdefault(entry["row"], set()).add(r["repo"])
    out: list[InPlace] = []
    for row in team_rows:
        known = [r["repo"] for r in ok if evaluate_row(row, repo_signals(r))["level"] is not None]
        out.append({"row": row["id"], "at_top": sum(repo in top.get(row["id"], set()) for repo in known),
                    "measured": len(known)})
    return out


def _biggest_lift(model: MaturityModel, team_rows: Sequence[Row], lifts: Mapping[Candidate, list[str]],
                  key: Key) -> Lift | None:
    if not lifts:
        return None
    order = {row["id"]: i for i, row in enumerate(team_rows)}
    best = min(lifts, key=lambda c: (-len(lifts[c]), order[c[0]], c[1], c[2], c[3]))
    row_id, level, signal, index = best
    row = team_rows[order[row_id]]
    criterion = _criteria(row, level)[index]
    # an eq_signal criterion's value is a second path, not an operand to set (lib/scoring/levels_fields.py)
    words = None if "eq_signal" in criterion else guidance(signal, criterion["op"], criterion["value"])
    gate, step = _gate(model, row_id, level)
    repos = lifts[best]
    unlock = f" It is the `{gate}` gate for Step {step}." if gate is not None else ""
    text = (f"Completing level {level} on {row['title']} would lift {len(repos)} {_plural(len(repos))} "
            f"to that level.{unlock}")
    return {"row": row_id, "level": level, "signal": signal, "repo_count": len(repos),
            "repos": sorted(key(r) for r in repos), "gate": gate, "step": step,
            "change": words["change"] if words else None,
            "done_when": words["done_when"] if words else None,
            "why": criterion_why(row, level, signal), "text": text}


def _sources(row: FleetRow) -> dict[str, str]:
    """The repository's shared CI source ids, each with its kind (from ``ci_includes``, since
    a hashed id carries no prefix), as the scan wrote them."""
    block = (row["scans"] or {}).get("shared_tooling")
    if not isinstance(block, dict) or block.get("available") is not True:
        return {}
    kinds = {e.get("id"): e.get("kind") for e in block.get("ci_includes") or [] if isinstance(e, dict)}
    out: dict[str, str] = {}
    for i in block.get("ci_source_ids") or []:
        if isinstance(i, str):
            kind = kinds.get(i)
            out[i] = kind if isinstance(kind, str) else i.partition(":")[0]
    return out


def _templates(team_rows: Sequence[Row], ok: Sequence[FleetRow], next_of: Mapping[str, list[Candidate]],
               key: Key) -> list[TemplateLift]:
    includers: dict[str, list[str]] = {}
    kinds: dict[str, str] = {}
    for scanned in ok:
        for source, kind in sorted(_sources(scanned).items()):
            includers.setdefault(source, []).append(scanned["repo"])
            kinds.setdefault(source, kind)
    titles = {row["id"]: row["title"] for row in team_rows}
    order = {row["id"]: i for i, row in enumerate(team_rows)}
    out: list[TemplateLift] = []
    for source, members in includers.items():
        if len(members) < MIN_INCLUDERS:
            continue
        lifted: dict[Candidate, list[str]] = {}
        for member in members:
            for cand in next_of.get(member, []):
                if cand[2].startswith(CI_PREFIX):
                    lifted.setdefault(cand, []).append(member)
        for (row_id, level, signal, _), repos in sorted(lifted.items(), key=lambda kv: (order[kv[0][0]], kv[0][1:])):
            text = (f"If this shared CI source ran tests or another enforced check, {len(repos)} of the "
                    f"{len(members)} repositories that include it would reach level {level} on "
                    f"{titles[row_id]}.")
            out.append({"source": source, "kind": kinds[source], "includers": len(members),
                        "row": row_id, "level": level, "signal": signal, "repo_count": len(repos),
                        "repos": sorted(key(r) for r in repos), "text": text})
    return sorted(out, key=lambda t: (t["source"], order[t["row"]], t["level"], t["signal"]))


def build_opportunities(model: MaturityModel | None, rows: Sequence[FleetRow], key: Key,
                        results: Mapping[str, LevelResult | None] | None = None) -> Opportunities | None:
    """The run's opportunities; None without a maturity model (nothing to lift to). ``results``
    are the rows' level results by id (``fleet_data.level_results``), for ``in_place`` and
    ``step_lifts``; when not passed, ``in_place`` computes each scanned row's (``repo_level``)
    and ``step_lifts`` is empty."""
    if model is None:
        return None
    team_rows = [row for row in model["rows"] if row["scope"] == SCOPE]
    ok = sorted((r for r in rows if r["status"] == STATUS_OK), key=lambda r: r["repo"])
    levels = results if results is not None else {r["repo"]: repo_level(model, r) for r in ok}
    next_of: dict[str, list[Candidate]] = {}
    lifts: dict[Candidate, list[str]] = {}
    for repo in ok:
        for row in team_rows:
            cand = _sole_unmet(row, repo_signals(repo))
            if cand is None:
                continue
            next_of.setdefault(repo["repo"], []).append(cand)
            lifts.setdefault(cand, []).append(repo["repo"])
    return {"measured": len(ok), "in_place": _in_place(team_rows, ok, levels),
            "biggest_lift": _biggest_lift(model, team_rows, lifts, key),
            "shared_templates": _templates(team_rows, ok, next_of, key),
            "step_lifts": step_lifts(model, (results or {}).values())}
