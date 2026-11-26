"""The ``skills/fleet/scripts/fleet.py`` command line: its flags and their usage rules.

``parse_args`` builds the parser, parses ``argv`` and enforces every rule that needs
no file read: ROOT or ``--from-list``, ``--discovered`` needs ROOT, the hashing flags
apply to counts-only output, positive ``--jobs``/``--timeout`` and platform limits, a
``--scan-jobs`` of 1 or more, by default ``scan_pool.share_of_cpus(--jobs)`` (#1381),
``--profile`` resolved to its shipped ``--levels`` file (``lib/profiles.py``, #1150),
``--monorepo-units`` and ``--pack`` need a model, and a ``--compare`` value that is not a run
directory (it most likely swallowed ROOT). A broken rule exits 2 through
``parser.error``. ``LATEST`` is the value a bare ``--compare`` takes: the newest
matching history entry.

Moved out of ``fleet.py`` (#1214) so the orchestrator stays under the file-size
ratchet's 800-line limit; the orchestrator re-exports ``parse_args`` as
``_parse_args``.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from lib.fleet.fleet_achievements import ACHIEVEMENTS_FILE
from lib.fleet.fleet_diagrams import REPORT_FILE
from lib.fleet.fleet_platform import DEFAULT_PLATFORM_JOBS, DEFAULT_PLATFORM_TIMEOUT
from lib.fleet.fleet_stamp import RUN_FILE
from lib.fleet.runner import DEFAULT_TIMEOUT, REPOS_FILE
from lib.scoring.levels import add_renamed_flag
from lib.profiles import ProfileError, add_model_arguments, profile_path
from lib.scan_pool import jobs_value, share_of_cpus
from lib.signals import platform_only_error


LATEST = "latest"  # bare --compare: the newest matching history entry


def _not_a_run(path: Path) -> bool:
    """A directory that holds neither ``run.json`` nor ``repos.jsonl`` (a ghsync root, say)."""
    return path.is_dir() and not (path / RUN_FILE).is_file() and not (path / REPOS_FILE).is_file()


def _model_rules(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """``--profile`` resolved to its ``--levels`` file, and the flags that need a model."""
    if args.profile is not None:
        # A profile is a shipped --levels file (lib/profiles.py, #1150): every --levels rule holds.
        if args.levels is not None:
            parser.error("--profile and --levels each choose the model: pass one")
        try:
            args.levels = str(profile_path(args.profile))
        except ProfileError as e:
            parser.error(str(e))
    if args.pack and args.levels is None:
        parser.error("--pack needs --levels or --profile: a pack's practices are scored with a maturity "
                     "model (the default one is skills/assess/levels/default.toml)")
    if args.monorepo_units and args.levels is None:
        parser.error("--monorepo-units needs --levels: a unit's Step comes from the maturity model")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
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
        "--levels", metavar="FILE",
        help="Maturity model (TOML, lib/scoring/levels.py format). Each group row in groups.jsonl "
             "carries every maturity model row's whole level (or unknown) and the aggregation rule used.",
    )
    add_model_arguments(parser, levels=False)
    parser.add_argument(
        "--pack", action="append", default=[], metavar="FILE",
        help="A practice pack (<name>.pack.toml, lib/packs.py) applied to every repository, in pass "
             "order: its checks are read from each repository's files and its [[rows]] are layered onto "
             "the model; the report, level.json, fleet-data.json and the page break results down per "
             "pack (lib/fleet/fleet_packs.py). Repeat for several. Needs --levels or --profile.",
    )
    parser.add_argument(
        "--settings", metavar="PATH",
        help="Group settings from ghreport (#565): a JSON fragment file, a JSON array of "
             "fragments, or a directory of fragment files (a ghreport --render-dir works: its "
             "settings/ subdirectory is read). Joined to each group by scope, nearest ancestor first.",
    )
    parser.add_argument(
        "--units", metavar="FILE",
        help="Org units mapping (TOML, lib/fleet/fleet_units.py format): declared units and repository "
             "placements for the ownership and system views of OUT/fleet-data.json. It overrides "
             "the tree inferred from repository paths, GitHub teams and CODEOWNERS.",
    )
    parser.add_argument(
        "--fleet-config", metavar="FILE",
        help="Fleet settings (TOML): the [ai_usage], [review] and [platform] sections, validated "
             "before any scan and passed to every repository scan. A repository's own "
             ".assess/config.toml sections extend it. run.json records its sha256.",
    )
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1,
                        help="Repositories scanned in parallel (default: CPU count).")
    parser.add_argument("--scan-jobs", type=jobs_value, default=None, metavar="N",
                        help="Scans run at a time inside each repository scan (assess_signals --jobs; "
                             "default: the CPU count divided by --jobs, capped at 8, at least 1, so the "
                             "default --jobs scans each repository one scan at a time). Rows are the "
                             "same for every N.")
    parser.add_argument("--window", default=None,
                        help="History window passed to assess_signals, e.g. 6mo, 90d (default: its own).")
    parser.add_argument("--scans", default=None,
                        help="Comma-separated scan names (default: all; under --levels or --profile, "
                             "the scans the model reads, lib/scan_plan.py).")
    parser.add_argument("--all-scans", action="store_true",
                        help="Under --levels or --profile, run every scan, not only those the model "
                             "reads. --scans, when given, still chooses the scans.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help="Seconds one repository may take before it is marked failed (default: %(default)s).")
    parser.add_argument("--full", action="store_true",
                        help="Keep full signal blocks (free text included) instead of counts-only.")
    parser.add_argument("--monorepo-units", action="store_true",
                        help="Scan each repository by monorepo unit too (assess_signals --units): "
                             "fleet-data.json gains unit rows. One scoped pass per unit. Needs --levels.")
    parser.add_argument("--hash-paths", action="store_true",
                        help="Hash repo-relative paths and repository ids under one salt drawn for this run "
                             "(hashes join within the run; reruns are not byte-identical).")
    parser.add_argument("--hash-salt", metavar="VALUE",
                        help="Hash paths and repository ids under this salt (implies --hash-paths); "
                             "reruns stay identical.")
    parser.add_argument(
        "--delete-after", action="store_true",
        help="Delete each ghsync clone (<repo>-main) once it is scanned ok, only when it has no "
             "local change, untracked or ignored file, stash, unpushed commit or linked worktree. "
             "Anything else is kept and listed on stderr.",
    )
    parser.add_argument(
        "--compare", metavar="RUN_DIR", nargs="?", const=LATEST,
        help="Compare this run with an earlier one and write OUT/compare.json plus a summary on "
             "stderr. RUN_DIR is an --out directory or a history entry; without it, the latest "
             "history entry whose stamp matches this run.",
    )
    parser.add_argument(
        "--history-dir", metavar="DIR",
        help="Run history root (default: ${XDG_DATA_HOME:-~/.local/share}/ai-native-toolkit/fleet). "
             "Each counts-only run is kept in DIR/<org>/<date>/.",
    )
    parser.add_argument("--history-org", metavar="NAME",
                        help="The <org> directory name in the history (default: from the repository ids).")
    parser.add_argument("--no-history", action="store_true", help="Do not keep this run in the history.")
    parser.add_argument(
        "--targets", metavar="FILE",
        help=f"Step targets (TOML, lib/fleet/fleet_targets.py): each [[target]] names a view, a unit and a "
             f"Step (step = 2 or higher). OUT/{ACHIEVEMENTS_FILE} credits a target when the unit's lowest "
             "Step reaches it and the report shows the gates still missing. Needs --levels with [[steps]]; "
             "not part of the run stamp.",
    )
    parser.add_argument(
        "--first-achievements", action="store_true",
        help=f"Also credit 'first' achievements in OUT/{ACHIEVEMENTS_FILE}: a unit reaching a level no "
             "unit of its kind has held on that row in this fleet's history. Off by default, since it "
             "compares a unit with its peers. Not part of the run stamp.",
    )
    parser.add_argument(
        "--no-diagrams", action="store_true",
        help=f"Do not draw the diagrams (OUT/{REPORT_FILE} and its SVGs); any a previous run left "
             "are removed.",
    )
    parser.add_argument(
        "--radar-min-measured", type=int, default=20, metavar="PERCENT",
        help="The practice radar (radar.svg) draws an axis for a practice measured in at least this "
             "percent of the run's repositories (default 20; 0 keeps every practice). Not part of the "
             "run stamp.",
    )
    parser.add_argument(
        "--managed-settings", action="store_true",
        help="Read the Claude Code managed settings of the machine running the fleet for the agent "
             "permissions practice (#1153): a central rule credits every repository scanned, so use "
             "it only when this machine carries the organisation's policy. Stamped in run.json. "
             "A repository's .assess/config.toml never turns it on.",
    )
    parser.add_argument(
        "--platform", action="store_true",
        help="After the offline scan, run a separate platform pass (gh or glab) for the scans in "
             "lib/signals.py PLATFORM_SCANS and join its blocks into each ok row. Needs gh or glab "
             "logged in; never changes a row's status. Without it no network call is made.",
    )
    parser.add_argument(
        "--platform-jobs", type=int, default=None, metavar="N",
        help="Repositories read in parallel by the platform pass (default: [platform] jobs in "
             f"--fleet-config, else {DEFAULT_PLATFORM_JOBS}).",
    )
    parser.add_argument(
        "--platform-timeout", type=int, default=None, metavar="SECONDS",
        help="Seconds one repository's platform read may take before its platform blocks are "
             f"unavailable (default: [platform] timeout in --fleet-config, else {DEFAULT_PLATFORM_TIMEOUT}).",
    )
    parser.add_argument(
        "--discovered", metavar="FILE",
        help="What ghsync discovered: its --porcelain or --porcelain-json output (one path or "
             "object per line). report.md and run.json then say how many of these repositories "
             "were assessed and count each gap: archived or empty, not cloned, could not be "
             "scanned, excluded by filters. Needs ROOT. Without it, coverage reads unknown.",
    )
    parser.add_argument(
        "--name-agent-clusters", action="store_true",
        help="Name the member repositories and groups of each agent cluster in "
             "OUT/agent_clusters.json. Off by default: a cluster carries counts and SDK slugs only.",
    )
    add_renamed_flag(parser)
    args = parser.parse_args(argv)
    if args.compare not in (None, LATEST) and _not_a_run(Path(args.compare)):
        parser.error(f"--compare took {args.compare!r} as the earlier run, but it holds no {RUN_FILE} "
                     f"or {REPOS_FILE}: did it swallow ROOT? Put ROOT first "
                     "(fleet.py ROOT --out DIR --compare [RUN_DIR]) or write --compare=RUN_DIR")
    if args.root is None and args.from_list is None:
        parser.error("give a ghsync ROOT or --from-list FILE")
    if args.discovered is not None and args.root is None:
        parser.error("--discovered needs ROOT, the ghsync root its paths are under")
    if args.full and (args.hash_paths or args.hash_salt is not None):
        parser.error("--hash-paths and --hash-salt apply to counts-only output, not --full")
    if args.hash_salt == "":
        parser.error("--hash-salt needs a non-empty value")
    if not 0 <= args.radar_min_measured <= 100:
        parser.error("--radar-min-measured must be from 0 to 100")
    if args.jobs < 1 or args.timeout < 1:
        parser.error("--jobs and --timeout must be at least 1")
    if args.scan_jobs is None:  # the CPUs split between the repositories scanned at once (#1381)
        args.scan_jobs = share_of_cpus(args.jobs)
    if any(value is not None and value < 1 for value in (args.platform_jobs, args.platform_timeout)):
        parser.error("--platform-jobs and --platform-timeout must be at least 1")
    if not args.platform and (args.platform_jobs is not None or args.platform_timeout is not None):
        parser.error("--platform-jobs and --platform-timeout apply to --platform")
    _model_rules(parser, args)
    if args.scans is not None:
        problem = platform_only_error([s.strip() for s in args.scans.split(",")], args.platform)
        if problem is not None:
            parser.error(problem)
    return args
