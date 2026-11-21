"""Before/after slope chart from a ``--compare`` result (issue #675, parent #672).

One line per group from the earlier run to this one: the group's mean
maturity then and now, labelled with the maturity-model row that moved most.
Built from ``lib.fleet_compare.Comparison`` alone (the deltas ``--compare``
already writes to ``compare.json``) plus the maturity model for each row's top
level; nothing is re-scanned.

Each row's level counts as its share of the row's top level over the ``team``
rows (``lib.fleet_maturity``: the unit and row set the quadrant and trend lines
use), so rows with different numbers of levels weigh alike; org rows are left
out of the mean, the counts and the mover. A group's mean
is taken only over the rows with a known level in *both* runs: a row that went
from unknown to known (or back) is left out, so a line never moves because
evidence appeared or vanished. A group with no such row (added or removed
since the earlier run, or unknown throughout) is never drawn at zero: it is
named on a hatched strip under the chart.

When an unknown row carries a confirmed floor (#697) on either side, the group
also gets ``floor_then`` and ``floor_now``: ``fleet_maturity.floor_mean`` over
the rows with a level or a floor in both runs, a lower bound printed in the
line's tooltip as "at least". The line itself stays on the known-only mean.

The label names the row with the largest change in share (ties to the lower
row id) as ``title then→now``; when its move was not a pure level change
(``cause`` ``set`` or ``set_and_level``: the measured set of repositories moved
too) it carries a ``*`` explained under the chart. Up is the accent colour,
down the alarm colour, flat muted; the signed change is printed, so colour is
never the only channel. End labels are spread to a minimum gap
(``fleet_svg.spread_vertical``). Deterministic: groups sorted by name, fixed
number formatting.
"""
from __future__ import annotations

import textwrap
from typing import NotRequired, TypedDict

from lib import fleet_svg as svg
from lib.fleet_compare import CAUSE_LEVEL, Comparison
from lib.fleet_maturity import SCOPE, Cell, floor_mean, has_floor, mean_share
from lib.fleet_svg import THEMED_HATCH, LinearScale
from lib.levels import MaturityModel, floor_of

TITLE = "Fleet maturity, before and after"
FILE = "slope.svg"
WIDTH = 860.0
PAD = 16.0
LX, RX = 250.0, 460.0
PLOT_T = 112.0
PLOT_H = 240.0
LABEL_GAP = 17.0  # minimum vertical distance between end-label baselines
MAX_GROUP = 24  # characters of a group name on the left
MAX_WHY = 42  # characters of the right-hand "group: row then→now" note
WRAP = 116
LINE_H = 16.0


class Mover(TypedDict):
    id: str
    title: str
    then: int
    now: int
    cause: str | None  # the comparison's cause; None when the row did not move


class GroupSlope(TypedDict):
    group: str
    then: float | None  # mean share of top level over paired rows; None: no paired row
    now: float | None
    paired_rows: int  # rows with a known level in both runs
    rows: int  # team rows of the maturity model the group carries on either side
    mover: Mover | None  # the row that moved most; None when no paired row moved
    absent: str | None  # "added" | "removed" when the group is in one run only
    floor_then: NotRequired[float | None]  # floor-inclusive lower bound (#697); absent without floors
    floor_now: NotRequired[float | None]


class SlopeView(TypedDict):
    earlier: str  # scan dates
    later: str
    groups: list[GroupSlope]


class EndLabel(TypedDict):
    group: str
    side: str  # "left" | "right"
    y: float  # the baseline after spreading
    point: float  # the y of the line end it labels


def slope_view(levels: MaturityModel, comparison: Comparison) -> SlopeView:
    """Each group's then/now mean share and its biggest mover, sorted by group."""
    tops = {row["id"]: len(row["levels"]) for row in levels["rows"]}
    titles = {row["id"]: row["title"] for row in levels["rows"]}
    groups: list[GroupSlope] = []
    for change in sorted(comparison["groups"], key=lambda g: g["group"]):
        then_cells: list[tuple[int, int]] = []
        now_cells: list[tuple[int, int]] = []
        mover: Mover | None = None
        best = 0.0
        then_present = now_present = False
        then_floors: list[Cell] = []
        now_floors: list[Cell] = []
        rows = sorted((r for r in change["rows"] if r["scope"] == SCOPE), key=lambda r: r["id"])
        for row in rows:
            then_present = then_present or row["then"] is not None
            now_present = now_present or row["now"] is not None
            top = tops.get(row["id"])
            a = row["then"]["level"] if row["then"] is not None else None
            b = row["now"]["level"] if row["now"] is not None else None
            fa = floor_of(row["then"]) if row["then"] is not None else None
            fb = floor_of(row["now"]) if row["now"] is not None else None
            if top and (a is not None or fa is not None) and (b is not None or fb is not None):
                then_floors.append((a, fa, top))
                now_floors.append((b, fb, top))
            if not top or a is None or b is None:
                continue
            then_cells.append((a, top))
            now_cells.append((b, top))
            moved = abs(b - a) / top
            if moved > best:
                best = moved
                mover = {"id": row["id"], "title": titles.get(row["id"], row["id"]),
                         "then": a, "now": b, "cause": row["cause"]}
        absent = ("added" if not then_present and now_present
                  else "removed" if then_present and not now_present else None)
        slope: GroupSlope = {
            "group": change["group"],
            "then": mean_share(then_cells),
            "now": mean_share(now_cells),
            "paired_rows": len(then_cells), "rows": len(rows),
            "mover": mover, "absent": absent,
        }
        if has_floor([*then_floors, *now_floors]):
            slope["floor_then"] = floor_mean(then_floors)
            slope["floor_now"] = floor_mean(now_floors)
        groups.append(slope)
    earlier, later = comparison["earlier"], comparison["later"]
    return {"earlier": earlier["scan_date"] if earlier is not None else "earlier",
            "later": later["scan_date"] if later is not None else "later", "groups": groups}


def plotted(view: SlopeView) -> list[GroupSlope]:
    return [g for g in view["groups"] if g["then"] is not None and g["now"] is not None]


def delta(group: GroupSlope) -> float:
    """The change in mean share, rounded to the two decimals printed."""
    return round((group["now"] or 0.0) - (group["then"] or 0.0), 2)


def tone(group: GroupSlope) -> str:
    change = delta(group)
    return "accent" if change > 0 else "bad" if change < 0 else "muted"


Y = LinearScale(0.0, 1.0, PLOT_T + PLOT_H, PLOT_T)


def end_labels(view: SlopeView) -> list[EndLabel]:
    """Left and right end-label baselines, spread ``LABEL_GAP`` apart on each side."""
    groups = plotted(view)
    out: list[EndLabel] = []
    for side in ("left", "right"):
        points = [Y((g["then"] if side == "left" else g["now"]) or 0.0) for g in groups]
        for group, point, y in zip(groups, points, svg.spread_vertical([p + 4 for p in points],
                                                                       LABEL_GAP)):
            out.append({"group": group["group"], "side": side, "y": y, "point": point})
    return out


def _why(group: GroupSlope) -> str:
    mover = group["mover"]
    name = svg.truncate(group["group"], 16)
    if mover is None:
        return f"{name}: no row moved"
    mark = " *" if mover["cause"] not in (None, CAUSE_LEVEL) else ""
    tail = f" {mover['then']}→{mover['now']}{mark}"
    room = max(MAX_WHY - len(name) - 2 - len(tail), 6)
    return f"{name}: {svg.truncate(mover['title'], room)}{tail}"


def _floor_tip(group: GroupSlope) -> str:
    then, now = group.get("floor_then"), group.get("floor_now")
    if then is None or now is None:
        return ""
    return f"; with floors at least {then:.2f} → {now:.2f}"


def _unknown_text(group: GroupSlope) -> str:
    if group["absent"] == "added":
        return f"{group['group']} (new since the earlier run)"
    if group["absent"] == "removed":
        return f"{group['group']} (not in this run)"
    return f"{group['group']} (no row measured in both runs)"


def render_slope(view: SlopeView) -> str:
    """The slope chart as an SVG document; the same view gives the same bytes."""
    groups = plotted(view)
    labels = end_labels(view)
    left = {lab["group"]: lab["y"] for lab in labels if lab["side"] == "left"}
    right = {lab["group"]: lab["y"] for lab in labels if lab["side"] == "right"}
    bottom = PLOT_T + PLOT_H
    body = [svg.text(PAD, PAD + 18, TITLE, size=18, weight="bold", cls="fg"),
            svg.text(PAD, PAD + 40, f"{len(view['groups'])} groups · mean maturity in "
                     f"{view['earlier']} and {view['later']}, labelled with the row that moved most",
                     cls="muted"),
            svg.text(PAD, PAD + 58, "Maturity: each team row's level as a share of its top level, "
                     "averaged over the team rows with a known level in both runs", size=11.5,
                     cls="muted")]
    for tick in Y.ticks(4):
        ty = Y(tick)
        body.append(f'<line x1="{LX:.1f}" y1="{ty:.1f}" x2="{RX:.1f}" y2="{ty:.1f}" '
                    f'class="s-rule" stroke-width="0.6"><title>{svg.tick_label(tick)}</title></line>')
    for x, date in ((LX, view["earlier"]), (RX, view["later"])):
        body.append(f'<line x1="{x:.1f}" y1="{PLOT_T - 8:.1f}" x2="{x:.1f}" y2="{bottom:.1f}" '
                    f'class="s-muted" stroke-width="1"/>')
        body.append(svg.text(x, PLOT_T - 16, date, size=11.5, anchor="middle", cls="muted"))
    cursor = max([bottom + 10, *left.values(), *right.values()])
    starred = False
    for group in groups:
        then, now = group["then"] or 0.0, group["now"] or 0.0
        y0, y1, colour = Y(then), Y(now), tone(group)
        change = delta(group)
        tip = (f"{group['group']}: {then:.2f} → {now:.2f} ({change:+.2f}; "
               f"{group['paired_rows']} of {group['rows']} rows known in both runs"
               + _floor_tip(group) + ")")
        body.append(f'<line x1="{LX:.1f}" y1="{y0:.1f}" x2="{RX:.1f}" y2="{y1:.1f}" '
                    f'class="s-{colour}" stroke-width="2.4"><title>{svg.esc(tip)}</title></line>')
        for x, y in ((LX, y0), (RX, y1)):
            body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" class="f-{colour}"/>')
        body.append(svg.text(LX - 12, left[group["group"]],
                             f"{svg.truncate(group['group'], MAX_GROUP)} {then:.2f}", size=12.5,
                             anchor="end", cls="fg", tooltip=group["group"]))
        ry = right[group["group"]]
        body.append(svg.text(RX + 12, ry, f"{now:.2f}", size=12.5, weight="600", cls="fg"))
        body.append(svg.text(RX + 50, ry, "=" if change == 0 else f"{change:+.2f}", size=11.5,
                             cls=colour))
        body.append(svg.text(RX + 96, ry, _why(group), size=11.5, cls="muted"))
        mover = group["mover"]
        starred = starred or (mover is not None and mover["cause"] not in (None, CAUSE_LEVEL))
    cursor += 24
    if starred:
        body.append(svg.text(PAD, cursor, "* the group's measured set of repositories moved too, "
                             "so that change is not a level change alone", size=11.5, cls="muted"))
        cursor += LINE_H + 4
    # a no-break space keeps each group with its count when the note wraps
    partial = [f"{g['group']}\u00a0({g['rows'] - g['paired_rows']}/{g['rows']})" for g in groups
               if g["paired_rows"] < g["rows"]]
    if partial:
        for line in textwrap.wrap("Rows left out of a mean, not known in both runs (left out/rows): "
                                  + ", ".join(partial), WRAP):
            body.append(svg.text(PAD, cursor, line, size=11.5, cls="muted"))
            cursor += LINE_H
        cursor += 4
    unknown = [_unknown_text(g) for g in view["groups"] if g["then"] is None or g["now"] is None]
    if unknown:
        lines = textwrap.wrap("Not plotted, unknown is not zero: " + ", ".join(unknown), WRAP)
        parts, height = svg.hatched_note(PAD, cursor - 13, lines)
        body += parts
        cursor += height
    desc = ("Slope chart: one line per group from its mean maturity in the earlier run to this "
            "one, up in the accent colour, down in the alarm colour; groups with no row known in "
            "both runs are listed on a hatched strip, not drawn at zero.")
    return svg.document(WIDTH, float(int(cursor + PAD)), TITLE, desc, body, defs=THEMED_HATCH)
