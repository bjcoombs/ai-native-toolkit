"""Headless blocks for the maturity model's layer 2, 3 and 4 levels (issue #667).

The default maturity model reads its layer 2-4 levels from four run-context
blocks that ``assess_core`` assembles through its own table entries in
``lib.run.core_scans`` and ``lib.run.run_setup`` (``signals.CORE_MODULES``):
``structure``, ``stats_summary``, ``promissory_markers`` and
``structure_drift``. Those entries hang off the full run's setup state (the
complexity-stats sidecar, the keyhole integration, the run header), which a
headless run never builds. ``HEADLESS_SCANS`` gives the headless runner
(``lib/run/signals.py``) one reportable entry per block instead, outside the core
modules, so the boundary from #557/#558 stays as it is:

- ``structure`` reads the core's private ``structure_scan`` (the import graph:
  networkx, plus each language builder's tool, grimp for Python). A repository
  with no import graph reports unavailable with a reason naming its dominant
  language, as the full run does: ``no_import_graph_<language>`` (a Go or
  Rust repository with no builder for its language reads unknown on
  layer 2 rather than 0), ``no_python_packages`` for a Python repository with
  no importable package, ``no_source_files`` for a repository with no source.
- ``promissory_markers`` reads the core's private ``promissory`` scan (needs
  ``rg``).
- ``structure_drift`` builds tier 0 (declared ownership patterns against the
  tracked files). Tier 1 needs the commit-log co-change pairs of the keyhole
  behaviour block, so it is reported unavailable. With no ownership map the
  block is the same ``no_ownership_map`` marker the full run writes (#908).
- ``stats_summary`` scores the tracked source files with lizard, in process.
  The full run's sidecar also scores scc-only languages, so its percentiles can
  differ; the headless block carries ``files_scored``, ``loc``, ``ccn`` and
  ``loc_by_class`` (the size split by path class, ``lib.class_sizes``, #820)
  only (no hotspot lists, which need churn and name files).

Every unavailable block is the bare ``{"available": false, "reason": ...}``
marker with a ``code: sentence`` reason, so the counts-only filter keeps a
named code (``no_lizard``, ``no_grimp``, ``no_networkx``, ``grimp_failed``, ``no_rg``,
``no_python_packages``, ``no_import_graph_<language>``, ``no_source_files``,
``no_ownership_map``) and a maturity-model row reads
unknown, never a level. The full run's blocks are unchanged.

The headless runner's entries for the four blocks the default maturity model's layer 2-4
rows read (#667), outside `CORE_MODULES`: `structure` (the core's private
`structure_scan`, needs networkx and a language builder's tool, grimp for Python), `promissory_markers` (the private
`promissory` scan, needs `rg`), `structure_drift` (tier 0 from the ownership map via
`context_blocks.structure_drift_tier_0`; tier 1 needs the keyhole co-change pairs and is
`not_probed`) and `stats_summary` (tracked source files scored by lizard in process:
`files_scored`, `loc` and file-aggregate `ccn`, percentiles by numpy's linear rule; no
scc languages, no hotspot lists). Every unavailable block is the bare marker with a
`code: sentence` reason (`no_grimp`, `no_networkx`, `grimp_failed`, `no_python_packages`, `no_import_graph_<language>`, `no_source_files`, `no_rg`,
`not_git_root`, `no_ownership_map`, `no_lizard`), so counts-only output keeps the code
and a maturity model row reads `unknown`. No ownership map reads `no_ownership_map`,
the same marker the full run writes (`context_blocks.no_ownership_map`, #908). It
diverges from the full run in one place: `stats_summary` covers lizard's languages only. Tests:
`tests/test_headless_layers.py`.
"""
from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, TypedDict

from lib.assess_config import is_user_excluded
from lib.class_sizes import loc_by_class, percentile
from lib.run.context_blocks import no_ownership_map, structure_drift_tier_0
from lib.counts_only.counts_only_rules import REASON_SENTENCE_CODES
from lib.docs.doc_graph import is_excluded_path
from lib.complexity.generated_files import generated_reason, matches_generated_name
from lib.history.git_churn import tracked_files
from lib.run_context_types import Distribution, JsonDict, SizeBlock
from lib.run.scan_registry import STAGE_READ_SIDE, ScanSpec

Excludes = tuple[set[str], list[str]]

# lizard's per-file result: nloc and the per-function cyclomatic complexities.
FileScore = tuple[int, list[float]]
Analyzer = Callable[[str], FileScore | None]

TIER_1_REASON = (
    "not_probed: tier 1 reads commit-log co-change from the keyhole behaviour "
    "block, which only a full /assess run builds"
)


class CcnSummary(Distribution):
    """``stats_summary.ccn``: the file-aggregate complexity distribution."""

    basis: str


class HeadlessStatsSummary(TypedDict):
    """``stats_summary`` from a headless run: lizard-scored files only."""

    available: bool
    files_scored: int
    loc: SizeBlock
    ccn: CcnSummary


def _marker(code: str, sentence: str) -> JsonDict:
    return {"available": False, "reason": f"{code}: {sentence}"}


def _named(block: Any, codes: Sequence[tuple[str, str]]) -> JsonDict | None:
    """The bare marker for an unavailable ``block``, its reason led by a code.

    ``codes`` maps a substring of the scan's own reason to the code that names
    it; an unmatched reason is kept whole (``reason_code`` then reads it as
    ``unavailable``). Returns None for an available block.
    """
    if not isinstance(block, dict):
        return _marker("invalid_block", "the scan returned no block")
    if block.get("available") is not False:
        return None
    reason = str(block.get("reason", ""))
    for needle, code in codes:
        if needle in reason:
            return _marker(code, reason)
    return {"available": False, "reason": reason or "unavailable"}


def structure(scan: Any) -> JsonDict:
    """The import-graph block, or a named marker (no tool, no graph).

    A reason the scan already opens with a code (``no_import_graph_go: ...``,
    ``no_source_files: ...``) is kept whole, so ``reason_code`` reads that code.
    """
    marker = _named(scan, REASON_SENTENCE_CODES)
    if marker is not None:
        return marker
    if not scan.get("module_count"):  # a scan from before #663 reported these available
        return _marker("no_python_packages",
                       "no module in the import graph")
    block: JsonDict = scan
    return block


def promissory_markers(scan: Any) -> JsonDict:
    """The promissory-marker summary, or a named marker (no ``rg``, no git root)."""
    marker = _named(scan, (("rg not on PATH", "no_rg"), ("not a git", "not_git_root")))
    if marker is not None:
        return marker
    block: JsonDict = scan
    return block


def structure_drift(repo_root: Path) -> JsonDict:
    """Tier 0 drift with tier 1 unavailable, or ``no_ownership_map``."""
    block = structure_drift_tier_0(repo_root)
    if block is None:
        return no_ownership_map()
    block["tier_1"] = {"available": False, "reason": TIER_1_REASON}
    return block


# -- stats_summary -----------------------------------------------------------

def _distribution(values: Sequence[float]) -> Distribution:
    return {"p50": percentile(values, 50), "p95": percentile(values, 95),
            "max": float(max(values)) if values else 0.0}


def summarise(scores: Iterable[FileScore]) -> HeadlessStatsSummary:
    """``files_scored``, ``loc`` and file-aggregate ``ccn``, as the sidecar computes them.

    A file's ccn is the sum of its functions' complexities, or 1 with none
    (the treemap's rule). Every scored file is code: lizard reads no data
    language, so ``max_data`` is 0.
    """
    locs: list[int] = []
    ccns: list[float] = []
    for nloc, fn_ccns in scores:
        locs.append(nloc)
        ccns.append(sum(fn_ccns) or 1.0)
    loc_dist = _distribution(locs)
    return {
        "available": True,
        "files_scored": len(locs),
        "loc": {**loc_dist, "max_code": loc_dist["max"], "max_data": 0.0, "total": sum(locs)},
        "ccn": {"basis": "file-aggregate", **_distribution(ccns)},
    }


def lizard_analyzer() -> Analyzer | None:
    """A per-file lizard scorer, or None when lizard is not importable."""
    try:
        lizard = importlib.import_module("lizard")
        get_reader_for = importlib.import_module("lizard_languages").get_reader_for
    except ImportError:
        return None
    analyze_file = getattr(lizard, "analyze_file", None)
    if analyze_file is None:  # a stub module standing in for lizard
        return None

    def analyze(path: str) -> FileScore | None:
        if get_reader_for(path) is None:
            return None
        info = analyze_file(path)
        return int(info.nloc), [float(fn.cyclomatic_complexity) for fn in info.function_list]

    return analyze


def source_files(repo_root: Path, excludes: Excludes, scope: Path | None) -> list[Path]:
    """Tracked files under ``scope`` (or the root), minus excluded and generated ones."""
    dirs, patterns = excludes
    root = repo_root.resolve()
    base = (scope or root).resolve()
    picked: list[Path] = []
    for path in sorted(tracked_files(root) or ()):
        try:
            rel = path.relative_to(root)
            path.relative_to(base)
        except ValueError:
            continue
        if is_excluded_path(rel) or is_user_excluded(rel, dirs, patterns):
            continue
        if matches_generated_name(rel) or not path.is_file() or generated_reason(path):
            continue
        picked.append(path)
    return picked


def stats_summary(
    repo_root: Path, excludes: Excludes, scope: Path | None,
    analyzer: Callable[[], Analyzer | None] | None = None,
) -> JsonDict:
    """The size and complexity distribution of the tracked source files, or ``no_lizard``."""
    analyze = (analyzer or lizard_analyzer)()
    if analyze is None:
        return _marker("no_lizard", "lizard not installed; file size and complexity not scored")
    scores: list[FileScore] = []
    rels: list[str] = []
    root = repo_root.resolve()
    for path in source_files(repo_root, excludes, scope):
        try:
            score = analyze(str(path))
        except Exception:  # noqa: BLE001 - one unreadable file never sinks the block
            continue
        if score is not None:
            scores.append(score)
            rels.append(path.relative_to(root).as_posix())
    block: JsonDict = dict(summarise(scores))
    block["loc_by_class"] = loc_by_class(zip(rels, (nloc for nloc, _ in scores)))
    return block


# The headless runner's entries for the four blocks. Each replaces any core
# entry that publishes the same name (``signals.signal_scans``).
HEADLESS_SCANS: tuple[ScanSpec, ...] = (
    ScanSpec("structure", structure, ("structure_scan",), STAGE_READ_SIDE),
    ScanSpec("stats_summary", stats_summary, ("repo_root", "excludes", "scope"), STAGE_READ_SIDE),
    ScanSpec("promissory_markers", promissory_markers, ("promissory",), STAGE_READ_SIDE),
    ScanSpec("structure_drift", structure_drift, ("repo_root",), STAGE_READ_SIDE),
)
