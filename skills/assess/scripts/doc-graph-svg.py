# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "networkx",
#     "numpy",
#     "matplotlib",
# ]
# ///
"""
doc-graph-svg.py - the unified doc map: connectivity by structure, staleness by colour.

This single graph carries both Layer 0 doc signals, with one channel each so
neither is overloaded:

  - **Structure** (position + edges) = navigability. A radial layout rings docs
    by link-distance from the entry point: the navigable core is central, and
    docs no traversal can reach are banished to the rim. Orphans float free;
    islands sit as detached clusters. The topology *is* the reachability story,
    so colour is freed for the other signal.
  - **Colour** = staleness, in the exact grammar of the docs-staleness heatmap:
    hue = days since the doc changed (red = stale), blended toward grey by the
    churn of the code it describes. Vivid red = a frozen doc beside churning
    code = a lying map; pale/grey = stable or low-churn.
  - **Size** = file length (lines).
  - The entry point carries a blue ring so the navigation root is obvious even
    though colour now means staleness.

Reuses ``lib.doc_graph`` (structure) and ``lib.doc_staleness`` (the staleness
metric) so the picture matches the Layer 0 score exactly, and folds the separate
docs-staleness treemap into this one artifact.

Usage:
    uv run skills/assess/scripts/doc-graph-svg.py <path> [-o out.svg]
        [--layout radial|web] [--size lines|centrality] [--colour staleness|status]
"""
from __future__ import annotations

import argparse
import html
import math
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.doc_graph import (  # noqa: E402
    build_doc_graph,
    classify_node,
    group_broken_links,
    radial_shells,
)
from lib.doc_staleness import analyze_doc_staleness  # noqa: E402
from lib.treemap_render import adaptive_cap, blend_to_grey, rgba_to_hex  # noqa: E402

# Colour-blind-safe by default. The status palette uses the Okabe-Ito set
# (distinguishable under all common colour-vision deficiencies); the staleness
# fill uses the OrRd sequential ramp (CVD-safe, varies in luminance) rather than
# red-green. Markers also carry non-colour cues (rings, dashes) so the graph
# never relies on hue alone.
STALENESS_CMAP = "OrRd"      # pale = fresh/neutral -> dark red = stale lying-map
COLOR_ENTRY = "#0072B2"      # Okabe-Ito blue
COLOR_REACHABLE = "#009E73"  # Okabe-Ito bluish-green
COLOR_ISLAND = "#E69F00"     # Okabe-Ito orange
COLOR_ORPHAN = "#D55E00"     # Okabe-Ito vermillion
EDGE_COLOR = "#9aa0a6"
ENTRY_RING = "#0072B2"       # blue ring marks the entry node when colour = staleness
ORPHAN_RING = "#1a1a1a"      # dark dashed ring marks orphans when colour = staleness
GHOST_COLOR = "#CC79A7"      # Okabe-Ito reddish-purple: broken-link "ghost" nodes

W, H = 1600.0, 1000.0
MARGIN = 70.0
R_MIN, R_MAX = 4.0, 24.0


_STATUS_COLOR = {
    "entry": COLOR_ENTRY, "reachable": COLOR_REACHABLE,
    "island": COLOR_ISLAND, "orphan": COLOR_ORPHAN,
}


def _fit_rect(pos: dict, nodes: list[str], rect: tuple[float, float, float, float]) -> dict:
    """Normalise spring-layout coords into a sub-rectangle (x, y, w, h)."""
    rx, ry, rw, rh = rect
    xs = np.array([pos[n][0] for n in nodes])
    ys = np.array([pos[n][1] for n in nodes])
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    sx = rw / (x1 - x0) if x1 > x0 else 0.0
    sy = rh / (y1 - y0) if y1 > y0 else 0.0
    # Centre when an axis is degenerate (single column/row).
    return {n: (rx + ((pos[n][0] - x0) * sx if sx else rw / 2),
                ry + ((pos[n][1] - y0) * sy if sy else rh / 2)) for n in nodes}


def _grid_positions(nodes: list[str], rect: tuple[float, float, float, float]) -> dict:
    """Lay nodes out in a tidy grid inside (x, y, w, h)."""
    rx, ry, rw, rh = rect
    count = len(nodes)
    if count == 0:
        return {}
    cols = max(1, round(math.sqrt(count * rw / rh)))
    rows = math.ceil(count / cols)
    cw = rw / cols
    ch = rh / max(rows, 1)
    out = {}
    for i, node in enumerate(nodes):
        c, r = i % cols, i // cols
        out[node] = (rx + cw * (c + 0.5), ry + ch * (r + 0.5))
    return out


def _doc_lines(repo_root: Path, rel: str) -> int:
    try:
        return max(1, (repo_root / rel).read_text(encoding="utf-8", errors="ignore").count("\n") + 1)
    except OSError:
        return 1


def _radial_positions(graph, entries: set[str], cx: float, cy: float, fit: float) -> dict:
    """Concentric rings by link-distance from the entry points.

    Centre = entry; ring k = docs k hops away (following links); everything
    unreachable is banished to the outer rings. The picture is the navigability
    claim made literal: the navigable core is central, the lost docs are at the
    rim. The plot is centred at (cx, cy) and scaled to radius `fit`.
    """
    # Shell assignment (the BFS/distance logic) lives in
    # lib.doc_graph.radial_shells, which is unit-tested; here we only turn the
    # shells into x/y coordinates.
    shells = radial_shells(graph, entries)
    if not shells:
        return {}

    raw = nx.shell_layout(graph, nlist=shells, rotate=0.3)
    max_r = max((math.hypot(x, y) for x, y in raw.values()), default=1.0) or 1.0
    return {n: (cx + x / max_r * fit, cy + y / max_r * fit) for n, (x, y) in raw.items()}


def _render_ghosts(broken_links: list[dict], pos: dict, radius,
                   show_labels: bool = False) -> str:
    """Draw one 'ghost' node per missing file — not per broken link. Several links
    to the same absent target (e.g. README.md and CONTRIBUTING.md both pointing at
    a missing CLAUDE.md) collapse to a single ghost they all tether to, so the map
    shows one missing file rather than a cloud of duplicates.

    Each ghost is a hollow dashed circle sitting just outside the centroid of the
    sources that reference it, joined to each by a dashed tether. The missing name
    lives in the hover tooltip; it is only drawn as a text label when
    ``show_labels`` is set, so ghosts read like every other node (clean by
    default, named on hover)."""
    if not broken_links:
        return ""
    out: list[str] = []
    for gi, group in enumerate(group_broken_links(broken_links)):
        key = group["target"]
        sources = [s for s in group["sources"] if s in pos]
        if not sources:
            continue
        # Anchor the shared ghost at the centroid of its sources, pushed radially
        # outward so it clears the cluster; fan distinct ghosts apart.
        cx = sum(pos[s][0] for s in sources) / len(sources)
        cy = sum(pos[s][1] for s in sources) / len(sources)
        ang = 0.6 + gi * 0.9
        off = max(radius(s) for s in sources) + 40
        gx, gy = cx + off * math.cos(ang), cy + off * math.sin(ang)
        for s in sources:
            sx, sy = pos[s]
            out.append(
                f'<line x1="{sx:.1f}" y1="{sy:.1f}" x2="{gx:.1f}" y2="{gy:.1f}" '
                f'stroke="{GHOST_COLOR}" stroke-width="1.2" stroke-dasharray="4,3" opacity="0.85"/>'
            )
        n = len(sources)
        srcs = ", ".join(sources)
        tip = html.escape(
            f"BROKEN LINK ({n} source{'s' if n != 1 else ''})\n"
            f"{srcs} -> {key}\nmissing file: create it or fix the links",
            quote=False)
        out.append(
            f'<circle cx="{gx:.1f}" cy="{gy:.1f}" r="7" fill="#ffffff" '
            f'stroke="{GHOST_COLOR}" stroke-width="1.6" stroke-dasharray="3,2">'
            f'<title>{tip}</title></circle>'
        )
        if show_labels:
            out.append(
                f'<text x="{gx:.1f}" y="{gy - 11:.1f}" font-size="10" fill="{GHOST_COLOR}" '
                f'text-anchor="middle">{html.escape(Path(key).name)}</text>'
            )
    return "\n".join(out)


def render(result, out_path: Path, repo_root: Path, *, layout: str = "radial",
           size_mode: str = "lines", colour: str = "staleness",
           staleness: dict | None = None, show_labels: bool = False) -> None:
    graph = result.graph
    nodes = list(graph.nodes())
    n = len(nodes)
    pr = result.pagerank or {x: 1.0 / max(n, 1) for x in nodes}
    pr_max = max(pr.values()) if pr else 1.0
    in_deg = dict(graph.in_degree())
    out_deg = dict(graph.out_degree())

    entries = set(result.entry_points)
    unreachable = set(result.unreachable)
    orphans = set(result.orphans)

    # Node size metric: file length (lines) or link-graph centrality.
    if size_mode == "lines":
        sizes = {x: _doc_lines(repo_root, x) for x in nodes}
        size_label = "file length (lines)"
    else:
        sizes = {x: pr.get(x, 0.0) for x in nodes}
        size_label = "link-graph centrality"
    size_max = max(sizes.values(), default=1.0) or 1.0

    # Fill colour. Default "staleness" reuses the docs-staleness heatmap grammar
    # (hue = days stale, blended toward grey by the churn of the code the doc
    # describes) so the two doc views speak one colour language. "status" is the
    # older navigability-by-colour mode, kept as an option.
    staleness = staleness or {}
    cmap = plt.get_cmap(STALENESS_CMAP)
    days = {x: float(staleness.get(x, {}).get("last_commit_days") or 0) for x in nodes}
    churn = {x: float(staleness.get(x, {}).get("code_churn_in_window") or 0) for x in nodes}
    day_cap, _ = adaptive_cap(list(days.values()))
    churn_cap, _ = adaptive_cap(list(churn.values()))

    def fill(node: str) -> str:
        if colour == "status":
            return _STATUS_COLOR[_classify(node, entries, unreachable, orphans)]
        base = cmap(min(days[node] / day_cap, 1.0) if day_cap else 0.0)
        sat = (churn[node] / churn_cap) if churn_cap else 0.0
        return rgba_to_hex(blend_to_grey(base, sat))

    # Canvas: square-ish for the radial layout (it's circular, so a wide canvas
    # wastes the sides); wide for the web two-panel. Header = centred title;
    # footer = centred legend (radial only).
    if layout == "radial":
        cw, ch, header, footer = 1180.0, 1230.0, 92.0, 104.0
    else:
        cw, ch, header, footer = 1600.0, 1000.0, 110.0, 0.0

    pos: dict = {}
    has_isolated = False
    if layout == "radial":
        cx, cy = cw / 2, header + (ch - header - footer) / 2
        fit = min(cw, ch - header - footer) / 2 - R_MAX - 10
        pos = _radial_positions(graph, entries, cx, cy, fit)
    else:
        # Two-panel: linked web (force) + isolated-docs grid.
        linked = [x for x in nodes if in_deg.get(x, 0) + out_deg.get(x, 0) > 0]
        isolated = [x for x in nodes if x not in set(linked)]
        has_isolated = bool(isolated)
        web_right = (0.60 * cw) if has_isolated else (cw - MARGIN)
        web_rect = (MARGIN, 110, web_right - MARGIN, ch - 110 - MARGIN)
        if linked:
            sub = graph.subgraph(linked).to_undirected()
            raw = nx.spring_layout(sub, k=1.2, iterations=250, seed=np.random.RandomState(42))
            pos.update(_fit_rect(raw, linked, web_rect))
        if has_isolated:
            orphan_rect = (web_right + 40, 140, (cw - MARGIN) - (web_right + 40), ch - 140 - MARGIN)
            pos.update(_grid_positions(sorted(isolated), orphan_rect))

    def radius(node: str) -> float:
        return R_MIN + (R_MAX - R_MIN) * math.sqrt(sizes.get(node, 0.0) / size_max)

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {cw:.0f} {ch:.0f}" '
        f'width="{cw:.0f}" height="{ch:.0f}" preserveAspectRatio="xMidYMid meet">',
        '<style>',
        '  text { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #1a1a1a; }',
        '  circle:hover { stroke: #000; stroke-width: 2; }',
        '</style>',
        f'<rect x="0" y="0" width="{cw:.0f}" height="{ch:.0f}" fill="#ffffff"/>',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{EDGE_COLOR}"/></marker></defs>',
    ]

    # Panels: a labelled bin for the isolated docs, separated from the web.
    if has_isolated:
        ox = web_right + 16
        parts.append(
            f'<rect x="{ox:.0f}" y="104" width="{(cw - MARGIN) - ox:.0f}" '
            f'height="{ch - 104 - MARGIN + 16:.0f}" rx="10" fill="#fcf0f0" stroke="#f1c0c0"/>'
        )
        parts.append(
            f'<text x="{ox + 16:.0f}" y="130" font-size="15" font-weight="600" fill="#86181d">'
            f'{len(isolated)} isolated docs — no link in or out</text>'
        )

    # Edges (drawn first, under the nodes). Pull the arrow back to the target rim.
    for u, v in graph.edges():
        if u not in pos or v not in pos:
            continue
        x1, y1 = pos[u]
        x2, y2 = pos[v]
        dx, dy = x2 - x1, y2 - y1
        dist = math.hypot(dx, dy) or 1.0
        rt = radius(v) + 3
        ex, ey = x2 - dx / dist * rt, y2 - dy / dist * rt
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{ex:.1f}" y2="{ey:.1f}" '
            f'stroke="{EDGE_COLOR}" stroke-width="1.2" opacity="0.6" marker-end="url(#arrow)"/>'
        )

    def size_text(node: str) -> str:
        return (f"{sizes.get(node, 0):.0f} lines" if size_mode == "lines"
                else f"centrality {sizes.get(node, 0):.3f}")

    # Nodes. The title (path + stats) lives in a hover tooltip; no inline text.
    label_nodes: list[tuple[float, float, float, str]] = []
    for node in nodes:
        x, y = pos[node]
        r = radius(node)
        status = classify_node(node, entries, unreachable, orphans)
        is_entry = status == "entry"
        # Staleness mode frees colour for staleness, so the entry root and
        # orphans are called out by stroke (a non-colour cue), not fill. A faint
        # grey base stroke keeps pale (fresh) nodes visible on the white canvas.
        dash = ""
        if is_entry:
            stroke, sw = ENTRY_RING, 3.5
        elif colour == "staleness" and status == "orphan":
            stroke, sw, dash = ORPHAN_RING, 1.8, ' stroke-dasharray="3,2"'
        else:
            stroke, sw = "#b8b8b8", 1.0
        days_txt = f"{days[node]:.0f}d stale" if colour == "staleness" else ""
        tip = html.escape(
            f"{node}\n{size_text(node)} · in {in_deg.get(node, 0)} · "
            f"out {out_deg.get(node, 0)} · {status}"
            + (f" · {days_txt}, subject churn {churn[node]:.0f}" if days_txt else ""),
            quote=False)
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{fill(node)}" '
            f'stroke="{stroke}" stroke-width="{sw}"{dash}><title>{tip}</title></circle>'
        )
        # Inline labels are opt-in only (--labels); default is hover-only.
        if show_labels and (is_entry or in_deg.get(node, 0) + out_deg.get(node, 0) > 0):
            label_nodes.append((x, y, r, Path(node).name))

    for x, y, r, name in label_nodes:
        parts.append(
            f'<text x="{x:.1f}" y="{y - r - 3:.1f}" font-size="11" '
            f'text-anchor="middle">{html.escape(name)}</text>'
        )

    parts.append(_render_ghosts(result.broken_links, pos, radius, show_labels))
    parts.append(_title(result, n, cw))
    parts.append(_legend(size_label, layout, colour, cw, ch, footer))
    parts.append('</svg>')
    out_path.write_text("\n".join(parts), encoding="utf-8")

    print(f"wrote {out_path}  ({n} docs, {graph.number_of_edges()} links, "
          f"{result.island_count} islands)")
    print(f"orphan-rate {result.orphan_rate:.0%}  reachable-from-entry "
          f"{result.reachability_pct:.0%}  entries={sorted(entries)}")


def _title(result, n: int, cw: float) -> str:
    """Two centred lines at the top: headline + one-line stats."""
    mid = cw / 2
    links = len(result.broken_links)
    ghosts = len(group_broken_links(result.broken_links))
    if not links:
        broken_clause = ""
    elif ghosts < links:
        # Several links point at the same absent file; show the link total and
        # the smaller count of distinct missing files they collapse to.
        broken_clause = f' · {links} broken links to {ghosts} missing files'
    else:
        broken_clause = f' · {links} broken links'
    return "\n".join([
        f'<text x="{mid:.0f}" y="40" font-size="24" font-weight="600" '
        f'text-anchor="middle">Doc map — {n} docs, {result.graph.number_of_edges()} '
        f'links, {result.island_count} islands</text>',
        f'<text x="{mid:.0f}" y="66" font-size="14" fill="#555" text-anchor="middle">'
        f'{result.orphan_rate:.0%} orphaned · {result.reachability_pct:.0%} '
        f'reachable from the entry point'
        + broken_clause
        + '</text>',
    ])


def _legend(size_label: str, layout: str, colour: str, cw: float, ch: float,
            footer: float) -> str:
    """Centred key. For the radial layout it sits in the bottom footer band; for
    the web layout it falls back to a top-left strip."""
    mid = cw / 2
    y = (ch - footer / 2) if footer else 86
    out: list[str] = []
    if colour == "staleness":
        struct = ("rings = link-distance from entry · rim = unreachable"
                  if layout == "radial" else "loose dots = orphans")
        out.append(
            '<defs><linearGradient id="stalegrad" x1="0" y1="0" x2="1" y2="0">'
            '<stop offset="0" stop-color="#fff7ec"/><stop offset="0.5" stop-color="#fc8d59"/>'
            '<stop offset="1" stop-color="#7f0000"/></linearGradient></defs>'
        )
        # Row 1, centred: gradient (flanked by plain words, no arrow glyph that
        # some SVG renderers tofu) + entry + orphan markers.
        gx = mid - 250
        out.append(f'<text x="{gx - 6:.0f}" y="{y - 16:.0f}" font-size="12" fill="#555" '
                   'text-anchor="end">stable</text>')
        out.append(f'<rect x="{gx:.0f}" y="{y - 27:.0f}" width="110" height="12" rx="3" fill="url(#stalegrad)"/>')
        out.append(f'<text x="{gx + 116:.0f}" y="{y - 16:.0f}" font-size="12" fill="#555">lying map</text>')
        ex = mid + 10
        out.append(f'<circle cx="{ex:.0f}" cy="{y - 20:.0f}" r="8" fill="#dddddd" stroke="{ENTRY_RING}" stroke-width="3"/>')
        out.append(f'<text x="{ex + 14:.0f}" y="{y - 16:.0f}" font-size="13">entry</text>')
        ox = mid + 105
        out.append(f'<circle cx="{ox:.0f}" cy="{y - 20:.0f}" r="8" fill="#dddddd" stroke="{ORPHAN_RING}" '
                   'stroke-width="2" stroke-dasharray="3,2"/>')
        out.append(f'<text x="{ox + 14:.0f}" y="{y - 16:.0f}" font-size="13">orphan</text>')
        gh = mid + 205
        out.append(f'<circle cx="{gh:.0f}" cy="{y - 20:.0f}" r="8" fill="#ffffff" stroke="{GHOST_COLOR}" '
                   'stroke-width="1.8" stroke-dasharray="3,2"/>')
        out.append(f'<text x="{gh + 14:.0f}" y="{y - 16:.0f}" font-size="13">ghost (broken link)</text>')
        out.append(f'<text x="{mid:.0f}" y="{y + 20:.0f}" font-size="12" fill="#555" '
                   f'text-anchor="middle">colour = staleness · size = {size_label} · {struct}</text>')
        return "\n".join(out)
    # Status mode: discrete swatches, centred.
    swatches = [(COLOR_ENTRY, "entry"), (COLOR_REACHABLE, "reachable"),
                (COLOR_ISLAND, "island"), (COLOR_ORPHAN, "orphan")]
    total = sum(34 + len(lbl) * 7.2 for _, lbl in swatches)
    x = mid - total / 2
    for color, label in swatches:
        out.append(f'<circle cx="{x + 6:.0f}" cy="{y - 4:.0f}" r="7" fill="{color}"/>')
        out.append(f'<text x="{x + 20:.0f}" y="{y:.0f}" font-size="13">{label}</text>')
        x += 34 + len(label) * 7.2
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Render the doc link-graph as a navigability node diagram.")
    ap.add_argument("path", type=Path, help="Directory to analyse")
    ap.add_argument("-o", "--out", type=Path,
                    help="Output SVG path (default ./doc-graph-<folder>.svg)")
    ap.add_argument("--layout", choices=["radial", "web"], default="radial",
                    help="radial: concentric rings by link-distance from entry "
                         "(rim = unreachable). web: force-directed cluster + "
                         "isolated-docs bin.")
    ap.add_argument("--size", choices=["lines", "centrality"], default="lines",
                    help="Node size metric (default file length in lines).")
    ap.add_argument("--colour", "--color", choices=["staleness", "status"],
                    default="staleness", dest="colour",
                    help="staleness: docs-staleness heatmap grammar (default). "
                         "status: navigability colours (entry/reachable/island/orphan).")
    ap.add_argument("--labels", action="store_true",
                    help="Add inline filename labels (default hover-only).")
    args = ap.parse_args()

    root = args.path.resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 1

    result = build_doc_graph(root)
    if not result.available:
        print(f"error: doc graph unavailable - {result.reason}", file=sys.stderr)
        return 1
    if result.doc_count == 0 or result.graph is None:
        print("error: no docs to graph", file=sys.stderr)
        return 1

    # Staleness data for colour (reuses the same metric as the heatmap).
    staleness_block = analyze_doc_staleness(
        root, doc_to_code_edges=result.doc_to_code_edges)
    staleness = {d["path"]: d for d in staleness_block.get("docs", [])}

    out = args.out or Path(f"doc-graph-{root.name}.svg")
    render(result, out, root, layout=args.layout, size_mode=args.size,
           colour=args.colour, staleness=staleness,
           show_labels=args.labels)
    return 0


if __name__ == "__main__":
    sys.exit(main())
