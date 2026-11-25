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
opening with the front screen (``lib/fleet/fleet_front.py``, #1255) and
embedding ``shared-tooling.svg``, ``trend.svg`` (group trend lines
over the run history, ``lib/fleet/fleet_trends.py``), and ``slope.svg`` (from the ``--compare``
result; and the Step diagrams of #941), or the reason each was skipped, and ``fleet-view.html`` (#946);
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

``--pack FILE`` (#1276, repeatable; needs a model) applies the same practice packs to every
repository: each is read once before any scan (a load error exits 2), every scan reads a
private copy in pass order, the packs' rows are layered onto the model, ``run.json`` names each
pack, and ``level.json``, ``fleet-data.json``, ``report.md`` and the page break results down per
pack (``lib/fleet/fleet_packs.py``).

``--monorepo-units`` (#866) scans each repository by monorepo unit: every child gets
``assess_signals.py --units``, and ``fleet-data.json`` gives each repository its
``unit_rows`` (``lib/fleet/fleet_data_units.py``), one Step, next blocking gate and per-tag
tally per unit. A unit is named only by its folder's hash under ``--hash-paths``. It costs
one scoped pass per unit and needs ``--levels`` (exit 2 without it): a unit's Step comes
from the model.

GitLab includes (#766): every scan of a ROOT run gets ``--ghsync-root ROOT``, so a
repository's ``include: project:`` / ``component:`` files are read from the clones
in the same root (``lib/ci/ci_includes.py``) and ``ci_checks`` and ``review_automation``
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
       a list entry whose ~user cannot be expanded, a --pack that does not load or layer, bad scan arguments, an --out that
       cannot be created or written (checked before any scan), an unusable history
       location, an unreadable --compare run, or an unreadable or invalid
       --fleet-config (the message names the key), or an unreadable or malformed
       --discovered listing (the message names the line)

Run:
    uv run fleet.py ROOT --out DIR [--levels FILE | --profile NAME] [--settings PATH] [--units FILE] [--jobs N] [--window 6mo]
        [--scans a,b] [--timeout SECONDS] [--full | --hash-paths | --hash-salt VALUE]
        [--delete-after] [--compare [RUN_DIR]] [--history-dir DIR] [--history-org NAME]
        [--no-history] [--no-diagrams] [--fleet-config FILE] [--name-agent-clusters] [--discovered FILE]
        [--platform [--platform-jobs N] [--platform-timeout SECONDS]] [--managed-settings] [--pack FILE ...]
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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

# skills/fleet/scripts -> skills/assess/scripts (the plugin ships both skills)
ASSESS_SCRIPTS = Path(__file__).resolve().parents[2] / "assess" / "scripts"
sys.path.insert(0, str(ASSESS_SCRIPTS))

from lib.fleet.runner import (
    FleetRow,
    LOGS_DIR,
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
from lib.fleet_activity import window_start
from lib.fleet.fleet_agent_building import AgentClusters, clusters, write_clusters
from lib.fleet.fleet_doc_freshness import FreshnessResult, freshness_rows, summary_line, write_freshness
from lib.fleet.fleet_cleanup import CloneDeleter
from lib.fleet.fleet_coverage import Discovered, DiscoveryError, coverage_line, read_discovery, reconcile
from lib.fleet.fleet_compare import (
    Comparison,
    FleetRun,
    RunVersionError,
    clear_stale_comparison,
    compare,
    load_run,
    not_comparable,
    comparison_lines,
    write_comparison,
)
from lib.fleet.fleet_diagrams import REPORT_FILE, build_diagrams, clear_diagrams, step_moves_for, write_diagrams
from lib.fleet.fleet_front import front_section, guarded_front
from lib.fleet.fleet_step_movement import StepMoves
from lib.fleet.fleet_page_data import mode_of
from lib.fleet.fleet_page import write_page
from lib.fleet.fleet_achievements import (
    ACHIEVEMENTS_FILE,
    Achievements,
    HistoryRun,
    build_achievements,
    clear_achievements,
    write_achievements,
)
from lib.fleet.fleet_achievements_runs import earlier_runs
from lib.fleet.fleet_credit import credit_section
from lib.fleet.fleet_opportunities_section import opportunities_section
from lib.fleet.fleet_cli_args import LATEST, parse_args as _parse_args
from lib.fleet.fleet_level import write_fleet_levels
from lib.fleet.fleet_packs import FleetPacks, fleet_model, load_fleet_packs, pack_copies, packs_section
from lib.fleet.condition_effect import (
    CONDITION_EFFECT_FILE,
    HistoryRun as EffectRun,
    build_condition_effect,
    history_runs as effect_history,
    write_condition_effect,
)
from lib.fleet.condition_demotion import DEMOTIONS_FILE, decide, with_demotions, write_demotions
from lib.measured_appendix import measured_section
from lib.fleet.fleet_data import FLEET_DATA_FILE, FleetData, build_fleet_data, privacy_for, write_fleet_data
from lib.fleet.fleet_targets import TargetSpec, TargetsError, check_units, load_targets, resolve
from lib.fleet_config import FleetConfig, FleetConfigError, config_sha256, parse_fleet_config, read_fleet_config
from lib.fleet.fleet_groups import GroupRow, SettingsError, load_settings, roll_up, write_groups
from lib.fleet.fleet_hashed import public_freshness, public_groups, public_ids, public_rows
from lib.fleet.fleet_repo_facts import RepoFacts, collect_facts
from lib.fleet.fleet_units import (
    Tree,
    UnitsError,
    UnitsMap,
    build_trees,
    empty_map,
    load_units,
    repo_owners,
    unmatched_repos,
)
from lib.fleet.fleet_history import (
    HistoryError,
    choose_entry,
    default_org,
    entry_dir,
    history_root,
    latest_matching,
    persist,
    safe_component,
)
from lib.fleet.fleet_platform import (
    DEFAULT_PLATFORM_JOBS,
    DEFAULT_PLATFORM_TIMEOUT,
    platform_scans,
    run_platform,
)
from lib.fleet.fleet_scanner_diff import row_signals, scanner_diff
from lib.fleet.fleet_trends import TrendRun, history_runs, series_runs, trend_run
from lib.fleet.fleet_view import FleetViewError
from lib.fleet.fleet_method import rules_sha256
from lib.fleet.fleet_stamp import FLEET_STAMP_VERSION, FleetStamp, build_stamp, write_stamp
from lib.levels import LevelsError, MaturityModel
from lib.packs import PackError
from lib.run_context_types import JsonDict
from lib.scan_plan import fleet_scans
from lib.tool_lookup import warn_missing_packages
from lib.signals import (
    DEFAULT_WINDOW,
    TOOLKIT_ROOT,
    UnknownScanError,
    known_scans,
    parse_window,
    read_plugin_version,
    select_scans,
    toolkit_commit,
)


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
    for line in comparison_lines(comparison):
        print(f"fleet: compare: {line}", file=sys.stderr)
    return comparison


def _condition_effect(out_dir: Path, maturity_model: MaturityModel | None, stamp: FleetStamp,
                      rows: list[FleetRow], org_dir: Path | None, entry: Path | None) -> list[str]:
    """``OUT/condition_effect.json`` (lib/fleet/condition_effect.py) and the exit rule's
    ``OUT/demotions.json`` (lib/fleet/condition_demotion.py), #1086: each condition's outcome
    effect over the comparable history and this run, and the conditions it demotes, which the
    returned ids apply to this run's level results. Only with ``--levels``; stale copies are
    removed without it. Never raises: on a failure nothing is demoted and the run completes."""
    paths = (out_dir / CONDITION_EFFECT_FILE, out_dir / DEMOTIONS_FILE)
    try:
        for path in paths:
            path.unlink(missing_ok=True)
        if maturity_model is None:
            return []
        repos = frozenset(row["repo"] for row in rows)
        earlier = [] if org_dir is None or entry is None else effect_history(org_dir, stamp, repos)
        effects = build_condition_effect(maturity_model, [*earlier, EffectRun(stamp["scan_date"], rows)])
        write_condition_effect(out_dir, effects)
        demotions = decide(maturity_model, effects, stamp["scan_date"])
        write_demotions(out_dir, demotions)
        if demotions["demoted"]:
            print(f"fleet: exit rule: {len(demotions['demoted'])} condition(s) demoted to informational: "
                  f"{', '.join(demotions['demoted'])}", file=sys.stderr)
        return demotions["demoted"]
    except Exception as e:  # noqa: BLE001 - informational, never fails a completed run
        for path in paths:  # never leave an earlier run's files beside this run's results
            path.unlink(missing_ok=True)
        print(f"fleet: {CONDITION_EFFECT_FILE} and {DEMOTIONS_FILE} not written ({type(e).__name__}); "
              "no demotions applied", file=sys.stderr)
        return []


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
              data: FleetData | None = None, earlier: FleetRun | str | None = None, achieved: Achievements | None = None,
              groups: Sequence[str] = ()) -> None:
    """Draw the diagrams, their report and fleet-view.html (#946), or clear them; a failure never changes the exit code."""
    try:
        if no_diagrams:
            clear_diagrams(out_dir)
            return
        def moves_of() -> StepMoves | None:  # one computation: the grid and the front screen
            if maturity_model is None or not maturity_model.get("steps") or data is None or earlier is None:
                return None
            return step_moves_for(maturity_model, data, earlier, comparison)
        # guarded: a raise becomes a skip line, so the report is still written and stale files cleared
        front, moves = guarded_front(maturity_model, data, moves_of, groups,
                                     mode_of(data) if data is not None else None)
        diagrams = build_diagrams(maturity_model, rows, window, history=history, comparison=comparison,
                                  data=data, earlier=earlier, moves=moves)
        ok = [row["scans"] for row in rows if row["status"] == "ok" and row["scans"] is not None]
        write_diagrams(out_dir, diagrams, credit, coverage, opportunities() if opportunities else None,
                       measured_section(ok), front_section(front, len(rows)),
                       packs_section(data.get("packs") if data is not None else None))
        write_page(out_dir, data, achieved, maturity_model, diagrams, front)
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
              fleet_config_text: str | None, platform: PlatformPass | None = None,
              packs: FleetPacks = FleetPacks()) -> list[FleetRow]:
    """Every scan, then the ``--platform`` pass when given. With ``--fleet-config`` or ``--pack``,
    each reads one private copy of each text validated before any scan, by absolute path, so an
    edit mid-run cannot reach some scans only. With a platform pass, ``--delete-after`` waits for it."""
    def scan(scan_options: ScanOptions) -> list[FleetRow]:
        rows = run_fleet(targets, scan_options, jobs, after=None if platform is not None else deleter)
        return platform(rows, scan_options) if platform is not None else rows

    if fleet_config_text is None and not packs.packs:
        return scan(options)
    with tempfile.TemporaryDirectory(prefix="fleet-config-") as tmp:
        options = replace(options, packs=pack_copies(Path(tmp).resolve(), packs))
        if fleet_config_text is None:
            return scan(options)
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


@dataclass
class _Inputs:
    """Every input file read and validated before anything is scanned or written."""

    targets: list[Target]
    step_targets: list[TargetSpec]
    maturity_model: MaturityModel | None = None
    scans: list[str] | None = None
    settings: dict[str, JsonDict] = field(default_factory=dict)
    units: UnitsMap = field(default_factory=empty_map)
    fleet_config: FleetConfig | None = None
    fleet_config_text: str | None = None
    discovered: list[Discovered] | None = None
    packs: FleetPacks = field(default_factory=FleetPacks)


def _usage_error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def _read_inputs(args: argparse.Namespace) -> _Inputs:
    """The input files; raises the loaders' errors, which ``_load_inputs`` turns into exit 2."""
    fleet_config_text = read_fleet_config(Path(args.fleet_config)) if args.fleet_config is not None else None
    fleet_config = parse_fleet_config(fleet_config_text) if fleet_config_text is not None else None
    packs = load_fleet_packs(args.pack)  # --pack: read once, layered onto the model (#1276)
    model = (fleet_model(Path(args.levels), args.profile or Path(args.levels).name, packs.packs)
             if args.levels is not None else None)
    inputs = _Inputs(targets=[], step_targets=[], maturity_model=model,
                     fleet_config=fleet_config, fleet_config_text=fleet_config_text, packs=packs)
    if args.settings is not None:
        inputs.settings = load_settings(Path(args.settings))
    if args.units is not None:
        inputs.units = load_units(Path(args.units))
    if args.scans is not None:
        inputs.scans = select_scans([s.strip() for s in args.scans.split(",") if s.strip()])
    elif (planned := fleet_scans(model, known_scans(), args.all_scans)) is not None:
        # --levels / --profile: only the scans the model reads, the same plan for every repository (#1268)
        inputs.scans = select_scans(planned)
    if args.window is not None:
        parse_window(args.window)
    if args.targets is not None:
        inputs.step_targets = load_targets(Path(args.targets), model)
    inputs.targets = _targets(args)
    if args.discovered is not None:
        inputs.discovered = read_discovery(Path(args.discovered), Path(args.root).resolve())
    return inputs


def _load_inputs(args: argparse.Namespace) -> _Inputs | int:
    """The validated inputs, or exit 2 with the message naming the flag at fault."""
    try:
        inputs = _read_inputs(args)
    except DiscoveryError as e:
        return _usage_error(f"--discovered {args.discovered}: {e}")
    except TargetsError as e:
        return _usage_error(f"--targets {args.targets}: {e}")
    except FleetConfigError as e:
        return _usage_error(f"--fleet-config {args.fleet_config}: {e}")
    except PackError as e:
        return _usage_error(f"--pack: {e}")
    except (LevelsError, SettingsError, UnitsError, UnknownScanError, ValueError, OSError) as e:
        return _usage_error(str(e))
    if not inputs.targets:
        return _usage_error("no repositories found (expected <path>/<repo>/<repo>-main)")
    return inputs


def _units_trees(args: argparse.Namespace, inputs: _Inputs,
                 out_dir: Path) -> tuple[dict[str, RepoFacts], dict[str, list[str]], list[Tree]] | int:
    """Each repository's facts, its CODEOWNERS owners and the units trees, or exit 2."""
    targets = inputs.targets
    inside = out_inside_target(out_dir, targets)
    if inside is not None:
        return _usage_error(f"--out {args.out} is inside target {inside.repo}; the fleet run never "
                            "writes into a target")
    # Web URLs and CODEOWNERS are read before any scan, so --delete-after cannot remove a
    # checkout first. The trees are built before anything is written, so a mapping that
    # names a missing unit exits 2 and leaves nothing behind.
    facts = collect_facts(targets, args.jobs)
    codeowners = {repo: list(f.owners) for repo, f in facts.items()}
    try:
        trees = build_trees((t.repo for t in targets), inputs.units, inputs.settings, codeowners)
    except UnitsError as e:
        return _usage_error(f"--units {args.units}: {e}")
    try:
        check_units(inputs.step_targets, trees, frozenset(t.repo for t in targets))
    except TargetsError as e:
        return _usage_error(f"--targets {args.targets}: {e}")
    for repo in unmatched_repos(inputs.units, (t.repo for t in targets)):
        print(f"fleet: units: {repo} is mapped but not in this run; skipped", file=sys.stderr)
    return facts, codeowners, trees


def _writable_out(args: argparse.Namespace, out_dir: Path) -> int | None:
    """None when ``out_dir`` exists and takes a file, else exit 2: checked before any scan,
    so --delete-after never removes a clone whose results cannot land."""
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return _usage_error(f"cannot create --out {args.out}: {e.strerror}")
    try:
        tempfile.TemporaryFile(dir=out_dir).close()
    except OSError as e:
        return _usage_error(f"cannot write to --out {args.out}: {e.strerror}")
    return None


def _print_summary(rows: list[FleetRow], path: Path, groups_path: Path, agent_clusters: AgentClusters,
                   freshness: FreshnessResult, freshness_path: Path | None,
                   deleter: CloneDeleter | None) -> None:
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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    loaded = _load_inputs(args)
    if isinstance(loaded, int):
        return loaded
    inputs = loaded
    targets, step_targets, units = inputs.targets, inputs.step_targets, inputs.units
    maturity_model, scans, settings = inputs.maturity_model, inputs.scans, inputs.settings
    fleet_config, fleet_config_text, discovered = inputs.fleet_config, inputs.fleet_config_text, inputs.discovered
    out_dir = Path(args.out)
    known = _units_trees(args, inputs, out_dir)
    if isinstance(known, int):
        return known
    facts, codeowners, trees = known
    today = utc_today()
    window = args.window or DEFAULT_WINDOW
    hash_salt = None if args.full else run_salt(args.hash_paths, args.hash_salt)
    # stamped only when a platform read runs: --scans can select no platform scan
    platform_ran = args.platform and bool(platform_scans(scans))
    planned: JsonDict = {
        "fleet_stamp_version": FLEET_STAMP_VERSION,
        "levels_version": maturity_model["version"] if maturity_model is not None else None, "window": window,
        "rules_sha256": rules_sha256(maturity_model),  # a same-day rerun under edited rules is kept apart (#952)
        "counts_only": not args.full, "scans": sorted(scans) if scans is not None else None,
        "paths_hashed": hash_salt is not None,
        "fleet_config_sha256": config_sha256(fleet_config) if fleet_config is not None else None,
        "platform": platform_ran,
        **({"packs": inputs.packs.identities()} if inputs.packs.packs else {}),  # #1276
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
        window=args.window, timeout=args.timeout,
        scans=args.scans if args.scans is not None or scans is None else ",".join(scans),
        log_dir=out_dir / LOGS_DIR, active_since=window_start(window, today),
        ghsync_root=include_root(args), units=args.monorepo_units,
        managed_settings=args.managed_settings,
    )
    unwritable = _writable_out(args, out_dir)
    if unwritable is not None:
        return unwritable
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
    rows = _scan_all(targets, options, args.jobs, deleter, fleet_config_text, platform, inputs.packs)
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
        managed_settings=options.managed_settings, model=maturity_model, packs=inputs.packs.identities(),
    )
    write_stamp(out_dir, stamp)
    demoted = _condition_effect(out_dir, maturity_model, stamp, public, org_dir, entry)
    if maturity_model is not None and demoted:  # every result below reads the same demotions (#1086)
        maturity_model = with_demotions(maturity_model, demoted)
    write_fleet_levels(out_dir, public, maturity_model, toolkit_commit=stamp["toolkit_commit"],
                       plugin_version=stamp["plugin_version"], packs=stamp.get("packs", []))
    data = _write_fleet_data(out_dir, stamp, rows, maturity_model, trees, args.full, hash_salt, units, codeowners,
                             {repo: f.web_url for repo, f in facts.items()})
    comparison = _keep_and_compare(out_dir, entry, FleetRun(stamp, public, groups, row_signals(data)), earlier)
    credit, achieved = _achievements(out_dir, data, stamp, maturity_model, org_dir, entry,
                           ids, step_targets,
                           privacy_for(args.full, hash_salt).key, args.first_achievements)
    history = None if args.no_diagrams else _trend_history(org_dir, entry, stamp, groups, public, maturity_model)
    _diagrams(out_dir, args.no_diagrams, maturity_model, public, window, history=history, comparison=comparison,
              credit=credit, coverage=coverage_line(coverage, len(rows)),
              opportunities=lambda: opportunities_section(data, achieved), data=data, earlier=earlier, achieved=achieved,
              groups=[g["group"] for g in groups])
    _print_summary(rows, path, groups_path, agent_clusters, freshness, freshness_path, deleter)
    return 0


if __name__ == "__main__":
    sys.exit(main())
