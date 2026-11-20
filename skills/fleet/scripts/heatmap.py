"""Leadership heat map from one fleet run (issue #570).

Reads ``<out>/groups.jsonl`` written by ``fleet.py --levels`` and writes one SVG:
groups as rows, maturity model rows as columns, each cell the whole level reached,
``unknown`` hatched apart from level 0 with the missing signal named in a key.
No rescan, no model, no network: the same run gives a byte-identical file. The
logic lives in ``skills/assess/scripts/lib/fleet_heatmap.py`` and the shared
reader in ``lib/fleet_view.py``.

Exit codes:
    0  the SVG was written
    2  usage error: unreadable or malformed groups.jsonl, a run without maturity model
       levels, a --levels file that is not the run's maturity model, or an unreadable
       --compare run

Run:
    uv run heatmap.py RUN [--levels FILE] [--compare EARLIER_RUN] [--output FILE]
"""
# /// script
# requires-python = ">=3.11"
# ///
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

# skills/fleet/scripts -> skills/assess/scripts (the plugin ships both skills)
ASSESS_SCRIPTS = Path(__file__).resolve().parents[2] / "assess" / "scripts"
sys.path.insert(0, str(ASSESS_SCRIPTS))

from lib.fleet_compare import view_check  # noqa: E402
from lib.fleet_heatmap import columns_for, render_heatmap  # noqa: E402
from lib.fleet_groups import GroupRow  # noqa: E402
from lib.fleet_view import (  # noqa: E402
    Comparison,
    FleetViewError,
    check_levels,
    compare_levels,
    groups_file,
    load_groups,
)
from lib.levels import add_renamed_flag, LevelsError, load_levels, MaturityModel  # noqa: E402

HEATMAP_FILE = "heatmap.svg"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the leadership heat map SVG from a fleet run's groups.jsonl.",
    )
    parser.add_argument("run", help="The fleet run: its --out directory or its groups.jsonl.")
    parser.add_argument(
        "--levels", metavar="FILE",
        help="The maturity model file the run used: gives column titles and each row's top level, so "
             "colour is scaled per row. Refused when its version or rows differ from the run's.",
    )
    parser.add_argument(
        "--compare", metavar="EARLIER_RUN",
        help="An earlier fleet run (directory or groups.jsonl): each cell shows its change. "
             "Not shown, with a note, when the maturity-model versions differ.",
    )
    parser.add_argument("--output", metavar="FILE",
                        help=f"SVG path (default: {HEATMAP_FILE} beside groups.jsonl).")
    add_renamed_flag(parser)
    return parser.parse_args(argv)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _compare(run: Path, earlier: Path, groups: list[GroupRow]) -> Comparison:
    """The level change per cell, gated by the fleet comparison rule (``lib/fleet_compare.py``).

    Two stamped run directories are refused on any fleet_compare reason (window,
    scans and the rest, not only the maturity-model version), and a note names the groups
    whose measured set moved. A bare ``groups.jsonl`` compares levels only.
    """
    reasons, set_moved = view_check(run, earlier)  # before load_groups: a version refusal is a note
    if reasons:
        return {"comparable": False, "changes": {}, "removed_groups": [],
                "note": "change not shown: " + "; ".join(reasons)}
    comparison = compare_levels(groups, load_groups(earlier))
    if set_moved and comparison["comparable"]:
        return {**comparison, "note": "measured set changed in " + ", ".join(set_moved)
                + ": a change there may not be a level change (fleet_compare.py names the cause)"}
    return comparison


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    run = Path(args.run)
    maturity_model: MaturityModel | None = None
    comparison: Comparison | None = None
    try:
        groups = load_groups(run)
        if args.levels is not None:
            maturity_model = load_levels(Path(args.levels))
            check_levels(maturity_model, groups)
        columns = columns_for(groups, maturity_model)
        if args.compare is not None:
            comparison = _compare(run, Path(args.compare), groups)
    except (FleetViewError, LevelsError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    out = Path(args.output) if args.output else groups_file(run).parent / HEATMAP_FILE
    _write(out, render_heatmap(groups, columns, comparison))
    print(f"heatmap: {len(groups)} groups x {len(columns)} maturity model rows -> {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
