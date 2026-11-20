"""Per-team next-steps pages from one fleet run's output (issue #571).

Reads ``OUT/repos.jsonl`` and ``OUT/groups.jsonl`` from a ``fleet.py`` run and
writes ``OUT/teams.md`` (an index) and one Markdown page per group under
``OUT/teams/``: the group's level on each maturity model row, the unmet criteria of
the next level as single-change steps with the repositories holding it back,
and for an ``unknown`` row the signals that could not be measured. No rescan;
the logic lives in ``skills/assess/scripts/lib/fleet_next_steps.py``.

Pass the maturity model and settings the fleet run used: every page level is checked
against ``groups.jsonl`` and a difference exits 2.

Exit codes:
    0  pages written
    2  unreadable or inconsistent inputs (a missing or malformed JSONL file, an
       invalid maturity model or settings, a maturity model or settings that differ from the run's)

Run:
    uv run fleet_next_steps.py OUT [--levels FILE] [--settings PATH]
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

from lib.fleet_groups import SettingsError, load_settings  # noqa: E402
from lib.fleet_view import FleetViewError, load_groups, read_repos  # noqa: E402
from lib.fleet_next_steps import NextStepsError, build_pages, write_pages  # noqa: E402
from lib.levels import add_renamed_flag, LevelsError, load_levels  # noqa: E402
from lib.level_signals import DEFAULT_LEVELS, signal_path_validator  # noqa: E402
from lib.run_context_types import JsonDict  # noqa: E402


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write per-team next-steps pages (Markdown) from a fleet run's output directory.")
    parser.add_argument("out", help="The fleet run's --out directory (repos.jsonl, groups.jsonl).")
    parser.add_argument("--levels", metavar="FILE", default=str(DEFAULT_LEVELS),
                        help="The maturity model the fleet run used (default: the shipped default maturity model).")
    parser.add_argument("--settings", metavar="PATH",
                        help="The ghreport settings the fleet run used, if any.")
    add_renamed_flag(parser)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out)
    settings: dict[str, JsonDict] = {}
    try:
        maturity_model = load_levels(Path(args.levels), signal_path_validator())
        if args.settings is not None:
            settings = load_settings(Path(args.settings))
        pages = build_pages(maturity_model, read_repos(out_dir), load_groups(out_dir), settings)
        written = write_pages(out_dir, pages)
    except (LevelsError, SettingsError, FleetViewError, NextStepsError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"fleet next steps: {len(written) - 1} team pages -> {out_dir / 'teams'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
