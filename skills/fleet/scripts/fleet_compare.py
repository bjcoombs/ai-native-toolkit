"""Compare two fleet runs without rescanning (issue #552).

Each argument is a run directory: a ``fleet.py --out`` directory or a run
history entry (``<history>/<org>/<date>/``), holding ``repos.jsonl``,
``groups.jsonl`` and ``run.json``. Prints the comparison JSON (the shape
``fleet.py --compare`` writes to ``compare.json``) to stdout, or writes it to
``--write FILE``, and a short summary to stderr. The logic lives in
``skills/assess/scripts/lib/fleet_compare.py``.

When the two runs were written by different toolkit commits, a moved team row whose
signals come from a changed scanner module carries ``cause`` ``scanner_changed`` and
``scanner`` (each block and its changed modules under ``skills/assess/scripts/lib/``),
diffed with git in this toolkit checkout. When the diff is unavailable (a run with no
``toolkit_commit`` at another ``plugin_version``, or a commit git cannot find) every
moved team row carries ``toolkit_changed_unknown_scope`` and ``notes`` says the scanner
diff was unavailable. A row whose unknown side lacks a signal the other run's scans hold
is ``cause`` ``set`` with ``absent_signals``: measured-set movement, not a level move
(``lib/fleet_scanner_diff.py``, #774).

Exit codes:
    0  compared
    2  a run directory is missing or malformed (a stamp missing a key, a bad
       JSON line), or --write cannot be written
    3  the runs are not comparable (different maturity-model version, window, output
       mode, scan selection, path hashing, fleet config, major plugin version, or either
       run's file versions); the JSON still says why

Run:
    uv run fleet_compare.py EARLIER LATER [--write FILE]
"""
# /// script
# requires-python = ">=3.11"
# ///
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

# skills/fleet/scripts -> skills/assess/scripts (the plugin ships both skills)
ASSESS_SCRIPTS = Path(__file__).resolve().parents[2] / "assess" / "scripts"
sys.path.insert(0, str(ASSESS_SCRIPTS))

from lib.fleet_compare import (  # noqa: E402
    FleetRun,
    RunVersionError,
    compare,
    dumps_comparison,
    load_run,
    not_comparable,
    summary_lines,
    write_comparison_file,
)
from lib.fleet_scanner_diff import scanner_diff  # noqa: E402
from lib.fleet_view import FleetViewError  # noqa: E402

EXIT_NOT_COMPARABLE = 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two fleet runs (level and measured-set movement).")
    parser.add_argument("earlier", help="The earlier run directory.")
    parser.add_argument("later", help="The later run directory.")
    parser.add_argument("--write", metavar="FILE", help="Write the comparison JSON here instead of stdout.")
    args = parser.parse_args(argv)
    runs: dict[str, FleetRun] = {}
    problems: list[str] = []
    try:
        for side, path in (("earlier", args.earlier), ("later", args.later)):
            try:
                runs[side] = load_run(Path(path))
            except RunVersionError as e:  # another file version: not comparable, either side
                problems.append(f"{side} run: {e}")
    except FleetViewError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if problems:
        comparison = not_comparable(runs["later"].stamp if "later" in runs else None, problems,
                                    runs["earlier"].stamp if "earlier" in runs else None)
    else:
        comparison = compare(runs["earlier"], runs["later"],
                             scanner_diff(runs["earlier"].stamp, runs["later"].stamp))
    text = dumps_comparison(comparison)
    if args.write:
        try:
            write_comparison_file(Path(args.write), comparison)
        except OSError as e:
            print(f"error: cannot write {args.write}: {e.strerror}", file=sys.stderr)
            return 2
    else:
        sys.stdout.write(text)
    for line in summary_lines(comparison):
        print(f"fleet compare: {line}", file=sys.stderr)
    return 0 if comparison["comparable"] else EXIT_NOT_COMPARABLE


if __name__ == "__main__":
    sys.exit(main())
