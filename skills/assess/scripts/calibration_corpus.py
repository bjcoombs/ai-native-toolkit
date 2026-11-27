"""Diff a run of the calibration corpus against its expected-state files (issue #842).

Run by hand, against local clones of the corpus repositories at their pinned commits in a
ghsync layout (``<root>/<path>/<repo>/<repo>-main``). The logic is in
``lib/fleet/calibration_corpus.py``.

    uv run --locked scripts/calibration_corpus.py diff CLONE_ROOT [--corpus DIR] [--jobs N]
    uv run --locked scripts/calibration_corpus.py seed CLONE_ROOT [--corpus DIR] [--jobs N]
    uv run --locked scripts/calibration_corpus.py report [--corpus DIR]

- ``diff`` refuses an expected file with no clone, a clone at another commit and a clone
  that fails to scan; otherwise it prints one line per condition whose state moved, then
  the non-discriminating conditions of the run.
- ``seed`` writes an expected-state file for every clone under the root, from this
  toolkit's run.
- ``report`` prints the non-discriminating conditions of the expected files alone.

``diff`` and ``report`` refuse a corpus directory with no expected-state files.

Exit codes: 0 no diff (or written, or reported), 1 diff lines printed, 2 refused or a usage
error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.fleet import calibration_corpus as cc
from lib.fleet.runner import ScanFn, discover_root, scan_target
from lib.scoring.level_tally import default_model


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diff the calibration corpus against its expected states.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("diff", "seed", "report"):
        p = sub.add_parser(name)
        if name != "report":
            p.add_argument("clone_root", type=Path, help="The ghsync root holding the corpus clones.")
            p.add_argument("--jobs", type=int, default=4, help="Clones scanned at a time (default: %(default)s).")
        p.add_argument("--corpus", type=Path, default=cc.CORPUS_DIR, help="The expected-state files.")
    return parser.parse_args(argv)


def _print(lines: list[str]) -> None:
    for line in lines:
        print(line)


def main(argv: list[str] | None = None, scan: ScanFn = scan_target) -> int:
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
        _print(cc.discrimination_lines(cc.discrimination({e["repo"]: e["states"] for e in expected})))
        return 0
    if not args.clone_root.is_dir():
        print(f"error: {args.clone_root} is not a directory", file=sys.stderr)
        return 2
    targets = discover_root(args.clone_root)
    model = default_model()
    if args.command == "seed":
        observed, refused = cc.observe(targets, model, args.jobs, scan)
        if refused:
            _print(cc.refusal_lines(refused))
            return 2
        for entry in cc.seed(observed, model):
            cc.write_expected(args.corpus, entry)
        print(f"wrote {len(observed)} expected-state files to {args.corpus}")
        return 0
    refused = cc.commit_refusals(expected, targets)
    if refused:
        _print(cc.refusal_lines(refused))
        return 2
    keys = {e["repo"] for e in expected}
    corpus_targets = [t for t in targets if cc.repo_key(t.repo) in keys]
    observed, refused = cc.observe(corpus_targets, model, args.jobs, scan)
    if refused:
        _print(cc.refusal_lines(refused))
        return 2
    lines = cc.diff_corpus(expected, observed)
    _print(lines)
    _print(cc.discrimination_lines(cc.discrimination({o["repo"]: o["states"] for o in observed})))
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
