"""Practice shape by unit: one radar per unit, one axis per practice (#1367, part 2 of #1314).

``radar_view(data, min_share)`` reads ``fleet-data.json`` alone: the model's ``team`` rows
(``model``), each repository's cells (``repos[].rows``) for the fleet-wide measured share, and
each unit's per-practice counts (``views[].units[].levels[]``: ``met``, ``measured``, ``repos``,
#1366). No mean of levels is taken (#772): a point is the unit's ``met`` over ``measured``, the
share of its measured repositories at the practice's top level.

**Axes.** One per ``team`` row of the model, in model order, kept when at least
``min_share`` percent (an integer, default ``RADAR_MIN_SHARE`` = 20, ``fleet.py
--radar-min-measured``) of the run's repositories have a cell with status ``level`` on it,
compared in integers (``known * 100 >= min_share * repos``); a failed repository counts in the
denominator, and a run with no repository keeps none. This is the rule of
``lib.fleet.fleet_page_data.radar_axes`` (#1368), so the report's radar and the page's spokes
keep the same practices: the count, the rule and the default are imported from there
(``measured_counts``, ``keeps_axis``, ``RADAR_MIN_SHARE``). The dropped practices are named in the caption.

**Units nest.** ``views[].units[]`` is a tree, and each unit's counts cover every repository
below it at any depth (``fleet_data.build_view``). Every unit with at least one repository
gets its own radar, as the fleet page and the achievements credit each unit at every depth: in
the document's order (the ownership view, then the system view, each sorted by key), never
ranked, never overlaid, laid out as small multiples (``COLUMNS`` per line). A nested unit
carries ``depth`` and ``parent`` (its parent's label); its subtitle and its table column name
the unit it sits in, and the caption says a parent's radar redraws its children's
repositories. No figure adds units together: ``in_place`` counts the run's repositories from
``repos[].rows``, each once, never unit and practice pairs.

**Unknown is never 0.** An axis the unit measured on no repository has no point: a hatched
mark sits at the rim with a ``not measured`` tooltip, and the outline breaks there (a segment is
drawn only between two neighbouring measured axes). The outline is filled only when every axis
is measured. A real 0 (measured, none at the top level) is a point at the centre.

**Rings** at 25, 50, 75 and 100%. Each point's ``<title>`` holds the unit, the practice and
``met/measured``.

**Labels.** A unit is labelled by its ``name`` (else its key) only when the run kept names
(``--full``) or hashed them (``--hash-salt``: the key is the hash, the name is null). A plain
counts-only run labels units by view and place (``ownership unit 2``), so neither the picture
nor the table names a unit or a repository.

``table(view)`` is the Markdown table ``report.md`` prints under the picture: one line per
practice, one column per unit (``label (in parent)`` when nested), each cell ``met/measured`` or
``not measured``. ``in_place`` is the section's opening line, strengths first: the run's
repositories at the top level on at least one drawn practice. ``render(view)`` is the SVG. Deterministic:
fixed order and formatting, no date. Tests: ``tests/test_fleet_radar.py``.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

import lib.fleet.fleet_svg as svg
from lib.assess_diagrams import wrap
from lib.fleet.fleet_page_data import RADAR_MIN_SHARE as RADAR_MIN_SHARE, keeps_axis, measured_counts
from lib.fleet.fleet_tag_heatmap import names_shown
from lib.vocabulary import IN_PLACE

FILE = "radar.svg"
TITLE = "Practice shape by unit"
QUESTION = "Where is each unit strong across the practices, and where is it not yet measured?"
RINGS = (0.25, 0.5, 0.75, 1.0)

PAD = 16.0
COLUMNS = 3
CELL_W = 300.0
CELL_H = 270.0
RADIUS = 72.0
AXIS_LABEL = 12  # characters; the full title is the tooltip
UNIT_LABEL = 36
SUBTITLE = 44  # characters at 11px across one cell; the full line is the tooltip
LINE_H = 18.0
MARK = 7.0


class RadarAxis(TypedDict):
    row: str  # the maturity-model row id
    title: str
    share: float  # the run's repositories with a known level on it, over all (for display)


class RadarPoint(TypedDict):
    met: int
    measured: int
    repos: int


class RadarUnit(TypedDict):
    view: str
    label: str  # the name or key where the run allows it, else "<view> unit N"
    depth: int  # 0 for a root of its view
    parent: str | None  # the parent unit's label; None for a root
    repos: int  # every repository below the unit, at any depth
    points: list[RadarPoint | None]  # per kept axis; None: not measured in this unit


class RadarView(TypedDict):
    min_share: int  # percent
    axes: list[RadarAxis]
    dropped: list[RadarAxis]
    units: list[RadarUnit]
    repos: int  # the run's repositories
    in_place: int  # the run's repositories at the top level on at least one kept axis, each once


def _pct(share: float) -> str:
    return f"{round(share * 100)}%"


def _axes(data: Mapping[str, Any], min_share: int) -> tuple[list[RadarAxis], list[RadarAxis]]:
    repos = data.get("repos") or []
    known = measured_counts(data)
    kept: list[RadarAxis] = []
    dropped: list[RadarAxis] = []
    for row in data.get("model") or []:
        if row.get("scope") != "team":
            continue
        count = known.get(row["id"], 0)
        axis: RadarAxis = {"row": row["id"], "title": row["title"], "share": count / len(repos) if repos else 0.0}
        (kept if keeps_axis(count, len(repos), min_share) else dropped).append(axis)
    return kept, dropped


def _repos_in_place(data: Mapping[str, Any], axes: Sequence[RadarAxis]) -> int:
    """The run's repositories at the top level on at least one kept axis, each counted once."""
    rows = {a["row"] for a in axes}
    tops = {row["id"]: row.get("top_level") for row in data.get("model") or []}
    return sum(1 for repo in data.get("repos") or []
               if any(c["row"] in rows and c.get("status") == "level" and c.get("level") is not None
                      and c["level"] == tops.get(c["row"]) for c in repo.get("rows") or []))


def _depth(key: str, parents: Mapping[str, str | None]) -> int:
    depth, current = 0, parents.get(key)
    while current is not None and current in parents and depth < len(parents):
        depth, current = depth + 1, parents[current]
    return depth


def _label(unit: Mapping[str, Any], show: bool, view: str, index: int) -> str:
    if show:
        return str(unit.get("name") or unit["key"])
    return f"{view} unit {index}"


def radar_view(data: Mapping[str, Any], min_share: int = RADAR_MIN_SHARE) -> RadarView:
    """The axes kept and dropped and every unit's points (see the module docstring)."""
    axes, dropped = _axes(data, min_share)
    show = names_shown(data.get("run") or {})
    units: list[RadarUnit] = []
    for view in data.get("views") or []:
        drawn = [u for u in view.get("units") or [] if u.get("repos")]
        labels = {u["key"]: _label(u, show, view["view"], i) for i, u in enumerate(drawn, 1)}
        parents = {u["key"]: u.get("parent") for u in view.get("units") or []}
        for unit in drawn:
            counts = {lv["row"]: lv for lv in unit.get("levels") or []}
            points: list[RadarPoint | None] = []
            for axis in axes:
                lv = counts.get(axis["row"])
                points.append({"met": lv["met"], "measured": lv["measured"], "repos": lv["repos"]}
                              if lv and lv.get("measured") else None)
            parent = unit.get("parent")
            units.append({"view": view["view"], "label": labels[unit["key"]],
                          "depth": _depth(unit["key"], parents),
                          "parent": labels.get(parent) if parent is not None else None,
                          "repos": unit["repos"], "points": points})
    return {"min_share": min_share, "axes": axes, "dropped": dropped, "units": units,
            "repos": len(data.get("repos") or []), "in_place": _repos_in_place(data, axes)}


def _plural(n: int, word: str) -> str:
    if n == 1:
        return f"{n} {word}"
    return f"{n} {word[:-1]}ies" if word.endswith("y") else f"{n} {word}s"


def _nested(view: RadarView) -> bool:
    return any(u["parent"] is not None for u in view["units"])


def in_place(view: RadarView) -> str:
    """The section's opening line: the run's repositories at the top level on a drawn practice,
    each counted once however many units above it redraw it."""
    return (f"{IN_PLACE} {view['in_place']} of the run's {_plural(view['repos'], 'repository')} meet the "
            f"top level on at least one of the {_plural(len(view['axes']), 'practice')} drawn, each "
            f"counted once, over {_plural(len(view['units']), 'unit')}.")


def caption(view: RadarView) -> str:
    text = ("Each point is the share of the unit's measured repositories at the practice's top level "
            "(met/measured), with rings at 25, 50, 75 and 100%. One radar per unit; units are not ranked "
            "or overlaid. A hatched mark at the rim was not measured in that unit, never read as 0, "
            "and the outline breaks there.")
    if _nested(view):
        text += (" Units nest: a unit counts every repository below it, so a parent's radar redraws "
                 "its children's repositories, and a nested unit names the unit it sits in.")
    if view["dropped"]:
        names = ", ".join(a["title"] for a in view["dropped"])
        text += (f" Measured in under {view['min_share']}% of the run's repositories, so left "
                 f"out: {names}.")
    return text


def point_title(unit: RadarUnit, axis: RadarAxis, point: RadarPoint | None) -> str:
    """A point's tooltip: the unit, the practice and met/measured."""
    if point is None:
        return f"{unit['label']}, {axis['title']}: not measured"
    return (f"{unit['label']}, {axis['title']}: {point['met']}/{point['measured']} measured "
            f"at the top level ({_plural(point['repos'], 'repository')})")


def _xy(cx: float, cy: float, index: int, count: int, share: float) -> tuple[float, float]:
    angle = -math.pi / 2 + 2 * math.pi * index / count
    return cx + RADIUS * share * math.cos(angle), cy + RADIUS * share * math.sin(angle)


def _outline(coords: Sequence[tuple[float, float] | None]) -> tuple[str, bool]:
    """The outline's path data and whether it closes (every axis measured)."""
    n = len(coords)
    if n >= 3 and all(c is not None for c in coords):
        return "M" + " L".join(f"{c[0]:.1f},{c[1]:.1f}" for c in coords if c is not None) + " Z", True
    pairs = range(n) if n >= 3 else range(n - 1)
    parts = []
    for i in pairs:
        a, b = coords[i], coords[(i + 1) % n]
        if a is not None and b is not None:
            parts.append(f"M{a[0]:.1f},{a[1]:.1f} L{b[0]:.1f},{b[1]:.1f}")
    return " ".join(parts), False


def _subtitle(unit: RadarUnit) -> str:
    where = f"in {unit['parent']}, " if unit["parent"] is not None else ""
    return f"{unit['view']} view, {where}{_plural(unit['repos'], 'repository')}"


def _radar(unit: RadarUnit, axes: Sequence[RadarAxis], index: int, x0: float, y0: float) -> str:
    cx, cy = x0 + CELL_W / 2, y0 + 44 + RADIUS + 18
    n = len(axes)
    label = svg.truncate(unit["label"], UNIT_LABEL)
    subtitle = _subtitle(unit)
    parts = [f'<g data-radar="{index}">',
             svg.text(x0 + 8, y0 + 18, label, size=12.5, weight="600", cls="fg",
                      tooltip=unit["label"] if label != unit["label"] else None),
             svg.text(x0 + 8, y0 + 34, svg.truncate(subtitle, SUBTITLE), size=11, cls="muted",
                      tooltip=subtitle if len(subtitle) > SUBTITLE else None)]
    for ring in RINGS:
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{RADIUS * ring:.1f}" fill="none" class="s-rule" '
                     f'stroke-width="1" data-ring="{_pct(ring)}"/>')
        parts.append(svg.text(cx + 3, cy - RADIUS * ring + 10, _pct(ring), size=8.5, cls="muted"))
    coords: list[tuple[float, float] | None] = []
    for i, (axis, point) in enumerate(zip(axes, unit["points"])):
        ex, ey = _xy(cx, cy, i, n, 1.0)
        parts.append(f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{ex:.1f}" y2="{ey:.1f}" class="s-rule" '
                     'stroke-width="1"/>')
        lx, ly = _xy(cx, cy, i, n, 1.0 + 16 / RADIUS)
        anchor = "middle" if abs(lx - cx) < 4 else ("start" if lx > cx else "end")
        short = svg.truncate(axis["title"], AXIS_LABEL)
        parts.append(svg.text(lx, ly + 4, short, size=10, anchor=anchor, cls="fg",
                              tooltip=axis["title"] if short != axis["title"] else None))
        coords.append(None if point is None else _xy(cx, cy, i, n, point["met"] / point["measured"]))
    path, closed = _outline(coords)
    if path:
        fill = 'class="f-accent s-accent" fill-opacity="0.18"' if closed else 'fill="none" class="s-accent"'
        parts.append(f'<path d="{path}" {fill} stroke-width="2" data-outline="{"closed" if closed else "broken"}"/>')
    for i, (axis, point, xy) in enumerate(zip(axes, unit["points"], coords)):
        title = svg.esc(point_title(unit, axis, point))
        if xy is None:
            mx, my = _xy(cx, cy, i, n, 1.0)
            parts.append(f'<rect x="{mx - MARK / 2:.1f}" y="{my - MARK / 2:.1f}" width="{MARK:.1f}" '
                         f'height="{MARK:.1f}" fill="{svg.HATCH_FILL}" class="s-muted" stroke-width="1" '
                         f'data-row="{svg.esc(axis["row"])}" data-cell="not_measured"><title>{title}</title></rect>')
        else:
            parts.append(f'<circle cx="{xy[0]:.1f}" cy="{xy[1]:.1f}" r="3.5" class="f-accent" '
                         f'data-row="{svg.esc(axis["row"])}" data-cell="share"><title>{title}</title></circle>')
    parts.append("</g>")
    return "".join(parts)


def render(view: RadarView) -> str:
    """``radar.svg``: one radar per unit, small multiples."""
    columns = max(1, min(COLUMNS, len(view["units"])))
    width = max(680.0, 2 * PAD + CELL_W * columns)
    body = [svg.text(PAD, 28, TITLE, size=15, weight="600", cls="fg"),
            svg.text(PAD, 48, "One radar per unit, one axis per practice: met/measured at the top level",
                     size=12, cls="muted")]
    y = 60.0
    for index, unit in enumerate(view["units"]):
        row, col = divmod(index, columns)
        body.append(_radar(unit, view["axes"], index + 1, PAD + col * CELL_W, y + row * CELL_H))
    y += CELL_H * math.ceil(len(view["units"]) / columns) + 8
    parts, used = svg.hatched_note(PAD, y, ["Not measured in that unit: hatched at the rim, never 0"], size=12)
    body.append(f'<g data-mark="unknown">{"".join(parts)}</g>')
    y += used
    for line in wrap(caption(view), int((width - 2 * PAD) / svg.CHAR_W)):
        y += LINE_H
        body.append(svg.text(PAD, y, line, size=12, cls="fg"))
    desc = f"{in_place(view)} {caption(view)}"
    return svg.document(width, y + PAD, TITLE, desc, body, defs=svg.THEMED_HATCH)


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _column(unit: RadarUnit) -> str:
    return _cell(unit["label"] if unit["parent"] is None else f"{unit['label']} (in {unit['parent']})")


def table(view: RadarView) -> str:
    """The radar's numbers as a Markdown table: practice by unit, ``met/measured``."""
    lines = [f"Met/measured by practice for {_plural(len(view['units']), 'unit')}, the radar's numbers:", "",
             "| Practice | " + " | ".join(_column(u) for u in view["units"]) + " |",
             "|---|" + "---|" * len(view["units"])]
    for i, axis in enumerate(view["axes"]):
        cells = [f"{p['met']}/{p['measured']}" if (p := u["points"][i]) is not None else "not measured"
                 for u in view["units"]]
        lines.append(f"| {_cell(axis['title'])} | " + " | ".join(cells) + " |")
    return "\n".join(lines)
