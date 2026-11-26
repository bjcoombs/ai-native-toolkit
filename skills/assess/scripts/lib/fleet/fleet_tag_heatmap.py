"""The estate-wide fleet tag heatmap: repositories by truth tag, under the median strip (#940).

``heatmap_rows(data)`` reads ``fleet-data.json`` alone: each repository's per-tag tally
(``repos[].tags``, #1073, the headline strip's cells from ``lib.scoring.level_tags.tag_cells``) and
its Step (``repos[].step``), never a second tally. One row per repository, every repository of
the run, sorted by Step (highest first, a repository with no Step last) and then by its key.
Under ``--full`` (``run.counts_only`` false) the rows group by unit first (#1331): the first view of
``fleet_units.VIEWS`` any repository is placed in, each repository under its placed unit
(``repos[].units``), labelled by the unit's name from ``views[].units[]`` (its key without the view
when the name is absent), groups in label order and a group of unplaced repositories last. Rows
group on the unit key, not the label, so two units sharing a name stay two groups. A
counts-only or hashed run keeps the plain Step order and draws no unit name or ``data-unit``.
One column per truth tag, every tag of
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

Each cell carries its tooltip values as ``data-tag-title`` and, when measured,
``data-percent-met`` (a whole percent), beside ``data-tag`` and ``data-cell`` (#1360), and the
tally its title prints as ``data-met``, ``data-measured``, ``data-verified``, ``data-not-measured``,
``data-hazards`` and ``data-declined``. Its
``<title>`` breaks the score down (#1331): met of measured, then verified, not measured, hazards
and declined from the repository's tally; a repository with no tally reads only "not measured".

Deterministic: fixed order, fixed formatting, no date; two renders are byte-identical. Golden
renders: ``tests/fixtures/fleet_diagrams/`` (``tests/fleet_tag_heatmap_fixture.py``). Tests:
``tests/test_fleet_tag_heatmap.py``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

import lib.fleet.fleet_svg as svg
from lib.svg_scope import SCOPES
from lib.assess_diagrams import wrap
from lib.fleet.fleet_units import VIEWS
from lib.scoring.level_result_types import TagCell
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
NO_UNIT = "No unit"


class HeatmapRow(TypedDict):
    """One repository of the heatmap."""

    label: str  # the key, or "repository N" under counts-only without hashing
    step: int | None
    shares: list[float | None]  # per tag of TRUTH_TAGS; None: not measured on this repository
    cells: list[TagCell | None]  # per tag of TRUTH_TAGS; None: the repository carries no tally for it
    unit: str | None  # the unit group label, under --full only; None: ungrouped or unplaced
    unit_key: str | None  # the unit the row groups on: names need not be unique


def _step_key(repo: Mapping[str, Any]) -> tuple[int, int, str]:
    step = repo.get("step")
    return (1, 0, str(repo["key"])) if step is None else (0, -int(step), str(repo["key"]))


def names_shown(run: Mapping[str, Any]) -> bool:
    """True when the run's keys may be drawn: ``--full``, or hashed ids."""
    return not run.get("counts_only", True) or bool(run.get("paths_hashed"))


def unit_labels(data: Mapping[str, Any]) -> tuple[str | None, dict[str, str]]:
    """The view rows group by and its unit labels by key: the first view of ``VIEWS`` any
    repository is placed in. ``(None, {})`` when no repository is placed in any view."""
    placed = {u.get("view") for r in data.get("repos") or [] for u in r.get("units") or []}
    view = next((v for v in VIEWS if v in placed), None)
    labels: dict[str, str] = {}
    for block in data.get("views") or []:
        if block.get("view") != view:
            continue
        for unit in block.get("units") or []:
            key = str(unit["key"])
            labels[key] = str(unit.get("name") or key.removeprefix(f"{view}/"))
    return view, labels


def _unit_of(repo: Mapping[str, Any], view: str | None, labels: Mapping[str, str]) -> tuple[str, str] | None:
    """The repository's ``(label, key)`` in ``view``; None when it is not placed there."""
    for placed in repo.get("units") or []:
        if placed.get("view") == view:
            key = str(placed["unit"])
            return labels.get(key, key.removeprefix(f"{view}/")), key
    return None


def heatmap_rows(data: Mapping[str, Any]) -> list[HeatmapRow]:
    """Every repository of the run, sorted by Step then key, with its per-tag shares; under
    ``--full``, grouped by unit first (unplaced repositories last)."""
    run = data.get("run") or {}
    show = names_shown(run)
    grouped = not run.get("counts_only", True)
    view, labels = unit_labels(data) if grouped else (None, {})
    repos = sorted(data.get("repos") or [], key=_step_key)
    units = {str(r["key"]): _unit_of(r, view, labels) if view else None for r in repos}
    if view:  # group on the key, so two units sharing a name stay apart; order by label, then key
        repos.sort(key=lambda r: (units[str(r["key"])] is None, units[str(r["key"])] or ("", "")))
    rows: list[HeatmapRow] = []
    for index, repo in enumerate(repos, start=1):
        by_tag = {c["tag"]: c for c in repo.get("tags") or []}
        cells: list[TagCell | None] = [by_tag.get(tag) for tag in TRUTH_TAGS]
        shares = [c["met"] / c["measured"] if c and c["measured"] > 0 else None for c in cells]
        rows.append({"label": str(repo["key"]) if show else f"repository {index}",
                     "step": repo.get("step"), "shares": shares, "cells": cells,
                     "unit": (units[str(repo["key"])] or (None, None))[0],
                     "unit_key": (units[str(repo["key"])] or (None, None))[1]})
    return rows


def tally_attrs(cell: TagCell | None) -> dict[str, int | None]:
    """The tally behind a cell's tooltip as data attributes; none for a repository with no tally."""
    if cell is None:
        return {}
    return {"met": cell["met"], "measured": cell["measured"], "verified": cell["verified"],
            "not-measured": cell["not_measured"], "hazards": cell["hazards"], "declined": cell["declined"]}


def cell_title(tag: str, cell: TagCell | None) -> str:
    """A cell's tooltip: the score, then its breakdown from the repository's tally."""
    title = TRUTH_TAG_TITLES[tag]
    if cell is None:
        return f"{title}: not measured"
    head = (f"{round(cell['met'] / cell['measured'] * 100)}% met" if cell["measured"] > 0 else "not measured")
    return (f"{title}: {head}. {cell['met']} of {cell['measured']} measured conditions met, {cell['verified']} verified, "
            f"{cell['not_measured']} not measured, {cell['hazards']} "
            f"{'hazard' if cell['hazards'] == 1 else 'hazards'}, {cell['declined']} declined")


def uncarried(data: Mapping[str, Any]) -> set[str]:
    """The tags no condition of the model carries yet (``tag_profile`` ``conditions`` 0)."""
    return {r["tag"] for r in data.get("tag_profile") or [] if r.get("conditions") == 0}


def in_place(rows: Sequence[HeatmapRow]) -> str:
    measured = sum(s is not None for r in rows for s in r["shares"])
    return (f"{IN_PLACE} {measured} repository and tag {'pair' if measured == 1 else 'pairs'} measured, "
            f"over {len(rows)} {'repository' if len(rows) == 1 else 'repositories'}.")


def caption(grouped: bool = False) -> str:
    order = ("Rows group by unit, then run from the highest Step down" if grouped
             else "Rows run from the highest Step down")
    return ("Each cell is that repository's share of the tag's measured conditions met, darker for more. "
            f"A hatched cell was not measured there, never read as zero. {order}, "
            "and a Step of ? was not measured.")


def render(rows: Sequence[HeatmapRow], none_yet: set[str]) -> str:
    """The heatmap SVG."""
    labels = [svg.truncate(r["label"], MAX_LABEL, keep_tail=True) for r in rows]
    grouped = any(r["unit"] is not None for r in rows)
    label_w = max([svg.text_width(label) for label in labels] + [svg.text_width("Repository")]) + 16
    x0 = PAD + label_w + STEP_W
    width = max(680.0, x0 + CELL_W * len(TRUTH_TAGS) + PAD)
    body = [svg.text(PAD, 28, TITLE, size=15, weight="600", cls="fg"),
            svg.text(PAD, 48, "One row per repository, one column per truth tag, by "
                     + ("unit and Step" if grouped else "Step"), size=12, cls="muted")]
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
    group: str | None = None
    for row, label in zip(rows, labels):
        name = row["unit"]
        if name is not None and row["unit_key"] != group:
            group = row["unit_key"]
            body.append(svg.text(PAD, y + 14, svg.truncate(name, MAX_LABEL * 2), size=11.5, weight="600",
                                 cls="fg", tooltip=name if len(name) > MAX_LABEL * 2 else None))
            y += CELL_H
        elif name is None and group is not None:
            group = None
            body.append(svg.text(PAD, y + 14, NO_UNIT, size=11.5, weight="600", cls="muted"))
            y += CELL_H
        unit_attr = svg.data_attrs({"unit": row["unit"]})
        parts = [f'<g data-row="{svg.esc(label)}"{unit_attr}>',
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
                             f'data-tag="{tag}" data-cell="not_measured"'
                             f'{svg.data_attrs({"tag-title": TRUTH_TAG_TITLES[tag], **tally_attrs(row["cells"][i])})}>'
                             f'<title>{svg.esc(cell_title(tag, row["cells"][i]))}</title></rect>')
                continue
            step = round(share * svg.TOP_STEP)
            pct = f"{round(share * 100)}%"
            attrs = svg.data_attrs({"tag-title": TRUTH_TAG_TITLES[tag], "percent-met": round(share * 100),
                                    **tally_attrs(row["cells"][i])})
            parts.append(f'<rect x="{x + 1:.1f}" y="{y + 1:.1f}" width="{CELL_W - 2:.1f}" height="{CELL_H - 2:.1f}" '
                         f'fill="{svg.LEVEL_RAMP[step]}" data-tag="{tag}" data-cell="share"{attrs}>'
                         f'<title>{svg.esc(cell_title(tag, row["cells"][i]))}</title></rect>')
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
    for line in wrap(caption(grouped), int((width - 2 * PAD) / svg.CHAR_W)):
        y += LINE_H - 2
        body.append(svg.text(PAD, y, line, size=12, cls="fg"))
    desc = f"{in_place(rows)} {caption(grouped)}"
    return svg.document(width, y + PAD, TITLE, desc, body, scope=SCOPES["tag-heatmap"], style=svg.theme_style(), defs=svg.THEMED_HATCH)
