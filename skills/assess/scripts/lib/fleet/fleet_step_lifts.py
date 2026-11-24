"""One change away: the single conditions that would take the most repositories to their next Step (#941).

``step_lifts`` reads each scanned repository's level result (``lib/fleet/fleet_level.repo_level``,
the result ``level.json`` holds) and is written as ``opportunities.step_lifts`` in
``fleet-data.json`` (``lib/fleet/fleet_opportunities.py``). ``biggest_lift`` is unchanged.

A repository counts toward a condition when it has a Step and a next Step, and exactly one
condition of that next Step's gates is not met (each gate reads its row's conditions from
level 1 up to the gate's level; a condition shared by two gates is one change). That
condition must be ``unmet`` on its own criteria: a ``not_measured`` or ``not_applicable``
one never counts, and nor does one that is unmet only through a ``requires`` (its reason
``requires:<id>``), since meeting it alone changes nothing. So a repository the run could
not see is never promised a lift. Meeting the condition holds every gate of the next Step,
so the repository reaches it.

Each entry is the condition id, its row and that row's model title, the first gate of the
next Step that reads it, the Step reached and the repository count. Entries are ranked by
count (most first), then Step, then the condition's order in the model. No entry names a
repository, so the list reads the same in ``--full``, counts-only and hashed output.

``render`` draws ``one-change-away.svg`` from a ``fleet-data.json`` mapping: one bar per
entry with its count, labelled with the gate and the row title (never the condition id,
which carries a layer number). Repositories whose next Step waits on a gate that was not
measured, or that the owner set aside, are counted on a hatched line under the bars, never
as a lift and never as 0. ``render`` is the stable entry point the fleet page (#946) calls.

`step_lifts(model, results)` -> `list[StepLift]` (`condition`, `row`, `title`, `gate`,
`step`, `repo_count`); `lift_condition(model, result)` is one repository's
`(condition, gate, step)` or None. `render(data)` -> the SVG; `in_place(data)` the report's
opening line. Tests: `tests/test_fleet_step_diagrams.py`.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, TypedDict

import lib.fleet.fleet_svg as svg
from lib.assess_diagrams import wrap
from lib.level_result_types import LevelResult
from lib.levels import MaturityModel, condition_id
from lib.vocabulary import IN_PLACE

FILE = "one-change-away.svg"
TITLE = "One change away from the next Step"
QUESTION = "Which single change would take the most repositories to their next Step?"
CAPTION = ("Each bar counts the repositories that one change would take to their next Step. "
           "A repository waiting on a gate that was not measured is not counted.")

PAD = 16.0
WIDTH = 720.0
BAR_W = 300.0
BAR_H = 22.0
ROW_H = 34.0
LINE_H = 18.0


class StepLift(TypedDict):
    condition: str  # the condition id to meet (``<row id>.<level>``)
    row: str  # its maturity-model row id
    title: str  # that row's model title
    gate: str  # the first gate of the next Step that reads it
    step: int  # the Step the repositories would reach
    repo_count: int


def lift_condition(model: MaturityModel, result: LevelResult) -> tuple[str, str, int] | None:
    """``(condition, gate, step)`` when meeting that one condition takes the repository to its
    next Step; None otherwise (see the module docstring)."""
    nxt = result["next_step"]
    if result["step"] is None or nxt is None:
        return None
    definition = next((s for s in model.get("steps", []) if s["step"] == nxt["step"]), None)
    if definition is None:
        return None
    by_id = {c["id"]: c for c in result["conditions"]}
    missing: dict[str, str] = {}
    for gate in definition["gates"]:
        for level in range(1, gate["level"] + 1):
            cid = condition_id(gate["row"], level)
            if cid in by_id and by_id[cid]["state"] != "met":
                missing.setdefault(cid, gate["id"])
    if len(missing) != 1:
        return None
    ((cid, gate_id),) = missing.items()
    cond = by_id[cid]
    if cond["state"] != "unmet" or cond.get("reason", "").startswith("requires:"):
        return None
    return cid, gate_id, nxt["step"]


def step_lifts(model: MaturityModel | None, results: Iterable[LevelResult | None]) -> list[StepLift]:
    """The ranked lifts over the run's level results (None: a repository not measured)."""
    if model is None:
        return []
    counts = Counter(lift for result in results if result is not None
                     and (lift := lift_condition(model, result)) is not None)
    order: dict[str, int] = {}
    titles: dict[str, tuple[str, str]] = {}
    for row in model["rows"]:
        for level in row["levels"]:
            cid = condition_id(row["id"], level["level"])
            order[cid] = len(order)
            titles[cid] = (row["id"], row["title"])
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0][2], order[kv[0][0]]))
    return [{"condition": cid, "row": titles[cid][0], "title": titles[cid][1], "gate": gate, "step": step,
             "repo_count": count} for (cid, gate, step), count in ranked]


def _plural(n: int) -> str:
    return f"{n} repository" if n == 1 else f"{n} repositories"


def lifts_of(data: Mapping[str, Any]) -> list[StepLift]:
    """``opportunities.step_lifts`` of a ``fleet-data.json`` mapping; [] when absent."""
    return list((data.get("opportunities") or {}).get("step_lifts") or [])


def waiting(data: Mapping[str, Any]) -> tuple[int, int]:
    """``(unknown, not_measured)``: repositories whose next Step waits on a gate not measured or
    set aside by the owner, and repositories with no measured Step."""
    unknown = not_measured = 0
    for repo in data.get("repos") or []:
        if repo.get("step") is None:
            not_measured += 1
            continue
        blockers = (repo.get("next_step") or {}).get("blockers") or []
        unknown += any(b.get("state") in ("not_measured", "not_applicable") for b in blockers)
    return unknown, not_measured


def in_place(data: Mapping[str, Any]) -> str:
    total = sum(lift["repo_count"] for lift in lifts_of(data))
    return f"{IN_PLACE} {_plural(total)} with one change left before their next Step."


def label(lift: StepLift) -> str:
    return f"{lift['gate']} gate ({lift['title']}), to Step {lift['step']}"


def _notes(data: Mapping[str, Any]) -> list[str]:
    unknown, not_measured = waiting(data)
    notes = []
    if unknown:
        notes.append(f"{_plural(unknown)} {'waits' if unknown == 1 else 'wait'} on a gate that was not measured or that the owner set "
                     "aside. No lift is promised for them.")
    if not_measured:
        notes.append(f"{_plural(not_measured)} had no measured Step in this run.")
    return notes


def render(data: Mapping[str, Any]) -> str:
    """``one-change-away.svg`` from a ``fleet-data.json`` mapping."""
    lifts = lifts_of(data)
    top = max((lift["repo_count"] for lift in lifts), default=0)
    scale = svg.LinearScale(0, svg.nice_ceiling(top), 0, BAR_W)
    body = [svg.text(PAD, 28, TITLE, size=15, weight="600", cls="fg"),
            svg.text(PAD, 48, "Repositories one change from their next Step, by the change", size=12, cls="muted")]
    y = 72.0
    limit = int((WIDTH - 2 * PAD) / svg.CHAR_W)
    if not lifts:
        body.append(svg.text(PAD, y, "No single change takes a repository to its next Step in this run.",
                             size=12.5, cls="fg"))
        y += ROW_H
    for lift in lifts:
        name = label(lift)
        width = scale(lift["repo_count"])
        body.append(f'<g data-condition="{svg.esc(lift["condition"])}"><title>{svg.esc(name)}</title>'
                    + svg.text(PAD, y, svg.truncate(name, limit), size=12, cls="fg")
                    + f'<rect x="{PAD:.1f}" y="{y + 6:.1f}" width="{width:.1f}" height="{BAR_H:.1f}" '
                    'class="f-accent"/>'
                    + svg.text(PAD + width + 8, y + 6 + BAR_H - 6, _plural(lift["repo_count"]), size=12,
                               cls="fg")
                    + "</g>")
        y += ROW_H + 14
    for note in _notes(data):
        lines = wrap(note, limit - 4)
        parts, used = svg.hatched_note(PAD, y - 6, lines, size=12, line_h=LINE_H)
        body.append(f'<g data-mark="unknown">{"".join(parts)}</g>')
        y += used + 8
    for line in wrap(CAPTION, limit):
        y += LINE_H
        body.append(svg.text(PAD, y, line, size=12, cls="fg"))
    desc = f"{in_place(data)} {CAPTION}"
    return svg.document(WIDTH, y + PAD, TITLE, desc, body, defs=svg.THEMED_HATCH)


def bars(lifts: Sequence[StepLift]) -> list[tuple[str, int]]:
    """The drawn bars as ``(label, count)``, in order: what a reader of the SVG sees."""
    return [(label(lift), lift["repo_count"]) for lift in lifts]
