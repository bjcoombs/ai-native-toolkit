"""A ghsync ``--from-list`` file of the CI include projects a fleet run could not read (issue #1356).

Reads ``OUT/run.json`` from a ``fleet.py --full`` run, checks each project in its
``not_cloned_includes`` through the GitLab API (``glab``, logged in to ``--host``), follows
their nested includes to fleet's nested-include depth, and writes the reachable projects to
``--output`` (default ``OUT/include-projects.txt``) in the format ghsync's ``--from-list``
reads. The summary, naming each unreachable project with its status (``not_found``,
``no_access`` or ``error``), goes to stdout. The logic lives in
``skills/assess/scripts/lib/fleet/fleet_include_list.py``.

Exit codes:
    0  list written (it may hold no project)
    2  unreadable inputs: no ``run.json``, or a run that does not name its include projects
       (run fleet with ``--full``)
    3  the GitLab host could not be read: ``glab`` missing, not logged in, or the host unreachable

Run:
    uv run fleet_include_list.py OUT --host gitlab.example.com [--output FILE]
"""
# /// script
# requires-python = ">=3.11"
# ///
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

# skills/fleet/scripts -> skills/assess/scripts (the plugin ships both skills)
ASSESS_SCRIPTS = Path(__file__).resolve().parents[2] / "assess" / "scripts"
sys.path.insert(0, str(ASSESS_SCRIPTS))

from lib.fleet.fleet_include_list import (
    GlabIncludeApi, IncludeApi, IncludeListError, list_file, report, seeds, walk)
from lib.hosting.gitlab_cli import normalize_host


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write a ghsync --from-list file of the reachable CI include projects a fleet run could not read.")
    parser.add_argument("out", help="The fleet run's --out directory (run.json), from a --full run.")
    parser.add_argument("--host", required=True, help="The GitLab host the includes name (glab must be logged in).")
    parser.add_argument("--output", metavar="FILE",
                        help="Where to write the list (default: OUT/include-projects.txt).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, api: IncludeApi | None = None) -> int:
    args = _parse_args(argv)
    out = Path(args.out)
    target = Path(args.output) if args.output else out / "include-projects.txt"
    try:
        run = json.loads((out / "run.json").read_text(encoding="utf-8"))
        if not isinstance(run, dict):
            raise IncludeListError("run.json is not a JSON object")
        start = seeds(run)
    except (OSError, json.JSONDecodeError, IncludeListError) as e:
        print(f"fleet_include_list: {e}", file=sys.stderr)
        return 2
    host = normalize_host(args.host)
    try:
        checked = walk(start, api if api is not None else GlabIncludeApi(host))
    except IncludeListError as e:
        print(f"fleet_include_list: GitLab host {host} could not be read: {e}", file=sys.stderr)
        return 3
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(list_file(checked), encoding="utf-8", newline="\n")
    sys.stdout.write(report(checked, target.as_posix(), host))
    return 0


if __name__ == "__main__":
    sys.exit(main())
