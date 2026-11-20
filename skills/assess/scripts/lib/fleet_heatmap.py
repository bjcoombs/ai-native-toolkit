"""Leadership heat map SVG from one fleet run's ``groups.jsonl`` (issue #570).

Groups are rows, maturity model rows are columns, and each cell is the whole level the
group reached on that row. Reads only what ``lib/fleet_view.py`` loads: no
rescan, no model.

Encoding:

- Fill: the ColorBrewer OrRd 9-class sequential ramp, hardcoded (no matplotlib,
  whose colormaps can shift between versions), so output is byte-identical
  across machines. Pale is level 0 (nothing reached), darker is a higher level
  reached. With a maturity model file a level is placed on the ramp by its share of its
  row's top level (``level * 8 / top``, rounded half up), so a row's top level
  is always the darkest step; without one the level indexes the ramp directly
  (capped at the last step). The legend states which.
- ``unknown``: white with grey diagonal hatching (the ``UNMEASURED_FILL`` idea
  in ``doc-graph-svg.py``), never a ramp colour, so it cannot read as level 0.
  The cell prints ``?`` and a key letter; the key under the grid names the
  missing signals for each distinct set, in visible text (an SVG shown as an
  image has no tooltips; ``<title>`` repeats it on hover).
- Colour is never the only channel: every known cell prints its level, and the
  text colour per ramp step comes from a fixed table (dark on pale steps, white
  on dark ones).
- Change against an earlier run (``fleet_view.compare_levels``) is text under
  the level: ``+1``, ``-1``, ``=``, ``new``, ``was 2`` or ``was ?``. Across
  maturity-model versions no change is drawn and a note says why.

The canvas has its own white background, so it reads the same on light and
dark pages. Text is escaped with ``html.escape``; labels are sized and
truncated by character count (no font metrics), and every number is formatted
with a fixed precision, so the same run renders byte-identical files.
"""
from __future__ import annotations

import html
import math
import textwrap
from collections.abc import Sequence
from typing import TypedDict

from lib import fleet_svg
from lib.fleet_svg import (  # the level ramp is shared with the layer bars
    CHAR_W,
    ORRD_9,
    STEP_TEXT,
    TEXT_DARK,
    hatch_pattern,
    ramp_step,
    truncate,
)

from lib.fleet_groups import GroupRow, RowLevel
from lib.fleet_view import (
    STATUS_LEVEL,
    Change,
    Comparison,
    cell,
    level_columns,
    run_levels_version,
)
from lib.levels import MaturityModel

UNKNOWN_FILL = "url(#fleet-unknown)"
_UNKNOWN_PATTERN = hatch_pattern("fleet-unknown", "#ffffff", "#8c8c8c")
MUTED = "#5f6368"
GRID_STROKE = "#ffffff"
FRAME_STROKE = "#bdbdbd"

PAD = 16.0
CELL_W = 64.0
CELL_H = 40.0
LINE_H = 18.0
SWATCH = 16.0
MAX_ROW_LABEL = 40
MAX_COL_LABEL = 48
KEY_WRAP = 96
MIN_WIDTH = 640.0
HEAD_H = 58.0  # title and subtitle

SVG_TITLE = "Fleet maturity model levels"


class Column(TypedDict):
    id: str
    title: str
    top: int | None  # the row's top level; None without a maturity model file


def columns_for(groups: Sequence[GroupRow], maturity_model: MaturityModel | None) -> list[Column]:
    """Columns in maturity model order; titles and top levels from the maturity model file when given."""
    ids = level_columns(groups)
    if maturity_model is None:
        return [{"id": row_id, "title": row_id, "top": None} for row_id in ids]
    by_id = {row["id"]: row for row in maturity_model["rows"]}
    return [{"id": row_id, "title": by_id[row_id]["title"],
             "top": max(level["level"] for level in by_id[row_id]["levels"])}
            for row_id in ids]


def key_label(index: int) -> str:
    """a, b, ... z, aa, ab, ...: a short, stable label for the nth unknown-key entry."""
    label = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        label = chr(ord("a") + rem) + label
    return label


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def change_text(change: Change) -> str:
    if change["kind"] == "new":
        return "new"
    if change["kind"] == "was":
        return f"was {'?' if change['then'] is None else change['then']}"
    delta = change["delta"] or 0
    return "=" if delta == 0 else f"{delta:+d}"


def _missing_text(entry: RowLevel) -> str:
    if entry["missing_signals"]:
        return "missing " + ", ".join(entry["missing_signals"])
    return "no evidence (no repository scanned)"


def unknown_keys(groups: Sequence[GroupRow], columns: Sequence[Column]) -> dict[str, str]:
    """Missing-signals text -> key label, numbered in grid reading order."""
    keys: dict[str, str] = {}
    for group in groups:
        for column in columns:
            entry = cell(group, column["id"])
            if entry["status"] != STATUS_LEVEL:
                keys.setdefault(_missing_text(entry), key_label(len(keys)))
    return keys


def _text(x: float, y: float, content: str, *, size: int = 12, fill: str = TEXT_DARK,
          anchor: str = "start", weight: str | None = None, extra: str = "",
          tooltip: str | None = None) -> str:
    return fleet_svg.text(x, y, content, size=size, fill=fill, anchor=anchor, weight=weight,
                          extra=extra, tooltip=tooltip)


def _legend_entries(groups: Sequence[GroupRow], columns: Sequence[Column]) -> list[tuple[int, str]]:
    """(ramp step, label) per step the map can use, in ramp order."""
    labels: dict[int, list[str]] = {}
    if all(column["top"] is not None for column in columns):
        for top in sorted({column["top"] or 0 for column in columns}):
            for level in range(top + 1):
                labels.setdefault(ramp_step(level, top), []).append(f"{level} of {top}")
    else:
        seen = [cell(g, c["id"])["level"] for g in groups for c in columns]
        highest = max([level for level in seen if level is not None], default=0)
        for level in range(highest + 1):
            labels.setdefault(ramp_step(level, None), []).append(f"level {level}")
    entries = []
    for step in sorted(labels):
        text = "0 (none reached)" if step == 0 else " / ".join(dict.fromkeys(labels[step]))
        entries.append((step, text))
    return entries


def _cell_parts(x: float, y: float, group: GroupRow, column: Column, keys: dict[str, str],
                change: Change | None) -> list[str]:
    entry = cell(group, column["id"])
    cx = x + CELL_W / 2
    known = entry["status"] == STATUS_LEVEL and entry["level"] is not None
    if known:
        level = entry["level"] or 0
        step = ramp_step(level, column["top"])
        fill, ink = ORRD_9[step], STEP_TEXT[step]
        of_top = "" if column["top"] is None else f" of {column['top']}"
        label, tip = str(level), f"level {level}{of_top}"
    else:
        missing = _missing_text(entry)
        fill, ink = UNKNOWN_FILL, TEXT_DARK
        label = f"? {keys[missing]}"
        tip = f"unknown: {missing}"
        if entry["unknown_repos"]:
            count = len(entry["unknown_repos"])
            tip += f"; {count} {'repository' if count == 1 else 'repositories'} without a level"
    if change is not None:
        tip += f"; change: {change_text(change)}"
    title = html.escape(f"{group['group']} · {column['title']}: {tip}", quote=False)
    parts = [f'<rect x="{x:.1f}" y="{y:.1f}" width="{CELL_W:.1f}" height="{CELL_H:.1f}" '
             f'fill="{fill}" stroke="{GRID_STROKE}" stroke-width="1"><title>{title}</title></rect>']
    text_y = y + (16.0 if change is not None else CELL_H / 2 + 5)
    if not known:
        # A white plate keeps the label clear of the hatch lines.
        parts.append(f'<rect x="{cx - 18:.1f}" y="{text_y - 12:.1f}" width="36.0" height="16.0" '
                     f'fill="#ffffff"/>')
    parts.append(_text(cx, text_y, label, size=14, fill=ink, anchor="middle", weight="bold"))
    if change is not None:
        if not known:
            parts.append(f'<rect x="{cx - 22:.1f}" y="{y + 23:.1f}" width="44.0" height="13.0" '
                         f'fill="#ffffff"/>')
        parts.append(_text(cx, y + 33, change_text(change), size=10, fill=ink, anchor="middle"))
    return parts


def render_heatmap(groups: Sequence[GroupRow], columns: Sequence[Column],
                   comparison: Comparison | None = None) -> str:
    """The heat map as an SVG document string; the same inputs give the same bytes."""
    version = run_levels_version(groups)
    keys = unknown_keys(groups, columns)
    row_labels = [truncate(g["group"], MAX_ROW_LABEL, keep_tail=True) for g in groups]
    col_labels = [truncate(c["title"], MAX_COL_LABEL) for c in columns]
    longest_col = max(len(label) for label in col_labels)
    header_h = math.ceil(longest_col * CHAR_W * math.sqrt(0.5)) + 12.0
    label_w = max(len(label) for label in row_labels) * CHAR_W + 12.0
    grid_x = PAD + label_w
    grid_y = PAD + HEAD_H + header_h
    grid_w = len(columns) * CELL_W
    grid_h = len(groups) * CELL_H
    show_change = comparison is not None and comparison["comparable"]

    body: list[str] = []
    subtitle = (f"maturity model {version} · {_count(len(groups), 'group')} · "
                f"{_count(len(columns), 'maturity model row')} · darker = higher level reached")
    body.append(_text(PAD, PAD + 18, SVG_TITLE, size=18, weight="bold"))
    body.append(_text(PAD, PAD + 40, subtitle, fill=MUTED))
    for index, label in enumerate(col_labels):
        cx = grid_x + index * CELL_W + CELL_W / 2
        cy = grid_y - 8
        full = columns[index]["title"]
        body.append(_text(cx, cy, label, extra=f' transform="rotate(-45 {cx:.1f} {cy:.1f})"',
                          tooltip=full if label != full else None))
    for row_index, group in enumerate(groups):
        y = grid_y + row_index * CELL_H
        cut = row_labels[row_index] != group["group"]
        body.append(_text(grid_x - 8, y + CELL_H / 2 + 4, row_labels[row_index], anchor="end",
                          tooltip=group["group"] if cut else None))
        for col_index, column in enumerate(columns):
            change = (comparison["changes"][group["group"]][column["id"]]
                      if comparison is not None and show_change else None)
            body.extend(_cell_parts(grid_x + col_index * CELL_W, y, group, column, keys, change))
    body.append(f'<rect x="{grid_x:.1f}" y="{grid_y:.1f}" width="{grid_w:.1f}" '
                f'height="{grid_h:.1f}" fill="none" stroke="{FRAME_STROKE}" stroke-width="1"/>')

    y = grid_y + grid_h + 28
    widest = len(subtitle)
    notes: list[str] = []
    if comparison is not None:
        if comparison["note"]:
            notes.append(comparison["note"])
        else:
            notes.append("change since the earlier run is shown under each level: +n / -n, "
                         "= unchanged, new, was n / was ? (the level was known / unknown)")
        if comparison["removed_groups"]:
            notes.append("groups in the earlier run only: " + ", ".join(comparison["removed_groups"]))
    for note in notes:
        for line in textwrap.wrap(note, KEY_WRAP):
            body.append(_text(PAD, y, line, fill=MUTED))
            widest = max(widest, len(line))
            y += LINE_H
    if notes:
        y += 10

    scale = ("level as a share of the row's top level" if all(c["top"] is not None for c in columns)
             else "whole level (no maturity model file: not scaled to each row's top level)")
    body.append(_text(PAD, y, f"Legend: {scale}", weight="bold"))
    y += 10
    entries: list[tuple[str, str]] = [(ORRD_9[step], label)
                                      for step, label in _legend_entries(groups, columns)]
    entries.append((UNKNOWN_FILL, "unknown: not measured, never level 0 (? and a key letter)"))
    for fill, label in entries:
        body.append(f'<rect x="{PAD:.1f}" y="{y:.1f}" width="{SWATCH:.1f}" height="{SWATCH:.1f}" '
                    f'fill="{fill}" stroke="{FRAME_STROKE}" stroke-width="1"/>')
        body.append(_text(PAD + SWATCH + 8, y + 12, label))
        widest = max(widest, len(label) + 4)
        y += LINE_H + 4

    if keys:
        y += 18
        body.append(_text(PAD, y, "Unknown cells: the signal that was missing", weight="bold"))
        y += LINE_H + 2
        for text, label in keys.items():
            for index, line in enumerate(textwrap.wrap(text, KEY_WRAP)):
                if index == 0:
                    body.append(_text(PAD, y, label, weight="bold"))
                body.append(_text(PAD + 28, y, line))
                widest = max(widest, len(line) + 4)
                y += LINE_H

    width = max(grid_x + grid_w + longest_col * CHAR_W * math.sqrt(0.5) + PAD,
                PAD * 2 + widest * CHAR_W, MIN_WIDTH)
    width = float(math.ceil(width))
    height = float(math.ceil(y + PAD))
    desc = ("Heat map of fleet groups (rows) against maturity model rows (columns); each cell is the "
            "whole level reached, darker is higher; hatched cells are unknown, not level 0.")
    style = ('<style>text { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", '
             'Helvetica, Arial, sans-serif; }</style>')
    return fleet_svg.document(width, height, SVG_TITLE, desc, body, style=style,
                              defs=_UNKNOWN_PATTERN, background="#ffffff")
