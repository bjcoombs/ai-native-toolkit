"""Step movement: a grid of each repository's Step in the earlier run against this one (#941).

``step_moves`` joins the ``--compare`` run (``lib/fleet/fleet_compare.FleetRun``) with this
run's ``fleet-data.json`` repositories by id. Rows are the Step then, columns the Step now,
and each cell counts repositories. Three extra lanes keep the measured set apart from the
Steps: ``not_measured`` (a row and a column: no Step on that side, never drawn as Step 1),
``added`` (a row: new in this run) and ``removed`` (a column: not in this run). An added or
removed repository is never shown as a drop. Cells over the Step and ``not_measured`` lanes
sum to the repositories present in both runs.

**Rule changes.** The Step "then" is the earlier run's stored Step (its ``level.json``,
``FleetRun.steps``). The earlier run's scans are also scored again under this run's model
(``fleet_level.repo_level`` over ``fleet_rescore.aliased`` scans, as the Recount does, #771).
A repository whose re-scored Step already equals its Step now, though its stored Step
differs, moved because the rules changed, not the repository: ``rule_only`` counts it
apart in its cell, drawn dashed with a hatched mark. An earlier run without ``level.json``
(kept before #941) reads its re-scored Step as "then", and the grid says moves are not
split. A change of rules alone is bridged by that re-score: the maturity-model version or the rules
hash (``fleet_method.RULE_KEYS``, #952, found by the override ``_version_only`` uses, never by
a reason's wording; this run's keys come from ``fleet-data.json`` ``run``, else the model);
any other reason
the comparison gives (window, scans, output mode, path hashing, fleet config and the rest,
``fleet_compare.comparability``) makes the pair not comparable, and the SVG shows the
reasons in place of the grid. An earlier run that could not be read is named by a fixed
sentence, never by its path. Two hashed runs that share no repository id (another salt, or
``--hash-paths`` with a fresh one) are not comparable either (``NO_JOIN``), never a grid of
every repository added and removed. A stored Step this model lacks (an earlier model with more
Steps) gets its own row and column, so the cells still sum to the repositories in both runs.

No repository is named, so the SVG reads the same in ``--full``, counts-only and hashed
output (a hashed run joins only under one ``--hash-salt``).

`step_moves(model, earlier, data, reasons)` -> `StepMoves` (`comparable`, `reasons`, `steps`,
`split`, `rescored`, `cells`: `then`, `now`, `count`, `rule_only`; lanes are Step numbers as
strings or `not_measured`, `added`, `removed`). `render(moves)` -> `step-movement.svg`, the
stable entry point the fleet page (#946) calls; `in_place(moves)` the report's opening line.
Tests: `tests/test_fleet_step_diagrams.py`.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict, cast

import lib.fleet.fleet_svg as svg
from lib.assess_diagrams import wrap
from lib.fleet.fleet_compare import FleetRun, comparability
from lib.fleet.fleet_method import RULE_KEYS, rule_reasons, rules_sha256
from lib.fleet.fleet_stamp import FleetStamp
from lib.fleet.fleet_level import repo_level
from lib.fleet.runner import STATUS_OK, FleetRow
from lib.levels import MaturityModel
from lib.signal_aliases import aliased
from lib.vocabulary import IN_PLACE

FILE = "step-movement.svg"
TITLE = "Step movement since the earlier run"
QUESTION = "How many repositories changed Step since the earlier run, and how many moved only because the rules changed?"
CAPTION = ("Each cell counts repositories by their Step in the earlier run and in this one. "
           "A dashed cell with a hatched mark holds moves that rule changes alone explain.")
UNREADABLE = "the earlier run's files are at a version this toolkit does not read"
NO_JOIN = ("the two runs hashed repository ids under different salts, so no repository is in both "
           "(compare runs that used one --hash-salt)")

NOT_MEASURED, ADDED, REMOVED = "not_measured", "added", "removed"
LANE_TEXT = {NOT_MEASURED: "Not measured", ADDED: "New in this run", REMOVED: "Not in this run"}

PAD = 16.0
HEAD_W = 150.0
CELL_W = 84.0
CELL_H = 42.0
LINE_H = 17.0
MIN_W = 720.0
ROLES: dict[str, tuple[str, str]] = {
    "same": ("#eef1ef", "#262e2b"), "up": ("#dcefe0", "#1f3b2a"),
    "down": ("#f4ead8", "#3d3322"), "lane": ("#f6f7f6", "#20262a"),
}


class MoveCell(TypedDict):
    then: str  # a Step number as a string, or not_measured / added
    now: str  # a Step number as a string, or not_measured / removed
    count: int
    rule_only: int  # of ``count``, the moves the rule change alone explains


class StepMoves(TypedDict):
    comparable: bool
    reasons: list[str]  # why the pair is not comparable; [] when it is
    steps: list[int]
    split: bool  # the earlier run stored its Steps, so rule-only moves are counted apart
    rescored: bool  # the earlier run used other rules (version or rules hash), bridged by the re-score
    cells: list[MoveCell]  # only cells with a count, in grid order


def _lane(step: int | None) -> str:
    return NOT_MEASURED if step is None else str(step)


def _rescored(model: MaturityModel, row: FleetRow) -> int | None:
    scans = row.get("scans")
    if row["status"] != STATUS_OK or not isinstance(scans, dict):
        return None
    return repo_level(model, cast(FleetRow, {**row, "scans": aliased(scans)}))["step"]


def _rule_bridge(model: MaturityModel, earlier: FleetRun, data: Mapping[str, Any],
                 reasons: Sequence[str]) -> tuple[list[str], bool]:
    """``(blocking, rescored)``: the comparison's reasons that are not rule reasons, and whether the
    rules differ. Rule reasons (``fleet_method.RULE_KEYS``: the model version and the rules hash
    of #952) are found as ``_version_only`` finds them, by comparing the earlier stamp with itself
    under this run's rule keys, so the re-score bridges every one."""
    run = data.get("run") or {}
    now = {key: run[key] if key in run else (model["version"] if key == "levels_version" else rules_sha256(model))
           for key in RULE_KEYS}
    swapped = cast(FleetStamp, {**earlier.stamp, **now})
    rule_texts = set(comparability(earlier.stamp, swapped)[0])
    then = {key: earlier.stamp.get(key) for key in RULE_KEYS}
    rescored = then["levels_version"] != now["levels_version"] or bool(
        rule_reasons(then, now))
    return [r for r in reasons if r not in rule_texts], rescored


def step_moves(model: MaturityModel, earlier: FleetRun | str, data: Mapping[str, Any],
               reasons: Sequence[str] = ()) -> StepMoves:
    """The grid's counts (see the module docstring). ``reasons`` are the comparison's."""
    steps = sorted(s["step"] for s in model.get("steps", []))
    if isinstance(earlier, str):
        return {"comparable": False, "reasons": [UNREADABLE], "steps": steps, "split": False,
                "rescored": False, "cells": []}
    blocking, rescored = _rule_bridge(model, earlier, data, reasons)
    if blocking:
        return {"comparable": False, "reasons": blocking, "steps": steps, "split": False,
                "rescored": rescored, "cells": []}
    then_rows = {row["repo"]: row for row in earlier.repos}
    now = {repo["key"]: repo.get("step") for repo in data.get("repos") or []}
    if earlier.stamp.get("paths_hashed") and then_rows and now and not set(then_rows) & set(now):
        return {"comparable": False, "reasons": [NO_JOIN], "steps": steps, "split": False,
                "rescored": rescored, "cells": []}
    stored = earlier.steps
    counts: Counter[tuple[str, str]] = Counter()
    rules: Counter[tuple[str, str]] = Counter()
    for key in sorted(set(then_rows) | set(now)):
        again = _rescored(model, then_rows[key]) if key in then_rows else None
        then = (stored.get(key) if stored is not None else again) if key in then_rows else None
        if key not in now:
            counts[(_lane(then), REMOVED)] += 1
        elif key not in then_rows:
            counts[(ADDED, _lane(now[key]))] += 1
        else:
            cell = (_lane(then), _lane(now[key]))
            counts[cell] += 1
            rules[cell] += (stored is not None and then is not None and now[key] is not None
                            and then != now[key] and again == now[key])
    # a stored Step this model lacks (an earlier model had more Steps) gets its own row and column
    steps = sorted(set(steps) | {int(lane) for cell in counts for lane in cell if lane.isdigit()})
    rows = [*map(str, steps), NOT_MEASURED, ADDED]
    cols = [*map(str, steps), NOT_MEASURED, REMOVED]
    cells: list[MoveCell] = [{"then": r, "now": c, "count": counts[(r, c)], "rule_only": rules[(r, c)]}
                             for r in rows for c in cols if counts[(r, c)]]
    return {"comparable": True, "reasons": [], "steps": steps, "split": stored is not None,
            "rescored": rescored, "cells": cells}


def _plural(n: int) -> str:
    return f"{n} repository" if n == 1 else f"{n} repositories"


def in_place(moves: StepMoves) -> str:
    if not moves["comparable"]:
        return f"{IN_PLACE} this run, which cannot be compared with the earlier one."
    both = [c for c in moves["cells"] if c["then"] != ADDED and c["now"] != REMOVED]
    same = sum(c["count"] for c in both if c["then"] == c["now"])
    return (f"{IN_PLACE} {_plural(sum(c['count'] for c in both))} in both runs, "
            f"{same} of them at the same Step.")


def _fill(then: str, now: str, count: int) -> str:
    if not count or not (then.isdigit() and now.isdigit()):
        return "lane"
    return "same" if then == now else ("up" if int(now) > int(then) else "down")


def _head(step: str) -> str:
    return f"Step {step}" if step.isdigit() else LANE_TEXT[step]


def _cell(x: float, y: float, then: str, now: str, cell: MoveCell | None) -> str:
    rule = cell is not None and cell["rule_only"] > 0
    dash = ' stroke-dasharray="4 3" data-mark="rule-change"' if rule else ""
    parts = [f'<g data-then="{then}" data-now="{now}">',
             f'<rect x="{x + 1:.1f}" y="{y + 1:.1f}" width="{CELL_W - 2:.1f}" height="{CELL_H - 2:.1f}" '
             f'class="f-{_fill(then, now, cell["count"] if cell else 0)} s-{"fg" if rule else "rule"}" stroke-width="1"{dash}/>']
    if cell is not None:
        parts.append(svg.text(x + CELL_W / 2, y + (17 if rule else 26), str(cell["count"]), size=13,
                              anchor="middle", weight="600", cls="fg"))
    if rule and cell is not None:
        parts.append(f'<rect x="{x + CELL_W - 17:.1f}" y="{y + 5:.1f}" width="12.0" height="12.0" '
                     f'fill="{svg.HATCH_FILL}" class="s-fg" stroke-width="1" data-mark="hatched"/>')
        parts.append(svg.text(x + CELL_W / 2, y + 33, f"{cell['rule_only']} by rules", size=10.5,
                              anchor="middle", cls="muted"))
    parts.append("</g>")
    return "".join(parts)


def render(moves: StepMoves) -> str:
    """``step-movement.svg``: the grid, or the reasons the runs are not compared."""
    rows = [*map(str, moves["steps"]), NOT_MEASURED, ADDED]
    cols = [*map(str, moves["steps"]), NOT_MEASURED, REMOVED]
    width = max(MIN_W, PAD * 2 + HEAD_W + CELL_W * len(cols))
    limit = int((width - 2 * PAD) / svg.CHAR_W)
    body = [svg.text(PAD, 28, TITLE, size=15, weight="600", cls="fg")]
    y = 52.0
    if not moves["comparable"]:
        body.append(svg.text(PAD, y, "Not compared: the two runs differ, so a grid would mislead.", size=12.5,
                             cls="fg"))
        for reason in moves["reasons"]:
            for line in wrap(f"- {reason}", limit):
                y += LINE_H + 1
                body.append(svg.text(PAD + 8, y, line, size=12, cls="muted"))
        desc = in_place(moves)
        return svg.document(width, y + PAD + 4, TITLE, desc, body)
    body.append(svg.text(PAD, y - 4, "Rows: Step in the earlier run. Columns: Step in this run.", size=12,
                         cls="muted"))
    x0, y0 = PAD + HEAD_W, y + 34
    for j, col in enumerate(cols):
        body.append(svg.text(x0 + CELL_W * (j + 0.5), y0 - 10, _head(col), size=11.5, anchor="middle",
                             cls="fg", weight="600"))
    by_pos = {(c["then"], c["now"]): c for c in moves["cells"]}
    for i, row in enumerate(rows):
        top = y0 + CELL_H * i
        body.append(svg.text(x0 - 10, top + 26, _head(row), size=11.5, anchor="end", cls="fg", weight="600"))
        for j, col in enumerate(cols):
            if row == ADDED and col == REMOVED:
                continue
            body.append(_cell(x0 + CELL_W * j, top, row, col, by_pos.get((row, col))))
    y = y0 + CELL_H * len(rows) + 8
    notes = [CAPTION]
    if moves["rescored"]:
        notes.append("The earlier run used other maturity-model rules, so its scans were scored again "
                     "under this run's rules.")
    if not moves["split"]:
        notes.append("The earlier run kept no Step results, so moves from rule changes are not split out.")
    for note in notes:
        for line in wrap(note, limit):
            y += LINE_H + 1
            body.append(svg.text(PAD, y, line, size=12, cls="fg"))
    desc = f"{in_place(moves)} {CAPTION}"
    return svg.document(width, y + PAD, TITLE, desc, body, style=svg.theme_style(ROLES),
                        defs=svg.THEMED_HATCH)
