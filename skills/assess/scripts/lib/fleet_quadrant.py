"""Activity x maturity quadrant SVG from one fleet run (issue #674, parent #672).

One dot per repository: across, the commits in the run's history window
(``ai_usage.total_commits``, non-merge commits); up, the repository's mean
maturity. Busy repositories with low maturity are where agents do the most
damage soonest, so that quadrant is shaded and its dots drawn in the alarm colour.

Maturity per repository re-evaluates each ``team`` row of the maturity model
(``lib.levels.evaluate_row``) against that repository's own ``scans``, as the
team pages do (``lib/fleet_next_steps.py``). The repository's maturity is
``lib.fleet_maturity.mean_share``: each row's share of its top level, averaged
over the rows that could be measured, the unit and row set the slope chart and
trend lines use too. A repository
with no measured row, or no commit count (a failed scan, an unavailable
``ai_usage`` block), is never drawn at zero: it is named on a hatched strip
under the plot with what is unknown.

The vertical split is half the top level; the horizontal split is the median
commit count of the plotted repositories. Labels are placed by
``fleet_svg.place_labels``; a label that fits nowhere is named under the plot.
Deterministic: points sorted by a fixed key, fixed number formatting.
"""
from __future__ import annotations

import textwrap
from collections.abc import Sequence
from typing import TypedDict

from lib import fleet_svg as svg
from lib.fleet import STATUS_OK, FleetRow
from lib.fleet_groups import repo_signals
from lib.fleet_maturity import SCOPE, known_shares, mean_share
from lib.fleet_svg import THEMED_HATCH, Box, LinearScale
from lib.levels import MaturityModel, evaluate_row

TITLE = "Fleet activity and maturity"
FILE = "quadrant.svg"
WIDTH = 760.0
PAD = 16.0
PLOT_L, PLOT_R = 70.0, 730.0
PLOT_T = 84.0
PLOT_H = 300.0
Y_SPLIT = 0.5
WRAP = 104  # characters per strip / note line at 11.5px
LINE_H = 16.0


_BOTTOM = PLOT_T + PLOT_H
# Quadrant captions: (x, y, text, anchor, theme role, weight); labels are placed around them.
CAPTIONS: tuple[tuple[float, float, str, str, str, str | None], ...] = (
    (PLOT_R - 8, _BOTTOM - 10, "Invest here: busy and immature", "end", "bad", "600"),
    (PLOT_R - 8, PLOT_T + 16, "Healthy", "end", "accent", "600"),
    (PLOT_L + 8, _BOTTOM - 10, "Quiet; can wait", "start", "muted", None),
    (PLOT_L + 8, PLOT_T + 16, "Mature, little change", "start", "muted", None),
)


class RepoPoint(TypedDict):
    repo: str
    commits: int | None  # None: no commit count for the window
    maturity: float | None  # mean share of top level over measured team rows; None: none measured
    measured_rows: int
    team_rows: int


def _commits(row: FleetRow) -> int | None:
    block = (row["scans"] or {}).get("ai_usage") if row["status"] == STATUS_OK else None
    if not isinstance(block, dict) or block.get("available") is not True:
        return None
    total = block.get("total_commits")
    return total if isinstance(total, int) and not isinstance(total, bool) else None


def repo_points(levels: MaturityModel, repos: Sequence[FleetRow]) -> list[RepoPoint]:
    """Each repository's commit count and mean maturity, sorted by repository id."""
    team = [row for row in levels["rows"] if row["scope"] == SCOPE]
    points: list[RepoPoint] = []
    for repo in sorted(repos, key=lambda r: r["repo"]):
        data = repo_signals(repo)
        cells = [(evaluate_row(row, data)["level"], len(row["levels"])) for row in team]
        points.append({
            "repo": repo["repo"], "commits": _commits(repo), "maturity": mean_share(cells),
            "measured_rows": len(known_shares(cells)), "team_rows": len(team),
        })
    return points


def median_commits(points: Sequence[RepoPoint]) -> int:
    """The lower median commit count of the plotted points, at least 1, so a repository
    with no commits is never "busy" even in a quiet window."""
    counts = sorted(p["commits"] for p in points if p["commits"] is not None)
    return max(counts[(len(counts) - 1) // 2] if counts else 0, 1)


def at_risk(point: RepoPoint, split: int) -> bool:
    """Busy (at or above the median) and below half of the top level."""
    return (point["commits"] is not None and point["maturity"] is not None
            and point["commits"] >= split and point["maturity"] < Y_SPLIT)


def _unknown_text(point: RepoPoint) -> str:
    commits, maturity = point["commits"], point["maturity"]
    if commits is None and maturity is None:
        return f"{point['repo']} (level and activity unknown)"
    if maturity is None:
        return f"{point['repo']} (level unknown, {commits} commits)"
    return f"{point['repo']} (activity unknown, level {maturity:.2f})"


def layout(points: Sequence[RepoPoint]) -> tuple[list[RepoPoint], LinearScale, LinearScale,
                                                  list[svg.PlacedLabel | None]]:
    """The plotted points in placement order, both scales and each point's label placement."""
    plotted = [p for p in points if p["commits"] is not None and p["maturity"] is not None]
    split = median_commits(plotted)
    plotted.sort(key=lambda p: (not at_risk(p, split), -(p["commits"] or 0), p["repo"]))
    top = svg.nice_ceiling(max([p["commits"] or 0 for p in plotted], default=0))
    x = LinearScale(0.0, top, PLOT_L, PLOT_R)
    y = LinearScale(0.0, 1.0, PLOT_T + PLOT_H, PLOT_T)
    anchors = [(x(p["commits"] or 0), y(p["maturity"] or 0.0), p["repo"]) for p in plotted]
    bounds = Box(PLOT_L, PLOT_T, PLOT_R - PLOT_L, PLOT_H)
    captions = [svg.text_box(cx, cy, caption, 12.5, anchor) for cx, cy, caption, anchor, _, _ in CAPTIONS]
    return plotted, x, y, svg.place_labels(anchors, bounds, obstacles=captions)


def count_ticks(top: float) -> int:
    """Commit-axis tick steps: one per commit below 5, so a count axis never shows 0.2."""
    return int(top) if top < 5 else 5


def _wrapped(prefix: str, items: Sequence[str]) -> list[str]:
    return textwrap.wrap(prefix + ", ".join(items), WRAP)


def render_quadrant(points: Sequence[RepoPoint], window: str) -> str:
    """The quadrant as an SVG document; the same points give the same bytes."""
    plotted, x, y, labels = layout(points)
    split = median_commits(plotted)
    bottom = PLOT_T + PLOT_H
    body = [svg.text(PAD, PAD + 18, TITLE, size=18, weight="bold", cls="fg"),
            svg.text(PAD, PAD + 40, f"{len(points)} repositories · commits in the {window} window "
                     "(non-merge) against mean maturity", cls="muted"),
            svg.text(PAD, PAD + 58, "Maturity: each team row's level as a share of its top level, "
                     "averaged over the rows that could be measured", size=11.5, cls="muted")]
    sx, sy = x(split), y(Y_SPLIT)
    body.append(f'<rect x="{sx:.1f}" y="{PLOT_T:.1f}" width="{PLOT_R - sx:.1f}" '
                f'height="{sy - PLOT_T:.1f}" class="f-accent" opacity="0.06"/>')
    body.append(f'<rect x="{sx:.1f}" y="{sy:.1f}" width="{PLOT_R - sx:.1f}" '
                f'height="{bottom - sy:.1f}" class="f-bad" opacity="0.08"/>')
    for tick in x.ticks(count_ticks(x.hi)):
        tx = x(tick)
        body.append(f'<line x1="{tx:.1f}" y1="{PLOT_T:.1f}" x2="{tx:.1f}" y2="{bottom:.1f}" '
                    f'class="s-rule" stroke-width="0.6"/>')
        body.append(svg.text(tx, bottom + 18, svg.tick_label(tick), size=11, anchor="middle",
                             cls="muted"))
    for tick in y.ticks(4):
        ty = y(tick)
        body.append(f'<line x1="{PLOT_L:.1f}" y1="{ty:.1f}" x2="{PLOT_R:.1f}" y2="{ty:.1f}" '
                    f'class="s-rule" stroke-width="0.6"/>')
        body.append(svg.text(PLOT_L - 10, ty + 4, svg.tick_label(tick), size=11, anchor="end",
                             cls="muted"))
    body.append(f'<line x1="{sx:.1f}" y1="{PLOT_T:.1f}" x2="{sx:.1f}" y2="{bottom:.1f}" '
                f'class="s-muted" stroke-dasharray="4 4"/>')
    body.append(f'<line x1="{PLOT_L:.1f}" y1="{sy:.1f}" x2="{PLOT_R:.1f}" y2="{sy:.1f}" '
                f'class="s-muted" stroke-dasharray="4 4"/>')
    for cx, cy, caption, anchor, tone, weight in CAPTIONS:
        body.append(svg.text(cx, cy, caption, size=12.5, anchor=anchor, weight=weight, cls=tone))
    body.append(svg.text((PLOT_L + PLOT_R) / 2, bottom + 38,
                         f"Commits in the {window} window (split at the median, {split})",
                         anchor="middle", cls="muted"))
    mid = PLOT_T + PLOT_H / 2
    body.append(svg.text(22, mid, "Mean share of top level", anchor="middle", cls="muted",
                         extra=f' transform="rotate(-90 22 {mid:.1f})"'))
    hidden: list[str] = []
    for point, placed in zip(plotted, labels):
        cx, cy = x(point["commits"] or 0), y(point["maturity"] or 0.0)
        tone = "bad" if at_risk(point, split) else "accent"
        tip = (f"{point['repo']}: {point['commits']} commits, maturity {point['maturity']:.2f} "
               f"({point['measured_rows']} of {point['team_rows']} team rows measured)")
        body.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="6.5" class="f-{tone} s-bg" '
                    f'stroke-width="1.5"><title>{svg.esc(tip)}</title></circle>')
        if placed is None:
            hidden.append(point["repo"])
        else:
            body.append(svg.text(placed.x, placed.y, point["repo"], size=11.5, anchor=placed.anchor,
                                 cls="fg"))
    cursor = bottom + 56
    for swatch, label in (("bad", f"Busy (at least {split} commits) and below half the top level"),
                          ("accent", "Other repositories")):
        body.append(f'<circle cx="{PLOT_L + 6:.1f}" cy="{cursor - 4:.1f}" r="5" class="f-{swatch}"/>')
        body.append(svg.text(PLOT_L + 18, cursor, label, size=11.5, cls="muted"))
        cursor += LINE_H + 2
    if hidden:
        cursor += 4
        for line in _wrapped("Not labelled (crowded): ", sorted(hidden)):
            body.append(svg.text(PLOT_L, cursor, line, size=11.5, cls="muted"))
            cursor += LINE_H
    unknown = [_unknown_text(p) for p in points if p["commits"] is None or p["maturity"] is None]
    if unknown:
        lines = _wrapped("Not plotted, unknown is not zero: ", unknown)
        cursor += 6
        parts, height = svg.hatched_note(PLOT_L, cursor, lines)
        body.extend(parts)
        cursor += height
    desc = ("Scatter plot of fleet repositories: commits in the history window across, mean "
            "maturity up; busy, immature repositories are shaded; repositories with an unknown "
            "level or commit count are listed on a hatched strip, not drawn at zero.")
    return svg.document(WIDTH, float(int(cursor + PAD)), TITLE, desc, body, defs=THEMED_HATCH)
