"""Diff a run of the calibration corpus against its expected-state files (issue #842).

Run by hand, against local clones of the corpus repositories at their pinned commits in a
ghsync layout (``<root>/<path>/<repo>/<repo>-main``). The logic is in
``lib/fleet/calibration_corpus.py``.

    uv run --locked scripts/calibration_corpus.py diff CLONE_ROOT [--corpus DIR] [--jobs N] [--platform]
    uv run --locked scripts/calibration_corpus.py seed CLONE_ROOT [--corpus DIR] [--jobs N] [--platform]
    uv run --locked scripts/calibration_corpus.py report [--corpus DIR] [--row ROW]

- ``diff`` refuses an expected file with no clone, a clone at another commit and a clone
  that fails to scan; otherwise it prints one line per condition whose state moved, then
  the non-discriminating conditions of the run.
- ``seed`` writes an expected-state file for every clone under the root, from this
  toolkit's run.
- ``report`` prints the non-discriminating conditions of the expected files alone; with
  ``--row`` (``backlog-ready``, #1324) it first prints how each of that row's conditions falls
  across the corpus, counts only. An unknown row, or one no file holds, is refused; files that lack
  the row are counted, since the per-state counts then sum to less than the corpus.
- ``--platform`` also reads the issue tracker for ``backlog_ready`` (``cc.PLATFORM_READS``)
  through the fleet platform pass, so level 2 of the backlog-ready row is measured; it needs
  network and gh or glab signed in. Seed and diff a corpus the same way: ``diff`` refuses a
  corpus seeded with it when run without it, and one seeded without it when run with it (#1628).

``diff`` and ``report`` refuse a corpus directory with no expected-state files, and ``seed``
a clone root with no clones; ``seed`` prints each key it writes.

Exit codes: 0 no diff (or written, or reported), 1 diff lines printed, 2 refused or a usage
error.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.fleet import calibration_corpus as cc
from lib.fleet.fleet_platform import DEFAULT_PLATFORM_TIMEOUT, run_platform
from lib.fleet.runner import FleetRow, ScanFn, ScanOptions, Target, discover_root, scan_target
from lib.scoring.level_tally import default_model


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diff the calibration corpus against its expected states.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("diff", "seed", "report"):
        p = sub.add_parser(name)
        if name != "report":
            p.add_argument("clone_root", type=Path, help="The ghsync root holding the corpus clones.")
            p.add_argument("--jobs", type=int, default=4, help="Clones scanned at a time (default: %(default)s).")
            p.add_argument("--platform", action="store_true",
                           help="Also read the issue tracker for backlog_ready (network, gh or glab).")
        else:
            p.add_argument("--row", help="Print how this row's conditions fall across the corpus.")
        p.add_argument("--corpus", type=Path, default=cc.CORPUS_DIR, help="The expected-state files.")
    return parser.parse_args(argv)


def _print(lines: list[str]) -> None:
    for line in lines:
        print(line)


def _platform(jobs: int) -> cc.PlatformFn:
    def read(rows: list[FleetRow], targets: Sequence[Target], options: ScanOptions) -> list[FleetRow]:
        return run_platform(rows, targets, options, jobs=jobs, timeout=DEFAULT_PLATFORM_TIMEOUT,
                            names=list(cc.PLATFORM_READS))
    return read


def _report(expected: list[cc.ExpectedState], row: str | None) -> int:
    """The non-discriminating conditions of the expected files, after ``row``'s split when given."""
    states = {e["repo"]: e["states"] for e in expected}
    if row:
        split = cc.row_split(states, row)
        if not split:
            print(f"error: no expected-state file holds a condition of row {row!r}: an unknown "
                  "row, or a corpus seeded before the row; reseed with seed --platform", file=sys.stderr)
            return 2
        lacking = sum(1 for s in states.values() if not any(c.startswith(f"{row}.") for c in s))
        _print(cc.split_lines(split, len(states)))
        if lacking:
            print(f"{lacking} of {len(states)} expected-state files hold no condition of {row}: reseed them")
    _print(cc.discrimination_lines(cc.discrimination(states)))
    return 0


def main(argv: list[str] | None = None, scan: ScanFn = scan_target,
         platform: cc.PlatformFn | None = None) -> int:
    args = _parse(argv)
    try:
        expected = cc.load_corpus(args.corpus)
    except cc.CorpusError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not expected and args.command != "seed":
        print(f"error: no expected-state files in {args.corpus}", file=sys.stderr)
        return 2
    if args.command == "report":
        return _report(expected, args.row)
    if not args.clone_root.is_dir():
        print(f"error: {args.clone_root} is not a directory", file=sys.stderr)
        return 2
    targets = discover_root(args.clone_root)
    model = default_model()
    if args.platform and platform is None:
        platform = _platform(args.jobs)
    if not args.platform:
        platform = None
    if args.command == "seed":
        if not targets:
            print(f"error: no clones under {args.clone_root} (ghsync layout <path>/<repo>/<repo>-main)",
                  file=sys.stderr)
            return 2
        observed, refused = cc.observe(targets, model, args.jobs, scan, platform)
        if refused:
            _print(cc.refusal_lines(refused))
            return 2
        for entry in cc.seed(observed, model):
            path = cc.write_expected(args.corpus, entry)
            print(f"wrote {path.stem}")
        print(f"wrote {len(observed)} expected-state files to {args.corpus}")
        return 0
    if args.platform != cc.platform_seeded(expected):
        if args.platform:
            print("error: the corpus was seeded without --platform (backlog-ready.2 is not measured "
                  "in any expected-state file); diff it without --platform, or reseed with it",
                  file=sys.stderr)
        else:
            print("error: the corpus was seeded with --platform (it measures backlog-ready.2); "
                  "diff it with --platform", file=sys.stderr)
        return 2
    refused = cc.commit_refusals(expected, targets)
    if refused:
        _print(cc.refusal_lines(refused))
        return 2
    keys = {e["repo"] for e in expected}
    corpus_targets = [t for t in targets if cc.repo_key(t.repo) in keys]
    observed, refused = cc.observe(corpus_targets, model, args.jobs, scan, platform)
    if refused:
        _print(cc.refusal_lines(refused))
        return 2
    lines = cc.diff_corpus(expected, observed)
    _print(lines)
    _print(cc.discrimination_lines(cc.discrimination({o["repo"]: o["states"] for o in observed})))
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
