"""Fleet runner: headless /assess signals across a ghsync root or a list (issue #567).

Runs ``skills/assess/scripts/assess_signals.py`` once per repository, each in
its own process from a worker pool, counts-only by default, and writes one JSON
line per repository to ``<out>/repos.jsonl``, sorted by repository id. A scan
process that exits non-zero leaves its stderr tail in ``<out>/logs/<repo>.stderr``. The row
shape is ``skills/fleet/references/repos.schema.json``; the logic lives in
``skills/assess/scripts/lib/fleet.py``. It then rolls the rows up to one
line per group in ``<out>/groups.jsonl`` (``lib/fleet_groups.py``, schema
``skills/fleet/references/groups.schema.json``): repository counts, distinct
shared CI sources and marketplaces, the joined ``--settings`` and each
``--rubric`` row's whole level.

Target repositories are never written to. With ``--delete-after`` a scanned
clone is removed once its row is in hand, only when nothing in it would be
lost (``lib/fleet_cleanup.py``). Every run also writes the run stamp
``<out>/run.json`` (``lib/fleet_stamp.py``).

Exit codes:
    0  the run completed and repos.jsonl and run.json were written. Repositories that could
       not be scanned are rows with "status": "failed" and a reason token; the
       count is printed to stderr. Failures never change the exit code.
    2  usage error: no root and no --from-list, a root or list that does not
       exist, no repositories found, --out inside a target, an unreadable
       --rubric or --settings, bad scan arguments, or an --out that cannot be
       created

Run:
    uv run fleet.py ROOT --out DIR [--rubric FILE] [--settings PATH] [--jobs N] [--window 6mo]
        [--scans a,b] [--timeout SECONDS] [--full | --hash-paths | --hash-salt VALUE]
        [--delete-after]
    uv run fleet.py --from-list FILE [ROOT] --out DIR [...]
"""
# /// script
# requires-python = ">=3.11"
# ///
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

# skills/fleet/scripts -> skills/assess/scripts (the plugin ships both skills)
ASSESS_SCRIPTS = Path(__file__).resolve().parents[2] / "assess" / "scripts"
sys.path.insert(0, str(ASSESS_SCRIPTS))

from lib.fleet import (  # noqa: E402
    DEFAULT_TIMEOUT,
    LOGS_DIR,
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
from lib.fleet_activity import window_start  # noqa: E402
from lib.fleet_cleanup import CloneDeleter  # noqa: E402
from lib.fleet_groups import SettingsError, load_settings, roll_up, write_groups  # noqa: E402
from lib.fleet_stamp import build_stamp, write_stamp  # noqa: E402
from lib.rubric import Rubric, RubricError, load_rubric  # noqa: E402
from lib.rubric_signals import signal_path_validator  # noqa: E402
from lib.run_context_types import JsonDict  # noqa: E402
from lib.signals import (  # noqa: E402
    DEFAULT_WINDOW,
    TOOLKIT_ROOT,
    UnknownScanError,
    parse_window,
    read_plugin_version,
    select_scans,
    toolkit_commit,
)


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
        help="Maturity rubric (TOML, lib/rubric.py format). Each group row in groups.jsonl "
             "carries every rubric row's whole level (or unknown) and the aggregation rule used.",
    )
    parser.add_argument(
        "--settings", metavar="PATH",
        help="Group settings from ghreport (#565): a JSON fragment file, a JSON array of "
             "fragments, or a directory of fragment files (a ghreport --render-dir works: its "
             "settings/ subdirectory is read). Joined to each group by scope, nearest ancestor first.",
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
    parser.add_argument(
        "--delete-after", action="store_true",
        help="Delete each ghsync clone (<repo>-main) once it is scanned ok, only when it has no "
             "local change, untracked or ignored file, stash, unpushed commit or linked worktree. "
             "Anything else is kept and listed on stderr.",
    )
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


def _report_deletions(deleter: CloneDeleter) -> None:
    kept = deleter.kept()
    print(f"fleet: --delete-after deleted {len(deleter.deleted())} clones, kept {len(kept)}", file=sys.stderr)
    for repo, reason in kept:
        print(f"  kept: {repo} ({reason})", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    rubric: Rubric | None = None
    scans: list[str] | None = None
    settings: dict[str, JsonDict] = {}
    try:
        if args.rubric is not None:
            rubric = load_rubric(Path(args.rubric), signal_path_validator())
        if args.settings is not None:
            settings = load_settings(Path(args.settings))
        if args.scans is not None:
            scans = [s.strip() for s in args.scans.split(",") if s.strip()]
            select_scans(scans)
        if args.window is not None:
            parse_window(args.window)
        targets = _targets(args)
    except (RubricError, SettingsError, UnknownScanError, ValueError, OSError) as e:
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
    today = datetime.now(timezone.utc).date()
    window = args.window or DEFAULT_WINDOW
    options = ScanOptions(
        counts_only=not args.full,
        hash_salt=None if args.full else run_salt(args.hash_paths, args.hash_salt),
        window=args.window, scans=args.scans, timeout=args.timeout,
        log_dir=out_dir / LOGS_DIR, active_since=window_start(window, today),
    )
    try:  # before any scan, so --delete-after never removes a clone whose results cannot land
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"error: cannot create --out {args.out}: {e.strerror}", file=sys.stderr)
        return 2
    deleter = CloneDeleter([out_dir, Path.cwd(), TOOLKIT_ROOT]) if args.delete_after else None
    rows = run_fleet(targets, options, args.jobs, after=deleter)
    path = write_rows(out_dir, rows)
    groups_path = write_groups(out_dir, roll_up(rows, rubric, settings, counts_only=not args.full))
    write_stamp(out_dir, build_stamp(
        rows, toolkit_commit=toolkit_commit(), plugin_version=read_plugin_version(),
        today=today, window=window, rubric_version=rubric["version"] if rubric is not None else None,
        counts_only=options.counts_only, paths_hashed=options.hash_salt is not None, scans=scans,
    ))
    failed = [row for row in rows if row["status"] == STATUS_FAILED]
    print(f"fleet: {len(rows)} repositories, {len(failed)} failed -> {path}, {groups_path.name}",
          file=sys.stderr)
    for row in failed:
        print(f"  failed: {row['repo']} ({row['reason']})", file=sys.stderr)
    if deleter is not None:
        _report_deletions(deleter)
    return 0


if __name__ == "__main__":
    sys.exit(main())
