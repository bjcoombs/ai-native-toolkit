"""Group trend lines across the dated fleet run history (issue #676, parent #672).

One line per group: its lowest Step in each comparable run, oldest on the left, this run on
the right. ``--compare`` sees two runs; this sees the history.

**Lowest Step (#806).** A group's point in a run is ``lib.fleet.fleet_maturity.group_steps``:
the lowest Step a measured repository of the group holds, with "N of M measured" (the #772
roll-up, option A). No mean is taken across repositories. The group's repositories come from
the run's ``repos.jsonl`` and their Steps from its ``level.json`` (#941). An entry kept before
``level.json`` was stored has no Steps: it is marked ``stepped: false`` and not drawn, and the
caption names it; the trend counts only stepped runs toward ``MIN_RUNS``. A group with no
measured repository in a run, or absent from it, has no value there: its line breaks and the
gap is named on a hatched note, never drawn at Step 0.

**Comparable runs.** ``history_runs`` reads the ``<date>[.<n>]`` entries under the history
``<org>`` directory (``lib/fleet/fleet_history.py``): ``run.json``, ``repos.jsonl`` and
``level.json``. An entry counts when its stamp is at this toolkit's stamp version, matches
this run on window, scan selection, output mode and path hashing, and records a
maturity-model version; an unreadable or malformed entry is passed over, never an error. One
run per scan date: the entry over this run's repository set when there is one, else the
highest suffix. Dates on or after this run's are dropped, since this run (from memory, not
the history, so a failed history write still shows it) is the last point. At most
``MAX_RUNS`` of the newest are drawn.

**Maturity-model version.** A change of version between two runs, or of the rules hash
(``rules_sha256``, #952) when both runs recorded one, breaks every line there (``--compare``
refuses to compare across versions) and a dashed rule marks it. Each run's point is still
drawn, so a one-run segment is a dot. Each point carries its tooltip values as
``data-group``, ``data-step``, ``data-measured``, ``data-repos`` and ``data-run`` (#1360).

``layout`` is shared by the renderer and its tests: end labels are spread by
``fleet_svg.spread_vertical`` so they never overlap. Deterministic: groups and runs sorted,
fixed number formatting, no clock read.

Group trend lines across the dated run history (#676, lowest Step since #806).
`history_runs(org_dir, stamp, repos)` reads each `<date>[.<n>]` entry
(`fleet_history.one_per_date`; `run.json`, `repos.jsonl` and `level.json`) dated before this
run whose stamp matches on window, scans, output mode, path hashing and `--fleet-config` hash
and records a maturity-model version; unreadable entries are passed over. One run per date:
the entry over this run's repository set, else the highest suffix. `trend_run(stamp, members,
steps)` is one run's point per group, `fleet_maturity.group_steps` over `(group, repository)`
pairs and each repository's Step: the lowest Step with "N of M measured", never a mean. A
run with no stored Steps (`steps` None: an entry kept before `level.json`, #941) is marked
`stepped: false` and not drawn (`stepped_runs`); `hidden_note` names it in the caption, or in
the skip line when fewer than `MIN_RUNS` stepped runs remain. `series_runs` appends this run
(from memory) and keeps the newest `MAX_RUNS`. `layout` splits each line at an unknown or
absent value and at a maturity-model version change or a rules-hash change both runs recorded
(#952; marked by a dashed rule), and spaces end labels with `fleet_svg.spread_vertical`
(`LABEL_GAP`); `render_trend(runs, steps)` draws it on a Step axis spanning the model's Steps,
naming every gap on a hatched note, never at Step 0. `fleet_diagrams` skips it below
`MIN_RUNS` (3). Golden render: `tests/fixtures/fleet_trends/` (regenerate with
`uv run --locked python tests/fleet_trends_fixture.py` from `skills/assess`).
"""
from __future__ import annotations

import json
import math
import textwrap
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NotRequired, TypedDict

import lib.fleet.fleet_svg as svg
from lib.svg_scope import SCOPES
from lib.fleet.fleet_compare import stored_steps
from lib.fleet.fleet_legacy import stamp_value, upgrade
from lib.fleet.fleet_history import one_per_date
from lib.fleet.fleet_level import LEVEL_FILE
from lib.fleet.fleet_maturity import StepPoint, group_steps, measured_text
from lib.fleet.fleet_stamp import FLEET_STAMP_VERSION, RUN_FILE, FleetStamp
from lib.fleet.fleet_svg import THEMED_HATCH, LinearScale
from lib.fleet.fleet_view import read_repos

TITLE = "Group Step over time"
FILE = "trend.svg"
MIN_RUNS = 3
MAX_RUNS = 12
WIDTH = 880.0
PAD = 16.0
PLOT_L, PLOT_R = 64.0, 520.0
PLOT_T, PLOT_H = 104.0, 260.0
LABEL_X = PLOT_R + 18
LABEL_GAP = 16.0  # minimum distance between end-label baselines
LABEL_SIZE = 12.0
NAME_CHARS = 18  # group name budget in an end label
TICK_W = 78.0  # room one date tick label needs
WRAP = 112
LINE_H = 16.0
AXIS_LABEL = "Lowest Step (measured repositories)"
MATCH_KEYS = ("window", "scans", "counts_only", "paths_hashed", "fleet_config_sha256", "platform",
              "managed_settings")

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
    points: dict[str, StepPoint]  # group -> its lowest Step with N of M measured
    stepped: bool  # False: an entry kept before level.json stored Steps (#941); not drawn
    rules_sha256: NotRequired[str | None]  # the rules the run scored with (#952); None or absent: not recorded


# --- reading the history ------------------------------------------------------


def trend_run(stamp: Mapping[str, Any], members: Iterable[tuple[str, str]],
              steps: Mapping[str, int | None] | None) -> TrendRun | None:
    """One run's point per group; None when the run had no maturity model. ``members`` are
    ``(group, repository)`` pairs; ``steps`` each repository's Step, None when the run stored none."""
    version = stamp.get("levels_version")
    if not isinstance(version, str) or not isinstance(stamp.get("scan_date"), str):
        return None
    run: TrendRun = {"label": stamp["scan_date"], "levels_version": version, "stepped": steps is not None,
                     "points": group_steps(members, steps or {})}
    run["rules_sha256"] = stamp.get("rules_sha256")
    return run


def stepped_runs(runs: Sequence[TrendRun]) -> list[TrendRun]:
    """The runs the trend draws: those that stored their Steps."""
    return [run for run in runs if run["stepped"]]


def hidden_note(runs: Sequence[TrendRun]) -> str | None:
    """Names the runs left out for want of stored Steps; None when none is."""
    old = [run["label"] for run in runs if not run["stepped"]]
    if not old:
        return None
    return (f"{len(old)} earlier run{'s' if len(old) > 1 else ''} kept before Steps were stored "
            f"not shown: {', '.join(old)}")


def _comparable(stamp: Any, current: FleetStamp) -> bool:
    return (isinstance(stamp, dict) and stamp.get("fleet_stamp_version") == FLEET_STAMP_VERSION
            and all(stamp_value(stamp, key) == stamp_value(current, key) for key in MATCH_KEYS))


def _read_entry(entry: Path, current: FleetStamp) -> TrendRun | None:
    """The entry as a trend point; None when it is unreadable or not comparable."""
    try:
        stamp: Any = json.loads((entry / RUN_FILE).read_text(encoding="utf-8"))
        if isinstance(stamp, dict):
            stamp = upgrade(stamp)  # an entry written before the 2.0.0 rename
        if not _comparable(stamp, current):
            return None
        members = [(row["group"], row["repo"]) for row in read_repos(entry)]
        return trend_run(stamp, members, stored_steps(entry / LEVEL_FILE))
    except (OSError, ValueError, KeyError, TypeError):  # FleetViewError is a ValueError
        return None


def history_runs(org_dir: Path, current: FleetStamp, repos: frozenset[str]) -> list[TrendRun]:
    """Earlier comparable runs under ``org_dir``, one per scan date before this run's, oldest first."""
    def read(entry: Path, date: str) -> TrendRun | None:
        run = _read_entry(entry, current)
        return run if run is not None and run["label"] == date else None

    return [run for _date, run in one_per_date(org_dir, current["scan_date"], repos, read)]


def series_runs(earlier: Sequence[TrendRun], current: TrendRun) -> list[TrendRun]:
    """The runs to draw: the newest ``MAX_RUNS``, this run last."""
    return [*earlier, current][-MAX_RUNS:]


def step_of(run: TrendRun, group: str) -> int | None:
    """The group's lowest Step in ``run``; None when absent or none of its repositories is measured."""
    point = run["points"].get(group)
    return None if point is None else point["step"]


# --- layout -------------------------------------------------------------------


@dataclass(frozen=True)
class EndLabel:
    group: str
    name: str  # possibly truncated
    value: int  # the lowest Step in the group's last run
    measured: str  # "N of M measured" in that run
    delta: int | None  # over the last unbroken segment; None for a one-run segment
    point: tuple[float, float]
    y: float  # baseline after spacing


@dataclass(frozen=True)
class Layout:
    groups: list[str]
    x: list[float]
    y: LinearScale
    steps: list[int]  # the Step ticks on the y axis
    labels: list[EndLabel]
    ticks: list[int]  # run indices given a date label
    breaks: list[int]  # run indices where the maturity-model version changes


def _break_before(runs: Sequence[TrendRun], i: int) -> bool:
    """Run ``i`` changes the rules: the maturity-model version, or the rules hash (#952) when both
    runs recorded one (a run that recorded none is not read as a rule change)."""
    then, now = runs[i - 1].get("rules_sha256"), runs[i].get("rules_sha256")
    return runs[i]["levels_version"] != runs[i - 1]["levels_version"] or (
        then is not None and now is not None and then != now)


def _segments(runs: Sequence[TrendRun], group: str) -> list[list[int]]:
    """Runs where ``group`` has a Step, split at gaps and version changes."""
    segments: list[list[int]] = []
    for i, run in enumerate(runs):
        if step_of(run, group) is None:
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


def step_range(runs: Sequence[TrendRun], steps: Sequence[int] = ()) -> list[int]:
    """The Steps the y axis shows: the model's ``steps`` and every Step drawn, as one whole range."""
    seen = [*steps, *(p["step"] for run in runs for p in run["points"].values() if p["step"] is not None)]
    lo, hi = (min(seen), max(seen)) if seen else (1, 2)
    return list(range(lo, max(hi, lo + 1) + 1))


def layout(runs: Sequence[TrendRun], steps: Sequence[int] = ()) -> Layout:
    """The plan for the stepped runs in ``runs`` (``stepped_runs``); the others are not drawn.
    ``steps`` are the maturity model's Step numbers, so the axis spans every Step."""
    runs = stepped_runs(runs)
    groups = sorted({g for run in runs for g in run["points"]})
    spacing = (PLOT_R - PLOT_L) / max(len(runs) - 1, 1)
    xs = [PLOT_L + spacing * i for i in range(len(runs))]
    axis = step_range(runs, steps)
    y = LinearScale(float(axis[0]), float(axis[-1]), PLOT_T + PLOT_H, PLOT_T)
    ends: list[tuple[str, int, str, int | None, tuple[float, float]]] = []
    for group in groups:
        segments = _segments(runs, group)
        if not segments:
            continue
        last = segments[-1]
        point = runs[last[-1]]["points"][group]
        value, first = point["step"], step_of(runs[last[0]], group)
        assert value is not None and first is not None
        delta = value - first if len(last) > 1 else None
        ends.append((group, value, measured_text(point), delta, (xs[last[-1]], y(value))))
    spread = svg.spread_vertical([end[-1][1] + 4 for end in ends], LABEL_GAP)
    labels = [EndLabel(group, svg.truncate(group, NAME_CHARS), value, measured, delta, point, ly)
              for (group, value, measured, delta, point), ly in zip(ends, spread)]
    breaks = [i for i in range(1, len(runs)) if _break_before(runs, i)]
    return Layout(groups, xs, y, axis, labels, _tick_indices(len(runs), spacing), breaks)


# --- render -------------------------------------------------------------------


def _delta_text(delta: int | None) -> str:
    if delta is None:
        return ""
    return "=" if delta == 0 else f"{delta:+d}"


def _gaps(runs: Sequence[TrendRun], groups: Sequence[str]) -> list[str]:
    notes = []
    for group in groups:
        absent = [r["label"] for r in runs if group not in r["points"]]
        unknown = [r["label"] for r in runs if group in r["points"] and step_of(r, group) is None]
        parts = ([f"no measured repository on {', '.join(unknown)}"] if unknown else []) + \
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


def _point(px: float, py: float, role: str, group: str, run: TrendRun) -> str:
    point = run["points"][group]
    tip = f"{group}: Step {point['step']} on {run['label']}, {measured_text(point)}"
    values: dict[str, str | int | None] = {"group": group, "step": point["step"], "measured": point["measured"],
                                           "repos": point["repos"], "run": run["label"]}
    return (f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" class="f-{role}"{svg.data_attrs(values)}>'
            f'<title>{svg.esc(tip)}</title></circle>')


def render_trend(runs: Sequence[TrendRun], steps: Sequence[int] = ()) -> str:
    """The trend chart as an SVG document; the same runs give the same bytes. Only the stepped
    runs are drawn; the others are named under the chart (``hidden_note``). ``steps`` are the
    maturity model's Step numbers, so the axis spans every Step."""
    hidden = hidden_note(runs)
    runs = stepped_runs(runs)
    plan = layout(runs, steps)
    bottom = PLOT_T + PLOT_H
    body = [svg.text(PAD, PAD + 18, TITLE, size=18, weight="bold", cls="fg"),
            svg.text(PAD, PAD + 40, f"{len(runs)} dated runs, {runs[0]['label']} to {runs[-1]['label']} "
                     "· each group's lowest Step over its measured repositories, never an average", cls="muted"),
            svg.text(PAD, PAD + 58, svg.truncate(_versions_line(runs), WRAP), size=11.5, cls="muted")]
    for tick in plan.steps:
        ty = plan.y(tick)
        body.append(f'<line x1="{PLOT_L:.1f}" y1="{ty:.1f}" x2="{PLOT_R:.1f}" y2="{ty:.1f}" '
                    f'class="s-rule" stroke-width="0.6"/>')
        body.append(svg.text(PLOT_L - 10, ty + 4, f"Step {tick}", size=11, anchor="end", cls="muted"))
    for i in plan.ticks:
        body.append(svg.text(plan.x[i], bottom + 18, runs[i]["label"], size=11, anchor="middle",
                             cls="muted"))
    taken: list[svg.Box] = []
    for i in plan.breaks:
        bx = (plan.x[i - 1] + plan.x[i]) / 2
        body.append(f'<line x1="{bx:.1f}" y1="{PLOT_T - 14:.1f}" x2="{bx:.1f}" y2="{bottom:.1f}" '
                    f'class="s-muted" stroke-dasharray="4 4"/>')
        caption = f"maturity model {runs[i]['levels_version']}"
        box = svg.text_box(bx + 4, PLOT_T - 18, caption, 11)
        if box.x + box.w <= WIDTH - PAD and not any(svg.overlaps(box, other) for other in taken):
            taken.append(box)
            body.append(svg.text(bx + 4, PLOT_T - 18, caption, size=11, cls="muted"))
    for index, group in enumerate(plan.groups):
        role, dash = _series_class(index)
        for segment in _segments(runs, group):
            points = [(plan.x[i], plan.y(step_of(runs[i], group) or 0)) for i in segment]
            if len(points) > 1:
                coords = " ".join(f"{px:.1f},{py:.1f}" for px, py in points)
                body.append(f'<polyline points="{coords}" fill="none" class="s-{role}" '
                            f'stroke-width="2"{dash}/>')
            for (px, py), i in zip(points, segment):
                body.append(_point(px, py, role, group, runs[i]))
    for label in plan.labels:
        role, _ = _series_class(plan.groups.index(label.group))
        px, py = label.point
        body.append(f'<line x1="{px + 5:.1f}" y1="{py:.1f}" x2="{LABEL_X - 4:.1f}" y2="{label.y - 4:.1f}" '
                    f'class="s-rule" stroke-width="1"/>')
        name = f"{label.name} Step {label.value}"
        body.append(svg.text(LABEL_X, label.y, name, size=LABEL_SIZE, cls=role, weight="600",
                             tooltip=label.group))
        cursor_x = LABEL_X + svg.text_width(name, LABEL_SIZE) + 6
        delta = _delta_text(label.delta)
        if delta:
            tone = "accent" if delta.startswith("+") else "bad" if delta.startswith("-") else "muted"
            body.append(svg.text(cursor_x, label.y, delta, size=11, cls=tone))
            cursor_x += svg.text_width(delta, 11) + 6
        body.append(svg.text(cursor_x, label.y, label.measured, size=11, cls="muted"))
    body.append(svg.text((PLOT_L + PLOT_R) / 2, bottom + 38, "Fleet run (scan date)", anchor="middle",
                         cls="muted"))
    mid = PLOT_T + PLOT_H / 2
    body.append(svg.text(16.0, mid, AXIS_LABEL, size=11.0, anchor="middle", cls="muted",
                         extra=f' transform="rotate(-90 16 {mid:.1f})"'))
    cursor = max(bottom + 60, max((label.y for label in plan.labels), default=0.0) + 20)
    for line in textwrap.wrap("End label: group, its lowest Step in its last run, the change over its last "
                              "unbroken stretch, and how many of its repositories were measured", WRAP):
        body.append(svg.text(PLOT_L, cursor, line, size=11.5, cls="muted"))
        cursor += LINE_H
    if hidden:
        for line in textwrap.wrap(hidden, WRAP):
            body.append(svg.text(PLOT_L, cursor, line, size=11.5, cls="muted"))
            cursor += LINE_H
    gaps = _gaps(runs, plan.groups)
    if gaps:
        lines = textwrap.wrap("Gaps in a line, unknown is not Step 0: " + ", ".join(gaps), WRAP)
        cursor += 6
        parts, height = svg.hatched_note(PLOT_L, cursor, lines)
        body.extend(parts)
        cursor += height
    desc = ("Line chart of each fleet group's lowest Step over its measured repositories across dated "
            "fleet runs, oldest on the left, never an average; a line breaks where the maturity-model "
            "version changes, older runs without stored Steps are named, not drawn, and runs where a "
            "group has no measured repository are listed on a hatched note, not drawn at Step 0.")
    return svg.document(WIDTH, float(int(cursor + PAD)), TITLE, desc, body, scope=SCOPES["trend"],
                        style=svg.theme_style(SERIES_ROLES), defs=THEMED_HATCH)
