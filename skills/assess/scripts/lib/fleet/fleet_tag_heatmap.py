"""The estate-wide fleet tag heatmap: repositories by truth tag, under the median strip (#940).

``heatmap_rows(data)`` reads ``fleet-data.json`` alone: each repository's per-tag tally
(``repos[].tags``, #1073, the headline strip's cells from ``lib.scoring.level_tags.tag_cells``) and
its Step (``repos[].step``), never a second tally. One row per repository, every repository of
the run, sorted by Step (highest first, a repository with no Step last) and then by its key;
never grouped by unit, and no unit or group name is drawn. One column per truth tag, every tag of
``TRUTH_TAGS`` in that fixed order. A cell is the repository's share of the tag's measured
conditions met (``met`` over ``measured``), shaded on the fleet's growth ramp
(``fleet_svg.LEVEL_RAMP``, nine steps, ``round(share * 8)``) and printed as a percentage. A
cell with no measured condition (``measured`` 0, a tag the repository's result does not carry,
or a repository with no tally) is hatched, never shaded, so it never reads as 0; a column no
condition of the model carries yet (``tag_profile`` ``conditions`` 0) says so in its header.

**Row identity.** A row is labelled with the repository key only when the run kept names
(``run.counts_only`` false, ``--full``) or hashed them (``run.paths_hashed``, ``--hash-salt``:
the key is already the hash). A counts-only run without hashing labels rows by their place in
the sort (``repository 3``), so the picture names no repository; names appear only with
``--full``.

Deterministic: fixed order, fixed formatting, no date; two renders are byte-identical. Golden
renders: ``tests/fixtures/fleet_diagrams/`` (``tests/fleet_tag_heatmap_fixture.py``). Tests:
``tests/test_fleet_tag_heatmap.py``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

import lib.fleet.fleet_svg as svg
from lib.assess_diagrams import wrap
from lib.scoring.levels_fields import TRUTH_TAG_TITLES, TRUTH_TAGS
from lib.vocabulary import IN_PLACE

FILE = "tag-heatmap.svg"
TITLE = "Truth tags by repository"
QUESTION = "Which tags have the most room to grow across the estate, and on which repositories?"
PAD = 16.0
CELL_W = 72.0
CELL_H = 20.0
STEP_W = 56.0
MAX_LABEL = 32
LINE_H = 20.0


class HeatmapRow(TypedDict):
    """One repository of the heatmap."""

    label: str  # the key, or "repository N" under counts-only without hashing
    step: int | None
    shares: list[float | None]  # per tag of TRUTH_TAGS; None: not measured on this repository


def _step_key(repo: Mapping[str, Any]) -> tuple[int, int, str]:
    step = repo.get("step")
    return (1, 0, str(repo["key"])) if step is None else (0, -int(step), str(repo["key"]))


def names_shown(run: Mapping[str, Any]) -> bool:
    """True when the run's keys may be drawn: ``--full``, or hashed ids."""
    return not run.get("counts_only", True) or bool(run.get("paths_hashed"))


def heatmap_rows(data: Mapping[str, Any]) -> list[HeatmapRow]:
    """Every repository of the run, sorted by Step then key, with its per-tag shares."""
    show = names_shown(data.get("run") or {})
    rows: list[HeatmapRow] = []
    for index, repo in enumerate(sorted(data.get("repos") or [], key=_step_key), start=1):
        cells = {c["tag"]: c for c in repo.get("tags") or []}
        shares: list[float | None] = []
        for tag in TRUTH_TAGS:
            cell = cells.get(tag)
            shares.append(cell["met"] / cell["measured"] if cell and cell["measured"] > 0 else None)
        rows.append({"label": str(repo["key"]) if show else f"repository {index}",
                     "step": repo.get("step"), "shares": shares})
    return rows


def uncarried(data: Mapping[str, Any]) -> set[str]:
    """The tags no condition of the model carries yet (``tag_profile`` ``conditions`` 0)."""
    return {r["tag"] for r in data.get("tag_profile") or [] if r.get("conditions") == 0}


def in_place(rows: Sequence[HeatmapRow]) -> str:
    measured = sum(s is not None for r in rows for s in r["shares"])
    return (f"{IN_PLACE} {measured} repository and tag {'pair' if measured == 1 else 'pairs'} measured, "
            f"over {len(rows)} {'repository' if len(rows) == 1 else 'repositories'}.")


def caption() -> str:
    return ("Each cell is that repository's share of the tag's measured conditions met, darker for more. "
            "A hatched cell was not measured there, never read as zero. Rows run from the highest Step down, "
            "and a Step of ? was not measured.")


def render(rows: Sequence[HeatmapRow], none_yet: set[str]) -> str:
    """The heatmap SVG."""
    labels = [svg.truncate(r["label"], MAX_LABEL, keep_tail=True) for r in rows]
    label_w = max([svg.text_width(label) for label in labels] + [svg.text_width("Repository")]) + 16
    x0 = PAD + label_w + STEP_W
    width = max(680.0, x0 + CELL_W * len(TRUTH_TAGS) + PAD)
    body = [svg.text(PAD, 28, TITLE, size=15, weight="600", cls="fg"),
            svg.text(PAD, 48, "One row per repository, one column per truth tag, by Step", size=12, cls="muted")]
    head = 64.0
    body.append(svg.text(PAD + label_w + STEP_W / 2, head + 28, "Step", size=11, anchor="middle", cls="muted"))
    for i, tag in enumerate(TRUTH_TAGS):
        cx = x0 + CELL_W * i + CELL_W / 2
        words = TRUTH_TAG_TITLES[tag].split(" ", 1)
        for j, word in enumerate(words):
            body.append(svg.text(cx, head + 14 + 13 * j, word, size=11, anchor="middle", cls="fg"))
        if tag in none_yet:
            body.append(svg.text(cx, head + 40, "none yet", size=10, anchor="middle", cls="muted"))
    y = head + 48
    for row, label in zip(rows, labels):
        parts = [f'<g data-row="{svg.esc(label)}">',
                 svg.text(PAD + label_w - 8, y + 14, label, size=11.5, anchor="end", cls="fg",
                          tooltip=row["label"] if label != row["label"] else None),
                 svg.text(PAD + label_w + STEP_W / 2, y + 14, "?" if row["step"] is None else str(row["step"]),
                          size=11.5, anchor="middle", cls="muted" if row["step"] is None else "fg")]
        for i, share in enumerate(row["shares"]):
            x = x0 + CELL_W * i
            tag = TRUTH_TAGS[i]
            if share is None:
                parts.append(f'<rect x="{x + 1:.1f}" y="{y + 1:.1f}" width="{CELL_W - 2:.1f}" '
                             f'height="{CELL_H - 2:.1f}" fill="{svg.HATCH_FILL}" class="s-rule" stroke-width="1" '
                             f'data-tag="{tag}" data-cell="not_measured"><title>{TRUTH_TAG_TITLES[tag]}: '
                             'not measured</title></rect>')
                continue
            step = round(share * svg.TOP_STEP)
            pct = f"{round(share * 100)}%"
            parts.append(f'<rect x="{x + 1:.1f}" y="{y + 1:.1f}" width="{CELL_W - 2:.1f}" height="{CELL_H - 2:.1f}" '
                         f'fill="{svg.LEVEL_RAMP[step]}" data-tag="{tag}" data-cell="share">'
                         f'<title>{TRUTH_TAG_TITLES[tag]}: {pct} met</title></rect>')
            parts.append(svg.text(x + CELL_W / 2, y + 14, pct, size=11, anchor="middle",
                                  fill=svg.STEP_TEXT[step]))
        parts.append("</g>")
        body.append("".join(parts))
        y += CELL_H
    y += 12
    ramp_x = PAD
    for step, colour in enumerate(svg.LEVEL_RAMP):
        body.append(f'<rect x="{ramp_x + step * 18:.1f}" y="{y:.1f}" width="18.0" height="12.0" fill="{colour}"/>')
    body.append(svg.text(ramp_x + 18 * len(svg.LEVEL_RAMP) + 8, y + 11, "0% to 100% of measured conditions met",
                         size=12, cls="fg"))
    y += LINE_H
    body.append(f'<rect x="{PAD:.1f}" y="{y:.1f}" width="18.0" height="12.0" fill="{svg.HATCH_FILL}" '
                'class="s-rule" stroke-width="1"/>')
    body.append(svg.text(PAD + 26, y + 11, "Not measured on that repository: hatched, never 0", size=12, cls="fg"))
    y += LINE_H - 4
    for line in wrap(caption(), int((width - 2 * PAD) / svg.CHAR_W)):
        y += LINE_H - 2
        body.append(svg.text(PAD, y, line, size=12, cls="fg"))
    desc = f"{in_place(rows)} {caption()}"
    return svg.document(width, y + PAD, TITLE, desc, body, style=svg.theme_style(), defs=svg.THEMED_HATCH)
