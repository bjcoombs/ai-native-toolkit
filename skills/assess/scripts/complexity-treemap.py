# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "lizard",
#     "squarify",
#     "matplotlib",
#     "numpy",
# ]
# ///
"""
complexity-treemap.py - Codecov-style hotspot treemap for any folder.

Each rectangle is one file, grouped by folder. The combined view encodes
three signals on one canvas (Adam Tornhill "hotspot" pattern, CodeScene-
style saturation):

  - Size        -> lines of code
  - Hue         -> cyclomatic complexity (red = complex, green = simple)
  - Saturation  -> recent git churn (vivid = active, grey = stable)

Vivid red = complex AND actively changing = highest migration risk.
Faded grey = code that hasn't moved lately, regardless of complexity.

Scoring tiers (size + complexity):
  1. lizard    -> real per-function cyclomatic complexity (Java, Python, JS,
                  TS, Go, C/C++, C#, Scala, Kotlin, Ruby, Swift, Rust, PHP).
  2. scc       -> keyword-heuristic complexity, covers 200+ languages.
                  Skipped for files lizard already scored. Optional.

Churn window auto-widens (12mo -> 24mo -> 5y -> all-time) until at least
10% of files have any activity. If the path isn't inside a git repo, the
saturation signal is dropped and files render fully vivid.

The hue and saturation gradients are independently capped at the 95th
percentile when the distribution has wild outliers (max > 5x p95);
otherwise the full max-of-data range is used so small/well-behaved data
sees the full gradient.

Output is a single self-contained SVG with hover tooltips on every block
(file path, LOC, complexity, recent commit count). Pass --labels to also
annotate large blocks with text.

Usage:
    uv run skills/assess/scripts/complexity-treemap.py <path> [-o out.svg] [--labels]
"""
from __future__ import annotations

import argparse
import html
import json
import math
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import lizard
import matplotlib.pyplot as plt
import numpy as np
import squarify


EXCLUDE_DIRS = {".git", "node_modules", "dist", "build", "target", "vendor",
                ".venv", "venv", "__pycache__", ".gradle", ".idea", ".mvn",
                "worktree", ".understand-anything", ".obsidian",
                ".taskmaster", ".claude"}


def lizard_scores(root: Path) -> dict[Path, tuple[int, float]]:
    scores: dict[Path, tuple[int, float]] = {}
    for f in lizard.analyze(paths=[str(root)], exclude_pattern=[]):
        path = Path(f.filename).resolve()
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        ccn = sum(fn.cyclomatic_complexity for fn in f.function_list) or 1
        scores[path] = (f.nloc, float(ccn))
    return scores


def scc_scores(root: Path) -> dict[Path, tuple[int, float]]:
    if shutil.which("scc") is None:
        return {}
    excludes = ",".join(EXCLUDE_DIRS)
    raw = subprocess.run(
        ["scc", "--by-file", "--format", "json",
         f"--exclude-dir={excludes}", str(root)],
        capture_output=True, text=True, check=True,
    ).stdout
    scores: dict[Path, tuple[int, float]] = {}
    for lang_block in json.loads(raw):
        for f in lang_block.get("Files", []):
            path = Path(f["Location"]).resolve()
            scores[path] = (int(f["Code"]), float(f["Complexity"]))
    return scores


def git_churn_scores(root: Path, since: str | None = None) -> dict[Path, int]:
    """Return {abs_path: commit_count} for files under root tracked in git.

    Returns empty dict if root is not inside a git repo. Commit counts are
    over the full history reachable from HEAD by default. Pass `since` as
    a git-compatible date expression (e.g. "6 months ago") to window the
    count. Renames are not followed - a file gets credit only under its
    current name.
    """
    try:
        repo_top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}
    repo = Path(repo_top).resolve()

    cmd = ["git", "-C", repo_top, "log",
           "--pretty=format:", "--name-only"]
    if since:
        cmd.append(f"--since={since}")
    try:
        raw = subprocess.run(
            cmd, capture_output=True, text=True, check=True,
        ).stdout
    except subprocess.CalledProcessError:
        return {}

    counts: dict[Path, int] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        path = (repo / line).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        counts[path] = counts.get(path, 0) + 1
    return counts


# Time windows tried in order from narrowest to widest. The first one
# that gives both gradient depth (max >= MIN_MAX) AND visual coverage
# (>= MIN_COVERAGE_PCT of files touched) wins. If nothing qualifies, we
# fall back to the widest window that returned any data. since=None
# means "full history".
CHURN_WINDOWS: list[tuple[str, str | None]] = [
    ("last 12mo", "12 months ago"),
    ("last 24mo", "24 months ago"),
    ("last 5y",   "5 years ago"),
    ("all-time",  None),
]
MIN_MAX = 3          # max commits per file must clear this for visible gradient
MIN_COVERAGE_PCT = 10.0   # % of scored files with any activity in the window


def _pick_churn_window(
    root: Path, files: list,
) -> tuple[dict[Path, int] | None, str | None]:
    """Walk CHURN_WINDOWS narrowest-to-widest; return the first window
    that gives both gradient depth and visual coverage. Falls back to
    the widest non-empty window if none qualify."""
    file_paths = [f[0] for f in files]
    total_files = max(len(file_paths), 1)
    widest_fallback: tuple[dict[Path, int], str] | None = None

    for label, since in CHURN_WINDOWS:
        data = git_churn_scores(root, since=since)
        if not data:
            continue
        # restrict to files we actually scored - "touched" means touched
        # AND scoreable, so coverage % is comparable across windows
        scored_hits = {p: data[p] for p in file_paths if p in data}
        if not scored_hits:
            continue
        if widest_fallback is None or label == CHURN_WINDOWS[-1][0]:
            widest_fallback = (data, label)
        coverage = 100.0 * len(scored_hits) / total_files
        max_commits = max(scored_hits.values())
        if max_commits >= MIN_MAX and coverage >= MIN_COVERAGE_PCT:
            if label != CHURN_WINDOWS[0][0]:
                print(f"note: activity sparse - widened window to "
                      f"{label} ({coverage:.0f}% of files touched, "
                      f"max {max_commits} commits).",
                      file=sys.stderr)
            return ({p: data.get(p, 0) for p in file_paths},
                    f"commits ({label})")

    if widest_fallback is not None:
        data, label = widest_fallback
        scored_hits = {p: data[p] for p in file_paths if p in data}
        coverage = 100.0 * len(scored_hits) / total_files
        max_commits = max(scored_hits.values()) if scored_hits else 0
        print(f"note: activity below thresholds in every window; "
              f"using {label} ({coverage:.0f}% coverage, "
              f"max {max_commits}).", file=sys.stderr)
        return ({p: data.get(p, 0) for p in file_paths},
                f"commits ({label})")
    return None, None


def _git_not_found_warning(root: Path) -> None:
    print(
        f"warning: no git history found for {root}\n"
        "         (path may not be a git repo, or the repo is "
        "empty / has a broken HEAD).\n"
        "         Rendering pure complexity (no saturation signal). "
        "If your code lives in a subdirectory\n"
        "         that IS a git repo (e.g. a -main subfolder), point "
        "the path at that.",
        file=sys.stderr,
    )


def collect(root: Path, by: str = "complexity"
            ) -> tuple[list[tuple[Path, int, float, str]], str,
                       dict[Path, int] | None, str | None]:
    """Returns (files, effective_by, aux_data, aux_label).

    - files: [(path, loc, metric, source)] - metric depends on mode
    - effective_by: may differ from `by` if we fell back (e.g. no git)
    - aux_data: secondary per-file signal (hotspot mode only); None otherwise
    - aux_label: human label for aux signal in tooltips
    """
    lz = lizard_scores(root)
    sc = scc_scores(root)
    files: list[tuple[Path, int, float, str]] = []
    for path, (loc, ccn) in lz.items():
        files.append((path, loc, ccn, "lizard"))
    for path, (loc, cx) in sc.items():
        if path not in lz:
            files.append((path, loc, cx, "scc"))
    files = [f for f in files if f[1] > 0]

    effective_by = by
    aux_data: dict[Path, int] | None = None
    aux_label: str | None = None

    if by == "churn":
        churn = git_churn_scores(root)
        if not churn:
            _git_not_found_warning(root)
            effective_by = "complexity"
        else:
            files = [(p, loc, float(churn.get(p, 0)), src)
                     for p, loc, _m, src in files]
    elif by == "hotspot":
        aux_data, aux_label = _pick_churn_window(root, files)
        if aux_data is None:
            _git_not_found_warning(root)
            effective_by = "complexity"

    return files, effective_by, aux_data, aux_label


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


def build_tree(files_with_color, root: Path,
               aux_data: dict[Path, int] | None = None,
               aux_label: str = "") -> Node:
    rootnode = Node(name=root.name)
    by_path: dict[Path, Node] = {root: rootnode}
    for path, loc, metric, _src, color in files_with_color:
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
        parent.children.append(Node(
            name=rel.parts[-1], size=loc, color=color,
            loc=loc, metric=metric, aux_metric=aux_val,
            aux_label=aux_label, rel_path=str(rel), is_file=True,
        ))

    def roll(n: Node) -> int:
        if n.is_file:
            return n.size
        n.size = sum(roll(c) for c in n.children)
        return n.size
    roll(rootnode)
    return rootnode


def layout(node: Node, x: float, y: float, w: float, h: float,
           out: list) -> None:
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


def _rgba_to_hex(rgba: tuple) -> str:
    r, g, b = rgba[0], rgba[1], rgba[2]
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


GREY = (0.82, 0.82, 0.84)  # cool light grey for "stable" / no recent churn


def _blend_to_grey(rgba: tuple, factor: float) -> tuple:
    """factor=0 returns full grey, factor=1 returns the original colour."""
    factor = max(0.0, min(1.0, factor))
    return tuple(
        GREY[i] + (rgba[i] - GREY[i]) * factor for i in range(3)
    ) + (1.0,)


def write_svg(rects: list, root: Path, W: float, H: float,
              out_path: Path, show_labels: bool,
              metric_label: str) -> None:
    label_threshold = (W * H) / 200
    parts: list[str] = [
        f'<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
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
        line2 = f"{node.loc} loc · {metric_label} {node.metric:.0f}"
        if node.aux_label:
            line2 += f" · {node.aux_label} {node.aux_metric:.0f}"
        tooltip = html.escape(f"{rel}\n{line2}", quote=False)
        parts.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" '
            f'width="{w:.2f}" height="{h:.2f}" '
            f'fill="{_rgba_to_hex(node.color)}" '
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
            parts.append(
                f'<text x="{cx:.1f}" y="{cy - fs:.1f}" '
                f'font-size="{fs}">{name}</text>'
                f'<text x="{cx:.1f}" y="{cy + 2:.1f}" '
                f'font-size="{max(6, fs - 2)}" fill="#444">'
                f'{node.loc} loc</text>'
                f'<text x="{cx:.1f}" y="{cy + fs + 4:.1f}" '
                f'font-size="{max(6, fs - 2)}" fill="#444">'
                f'{metric_label} {node.metric:.0f}</text>'
            )

    parts.append('</svg>')
    out_path.write_text("\n".join(parts))


def _adaptive_cap(values: list[float]) -> tuple[float, str]:
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


def render(files: list[tuple[Path, int, float, str]],
           root: Path, out_path: Path, title: str,
           show_labels: bool = False,
           by: str = "complexity",
           aux_data: dict[Path, int] | None = None,
           aux_label: str | None = None) -> None:
    metric_label = "commits" if by == "churn" else "ccn"
    metrics = [f[2] for f in files]
    cap, cap_kind = _adaptive_cap(metrics)
    cmap = plt.get_cmap("RdYlGn_r")

    aux_cap = 1.0
    aux_cap_kind = ""
    if aux_data is not None:
        aux_values = [float(aux_data.get(f[0], 0)) for f in files]
        aux_cap, aux_cap_kind = _adaptive_cap(aux_values)

    files_colored = []
    for f in files:
        base = cmap(min(f[2] / cap, 1.0))
        if aux_data is not None:
            aux_val = float(aux_data.get(f[0], 0))
            color = _blend_to_grey(base, aux_val / aux_cap)
        else:
            color = base
        files_colored.append((f[0], f[1], f[2], f[3], color))

    tree = build_tree(files_colored, root, aux_data, aux_label or "")
    W, H = 1600.0, 1000.0
    rects: list = []
    layout(tree, 0, 0, W, H, rects)

    write_svg(rects, root, W, H, out_path, show_labels, metric_label)

    print(f"wrote {out_path}  ({len(files)} files, "
          f"{sum(1 for f in files if f[3] == 'lizard')} lizard, "
          f"{sum(1 for f in files if f[3] == 'scc')} scc)")
    mx = float(max(metrics)) if metrics else 0.0
    print(f"hue: {metric_label}; range 0-{mx:.0f}; "
          f"cap {cap:.0f} ({cap_kind})")
    if aux_data is not None:
        aux_max = max((float(aux_data.get(f[0], 0)) for f in files),
                       default=0.0)
        print(f"saturation: {aux_label}; range 0-{aux_max:.0f}; "
              f"cap {aux_cap:.0f} ({aux_cap_kind})")

    biggest = sorted(files, key=lambda f: -f[1])[:5]
    if biggest:
        print("biggest files (size dominates layout):")
        for path, loc, metric, src in biggest:
            try:
                rel = path.relative_to(root)
            except ValueError:
                rel = path
            aux_str = ""
            if aux_data is not None:
                aux_str = f"  {aux_label} {aux_data.get(path, 0):>4d}"
            print(f"  {loc:>7} loc  {metric_label} {metric:>5.0f}{aux_str}  "
                  f"[{src:6}]  {rel}")


def write_stats(files: list[tuple[Path, int, float, str]],
                aux_data: dict[Path, int] | None,
                aux_label: str | None,
                root: Path, out_path: Path) -> None:
    """Write a JSON stats sidecar summarising the treemap data.

    Consumed by the /assess skill: percentiles drive Layer 2 scoring,
    top hotspot lists become the named files in the actions table.
    Composite hotspot score = ccn * (1 + log1p(churn)), so a vivid
    red block (complex AND active) outranks a frozen complex file.
    """
    locs = [f[1] for f in files]
    ccns = [f[2] for f in files]
    churns = ([float(aux_data.get(f[0], 0)) for f in files]
              if aux_data is not None else [])

    def pct(values: list[float], q: float) -> float:
        return float(np.percentile(values, q)) if values else 0.0

    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(root))
        except ValueError:
            return str(p)

    enriched = []
    for path, loc, ccn, src in files:
        churn = float(aux_data.get(path, 0)) if aux_data else 0.0
        enriched.append({
            "path": rel(path),
            "loc": int(loc),
            "ccn": float(ccn),
            "churn": int(churn) if aux_data else None,
            "source": src,
            "_score": ccn * (1.0 + math.log1p(churn)),
        })

    def strip(rows: list[dict]) -> list[dict]:
        return [{k: v for k, v in r.items() if k != "_score"} for r in rows]

    by_score = sorted(enriched, key=lambda f: -f["_score"])
    by_ccn = sorted(enriched, key=lambda f: -f["ccn"])
    by_loc = sorted(enriched, key=lambda f: -f["loc"])

    stats: dict = {
        "files_scored": len(files),
        "scoring_coverage": {
            "lizard": sum(1 for f in files if f[3] == "lizard"),
            "scc": sum(1 for f in files if f[3] == "scc"),
        },
        "churn_window": aux_label,
        "loc": {
            "p50": pct(locs, 50),
            "p95": pct(locs, 95),
            "max": float(max(locs)) if locs else 0.0,
            "total": sum(locs),
        },
        "ccn": {
            "p50": pct(ccns, 50),
            "p95": pct(ccns, 95),
            "max": float(max(ccns)) if ccns else 0.0,
        },
        "churn": ({
            "p50": pct(churns, 50),
            "p95": pct(churns, 95),
            "max": float(max(churns)) if churns else 0.0,
        } if aux_data is not None else None),
        "top_hotspots": strip(by_score[:10]),
        "top_complex": strip(by_ccn[:10]),
        "top_large": strip(by_loc[:10]),
    }

    out_path.write_text(json.dumps(stats, indent=2))
    print(f"wrote {out_path}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Render a Codecov-style hotspot treemap of any folder. "
            "Hue = cyclomatic complexity, saturation = recent git churn "
            "(last 6 months). Vivid red = complex AND active = highest risk."
        ))
    ap.add_argument("path", type=Path, help="Directory to analyse")
    ap.add_argument(
        "-o", "--out", type=Path,
        help=("Output SVG path. If omitted, writes "
              "./hotspot-<folder>.svg in the current working directory."),
    )
    ap.add_argument("--labels", action="store_true",
                    help="Annotate large blocks with filename, LOC and metric")
    ap.add_argument(
        "--stats", type=Path,
        help=("Write a JSON stats sidecar (file count, LOC/CCN percentiles, "
              "top hotspot/complex/large files). Used by /assess to score "
              "Layer 2 and surface specific improvement actions."),
    )
    args = ap.parse_args()

    root = args.path.resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 1

    files, effective_by, aux_data, aux_label = collect(root, by="hotspot")
    if not files:
        print("error: no scoreable files found", file=sys.stderr)
        return 1
    default_name = f"hotspot-{root.name}.svg"
    out = args.out or Path(default_name)
    render(files, root, out, f"Hotspot: {root.name}",
           show_labels=args.labels, by=effective_by,
           aux_data=aux_data, aux_label=aux_label)
    if args.stats:
        write_stats(files, aux_data, aux_label, root, args.stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
