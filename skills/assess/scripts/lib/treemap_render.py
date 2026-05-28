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


def build_tree(files_with_color, root: Path,
               aux_data: dict[Path, int] | None = None,
               aux_label: str = "",
               node_overrides: dict[Path, dict] | None = None) -> Node:
    """Build the directory tree of Nodes. `files_with_color` is a list of
    (path, size, metric, source, color). `node_overrides` optionally maps a
    file path to a dict of extra Node fields (tooltip2, label_* ...)."""
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
        leaf = Node(
            name=rel.parts[-1], size=size, color=color,
            loc=size, metric=metric, aux_metric=aux_val,
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


def write_svg(rects: list, root: Path, W: float, H: float,
              out_path: Path, show_labels: bool,
              metric_label: str) -> None:
    label_threshold = (W * H) / 200
    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W:.0f} {H:.0f}" '
        f'width="{W:.0f}" height="{H:.0f}" '
        f'preserveAspectRatio="xMidYMid meet">',
        '<style>',
        '  rect:hover { stroke: #000; stroke-width: 1.5; }',
        '  text { font-family: -apple-system, BlinkMacSystemFont, '
        '"Segoe UI", sans-serif; fill: #1a1a1a; '
        'pointer-events: none; text-anchor: middle; '
        'dominant-baseline: middle; }',
        '</style>',
    ]

    for x, y, w, h, node in rects:
        rel = node.rel_path or node.name
        if node.tooltip2:
            line2 = node.tooltip2
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

    if show_labels:
        for x, y, w, h, node in rects:
            if w * h <= label_threshold:
                continue
            fs = max(7, min(int(min(w, h) / 6), 18))
            cx, cy = x + w / 2, y + h / 2
            name = html.escape(node.name)
            size_text = node.label_size_text or f"{node.loc} loc"
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

    parts.append('</svg>')
    out_path.write_text("\n".join(parts), encoding="utf-8")
