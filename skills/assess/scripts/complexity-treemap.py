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
  - Hue         -> cyclomatic complexity (dark red = complex, pale = simple;
                   colour-blind-safe OrRd ramp, no red-green)
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

Build artifacts (main.dart.js, *.min.js, *.bundle.js, *.map, files under
node_modules/dist/build/.next/.nuxt/etc.) and generated code (*.pb.go,
*.connect.go, *_pb.ts, wire_gen.go, zz_generated_*.go, *.freezed.dart,
*.designer.cs, etc.) are filtered by default so compiled bundles and
protoc-emitted bindings don't dominate the "most complex" lists with
code nobody wrote by hand. If a single remaining file still holds >30%
of total LOC, a warning prints to stderr suggesting it might be a build
artifact that needs .gitignore. Pass --include-artifacts to disable the
filter entirely.

Usage:
    uv run skills/assess/scripts/complexity-treemap.py <path> [-o out.svg] [--labels] [--include-artifacts]
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import lizard
import matplotlib.pyplot as plt
import numpy as np

# Shared layout/SVG primitives and churn machinery live in lib/ so the code
# heatmap, the docs heatmap, and the Layer 0 staleness metric reuse one
# implementation. Only the colour mapping differs per heatmap and stays local.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.assess_config import load_excludes  # noqa: E402
from lib.git_churn import git_churn_scores, pick_churn_window  # noqa: E402
from lib.treemap_render import (  # noqa: E402
    adaptive_cap,
    blend_to_grey,
    build_tree,
    layout,
    write_svg,
)


EXCLUDE_DIRS = {".git", "node_modules", "dist", "build", "target", "vendor",
                ".venv", "venv", "__pycache__", ".gradle", ".idea", ".mvn",
                "worktree", ".understand-anything", ".obsidian",
                ".taskmaster", ".claude",
                # /assess's own output directory. Without this the prior
                # run's run-context.json (often 2,000+ LOC) gets picked up
                # as a top-large file on every re-run - circular pollution.
                ".assess",
                # modern web framework build outputs
                ".next", ".nuxt", ".output", ".svelte-kit", ".astro",
                "out", "coverage", "htmlcov",
                # iOS/Xcode
                "Pods", "DerivedData",
                # Flutter web build output (lives under web/ or public/)
                "flutter_assets"}

# Filenames that match these glob patterns are treated as build artifacts
# or generated code and excluded from scoring. Two motivations:
#   - Compiled bundles (main.dart.js etc.) eat 78% of LOC and skew
#     every percentile (see PR #12).
#   - Generated bindings (.pb.go, *_pb.ts etc.) dominate "most complex"
#     lists with machine-emitted switch statements and getters that
#     nobody wrote by hand (see meridian PR #2212 - 8/10 most-complex
#     files were protobuf bindings).
# Pass --include-artifacts to disable.
EXCLUDE_FILE_PATTERNS = [
    # --- build artifacts ---
    # Minified / bundled JS-CSS
    "*.min.js", "*.min.mjs", "*.min.css",
    "*.bundle.js", "*.bundle.mjs", "*.bundle.css",
    "*.chunk.js", "*.chunk.mjs",
    "*-bundle.js", "*-min.js",
    # Sourcemaps and build metadata
    "*.map", "*.tsbuildinfo",
    # Flutter web outputs. `--web-renderer canvaskit` always emits the
    # canvaskit/skwasm runtime bundles (framework code, churn=1, not source);
    # basename globs catch them wherever the build nests them
    # (e.g. canvaskit/chromium/canvaskit.js).
    "main.dart.js", "flutter_service_worker.js", "flutter.js",
    "canvaskit.js", "skwasm*.js",
    # PWA / service worker stubs
    "service-worker.js", "sw.js", "workbox-*.js",

    # --- generated code ---
    # Go protobuf / gRPC / grpc-gateway / Connect
    "*.pb.go", "*_grpc.pb.go", "*.pb.gw.go", "*.connect.go",
    # JS/TS protobuf / Connect (buf, ts-proto, protoc-gen-grpc-web)
    "*_pb.ts", "*_pb.d.ts", "*_pb.js",
    "*_connect.ts", "*_connect.d.ts", "*_connect.js",
    # Python protobuf
    "*_pb2.py", "*_pb2_grpc.py",
    # C++ protobuf
    "*.pb.cc", "*.pb.h",
    # Go generators (wire, controller-gen, mockgen, bindata)
    "*.gen.go", "*.generated.go",
    "wire_gen.go",
    "zz_generated_*.go",
    "bindata.go", "bindata_assetfs.go",
    # .NET designer / source generators
    "*.designer.cs", "*.g.cs", "*.g.i.cs",
    # Dart/Flutter codegen (freezed, json_serializable, riverpod, get_it)
    "*.freezed.dart", "*.g.dart", "*.gr.dart", "*.config.dart",
]


def _is_build_artifact(rel: Path) -> bool:
    """True if rel matches any EXCLUDE_FILE_PATTERNS glob (basename match)."""
    name = rel.name
    return any(fnmatch.fnmatch(name, pat) for pat in EXCLUDE_FILE_PATTERNS)


def _is_user_excluded(rel: Path, extra_dirs: set[str],
                      extra_patterns: list[str]) -> bool:
    """True if `rel` matches a user-supplied exclude (CLI `--exclude` or
    `.assess/config.toml`).

    `extra_dirs` is matched exactly against any path component (mirrors how
    `EXCLUDE_DIRS` works). `extra_patterns` is matched as a basename glob
    (mirrors `EXCLUDE_FILE_PATTERNS`).
    """
    if extra_dirs and any(part in extra_dirs for part in rel.parts):
        return True
    if extra_patterns and any(
        fnmatch.fnmatch(rel.name, pat) for pat in extra_patterns
    ):
        return True
    return False


def lizard_scores(
    root: Path, include_artifacts: bool = False,
    extra_exclude_dirs: set[str] | None = None,
    extra_exclude_patterns: list[str] | None = None,
) -> dict[Path, tuple[int, float]]:
    extra_dirs = extra_exclude_dirs or set()
    extra_pats = extra_exclude_patterns or []
    scores: dict[Path, tuple[int, float]] = {}
    for f in lizard.analyze(paths=[str(root)], exclude_pattern=[]):
        path = Path(f.filename).resolve()
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if not include_artifacts and _is_build_artifact(rel):
            continue
        if _is_user_excluded(rel, extra_dirs, extra_pats):
            continue
        ccn = sum(fn.cyclomatic_complexity for fn in f.function_list) or 1
        scores[path] = (f.nloc, float(ccn))
    return scores


def scc_scores(
    root: Path, include_artifacts: bool = False,
    extra_exclude_dirs: set[str] | None = None,
    extra_exclude_patterns: list[str] | None = None,
) -> dict[Path, tuple[int, float]]:
    if shutil.which("scc") is None:
        return {}
    extra_dirs = extra_exclude_dirs or set()
    extra_pats = extra_exclude_patterns or []
    # scc's --exclude-dir wants a comma-separated list. Merge defaults with
    # user-supplied dirs so scc skips them at scan time (cheaper than
    # filtering in Python after the fact).
    excludes = ",".join(sorted(EXCLUDE_DIRS | extra_dirs))
    try:
        raw = subprocess.run(
            ["scc", "--by-file", "--format", "json",
             f"--exclude-dir={excludes}", str(root)],
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"warning: scc failed ({e}); continuing without scc scores",
              file=sys.stderr)
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print("warning: scc returned invalid JSON; continuing without scc scores",
              file=sys.stderr)
        return {}
    scores: dict[Path, tuple[int, float]] = {}
    for lang_block in payload:
        for f in lang_block.get("Files", []):
            path = Path(f["Location"]).resolve()
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            if not include_artifacts and _is_build_artifact(rel):
                continue
            # scc honoured extra_dirs at the --exclude-dir level, but
            # exclude_patterns are basename-only so we filter them here.
            if _is_user_excluded(rel, set(), extra_pats):
                continue
            scores[path] = (int(f["Code"]), float(f["Complexity"]))
    return scores


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


def collect(root: Path, by: str = "complexity",
            include_artifacts: bool = False,
            extra_exclude_dirs: set[str] | None = None,
            extra_exclude_patterns: list[str] | None = None,
            ) -> tuple[list[tuple[Path, int, float, str]], str,
                       dict[Path, int] | None, str | None]:
    """Returns (files, effective_by, aux_data, aux_label).

    - files: [(path, loc, metric, source)] - metric depends on mode
    - effective_by: may differ from `by` if we fell back (e.g. no git)
    - aux_data: secondary per-file signal (hotspot mode only); None otherwise
    - aux_label: human label for aux signal in tooltips
    """
    lz = lizard_scores(
        root, include_artifacts=include_artifacts,
        extra_exclude_dirs=extra_exclude_dirs,
        extra_exclude_patterns=extra_exclude_patterns,
    )
    sc = scc_scores(
        root, include_artifacts=include_artifacts,
        extra_exclude_dirs=extra_exclude_dirs,
        extra_exclude_patterns=extra_exclude_patterns,
    )
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
        aux_data, aux_label = pick_churn_window(root, [f[0] for f in files])
        if aux_data is None:
            _git_not_found_warning(root)
            effective_by = "complexity"

    return files, effective_by, aux_data, aux_label


DOMINANCE_WARN_THRESHOLD = 0.30  # one file >30% of total LOC = suspicious


def _warn_if_dominated_by_one_file(
    files: list[tuple[Path, int, float, str]],
) -> None:
    """If a single file is >30% of total LOC, flag it as likely-build-artifact.

    Compiled bundles (main.dart.js, *.min.js) that slip past the filter
    skew every percentile and dominate the treemap. The size signal alone
    is enough to flag suspects - human-written codebases rarely have one
    file holding a third of the LOC.
    """
    if len(files) < 2:
        return
    total_loc = sum(f[1] for f in files)
    if total_loc == 0:
        return
    biggest = max(files, key=lambda f: f[1])
    share = biggest[1] / total_loc
    if share < DOMINANCE_WARN_THRESHOLD:
        return
    print(
        f"warning: one file holds {share:.0%} of scoreable LOC "
        f"({biggest[1]:,} of {total_loc:,}).\n"
        f"         {biggest[0].name} - looks like a build artifact or "
        f"generated file.\n"
        f"         If so, add it to .gitignore and re-run. To score it "
        f"anyway, pass --include-artifacts.",
        file=sys.stderr,
    )


# Survivor-density overlay thresholds. A file whose mutation survivor density
# (survivors / mutants, from the test_pressure block) clears these stops
# rendering as safe green: >30% gets a diagonal hatch, >50% a cross-hatch.
SURVIVOR_DIAG_THRESHOLD = 0.30
SURVIVOR_CROSS_THRESHOLD = 0.50


def _hatch_for_density(density: float | None) -> str:
    """Map a survivor density to a hatch level. "" when below threshold or
    unknown - so absent/empty data silently renders no overlay."""
    if density is None:
        return ""
    if density > SURVIVOR_CROSS_THRESHOLD:
        return "cross"
    if density > SURVIVOR_DIAG_THRESHOLD:
        return "diag"
    return ""


def _survivor_overrides(
    files: list[tuple[Path, int, float, str]],
    survivor_density: dict[Path, float] | None,
) -> dict[Path, dict]:
    """Build the per-file Node overrides (``{path: {"hatch": ...}}``) for the
    survivor-density overlay. Keys in ``survivor_density`` are matched against
    each file's resolved path (``files[i][0]``). Returns an empty dict when no
    data is supplied or nothing clears the threshold - the caller treats that
    as "no overlay"."""
    if not survivor_density:
        return {}
    overrides: dict[Path, dict] = {}
    for f in files:
        hatch = _hatch_for_density(survivor_density.get(f[0]))
        if hatch:
            overrides[f[0]] = {"hatch": hatch}
    return overrides


def render(files: list[tuple[Path, int, float, str]],
           root: Path, out_path: Path, title: str,
           show_labels: bool = False,
           by: str = "complexity",
           aux_data: dict[Path, int] | None = None,
           aux_label: str | None = None,
           survivor_density: dict[Path, float] | None = None) -> None:
    metric_label = "commits" if by == "churn" else "ccn"
    metrics = [f[2] for f in files]
    cap, cap_kind = adaptive_cap(metrics)
    # OrRd (ColorBrewer): colour-blind-safe sequential ramp, pale = simple ->
    # dark red = complex. Avoids the red-green of RdYlGn (the most common CVD).
    cmap = plt.get_cmap("OrRd")

    aux_cap = 1.0
    aux_cap_kind = ""
    if aux_data is not None:
        aux_values = [float(aux_data.get(f[0], 0)) for f in files]
        aux_cap, aux_cap_kind = adaptive_cap(aux_values)

    files_colored = []
    for f in files:
        # Floor the ramp at 0.12 so the calm (low-complexity) end is a visible
        # pale orange, not near-white that washes out against the white canvas.
        base = cmap(0.12 + 0.88 * min(f[2] / cap, 1.0))
        if aux_data is not None:
            aux_val = float(aux_data.get(f[0], 0))
            color = blend_to_grey(base, aux_val / aux_cap)
        else:
            color = base
        files_colored.append((f[0], f[1], f[2], f[3], color))

    overrides = _survivor_overrides(files, survivor_density)
    tree = build_tree(files_colored, root, aux_data, aux_label or "",
                      node_overrides=overrides or None)
    W, H = 1600.0, 1000.0
    rects: list = []
    layout(tree, 0, 0, W, H, rects)

    write_svg(rects, root, W, H, out_path, show_labels, metric_label,
              show_survivor_legend=bool(overrides))

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


def _read_plugin_version() -> str:
    """Read the plugin version from .claude-plugin/plugin.json.

    plugin.json lives three directories up from this script:
        scripts/complexity-treemap.py -> scripts/ -> skills/assess/ -> skills/ -> repo root
    Stamped into the stats sidecar so a later run can detect when its prior
    snapshot came from a differently-filtered plugin and suppress a misleading
    diff (filter-mismatched "graduated" ghosts). Returns "unknown" if absent.
    """
    plugin_json = Path(__file__).resolve().parents[3] / ".claude-plugin" / "plugin.json"
    try:
        data = json.loads(plugin_json.read_text(encoding="utf-8"))
        return str(data.get("version", "unknown"))
    except (FileNotFoundError, json.JSONDecodeError):
        return "unknown"


def write_stats(files: list[tuple[Path, int, float, str]],
                aux_data: dict[Path, int] | None,
                aux_label: str | None,
                root: Path, out_path: Path) -> None:
    """Write a JSON stats sidecar summarising the treemap data.

    Consumed by the /assess skill: percentiles drive Layer 3 (linter) scoring,
    top hotspot lists become the named files in the actions table.
    Composite hotspot score = sqrt(ccn) * sqrt(1 + commits): a sub-linear
    geometric mean of complexity and recent churn. Both axes are damped, so a
    complex-AND-active file leads, a frozen-but-complex file ranks below it, and
    a trivially-simple-but-churny file can't top the list on churn alone.
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
            # Named `commits` to match what every consumer reads (stats_diff,
            # assess_core, the hotspot template). None when churn is unavailable
            # (no git), so a missing value is distinct from a real 0.
            "commits": int(churn) if aux_data else None,
            "source": src,
            "_score": math.sqrt(ccn) * math.sqrt(1.0 + churn),
        })

    def strip(rows: list[dict]) -> list[dict]:
        return [{k: v for k, v in r.items() if k != "_score"} for r in rows]

    by_score = sorted(enriched, key=lambda f: -f["_score"])
    by_ccn = sorted(enriched, key=lambda f: -f["ccn"])
    by_loc = sorted(enriched, key=lambda f: -f["loc"])

    stats: dict = {
        "plugin_version": _read_plugin_version(),
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

    out_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")


def load_survivor_density(run_context_path: Path,
                          root: Path) -> dict[Path, float]:
    """Build a ``{resolved_path: density}`` map from a run-context.json's
    ``test_pressure`` block, for the survivor-density overlay.

    Per-file density is ``survived / total`` taken from ``test_pressure.per_file``
    (the only source carrying per-file totals; ``survivor_density.by_file`` holds
    raw survivor *counts*). Entries without a total (e.g. mutmut, which lists
    only survivors) are skipped - we hatch on a real density or not at all.

    File paths reported by mutation tools are resolved against ``root`` so they
    match the treemap's resolved file paths. Degrades silently to ``{}`` on any
    error or when no ``test_pressure`` data is present - an absent or empty block
    means no overlay, no warning.
    """
    try:
        ctx = json.loads(run_context_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(ctx, dict):
        return {}
    tp = ctx.get("test_pressure")
    if not isinstance(tp, dict):
        return {}
    per_file = tp.get("per_file") or []
    density: dict[Path, float] = {}
    for entry in per_file:
        if not isinstance(entry, dict):
            continue
        total = entry.get("total")
        survived = entry.get("survived")
        file_str = entry.get("file")
        if not file_str or not total:  # None or 0 total -> no derivable density
            continue
        try:
            resolved = (root / file_str).resolve()
            density[resolved] = (survived or 0) / total
        except (TypeError, ValueError, OSError):
            continue
    return density


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Render a Codecov-style hotspot treemap of any folder. "
            "Hue = cyclomatic complexity, saturation = recent git churn "
            "(auto-windowed: 12mo -> 24mo -> 5y -> all-time). "
            "Vivid red = complex AND active = highest risk."
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
              "Layer 3 and surface specific improvement actions."),
    )
    ap.add_argument(
        "--include-artifacts", action="store_true",
        help=("Score known build artifacts that are normally filtered "
              "(main.dart.js, *.min.js, *.bundle.js, *.map, etc.). "
              "Use this only when you specifically want to visualise "
              "the build output - typically you'd .gitignore these instead."),
    )
    ap.add_argument(
        "--test-pressure", type=Path, metavar="RUN_CONTEXT_JSON",
        help=("Path to a run-context.json. When its `test_pressure` block "
              "carries per-file mutation results, files with high survivor "
              "density are hatched (>30% diagonal, >50% cross-hatch) so "
              "covered-but-unpinned code stops rendering as safe green. "
              "Absent or empty test_pressure data -> no overlay (silent)."),
    )
    ap.add_argument(
        "--exclude", action="append", default=[], metavar="PATTERN",
        help=("Skip files / directories that match PATTERN. Repeatable. "
              "A plain string (`regulatory-raw`) is treated as a directory "
              "name; a glob (`*.csv`) is matched against the basename. "
              "Extends the built-in defaults rather than replacing them. "
              "For a durable per-repo exclude list, use "
              "`.assess/config.toml` (top-level `exclude_dirs` / "
              "`exclude_patterns`)."),
    )
    args = ap.parse_args()

    root = args.path.resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 1

    # Resolve user excludes from `.assess/config.toml` first, then layer the
    # CLI `--exclude` on top. Both extend the built-in defaults; the CLI is
    # not "ad-hoc only" - it just doesn't need to be remembered between runs
    # the way the config does. A CLI pattern containing a glob char goes to
    # exclude_patterns; everything else goes to exclude_dirs (so the same
    # `--exclude regulatory-raw` shape works as a dir match without needing
    # the user to pick the right list). The same lists feed every other
    # /assess scan via the orchestrator - see `assess_core.build_run_context`.
    cfg_dirs, cfg_patterns = load_excludes(root)
    extra_dirs: set[str] = set(cfg_dirs)
    extra_patterns: list[str] = list(cfg_patterns)
    for pat in args.exclude:
        if any(c in pat for c in "*?["):
            extra_patterns.append(pat)
        else:
            extra_dirs.add(pat)

    files, effective_by, aux_data, aux_label = collect(
        root, by="hotspot", include_artifacts=args.include_artifacts,
        extra_exclude_dirs=extra_dirs,
        extra_exclude_patterns=extra_patterns,
    )
    if not files:
        print("error: no scoreable files found", file=sys.stderr)
        return 1

    _warn_if_dominated_by_one_file(files)
    survivor_density = (
        load_survivor_density(args.test_pressure, root)
        if args.test_pressure else {}
    )
    default_name = f"hotspot-{root.name}.svg"
    out = args.out or Path(default_name)
    render(files, root, out, f"Hotspot: {root.name}",
           show_labels=args.labels, by=effective_by,
           aux_data=aux_data, aux_label=aux_label,
           survivor_density=survivor_density)
    if args.stats:
        write_stats(files, aux_data, aux_label, root, args.stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
