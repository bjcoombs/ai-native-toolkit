"""Before/after slope chart from a ``--compare`` run (issue #675, parent #672; lowest Step since #806).

One line per group from the earlier run to this one: the group's lowest Step then and now,
each with "N of M measured" (``lib.fleet.fleet_maturity.group_steps``, the #772 roll-up,
option A). No mean is taken across repositories. The earlier side reads the compared run's
``repos.jsonl`` (each repository's group) and its stored Steps (``level.json``,
``fleet_compare.FleetRun.steps``, #941); this side reads this run's repository rows and the
Steps ``fleet-data.json`` gives them. Nothing is re-scanned.

A group added or removed since the earlier run, or with no measured repository on a side, is
never drawn at Step 0: it is named on a hatched strip under the chart. A ``*`` marks a group
whose repositories or measured count moved too (the comparison's ``set_changed``, or another
"N of M"), so a Step move may come from the set and not the repositories; it is explained
under the chart. Up is the accent colour, down the alarm colour, flat muted; the signed change
is printed, so colour is never the only channel. End labels are spread to a minimum gap
(``fleet_svg.spread_vertical``). Each line carries its tooltip values as data attributes
(#1360): ``data-group``, ``data-then``, ``data-now``, ``data-change``, ``data-measured-then``,
``data-repos-then``, ``data-measured-now`` and ``data-repos-now``. Deterministic: groups
sorted by name, fixed number formatting.

The before/after slope chart (#675). `slope_view(comparison, earlier, rows, steps)` gives each
group its lowest Step with "N of M measured" then (`earlier`: the compared `FleetRun`, its
repositories and stored Steps) and now (`rows` and their `steps`), by
`fleet_maturity.group_steps`, never a mean; `absent` is `added` or `removed` for a group in
one run only, and `set_moved` marks a group whose set (`Comparison` `set_changed`) or measured
count moved. `plotted` is the groups with a Step on both sides; the rest are named on a hatched
strip, never at Step 0. `end_labels` spreads the end labels `LABEL_GAP` apart
(`fleet_svg.spread_vertical`), read by both `render_slope(view, steps)` and its gap test; the
y axis spans the maturity model's Step numbers (`steps`).
"""
from __future__ import annotations

import textwrap
from collections.abc import Mapping, Sequence
from typing import TypedDict

import lib.fleet.fleet_svg as svg
from lib.svg_scope import SCOPES
from lib.fleet.fleet_compare import Comparison, FleetRun
from lib.fleet.fleet_maturity import StepPoint, group_steps, measured_text
from lib.fleet.fleet_svg import THEMED_HATCH, LinearScale
from lib.fleet.runner import FleetRow

TITLE = "Fleet Steps, before and after"
FILE = "slope.svg"
WIDTH = 860.0
PAD = 16.0
LX, RX = 250.0, 460.0
PLOT_T = 112.0
PLOT_H = 240.0
LABEL_GAP = 17.0  # minimum vertical distance between end-label baselines
MAX_GROUP = 22  # characters of a group name on the left
WRAP = 116
LINE_H = 16.0


class GroupSlope(TypedDict):
    group: str
    then: StepPoint | None  # None: the group is not in the earlier run
    now: StepPoint | None  # None: the group is not in this run
    set_moved: bool  # the group's repositories or measured count moved too
    absent: str | None  # "added" | "removed" when the group is in one run only


class SlopeView(TypedDict):
    earlier: str  # scan dates
    later: str
    groups: list[GroupSlope]


class EndLabel(TypedDict):
    group: str
    side: str  # "left" | "right"
    y: float  # the baseline after spreading
    point: float  # the y of the line end it labels


def slope_view(comparison: Comparison, earlier: FleetRun, rows: Sequence[FleetRow],
               steps: Mapping[str, int | None]) -> SlopeView:
    """Each group's lowest Step then and now with N of M measured, sorted by group."""
    then = group_steps(((r["group"], r["repo"]) for r in earlier.repos), earlier.steps or {})
    now = group_steps(((r["group"], r["repo"]) for r in rows), steps)
    moved = {g["group"] for g in comparison["groups"] if g["set_changed"]}
    groups: list[GroupSlope] = []
    for name in sorted({*then, *now}):
        a, b = then.get(name), now.get(name)
        counts = a is not None and b is not None and (a["measured"], a["repos"]) != (b["measured"], b["repos"])
        groups.append({"group": name, "then": a, "now": b, "set_moved": name in moved or counts,
                       "absent": "added" if a is None else "removed" if b is None else None})
    first, last = comparison["earlier"], comparison["later"]
    return {"earlier": first["scan_date"] if first is not None else "earlier",
            "later": last["scan_date"] if last is not None else "later", "groups": groups}


def _step(point: StepPoint | None) -> int | None:
    return None if point is None else point["step"]


def plotted(view: SlopeView) -> list[GroupSlope]:
    """The groups with a lowest Step on both sides."""
    return [g for g in view["groups"] if _step(g["then"]) is not None and _step(g["now"]) is not None]


def delta(group: GroupSlope) -> int:
    """The change in lowest Step."""
    return (_step(group["now"]) or 0) - (_step(group["then"]) or 0)


def tone(group: GroupSlope) -> str:
    change = delta(group)
    return "accent" if change > 0 else "bad" if change < 0 else "muted"


def axis(view: SlopeView, steps: Sequence[int] = ()) -> LinearScale:
    """The y scale over the model's ``steps`` and every Step drawn."""
    seen = [*steps, *(s for g in plotted(view) for s in (_step(g["then"]), _step(g["now"])) if s is not None)]
    lo, hi = (min(seen), max(seen)) if seen else (1, 2)
    return LinearScale(float(lo), float(max(hi, lo + 1)), PLOT_T + PLOT_H, PLOT_T)


def end_labels(view: SlopeView, steps: Sequence[int] = ()) -> list[EndLabel]:
    """Left and right end-label baselines, spread ``LABEL_GAP`` apart on each side."""
    groups = plotted(view)
    y = axis(view, steps)
    out: list[EndLabel] = []
    for side in ("left", "right"):
        points = [y(_step(g["then"] if side == "left" else g["now"]) or 0) for g in groups]
        for group, point, ly in zip(groups, points, svg.spread_vertical([p + 4 for p in points], LABEL_GAP)):
            out.append({"group": group["group"], "side": side, "y": ly, "point": point})
    return out


def _counts(group: GroupSlope) -> str:
    then, now = group["then"], group["now"]
    assert then is not None and now is not None
    mark = " *" if group["set_moved"] else ""
    return f"{then['measured']} of {then['repos']} → {measured_text(now)}{mark}"


def _unknown_text(group: GroupSlope) -> str:
    if group["absent"] == "added":
        return f"{group['group']} (new since the earlier run)"
    if group["absent"] == "removed":
        return f"{group['group']} (not in this run)"
    return f"{group['group']} (no measured repository in both runs)"


def _line(group: GroupSlope, y0: float, y1: float, colour: str) -> str:
    then, now = group["then"], group["now"]
    assert then is not None and now is not None
    change = delta(group)
    tip = (f"{group['group']}: Step {then['step']} → Step {now['step']} ({change:+d}); "
           f"{measured_text(then)} then, {measured_text(now)} now")
    values: dict[str, str | int | None] = {
        "group": group["group"], "then": then["step"], "now": now["step"], "change": f"{change:+d}",
        "measured-then": then["measured"], "repos-then": then["repos"],
        "measured-now": now["measured"], "repos-now": now["repos"]}
    return (f'<line x1="{LX:.1f}" y1="{y0:.1f}" x2="{RX:.1f}" y2="{y1:.1f}" '
            f'class="s-{colour}" stroke-width="2.4"{svg.data_attrs(values)}>'
            f'<title>{svg.esc(tip)}</title></line>')


def render_slope(view: SlopeView, steps: Sequence[int] = ()) -> str:
    """The slope chart as an SVG document; the same view gives the same bytes. ``steps`` are
    the maturity model's Step numbers, so the axis spans every Step."""
    groups = plotted(view)
    y = axis(view, steps)
    labels = end_labels(view, steps)
    left = {lab["group"]: lab["y"] for lab in labels if lab["side"] == "left"}
    right = {lab["group"]: lab["y"] for lab in labels if lab["side"] == "right"}
    bottom = PLOT_T + PLOT_H
    body = [svg.text(PAD, PAD + 18, TITLE, size=18, weight="bold", cls="fg"),
            svg.text(PAD, PAD + 40, f"{len(view['groups'])} groups · lowest Step in "
                     f"{view['earlier']} and {view['later']}, with how many repositories were measured",
                     cls="muted"),
            svg.text(PAD, PAD + 58, "Each group's lowest Step over its measured repositories, never an "
                     "average; a repository with no Step counts as not measured", size=11.5, cls="muted")]
    for tick in range(int(y.lo), int(y.hi) + 1):
        ty = y(tick)
        body.append(f'<line x1="{LX:.1f}" y1="{ty:.1f}" x2="{RX:.1f}" y2="{ty:.1f}" '
                    f'class="s-rule" stroke-width="0.6"{svg.data_attrs({"tick": tick})}>'
                    f'<title>Step {tick}</title></line>')
    for x, date in ((LX, view["earlier"]), (RX, view["later"])):
        body.append(f'<line x1="{x:.1f}" y1="{PLOT_T - 8:.1f}" x2="{x:.1f}" y2="{bottom:.1f}" '
                    f'class="s-muted" stroke-width="1"/>')
        body.append(svg.text(x, PLOT_T - 16, date, size=11.5, anchor="middle", cls="muted"))
    cursor = max([bottom + 10, *left.values(), *right.values()])
    for group in groups:
        then, now = _step(group["then"]) or 0, _step(group["now"]) or 0
        y0, y1, colour = y(then), y(now), tone(group)
        change = delta(group)
        body.append(_line(group, y0, y1, colour))
        for x, py in ((LX, y0), (RX, y1)):
            body.append(f'<circle cx="{x:.1f}" cy="{py:.1f}" r="4" class="f-{colour}"/>')
        body.append(svg.text(LX - 12, left[group["group"]],
                             f"{svg.truncate(group['group'], MAX_GROUP)} Step {then}", size=12.5,
                             anchor="end", cls="fg", tooltip=group["group"]))
        ry = right[group["group"]]
        body.append(svg.text(RX + 12, ry, f"Step {now}", size=12.5, weight="600", cls="fg"))
        body.append(svg.text(RX + 70, ry, "=" if change == 0 else f"{change:+d}", size=11.5, cls=colour))
        body.append(svg.text(RX + 96, ry, _counts(group), size=11.5, cls="muted"))
    cursor += 24
    if any(g["set_moved"] for g in groups):
        body.append(svg.text(PAD, cursor, "* the group's repositories or how many were measured moved too, "
                             "so the Step may have moved with the set", size=11.5, cls="muted"))
        cursor += LINE_H + 4
    unknown = [_unknown_text(g) for g in view["groups"] if g not in groups]
    if unknown:
        lines = textwrap.wrap("Not plotted, unknown is not Step 0: " + ", ".join(unknown), WRAP)
        parts, height = svg.hatched_note(PAD, cursor - 13, lines)
        body += parts
        cursor += height
    desc = ("Slope chart: one line per group from its lowest Step in the earlier run to this one, "
            "never an average, up in the accent colour, down in the alarm colour; groups with no "
            "measured repository on a side are listed on a hatched strip, not drawn at Step 0.")
    return svg.document(WIDTH, float(int(cursor + PAD)), TITLE, desc, body, scope=SCOPES["slope"], defs=THEMED_HATCH)
