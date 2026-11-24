"""The fleet front screen: one summary that opens ``report.md`` and ``fleet-view.html`` (#1255).

``front_screen`` gathers, from facts other modules already compute, the four numbers a lead
reads first:

- **Steps**: the repositories at each Step (``fleet_step_funnel.funnel_view``, #941), and the
  repositories with no measured Step, never counted at Step 1;
- **Moved**: how many moved up a Step and how many moved down since the compared run
  (``fleet_step_movement.step_moves``, #941), with the moves rule changes alone explain counted
  apart; None when no earlier run was compared, and a not-comparable pair says why;
- **One change away**: the repositories one change from their next Step (the sum of
  ``opportunities.step_lifts``, ``fleet_step_lifts``; each repository counts toward one lift);
- **Gates**: those lifts summed by Step gate and ranked by the repositories each would move,
  most first, then gate id. Gates are ranked; repositories never are, and none is named.

Step names come from ``card_facts.step_name`` and gate wording from ``top_card.GATE_TASK``
(#1252), so ``/assess`` and ``/fleet`` state a Step or a gate the same way.

**Team links** are alphabetical by id and follow the output mode (``fleet_page_data.mode_of``):
``--full`` and hashed runs link each group's page (``fleet_next_steps.page_path``, by group id or
hashed id); a counts-only run links only the ``teams.md`` index, so the front screen names no
group or repository. The page fills its own links (the ownership view's team units, by label).

`front_screen(model, data, moves)` -> `FrontScreen`; `report_teams(groups, mode)` the report's
links; `front_section(front, repos)` the Markdown section, opening with ``In place:`` (#743).
Tests: `tests/test_fleet_front.py`.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict
from urllib.parse import quote

from lib.card_facts import step_name
from lib.fleet.fleet_next_steps import INDEX_FILE, page_path
from lib.fleet.fleet_page_data import COUNTS_ONLY
from lib.fleet.fleet_step_funnel import funnel_view
from lib.fleet.fleet_step_lifts import lifts_of
from lib.fleet.fleet_step_movement import StepMoves
from lib.levels import MaturityModel
from lib.top_card import GATE_TASK
from lib.vocabulary import IN_PLACE

TITLE = "Fleet at a glance"
QUESTION = ("How many repositories sit at each Step, what moved since the last run, and which gate "
            "would move the most repositories?")


class StepCount(TypedDict):
    step: int
    name: str
    count: int


class Movement(TypedDict):
    comparable: bool
    reasons: list[str]  # why the runs are not compared; [] when they are
    up: int
    down: int
    same: int
    rule_only: int  # of ``up`` and ``down``, the moves rule changes alone explain


class GateLift(TypedDict):
    gate: str
    task: str  # the gate as a task (top_card.GATE_TASK)
    repos: int  # repositories meeting it would take to their next Step


class TeamLink(TypedDict):
    label: str
    href: str | None  # a relative link in report.md; None on the page (a unit button)


class FrontScreen(TypedDict):
    skipped: str | None  # why there are no Step counts (no model, no Steps, no fleet-data.json)
    steps: list[StepCount]
    not_measured: int  # repositories with no measured Step
    moved: Movement | None  # None: no earlier run compared
    one_change: int
    gates: list[GateLift]
    teams: list[TeamLink]
    teams_index: str | None  # the counts-only report's single link, to the teams.md index


def _plural(n: int, one: str = "repository", many: str = "repositories") -> str:
    return f"{n} {one if n == 1 else many}"


def movement(moves: StepMoves) -> Movement:
    """Up, down and same over the repositories with a Step in both runs."""
    up = down = same = rules = 0
    for cell in moves["cells"]:
        then, now = cell["then"], cell["now"]
        if not (then.isdigit() and now.isdigit()):  # added, removed and not-measured lanes are no move
            continue
        if int(now) > int(then):
            up += cell["count"]
        elif int(now) < int(then):
            down += cell["count"]
        else:
            same += cell["count"]
        if then != now:
            rules += cell["rule_only"]
    return {"comparable": moves["comparable"], "reasons": list(moves["reasons"]), "up": up, "down": down,
            "same": same, "rule_only": rules}


def gate_lifts(data: Mapping[str, Any]) -> list[GateLift]:
    """``opportunities.step_lifts`` summed by gate: most repositories first, then gate id."""
    counts: Counter[str] = Counter()
    for lift in lifts_of(data):
        counts[lift["gate"]] += lift["repo_count"]
    return [{"gate": gate, "task": GATE_TASK.get(gate, f"make the {gate} gate hold"), "repos": n}
            for gate, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def front_screen(model: MaturityModel | None, data: Mapping[str, Any] | None,
                 moves: StepMoves | None = None) -> FrontScreen:
    """The front screen's facts (see the module docstring); ``moves`` is None without a compared run."""
    front: FrontScreen = {"skipped": None, "steps": [], "not_measured": 0,
                          "moved": movement(moves) if moves is not None else None,
                          "one_change": 0, "gates": [], "teams": [], "teams_index": None}
    if model is None:
        front["skipped"] = "the run had no maturity model (--levels), so no Steps."
    elif not model.get("steps"):
        front["skipped"] = "the maturity model defines no Steps."
    elif data is None:
        front["skipped"] = "the run wrote no fleet-data.json to read the Steps from."
    if front["skipped"] is not None or model is None or data is None:
        return front
    view = funnel_view(model, data)
    front["steps"] = [{"step": b["step"], "name": step_name(model, b["step"]), "count": b["count"]}
                      for b in view["bars"]]
    front["not_measured"] = sum(n for _, n in view["not_measured"])
    front["gates"] = gate_lifts(data)
    front["one_change"] = sum(g["repos"] for g in front["gates"])
    return front


def report_teams(groups: Sequence[str], mode: str) -> tuple[list[TeamLink], str | None]:
    """The report's team links, alphabetical by id, and the counts-only index link (see the docstring)."""
    if mode == COUNTS_ONLY:
        return [], INDEX_FILE if groups else None
    return [{"label": g, "href": quote(page_path(g).as_posix())} for g in sorted(groups)], None


def _moved_line(moved: Movement | None) -> str:
    if moved is None:
        return "This run is the baseline: no earlier run was compared, so no Step moves are counted yet."
    if not moved["comparable"]:
        return ("Not compared with the earlier run: " + "; ".join(moved["reasons"]) + ".")
    line = (f"{_plural(moved['up'])} moved up a Step and {_plural(moved['down'])} moved down; "
            f"{moved['same']} stayed at the same Step.")
    if moved["rule_only"]:
        line += f" {_plural(moved['rule_only'], 'move', 'moves')} came from rule changes alone."
    return line


def front_section(front: FrontScreen, repos: int) -> str:
    """``report.md``'s first section, as Markdown ending in a newline."""
    measured = sum(s["count"] for s in front["steps"])
    if front["skipped"] is not None:
        in_place = f"{IN_PLACE} {_plural(repos)} in this run, scanned with no model call."
    else:
        in_place = f"{IN_PLACE} {_plural(measured)} with a measured Step."
    lines = [f"## {TITLE}", "", in_place, "", QUESTION, ""]
    if front["skipped"] is not None:
        lines += [f"No Step counts: {front['skipped']}", ""]
    else:
        lines += ["| Step | Repositories |", "|------|--------------|"]
        lines += [f"| Step {s['step']}{' · ' + s['name'] if s['name'] else ''} | {s['count']} |" for s in front["steps"]]
        lines.append("")
        if front["not_measured"]:
            lines += [f"{_plural(front['not_measured'])} had no measured Step in this run, so "
                      f"{'it is' if front['not_measured'] == 1 else 'they are'} not counted at any Step.", ""]
    if front["skipped"] is None:
        lines += [f"**Since the last run.** {_moved_line(front['moved'])}", ""]
        verb = "is" if front["one_change"] == 1 else "are"
        lines += [f"**One change away.** {_plural(front['one_change'])} {verb} one change from the next Step.", ""]
        if front["gates"]:
            lines += ["**The gates that would move the most repositories**, most first:", ""]
            lines += [f"{i}. `{g['gate']}`: {g['task']}. Moves {_plural(g['repos'])} to the next Step."
                      for i, g in enumerate(front["gates"], 1)]
            lines.append("")
        else:
            lines += ["No single gate takes a repository to its next Step in this run.", ""]
    if front["teams"]:
        lines += ["**Team pages**, by name:", ""]
        lines += [f"- [{t['label']}](./{t['href']})" if t["href"] else f"- {t['label']}" for t in front["teams"]]
        lines.append("")
        lines += ["`fleet_next_steps.py` writes these pages.", ""]
    elif front["teams_index"]:
        lines += [f"**Team pages** are listed in [`{front['teams_index']}`](./{front['teams_index']}), "
                  "which `fleet_next_steps.py` writes.", ""]
    return "\n".join(lines)
