"""Per-layer maturity bars SVG from one fleet run (issue #675, parent #672).

One horizontal bar per ``team`` row of the maturity model (a layer), split by
how many repositories sit at each level of that row. Every repository in the
run is in every bar, so each bar is the same length and a wide pale band is
where the whole fleet is behind.

A repository's level for a row is ``lib.levels.evaluate_row`` against its own
``scans``, as the quadrant and the team pages read it. A repository whose
level for a row is undetermined (a failed scan, a signal the run did not
collect) is a hatched "unknown" segment at the end of the bar, never drawn as
level 0. ``org`` rows read the group's settings, not its repositories, so they
have no per-repository level and are named under the bars instead.

An unknown repository whose confirmed criteria reach a floor (#697,
``lib.levels.floor_of``) is drawn first within the unknown part of the bar: the
ramp colour of its floor under a hatch, so it reads as "at least N", apart from
both a known level and plain unknown. The notes name it with its floor.

Levels are coloured with the heat map's ramp (``fleet_svg.LEVEL_RAMP``) by their
share of the row's top level, so a row's top level is always the darkest step
whatever its number of levels; each segment prints its count in the ramp's
fixed text colour. Deterministic: bars in maturity-model order, fixed number
formatting.

The per-layer maturity bars (#675). `layer_bars(levels, repos)` evaluates every `team` row
against each repository's own `scans` (`levels.evaluate_row`) and counts repositories per
level, with the undetermined ones (failed scan, uncollected signal) apart. `render_layer_bars`
stacks them pale to dark on the heat map's ramp (`fleet_svg.ramp_step`, so a row's top level is
the darkest step whatever its level count), the unknown segment hatched and its repositories
named, never counted as level 0; `org` rows, which have no per-repository level, are named
under the bars.
An unknown repository with a floor (#697) is named in `floored` and drawn first in the unknown part of the bar, in its floor's ramp colour under a hatch; the notes name it "(at least N)".
"""
from __future__ import annotations

import textwrap
from collections.abc import Sequence
from typing import NotRequired, TypedDict

from lib import fleet_svg as svg
from lib.fleet import FleetRow
from lib.fleet_groups import repo_signals
from lib.fleet_svg import HATCH_FILL, LEVEL_RAMP, STEP_TEXT, THEMED_HATCH, LinearScale
from lib.levels import MaturityModel, evaluate_row, floor_of

TITLE = "Fleet maturity by layer"
FILE = "layer-bars.svg"
WIDTH = 760.0
PAD = 16.0
PLOT_R = 700.0
TOP = 84.0
ROW_H = 38.0
BAR_H = 26.0
MAX_LABEL = 34  # characters of a row title at 12.5px
MIN_COUNT_W = 18.0  # narrower segments carry their count in the tooltip only
WRAP = 104
LINE_H = 16.0
MAX_NAMED = 5  # unknown repositories named per bar before "and N more"


class LayerBar(TypedDict):
    id: str
    title: str
    top: int  # the row's top level
    counts: list[int]  # repositories at level 0..top
    unknown: list[str]  # repositories whose level for this row is undetermined, sorted
    floored: NotRequired[dict[str, int]]  # of those, each one with a confirmed floor (#697)


def layer_bars(levels: MaturityModel, repos: Sequence[FleetRow]) -> list[LayerBar]:
    """Per team row of the maturity model, in its order: repositories at each level and unknown."""
    ordered = sorted(repos, key=lambda r: r["repo"])
    signals = [(repo["repo"], repo_signals(repo)) for repo in ordered]
    bars: list[LayerBar] = []
    for row in levels["rows"]:
        if row["scope"] != "team":
            continue
        top = len(row["levels"])
        counts = [0] * (top + 1)
        unknown: list[str] = []
        floored: dict[str, int] = {}
        for name, data in signals:
            evaluation = evaluate_row(row, data)
            level = evaluation["level"]
            if level is None:
                unknown.append(name)
                floor = floor_of(evaluation)
                if floor is not None:
                    floored[name] = floor
            else:
                counts[level] += 1
        bar: LayerBar = {"id": row["id"], "title": row["title"], "top": top, "counts": counts,
                         "unknown": unknown}
        if floored:
            bar["floored"] = floored
        bars.append(bar)
    return bars


FLOOR_ID = "fleet-bars-floor"
FLOOR_FILL = f"url(#{FLOOR_ID})"
# Hatch lines on a transparent ground, laid over a floor segment's ramp colour.
FLOOR_HATCH = svg.hatch_pattern(FLOOR_ID, "none", "#8c8c8c", "1.4")


def floor_counts(bar: LayerBar) -> list[tuple[int, int]]:
    """(floor, repositories) for each floor level present in the bar, lowest first."""
    floors = list(bar.get("floored", {}).values())
    return [(level, floors.count(level)) for level in sorted(set(floors))]


def unknown_names(bar: LayerBar, total: int) -> str:
    """The repositories with an unknown level for a bar, capped so a fleet-wide gap stays one line."""
    floored = bar.get("floored", {})
    names = [f"{n} (at least {floored[n]})" if n in floored else n for n in bar["unknown"]]
    if len(names) == total and not floored:
        return f"all {total} repositories"
    if len(names) <= MAX_NAMED:
        return ", ".join(names)
    return f"{', '.join(names[:MAX_NAMED])} and {len(names) - MAX_NAMED} more"


def axis_step(total: int) -> int:
    """An integer tick step giving at most about seven ticks."""
    return max(1, int(svg.nice_ceiling(total / 7)))


def _plot_left(bars: Sequence[LayerBar]) -> float:
    longest = max((len(svg.truncate(b["title"], MAX_LABEL)) for b in bars), default=0)
    return PAD + svg.text_width("x" * longest, 12.5) + 14


def _legend(bars: Sequence[LayerBar], left: float, cursor: float) -> tuple[list[str], float]:
    """One swatch row per distinct top level: level 0 (pale) up to that top (darkest)."""
    parts: list[str] = []
    tops = sorted({b["top"] for b in bars})
    for top in tops:
        x = left
        if len(tops) > 1:
            lead = f"Rows with top level {top}:"
            parts.append(svg.text(x, cursor, lead, size=11.5, cls="muted"))
            x += svg.text_width(lead, 11.5) + 10
        start = x
        for level in range(top + 1):
            step = svg.ramp_step(level, top)
            label = f"Level {level}" + (" (top)" if level == top else "")
            if x + 20 + svg.text_width(label, 11.5) > WIDTH - PAD:  # wrap a long ramp
                x, cursor = start, cursor + LINE_H + 2
            parts.append(f'<rect x="{x:.1f}" y="{cursor - 11:.1f}" width="14.0" height="14.0" '
                         f'fill="{LEVEL_RAMP[step]}" class="s-muted" stroke-width="0.6"/>')
            parts.append(svg.text(x + 20, cursor, label, size=11.5, cls="fg"))
            x += 20 + svg.text_width(label, 11.5) + 16
        cursor += LINE_H + 2
    swatch = (f'<rect x="{left:.1f}" y="{cursor - 11:.1f}" width="14.0" height="14.0" '
              f'fill="{HATCH_FILL}" class="s-muted" stroke-width="0.6"/>')
    parts += [swatch, svg.text(left + 20, cursor, "Unknown: level undetermined, never counted as 0",
                               size=11.5, cls="fg")]
    if any(b.get("floored") for b in bars):
        cursor += LINE_H + 2
        parts += [f'<rect x="{left:.1f}" y="{cursor - 11:.1f}" width="14.0" height="14.0" '
                  f'fill="{LEVEL_RAMP[2]}"/>',
                  f'<rect x="{left:.1f}" y="{cursor - 11:.1f}" width="14.0" height="14.0" '
                  f'fill="{FLOOR_FILL}" class="s-muted" stroke-width="0.6"/>',
                  svg.text(left + 20, cursor, "At least level N: confirmed up to N, a higher level not "
                           "measured (a lower bound)", size=11.5, cls="fg")]
    return parts, cursor + LINE_H + 2


def _segment(x0: float, x1: float, y: float, fill: str, ink: str | None, count: int,
             tip: str) -> list[str]:
    parts = [f'<rect x="{x0:.1f}" y="{y:.1f}" width="{x1 - x0:.1f}" height="{BAR_H:.1f}" '
             f'fill="{fill}" class="s-bg" stroke-width="1"><title>{svg.esc(tip)}</title></rect>']
    if x1 - x0 > MIN_COUNT_W:
        # a ramp segment's ink is a fixed colour (its fill is fixed); the hatch follows the theme
        parts.append(svg.text((x0 + x1) / 2, y + 17, str(count), size=11, anchor="middle",
                              fill=ink, cls=None if ink is not None else "fg",
                              weight=None if ink is not None else "600"))
    return parts


def render_layer_bars(bars: Sequence[LayerBar], org_rows: Sequence[str] = ()) -> str:
    """The layer bars as an SVG document; the same bars give the same bytes."""
    total = sum(bars[0]["counts"]) + len(bars[0]["unknown"]) if bars else 0
    left = _plot_left(bars)
    x = LinearScale(0.0, float(max(total, 1)), left, PLOT_R)
    bottom = TOP + ROW_H * len(bars)
    body = [svg.text(PAD, PAD + 18, TITLE, size=18, weight="bold", cls="fg"),
            svg.text(PAD, PAD + 40, f"{total} repositories · one bar per team row of the maturity "
                     "model, split by level reached", cls="muted"),
            svg.text(PAD, PAD + 58, "A wide pale or hatched band is where the whole fleet is behind",
                     size=11.5, cls="muted")]
    step = axis_step(total)
    for tick in range(0, total + 1, step):
        tx = x(tick)
        body.append(f'<line x1="{tx:.1f}" y1="{TOP:.1f}" x2="{tx:.1f}" y2="{bottom:.1f}" '
                    f'class="s-rule" stroke-width="0.6"/>')
        body.append(svg.text(tx, bottom + 16, str(tick), size=11, anchor="middle", cls="muted"))
    for index, bar in enumerate(bars):
        y = TOP + index * ROW_H + 6
        body.append(svg.text(left - 10, y + 17, svg.truncate(bar["title"], MAX_LABEL), size=12.5,
                             anchor="end", cls="fg", tooltip=f"{bar['title']} ({bar['id']})"))
        acc = 0
        for level, count in enumerate(bar["counts"]):
            if count == 0:
                continue
            ramp = svg.ramp_step(level, bar["top"])
            tip = f"{bar['title']}: {count} at level {level} of {bar['top']}"
            body += _segment(x(acc), x(acc + count), y, LEVEL_RAMP[ramp], STEP_TEXT[ramp], count, tip)
            acc += count
        floored = bar.get("floored", {})
        for floor, count in floor_counts(bar):
            ramp = svg.ramp_step(floor, bar["top"])
            names = ", ".join(n for n in bar["unknown"] if floored.get(n) == floor)
            tip = f"{bar['title']}: at least level {floor} of {bar['top']} for {names}"
            rect, *label = _segment(x(acc), x(acc + count), y, LEVEL_RAMP[ramp], STEP_TEXT[ramp], count, tip)
            overlay = (f'<rect x="{x(acc):.1f}" y="{y:.1f}" width="{x(acc + count) - x(acc):.1f}" '
                       f'height="{BAR_H:.1f}" fill="{FLOOR_FILL}" pointer-events="none"/>')
            body += [rect, overlay, *label]
            acc += count
        plain = [n for n in bar["unknown"] if n not in floored]
        if plain:
            count = len(plain)
            tip = f"{bar['title']}: level unknown for {', '.join(plain)}"
            body += _segment(x(acc), x(acc + count), y, HATCH_FILL, None, count, tip)
    body.append(svg.text((left + PLOT_R) / 2, bottom + 34, "Repositories", anchor="middle",
                         cls="muted"))
    parts, cursor = _legend(bars, left, bottom + 58)
    body += parts
    notes = [f"Level unknown for {b['title']}: {unknown_names(b, total)}." for b in bars if b["unknown"]]
    if org_rows:
        notes.append("Not drawn: org rows read group settings, not repositories: "
                     + ", ".join(org_rows) + ".")
    cursor += 4
    for note in notes:
        for line in textwrap.wrap(note, WRAP):
            body.append(svg.text(PAD, cursor, line, size=11.5, cls="muted"))
            cursor += LINE_H
    desc = ("Stacked horizontal bars, one per team row of the maturity model: how many fleet "
            "repositories reach each level, pale to dark; repositories with an undetermined level "
            "are a hatched segment, not counted as level 0.")
    floors = any(b.get("floored") for b in bars)
    return svg.document(WIDTH, float(int(cursor + PAD)), TITLE, desc, body,
                        defs=THEMED_HATCH + (FLOOR_HATCH if floors else ""))
