"""Compare two fleet runs without rescanning (issue #552).

Each argument is a run directory: a ``fleet.py --out`` directory or a run
history entry (``<history>/<org>/<date>/``), holding ``repos.jsonl``,
``groups.jsonl`` and ``run.json``. Prints the comparison JSON (the shape
``fleet.py --compare`` writes to ``compare.json``) to stdout, or writes it to
``--write FILE``, and a short summary to stderr. The logic lives in
``skills/assess/scripts/lib/fleet_compare.py``.

Exit codes:
    0  compared
    2  a run directory is missing or malformed
    3  the runs are not comparable (different rubric version, window, output
       mode, scan selection, path hashing, major plugin version or file
       version); the JSON still says why

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
    RunVersionError,
    compare,
    dumps_comparison,
    load_run,
    not_comparable,
    summary_lines,
)
from lib.fleet_inputs import FleetInputError  # noqa: E402

EXIT_NOT_COMPARABLE = 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two fleet runs (level and measured-set movement).")
    parser.add_argument("earlier", help="The earlier run directory.")
    parser.add_argument("later", help="The later run directory.")
    parser.add_argument("--write", metavar="FILE", help="Write the comparison JSON here instead of stdout.")
    args = parser.parse_args(argv)
    try:
        later = load_run(Path(args.later))
        try:
            comparison = compare(load_run(Path(args.earlier)), later)
        except RunVersionError as e:
            comparison = not_comparable(later.stamp, [f"earlier run: {e}"])
    except (RunVersionError, FleetInputError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    text = dumps_comparison(comparison)
    if args.write:
        try:
            Path(args.write).write_text(text, encoding="utf-8")
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
