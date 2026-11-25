"""The fleet median tag profile strip and its data, ``fleet-data.json`` ``tag_profile`` (#940).

**Data** (``tag_profile``). One ``FleetTagRow`` per truth tag, every tag of ``TRUTH_TAGS`` in
that fixed order, from each repository's per-tag tally (``repos[].tags``, #1073: the
headline strip's cells, ``lib.scoring.level_tags.tag_cells``), never a second tally. A repository is
measured on a tag when its cell has ``measured`` above 0; its share is ``met`` over
``measured``. ``median``, ``p25`` and ``p75`` are those shares' percentiles with
``numpy.percentile``'s linear interpolation (``lib.class_sizes.percentile``), rounded to 4
places, and null when no repository was measured on the tag: never 0. ``repos`` counts the
repositories with a per-tag tally (scanned, with a model) and ``measured`` those measured on
the tag. ``conditions`` and ``countable`` come from the maturity model through
``lib.scoring.level_tags.tag_rows`` over a blank evaluation (a condition's grade is the model's, so
whether it can count is the same in every repository). The rows carry tag tokens and
numbers only, so they name no repository. None without a maturity model.

**Strip** (``tag-profile.svg``, beside ``groups.jsonl``). One row per tag on a shared 0 to
100 percent scale: a dot at the median and a whisker from the 25th to the 75th percentile (a
whisker of zero length, as with one repository, is not drawn: it collapses to the dot). The
row label is the tag title, its condition count and the repositories measured on it
(``Behaviour · 3 conditions · 12 of 14 repositories``). A row no repository measured is a dashed, hollow track
with its reason and no dot (``row_state``): no condition has the tag yet, its conditions are
listed and never counted, or no repository measured a counted condition. Each row's hover
text is the tag ``why`` (#939). Point marks and lines only, never a filled area; no
repository name, so counts-only and ``--hash-salt`` runs draw the same picture.
Deterministic: two renders are byte-identical. Golden renders:
``tests/fixtures/fleet_diagrams/`` (``tests/fleet_tag_profile_fixture.py``). Tests:
``tests/test_fleet_tag_profile.py``.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import TypedDict

import lib.fleet.fleet_svg as svg
from lib.assess_diagrams import wrap
from lib.class_sizes import percentile
from lib.scoring.level_result_types import TagCell
from lib.scoring.level_tags import conditions_label, tag_rows
from lib.scoring.level_tally import level_result
from lib.scoring.levels import MaturityModel
from lib.scoring.levels_fields import TRUTH_TAG_TITLES
from lib.vocabulary import IN_PLACE

FILE = "tag-profile.svg"
TITLE = "Practices in place by truth tag, across the fleet"
PLACES = 4
STATES = ("share", "none", "listed", "not_measured")
DASHED_TEXT = {
    "none": "not yet measured: no condition has this tag",
    "listed": "not counted: its conditions are listed, never counted",
    "not_measured": "not measured on any repository",
}

PAD = 16.0
MIN_W = 680.0
LINE_H = 20.0
ROW_H = 30.0
TRACK_W = 320.0
DOT_R = 6.0
CAP_H = 10.0
TICKS = (0, 25, 50, 75, 100)


class FleetTagRow(TypedDict):
    """One truth tag of ``fleet-data.json`` ``tag_profile`` (#940)."""

    tag: str  # one of lib.scoring.levels_fields.TRUTH_TAGS
    conditions: int  # the model's conditions with this tag; 0: none carries it yet
    countable: int  # of those, the ones the tally can count by grade
    repos: int  # repositories with a per-tag tally (scanned, with a maturity model)
    measured: int  # of those, the ones with a measured condition on this tag
    median: float | None  # the measured repositories' share met; None when none is measured
    p25: float | None
    p75: float | None


def tag_profile(model: MaturityModel | None,
                repo_tags: Iterable[Sequence[TagCell] | None]) -> list[FleetTagRow] | None:
    """The fleet's per-tag roll-up from each repository's per-tag tally (see the module doc)."""
    if model is None:
        return None
    tallies = [list(cells) for cells in repo_tags if cells is not None]
    out: list[FleetTagRow] = []
    for row in tag_rows(level_result(model, {}), model):
        shares = [cell["met"] / cell["measured"] for cells in tallies for cell in cells
                  if cell["tag"] == row["tag"] and cell["measured"] > 0]
        stats = ([round(percentile(shares, q), PLACES) for q in (50, 25, 75)] if shares
                 else [None, None, None])
        out.append({"tag": row["tag"], "conditions": row["conditions"], "countable": row["countable"],
                    "repos": len(tallies), "measured": len(shares),
                    "median": stats[0], "p25": stats[1], "p75": stats[2]})
    return out


def row_state(row: FleetTagRow) -> str:
    """How a row reads; only ``share`` draws a dot."""
    if row["conditions"] == 0:
        return "none"
    if row["measured"] > 0:
        return "share"
    if row["countable"] == 0:
        return "listed"
    return "not_measured"


def _plural(n: int) -> str:
    return f"{n} repository" if n == 1 else f"{n} repositories"


def row_label(row: FleetTagRow) -> str:
    """The tag title, its condition count and the repositories measured on it, of those with a tally."""
    return (f"{TRUTH_TAG_TITLES[row['tag']]} · {conditions_label(row['conditions'])} · "
            f"{row['measured']} of {_plural(row['repos'])}")


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def side_text(row: FleetTagRow) -> str:
    if row_state(row) != "share" or row["median"] is None or row["p25"] is None or row["p75"] is None:
        return DASHED_TEXT[row_state(row)]
    if row["p25"] == row["p75"]:
        return f"median {_pct(row['median'])}"
    return f"median {_pct(row['median'])}, middle half {_pct(row['p25'])} to {_pct(row['p75'])}"


def in_place(rows: Sequence[FleetTagRow]) -> str:
    measured = sum(row_state(r) == "share" for r in rows)
    return (f"{IN_PLACE} {measured} of the {len(rows)} truth tags measured on at least one repository, "
            "each shown with the median share met.")


def caption() -> str:
    return ("Each dot is the median share of a tag's measured conditions met across the repositories "
            "measured on it, and the whisker spans the middle half of them. A dashed row was not "
            "measured on any repository, never read as zero.")


def render(rows: Sequence[FleetTagRow], whys: Mapping[str, str]) -> str:
    """The fleet median tag profile strip SVG."""
    labels = [row_label(r) for r in rows]
    label_w = max(svg.text_width(label) for label in labels) + 16
    x0 = PAD + label_w
    x1 = x0 + TRACK_W
    side = [side_text(r) for r in rows]
    width = max(MIN_W, x1 + 16 + max(svg.text_width(s, 11.5) for s in side) + PAD)
    body = [svg.text(PAD, 28, TITLE, size=15, weight="600", cls="fg"),
            svg.text(PAD, 48, "Median share of each tag's measured conditions met, one row per truth tag",
                     size=12, cls="muted")]
    top = 64.0
    for tick in TICKS:
        body.append(svg.text(x0 + TRACK_W * tick / 100, top + 12, f"{tick}%", size=11, anchor="middle",
                             cls="muted"))
    y = top + 24
    bottom = y + ROW_H * len(rows)
    for tick in TICKS:
        x = x0 + TRACK_W * tick / 100
        body.append(f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x:.1f}" y2="{bottom:.1f}" class="s-rule" '
                    'stroke-width="1"/>')
    for row, label, note in zip(rows, labels, side):
        state = row_state(row)
        mid = y + ROW_H / 2
        parts = [f'<g data-tag="{row["tag"]}" data-state="{state}"><title>{svg.esc(whys[row["tag"]])}</title>',
                 svg.text(x0 - 12, mid + 4, label, anchor="end", cls="fg")]
        dash = "" if state == "share" else ' stroke-dasharray="4 4" data-mark="dashed"'
        parts.append(f'<line x1="{x0:.1f}" y1="{mid:.1f}" x2="{x1:.1f}" y2="{mid:.1f}" class="s-muted" '
                     f'stroke-width="1.5"{dash}/>')
        if state == "share" and row["median"] is not None and row["p25"] is not None and row["p75"] is not None:
            lo, hi = x0 + TRACK_W * row["p25"], x0 + TRACK_W * row["p75"]
            if hi > lo:
                parts.append(f'<g data-mark="whisker" class="s-fg" stroke-width="2">'
                             f'<line x1="{lo:.1f}" y1="{mid:.1f}" x2="{hi:.1f}" y2="{mid:.1f}"/>'
                             f'<line x1="{lo:.1f}" y1="{mid - CAP_H / 2:.1f}" x2="{lo:.1f}" y2="{mid + CAP_H / 2:.1f}"/>'
                             f'<line x1="{hi:.1f}" y1="{mid - CAP_H / 2:.1f}" x2="{hi:.1f}" y2="{mid + CAP_H / 2:.1f}"/>'
                             '</g>')
            dx = x0 + TRACK_W * row["median"]
            parts.append(f'<circle cx="{dx:.1f}" cy="{mid:.1f}" r="{DOT_R:.1f}" class="f-accent s-bg" '
                         'stroke-width="1.5" data-mark="dot"/>')
        parts.append(svg.text(x1 + 16, mid + 4, note, size=11.5, cls="fg" if state == "share" else "muted"))
        parts.append("</g>")
        body.append("".join(parts))
        y += ROW_H
    y += 12
    key = [(f'<circle cx="{PAD + 7:.1f}" cy="{{cy}}" r="{DOT_R:.1f}" class="f-accent s-bg" stroke-width="1.5"/>',
            "Median: the middle value of the repositories' shares of the tag's measured conditions met"),
           (f'<line x1="{PAD:.1f}" y1="{{cy}}" x2="{PAD + 14:.1f}" y2="{{cy}}" class="s-fg" stroke-width="2"/>',
            "Whisker: the middle half of the repositories, 25th to 75th percentile"),
           (f'<line x1="{PAD:.1f}" y1="{{cy}}" x2="{PAD + 14:.1f}" y2="{{cy}}" class="s-muted" '
            'stroke-width="1.5" stroke-dasharray="4 4"/>', "Not measured on any repository: no dot, never 0")]
    for paint, text in key:
        body.append(paint.format(cy=f"{y + 9:.1f}"))
        body.append(svg.text(PAD + 22, y + 13, text, size=12, cls="fg"))
        y += LINE_H
    limit = int((width - 2 * PAD) / svg.CHAR_W)
    notes = ["Row label: the tag, its condition count and the repositories measured on it, of those "
             "scanned with a per-tag tally.", caption()]
    for note_text, cls in zip(notes, ("muted", "fg")):
        for line in wrap(note_text, limit):
            y += LINE_H - 2
            body.append(svg.text(PAD, y, line, size=12, cls=cls))
    desc = f"{in_place(rows)} {caption()}"
    return svg.document(width, y + PAD, TITLE, desc, body, style=svg.theme_style())

