"""The package map, an /assess diagram of which packages import which (#942).

``package_diagrams(ctx)`` returns it as an ``AssessDiagram``
(``lib/run_context_types_diagrams.py``): ``package-map.svg``, or the one-line reason it was
not drawn; it never raises. ``lib/assess_diagrams_review.py`` puts it in ``build_all`` (after
the evidence waffle), ``ALL_FILES`` and, as ``package-map.counts-only.svg``, in
``counts_only_diagrams``, so a counts-only run draws it from the counts-only document. Its
labels are hashed only under ``--hash-paths`` or ``--hash-salt``; under ``--counts-only``
alone package ids are path class and stay in clear, as in ``run-context.counts-only.json``.

The map is an adjacency matrix of the first ``TOP`` packages of ``structure.packages`` (the
block's order: most lines of code first). Row ``i`` imports from column ``j``; a cell's shade
is the number of import edges from the row's package into the column's (``shade_step``: one
ramp step per power of two, never the near-white first step), and a corner notch marks a
cell whose imports include a front-door bypass (``notch``). The ramp fills are fixed colours,
so the notch is drawn in the themed foreground with a themed background edge and reads on
every step in both colour schemes. Hatch means unknown here as in every diagram, so the
bypass mark is never a hatch. Each row
carries a bar of its package's lines of code. A cell between two drawn packages with no
recorded pair is empty (no imports), unless ``structure.package_edges`` was capped
(``package_edge_count`` above the list's length): the pair may then be one the block did not
keep, so the cell is hatched as unknown, never read as zero.

What the matrix leaves out is stated, never dropped (``package_view``): the packages past
the top, the recorded imports to or from them, the imports the block does not record (its
200-package and 400-pair caps), and the front-door bypasses outside the drawn cells. Drawn
plus stated imports equal ``cross_package_edges``; drawn plus stated bypasses equal
``burrow_edge_count``.

Not drawn, with the reason: no ``structure`` block, an unavailable one (its reason code), a
block from before the package data (no ``packages`` key), a single package, or no import
across a package boundary. Deterministic: the block's order, integer shading, fixed
formatting. Golden renders: ``tests/fixtures/assess_diagrams/`` (``make regen``, or
``uv run --locked python tests/assess_diagrams_packages_fixture.py`` from ``skills/assess``).
Tests: ``tests/test_assess_diagrams_packages.py``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import lib.fleet.fleet_svg as svg
from lib.assess_diagrams import wrap
from lib.headline import reason_word
from lib.reason_codes import reason_code
from lib.run_context_types_diagrams import AssessDiagram, PackageView
from lib.vocabulary import IN_PLACE

PACKAGE_FILE = "package-map.svg"
PACKAGE_COUNTS_ONLY_FILE = "package-map.counts-only.svg"
TITLE = "Packages and their imports"
TOP = 20

PAD = 16.0
CELL = 18.0
GAP = 2.0
LINE_H = 20.0
LABEL_CHARS = 28
BAR_W = 56.0
MIN_W = 680.0
HEAD_H = 92.0
NOTCH = 8.0


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _entry() -> AssessDiagram:
    return {"file": PACKAGE_FILE, "title": TITLE,
            "in_place": f"{IN_PLACE} this run's scans, gathered with no model call.",
            "caption": "", "svg": None, "skipped": None}


def shade_step(imports: int) -> int:
    """The ``fleet_svg.LEVEL_RAMP`` step for a cell: 2 for one import, then one step per power
    of two, capped at the top step (integer only, so every platform draws the same bytes)."""
    return min(svg.TOP_STEP, 1 + max(1, imports).bit_length())


def _bucket(step: int) -> str:
    """The import counts a ramp step stands for, as a key label."""
    low = 1 << (step - 2)
    if step == svg.TOP_STEP:
        return f"{low} or more"
    high = (low << 1) - 1
    return str(low) if low == high else f"{low} to {high}"


def package_view(block: Mapping[str, Any]) -> PackageView:
    """The drawn packages, their recorded pairs and every count the matrix leaves out."""
    packages = [p for p in block.get("packages") or [] if isinstance(p, Mapping)]
    drawn = packages[:TOP]
    ids = [str(p["id"]) for p in drawn]
    inside = set(ids)
    cells: dict[str, dict[str, list[int]]] = {}
    recorded = recorded_bypass = drawn_imports = drawn_bypass = 0
    edges = [e for e in block.get("package_edges") or [] if isinstance(e, Mapping)]
    for edge in edges:
        imports, bypass = int(edge.get("imports") or 0), int(edge.get("bypass") or 0)
        recorded += imports
        recorded_bypass += bypass
        source, target = str(edge.get("from")), str(edge.get("to"))
        if source in inside and target in inside:
            cells.setdefault(source, {})[target] = [imports, bypass]
            drawn_imports += imports
            drawn_bypass += bypass
    cross = int(block.get("cross_package_edges") or 0)
    return {
        "ids": ids, "loc": [int(p.get("loc") or 0) for p in drawn], "cells": cells,
        "package_count": int(block.get("package_count") or len(packages)),
        "cross_imports": cross, "drawn_imports": drawn_imports,
        "past_top": recorded - drawn_imports, "unrecorded": max(0, cross - recorded),
        "burrows": int(block.get("burrow_edge_count") or 0), "drawn_bypass": drawn_bypass,
        "edge_cap_applied": int(block.get("package_edge_count") or 0) > len(edges),
    }


def notes(view: PackageView) -> list[str]:
    """The lines that state what the matrix leaves out."""
    shown, total = len(view["ids"]), view["package_count"]
    out = [f"Drawn: the {_plural(shown, 'package')} with the most lines of code"
           + (f", of {total}." if total > shown else ".")
           + f" {view['drawn_imports']} of {_plural(view['cross_imports'], 'cross-package import')}"
           " are between them."]
    if view["past_top"]:
        out.append(f"{_plural(view['past_top'], 'import')} to or from packages past the "
                   f"top {shown} {'is' if view['past_top'] == 1 else 'are'} not drawn.")
    if view["unrecorded"]:
        out.append(f"{_plural(view['unrecorded'], 'import')} between packages the run does not "
                   "record (it keeps the 200 largest packages and the 400 busiest pairs).")
    if view["edge_cap_applied"]:
        out.append("The busiest-pairs limit applied, so a hatched cell may hold imports the "
                   "run did not keep.")
    out.append(f"Front-door bypasses: {view['drawn_bypass']} of {view['burrows']} are between "
               "the drawn packages.")
    return out


def package_caption() -> str:
    """The one-line caption: what to take from the package map."""
    return ("Each row imports from the columns it shades, darker for more imports; a corner "
            "mark flags imports that reach past the target package's front door.")


def package_in_place(view: PackageView) -> str:
    return (f"{IN_PLACE} an import graph of {_plural(view['package_count'], 'package')} with "
            f"{_plural(view['cross_imports'], 'cross-package import')}, "
            f"{view['cross_imports'] - view['burrows']} of them through a front door.")


def _cell(x: float, y: float, row: str, col: str, view: PackageView, tip: str) -> list[str]:
    recorded = view["cells"].get(row, {}).get(col)
    hover = f"<title>{svg.esc(tip)}</title>"
    box = f'x="{x:.1f}" y="{y:.1f}" width="{CELL:.1f}" height="{CELL:.1f}"'
    if row == col:
        return [f'<rect {box} class="f-rule" data-cell="self">{hover}</rect>']
    if recorded is None:
        if view["edge_cap_applied"]:
            return [f'<rect {box} fill="{svg.HATCH_FILL}" class="s-rule" stroke-width="1" '
                    f'data-cell="unknown">{hover}</rect>']
        return [f'<rect {box} class="f-bg s-rule" stroke-width="1" data-cell="none">{hover}</rect>']
    imports, bypass = recorded
    step = shade_step(imports)
    parts = [f'<rect {box} fill="{svg.LEVEL_RAMP[step]}" class="s-rule" stroke-width="1" '
             f'data-cell="imports" data-imports="{imports}" data-bypass="{bypass}">{hover}</rect>']
    if bypass:
        parts.append(notch(x + CELL, y, mark=True))
    return parts


def notch(right: float, top: float, mark: bool = False) -> str:
    """The bypass mark: a corner triangle in the themed foreground with a themed background
    edge, so it reads on every ramp step in both colour schemes (the fill is not themed)."""
    size = NOTCH
    tag = ' data-mark="bypass"' if mark else ""
    return (f'<polygon points="{right - size:.1f},{top:.1f} {right:.1f},{top:.1f} '
            f'{right:.1f},{top + size:.1f}" class="f-fg s-bg" stroke-width="1.5"{tag}/>')


def _tip(row: str, col: str, view: PackageView) -> str:
    if row == col:
        return f"{row}: imports inside one package are not drawn"
    recorded = view["cells"].get(row, {}).get(col)
    if recorded is None:
        return (f"{row} to {col}: not recorded (the busiest-pairs limit applied)"
                if view["edge_cap_applied"] else f"{row} to {col}: no imports")
    imports, bypass = recorded
    past = f", {bypass} past the front door" if bypass else ""
    return f"{row} to {col}: {_plural(imports, 'import')}{past}"


def _key(x: float, y: float, steps: Sequence[int], unknown: bool) -> tuple[list[str], float]:
    """Swatches with labels, one per line; each swatch is a function of its top edge."""
    def box(at: float, paint: str) -> str:
        return f'<rect x="{x:.1f}" y="{at:.1f}" width="14.0" height="14.0" {paint}/>'

    entries: list[tuple[list[str], str]] = []
    for s in steps:
        entries.append(([f'fill="{svg.LEVEL_RAMP[s]}" class="s-rule" stroke-width="1"'],
                        f"{_bucket(s)} {'import' if _bucket(s) == '1' else 'imports'}"))
    if not unknown:  # with the pair cap applied no empty cell is drawn
        entries.append((['class="f-bg s-rule" stroke-width="1"'], "No imports"))
    entries.append(([f'fill="{svg.LEVEL_RAMP[5]}" class="s-rule" stroke-width="1"', "notch"],
                    "Corner mark: some imports bypass the front door"))
    if unknown:
        entries.append(([f'fill="{svg.HATCH_FILL}" class="s-rule" stroke-width="1"'],
                        "Not recorded: the busiest-pairs limit applied"))
    parts: list[str] = []
    for index, (paints, label) in enumerate(entries):
        at = y + index * LINE_H + 2
        parts.append(box(at, paints[0]))
        if "notch" in paints:
            parts.append(notch(x + 14, at))
        parts.append(svg.text(x + 22, at + 11, label, size=12, cls="fg"))
    return parts, len(entries) * LINE_H


def render_package_map(block: Mapping[str, Any]) -> str:
    """The package-map SVG for a ``structure`` block with two or more packages."""
    view = package_view(block)
    ids, count = view["ids"], len(view["ids"])
    labels = [f"{i + 1:>2} {svg.truncate(pid, LABEL_CHARS, keep_tail=True)}" for i, pid in enumerate(ids)]
    label_w = max(svg.text_width(label) for label in labels) + 12
    bar_x = PAD + label_w
    grid_x = bar_x + BAR_W + 12
    step = CELL + GAP
    width = max(MIN_W, grid_x + count * step + PAD)
    top_loc = max(view["loc"]) or 1
    body = [svg.text(PAD, 28, TITLE, size=15, weight="600", cls="fg"),
            svg.text(PAD, 48, "Row imports from column; columns numbered as rows", size=12, cls="muted"),
            svg.text(bar_x, HEAD_H - 8, "lines", size=11, cls="muted")]
    for j in range(count):
        body.append(svg.text(grid_x + j * step + CELL / 2, HEAD_H - 8, str(j + 1), size=10,
                             anchor="middle", cls="muted"))
    for i, row in enumerate(ids):
        y = HEAD_H + i * step
        cut = labels[i] != f"{i + 1:>2} {row}"
        body.append(svg.text(bar_x - 10, y + CELL - 5, labels[i], size=11.5, anchor="end", cls="fg",
                             tooltip=row if cut else None))
        bar = max(1.0, BAR_W * view["loc"][i] / top_loc)
        body.append(f'<rect x="{bar_x:.1f}" y="{y + 4:.1f}" width="{bar:.1f}" height="{CELL - 8:.1f}" '
                    f'class="f-accent" data-loc="{view["loc"][i]}"><title>{svg.esc(row)}: '
                    f'{view["loc"][i]} lines of code</title></rect>')
        for j, col in enumerate(ids):
            body += _cell(grid_x + j * step, y, row, col, view, _tip(row, col, view))
    y = HEAD_H + count * step + 14
    steps = sorted({shade_step(v[0]) for targets in view["cells"].values() for v in targets.values()})
    key_parts, key_h = _key(PAD, y, steps, view["edge_cap_applied"])
    body += key_parts
    y += key_h + 6
    limit = int((width - 2 * PAD) / svg.CHAR_W)
    for note in notes(view):
        for line in wrap(note, limit):
            y += LINE_H - 4
            body.append(svg.text(PAD, y, line, size=11.5, cls="muted"))
    caption = package_caption()
    y += 6
    for line in wrap(caption, limit):
        y += LINE_H - 2
        body.append(svg.text(PAD, y, line, size=12, cls="fg"))
    return svg.document(width, y + PAD, TITLE, f"{package_in_place(view)} {caption}", body,
                        defs=svg.THEMED_HATCH)


def _package_map(ctx: Mapping[str, Any]) -> AssessDiagram:
    entry = _entry()
    block = ctx.get("structure")
    if not isinstance(block, Mapping):
        entry["skipped"] = "the run has no import graph data."
    elif block.get("available") is False:
        entry["skipped"] = f"not measured here: {reason_word(reason_code(block.get('reason')))}."
    elif not isinstance(block.get("packages"), list):
        entry["skipped"] = "this run's import graph data has no package list."
    elif len(block["packages"]) < 2:
        entry["skipped"] = "the import graph has one package, so no import crosses a package boundary."
    elif not block.get("cross_package_edges"):
        entry["skipped"] = "no import crosses a package boundary."
    else:
        view = package_view(block)
        entry["in_place"] = package_in_place(view)
        entry["caption"] = package_caption()
        entry["svg"] = render_package_map(block)
    return entry


def package_diagrams(ctx: Mapping[str, Any]) -> list[AssessDiagram]:
    """The package map for one run context; never raises."""
    try:
        return [_package_map(ctx)]
    except Exception as e:  # noqa: BLE001 - a diagram never stops a completed run
        entry = _entry()
        entry["skipped"] = f"the renderer raised {type(e).__name__}."
        return [entry]
