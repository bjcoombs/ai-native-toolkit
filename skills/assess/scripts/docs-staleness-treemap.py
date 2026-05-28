# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "squarify",
#     "matplotlib",
#     "numpy",
#     "networkx",
# ]
# ///
"""
docs-staleness-treemap.py - the inverted twin of the code heatmap.

The code heatmap scores risk-to-change: vivid red = complex AND churning. A
doc's risk profile is the *opposite* - the danger isn't "complex and changing",
it's **frozen while its subject moves** (the decaying map). So this treemap
keeps the same visual grammar and inverts the meaning:

  - Size        -> link-graph centrality (PageRank) - how load-bearing the doc
                   is. Hubs / MOCs are biggest, so a stale hub dominates the
                   map. Falls back to doc length when the graph is unavailable.
  - Hue         -> doc staleness (days since the doc last changed); red = stale.
  - Saturation  -> churn of the *code the doc describes*; vivid = subject moving.

  Vivid red = a frozen doc whose subject is churning = the most dangerous lying
  map. A stale doc beside dead code is pale (harmless); the ratio is what
  colours it.

Doc->code association (which code a doc describes) uses the nearest-ancestor
base-doc rule with ordered fallbacks - see lib/doc_staleness.py. The method used
per doc, and its limits, are reported in the stats sidecar.

Usage:
    uv run skills/assess/scripts/docs-staleness-treemap.py <path> \
        [-o out.svg] [--stats out.json] [--labels]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.doc_graph import build_doc_graph  # noqa: E402
from lib.doc_staleness import analyze_doc_staleness  # noqa: E402
from lib.treemap_render import (  # noqa: E402
    adaptive_cap,
    blend_to_grey,
    build_tree,
    layout,
    write_svg,
)

# Scale PageRank (sums to 1, values ~0.01-0.2) up to integer treemap sizes so a
# hub visibly dominates rather than collapsing to a one-pixel square.
PAGERANK_SIZE_SCALE = 100_000


def _doc_line_count(path: Path) -> int:
    try:
        return max(1, len(path.read_text(encoding="utf-8", errors="ignore").splitlines()))
    except OSError:
        return 1


def collect_docs(root: Path):
    """Return (rows, graph_dict, staleness, size_basis).

    rows: list of (abs_path, size, staleness_days, "doc", churn, pagerank, method)
    size_basis: "centrality" or "doc-length" (reported in output).
    """
    graph_result = build_doc_graph(root)
    pagerank = graph_result.pagerank if graph_result.available else {}
    use_centrality = bool(pagerank) and graph_result.edge_count > 0

    staleness = analyze_doc_staleness(
        root,
        doc_to_code_edges=(graph_result.doc_to_code_edges
                           if graph_result.available else None),
    )

    rows = []
    for d in staleness["docs"]:
        rel = d["path"]
        abs_path = (root / rel).resolve()
        days = d["last_commit_days"] if d["last_commit_days"] is not None else 0
        churn = d["code_churn_in_window"]
        pr = pagerank.get(rel, 0.0)
        if use_centrality:
            size = max(1, int(round(pr * PAGERANK_SIZE_SCALE)))
        else:
            size = _doc_line_count(abs_path)
        rows.append((abs_path, size, float(days), "doc", churn, pr, d["subject_method"]))

    size_basis = "centrality" if use_centrality else "doc-length"
    return rows, graph_result, staleness, size_basis


def render(rows, root: Path, out_path: Path, show_labels: bool,
           size_basis: str) -> None:
    # Hue = staleness (red = stale). Saturation = subject (code) churn (vivid).
    staleness_vals = [r[2] for r in rows]
    churn_vals = [float(r[4]) for r in rows]
    hue_cap, hue_cap_kind = adaptive_cap(staleness_vals)
    sat_cap, sat_cap_kind = adaptive_cap(churn_vals)
    cmap = plt.get_cmap("RdYlGn_r")

    files_colored = []
    overrides: dict[Path, dict] = {}
    for abs_path, size, days, src, churn, pr, method in rows:
        base = cmap(min(days / hue_cap, 1.0))
        color = blend_to_grey(base, (churn / sat_cap) if sat_cap else 0.0)
        files_colored.append((abs_path, size, days, src, color))
        overrides[abs_path] = {
            "tooltip2": (f"centrality {pr:.4f} · stale {int(days)}d · "
                         f"code-churn {int(churn)} · {method}"),
            "label_size_text": (f"pr {pr:.3f}" if size_basis == "centrality"
                                else f"{size} lines"),
            "label_metric_text": f"stale {int(days)}d",
        }

    tree = build_tree(files_colored, root, node_overrides=overrides)
    W, H = 1600.0, 1000.0
    rects: list = []
    layout(tree, 0, 0, W, H, rects)
    write_svg(rects, root, W, H, out_path, show_labels, "stale (days)")

    print(f"wrote {out_path}  ({len(rows)} docs, size={size_basis})")
    mx_days = max(staleness_vals) if staleness_vals else 0.0
    mx_churn = max(churn_vals) if churn_vals else 0.0
    print(f"hue: staleness; range 0-{mx_days:.0f}d; cap {hue_cap:.0f} ({hue_cap_kind})")
    print(f"saturation: subject code churn; range 0-{mx_churn:.0f}; "
          f"cap {sat_cap:.0f} ({sat_cap_kind})")
    leaders = sorted(rows, key=lambda r: -(r[2] * (1 + r[4]) * (r[1])))[:5]
    if leaders:
        print("most dangerous lying maps (size x stale x subject-churn):")
        for abs_path, size, days, _src, churn, pr, method in leaders:
            try:
                rel = abs_path.relative_to(root)
            except ValueError:
                rel = abs_path
            print(f"  stale {int(days):>4}d  code-churn {int(churn):>4}  "
                  f"pr {pr:.3f}  [{method}]  {rel}")


def write_stats(rows, graph_result, staleness, size_basis: str,
                root: Path, out_path: Path) -> None:
    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(root))
        except ValueError:
            return str(p)

    # Composite "lying map" score: stale x (1+subject churn) x load-bearing.
    enriched = []
    for abs_path, size, days, _src, churn, pr, method in rows:
        enriched.append({
            "path": rel(abs_path),
            "staleness_days": int(days),
            "code_churn_in_window": int(churn),
            "centrality": round(pr, 4),
            "subject_method": method,
            "_score": days * (1.0 + churn) * (pr if size_basis == "centrality" else 1.0),
        })
    by_score = sorted(enriched, key=lambda r: -r["_score"])
    top = [{k: v for k, v in r.items() if k != "_score"} for r in by_score[:10]]

    stats = {
        "docs_scored": len(rows),
        "size_basis": size_basis,
        "churn_window": staleness.get("churn_window"),
        "association": staleness.get("association"),
        "modularity": staleness.get("modularity"),
        "graph": {
            "available": graph_result.available,
            "orphan_rate": graph_result.orphan_rate,
            "island_count": graph_result.island_count,
            "reachability_pct": graph_result.reachability_pct,
            "moc_named_but_not_wired": graph_result.moc_named_but_not_wired,
        },
        "top_lying_maps": top,
    }
    out_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Render a docs-staleness treemap (inverted code heatmap). "
            "Size = link-graph centrality, hue = doc staleness, "
            "saturation = churn of the code the doc describes. "
            "Vivid red = a frozen doc whose subject is churning = lying map."
        ))
    ap.add_argument("path", type=Path, help="Directory to analyse")
    ap.add_argument("-o", "--out", type=Path,
                    help="Output SVG path (default ./docs-staleness-<folder>.svg)")
    ap.add_argument("--stats", type=Path, help="Write a JSON stats sidecar")
    ap.add_argument("--labels", action="store_true",
                    help="Annotate large blocks with doc name, centrality, staleness")
    args = ap.parse_args()

    root = args.path.resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 1

    rows, graph_result, staleness, size_basis = collect_docs(root)
    if not rows:
        print("error: no docs found to score", file=sys.stderr)
        return 1

    out = args.out or Path(f"docs-staleness-{root.name}.svg")
    render(rows, root, out, show_labels=args.labels, size_basis=size_basis)
    if args.stats:
        write_stats(rows, graph_result, staleness, size_basis, root, args.stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
