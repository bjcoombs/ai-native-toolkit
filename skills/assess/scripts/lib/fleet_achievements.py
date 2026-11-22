"""The fleet achievements engine: what each unit earned, from run data only (issue #704, parent #692).

Reads this run's ``fleet-data.json`` and the earlier comparable runs' copies in the run
history (``lib/fleet_achievements_runs.py`` finds them), plus the maturity model the
runs share, and writes ``achievements.json``. No model call, no clock, no earlier
``achievements.json``: the whole comparable history is replayed every run, so the
output is a pure function of the run data and cannot be gamed by editing a page.

**Subjects.** Every unit of every view in ``fleet-data.json`` (org, division,
department, team, system) and every repository (``view`` ``repo``, ``unit_kind``
``repo``). A unit's members in a run are the repositories whose unit in that view is
the unit or one below it.

**Known.** ``known_level`` is the one place a cell's level is read: a ``level`` cell
gives its level, anything else (``unknown``, a failed scan) gives None. When #697
adds a floor status this predicate decides what a floor earns; nothing else reads
``status``.

**A subject's level on a row** in one run is the row's roll-up rule
(``lib.levels.share_level``; ``min`` is share 1) over its *measured* members, those
with a known level; ``measured`` is that set of repository keys. No measured member:
no level.

**Clean move.** A row moves cleanly between two runs when the subject has a level in
both and the measured set is identical (``lib/fleet_compare.py``'s ``cause: level``).
Anything else (a repository going unknown, failing its scan, joining or leaving the
unit, keys hashed under another salt) is neutral: it neither earns nor loses
anything. This is how an unknown level never earns or loses anything at unit level.

**Re-scored entries (#771).** An earlier run kept under another maturity-model version
replays re-scored under this run's model (``lib/fleet_achievements_runs.py``,
``lib/fleet_rescore.py``), so every move the replay sees is the repositories' own. A move
the rule change alone caused is never earned or lost: it is listed in ``recount``
(``fleet_rescore.recount``, from the newest earlier entry when it was re-scored) and never
feeds the replay. On a step from a re-scored entry into a run at another version (this
run, or one kept under a later version) a repository's own move to or from
unknown counts (``unknown_move``): known to unknown is a slip for its streak, unknown to a
level above 0 extends it; a cell the re-score made unknown, or a failed scan, stays neutral.

**Evidence.** Every earned achievement carries the repositories that moved up on the
row (``moves``: from which level to which, and ``criterion``, the earlier run's first
unmet next-level criterion: the one that went from unmet to met).

**Kinds**, replayed over the comparable runs oldest first; the oldest run is the
baseline and earns nothing:

- ``level_up``: a clean move to a higher level on a row.
- ``first`` (off by default, #772; ``firsts=True``, ``fleet.py --first-achievements``,
  turns it on): a ``level_up`` to a level no subject of the same view and kind held on
  that row in any earlier run of this fleet's history; every subject reaching it in
  the same run is credited. Off by default because it compares a subject with its
  peers, which the no-comparison rule (#740) leaves out. A view and kind with a single
  subject in the run (one org root) earns no ``first``: its level-up already says it.
- ``streak``: the subject's run count since its last slip reaches a milestone
  (``STREAK_MILESTONES``). A slip is a clean move down on any row. A run where the
  subject had a clean move on some row and slipped on none extends the streak; a run
  with no clean move is neutral.
- ``target_hit``: a clean move from below a target to at or above it (targets file,
  ``lib/fleet_targets.py``). A level target reads the subject's level on the row; a
  share target reads its mean share of top level on the row over the measured members
  (``RowState.share``, worked out here from the cells: ``fleet-data.json`` holds no
  average since #772), so a ``min`` row can hit a share target while its level stays put.

``personal_best`` was retired in #772 (an average compared across runs); ``share_from``
and ``share_to`` stay in ``Evidence`` for share targets.

**Next steps** (this run only): ``nearest_level_up`` per subject and row below the top
level: among the measured members one level below the next level, the next-level
criterion (their cell ``signal``) shared by the most of them, the repositories it
applies to, how many must reach the next level for the subject to move (``needed``)
and the change and "done when" from ``lib.next_step_guidance`` (the team next-steps
wording). ``targets`` gives each target's distance: in levels for a level target, in
share of top level for a share target.

**Privacy.** Keys, names and values come from ``fleet-data.json``, already reduced
for counts-only (a unit's ``name`` is null there, so its key is shown). Owners are
never read. Row titles and signal names come from the caller's maturity model.

Deterministic: achievements sorted by run, kind (``KINDS`` order), view, unit, row;
canonical JSON. The same history gives the same bytes.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import NamedTuple, TypedDict

from lib.fleet_data import FLEET_DATA_VERSION, NOT_SCANNED, PLACES, Cell, FleetData
from lib.fleet_maturity import SCOPE, mean_share
from lib.fleet_next_steps import code, expression, text
from lib.fleet_rescore import Recount, recount, rule_unknowns
from lib.levels import MaturityModel, Row, share_level
from lib.next_step_guidance import guidance

# 1 (#704): first version. 2 (#780): share targets (``Evidence.target_share``,
# ``TargetProgress.share_target``/``share``/``share_distance``, ``target`` nullable).
# 3 (#771): ``recount`` (lib/fleet_rescore.py), the rule-caused moves of a maturity-model bump.
# 4 (#772): ``personal_best`` retired; ``first`` off by default.
# Bump on any change to the shapes below; #705 and #744 read it.
ACHIEVEMENTS_VERSION = 4
ACHIEVEMENTS_FILE = "achievements.json"
REPO_VIEW = "repo"
LEVEL_UP, FIRST, TARGET_HIT, STREAK = "level_up", "first", "target_hit", "streak"
NEAREST_LEVEL_UP = "nearest_level_up"
KINDS = (LEVEL_UP, FIRST, TARGET_HIT, STREAK)  # report and sort order
STREAK_MILESTONES = (3, 5, 10, 25, 50, 100)


class Move(TypedDict):
    """One repository's clean move up on one row: the evidence behind an achievement."""

    repo: str
    row: str
    from_level: int
    to_level: int
    criterion: str | None  # the earlier run's first unmet next-level criterion, now met


class Evidence(TypedDict):
    row: str | None  # None for streak
    from_level: int | None
    to_level: int | None
    level: int | None  # first: the level reached first; target_hit: the target level (level targets)
    share_from: float | None  # target_hit (share targets): the row's mean share then
    share_to: float | None  # target_hit (share targets): the row's mean share now
    target_share: float | None  # target_hit (share targets): the target mean share on the row
    runs: int | None  # streak: runs since the last slip
    moves: list[Move]


class Achievement(TypedDict):
    kind: str
    view: str  # "ownership" | "system" | "repo"
    unit: str  # the unit or repository key, as fleet-data.json writes it
    unit_kind: str  # org, division, department, team, system, or repo
    run: str  # the run it was earned in
    previous_run: str  # the run it was earned against
    evidence: Evidence
    credit: str  # one Markdown line


class NextStep(TypedDict):
    kind: str  # always "nearest_level_up"
    view: str
    unit: str
    unit_kind: str
    row: str
    level: int
    top_level: int
    signal: str
    repos: list[str]  # members one level below the next whose next criterion is ``signal``
    needed: int  # members that must reach the next level for the subject to move up
    change: str
    done_when: str
    credit: str  # one Markdown line, phrased as a next step


class TargetProgress(TypedDict):
    view: str
    unit: str
    row: str
    target: int | None  # the target level; None for a share target
    share_target: float | None  # the target mean share on the row; None for a level target
    level: int | None  # None when unknown in this run
    share: float | None  # the row's mean share of top level this run; None when unknown
    distance: int | None  # level targets: levels to go; 0 when met; None when unknown or a share target
    share_distance: float | None  # share targets: share to go; 0 when met; None when unknown or a level target


class Achievements(TypedDict):
    achievements_version: int
    fleet_data_version: int
    run: str
    levels_version: str | None
    runs: list[str]  # the comparable runs replayed, oldest first, this run last
    note: str | None  # why no earlier run was replayed, when none was
    achievements: list[Achievement]
    nearest_level_ups: list[NextStep]
    targets: list[TargetProgress]
    recount: Recount | None  # set on the first run under a new maturity-model version
    rescored_runs: list[str]  # the replayed runs re-scored from an earlier maturity-model version


class HistoryRun(NamedTuple):
    run_id: str
    data: FleetData
    rescored_from: str | None = None  # the stored maturity-model version of a re-scored entry
    stored: FleetData | None = None  # a re-scored entry's cells as stored, under its own rules


class Target(NamedTuple):
    """One target: a ``level`` or a mean ``share`` of top level on ``row`` (exactly one is set)."""

    view: str
    unit: str  # key as fleet-data.json writes it (hashed under the run's salt when hashing)
    row: str
    level: int | None
    share: float | None = None


def target_order(t: Target) -> tuple[str, str, str, int, float]:
    return (t.view, t.unit, t.row, t.level or 0, t.share or 0.0)


@dataclass(frozen=True)
class RowState:
    level: int | None
    measured: frozenset[str]
    share: float | None = None  # mean share of top level over ``measured`` (share targets only)


@dataclass(frozen=True)
class Subject:
    view: str
    key: str
    kind: str
    label: str
    rows: Mapping[str, RowState]


def known_level(cell: Cell) -> int | None:
    """The level a cell counts at; None for anything that is not a known level (see module doc)."""
    return cell["level"] if cell["status"] == "level" else None


# --- one run -----------------------------------------------------------------


def _rules(model: MaturityModel) -> dict[str, tuple[Fraction, int]]:
    """``{row id: (share, top level)}`` for the team rows."""
    out: dict[str, tuple[Fraction, int]] = {}
    for row in model["rows"]:
        if row["scope"] == SCOPE and row["aggregation"] is not None:
            agg = row["aggregation"]
            out[row["id"]] = (Fraction(1) if agg["rule"] == "min" else Fraction(str(agg["share"])), len(row["levels"]))
    return out


def _row_state(cells: Mapping[str, Cell], members: Sequence[str], rule: tuple[Fraction, int]) -> RowState:
    """The level by the row's rule, the measured set, and the mean share of top level over the
    measured members (``lib.fleet_maturity.mean_share``, rounded), which share targets read."""
    known = {m: lv for m in members if m in cells and (lv := known_level(cells[m])) is not None}
    level = share_level(list(known.values()), rule[0], rule[1]) if known else None
    share = mean_share((lv, cells[m]["top_level"]) for m, lv in known.items())
    return RowState(level, frozenset(known), round(share, PLACES) if share is not None else None)


def subjects(data: FleetData, rules: Mapping[str, tuple[Fraction, int]]) -> dict[tuple[str, str], Subject]:
    """Every unit and repository of one run, by ``(view, key)``."""
    cells = run_cells(data)
    out: dict[tuple[str, str], Subject] = {}
    for repo in data["repos"]:
        rows = {row: _row_state({repo["key"]: cells[repo["key"]][row]}, [repo["key"]], rule)
                for row, rule in rules.items() if row in cells[repo["key"]]}
        out[(REPO_VIEW, repo["key"])] = Subject(REPO_VIEW, repo["key"], "repo", repo["key"], rows)
    for view in data["views"]:
        parent = {u["key"]: u["parent"] for u in view["units"]}
        members: dict[str, list[str]] = {u["key"]: [] for u in view["units"]}
        for repo in data["repos"]:
            for placed in repo["units"]:
                current: str | None = placed["unit"] if placed["view"] == view["view"] else None
                while current is not None and current in members:
                    members[current].append(repo["key"])
                    current = parent[current]
        for unit in view["units"]:
            mine = sorted(members[unit["key"]])
            rows = {row: _row_state({m: cells[m][row] for m in mine if row in cells[m]}, mine, rule)
                    for row, rule in rules.items()}
            out[(view["view"], unit["key"])] = Subject(view["view"], unit["key"], unit["kind"],
                                                      unit["name"] or unit["key"], rows)
    return out


def run_cells(data: FleetData) -> dict[str, dict[str, Cell]]:
    """``{repository key: {row id: cell}}`` for one run."""
    return {repo["key"]: {c["row"]: c for c in repo["rows"]} for repo in data["repos"]}


# --- moves -------------------------------------------------------------------


def clean(then: RowState | None, now: RowState | None) -> bool:
    return (then is not None and now is not None and then.level is not None and now.level is not None
            and bool(now.measured) and then.measured == now.measured)


def moves_up(row: str, state: RowState, then_cells: Mapping[str, Mapping[str, Cell]],
             now_cells: Mapping[str, Mapping[str, Cell]]) -> list[Move]:
    """The measured members that moved up on ``row`` (both levels known), with the criterion met."""
    out: list[Move] = []
    for repo in sorted(state.measured):
        before, after = then_cells[repo][row], now_cells[repo][row]
        lv_then, lv_now = known_level(before), known_level(after)
        if lv_then is not None and lv_now is not None and lv_now > lv_then:
            out.append({"repo": repo, "row": row, "from_level": lv_then, "to_level": lv_now,
                        "criterion": before["signal"]})
    return out


def _evidence(row: str | None = None, from_level: int | None = None, to_level: int | None = None,
              moves: list[Move] | None = None, *, level: int | None = None, share_from: float | None = None,
              share_to: float | None = None, runs: int | None = None,
              target_share: float | None = None) -> Evidence:
    return {"row": row, "from_level": from_level, "to_level": to_level, "level": level,
            "share_from": share_from, "share_to": share_to, "runs": runs, "moves": moves or [],
            "target_share": target_share}


def _crossed(target: Target, then: RowState, now: RowState) -> bool:
    """Whether a clean move took the row from below ``target`` to at or above it."""
    if target.share is not None:
        if then.share is None or now.share is None:
            return False
        goal = Fraction(str(target.share))
        return Fraction(str(then.share)) < goal <= Fraction(str(now.share))
    assert then.level is not None and now.level is not None and target.level is not None
    return then.level < target.level <= now.level


# --- credit sentences ----------------------------------------------------------


def _who(subject: Subject) -> str:
    return code(subject.label)


def _pct(share: float) -> str:
    return f"{share * 100:.0f}%"


def _moved(moves: Sequence[Move]) -> str:
    parts = [code(m["repo"]) + (f" met {code(m['criterion'])}" if m["criterion"] else "") for m in moves]
    return "; ".join(parts)


def _credit(kind: str, subject: Subject, ev: Evidence, titles: Mapping[str, str], tops: Mapping[str, int]) -> str:
    row = ev["row"]
    title = text(titles.get(row, row)) if row else ""
    if kind == LEVEL_UP:
        line = f"{_who(subject)} reached level {ev['to_level']} of {tops[row or '']} on {title}"
        if subject.kind == "repo":
            met = ev["moves"][0]["criterion"] if ev["moves"] else None
            return f"{line} by meeting {code(met)}." if met else f"{line}."
        return f"{line}: {_moved(ev['moves'])}."
    if kind == FIRST:
        what = "repository" if subject.kind == "repo" else subject.kind
        return f"A first for this fleet: {_who(subject)} is the first {what} at level {ev['level']} on {title}."
    if kind == TARGET_HIT:
        if ev["target_share"] is not None:
            return (f"{_who(subject)} hit its target of {_pct(ev['target_share'])} of top level on {title}: "
                    f"mean share {_pct(ev['share_from'] or 0.0)} to {_pct(ev['share_to'] or 0.0)}.")
        return f"{_who(subject)} hit its target of level {ev['level']} on {title}."
    return f"{_who(subject)} held every level for {ev['runs']} runs in a row."


# --- replay ------------------------------------------------------------------


def _achievement(kind: str, subject: Subject, run: str, previous: str, ev: Evidence,
                 titles: Mapping[str, str], tops: Mapping[str, int]) -> Achievement:
    return {"kind": kind, "view": subject.view, "unit": subject.key, "unit_kind": subject.kind, "run": run,
            "previous_run": previous, "evidence": ev, "credit": _credit(kind, subject, ev, titles, tops)}




SubjectKey = tuple[str, str]  # (view, key)


class _Replay:
    """What carries from run to run: the highest level each (view, kind, row) has held (for
    ``first``, earned only when ``firsts`` is on) and each subject's streak."""

    def __init__(self, titles: Mapping[str, str], tops: Mapping[str, int], targets: Sequence[Target],
                 firsts: bool = False) -> None:
        self.titles, self.tops, self.targets, self.firsts = titles, tops, targets, firsts
        self.reached: dict[tuple[str, str, str], int] = {}
        self.streak: dict[SubjectKey, int] = {}
        self.out: list[Achievement] = []

    def see(self, subs: Mapping[SubjectKey, Subject]) -> None:
        """Record a run's known levels as held (any run, clean or not)."""
        for sub in subs.values():
            for row, state in sub.rows.items():
                if state.level is not None:
                    slot = (sub.view, sub.kind, row)
                    self.reached[slot] = max(self.reached.get(slot, state.level), state.level)

    def _earn(self, kind: str, sub: Subject, run: str, previous: str, ev: Evidence) -> None:
        self.out.append(_achievement(kind, sub, run, previous, ev, self.titles, self.tops))

    def step(self, run: str, previous: str, then: Mapping[SubjectKey, Subject], now: Mapping[SubjectKey, Subject],
             then_cells: Mapping[str, Mapping[str, Cell]], now_cells: Mapping[str, Mapping[str, Cell]],
             rescored: frozenset[tuple[str, str]] | None = None) -> None:
        """Everything earned moving from ``previous`` to ``run``; then ``run`` is recorded as held.

        ``rescored`` is set when ``previous`` is a re-scored entry: the cells either run's
        re-score made unknown (the rules caused it; see ``unknown_move``)."""
        reached_before = dict(self.reached)
        peers: dict[tuple[str, str], int] = {}
        for sub in now.values():  # a kind with one subject (the org root) has no firsts to claim
            peers[(sub.view, sub.kind)] = peers.get((sub.view, sub.kind), 0) + 1
        for key in sorted(now):
            sub, old = now[key], then.get(key)
            if old is None:  # new to the fleet (or keys under another salt): nothing to compare with
                continue
            clean_rows = [row for row in sorted(sub.rows) if clean(old.rows.get(row), sub.rows[row])]
            slipped = False
            for row in clean_rows:
                then_row, now_row = old.rows[row], sub.rows[row]
                lv_then, lv_now = then_row.level, now_row.level
                assert lv_then is not None and lv_now is not None  # clean() guarantees both
                if lv_now < lv_then:
                    slipped = True
                moves = moves_up(row, now_row, then_cells, now_cells)
                if lv_now > lv_then:
                    self._earn(LEVEL_UP, sub, run, previous, _evidence(row, lv_then, lv_now, moves))
                    held = reached_before.get((sub.view, sub.kind, row))
                    if self.firsts and (held is None or lv_now > held) and peers[(sub.view, sub.kind)] > 1:
                        self._earn(FIRST, sub, run, previous, _evidence(row, lv_then, lv_now, moves, level=lv_now))
                # a share target can be hit with no level-up: under ``min`` the share rises first
                for target in sorted(self.targets, key=target_order):
                    if (target.view, target.unit, target.row) == (sub.view, sub.key, row) \
                            and _crossed(target, then_row, now_row):
                        ev = (_evidence(row, lv_then, lv_now, moves, share_from=then_row.share,
                                        share_to=now_row.share, target_share=target.share)
                              if target.share is not None else
                              _evidence(row, lv_then, lv_now, moves, level=target.level))
                        self._earn(TARGET_HIT, sub, run, previous, ev)
            moved_unknown = [unknown_move(then_cells, now_cells, sub.key, row, rescored)
                             for row in sorted(sub.rows) if row not in clean_rows] \
                if rescored is not None and sub.kind == REPO_VIEW else []
            slipped = slipped or LAPSE in moved_unknown
            if slipped:
                self.streak[key] = 1
            elif clean_rows or GAIN in moved_unknown:
                self.streak[key] = self.streak.get(key, 1) + 1
                if self.streak[key] in STREAK_MILESTONES:
                    self._earn(STREAK, sub, run, previous, _evidence(runs=self.streak[key]))
        self.see(now)


LAPSE, GAIN = "lapse", "gain"


def unknown_move(then_cells: Mapping[str, Mapping[str, Cell]], now_cells: Mapping[str, Mapping[str, Cell]],
                 repo: str, row: str, rule_caused: frozenset[tuple[str, str]]) -> str | None:
    """A repository's move to or from unknown on ``row`` across a step from a re-scored entry (#771):
    known to unknown is a ``LAPSE``, unknown to a level above 0 a ``GAIN``; None otherwise.

    Only a repository-caused move counts: a cell the re-score made unknown (``rule_caused``,
    the rules caused it) or a failed scan is neutral."""
    before, after = then_cells.get(repo, {}).get(row), now_cells.get(repo, {}).get(row)
    if before is None or after is None or (repo, row) in rule_caused \
            or NOT_SCANNED in (before["reason"], after["reason"]):
        return None
    lv_then, lv_now = known_level(before), known_level(after)
    if lv_then is not None and lv_now is None:
        return LAPSE
    if lv_then is None and lv_now:
        return GAIN
    return None


def _sort_key(a: Achievement) -> tuple[int, str, str, str]:
    return (KINDS.index(a["kind"]), a["view"], a["unit"], a["evidence"]["row"] or "")


# --- next steps ----------------------------------------------------------------


def _wording(row: Row, level: int, signal: str) -> tuple[str, str]:
    """``(change, done_when)`` for ``signal``'s criterion at ``level`` of ``row``."""
    criteria = [c for c in row["levels"][level - 1]["criteria"] if c["signal"] == signal] \
        if 0 < level <= len(row["levels"]) else []
    if not criteria:
        return f"Meet {code(signal)}.", f"the next fleet run reads {code(signal)} met."
    words = guidance(signal, criteria[0]["op"], criteria[0]["value"])
    if words is None:
        return f"Meet {expression(criteria[0])}.", f"the next fleet run reads {expression(criteria[0])}."
    return words["change"], words["done_when"]


def nearest_level_up(sub: Subject, row: Row, rule: tuple[Fraction, int],
                     cells: Mapping[str, Mapping[str, Cell]]) -> NextStep | None:
    """The single next-level criterion closest to moving ``sub`` up on ``row``; None at the top
    level, with no measured member, or when no member one level below has a named criterion."""
    state = sub.rows.get(row["id"])
    if state is None or state.level is None or state.level >= rule[1]:
        return None
    target = state.level + 1
    levels = {m: known_level(cells[m][row["id"]]) for m in state.measured}
    above = sum(1 for lv in levels.values() if lv is not None and lv >= target)
    needed = max(1, math.ceil(rule[0] * len(levels)) - above)
    by_signal: dict[str, list[str]] = {}
    for m in sorted(state.measured):
        signal = cells[m][row["id"]]["signal"]
        if levels[m] == state.level and signal is not None:
            by_signal.setdefault(signal, []).append(m)
    if not by_signal:
        return None
    signal = min(by_signal, key=lambda s: (-len(by_signal[s]), s))
    repos = by_signal[signal]
    change, done_when = _wording(row, target, signal)
    where = "" if sub.kind == "repo" else (
        f" Applies to {len(repos)} {'repository' if len(repos) == 1 else 'repositories'} "
        f"({', '.join(code(r) for r in repos)}); {needed} more at level {target} moves "
        f"{_who(sub)} up.")
    credit = (f"Next for {_who(sub)}: level {target} of {rule[1]} on {text(row['title'])}. {change} "
              f"Done when: {done_when}{where}")
    return {"kind": NEAREST_LEVEL_UP, "view": sub.view, "unit": sub.key, "unit_kind": sub.kind, "row": row["id"],
            "level": target, "top_level": rule[1], "signal": signal, "repos": repos, "needed": needed,
            "change": change, "done_when": done_when, "credit": credit}


def target_progress(subs: Mapping[SubjectKey, Subject], targets: Sequence[Target]) -> list[TargetProgress]:
    out: list[TargetProgress] = []
    for t in sorted(targets, key=target_order):
        sub = subs.get((t.view, t.unit))
        state = sub.rows.get(t.row) if sub is not None else None
        level = state.level if state is not None else None
        share = state.share if state is not None else None
        distance = None if level is None or t.level is None else max(0, t.level - level)
        share_distance = None if share is None or t.share is None else round(max(0.0, t.share - share), PLACES)
        out.append({"view": t.view, "unit": t.unit, "row": t.row, "target": t.level, "share_target": t.share,
                    "level": level, "share": share, "distance": distance, "share_distance": share_distance})
    return out


# --- the document --------------------------------------------------------------


def build_achievements(current: HistoryRun, earlier: Sequence[HistoryRun], model: MaturityModel | None, *,
                       targets: Sequence[Target] = (), note: str | None = None,
                       firsts: bool = False) -> Achievements:
    """Replay ``earlier`` (comparable runs, oldest first, before ``current``) then ``current``.

    ``firsts`` turns on the ``first`` kind (off by default, #772).

    ``note`` says why there is no earlier run when the caller read none (for example, the run is
    not kept in the history); a history with no comparable run gets a default note."""
    rules = _rules(model) if model is not None else {}
    rows = {row["id"]: row for row in model["rows"]} if model is not None else {}
    titles = {rid: row["title"] for rid, row in rows.items()}
    tops = {rid: rule[1] for rid, rule in rules.items()}
    runs = [*earlier, current]
    replay = _Replay(titles, tops, targets, firsts)
    states = [(run.run_id, subjects(run.data, rules), run_cells(run.data)) for run in runs]
    caused = [rule_unknowns(run.stored, run.data) if run.stored is not None else None for run in runs]
    replay.see(states[0][1])
    for i, ((prev_id, prev, prev_cells), (run_id, now, now_cells)) in enumerate(zip(states, states[1:])):
        then_caused = caused[i]
        # both runs re-scored from one earlier version: the step keeps its native-model credit
        native = runs[i].rescored_from is not None and runs[i].rescored_from == runs[i + 1].rescored_from
        rescored = (then_caused | (caused[i + 1] or frozenset())
                    if then_caused is not None and not native else None)
        replay.step(run_id, prev_id, prev, now, prev_cells, now_cells, rescored)
    order = {run.run_id: i for i, run in enumerate(runs)}
    achievements = sorted(replay.out, key=lambda a: (order[a["run"]], *_sort_key(a)))
    now_subs, now_cells = states[-1][1], states[-1][2]
    steps = [step for key in sorted(now_subs) for rid in rules
             if (step := nearest_level_up(now_subs[key], rows[rid], rules[rid], now_cells)) is not None]
    last = earlier[-1] if earlier else None
    counted = (recount(last.stored, last.data, model, last.run_id, last.rescored_from)
               if last is not None and last.stored is not None and last.rescored_from is not None
               and model is not None else None)
    if not earlier and note is None:
        note = "no earlier comparable run in the history, so this run is the baseline"
    return {"achievements_version": ACHIEVEMENTS_VERSION, "fleet_data_version": FLEET_DATA_VERSION,
            "run": current.run_id, "levels_version": model["version"] if model is not None else None,
            "runs": [run.run_id for run in runs], "note": note if not earlier else None,
            "achievements": achievements, "nearest_level_ups": steps,
            "targets": target_progress(now_subs, targets), "recount": counted,
            "rescored_runs": [run.run_id for run in earlier if run.rescored_from is not None]}


def dumps_achievements(doc: Achievements) -> str:
    """Canonical bytes: sorted keys, compact, no NaN, trailing newline."""
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"


def write_achievements(out_dir: Path, doc: Achievements) -> Path:
    """Write ``<out_dir>/achievements.json`` through a temp file and ``os.replace``."""
    target = out_dir / ACHIEVEMENTS_FILE
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{ACHIEVEMENTS_FILE}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(dumps_achievements(doc))
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def clear_achievements(out_dir: Path) -> None:
    """Remove an ``achievements.json`` a previous run left (a regular file only)."""
    path = out_dir / ACHIEVEMENTS_FILE
    if path.is_file() and not path.is_symlink():
        path.unlink()
