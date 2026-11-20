"""Group trend lines across the dated fleet run history (issue #676, parent #672).

One line per group: its mean maturity in each comparable run, oldest on the
left, this run on the right. ``--compare`` sees two runs; this sees the history.

**Mean maturity.** A group's value in a run is ``lib.fleet_maturity.mean_share``
over its ``team`` rows in ``groups.jsonl``: each known row level as a share of the
row's ``top_level``, the unit and row set of the slope chart (#675) and the
quadrant (#684). An entry written before ``groups.jsonl`` carried ``top_level``
takes each row's top level from this run's maturity model when the entry used the
same version (same version, same rows). Otherwise a run with any known team row
lacking a top level falls back to the plain mean of its known team-row levels: the run is marked unscaled, its points are drawn hollow
and the caption says they are plain means, and a line breaks wherever the unit
changes, so a share and a plain mean are never joined. A group with no team row
at a level, or absent from a run, has no value there: its line breaks and the
gap is named on a hatched note, never drawn at zero.

**Comparable runs.** ``history_runs`` reads the ``<date>[.<n>]`` entries under
the history ``<org>`` directory (``lib/fleet_history.py``): ``run.json`` and
``groups.jsonl``, and ``repos.jsonl`` only to choose between same-day runs. An entry counts when its stamp is at this toolkit's stamp
version, matches this run on window, scan selection, output mode and path
hashing, and records a maturity-model version; an unreadable or malformed
entry is passed over, never an error. One run per scan date: the entry over
this run's repository set when there is one, else the highest suffix. Dates
on or after this run's are dropped, since this run (from memory, not the
history, so a failed history write still shows it) is the last point. At most
``MAX_RUNS`` of the newest are drawn.

**Maturity-model version.** A change of version between two runs breaks every
line there (``--compare`` refuses to compare across versions) and a dashed rule
marks it; so does a change between plain-mean and share runs. Each run's point is still drawn, so a one-run segment is a dot.

``layout`` is shared by the renderer and its tests: end labels are spread by
``fleet_svg.spread_vertical`` so they never overlap. Deterministic: groups and
runs sorted, fixed number formatting, no clock read.
"""
from __future__ import annotations

import json
import math
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

from lib import fleet_svg as svg
from lib.fleet_groups import GROUPS_FILE, GroupRow, RubricRowLevel
from lib.fleet_history import dated_entries, entry_repos
from lib.fleet_maturity import SCOPE, mean_share
from lib.fleet_stamp import FLEET_STAMP_VERSION, RUN_FILE, FleetStamp
from lib.fleet_svg import THEMED_HATCH, LinearScale
from lib.fleet_view import load_groups
from lib.rubric import Rubric

TITLE = "Group maturity over time"
FILE = "trend.svg"
MIN_RUNS = 3
MAX_RUNS = 12
WIDTH = 760.0
PAD = 16.0
PLOT_L, PLOT_R = 56.0, 520.0
PLOT_T, PLOT_H = 104.0, 260.0
LABEL_X = PLOT_R + 18
LABEL_GAP = 16.0  # minimum distance between end-label baselines
LABEL_SIZE = 12.0
NAME_CHARS = 22  # group name budget in an end label
TICK_W = 78.0  # room one date tick label needs
WRAP = 104
LINE_H = 16.0
AXIS_LABEL = "Mean share of top level"
MIXED_NOTE = "(hollow: plain mean level)"
MATCH_KEYS = ("window", "scans", "counts_only", "paths_hashed")

# Series colours (light, dark), assigned to groups in sorted order; past the eighth
# group the colours repeat with a dashed line.
SERIES: tuple[tuple[str, str], ...] = (
    ("#1f6f6b", "#5fb8b1"), ("#b7791f", "#e0a84a"), ("#6b4fbb", "#a993f0"),
    ("#b33a2e", "#ec7a6c"), ("#2f6db5", "#7fb0ec"), ("#4f7f22", "#9ccc6b"),
    ("#a8458a", "#e08cc8"), ("#5d6763", "#b5bfba"),
)
SERIES_ROLES = {f"series{i}": colours for i, colours in enumerate(SERIES)}


class TrendRun(TypedDict):
    label: str  # the scan date
    levels_version: str  # the maturity-model version the run used
    means: dict[str, float | None]  # group -> mean share of top level; None: no team row at a level
    scaled: bool  # False: an entry without top levels, so ``means`` are plain mean levels


# --- reading the history ------------------------------------------------------


def _known(group: GroupRow) -> list[RubricRowLevel]:
    """The group's team rows with a whole level."""
    return [row for row in group["rubric"] if row["scope"] == SCOPE and row["status"] == "level"
            and type(row["level"]) is int]


def scaled(groups: Sequence[GroupRow]) -> bool:
    """Every known team row in the run carries a positive ``top_level`` (#684)."""
    return all(type(top := row.get("top_level")) is int and top > 0
               for group in groups for row in _known(group))


def mean_level(group: GroupRow) -> float | None:
    """The plain mean of the group's known team-row levels (the fallback for an entry
    without top levels); None when none is known."""
    levels = [row["level"] or 0 for row in _known(group)]
    return sum(levels) / len(levels) if levels else None


def group_mean(group: GroupRow) -> float | None:
    """The group's mean share of top level over its known team rows; None when none is."""
    return mean_share((row["level"], row.get("top_level")) for row in _known(group))


def with_top_levels(groups: Sequence[GroupRow], levels: Rubric | None,
                    version: str) -> list[GroupRow]:
    """``groups`` with each row lacking ``top_level`` given the row's level count from
    ``levels`` when the entry used that maturity-model version; else unchanged."""
    if levels is None or levels["version"] != version:
        return list(groups)
    tops = {row["id"]: len(row["levels"]) for row in levels["rows"]}
    filled: list[GroupRow] = []
    for group in groups:
        rows: list[RubricRowLevel] = []
        for row in group["rubric"]:
            if "top_level" not in row and row["id"] in tops:
                row = {**row, "top_level": tops[row["id"]]}
            rows.append(row)
        filled.append({**group, "rubric": rows})
    return filled


def trend_run(stamp: Mapping[str, Any], groups: Sequence[GroupRow],
              levels: Rubric | None = None) -> TrendRun | None:
    """One run's point per group; None when the run had no maturity model. ``levels`` (this
    run's maturity model) supplies top levels an entry of the same version did not record."""
    version = stamp.get("rubric_version")
    if not isinstance(version, str) or not isinstance(stamp.get("scan_date"), str):
        return None
    groups = with_top_levels(groups, levels, version)
    unit = scaled(groups)
    mean = group_mean if unit else mean_level
    return {"label": stamp["scan_date"], "levels_version": version, "scaled": unit,
            "means": {g["group"]: mean(g) for g in sorted(groups, key=lambda g: g["group"])}}


def _comparable(stamp: Any, current: FleetStamp) -> bool:
    return (isinstance(stamp, dict) and stamp.get("fleet_stamp_version") == FLEET_STAMP_VERSION
            and all(stamp.get(key) == current.get(key) for key in MATCH_KEYS))


def _read_entry(entry: Path, current: FleetStamp, levels: Rubric | None) -> TrendRun | None:
    """The entry as a trend point; None when it is unreadable or not comparable."""
    try:
        stamp: Any = json.loads((entry / RUN_FILE).read_text(encoding="utf-8"))
        if not _comparable(stamp, current):
            return None
        return trend_run(stamp, load_groups(entry / GROUPS_FILE), levels)
    except (OSError, ValueError, KeyError, TypeError):  # FleetViewError is a ValueError
        return None


def history_runs(org_dir: Path, current: FleetStamp, repos: frozenset[str],
                 levels: Rubric | None = None) -> list[TrendRun]:
    """Earlier comparable runs under ``org_dir``, one per scan date before this run's, oldest
    first; ``levels`` is this run's maturity model (``trend_run``)."""
    by_date: dict[str, list[tuple[int, Path, TrendRun]]] = {}
    for (date, suffix), entry in dated_entries(org_dir):
        if date >= current["scan_date"]:
            continue
        run = _read_entry(entry, current, levels)
        if run is not None and run["label"] == date:
            by_date.setdefault(date, []).append((suffix, entry, run))
    chosen = []
    for date in sorted(by_date):
        options = by_date[date]  # by suffix; repos.jsonl is read only to choose between same-day runs
        if len(options) > 1:
            options = sorted(options, key=lambda o: (entry_repos(o[1]) == repos, o[0]))
        chosen.append(options[-1][2])
    return chosen


def series_runs(earlier: Sequence[TrendRun], current: TrendRun) -> list[TrendRun]:
    """The runs to draw: the newest ``MAX_RUNS``, this run last."""
    return [*earlier, current][-MAX_RUNS:]


# --- layout -------------------------------------------------------------------


@dataclass(frozen=True)
class EndLabel:
    group: str
    name: str  # possibly truncated
    value: float
    delta: float | None  # over the last unbroken segment; None for a one-run segment
    scaled: bool  # the segment is a share of top level (two decimals), not a plain mean
    point: tuple[float, float]
    y: float  # baseline after spacing


@dataclass(frozen=True)
class Layout:
    groups: list[str]
    x: list[float]
    y: LinearScale
    y_ticks: list[float]  # value-axis gridlines
    labels: list[EndLabel]
    ticks: list[int]  # run indices given a date label
    breaks: list[int]  # run indices where the maturity-model version or the unit changes


def _break_before(runs: Sequence[TrendRun], i: int) -> bool:
    """Run ``i`` changes the maturity-model version or the unit (share against plain mean)."""
    return (runs[i]["levels_version"] != runs[i - 1]["levels_version"]
            or runs[i]["scaled"] != runs[i - 1]["scaled"])


def _segments(runs: Sequence[TrendRun], group: str) -> list[list[int]]:
    """Runs where ``group`` has a value, split at gaps, version changes and unit changes."""
    segments: list[list[int]] = []
    for i, run in enumerate(runs):
        if run["means"].get(group) is None:
            continue
        joined = segments and segments[-1][-1] == i - 1 and not _break_before(runs, i)
        if joined:
            segments[-1].append(i)
        else:
            segments.append([i])
    return segments


def _tick_indices(count: int, spacing: float) -> list[int]:
    """Run indices whose date fits, counted back from the last run."""
    step = max(1, math.ceil(TICK_W / spacing)) if spacing > 0 else 1
    return sorted(range(count - 1, -1, -step))


def layout(runs: Sequence[TrendRun]) -> Layout:
    groups = sorted({g for run in runs for g in run["means"]})
    values = [v for run in runs for v in run["means"].values() if v is not None]
    if all(run["scaled"] for run in runs):
        y_ticks = [0.0, 0.25, 0.5, 0.75, 1.0]
    else:  # plain means of older entries run past 1: whole-level gridlines
        y_ticks = [float(t) for t in range(max(1, math.ceil(max(values, default=0.0))) + 1)]
    spacing = (PLOT_R - PLOT_L) / max(len(runs) - 1, 1)
    xs = [PLOT_L + spacing * i for i in range(len(runs))]
    y = LinearScale(0.0, y_ticks[-1], PLOT_T + PLOT_H, PLOT_T)
    ends: list[tuple[str, float, float | None, bool, tuple[float, float]]] = []
    for group in groups:
        segments = _segments(runs, group)
        if not segments:
            continue
        last = segments[-1]
        value = runs[last[-1]]["means"][group]
        first = runs[last[0]]["means"][group]
        assert value is not None and first is not None
        unit = runs[last[-1]]["scaled"]
        delta = round(value - first, 2 if unit else 1) if len(last) > 1 else None
        ends.append((group, value, delta, unit, (xs[last[-1]], y(value))))
    spread = svg.spread_vertical([point[1] + 4 for *_, point in ends], LABEL_GAP)
    labels = [EndLabel(group, svg.truncate(group, NAME_CHARS), value, delta, unit, point, ly)
              for (group, value, delta, unit, point), ly in zip(ends, spread)]
    breaks = [i for i in range(1, len(runs)) if _break_before(runs, i)]
    return Layout(groups, xs, y, y_ticks, labels, _tick_indices(len(runs), spacing), breaks)


# --- render -------------------------------------------------------------------


def _delta_text(delta: float | None, scaled: bool = False) -> str:
    if delta is None:
        return ""
    places = 2 if scaled else 1
    return f"{0:.{places}f}" if delta == 0 else f"{delta:+.{places}f}"


def _gaps(runs: Sequence[TrendRun], groups: Sequence[str]) -> list[str]:
    notes = []
    for group in groups:
        absent = [r["label"] for r in runs if group not in r["means"]]
        unknown = [r["label"] for r in runs if group in r["means"] and r["means"][group] is None]
        parts = ([f"no level on {', '.join(unknown)}"] if unknown else []) + \
                ([f"not in the run on {', '.join(absent)}"] if absent else [])
        if parts:
            notes.append(f"{group} ({'; '.join(parts)})")
    return notes


def _versions_line(runs: Sequence[TrendRun]) -> str:
    spans: list[tuple[str, int]] = []
    for run in runs:
        if spans and spans[-1][0] == run["levels_version"]:
            spans[-1] = (spans[-1][0], spans[-1][1] + 1)
        else:
            spans.append((run["levels_version"], 1))
    if len(spans) == 1:
        return f"Maturity model {spans[0][0]} throughout"
    shown = ", ".join(f"{v} ({n} run{'s' if n > 1 else ''})" for v, n in spans)
    return f"Maturity model {shown}; each line breaks where the version changes"


def _series_class(index: int) -> tuple[str, str]:
    """The colour role and any dash for the ``index``-th group."""
    dash = ' stroke-dasharray="6 3"' if index >= len(SERIES) else ""
    return f"series{index % len(SERIES)}", dash


def render_trend(runs: Sequence[TrendRun]) -> str:
    """The trend chart as an SVG document; the same runs give the same bytes."""
    plan = layout(runs)
    bottom = PLOT_T + PLOT_H
    body = [svg.text(PAD, PAD + 18, TITLE, size=18, weight="bold", cls="fg"),
            svg.text(PAD, PAD + 40, f"{len(runs)} dated runs, {runs[0]['label']} to {runs[-1]['label']} "
                     "· mean share of top level per group (team rows with a level)", cls="muted"),
            svg.text(PAD, PAD + 58, svg.truncate(_versions_line(runs), WRAP), size=11.5, cls="muted")]
    for tick in plan.y_ticks:
        ty = plan.y(tick)
        body.append(f'<line x1="{PLOT_L:.1f}" y1="{ty:.1f}" x2="{PLOT_R:.1f}" y2="{ty:.1f}" '
                    f'class="s-rule" stroke-width="0.6"/>')
        body.append(svg.text(PLOT_L - 10, ty + 4, svg.tick_label(tick), size=11, anchor="end",
                             cls="muted"))
    for i in plan.ticks:
        body.append(svg.text(plan.x[i], bottom + 18, runs[i]["label"], size=11, anchor="middle",
                             cls="muted"))
    taken: list[svg.Box] = []
    for i in plan.breaks:
        bx = (plan.x[i - 1] + plan.x[i]) / 2
        body.append(f'<line x1="{bx:.1f}" y1="{PLOT_T - 14:.1f}" x2="{bx:.1f}" y2="{bottom:.1f}" '
                    f'class="s-muted" stroke-dasharray="4 4"/>')
        caption = (f"maturity model {runs[i]['levels_version']}"
                   if runs[i]["levels_version"] != runs[i - 1]["levels_version"]
                   else "share of top level" if runs[i]["scaled"] else "plain mean level")
        box = svg.text_box(bx + 4, PLOT_T - 18, caption, 11)
        if box.x + box.w <= WIDTH - PAD and not any(svg.overlaps(box, other) for other in taken):
            taken.append(box)
            body.append(svg.text(bx + 4, PLOT_T - 18, caption, size=11, cls="muted"))
    for index, group in enumerate(plan.groups):
        role, dash = _series_class(index)
        for segment in _segments(runs, group):
            points = [(plan.x[i], plan.y(runs[i]["means"][group] or 0.0)) for i in segment]
            if len(points) > 1:
                coords = " ".join(f"{px:.1f},{py:.1f}" for px, py in points)
                body.append(f'<polyline points="{coords}" fill="none" class="s-{role}" '
                            f'stroke-width="2"{dash}/>')
            for (px, py), i in zip(points, segment):
                unit = "" if runs[i]["scaled"] else " (plain mean level)"
                tip = f"{group}: {runs[i]['means'][group]:.2f}{unit} on {runs[i]['label']}"
                fill = (f'class="f-{role}"' if runs[i]["scaled"]
                        else f'class="f-bg s-{role}" stroke-width="1.5"')
                body.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" {fill}>'
                            f'<title>{svg.esc(tip)}</title></circle>')
    for label in plan.labels:
        role, _ = _series_class(plan.groups.index(label.group))
        px, py = label.point
        body.append(f'<line x1="{px + 5:.1f}" y1="{py:.1f}" x2="{LABEL_X - 4:.1f}" y2="{label.y - 4:.1f}" '
                    f'class="s-rule" stroke-width="1"/>')
        name = f"{label.name} {label.value:.2f}" if label.scaled else f"{label.name} {label.value:.1f}"
        body.append(svg.text(LABEL_X, label.y, name, size=LABEL_SIZE, cls=role, weight="600",
                             tooltip=label.group))
        delta = _delta_text(label.delta, label.scaled)
        if delta:
            tone = "accent" if delta.startswith("+") else "bad" if delta.startswith("-") else "muted"
            body.append(svg.text(LABEL_X + svg.text_width(name, LABEL_SIZE) + 6, label.y, delta,
                                 size=11, cls=tone))
    body.append(svg.text((PLOT_L + PLOT_R) / 2, bottom + 38, "Fleet run (scan date)", anchor="middle",
                         cls="muted"))
    mid = PLOT_T + PLOT_H / 2
    mixed = not all(run["scaled"] for run in runs)
    for ax, line, size in ((18.0, AXIS_LABEL, 12.0), *([(32.0, MIXED_NOTE, 10.5)] if mixed else [])):
        body.append(svg.text(ax, mid, line, size=size, anchor="middle", cls="muted",
                             extra=f' transform="rotate(-90 {ax:.0f} {mid:.1f})"'))
    cursor = max(bottom + 60, max((label.y for label in plan.labels), default=0.0) + 20)
    body.append(svg.text(PLOT_L, cursor, "End label: group, its mean in its last run, and the change "
                         "over its last unbroken stretch", size=11.5, cls="muted"))
    cursor += LINE_H
    if mixed:
        old = [run["label"] for run in runs if not run["scaled"]]
        for line in textwrap.wrap(
                "Hollow points: runs kept before the history recorded each row's top level, "
                "plotted as the plain mean of the team-row levels, not a share, and never joined "
                "to a share: " + ", ".join(old), WRAP):
            body.append(svg.text(PLOT_L, cursor, line, size=11.5, cls="muted"))
            cursor += LINE_H
    gaps = _gaps(runs, plan.groups)
    if gaps:
        lines = textwrap.wrap("Gaps in a line, unknown is not zero: " + ", ".join(gaps), WRAP)
        cursor += 6
        parts, height = svg.hatched_note(PLOT_L, cursor, lines)
        body.extend(parts)
        cursor += height
    desc = ("Line chart of each fleet group's mean share of top level across dated fleet runs, "
            "oldest on the left; a line breaks where the maturity-model version or the unit changes, "
            "older runs without top levels are hollow plain means, and runs where a group has no "
            "level are listed on a hatched note, not drawn at zero.")
    return svg.document(WIDTH, float(int(cursor + PAD)), TITLE, desc, body,
                        style=svg.theme_style(SERIES_ROLES), defs=THEMED_HATCH)
