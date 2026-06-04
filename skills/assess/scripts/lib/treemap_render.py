"""Shared treemap layout + SVG primitives.

The code heatmap (``complexity-treemap.py``) and the docs-staleness heatmap
(``docs-staleness-treemap.py``) use the *same visual grammar* -- size, hue,
saturation, squarified layout, hover tooltips -- but with opposite risk models
(code red = "hard to change safely"; docs red = "actively misleading"). So the
mechanical parts live here and each script keeps only its own colour mapping.

Heavy deps (matplotlib/squarify/numpy) are imported here, so this module must
only be imported by the treemap scripts -- never by the deterministic core,
which runs with networkx alone.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# `squarify` is imported lazily inside layout() so light consumers of the colour
# helpers (rgba_to_hex / blend_to_grey / adaptive_cap) - e.g. the doc-graph
# renderer - don't have to depend on it.


@dataclass
class Node:
    name: str
    size: int = 0
    color: tuple = (0.5, 0.5, 0.5, 1.0)
    loc: int = 0
    # Estimated token count (chars/4). The code heatmap sizes blocks by this so
    # the layout reflects context-window burden, not line count; ``loc`` stays
    # the file's real line count for the tooltip. 0 means "no token signal"
    # (the docs heatmap, which sizes by loc and renders its own tooltip2).
    est_tokens: int = 0
    metric: float = 0.0
    aux_metric: float = 0.0
    aux_label: str = ""
    rel_path: str = ""
    children: list["Node"] = field(default_factory=list)
    is_file: bool = False
    # Optional display overrides used by the docs heatmap. When unset the code
    # heatmap's default formatting applies, so its output is unchanged.
    tooltip2: str = ""
    label_size_text: str = ""
    label_metric_text: str = ""
    # Survivor-density overlay (code heatmap only). "" = no overlay, "diag" =
    # diagonal hatch (>30% mutants survive), "cross" = cross-hatch (>50%).
    # A hatched block reads as "covered but unpinned" so it stops rendering as
    # safe green. Unset everywhere else, so other heatmaps are unchanged.
    hatch: str = ""


def build_tree(files_with_color, root: Path,
               aux_data: dict[Path, int] | None = None,
               aux_label: str = "",
               node_overrides: dict[Path, dict] | None = None,
               size_by: dict[Path, int] | None = None) -> Node:
    """Build the directory tree of Nodes. `files_with_color` is a list of
    (path, size, metric, source, color). `node_overrides` optionally maps a
    file path to a dict of extra Node fields (tooltip2, label_* ...).

    `size_by` optionally overrides the block *area* per file (path -> size)
    while `size` from the tuple is preserved as the node's `loc`. The code
    heatmap passes estimated token counts here so blocks are sized by
    context-window burden; `loc` stays the real line count for the tooltip.
    When `size_by` is None the area is the tuple's size (unchanged - the docs
    heatmap path)."""
    rootnode = Node(name=root.name)
    by_path: dict[Path, Node] = {root: rootnode}
    for path, size, metric, _src, color in files_with_color:
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        parent = rootnode
        cur = root
        for part in rel.parts[:-1]:
            cur = cur / part
            if cur not in by_path:
                n = Node(name=part)
                by_path[cur] = n
                parent.children.append(n)
            parent = by_path[cur]
        aux_val = float(aux_data.get(path, 0)) if aux_data else 0.0
        area = size_by.get(path, size) if size_by else size
        est_tokens = size_by.get(path, 0) if size_by else 0
        leaf = Node(
            name=rel.parts[-1], size=area, color=color,
            loc=size, est_tokens=est_tokens, metric=metric, aux_metric=aux_val,
            aux_label=aux_label, rel_path=str(rel), is_file=True,
        )
        if node_overrides and path in node_overrides:
            for k, v in node_overrides[path].items():
                setattr(leaf, k, v)
        parent.children.append(leaf)

    def roll(n: Node) -> int:
        if n.is_file:
            return n.size
        n.size = sum(roll(c) for c in n.children)
        return n.size
    roll(rootnode)
    return rootnode


def layout(node: Node, x: float, y: float, w: float, h: float,
           out: list) -> None:
    import squarify
    if node.is_file:
        out.append((x, y, w, h, node))
        return
    kids = sorted([c for c in node.children if c.size > 0],
                  key=lambda c: -c.size)
    if not kids or w <= 0 or h <= 0:
        return
    sizes = [c.size for c in kids]
    norm = squarify.normalize_sizes(sizes, w, h)
    placed = squarify.squarify(norm, x, y, w, h)
    for child, r in zip(kids, placed):
        layout(child, r["x"], r["y"], r["dx"], r["dy"], out)


def rgba_to_hex(rgba: tuple) -> str:
    r, g, b = rgba[0], rgba[1], rgba[2]
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


GREY = (0.82, 0.82, 0.84)  # cool light grey for "stable" / no recent churn


def blend_to_grey(rgba: tuple, factor: float) -> tuple:
    """factor=0 returns full grey, factor=1 returns the original colour."""
    factor = max(0.0, min(1.0, factor))
    return tuple(
        GREY[i] + (rgba[i] - GREY[i]) * factor for i in range(3)
    ) + (1.0,)


def adaptive_cap(values: list[float]) -> tuple[float, str]:
    """Pick a sensible cap: max for well-behaved data, p95 for outlier-heavy."""
    if not values:
        return 1.0, "max"
    mx = float(max(values))
    if mx == 0:
        return 1.0, "max"
    p95 = float(np.percentile(values, 95))
    if p95 > 0 and mx > 5 * p95:
        return p95, "p95 (outlier-suppressed)"
    return mx, "max"


# Extra band drawn below the treemap to key the survivor-density overlay.
SURVIVOR_LEGEND_H = 84.0

# SVG <pattern> defs for the survivor-density overlay. Dark, semi-transparent
# strokes read on any OrRd fill without relying on hue, so the hatch is
# distinguishable by texture alone (colour-blind safe). "diag" = single
# diagonal hatch (>30% survivors), "cross" = cross-hatch (>50%, severe).
_SURVIVOR_DEFS = (
    '<defs>'
    '<pattern id="survivor-diag" patternUnits="userSpaceOnUse" '
    'width="7" height="7" patternTransform="rotate(45)">'
    '<line x1="0" y1="0" x2="0" y2="7" stroke="#1a1a1a" '
    'stroke-width="1" stroke-opacity="0.55"/></pattern>'
    '<pattern id="survivor-cross" patternUnits="userSpaceOnUse" '
    'width="6" height="6">'
    '<path d="M0,0 l6,6 M6,0 l-6,6" stroke="#1a1a1a" '
    'stroke-width="1" stroke-opacity="0.6"/></pattern>'
    '</defs>'
)


def _survivor_legend_parts(W: float, H: float) -> list[str]:
    """Legend band keyed under the treemap, explaining the hatch overlay.
    Drawn only when the overlay is active, so a run with no survivor data
    keeps the original full-canvas treemap untouched."""
    rows = [
        ("survivor-diag",
         "&gt;30% survivor density - covered but unpinned "
         "(tests run this code without constraining it)"),
        ("survivor-cross",
         "&gt;50% survivor density - severe; most mutations survive the suite"),
    ]
    sw = 16.0
    parts = [
        f'<line x1="0" y1="{H:.1f}" x2="{W:.1f}" y2="{H:.1f}" '
        f'stroke="#cccccc" stroke-width="1"/>',
        f'<text x="14" y="{H + 18:.1f}" font-size="13" text-anchor="start" '
        f'font-weight="bold">Survivor-density overlay (hatched = covered but '
        f'unpinned)</text>',
    ]
    row_y = H + 34.0
    for pid, label in rows:
        cy = row_y + sw / 2
        parts.append(
            f'<rect x="14" y="{row_y:.1f}" width="{sw:.0f}" height="{sw:.0f}" '
            f'fill="#f2f2f2" stroke="#888888" stroke-width="0.5"/>'
        )
        parts.append(
            f'<rect x="14" y="{row_y:.1f}" width="{sw:.0f}" height="{sw:.0f}" '
            f'fill="url(#{pid})" stroke="none" pointer-events="none"/>'
        )
        parts.append(
            f'<text x="{14 + sw + 10:.0f}" y="{cy:.1f}" font-size="12" '
            f'text-anchor="start">{label}</text>'
        )
        row_y += sw + 6.0
    return parts


def write_svg(rects: list, root: Path, W: float, H: float,
              out_path: Path, show_labels: bool,
              metric_label: str,
              show_survivor_legend: bool = False) -> None:
    label_threshold = (W * H) / 200
    has_hatch = any(node.hatch for _x, _y, _w, _h, node in rects)
    total_h = H + (SURVIVOR_LEGEND_H if show_survivor_legend else 0.0)
    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W:.0f} {total_h:.0f}" '
        f'width="{W:.0f}" height="{total_h:.0f}" '
        f'preserveAspectRatio="xMidYMid meet">',
        '<style>',
        '  rect:hover { stroke: #000; stroke-width: 1.5; }',
        '  text { font-family: -apple-system, BlinkMacSystemFont, '
        '"Segoe UI", sans-serif; fill: #1a1a1a; '
        'pointer-events: none; text-anchor: middle; '
        'dominant-baseline: middle; }',
        '</style>',
    ]
    if has_hatch or show_survivor_legend:
        parts.append(_SURVIVOR_DEFS)

    for x, y, w, h, node in rects:
        rel = node.rel_path or node.name
        if node.tooltip2:
            line2 = node.tooltip2
        elif node.est_tokens:
            # Code heatmap: block area is estimated tokens, so lead with that
            # and keep the familiar LOC one hover away (PRD: nothing lost).
            line2 = (f"{node.est_tokens:,} est. tokens · {node.loc} loc "
                     f"· {metric_label} {node.metric:.0f}")
            if node.aux_label:
                line2 += f" · {node.aux_label} {node.aux_metric:.0f}"
        else:
            line2 = f"{node.loc} loc · {metric_label} {node.metric:.0f}"
            if node.aux_label:
                line2 += f" · {node.aux_label} {node.aux_metric:.0f}"
        tooltip = html.escape(f"{rel}\n{line2}", quote=False)
        parts.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" '
            f'width="{w:.2f}" height="{h:.2f}" '
            f'fill="{rgba_to_hex(node.color)}" '
            f'stroke="white" stroke-width="0.5">'
            f'<title>{tooltip}</title></rect>'
        )

    # Hatch overlays sit on top of every base rect. pointer-events="none" keeps
    # the underlying block's hover tooltip working through the overlay.
    for x, y, w, h, node in rects:
        if not node.hatch:
            continue
        parts.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" '
            f'width="{w:.2f}" height="{h:.2f}" '
            f'fill="url(#survivor-{node.hatch})" stroke="none" '
            f'pointer-events="none"/>'
        )

    if show_labels:
        for x, y, w, h, node in rects:
            if w * h <= label_threshold:
                continue
            fs = max(7, min(int(min(w, h) / 6), 18))
            cx, cy = x + w / 2, y + h / 2
            name = html.escape(node.name)
            if node.label_size_text:
                size_text = node.label_size_text
            elif node.est_tokens:
                size_text = f"{node.est_tokens:,} est. tokens"
            else:
                size_text = f"{node.loc} loc"
            metric_text = node.label_metric_text or f"{metric_label} {node.metric:.0f}"
            parts.append(
                f'<text x="{cx:.1f}" y="{cy - fs:.1f}" '
                f'font-size="{fs}">{name}</text>'
                f'<text x="{cx:.1f}" y="{cy + 2:.1f}" '
                f'font-size="{max(6, fs - 2)}" fill="#444">'
                f'{html.escape(size_text)}</text>'
                f'<text x="{cx:.1f}" y="{cy + fs + 4:.1f}" '
                f'font-size="{max(6, fs - 2)}" fill="#444">'
                f'{html.escape(metric_text)}</text>'
            )

    if show_survivor_legend:
        parts.extend(_survivor_legend_parts(W, H))

    parts.append('</svg>')
    out_path.write_text("\n".join(parts), encoding="utf-8")
