"""Fleet runner: headless /assess signals across a ghsync root or a list (issue #567).

Runs ``skills/assess/scripts/assess_signals.py`` once per repository, each in
its own process from a worker pool, counts-only by default, and writes one JSON
line per repository to ``<out>/repos.jsonl``, sorted by repository id. A scan
process that exits non-zero leaves its stderr tail in ``<out>/logs/<repo>.stderr``. The row
shape is ``skills/fleet/references/repos.schema.json``; the logic lives in
``skills/assess/scripts/lib/fleet/runner.py``. It then rolls the rows up to one
line per group in ``<out>/groups.jsonl`` (``lib/fleet/fleet_groups.py``, schema
``skills/fleet/references/groups.schema.json``): repository counts, distinct
shared CI sources and marketplaces, the joined ``--settings`` and each
``--levels`` row's whole level, plus each group's agent-building class counts.
With ``--levels`` each scanned repository's verified tally, condition states and
autonomy Step go to ``<out>/level.json`` (``lib/fleet/fleet_level.py``, schema
``skills/fleet/references/level.schema.json``; #744).
Similar ``builds-agent`` repositories across groups are clustered into
``<out>/agent_clusters.json`` (``lib/fleet/fleet_agent_building.py``): counts and SDK
slugs only, member repositories named only with ``--name-agent-clusters``.
``<out>/fleet-data.json`` (``lib/fleet/fleet_data.py``, schema
``skills/fleet/references/fleet-data.schema.json``) holds the org units tree per view
(``lib/fleet/fleet_units.py``; ``--units`` maps it), each unit's roll-ups and each repository's
units, web URL and per-row level, value and reason. ``<out>/achievements.json``
(``lib/fleet/fleet_achievements.py``, schema ``skills/fleet/references/achievements.schema.json``)
replays the comparable runs in the history and credits each unit's level-ups, streaks
and ``--targets`` hit (firsts only with ``--first-achievements``), with each unit's
nearest level-up; the
report opens with them ("What changed and who earned it", ``lib/fleet/fleet_credit.py``).
With ``--levels``, ``<out>/condition_effect.json`` (``lib/fleet/condition_effect.py``, #1086)
estimates each condition's effect on the agent-outcome shares across this run and the
comparable history, with its interval and sample size; informational, it moves nothing.

Target repositories are never written to. With ``--delete-after`` a scanned
clone is removed once its row is in hand, only when nothing in it would be
lost (``lib/fleet/fleet_cleanup.py``). Every run also writes the run stamp
``<out>/run.json`` (``lib/fleet/fleet_stamp.py``). A docs repository that declares
``[[describes]]`` mappings gets one row per mapping in ``<out>/doc_freshness.jsonl``
(``lib/fleet/fleet_doc_freshness.py``); without any, no such file is written.

It also draws the fleet diagrams (``lib/fleet/fleet_diagrams.py``): ``<out>/report.md``
embedding ``quadrant.svg``, ``shared-tooling.svg``, ``trend.svg`` (group trend lines
over the run history, ``lib/fleet/fleet_trends.py``), and ``slope.svg`` (from the ``--compare``
result), or the reason each was skipped;
``--no-diagrams`` turns them off. ``report.md`` opens with the coverage line: with
``--discovered FILE`` (ghsync's ``--porcelain`` or ``--porcelain-json`` listing) how many of
the discoverable repositories were assessed and each gap counted, also in ``run.json``
(``lib/fleet/fleet_coverage.py``, #745); without it, coverage unknown. After the credit section
comes "Opportunities" (``lib/fleet/fleet_opportunities.py``, #746), also in ``fleet-data.json``.

``--fleet-config FILE`` (``lib/fleet_config.py``, #706) is validated before any scan
and every repository scan gets the same copy of it; a repository's own
``.assess/config.toml`` sections extend it for that repository. ``run.json``
records its canonical sha256 and per-section entry counts, never a value, and
runs with different configs do not compare.

``--platform`` (``lib/fleet/fleet_platform.py``, #714) runs a separate platform pass after
the offline scan: one more ``assess_signals.py --platform`` child per ok repository
(``--platform-jobs`` at a time, ``--platform-timeout`` each; defaults from the
``[platform]`` section of ``--fleet-config``) whose ``gh``/``glab`` blocks
(``lib/signals.py`` ``PLATFORM_SCANS``) are joined into the row by repository. It
never changes a row's status; a failed read is an unavailable block. ``run.json``
records ``platform`` (true only when a platform read ran: ``--scans`` selecting no
platform scan stamps false), and runs with and without it do not compare. With
``--delete-after`` a clone is deleted only after its platform read.

``--monorepo-units`` (#866) scans each repository by monorepo unit: every child gets
``assess_signals.py --units``, and ``fleet-data.json`` gives each repository its
``unit_rows`` (``lib/fleet/fleet_data_units.py``), one Step, next blocking gate and per-tag
tally per unit. A unit is named only by its folder's hash under ``--hash-paths``. It costs
one scoped pass per unit and needs ``--levels`` (exit 2 without it): a unit's Step comes
from the model.

GitLab includes (#766): every scan of a ROOT run gets ``--ghsync-root ROOT``, so a
repository's ``include: project:`` / ``component:`` files are read from the clones
in the same root (``lib/ci_includes.py``) and ``ci_checks`` and ``review_automation``
see the jobs in shared templates; a template that is not cloned stays unknown.
A ``--from-list`` run passes ROOT when one is given. With ``--delete-after`` no root
is passed (a template clone could be deleted before a repository including it is
scanned), so those includes stay unknown.

A counts-only run is also kept in the run history,
``${XDG_DATA_HOME:-~/.local/share}/ai-native-toolkit/fleet/<org>/<date>/``
(``--history-dir``, ``--history-org``, ``--no-history``; ``lib/fleet/fleet_history.py``).
``--compare [RUN_DIR]`` compares this run with an earlier one (by default the
latest history entry whose stamp matches) and writes ``<out>/compare.json``
(``lib/fleet/fleet_compare.py``).

Exit codes:
    0  the run completed and repos.jsonl and run.json were written. Repositories that could
       not be scanned are rows with "status": "failed" and a reason token; the
       count is printed to stderr. Failures never change the exit code.
    2  usage error: no root and no --from-list, a root or list that does not
       exist, no repositories found, --out inside a target, an unreadable
       --levels, --settings or --units (a settings directory with no fragment files included,
       or a units file naming a missing unit),
       a list entry whose ~user cannot be expanded, bad scan arguments, an --out that
       cannot be created or written (checked before any scan), an unusable history
       location, an unreadable --compare run, or an unreadable or invalid
       --fleet-config (the message names the key), or an unreadable or malformed
       --discovered listing (the message names the line)

Run:
    uv run fleet.py ROOT --out DIR [--levels FILE] [--settings PATH] [--units FILE] [--jobs N] [--window 6mo]
        [--scans a,b] [--timeout SECONDS] [--full | --hash-paths | --hash-salt VALUE]
        [--delete-after] [--compare [RUN_DIR]] [--history-dir DIR] [--history-org NAME]
        [--no-history] [--no-diagrams] [--fleet-config FILE] [--name-agent-clusters] [--discovered FILE]
        [--platform [--platform-jobs N] [--platform-timeout SECONDS]]
    uv run fleet.py --from-list FILE [ROOT] --out DIR [...]
"""
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     # Each scan runs assess_signals.py with this interpreter, so these are
#     # its layer 2-4 dependencies (lib/headless_layers.py): the import graph
#     # and the file size and complexity scores. Without them each block
#     # reports unavailable with a named reason. lizard is pinned to the
#     # treemap's version.
#     "networkx",
#     "grimp",
#     "lizard==1.23.0",
# ]
# ///
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

# skills/fleet/scripts -> skills/assess/scripts (the plugin ships both skills)
ASSESS_SCRIPTS = Path(__file__).resolve().parents[2] / "assess" / "scripts"
sys.path.insert(0, str(ASSESS_SCRIPTS))

from lib.fleet.runner import (  # noqa: E402
    DEFAULT_TIMEOUT,
    FleetRow,
    LOGS_DIR,
    REPOS_FILE,
    STATUS_FAILED,
    STATUS_OK,
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
from lib.fleet.fleet_agent_building import clusters, write_clusters  # noqa: E402
from lib.fleet.fleet_doc_freshness import freshness_rows, summary_line, write_freshness  # noqa: E402
from lib.fleet.fleet_cleanup import CloneDeleter  # noqa: E402
from lib.fleet.fleet_coverage import Discovered, DiscoveryError, coverage_line, read_discovery, reconcile  # noqa: E402
from lib.fleet.fleet_compare import (  # noqa: E402
    Comparison,
    FleetRun,
    RunVersionError,
    clear_stale_comparison,
    compare,
    load_run,
    not_comparable,
    summary_lines,
    write_comparison,
)
from lib.fleet.fleet_diagrams import (  # noqa: E402
    REPORT_FILE,
    build_diagrams,
    clear_diagrams,
    write_diagrams,
)
from lib.fleet.fleet_achievements import (  # noqa: E402
    ACHIEVEMENTS_FILE,
    Achievements,
    HistoryRun,
    build_achievements,
    clear_achievements,
    write_achievements,
)
from lib.fleet.fleet_achievements_runs import earlier_runs  # noqa: E402
from lib.fleet.fleet_credit import credit_section  # noqa: E402
from lib.fleet.fleet_opportunities_section import opportunities_section  # noqa: E402
from lib.fleet.fleet_level import write_fleet_levels  # noqa: E402
from lib.fleet.condition_effect import (  # noqa: E402
    CONDITION_EFFECT_FILE,
    HistoryRun as EffectRun,
    build_condition_effect,
    history_runs as effect_history,
    write_condition_effect,
)
from lib.measured_appendix import measured_section  # noqa: E402
from lib.fleet.fleet_data import FLEET_DATA_FILE, FleetData, build_fleet_data, privacy_for, write_fleet_data  # noqa: E402
from lib.fleet.fleet_tag_profile import FleetTagRow  # noqa: E402
from lib.fleet.fleet_targets import TargetSpec, TargetsError, check_units, load_targets, resolve  # noqa: E402
from lib.fleet_config import FleetConfig, FleetConfigError, config_sha256, parse_fleet_config, read_fleet_config  # noqa: E402
from lib.fleet.fleet_groups import GroupRow, SettingsError, load_settings, roll_up, write_groups  # noqa: E402
from lib.fleet.fleet_hashed import public_freshness, public_groups, public_ids, public_rows  # noqa: E402
from lib.fleet.fleet_repo_facts import collect_facts  # noqa: E402
from lib.fleet.fleet_units import (  # noqa: E402
    Tree,
    UnitsError,
    UnitsMap,
    build_trees,
    empty_map,
    load_units,
    repo_owners,
    unmatched_repos,
)
from lib.fleet.fleet_history import (  # noqa: E402
    HistoryError,
    choose_entry,
    default_org,
    entry_dir,
    history_root,
    latest_matching,
    persist,
    safe_component,
)
from lib.fleet.fleet_platform import (  # noqa: E402
    DEFAULT_PLATFORM_JOBS,
    DEFAULT_PLATFORM_TIMEOUT,
    platform_scans,
    run_platform,
)
from lib.fleet.fleet_scanner_diff import row_signals, scanner_diff  # noqa: E402
from lib.fleet.fleet_trends import TrendRun, history_runs, series_runs, trend_run  # noqa: E402
from lib.fleet.fleet_view import FleetViewError  # noqa: E402
from lib.fleet.fleet_stamp import FLEET_STAMP_VERSION, RUN_FILE, FleetStamp, build_stamp, write_stamp  # noqa: E402
from lib.levels import add_renamed_flag, LevelsError, load_levels, MaturityModel  # noqa: E402
from lib.level_signals import signal_path_validator  # noqa: E402
from lib.run_context_types import JsonDict  # noqa: E402
from lib.tool_lookup import warn_missing_packages  # noqa: E402
from lib.signals import (  # noqa: E402
    DEFAULT_WINDOW,
    TOOLKIT_ROOT,
    UnknownScanError,
    parse_window,
    platform_only_error,
    read_plugin_version,
    select_scans,
    toolkit_commit,
)


LATEST = "latest"  # bare --compare: the newest matching history entry


def _not_a_run(path: Path) -> bool:
    """A directory that holds neither ``run.json`` nor ``repos.jsonl`` (a ghsync root, say)."""
    return path.is_dir() and not (path / RUN_FILE).is_file() and not (path / REPOS_FILE).is_file()


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
        "--levels", metavar="FILE",
        help="Maturity model (TOML, lib/levels.py format). Each group row in groups.jsonl "
             "carries every maturity model row's whole level (or unknown) and the aggregation rule used.",
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
    parser.add_argument("--window", default=None,
                        help="History window passed to assess_signals, e.g. 6mo, 90d (default: its own).")
    parser.add_argument("--scans", default=None, help="Comma-separated scan names (default: all).")
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
    if args.jobs < 1 or args.timeout < 1:
        parser.error("--jobs and --timeout must be at least 1")
    if any(value is not None and value < 1 for value in (args.platform_jobs, args.platform_timeout)):
        parser.error("--platform-jobs and --platform-timeout must be at least 1")
    if not args.platform and (args.platform_jobs is not None or args.platform_timeout is not None):
        parser.error("--platform-jobs and --platform-timeout apply to --platform")
    if args.monorepo_units and args.levels is None:
        parser.error("--monorepo-units needs --levels: a unit's Step comes from the maturity model")
    if args.scans is not None:
        problem = platform_only_error([s.strip() for s in args.scans.split(",")], args.platform)
        if problem is not None:
            parser.error(problem)
    return args


def include_root(args: argparse.Namespace) -> Path | None:
    """The root GitLab includes resolve against (#766): ROOT, unless --delete-after, where a
    template clone could be deleted before a repository that includes it is scanned, so a
    row would depend on scan order. Then includes of other projects stay unknown."""
    if args.root is None or args.delete_after:
        return None
    return Path(args.root).resolve()


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


def _history_org_dir(args: argparse.Namespace, targets: list[Target]) -> Path:
    """``<history root>/<org>``; raises ``HistoryError`` for an unsafe name or a symlinked directory."""
    root = Path(args.history_dir) if args.history_dir else history_root(os.environ, Path.home())
    org = (safe_component(args.history_org, "--history-org") if args.history_org is not None
           else default_org(t.repo for t in targets))
    return entry_dir(root, org, "x").parent  # validates root and org


def _history_entry(args: argparse.Namespace, targets: list[Target], org_dir: Path | None, scan_date: str,
                   planned: JsonDict, repos: frozenset[str]) -> Path | None:
    """The history directory this run will be kept in, created; None when it is not kept.

    Raises ``HistoryError`` or ``OSError`` when the location cannot be used.
    """
    if args.no_history:
        return None
    if args.full:
        print("fleet: history: --full output is not kept in the run history", file=sys.stderr)
        return None
    assert org_dir is not None  # resolved by main whenever the history is kept
    entry, passed = choose_entry(org_dir.parent, org_dir.name, scan_date, planned, repos)
    inside = out_inside_target(entry, targets)
    if inside is not None:
        raise HistoryError(f"history directory {entry} is inside target {inside.repo}")
    for reason in passed:
        print(f"fleet: history: kept {reason}", file=sys.stderr)
    if passed:
        print(f"fleet: history: this run goes to {entry.name} beside it", file=sys.stderr)
    entry.mkdir(parents=True, exist_ok=True)
    return entry


def _load_earlier(run_dir: str | None, org_dir: Path | None, planned: JsonDict,
                  repos: frozenset[str]) -> FleetRun | str | None:
    """The ``--compare`` run, the reason it cannot be compared, or None (no ``--compare``, or
    no matching history entry for a bare ``--compare``).

    Raises ``FleetViewError`` when the directory is missing or malformed.
    """
    if run_dir is None:
        return None
    if run_dir == LATEST:
        assert org_dir is not None  # resolved by main for a bare --compare
        latest = latest_matching(org_dir, planned, repos)
        if latest is None:
            print(f"fleet: compare: no earlier run in {org_dir} matches this run's stamp; "
                  "nothing compared", file=sys.stderr)
            return None
        print(f"fleet: compare: against {latest}", file=sys.stderr)
        run_dir = str(latest)
    try:
        return load_run(Path(run_dir))
    except RunVersionError as e:
        return str(e)


def _keep_and_compare(out_dir: Path, entry: Path | None, later: FleetRun,
                      earlier: FleetRun | str | None) -> Comparison | None:
    """Keep the run in the history and write ``compare.json``; neither ever loses the ``--out`` results.

    Returns the comparison written, None when nothing was compared."""
    if entry is not None:
        try:
            kept = persist(out_dir, entry, later.stamp, frozenset(row["repo"] for row in later.repos))
            print(f"fleet: history -> {kept}", file=sys.stderr)
        except (HistoryError, OSError) as e:
            print(f"fleet: history: not recorded ({e}); results are in {out_dir}", file=sys.stderr)
    if earlier is None:  # nothing to compare: a compare.json left by an earlier run would mislead
        clear_stale_comparison(out_dir)
        return None
    comparison = (compare(earlier, later, scanner_diff(earlier.stamp, later.stamp))
                  if isinstance(earlier, FleetRun)
                  else not_comparable(later.stamp, [earlier]))
    write_comparison(out_dir, comparison)
    for line in summary_lines(comparison):
        print(f"fleet: compare: {line}", file=sys.stderr)
    return comparison


def _condition_effect(out_dir: Path, maturity_model: MaturityModel | None, stamp: FleetStamp,
                      rows: list[FleetRow], org_dir: Path | None, entry: Path | None) -> None:
    """``OUT/condition_effect.json`` (lib/fleet/condition_effect.py, #1086): each condition's
    outcome effect over the comparable history and this run. Only with ``--levels``; a stale
    copy is removed without it. Never raises: the estimate never fails a completed run."""
    path = out_dir / CONDITION_EFFECT_FILE
    try:
        if maturity_model is None:
            path.unlink(missing_ok=True)
            return
        repos = frozenset(row["repo"] for row in rows)
        earlier = [] if org_dir is None or entry is None else effect_history(org_dir, stamp, repos)
        write_condition_effect(out_dir, build_condition_effect(
            maturity_model, [*earlier, EffectRun(stamp["scan_date"], rows)]))
    except Exception as e:  # noqa: BLE001 - informational, never fails a completed run
        print(f"fleet: {CONDITION_EFFECT_FILE} not written ({type(e).__name__})", file=sys.stderr)


def _trend_history(org_dir: Path | None, entry: Path | None, stamp: FleetStamp,
                   groups: list[GroupRow], rows: list[FleetRow],
                   maturity_model: MaturityModel | None = None) -> list[TrendRun] | None:
    """The trend's runs: earlier comparable history entries, then this run from memory; None
    when the run is not kept in the history or had no maturity model. Never raises."""
    current = trend_run(stamp, groups)
    if org_dir is None or entry is None or current is None:
        return None
    try:
        repos = frozenset(row["repo"] for row in rows)
        return series_runs(history_runs(org_dir, stamp, repos, maturity_model), current)
    except Exception as e:  # noqa: BLE001 - the history never fails a completed run
        print(f"fleet: diagrams: run history not read ({type(e).__name__})", file=sys.stderr)
        return None


def _diagrams(out_dir: Path, no_diagrams: bool, maturity_model: MaturityModel | None, rows: list[FleetRow],
              window: str, *, history: list[TrendRun] | None = None,
              comparison: Comparison | None = None, credit: str | None = None,
              coverage: str | None = None, opportunities: Callable[[], str | None] | None = None,
              tag_profile: list[FleetTagRow] | None = None) -> None:
    """Draw the diagrams and their report, or clear them; a failure never changes the exit code."""
    try:
        if no_diagrams:
            clear_diagrams(out_dir)
            return
        diagrams = build_diagrams(maturity_model, rows, window, history=history, comparison=comparison,
                                  tag_profile=tag_profile)
        ok = [row["scans"] for row in rows if row["status"] == "ok" and row["scans"] is not None]
        write_diagrams(out_dir, diagrams, credit, coverage, opportunities() if opportunities else None,
                       measured_section(ok))
    except Exception as e:  # noqa: BLE001 - a diagram never fails a completed run
        print(f"fleet: diagrams: not written ({type(e).__name__}: {e})", file=sys.stderr)
        return
    for diagram in diagrams:
        if diagram["skipped"] is not None:
            print(f"fleet: diagrams: {diagram['file']} {diagram['skipped']}", file=sys.stderr)
    print(f"fleet: diagrams -> {out_dir / REPORT_FILE}", file=sys.stderr)


def _achievements(out_dir: Path, data: FleetData, stamp: FleetStamp, maturity_model: MaturityModel | None,
                  org_dir: Path | None, entry: Path | None, repos: frozenset[str], specs: list[TargetSpec],
                  privacy_key: Callable[[str], str], firsts: bool = False) -> tuple[str | None, Achievements | None]:
    """``OUT/achievements.json`` (lib/fleet/fleet_achievements.py), the report's credit section and
    the document; both None when not written. Never raises: achievements never fail a run."""
    try:
        if org_dir is None or entry is None:
            earlier, note = [], "this run is not kept in the run history (--no-history or --full)"
        else:
            earlier, note = earlier_runs(org_dir, stamp, repos, model=maturity_model, key=privacy_key), None
        doc = build_achievements(HistoryRun(stamp["scan_date"], data), earlier, maturity_model,
                                 targets=resolve(specs, privacy_key), note=note, firsts=firsts)
        write_achievements(out_dir, doc)
        section = credit_section(doc, maturity_model)
    except Exception as e:  # noqa: BLE001 - achievements never fail a completed run
        clear_achievements(out_dir)
        print(f"fleet: achievements: not written ({type(e).__name__}: {e})", file=sys.stderr)
        return None, None
    mine = sum(1 for a in doc["achievements"] if a["run"] == doc["run"])
    print(f"fleet: achievements: {mine} earned this run over {len(doc['runs'])} comparable run(s) "
          f"-> {ACHIEVEMENTS_FILE}", file=sys.stderr)
    for t in doc["targets"]:
        if t["lowest_step"] is None:
            print(f"fleet: targets: {t['view']} {t['unit']} has no measured Step this run", file=sys.stderr)
    return section, doc


def _write_fleet_data(out_dir: Path, stamp: FleetStamp, rows: list[FleetRow],
                      maturity_model: MaturityModel | None, trees: list[Tree], full: bool,
                      hash_salt: str | None, units: UnitsMap, codeowners: dict[str, list[str]],
                      web_urls: dict[str, str | None]) -> FleetData:
    """``OUT/fleet-data.json`` (lib/fleet/fleet_data.py), before the run is kept in the history."""
    data = build_fleet_data(stamp, rows, maturity_model, trees,
                            owners=repo_owners(units, codeowners, (row["repo"] for row in rows)),
                            web_urls=web_urls, privacy=privacy_for(full, hash_salt))
    write_fleet_data(out_dir, data)
    print(f"fleet: units: {sum(len(v['units']) for v in data['views'])} units -> {FLEET_DATA_FILE}",
          file=sys.stderr)
    return data


PlatformPass = Callable[[list[FleetRow], ScanOptions], list[FleetRow]]


def _scan_all(targets: list[Target], options: ScanOptions, jobs: int, deleter: CloneDeleter | None,
              fleet_config_text: str | None, platform: PlatformPass | None = None) -> list[FleetRow]:
    """Every scan, then the ``--platform`` pass when given. With ``--fleet-config``, each reads
    one private copy of the text validated before any scan, by absolute path, so an edit
    mid-run cannot reach some scans only. With a platform pass, ``--delete-after`` waits for it."""
    def scan(scan_options: ScanOptions) -> list[FleetRow]:
        rows = run_fleet(targets, scan_options, jobs, after=None if platform is not None else deleter)
        return platform(rows, scan_options) if platform is not None else rows

    if fleet_config_text is None:
        return scan(options)
    with tempfile.TemporaryDirectory(prefix="fleet-config-") as tmp:
        copy = Path(tmp).resolve() / "fleet-config.toml"
        copy.write_text(fleet_config_text, encoding="utf-8", newline="\n")
        return scan(replace(options, fleet_config=copy))


def _platform_pass(args: argparse.Namespace, targets: list[Target], fleet_config: FleetConfig | None,
                   scans: list[str] | None, deleter: CloneDeleter | None) -> PlatformPass | None:
    """``--platform``: the separate pass run after the offline scan and joined into its rows
    (``lib/fleet/fleet_platform.py``); None without it."""
    if not args.platform:
        return None
    section = fleet_config["platform"] if fleet_config is not None else {}
    jobs = args.platform_jobs or section.get("jobs") or DEFAULT_PLATFORM_JOBS
    timeout = args.platform_timeout or section.get("timeout") or DEFAULT_PLATFORM_TIMEOUT
    names = platform_scans(scans)

    def platform(rows: list[FleetRow], options: ScanOptions) -> list[FleetRow]:
        ok = sum(row["status"] == STATUS_OK for row in rows)
        print(f"fleet: platform: {', '.join(names) or 'no platform scan selected by --scans'} "
              f"over {ok} repositories, {jobs} at a time", file=sys.stderr)
        return run_platform(rows, targets, options, jobs=jobs, timeout=timeout, names=names, after=deleter)
    return platform


def utc_today() -> date:
    """The run date, today in UTC: ``scan_date`` in ``run.json``, ``fleet-data.json`` and
    ``achievements.json``, and the history entry name. Read once per run; the tests pin it
    so runs either side of 00:00Z stay byte-identical (#1029)."""
    return datetime.now(timezone.utc).date()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    maturity_model: MaturityModel | None = None
    scans: list[str] | None = None
    settings: dict[str, JsonDict] = {}
    units: UnitsMap = empty_map()
    fleet_config: FleetConfig | None = None
    fleet_config_text: str | None = None
    discovered: list[Discovered] | None = None
    try:
        if args.fleet_config is not None:
            fleet_config_text = read_fleet_config(Path(args.fleet_config))
            fleet_config = parse_fleet_config(fleet_config_text)
        if args.levels is not None:
            maturity_model = load_levels(Path(args.levels), signal_path_validator())
        if args.settings is not None:
            settings = load_settings(Path(args.settings))
        if args.units is not None:
            units = load_units(Path(args.units))
        if args.scans is not None:
            scans = select_scans([s.strip() for s in args.scans.split(",") if s.strip()])
        if args.window is not None:
            parse_window(args.window)
        step_targets = load_targets(Path(args.targets), maturity_model) if args.targets is not None else []
        targets = _targets(args)
        if args.discovered is not None:
            discovered = read_discovery(Path(args.discovered), Path(args.root).resolve())
    except DiscoveryError as e:
        print(f"error: --discovered {args.discovered}: {e}", file=sys.stderr)
        return 2
    except TargetsError as e:
        print(f"error: --targets {args.targets}: {e}", file=sys.stderr)
        return 2
    except FleetConfigError as e:
        print(f"error: --fleet-config {args.fleet_config}: {e}", file=sys.stderr)
        return 2
    except (LevelsError, SettingsError, UnitsError, UnknownScanError, ValueError, OSError) as e:
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
    # Web URLs and CODEOWNERS are read before any scan, so --delete-after cannot remove a
    # checkout first. The trees are built before anything is written, so a mapping that
    # names a missing unit exits 2 and leaves nothing behind.
    facts = collect_facts(targets, args.jobs)
    codeowners = {repo: list(f.owners) for repo, f in facts.items()}
    try:
        trees = build_trees((t.repo for t in targets), units, settings, codeowners)
    except UnitsError as e:
        print(f"error: --units {args.units}: {e}", file=sys.stderr)
        return 2
    try:
        check_units(step_targets, trees, frozenset(t.repo for t in targets))
    except TargetsError as e:
        print(f"error: --targets {args.targets}: {e}", file=sys.stderr)
        return 2
    for repo in unmatched_repos(units, (t.repo for t in targets)):
        print(f"fleet: units: {repo} is mapped but not in this run; skipped", file=sys.stderr)
    today = utc_today()
    window = args.window or DEFAULT_WINDOW
    hash_salt = None if args.full else run_salt(args.hash_paths, args.hash_salt)
    # stamped only when a platform read runs: --scans can select no platform scan
    platform_ran = args.platform and bool(platform_scans(scans))
    planned: JsonDict = {
        "fleet_stamp_version": FLEET_STAMP_VERSION,
        "levels_version": maturity_model["version"] if maturity_model is not None else None, "window": window,
        "counts_only": not args.full, "scans": sorted(scans) if scans is not None else None,
        "paths_hashed": hash_salt is not None,
        "fleet_config_sha256": config_sha256(fleet_config) if fleet_config is not None else None,
        "platform": platform_ran,
    }
    keeps_history = not args.no_history and not args.full
    ids = public_ids((t.repo for t in targets), hash_salt)  # the ids every output file names (#909)
    try:
        # <org> is resolved only when the history is used: kept, or searched by a bare --compare
        org_dir = (_history_org_dir(args, targets) if keeps_history or args.compare == LATEST
                   else None)
        # read before any scan: the same-day history entry may be replaced
        earlier = _load_earlier(args.compare, org_dir, planned, ids)
    except HistoryError as e:
        print(f"error: run history: {e} (use --history-org, --history-dir or --no-history)",
              file=sys.stderr)
        return 2
    except FleetViewError as e:
        print(f"error: --compare {args.compare}: {e}", file=sys.stderr)
        return 2
    options = ScanOptions(
        counts_only=not args.full,
        hash_salt=hash_salt,
        window=args.window, scans=args.scans, timeout=args.timeout,
        log_dir=out_dir / LOGS_DIR, active_since=window_start(window, today),
        ghsync_root=include_root(args), units=args.monorepo_units,
    )
    try:  # before any scan, so --delete-after never removes a clone whose results cannot land
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"error: cannot create --out {args.out}: {e.strerror}", file=sys.stderr)
        return 2
    try:
        tempfile.TemporaryFile(dir=out_dir).close()  # writable: the results land after every scan
    except OSError as e:
        print(f"error: cannot write to --out {args.out}: {e.strerror}", file=sys.stderr)
        return 2
    try:
        entry = _history_entry(args, targets, org_dir, today.isoformat(), planned, ids)
    except (HistoryError, OSError) as e:
        print(f"error: run history: {e} (use --history-org, --history-dir or --no-history)",
              file=sys.stderr)
        return 2
    protected = [out_dir, Path.cwd(), TOOLKIT_ROOT, *([entry] if entry is not None else [])]
    deleter = CloneDeleter(protected) if args.delete_after else None
    # Cross-repo doc freshness (#664) reads both checkouts: before any scan, so
    # --delete-after cannot remove one first. Opt-in: nothing without [[describes]].
    freshness = freshness_rows(targets, full=args.full, hash_salt=hash_salt)
    for message in freshness.errors:
        print(f"fleet: describes: {message}", file=sys.stderr)
    freshness_path = write_freshness(out_dir, public_freshness(freshness.rows, hash_salt))
    # Each scan runs with this interpreter: one hint here, not one per repository.
    warn_missing_packages()
    platform = _platform_pass(args, targets, fleet_config, scans, deleter)
    rows = _scan_all(targets, options, args.jobs, deleter, fleet_config_text, platform)
    public = public_rows(rows, hash_salt)  # fleet-data.json and the coverage read the real ids
    path = write_rows(out_dir, public)
    groups = public_groups(roll_up(rows, maturity_model, settings, counts_only=not args.full), hash_salt)
    groups_path = write_groups(out_dir, groups)
    coverage = (None if discovered is None else
                reconcile(discovered, rows, Path(args.root).resolve(), frozenset(t.repo for t in targets)))
    agent_clusters = clusters(public, name=args.name_agent_clusters)
    write_clusters(out_dir, agent_clusters)
    stamp = build_stamp(
        public, toolkit_commit=toolkit_commit(), plugin_version=read_plugin_version(),
        today=today, window=window, levels_version=maturity_model["version"] if maturity_model is not None else None,
        counts_only=options.counts_only, paths_hashed=options.hash_salt is not None, scans=scans,
        fleet_config=fleet_config, platform=platform_ran, coverage=coverage,
    )
    write_stamp(out_dir, stamp)
    write_fleet_levels(out_dir, public, maturity_model, toolkit_commit=stamp["toolkit_commit"],
                       plugin_version=stamp["plugin_version"])
    _condition_effect(out_dir, maturity_model, stamp, public, org_dir, entry)
    data = _write_fleet_data(out_dir, stamp, rows, maturity_model, trees, args.full, hash_salt, units, codeowners,
                             {repo: f.web_url for repo, f in facts.items()})
    comparison = _keep_and_compare(out_dir, entry, FleetRun(stamp, public, groups, row_signals(data)), earlier)
    credit, achieved = _achievements(out_dir, data, stamp, maturity_model, org_dir, entry,
                           ids, step_targets,
                           privacy_for(args.full, hash_salt).key, args.first_achievements)
    history = None if args.no_diagrams else _trend_history(org_dir, entry, stamp, groups, public, maturity_model)
    _diagrams(out_dir, args.no_diagrams, maturity_model, public, window, history=history, comparison=comparison,
              credit=credit, coverage=coverage_line(coverage, len(rows)),
              opportunities=lambda: opportunities_section(data, achieved), tag_profile=data.get("tag_profile"))
    failed = [row for row in rows if row["status"] == STATUS_FAILED]
    print(f"fleet: {len(rows)} repositories, {len(failed)} failed -> {path}, {groups_path.name}",
          file=sys.stderr)
    print(f"fleet: agent clusters: {len(agent_clusters['clusters'])} over "
          f"{agent_clusters['clustered_repos']} of {agent_clusters['builds_agent_repos']} builds-agent "
          "repositories", file=sys.stderr)
    for row in failed:
        print(f"  failed: {row['repo']} ({row['reason']})", file=sys.stderr)
    if freshness_path is not None:
        print(f"fleet: doc freshness: {summary_line(freshness.rows)} -> {freshness_path.name}",
              file=sys.stderr)
    if deleter is not None:
        _report_deletions(deleter)
    return 0


if __name__ == "__main__":
    sys.exit(main())
