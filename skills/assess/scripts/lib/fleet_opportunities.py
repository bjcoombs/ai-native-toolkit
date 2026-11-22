"""Fleet opportunities: the changes that would lift the most repositories (issue #746, parent #740).

Computed from one run's rows and the maturity model, with no model call and no history, and
written as ``opportunities`` in ``fleet-data.json`` (``lib/fleet_data.py``). ``report.md``
renders it as its "Opportunities" section (``opportunities_section``). Every insight is
phrased as an opportunity: what is already in place first, then the change that unlocks the
next level. No team or repository is ranked, numbered or compared, and no insight names a
person (the inputs are scan blocks and repository ids only).

- ``in_place``: per ``team`` row, how many measured repositories already hold its top level.
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

Recent progress (units that reached a new level since the previous run) is display only:
the report section reads it from this run's ``level_up`` achievements
(``lib/fleet_achievements.py``), which come from the run history. It is never written into
``fleet-data.json``, the achievements engine's own input, and never feeds a level.

Privacy follows ``fleet-data.json``: repository keys and shared-source ids are written with
the run's ``Privacy.key`` (in clear in counts-only output, hashed under ``--hash-paths``);
``text`` lines hold counts and maturity-model titles only. Deterministic: lists sorted.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypedDict

from lib.fleet import STATUS_OK, FleetRow
from lib.fleet_groups import repo_signals
from lib.fleet_maturity import SCOPE
from lib.levels import Criterion, MaturityModel, Row, evaluate_row
from lib.next_step_guidance import guidance

TITLE = "Opportunities"
CI_PREFIX = "ci_checks."
MIN_INCLUDERS = 2  # a CI source fewer repositories include is not shared
PER_LIST = 20  # repository names per report line; the rest are counted


class InPlace(TypedDict):
    row: str
    at_top: int  # measured repositories at the row's top level
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


Key = Callable[[str], str]
Candidate = tuple[str, int, str]  # (row id, level, signal)


def _sole_unmet(row: Row, scans: Mapping[str, Any]) -> tuple[int, int, str] | None:
    """``(current level, next level, signal)`` when one unmet criterion stands between a
    measured repository and its next level on ``row``; None otherwise."""
    ev = evaluate_row(row, dict(scans))
    level = ev["level"]
    if ev["status"] != "level" or level is None or level >= len(row["levels"]):
        return None
    nxt = next((lv for lv in ev["levels"] if lv["level"] == level + 1), None)
    if nxt is None or len(nxt["unmet"]) != 1 or nxt["unmet"][0]["status"] != "unmet":
        return None
    return level, level + 1, nxt["unmet"][0]["signal"]


def _criterion(row: Row, level: int, signal: str) -> Criterion:
    (match,) = [c for lv in row["levels"] if lv["level"] == level for c in lv["criteria"]
                if c["signal"] == signal][:1]
    return match


def _gate(model: MaturityModel, row: str, level: int) -> tuple[str | None, int | None]:
    for step in sorted(model.get("steps", []), key=lambda s: s["step"]):
        for gate in step["gates"]:
            if gate["row"] == row and gate["level"] == level:
                return gate["id"], step["step"]
    return None, None


def _plural(n: int) -> str:
    return "repository" if n == 1 else "repositories"


def _in_place(team_rows: Sequence[Row], ok: Sequence[FleetRow]) -> list[InPlace]:
    out: list[InPlace] = []
    for row in team_rows:
        levels = [evaluate_row(row, repo_signals(r))["level"] for r in ok]
        known = [lv for lv in levels if lv is not None]
        out.append({"row": row["id"], "at_top": sum(lv == len(row["levels"]) for lv in known),
                    "measured": len(known)})
    return out


def _biggest_lift(model: MaturityModel, team_rows: Sequence[Row], lifts: Mapping[Candidate, list[str]],
                  key: Key) -> Lift | None:
    if not lifts:
        return None
    order = {row["id"]: i for i, row in enumerate(team_rows)}
    (row_id, level, signal) = min(lifts, key=lambda c: (-len(lifts[c]), order[c[0]], c[1], c[2]))
    row = team_rows[order[row_id]]
    criterion = _criterion(row, level, signal)
    words = guidance(signal, criterion["op"], criterion["value"])
    gate, step = _gate(model, row_id, level)
    repos = lifts[(row_id, level, signal)]
    unlock = f" It is the `{gate}` gate for Step {step}." if gate is not None else ""
    text = (f"Completing level {level} on {row['title']} would lift {len(repos)} {_plural(len(repos))} "
            f"to that level.{unlock}")
    return {"row": row_id, "level": level, "signal": signal, "repo_count": len(repos),
            "repos": sorted(key(r) for r in repos), "gate": gate, "step": step,
            "change": words["change"] if words else None,
            "done_when": words["done_when"] if words else None, "text": text}


def _sources(row: FleetRow) -> list[str]:
    block = (row["scans"] or {}).get("shared_tooling")
    if not isinstance(block, dict) or block.get("available") is not True:
        return []
    return sorted({i for i in block.get("ci_source_ids") or [] if isinstance(i, str)})


def _templates(team_rows: Sequence[Row], ok: Sequence[FleetRow], next_of: Mapping[str, list[Candidate]],
               key: Key) -> list[TemplateLift]:
    includers: dict[str, list[str]] = {}
    for scanned in ok:
        for source in _sources(scanned):
            includers.setdefault(source, []).append(scanned["repo"])
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
        for (row_id, level, signal), repos in sorted(lifted.items(), key=lambda kv: (order[kv[0][0]], kv[0][1:])):
            text = (f"If this shared CI source ran tests or another enforced check, {len(repos)} of the "
                    f"{len(members)} repositories that include it would reach level {level} on "
                    f"{titles[row_id]}.")
            out.append({"source": key(source), "kind": source.partition(":")[0], "includers": len(members),
                        "row": row_id, "level": level, "signal": signal, "repo_count": len(repos),
                        "repos": sorted(key(r) for r in repos), "text": text})
    return sorted(out, key=lambda t: (t["source"], order[t["row"]], t["level"], t["signal"]))


def build_opportunities(model: MaturityModel | None, rows: Sequence[FleetRow], key: Key) -> Opportunities | None:
    """The run's opportunities; None without a maturity model (nothing to lift to)."""
    if model is None:
        return None
    team_rows = [row for row in model["rows"] if row["scope"] == SCOPE]
    ok = sorted((r for r in rows if r["status"] == STATUS_OK), key=lambda r: r["repo"])
    next_of: dict[str, list[Candidate]] = {}
    lifts: dict[Candidate, list[str]] = {}
    for repo in ok:
        for row in team_rows:
            found = _sole_unmet(row, repo_signals(repo))
            if found is None:
                continue
            cand = (row["id"], found[1], found[2])
            next_of.setdefault(repo["repo"], []).append(cand)
            lifts.setdefault(cand, []).append(repo["repo"])
    return {"measured": len(ok), "in_place": _in_place(team_rows, ok),
            "biggest_lift": _biggest_lift(model, team_rows, lifts, key),
            "shared_templates": _templates(team_rows, ok, next_of, key)}
