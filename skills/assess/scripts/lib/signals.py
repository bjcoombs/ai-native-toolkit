"""Headless, signals-only run of the registered scans (issue #545).

``assess_core`` is built for one repository with a person in the loop: it
writes an ``.assess/`` wiki into the target, renders treemaps and calls ``gh``.
A scan across many repositories wants only the deterministic signals. This
module runs the scan table in ``scan_registry`` with inputs it builds itself,
and returns one JSON-ready document: a run stamp plus one block per scan.

It never writes, never reaches the network and never prompts. A scan declared
``network`` (outside ``--platform``, below) runs its ``offline_fn`` when it has one and is otherwise reported
``{"available": false, "reason": ...}``; so is every block in
``UNREGISTERED_SCANS``. A ``PLATFORM_ONLY_SCANS`` block (``delivery``) is left
out instead. A scan that raises degrades through the registry's
``safe`` wrapper and the run completes.

``--platform`` (``build_document(platform=True)``, #714) is the one opt-in
exception: the scans in ``PLATFORM_SCANS`` (and the network specs they read)
make their ``gh`` / ``glab`` reads. ``fleet --platform`` runs it in a separate
pass after the offline scan.

The scan list is every published registry key outside the core's own adapter
modules (``CORE_MODULES``), plus ``UNREGISTERED_SCANS``, so a scan added to the
table in its own module runs here without an edit. ``lib.headless_layers``
supplies the blocks the core builds from setup state a headless run lacks
(``structure``, ``stats_summary``, ``promissory_markers``, ``structure_drift``;
#667) and ``lib.headless_static`` the static ones (``observability``,
``doc_graph``, ``doc_staleness``, ``archetype``; #699; ``owner_exclusions``, #944), in place of the core entries that publish them (``SIGNAL_SCANS``). The core's entries (setup
state, the run header, the serialised blocks) run only when a reported scan
reads one of them; the publish step that writes the wiki is never called.

**Scope and units** (#866). ``build_document(scope=...)`` (``--scope``) gives the scans
that read ``scope`` one folder of the checkout, as ``assess_core --scope`` does, and the
stamp records it (``scope``, repository-relative; None for the whole checkout). The
monorepo unit scans ``UNIT_SCANS`` (``units``, ``unit_readings``) are the core's entries the
runner still reports: ``signal_scans`` appends them after the headless entries, whose
blocks the unit pass reads, and they run only when named or with ``units=True``
(``--units``), since the unit pass costs one scoped run per unit.

The headless, signals-only run behind `assess_signals.py` (issue #545). `collect` runs
the selected registry scans, plus the registry keys they read, through `run_scans` with
inputs `build_inputs` assembles itself (`repo_root`, `instruction_files` from
`grade_instruction_files`, `scope` (`--scope`, or None), `excludes` from `load_excludes`,
`history_window` set to `--window` for history-reading scans, `fleet_config` merged from
`--fleet-config` and the target's `.assess/config.toml` (`fleet_config.repo_fleet_config`), and the
`build_run_context` arguments `requested_scope` (the same scope), `run_date` and
`non_interactive=True`), so a raising scan degrades through `safe` and the run
completes. A name the runner provides is never recomputed from the table. A
`network` scan runs its `offline_fn` or is reported
`{"available": false, "reason": NETWORK_REASON}` (code `network`; the sentence names
both providers; defined in `gh_cli.py`, re-exported here). Offline, `merged_sample`
runs `offline_merged_sample` and `review_reality` passes that value through, so a
repository with no remote reads `no_remote` as `/assess` and `--platform` do (#908), except a `PLATFORM_ONLY_SCANS` block (`delivery`, #779), which is left
out of the document offline: absent, never a placeholder. `platform_only_error` makes
`assess_signals.py` and `fleet.py` exit 2 on a `--scans` naming one without `--platform`, and
`offline_scans` strips those names from the `--scans` of a fleet run's offline child
(`fleet.signals_command`); `fleet_groups.platform_only` reads such an absent block as a
`--platform` gap only on a row that has scans. `collect(platform=True)` (`assess_signals.py --platform`, #714) is the
one exception: a network scan in `PLATFORM_SCANS` (an explicit list: `review_reality`,
`delivery` and `review_evidence`, an offline block the pass rebuilds because it reads
`review_reality`, #696), or a network spec one of them reads, runs its `fn`. Extension point:
append a new platform block's name to `PLATFORM_SCANS`; blocks that describe merged
changes read the private `network=True` spec `merged_sample` (`merged_sample.py`, #715),
which runs once under `--platform` as their dependency. Only published, single-key scans
outside `CORE_MODULES` (`core_scans.py`, `run_setup.py`: the core's own entries since
#557) are reported; the core's entries run only when a reported scan reads one, and
the publish step never runs. The runner's table is `SIGNAL_SCANS` (`signal_scans`): the
registry with `HEADLESS_SCANS` (`headless_layers.py`, #667, and `headless_static.py`,
#699) in place of every core entry that publishes one of their names, and of every entry
that reads a displaced one. Names in `UNREGISTERED_SCANS` (run-context blocks
`assess_core` assembles, several of which report `available: false` without the
`reason` the schema requires) are reported unavailable with `UNREGISTERED_REASON`; a
name a reported scan also holds is dropped at runtime. `select_scans` raises
`UnknownScanError` naming every name in neither set, and adds each requested name's
`COMPANION_SCANS` (`with_companions`; #696: `review_reality` brings `review_automation`
and `review_evidence`, the Layer 7 level 1 signal); `fleet_platform.platform_scans`
expands a `--scans` selection the same way. `build_stamp` records the toolkit
commit, plugin version, UTC scan date, `--window` and the target's HEAD and dirty flag
(each nullable outside git). The window is validated, stamped and passed to the scans
that read `history_window` (`ai_usage`). Every git call runs inside `read_only_git` (`GIT_OPTIONAL_LOCKS=0`,
forced and restored), so `git status` cannot rewrite the target's index. The stamp is read once,
before the scans; the caller keeps the checkout fixed for the run. Nothing here writes or reaches
the network.
`FULL_RUN_ONLY_REASONS` (#697) is the set of reason codes (`lib.counts_only.content_free.reason_code` of `UNREGISTERED_REASON` and `NETWORK_REASON`: `not_registered`, `network`) that mark a block only a full `/assess` run produces; derived, never listed by hand.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.assess_config import load_excludes
from lib.counts_only.content_free import reason_code
from lib.fleet_config import FleetConfig, repo_fleet_config
from lib.hosting.gh_cli import NETWORK_REASON as NETWORK_REASON  # re-exported: defined beside the platform reads
from lib.git_churn import git_commit_info
from lib.history_window import split_window
from lib.headless_layers import HEADLESS_SCANS as LAYER_SCANS
from lib.headless_static import HEADLESS_SCANS as STATIC_SCANS
from lib.instruction_files import grade_instruction_files
from lib.plugin_version import read_version
from lib.run_context_types import JsonDict, SignalsDocument, SignalsStamp
from lib.scan_registry import SCANS, STAGES, ScanSpec, run_scans

# Bumped when the document's envelope (stamp fields, top-level keys) changes,
# or when a scanner change alters when an existing signal reads true or null
# without a new signal name (the fleet comparison convention,
# lib/fleet/fleet_scanner_diff.py). Per-scan block shapes are not versioned here.
# 3 (3.0.0 release): one change since 2.
# - layer_grades.instruction_file_present reads true, and instructions_grade a letter,
#   for a repository whose only instruction files are nested (instruction_presence
#   nested_only, issue #821). instruction_file_present, documented as never null before,
#   now reads null (with instruction_presence) when there is no root file and the
#   nested_instructions scan did not run.
SIGNALS_SCHEMA_VERSION = 3

# Run-context blocks ``assess_core`` assembles through its own table entries in
# ``CORE_MODULES`` (#557, #558). Names only: the headless runner reports each as
# unavailable. Several report ``available: false`` without the ``reason`` the
# published schema requires (``dead_code`` with no tool, ``coverage_report``
# with no report), so they stay unreported until each shape is checked. A name
# a reported scan also holds is dropped at runtime.
UNREGISTERED_SCANS: tuple[str, ...] = (
    "diff",
    "dead_code",
    "ancestor_instruction_files",
    "behaviour",
    "documentation",
    "understanding",
    "runtime",
    "derived_findings",
    "coverage_report",
    "test_pressure",
    "test_focus",
    "accretion_ratchet",
    "gap_actions",
)

UNREGISTERED_REASON = (
    "not_registered: assembled by assess_core.build_run_context; "
    "the headless runner covers registered scans only"
)

# The headless runner's own entries (``lib.headless_layers``, #667;
# ``lib.headless_static``, #699).
HEADLESS_SCANS: tuple[ScanSpec, ...] = (*LAYER_SCANS, *STATIC_SCANS)

# The modules that hold the core's own table entries (#557).
CORE_MODULES = frozenset({"lib.core_scans", "lib.run_setup"})

# The monorepo unit scans the runner reports although their entries are the core's (#866):
# the units (``lib.unit_discovery``, #862) and each unit's own readings
# (``lib.unit_readings``, #863), which run the scoped scans again per unit. Opt-in
# (``--units``, ``select_scans(units=True)``) or named in ``--scans``: the unit pass costs one
# scoped run per unit. Each unit's Step is not reported, as the repository's is not: a fleet
# run computes both from the row with its own model (``lib.fleet.fleet_data_units``).
UNIT_SCANS: tuple[str, ...] = ("units", "unit_readings")


def _published(spec: ScanSpec) -> set[str]:
    return set(spec.spread) if spec.spread else {spec.key}


def signal_scans(
    specs: Sequence[ScanSpec] = SCANS, headless: Sequence[ScanSpec] = HEADLESS_SCANS,
) -> tuple[ScanSpec, ...]:
    """The headless runner's table: ``specs`` with ``headless`` entries in place of the core's.

    A ``headless`` entry (``lib/headless_layers.py``, #667) reports a block the
    core assembles from setup state a headless run never builds. Each core
    entry that publishes one of those names is dropped, and so is every entry
    that reads a dropped key, transitively; they are core entries the runner
    never reports. The headless entries go last, at their declared stage.
    The ``UNIT_SCANS`` entries the rule drops (the unit pass reads blocks the headless
    entries publish under the same names) go after them, reading the headless blocks.
    """
    names = {spec.key for spec in headless}
    dropped = {spec.key for spec in specs if _published(spec) & names}
    kept: list[ScanSpec] = []
    for spec in specs:
        if spec.key in dropped or dropped & set(spec.reads):
            dropped.add(spec.key)
            continue
        kept.append(spec)
    units = [spec for spec in specs if spec.key in UNIT_SCANS and spec.key in dropped]
    return (*kept, *headless, *units)


SIGNAL_SCANS = signal_scans()

# The reason codes of the blocks only a full /assess run measures (#697): the code
# ``lib.counts_only.content_free.reason_code`` keeps of each reason the runner writes for a block it
# cannot produce, so a counts-only row and a --full row read alike. Derived, never
# listed, so a reason added here (or a scan moving out of UNREGISTERED_SCANS) carries.
# A PLATFORM_SCANS block is the exception: ``fleet --platform`` measures it, so its
# ``network`` gap is a scan option away, not a full run (``lib.fleet.fleet_groups``).
FULL_RUN_ONLY_REASONS: frozenset[str] = frozenset(
    reason_code(reason) for reason in (UNREGISTERED_REASON, NETWORK_REASON))

# The scans ``--platform`` runs with their network reads (#714), an explicit list:
# every other network scan still reports ``NETWORK_REASON``. ``fleet --platform``
# runs exactly these in its separate platform pass. Extension point (#715, #716):
# append a new block's name here. Blocks that should share one sample of merged
# changes read it as a registry input: register the sample as a private
# ``network=True`` spec, list it in the blocks' ``reads``, and it runs (once) under
# ``--platform`` because it is a dependency of a listed scan.
# ``merged_sample`` (lib/hosting/merged_sample.py, #715) is that shared sample: ``review_reality``
# and ``delivery`` read it, and #716 extends the same two blocks on GitLab.
# ``review_evidence`` (lib/review_automation.py, #696) is offline itself but reads
# ``review_reality``, so the platform pass rebuilds it from the measured block.
# ``review_automation`` and ``ci_checks`` are offline too, but read the private network
# spec ``gitlab_merged_ci`` (lib/hosting/gitlab_ci_lint.py, #796): GitLab's merged CI
# configuration where an include did not resolve offline.
PLATFORM_SCANS: tuple[str, ...] = ("review_reality", "delivery", "review_evidence",
                                   "review_automation", "ci_checks", "org_security_policy",
                                   "github_secret_scanning")

# The PLATFORM_SCANS blocks that exist only on the platform (#779): without
# ``--platform`` the document leaves them out (absent, never a ``network``
# placeholder), so a row read without the platform carries no figure, not even
# an unavailable one. ``review_reality`` runs its offline read (``no_remote``, or
# ``network`` for a remote only the platform reads) and ``review_evidence`` is
# offline itself, so neither is listed.
PLATFORM_ONLY_SCANS: tuple[str, ...] = ("delivery",)


def platform_only_error(requested: Iterable[str] | None, platform: bool) -> str | None:
    """The error for a ``--scans`` selection naming a ``PLATFORM_ONLY_SCANS`` block
    without ``--platform`` (it would produce no block and no reason); None when fine."""
    named = [name for name in (requested or ()) if name in PLATFORM_ONLY_SCANS]
    if platform or not named:
        return None
    return f"--scans {', '.join(named)} needs --platform: the block exists only on the platform"


def scope_units_error(requested: Iterable[str] | None, scope: Path | None) -> str | None:
    """The error for a ``--scans`` selection naming a ``UNIT_SCANS`` block on a scoped run:
    unit discovery reads the whole checkout, so the blocks would not describe the stamped
    scope (#866). None when fine."""
    named = [name for name in (requested or ()) if name in UNIT_SCANS]
    if scope is None or not named:
        return None
    return f"--scans {', '.join(named)} reads the whole checkout's units; it does not combine with --scope"


def offline_scans(scans: str | None) -> str | None:
    """A comma-separated ``--scans`` value without its ``PLATFORM_ONLY_SCANS`` names, for the
    offline child of a fleet run (the platform pass reads them)."""
    if scans is None:
        return None
    return ",".join(s.strip() for s in scans.split(",")
                    if s.strip() and s.strip() not in PLATFORM_ONLY_SCANS)

DEFAULT_WINDOW = "6mo"

# skills/assess/scripts/lib/signals.py -> the toolkit checkout root.
TOOLKIT_ROOT = Path(__file__).resolve().parents[4]


class UnknownScanError(ValueError):
    """``--scans`` named a scan that is neither reported nor a core block."""

    def __init__(self, names: Sequence[str]) -> None:
        self.names = list(names)
        super().__init__(f"unknown scan(s): {', '.join(self.names)}")


def _reported(spec: ScanSpec) -> bool:
    """A registered scan the runner reports: published, single-key, not the core's own
    (the ``UNIT_SCANS`` excepted)."""
    if spec.key in UNIT_SCANS:
        return True
    return not spec.private and not spec.spread and spec.fn.__module__ not in CORE_MODULES


def known_scans(specs: Iterable[ScanSpec] = SIGNAL_SCANS) -> list[str]:
    """Every scan name the runner accepts, registry first, in table order."""
    registered = [spec.key for spec in specs if _reported(spec)]
    return registered + [name for name in UNREGISTERED_SCANS if name not in registered]


# Scans a --scans selection brings along with a name (#696): Layer 7 level 1 reads
# review_evidence, built from review_automation and review_reality, so selecting
# review_reality alone still gives the row its level-1 evidence.
COMPANION_SCANS: dict[str, tuple[str, ...]] = {
    "review_reality": ("review_automation", "review_evidence"),
}


def with_companions(names: Iterable[str]) -> list[str]:
    """``names`` plus each one's ``COMPANION_SCANS``, first appearance kept."""
    out: list[str] = []
    for name in names:
        for item in (name, *COMPANION_SCANS.get(name, ())):
            if item not in out:
                out.append(item)
    return out


def select_scans(requested: Sequence[str] | None, specs: Sequence[ScanSpec] = SIGNAL_SCANS,
                 units: bool = False) -> list[str]:
    """The requested names in table order, or every known name when None.

    The ``UNIT_SCANS`` run only when named or with ``units`` (``--units``), which adds them
    to any selection. Raises ``UnknownScanError`` naming every unknown entry.
    """
    known = known_scans(specs)
    extra = set(UNIT_SCANS) if units else set()
    if requested is None:
        return [name for name in known if name not in UNIT_SCANS or name in extra]
    unknown = [name for name in requested if name not in known]
    if unknown:
        raise UnknownScanError(unknown)
    wanted = set(with_companions(requested)) | extra
    return [name for name in known if name in wanted]


def parse_window(window: str) -> str:
    """Validate a ``--window`` like ``6mo``, ``90d``, ``12w`` or ``1y``."""
    try:
        split_window(window)
    except ValueError:
        raise ValueError(f"invalid window {window!r}: expected <n>d, <n>w, <n>mo or <n>y") from None
    return window


def build_inputs(repo_root: Path, window: str = DEFAULT_WINDOW,
                 fleet_config: FleetConfig | None = None,
                 ghsync_root: Path | None = None, scope: Path | None = None,
                 managed_settings: bool = False) -> dict[str, Any]:
    """The registry's ``PROVIDED_INPUTS``, built the way ``assess_core`` builds them.

    ``scope`` is ``--scope`` resolved to an absolute folder under ``repo_root``
    (``run_scope.resolve_scope``), or None for the whole checkout (#866). The scans that
    read ``scope`` measure that folder; every other scan reads the whole repository, as
    ``assess_core --scope`` does.
    ``history_window`` carries ``--window`` for history-reading scans
    (``ai_usage``, #547). ``fleet_config`` is the ``--fleet-config`` file merged
    with the target's ``.assess/config.toml`` sections (``lib/fleet_config.py``, #706).
    ``ghsync_root`` is ``--ghsync-root``, the root GitLab includes resolve against (#766).
    ``maturity_model`` is None: no headless scan evaluates a maturity model (#1150).
    ``managed_settings`` is ``--managed-settings`` (#1153): ``agent_ops`` reads this machine's
    Claude Code managed settings. Never read from the target's ``.assess/config.toml``.
    Besides the ``build_run_context`` arguments, it supplies the setup values
    ``instruction_files``, ``scope``, ``excludes``, ``history_window``,
    ``fleet_config`` and ``ghsync_root`` itself; the table never recomputes a name the runner provides.
    """
    instruction_files = grade_instruction_files(repo_root)[0]
    return {
        "repo_root": repo_root,
        "requested_scope": scope,
        "run_date": datetime.now(timezone.utc).date().isoformat(),
        "non_interactive": True,
        "maturity_model": None,  # the headless runner reports no level (/fleet --levels scores the rows)
        "managed_settings": managed_settings,
        "instruction_files": instruction_files,
        "scope": scope,
        "excludes": load_excludes(repo_root),
        "history_window": window,
        "fleet_config": repo_fleet_config(repo_root, fleet_config),
        "ghsync_root": ghsync_root,
    }


def _with_dependencies(
    selected: Iterable[str], specs: Sequence[ScanSpec], provided: Iterable[str] = (),
) -> set[str]:
    """``selected`` plus every registry key a selected scan reads, transitively.

    Only reported scans are selectable; a name in ``provided`` is never recomputed.
    """
    by_key = {spec.key: spec for spec in specs}
    skip = set(provided)
    needed: set[str] = set()
    pending = [name for name in selected if name in by_key and _reported(by_key[name])]
    while pending:
        key = pending.pop()
        if key in needed or key in skip:
            continue
        needed.add(key)
        pending.extend(name for name in by_key[key].reads if name in by_key)
    return needed


def _unavailable(reason: str) -> JsonDict:
    return {"available": False, "reason": reason}


def collect(
    repo_root: Path, selected: Sequence[str], specs: Sequence[ScanSpec] = SIGNAL_SCANS,
    window: str = DEFAULT_WINDOW, fleet_config: FleetConfig | None = None, platform: bool = False,
    ghsync_root: Path | None = None, scope: Path | None = None, managed_settings: bool = False,
) -> dict[str, JsonDict]:
    """Run the selected scans (and what they read); one block per name.

    Offline unless ``platform``: then a network scan in ``PLATFORM_SCANS``, or one
    a listed scan reads, runs its network ``fn``. Offline, a ``PLATFORM_ONLY_SCANS``
    name gets no block at all.
    """
    inputs = build_inputs(repo_root, window, fleet_config, ghsync_root, scope, managed_settings)
    needed = _with_dependencies(selected, specs, inputs)
    online = _with_dependencies(PLATFORM_SCANS, specs, inputs) if platform else set()
    ctx: dict[str, Any] = {}
    runnable: list[ScanSpec] = []
    for spec in specs:
        if spec.key not in needed:
            continue
        if not spec.network or spec.key in online:
            runnable.append(spec)
        elif spec.offline_fn is not None:
            runnable.append(replace(spec, fn=spec.offline_fn))
        else:
            ctx[spec.key] = _unavailable(NETWORK_REASON)
    if runnable:
        for stage in STAGES:
            run_scans(ctx, inputs, stage, runnable)
    return {name: ctx.get(name, _unavailable(UNREGISTERED_REASON)) for name in selected
            if platform or name not in PLATFORM_ONLY_SCANS}


def read_plugin_version(toolkit_root: Path = TOOLKIT_ROOT) -> str:
    """``.claude-plugin/plugin.json`` ``version``, else the standalone skill's version file
    (``lib.plugin_version``, #1166), or ``unknown``."""
    return read_version(toolkit_root / ".claude-plugin" / "plugin.json")


def _git(root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=True, timeout=30,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def is_repo_root(path: Path) -> bool:
    """True when ``path`` is itself a git work-tree root.

    ``git -C`` walks up parent directories, so without this a non-git plugin
    install or target nested in another checkout would be stamped with the
    outer repository's HEAD.
    """
    top = _git(path, "rev-parse", "--show-toplevel")
    return top is not None and Path(top).resolve() == path.resolve()


def toolkit_commit(toolkit_root: Path = TOOLKIT_ROOT) -> str | None:
    """The toolkit checkout's HEAD, or None for an install that is not a git checkout."""
    if not is_repo_root(toolkit_root):
        return None
    return _git(toolkit_root, "rev-parse", "HEAD")


def build_stamp(repo_root: Path, window: str, toolkit_root: Path = TOOLKIT_ROOT,
                scope: Path | None = None, managed_settings: bool = False) -> SignalsStamp:
    """Toolkit commit, plugin version, scan date, window, the target's HEAD, the scope
    (repository-relative, posix; None for the whole checkout) and whether this machine's
    managed settings were read (``--managed-settings``, #1153)."""
    head = git_commit_info(repo_root) if is_repo_root(repo_root) else {}
    available = bool(head.get("available"))
    return {
        "toolkit_commit": toolkit_commit(toolkit_root),
        "plugin_version": read_plugin_version(toolkit_root),
        "scan_date": datetime.now(timezone.utc).date().isoformat(),
        "window": window,
        "target_head": head.get("head_sha") if available else None,
        "target_dirty": bool(head.get("dirty")) if available else None,
        "scope": scope.relative_to(repo_root).as_posix() if scope is not None else None,
        "managed_settings": managed_settings,
    }


@contextmanager
def read_only_git() -> Iterator[None]:
    """Force ``GIT_OPTIONAL_LOCKS=0`` for the duration, restoring the prior value.

    ``git status`` (run by the stamp and by scans) may otherwise refresh and
    rewrite the target's ``.git/index``. Forced rather than defaulted, so an
    inherited ``GIT_OPTIONAL_LOCKS=1`` cannot reopen that write; scoped, so
    ``assess_core`` and the caller's environment are unchanged afterwards.
    """
    prior = os.environ.get("GIT_OPTIONAL_LOCKS")
    os.environ["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop("GIT_OPTIONAL_LOCKS", None)
        else:
            os.environ["GIT_OPTIONAL_LOCKS"] = prior


def build_document(
    repo_root: Path, requested: Sequence[str] | None = None, window: str = DEFAULT_WINDOW,
    toolkit_root: Path = TOOLKIT_ROOT, specs: Sequence[ScanSpec] = SIGNAL_SCANS,
    fleet_config: FleetConfig | None = None, platform: bool = False,
    ghsync_root: Path | None = None, scope: Path | None = None, units: bool = False,
    managed_settings: bool = False,
) -> SignalsDocument:
    """The whole signals document. Raises ``UnknownScanError`` or ``ValueError`` on bad arguments.

    Every git call runs with optional locks off (``read_only_git``). The stamp
    is read once, before the scans: the caller keeps the target checkout fixed
    for the run (a fleet caller scans checkouts it controls), and the stamp
    describes that checkout. A checkout that moves mid-run is not detected.
    """
    selected = select_scans(requested, specs, units)
    window = parse_window(window)
    with read_only_git():
        return {
            "signals_schema_version": SIGNALS_SCHEMA_VERSION,
            "stamp": build_stamp(repo_root, window, toolkit_root, scope, managed_settings),
            "scans": collect(repo_root, selected, specs, window, fleet_config, platform,
                             ghsync_root, scope, managed_settings),
        }
