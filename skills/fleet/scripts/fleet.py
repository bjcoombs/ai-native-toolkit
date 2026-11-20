"""Fleet runner: headless /assess signals across a ghsync root or a list (issue #567).

Runs ``skills/assess/scripts/assess_signals.py`` once per repository, each in
its own process from a worker pool, counts-only by default, and writes one JSON
line per repository to ``<out>/repos.jsonl``, sorted by repository id. The row
shape is ``skills/fleet/references/repos.schema.json``; the logic lives in
``skills/assess/scripts/lib/fleet.py``.

Target repositories are never written to.

Exit codes:
    0  the run completed and repos.jsonl was written. Repositories that could
       not be scanned are rows with "status": "failed" and a reason token; the
       count is printed to stderr. Failures never change the exit code.
    2  usage error: no root and no --from-list, a root or list that does not
       exist, no repositories found, --out inside a target, an unreadable
       --rubric, or bad scan arguments

Run:
    uv run fleet.py ROOT --out DIR [--rubric FILE] [--jobs N] [--window 6mo]
        [--scans a,b] [--timeout SECONDS] [--full | --hash-paths | --hash-salt VALUE]
    uv run fleet.py --from-list FILE [ROOT] --out DIR [...]
"""
# /// script
# requires-python = ">=3.11"
# ///
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True

# skills/fleet/scripts -> skills/assess/scripts (the plugin ships both skills)
ASSESS_SCRIPTS = Path(__file__).resolve().parents[2] / "assess" / "scripts"
sys.path.insert(0, str(ASSESS_SCRIPTS))

from lib.fleet import (  # noqa: E402
    DEFAULT_TIMEOUT,
    STATUS_FAILED,
    ScanOptions,
    Target,
    discover_root,
    out_inside_target,
    parse_list,
    run_fleet,
    run_salt,
    targets_from_list,
    write_rows,
)
from lib.rubric import RubricError, load_rubric  # noqa: E402
from lib.signals import UnknownScanError, parse_window, select_scans  # noqa: E402


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run headless /assess signals on every repository under a ghsync root "
                    "(or in a list) and write one JSON line per repository to OUT/repos.jsonl.",
    )
    parser.add_argument(
        "root", nargs="?",
        help="ghsync root (<root>/<path>/<repo>-main). With --from-list, resolves list entries "
             "relative to it and names repositories by their path under it.",
    )
    parser.add_argument(
        "--from-list", metavar="FILE",
        help="Scan the repositories in FILE instead of discovering them: one per line, a "
             "<repo>-main checkout, its repository directory, a plain git checkout, or a path "
             "relative to ROOT (ghsync --porcelain output). Blank lines and # comments ignored.",
    )
    parser.add_argument("--out", required=True, help="Output directory; repos.jsonl is written here.")
    parser.add_argument(
        "--rubric", metavar="FILE",
        help="Maturity rubric (TOML, lib/rubric.py format). Validated here; the group roll-up "
             "that applies it is not yet part of the runner.",
    )
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1,
                        help="Repositories scanned in parallel (default: CPU count).")
    parser.add_argument("--window", default=None,
                        help="History window passed to assess_signals, e.g. 6mo, 90d (default: its own).")
    parser.add_argument("--scans", default=None, help="Comma-separated scan names (default: all).")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help="Seconds one repository may take before it is marked failed (default: %(default)s).")
    parser.add_argument("--full", action="store_true",
                        help="Keep full signal blocks (free text included) instead of counts-only.")
    parser.add_argument("--hash-paths", action="store_true",
                        help="Hash repo-relative paths under one salt drawn for this run "
                             "(hashes join within the run; reruns are not byte-identical).")
    parser.add_argument("--hash-salt", metavar="VALUE",
                        help="Hash paths under this salt (implies --hash-paths); reruns stay identical.")
    args = parser.parse_args(argv)
    if args.root is None and args.from_list is None:
        parser.error("give a ghsync ROOT or --from-list FILE")
    if args.full and (args.hash_paths or args.hash_salt is not None):
        parser.error("--hash-paths and --hash-salt apply to counts-only output, not --full")
    if args.hash_salt == "":
        parser.error("--hash-salt needs a non-empty value")
    if args.jobs < 1 or args.timeout < 1:
        parser.error("--jobs and --timeout must be at least 1")
    return args


def _targets(args: argparse.Namespace) -> list[Target]:
    root = Path(args.root) if args.root is not None else None
    if root is not None and not root.is_dir():
        raise ValueError(f"{args.root} is not a directory")
    if args.from_list is None:
        assert root is not None
        return discover_root(root)
    try:
        text = Path(args.from_list).read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"cannot read --from-list {args.from_list}: {e.strerror}") from e
    return targets_from_list(parse_list(text), root, Path.cwd())


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.rubric is not None:
            load_rubric(Path(args.rubric))
        if args.scans is not None:
            select_scans([s.strip() for s in args.scans.split(",") if s.strip()])
        if args.window is not None:
            parse_window(args.window)
        targets = _targets(args)
    except (RubricError, UnknownScanError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not targets:
        print("error: no repositories found (expected <path>/<repo>/<repo>-main)", file=sys.stderr)
        return 2
    out_dir = Path(args.out)
    inside = out_inside_target(out_dir, targets)
    if inside is not None:
        print(f"error: --out {args.out} is inside target {inside.repo}; the fleet run never "
              "writes into a target", file=sys.stderr)
        return 2
    options = ScanOptions(
        counts_only=not args.full,
        hash_salt=None if args.full else run_salt(args.hash_paths, args.hash_salt),
        window=args.window, scans=args.scans, timeout=args.timeout,
    )
    rows = run_fleet(targets, options, args.jobs)
    path = write_rows(out_dir, rows)
    failed = [row for row in rows if row["status"] == STATUS_FAILED]
    print(f"fleet: {len(rows)} repositories, {len(failed)} failed -> {path}", file=sys.stderr)
    for row in failed:
        print(f"  failed: {row['repo']} ({row['reason']})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
