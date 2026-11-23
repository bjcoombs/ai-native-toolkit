"""Headless observability, doc graph, doc staleness, archetype, owner exclusions and contributor files blocks (issue #699).

``assess_core`` publishes these five blocks through its own table entries in
``lib.core_scans`` (``signals.CORE_MODULES``), so the headless runner
(``lib/signals.py``) used to report each as ``not_registered``. Each scan is
static: it reads committed files and, for doc staleness, git history, and none
needs the full run's setup state. ``HEADLESS_SCANS`` gives the runner one entry
per block here, outside the core, as ``lib.headless_layers`` does for the layer
2-4 blocks (#667); the full run's blocks are unchanged.

- ``observability`` calls ``liveness_scan.scan_observability`` directly. The
  core reaches it through ``scan_liveness``, which also runs the dead-code
  tools; those stay full-run only (``dead_code``, ``runtime``).
- ``doc_graph`` is the core's doc graph. Without networkx it reports
  ``no_networkx``.
- ``doc_staleness`` reads the doc graph's doc->code edges, as the core does,
  and needs git: a target that is not the top of a git work tree reports
  ``not_git_root``.
- ``archetype`` is the core's software-or-knowledge-base verdict.
- ``owner_exclusions`` is the core's ``[checks] exclude`` block (#910), so a fleet
  row's level result applies the owner's exclusions as ``/assess`` does (#944). It
  never degrades: a bad entry is listed under ``skipped``.
- ``contributor_files`` is the core's scan (#1045), run here because it reads the
  headless ``archetype`` (a knowledge base reads not measured).

Each block degrades to the bare ``{"available": false, "reason": ...}`` marker
with a ``code: sentence`` reason (``scan_failed`` when the scan raises), so
counts-only keeps a named code and a maturity-model row reads unknown.

The headless runner's entries for the six static blocks the core publishes from
`core_scans.py` (#699): `observability` (`liveness_scan.scan_observability` called
directly, so the dead-code tools `scan_liveness` also runs stay full-run only),
`doc_graph` (needs networkx: `no_networkx`), `doc_staleness` (over the doc graph's
doc->code edges; needs a git work tree: `not_git_root`), `archetype` and `owner_exclusions`
(#944: `owner_exclusions.owner_exclusions_block`, so a fleet row's level applies the
owner's exclusions) and `contributor_files` (#1045, after the headless `archetype` it reads). A scan that
raises is the bare marker `scan_failed: <error>`, so counts-only keeps a code and a
maturity-model row reads `unknown`. `dead_code`, `understanding` and `runtime` stay in
`signals.UNREGISTERED_SCANS` (`not_registered`). Tests: `tests/test_headless_static.py`.
"""
from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lib.keyhole.archetype import analyze_archetype
from lib.assess_config import load_working_notes_config
from lib.context_blocks import doc_to_code_edges
from lib.docs.doc_graph import build_doc_graph
from lib.docs.doc_staleness import analyze_doc_staleness
from lib.keyhole.liveness_scan import scan_observability
from lib.owner_exclusions import owner_exclusions_block
from lib.run_context_types import JsonDict
from lib.contributor_files import scan_contributor_files
from lib.scan_registry import STAGE_POST_OFFERS, STAGE_READ_SIDE, STAGE_SETUP, ScanSpec

Excludes = tuple[set[str], list[str]]


def _marker(code: str, sentence: str) -> JsonDict:
    return {"available": False, "reason": f"{code}: {sentence}"}


def _guarded(name: str, build: Callable[[], Any]) -> JsonDict:
    """``build()``, or the ``scan_failed`` marker naming the error."""
    try:
        block: JsonDict = build()
    except Exception as e:  # noqa: BLE001 - one failed scan never sinks the run
        return _marker("scan_failed", f"the {name} scan raised {type(e).__name__}: {e}")
    return block


def observability(repo_root: Path, excludes: Excludes) -> JsonDict:
    """The observability rung from committed files (telemetry, runbooks, MCP servers, skills)."""
    dirs, patterns = excludes
    return _guarded("observability", lambda: dict(scan_observability(
        repo_root, extra_exclude_dirs=dirs, extra_exclude_patterns=patterns).as_dict()))


def doc_graph(repo_root: Path, excludes: Excludes) -> JsonDict:
    """The Layer 0 doc graph, or ``no_networkx``."""
    dirs, patterns = excludes

    def build() -> JsonDict:
        notes = load_working_notes_config(repo_root)
        block = dict(build_doc_graph(
            repo_root, extra_exclude_dirs=dirs, extra_exclude_patterns=patterns,
            working_notes_dirs=notes.dirs, working_notes_ignore=notes.ignore,
        ).as_dict())
        if block.get("available") is False:
            reason = str(block.get("reason") or "doc graph unavailable")
            code = "no_networkx" if "networkx" in reason else "unavailable"
            return _marker(code, reason)
        return block

    return _guarded("doc_graph", build)


def is_git_work_tree(repo_root: Path) -> bool:
    """True when ``repo_root`` is itself the top of a git work tree.

    ``git -C`` walks up parent directories, so a target nested in another
    checkout, or one with a broken ``.git`` file, is not a work tree here.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", check=True, timeout=30,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return False
    top = out.stdout.strip()
    return bool(top) and Path(top).resolve() == repo_root.resolve()


def doc_staleness(repo_root: Path, graph: Any, excludes: Excludes) -> JsonDict:
    """Doc staleness over the doc graph's doc->code edges, or ``not_git_root``."""
    if not is_git_work_tree(repo_root):
        return _marker("not_git_root", "doc staleness reads git history; the target is not a git work tree")
    dirs, patterns = excludes
    edges = doc_to_code_edges(graph) if isinstance(graph, dict) else []
    return _guarded("doc_staleness", lambda: dict(analyze_doc_staleness(
        repo_root, doc_to_code_edges=edges,
        extra_exclude_dirs=dirs, extra_exclude_patterns=patterns)))


def archetype(repo_root: Path) -> JsonDict:
    """Software repository or knowledge base."""
    return _guarded("archetype", lambda: analyze_archetype(repo_root))


def contributor_files(repo_root: Path, archetype: Any, org_security_policy: Any) -> JsonDict:
    """The contributing guide and security policy (#1045), after the headless archetype."""
    return _guarded("contributor_files",
                    lambda: dict(scan_contributor_files(repo_root, archetype, org_security_policy)))


def owner_exclusions(repo_root: Path) -> JsonDict:
    """The owner's ``[checks] exclude`` entries (#910), so a fleet row's level applies them (#944)."""
    return dict(owner_exclusions_block(repo_root))


# The headless runner's entries for the six blocks. Each replaces the core
# entry that publishes the same name (``signals.signal_scans``). The doc graph
# runs at the setup stage, as in the core, so doc staleness can read it.
HEADLESS_SCANS: tuple[ScanSpec, ...] = (
    ScanSpec("observability", observability, ("repo_root", "excludes"), STAGE_READ_SIDE),
    ScanSpec("doc_graph", doc_graph, ("repo_root", "excludes"), STAGE_SETUP),
    ScanSpec("doc_staleness", doc_staleness, ("repo_root", "doc_graph", "excludes"),
             STAGE_READ_SIDE),
    ScanSpec("archetype", archetype, ("repo_root",), STAGE_READ_SIDE),
    ScanSpec("owner_exclusions", owner_exclusions, ("repo_root",), STAGE_READ_SIDE),
    # Reads the headless archetype above, so it is a variant too (#1045).
    ScanSpec("contributor_files", contributor_files, ("repo_root", "archetype", "org_security_policy"),
             STAGE_POST_OFFERS),
)
