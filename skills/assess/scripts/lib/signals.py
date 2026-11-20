"""Headless, signals-only run of the registered scans (issue #545).

``assess_core`` is built for one repository with a person in the loop: it
writes an ``.assess/`` wiki into the target, renders treemaps and calls ``gh``.
A scan across many repositories wants only the deterministic signals. This
module runs the scan table in ``scan_registry`` with inputs it builds itself,
and returns one JSON-ready document: a run stamp plus one block per scan.

It never writes, never reaches the network and never prompts. A scan declared
``network`` runs its ``offline_fn`` when it has one and is otherwise reported
``{"available": false, "reason": ...}``; so is every block in
``UNREGISTERED_SCANS``. A scan that raises degrades through the registry's
``safe`` wrapper and the run completes.

The scan list is every published registry key outside the core's own adapter
modules (``CORE_MODULES``), plus ``UNREGISTERED_SCANS``, so a scan added to the
table in its own module runs here without an edit. ``lib.headless_layers``
supplies the blocks the core builds from setup state a headless run lacks
(``structure``, ``stats_summary``, ``promissory_markers``, ``structure_drift``;
#667), in place of the core entries that publish them (``SIGNAL_SCANS``). The core's entries (setup
state, the run header, the serialised blocks) run only when a reported scan
reads one of them; the publish step that writes the wiki is never called.
"""
from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.assess_config import load_excludes
from lib.fleet_config import FleetConfig, repo_fleet_config
from lib.git_churn import git_commit_info
from lib.history_window import split_window
from lib.headless_layers import HEADLESS_SCANS
from lib.instruction_files import grade_instruction_files
from lib.run_context_types import JsonDict, SignalsDocument, SignalsStamp
from lib.scan_registry import SCANS, STAGES, ScanSpec, run_scans

# Bumped when the document's envelope (stamp fields, top-level keys) changes.
# Per-scan blocks carry their own shapes and are not versioned here.
SIGNALS_SCHEMA_VERSION = 1

# Run-context blocks ``assess_core`` assembles through its own table entries in
# ``CORE_MODULES`` (#557, #558). Names only: the headless runner reports each as
# unavailable. Several report ``available: false`` without the ``reason`` the
# published schema requires (``dead_code`` with no tool, ``coverage_report``
# with no report), so they stay unreported until each shape is checked. A name
# a reported scan also holds is dropped at runtime.
UNREGISTERED_SCANS: tuple[str, ...] = (
    "diff",
    "doc_graph",
    "doc_staleness",
    "dead_code",
    "observability",
    "archetype",
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

# The modules that hold the core's own table entries (#557).
CORE_MODULES = frozenset({"lib.core_scans", "lib.run_setup"})


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
    """
    names = {spec.key for spec in headless}
    dropped = {spec.key for spec in specs if _published(spec) & names}
    kept: list[ScanSpec] = []
    for spec in specs:
        if spec.key in dropped or dropped & set(spec.reads):
            dropped.add(spec.key)
            continue
        kept.append(spec)
    return (*kept, *headless)


SIGNAL_SCANS = signal_scans()
NETWORK_REASON = "network: reads GitHub via gh; the headless runner makes no network calls"

DEFAULT_WINDOW = "6mo"

# skills/assess/scripts/lib/signals.py -> the toolkit checkout root.
TOOLKIT_ROOT = Path(__file__).resolve().parents[4]


class UnknownScanError(ValueError):
    """``--scans`` named a scan that is neither reported nor a core block."""

    def __init__(self, names: Sequence[str]) -> None:
        self.names = list(names)
        super().__init__(f"unknown scan(s): {', '.join(self.names)}")


def _reported(spec: ScanSpec) -> bool:
    """A registered scan the runner reports: published, single-key, not the core's own."""
    return not spec.private and not spec.spread and spec.fn.__module__ not in CORE_MODULES


def known_scans(specs: Iterable[ScanSpec] = SIGNAL_SCANS) -> list[str]:
    """Every scan name the runner accepts, registry first, in table order."""
    registered = [spec.key for spec in specs if _reported(spec)]
    return registered + [name for name in UNREGISTERED_SCANS if name not in registered]


def select_scans(requested: Sequence[str] | None, specs: Sequence[ScanSpec] = SIGNAL_SCANS) -> list[str]:
    """The requested names in table order, or every known name when None.

    Raises ``UnknownScanError`` naming every unknown entry.
    """
    known = known_scans(specs)
    if requested is None:
        return known
    unknown = [name for name in requested if name not in known]
    if unknown:
        raise UnknownScanError(unknown)
    wanted = set(requested)
    return [name for name in known if name in wanted]


def parse_window(window: str) -> str:
    """Validate a ``--window`` like ``6mo``, ``90d``, ``12w`` or ``1y``."""
    try:
        split_window(window)
    except ValueError:
        raise ValueError(f"invalid window {window!r}: expected <n>d, <n>w, <n>mo or <n>y") from None
    return window


def build_inputs(repo_root: Path, window: str = DEFAULT_WINDOW,
                 fleet_config: FleetConfig | None = None) -> dict[str, Any]:
    """The registry's ``PROVIDED_INPUTS``, built the way ``assess_core`` builds them.

    ``scope`` is None: a headless run measures the whole checkout.
    ``history_window`` carries ``--window`` for history-reading scans
    (``ai_usage``, #547). ``fleet_config`` is the ``--fleet-config`` file merged
    with the target's ``.assess/config.toml`` sections (``lib/fleet_config.py``, #706).
    Besides the ``build_run_context`` arguments, it supplies the setup values
    ``instruction_files``, ``scope``, ``excludes``, ``history_window`` and
    ``fleet_config`` itself; the table never recomputes a name the runner provides.
    """
    instruction_files = grade_instruction_files(repo_root)[0]
    return {
        "repo_root": repo_root,
        "requested_scope": None,
        "run_date": datetime.now(timezone.utc).date().isoformat(),
        "non_interactive": True,
        "instruction_files": instruction_files,
        "scope": None,
        "excludes": load_excludes(repo_root),
        "history_window": window,
        "fleet_config": repo_fleet_config(repo_root, fleet_config),
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
    window: str = DEFAULT_WINDOW, fleet_config: FleetConfig | None = None,
) -> dict[str, JsonDict]:
    """Run the selected scans (and what they read) offline; one block per name."""
    inputs = build_inputs(repo_root, window, fleet_config)
    needed = _with_dependencies(selected, specs, inputs)
    ctx: dict[str, Any] = {}
    runnable: list[ScanSpec] = []
    for spec in specs:
        if spec.key not in needed:
            continue
        if not spec.network:
            runnable.append(spec)
        elif spec.offline_fn is not None:
            runnable.append(replace(spec, fn=spec.offline_fn))
        else:
            ctx[spec.key] = _unavailable(NETWORK_REASON)
    if runnable:
        for stage in STAGES:
            run_scans(ctx, inputs, stage, runnable)
    return {name: ctx.get(name, _unavailable(UNREGISTERED_REASON)) for name in selected}


def read_plugin_version(toolkit_root: Path = TOOLKIT_ROOT) -> str:
    """``.claude-plugin/plugin.json`` ``version``, or ``unknown``."""
    try:
        data = json.loads((toolkit_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        return str(data.get("version", "unknown"))
    except (OSError, ValueError, AttributeError):
        return "unknown"


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


def build_stamp(repo_root: Path, window: str, toolkit_root: Path = TOOLKIT_ROOT) -> SignalsStamp:
    """Toolkit commit, plugin version, scan date, window and the target's HEAD."""
    head = git_commit_info(repo_root) if is_repo_root(repo_root) else {}
    available = bool(head.get("available"))
    return {
        "toolkit_commit": toolkit_commit(toolkit_root),
        "plugin_version": read_plugin_version(toolkit_root),
        "scan_date": datetime.now(timezone.utc).date().isoformat(),
        "window": window,
        "target_head": head.get("head_sha") if available else None,
        "target_dirty": bool(head.get("dirty")) if available else None,
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
    fleet_config: FleetConfig | None = None,
) -> SignalsDocument:
    """The whole signals document. Raises ``UnknownScanError`` or ``ValueError`` on bad arguments.

    Every git call runs with optional locks off (``read_only_git``). The stamp
    is read once, before the scans: the caller keeps the target checkout fixed
    for the run (a fleet caller scans checkouts it controls), and the stamp
    describes that checkout. A checkout that moves mid-run is not detected.
    """
    selected = select_scans(requested, specs)
    window = parse_window(window)
    with read_only_git():
        return {
            "signals_schema_version": SIGNALS_SCHEMA_VERSION,
            "stamp": build_stamp(repo_root, window, toolkit_root),
            "scans": collect(repo_root, selected, specs, window, fleet_config),
        }
