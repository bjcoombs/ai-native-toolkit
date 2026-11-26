"""Group trend lines across the dated fleet run history (issue #676, parent #672).

One line per group: its mean maturity in each comparable run, oldest on the
left, this run on the right. ``--compare`` sees two runs; this sees the history.

**Mean maturity.** A group's value in a run is ``lib.fleet.fleet_maturity.mean_share``
over its ``team`` rows in ``groups.jsonl``: each known row level as a share of the
row's ``top_level``, the unit and row set of the slope chart (#675). An entry written before ``groups.jsonl`` carried ``top_level``
takes each row's top level from this run's maturity model when the entry used the
same version (same version, same rows). Any other run with a known team row
lacking a top level (an entry on an older maturity-model version) is marked
unscaled and not drawn: the axis is always 0 to 1 share of top level, and a plain
mean on it would read as a false drop. The caption names those runs, and the
trend counts only share runs toward ``MIN_RUNS``. A group with no team row
at a level, or absent from a run, has no value there: its line breaks and the
gap is named on a hatched note, never drawn at zero.

**Lower bound (#697).** A run whose groups have an unknown team row with a confirmed
``floor`` also carries ``floor_means``: ``fleet_maturity.floor_mean`` per group, a
lower bound over the rows with a level or a floor. It is printed as "at least" in a
point's tooltip and its ``data-floor-mean``, never drawn: an entry written before #697 has no floors, so a line
on that figure would jump where the floors first appear.

**Comparable runs.** ``history_runs`` reads the ``<date>[.<n>]`` entries under
the history ``<org>`` directory (``lib/fleet/fleet_history.py``): ``run.json`` and
``groups.jsonl``, and ``repos.jsonl`` only to choose between same-day runs. An entry counts when its stamp is at this toolkit's stamp
version, matches this run on window, scan selection, output mode and path
hashing, and records a maturity-model version; an unreadable or malformed
entry is passed over, never an error. One run per scan date: the entry over
this run's repository set when there is one, else the highest suffix. Dates
on or after this run's are dropped, since this run (from memory, not the
history, so a failed history write still shows it) is the last point. At most
``MAX_RUNS`` of the newest are drawn.

**Maturity-model version.** A change of version between two runs, or of the rules hash
(``rules_sha256``, #952) when both runs recorded one, breaks every
line there (``--compare`` refuses to compare across versions) and a dashed rule
marks it. Each run's point is still drawn, so a one-run segment is a dot. Each point
carries its tooltip values as ``data-group``, ``data-mean``, ``data-run`` and, with
floors, ``data-floor-mean`` (#1360).

``layout`` is shared by the renderer and its tests: end labels are spread by
``fleet_svg.spread_vertical`` so they never overlap. Deterministic: groups and
runs sorted, fixed number formatting, no clock read.

Group trend lines across the dated run history (#676). `history_runs(org_dir, stamp, repos)`
reads each `<date>[.<n>]` entry (`fleet_history.one_per_date`; `run.json`,
`groups.jsonl`, and `repos.jsonl` only between same-day runs) dated before this run whose stamp matches on window, scans, output mode,
path hashing and `--fleet-config` hash and records a maturity-model version; unreadable entries are passed over. One
run per date: the entry over this run's repository set, else the highest suffix.
`trend_run(stamp, groups, levels)` is one run's point per group: `group_mean`, the
`fleet_maturity.mean_share` of the group's known team rows over their `top_level` (#684, the
slope chart's unit and row set). An entry without `top_level` takes it from
this run's maturity model (`levels`, passed through `history_runs`) when it used the same
version (`with_top_levels`); otherwise a run with any known team row lacking it is marked
`scaled: false` and not drawn (`share_runs`), so the axis is always 0 to 1, and `hidden_note`
names it in the caption, or in the skip line when fewer than `MIN_RUNS` share runs remain;
`series_runs` appends this run (from memory) and keeps the newest `MAX_RUNS`. `layout`
splits each line at an unknown or absent value and at a maturity-model version change or a
rules-hash change both runs recorded (#952; marked by a dashed rule), and spaces end labels with `fleet_svg.spread_vertical`
(`LABEL_GAP`); `render_trend` draws it, naming every gap on a hatched note, never at zero.
`fleet_diagrams` skips it below `MIN_RUNS` (3). Golden render: `tests/fixtures/fleet_trends/`
(regenerate with `uv run --locked python tests/fleet_trends_fixture.py` from `skills/assess`).
A run with floored unknown team rows carries `floor_means` (#697), printed in each point's tooltip and never drawn, so the line does not jump where floors first appear in the history.
"""
from __future__ import annotations

import json
import math
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NotRequired, TypedDict

import lib.fleet.fleet_svg as svg
from lib.svg_scope import SCOPES
from lib.fleet.fleet_groups import GROUPS_FILE, GroupRow, RowLevel
from lib.fleet.fleet_legacy import stamp_value, upgrade
from lib.fleet.fleet_history import one_per_date
from lib.fleet.fleet_maturity import SCOPE, floor_mean, has_floor, mean_share
from lib.fleet.fleet_stamp import FLEET_STAMP_VERSION, RUN_FILE, FleetStamp
from lib.fleet.fleet_svg import THEMED_HATCH, LinearScale
from lib.fleet.fleet_view import load_groups
from lib.scoring.levels import MaturityModel, floor_of

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
Y_TICKS = (0.0, 0.25, 0.5, 0.75, 1.0)
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
    means: dict[str, float | None]  # group -> mean share of top level; None: no team row at a level
    scaled: bool  # False: an entry without top levels on an older model version; not drawn
    floor_means: NotRequired[dict[str, float | None]]  # floor-inclusive lower bound (#697)
    rules_sha256: NotRequired[str | None]  # the rules the run scored with (#952); None or absent: not recorded


# --- reading the history ------------------------------------------------------


def _known(group: GroupRow) -> list[RowLevel]:
    """The group's team rows with a whole level."""
    return [row for row in group["levels"] if row["scope"] == SCOPE and row["status"] == "level"
            and type(row["level"]) is int]


def scaled(groups: Sequence[GroupRow]) -> bool:
    """Every known team row in the run carries a positive ``top_level`` (#684)."""
    return all(type(top := row.get("top_level")) is int and top > 0
               for group in groups for row in _known(group))


def group_mean(group: GroupRow) -> float | None:
    """The group's mean share of top level over its known team rows; None when none is."""
    return mean_share((row["level"], row.get("top_level")) for row in _known(group))


def _floor_cells(group: GroupRow) -> list[tuple[int | None, int | None, int | None]]:
    return [(row["level"] if row["status"] == "level" and type(row["level"]) is int else None,
             floor_of(row), row.get("top_level")) for row in group["levels"] if row["scope"] == SCOPE]


def group_floor_mean(group: GroupRow) -> float | None:
    """The group's floor-inclusive mean share (a lower bound); None when no row has a level or floor."""
    return floor_mean(_floor_cells(group))


def with_top_levels(groups: Sequence[GroupRow], levels: MaturityModel | None,
                    version: str) -> list[GroupRow]:
    """``groups`` with each row lacking ``top_level`` given the row's level count from
    ``levels`` when the entry used that maturity-model version; else unchanged."""
    if levels is None or levels["version"] != version:
        return list(groups)
    tops = {row["id"]: len(row["levels"]) for row in levels["rows"]}
    filled: list[GroupRow] = []
    for group in groups:
        rows: list[RowLevel] = []
        for row in group["levels"]:
            if "top_level" not in row and row["id"] in tops:
                row = {**row, "top_level": tops[row["id"]]}
            rows.append(row)
        filled.append({**group, "levels": rows})
    return filled


def trend_run(stamp: Mapping[str, Any], groups: Sequence[GroupRow],
              levels: MaturityModel | None = None) -> TrendRun | None:
    """One run's point per group; None when the run had no maturity model. ``levels`` (this
    run's maturity model) supplies top levels an entry of the same version did not record."""
    version = stamp.get("levels_version")
    if not isinstance(version, str) or not isinstance(stamp.get("scan_date"), str):
        return None
    groups = with_top_levels(groups, levels, version)
    unit = scaled(groups)
    ordered = sorted(groups, key=lambda g: g["group"])
    run: TrendRun = {"label": stamp["scan_date"], "levels_version": version, "scaled": unit,
                     "means": {g["group"]: group_mean(g) if unit else None for g in ordered}}
    run["rules_sha256"] = stamp.get("rules_sha256")
    if unit and any(has_floor(_floor_cells(g)) for g in ordered):
        run["floor_means"] = {g["group"]: group_floor_mean(g) for g in ordered}
    return run


def share_runs(runs: Sequence[TrendRun]) -> list[TrendRun]:
    """The runs the trend draws: those whose means are shares of top level."""
    return [run for run in runs if run["scaled"]]


def hidden_note(runs: Sequence[TrendRun]) -> str | None:
    """Names the runs left out for want of top levels; None when none is."""
    old = [run["label"] for run in runs if not run["scaled"]]
    if not old:
        return None
    return (f"{len(old)} earlier run{'s' if len(old) > 1 else ''} on an older maturity-model version "
            f"not shown: {', '.join(old)}")


def _comparable(stamp: Any, current: FleetStamp) -> bool:
    return (isinstance(stamp, dict) and stamp.get("fleet_stamp_version") == FLEET_STAMP_VERSION
            and all(stamp_value(stamp, key) == stamp_value(current, key) for key in MATCH_KEYS))


def _read_entry(entry: Path, current: FleetStamp, levels: MaturityModel | None) -> TrendRun | None:
    """The entry as a trend point; None when it is unreadable or not comparable."""
    try:
        stamp: Any = json.loads((entry / RUN_FILE).read_text(encoding="utf-8"))
        if isinstance(stamp, dict):
            stamp = upgrade(stamp)  # an entry written before the 2.0.0 rename
        if not _comparable(stamp, current):
            return None
        return trend_run(stamp, load_groups(entry / GROUPS_FILE), levels)
    except (OSError, ValueError, KeyError, TypeError):  # FleetViewError is a ValueError
        return None


def history_runs(org_dir: Path, current: FleetStamp, repos: frozenset[str],
                 levels: MaturityModel | None = None) -> list[TrendRun]:
    """Earlier comparable runs under ``org_dir``, one per scan date before this run's, oldest
    first; ``levels`` is this run's maturity model (``trend_run``)."""
    def read(entry: Path, date: str) -> TrendRun | None:
        run = _read_entry(entry, current, levels)
        return run if run is not None and run["label"] == date else None

    return [run for _date, run in one_per_date(org_dir, current["scan_date"], repos, read)]


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
    point: tuple[float, float]
    y: float  # baseline after spacing


@dataclass(frozen=True)
class Layout:
    groups: list[str]
    x: list[float]
    y: LinearScale
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
    """Runs where ``group`` has a value, split at gaps and version changes."""
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
    """The plan for the share runs in ``runs`` (``share_runs``); the others are not drawn."""
    runs = share_runs(runs)
    groups = sorted({g for run in runs for g in run["means"]})
    spacing = (PLOT_R - PLOT_L) / max(len(runs) - 1, 1)
    xs = [PLOT_L + spacing * i for i in range(len(runs))]
    y = LinearScale(0.0, 1.0, PLOT_T + PLOT_H, PLOT_T)
    ends: list[tuple[str, float, float | None, tuple[float, float]]] = []
    for group in groups:
        segments = _segments(runs, group)
        if not segments:
            continue
        last = segments[-1]
        value = runs[last[-1]]["means"][group]
        first = runs[last[0]]["means"][group]
        assert value is not None and first is not None
        delta = round(value - first, 2) if len(last) > 1 else None
        ends.append((group, value, delta, (xs[last[-1]], y(value))))
    spread = svg.spread_vertical([point[1] + 4 for *_, point in ends], LABEL_GAP)
    labels = [EndLabel(group, svg.truncate(group, NAME_CHARS), value, delta, point, ly)
              for (group, value, delta, point), ly in zip(ends, spread)]
    breaks = [i for i in range(1, len(runs)) if _break_before(runs, i)]
    return Layout(groups, xs, y, labels, _tick_indices(len(runs), spacing), breaks)


# --- render -------------------------------------------------------------------


def _delta_text(delta: float | None) -> str:
    if delta is None:
        return ""
    return "0.00" if delta == 0 else f"{delta:+.2f}"


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


def _bound_tip(run: TrendRun, group: str) -> str:
    bound = run.get("floor_means", {}).get(group)
    return "" if bound is None else f" (with floors, at least {bound:.2f})"


def _bound_lines(run: TrendRun, groups: Sequence[str]) -> list[str]:
    """The last run's floor-inclusive lower bounds as visible text; none without floors."""
    bounds = run.get("floor_means", {})
    floored = [f"{g}\u00a0{bounds[g]:.2f}" for g in groups if bounds.get(g) is not None]
    if not floored:
        return []
    return textwrap.wrap(f"With floors, a lower bound (unknown practices counted at their confirmed floor), "
                         f"at least, on {run['label']}: " + ", ".join(floored), WRAP)


def _series_class(index: int) -> tuple[str, str]:
    """The colour role and any dash for the ``index``-th group."""
    dash = ' stroke-dasharray="6 3"' if index >= len(SERIES) else ""
    return f"series{index % len(SERIES)}", dash


def render_trend(runs: Sequence[TrendRun]) -> str:
    """The trend chart as an SVG document; the same runs give the same bytes. Only the share
    runs are drawn; the others are named under the chart (``hidden_note``)."""
    hidden = hidden_note(runs)
    runs = share_runs(runs)
    plan = layout(runs)
    bottom = PLOT_T + PLOT_H
    body = [svg.text(PAD, PAD + 18, TITLE, size=18, weight="bold", cls="fg"),
            svg.text(PAD, PAD + 40, f"{len(runs)} dated runs, {runs[0]['label']} to {runs[-1]['label']} "
                     "· mean share of top level per group (practices with a level)", cls="muted"),
            svg.text(PAD, PAD + 58, svg.truncate(_versions_line(runs), WRAP), size=11.5, cls="muted")]
    for tick in Y_TICKS:
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
        caption = f"maturity model {runs[i]['levels_version']}"
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
                tip = f"{group}: {runs[i]['means'][group]:.2f} on {runs[i]['label']}"
                tip += _bound_tip(runs[i], group)
                bound = runs[i].get("floor_means", {}).get(group)
                values = {"group": group, "mean": f"{runs[i]['means'][group]:.2f}", "run": runs[i]["label"],
                          "floor-mean": None if bound is None else f"{bound:.2f}"}
                body.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" class="f-{role}"{svg.data_attrs(values)}>'
                            f'<title>{svg.esc(tip)}</title></circle>')
    for label in plan.labels:
        role, _ = _series_class(plan.groups.index(label.group))
        px, py = label.point
        body.append(f'<line x1="{px + 5:.1f}" y1="{py:.1f}" x2="{LABEL_X - 4:.1f}" y2="{label.y - 4:.1f}" '
                    f'class="s-rule" stroke-width="1"/>')
        name = f"{label.name} {label.value:.2f}"
        body.append(svg.text(LABEL_X, label.y, name, size=LABEL_SIZE, cls=role, weight="600",
                             tooltip=label.group))
        delta = _delta_text(label.delta)
        if delta:
            tone = "accent" if delta.startswith("+") else "bad" if delta.startswith("-") else "muted"
            body.append(svg.text(LABEL_X + svg.text_width(name, LABEL_SIZE) + 6, label.y, delta,
                                 size=11, cls=tone))
    body.append(svg.text((PLOT_L + PLOT_R) / 2, bottom + 38, "Fleet run (scan date)", anchor="middle",
                         cls="muted"))
    mid = PLOT_T + PLOT_H / 2
    body.append(svg.text(22.0, mid, AXIS_LABEL, size=12.0, anchor="middle", cls="muted",
                         extra=f' transform="rotate(-90 22 {mid:.1f})"'))
    cursor = max(bottom + 60, max((label.y for label in plan.labels), default=0.0) + 20)
    body.append(svg.text(PLOT_L, cursor, "End label: group, its mean in its last run, and the change "
                         "over its last unbroken stretch", size=11.5, cls="muted"))
    cursor += LINE_H
    for line in _bound_lines(runs[-1], plan.groups):
        body.append(svg.text(PLOT_L, cursor, line, size=11.5, cls="muted"))
        cursor += LINE_H
    if hidden:
        for line in textwrap.wrap(hidden, WRAP):
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
            "oldest on the left; a line breaks where the maturity-model version changes, older runs "
            "without top levels are named, not drawn, and runs where a group has no level are "
            "listed on a hatched note, not drawn at zero.")
    return svg.document(WIDTH, float(int(cursor + PAD)), TITLE, desc, body, scope=SCOPES["trend"],
                        style=svg.theme_style(SERIES_ROLES), defs=THEMED_HATCH)
