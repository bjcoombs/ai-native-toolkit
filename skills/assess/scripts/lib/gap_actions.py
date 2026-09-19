"""Deterministic gap actions: Top 3 candidates read from signals the core holds.

``prescribed_actions`` fills the Top 3 from the attention ranking. When that
ranking is low signal (or empty) it leaves slots free, and the report writer
used to fill them by judgement alone. ``build_gap_actions`` turns two measured
gaps into ready-made actions for those slots, so a free slot is filled from a
signal before it is filled from judgement:

- ``coverage_report``: no line-coverage report was found in a software repo,
  so test depth on the hotspots is unmeasured. Names the top non-archive
  hotspots to measure first; silent when none remain, and silent on a
  knowledge base, whose test layers are N/A.
- ``doc_graph``: fewer than ``REACHABILITY_FLOOR`` of the docs are reachable
  from the entry points. Names the unreachable docs.

Each entry is ``{signal, action, paths}``, where ``signal`` is the run-context
block the gap was read from. The coverage entry, when present, comes first.
A lint complexity-rule gap is deliberately absent: the core has no detector for
it, and the layer scorer owns that check.
"""
from __future__ import annotations

from lib.keyhole_signals import is_archive_path

# Reachability floor for the doc_graph gap. Below half, most of the docs cannot
# be reached by following links from README / AGENTS.md / an index page, so an
# agent that starts at the entry points misses the larger part of the written
# context: the map no longer covers the territory. At or above half the
# unreachable docs are a minority, a tidy-up rather than a Top 3 action.
REACHABILITY_FLOOR = 0.5

# How many top hotspots the coverage action names: the three riskiest files,
# enough to start measuring in one action without turning it into a file list.
MAX_COVERAGE_PATHS = 3
# How many unreachable docs the reachability action names; the rest are in
# doc_graph.unreachable.
MAX_UNREACHABLE_PATHS = 10


def _coverage_gap(
    coverage_report: dict, top_hotspots: list[dict], archetype: dict,
) -> dict | None:
    # A knowledge base marks the test layers N/A, so no coverage remediation is
    # proposed there; an unknown archetype is not assumed to be software.
    if archetype.get("archetype") != "software":
        return None
    if coverage_report.get("available") is not False:
        return None
    paths = [
        h["path"] for h in top_hotspots
        if h.get("path") and not is_archive_path(h["path"])
    ][:MAX_COVERAGE_PATHS]
    # Top 3 actions name files; with no live hotspot there is nothing to name.
    if not paths:
        return None
    return {
        "signal": "coverage_report",
        "action": (
            "Generate a line-coverage report (coverage.xml or lcov.info) in CI "
            "and read it for the top hotspots first: no report was found, so how "
            "well the tests cover the riskiest files is unmeasured."
        ),
        "paths": paths,
    }


def _reachability_gap(doc_graph: dict) -> dict | None:
    # A repo with no markdown reports reachability 0.0 with available: true.
    # Nothing is unreachable there, so no link-the-docs action applies; a
    # missing README or instruction file is the layer scorer's finding.
    if not doc_graph.get("available") or not doc_graph.get("doc_count"):
        return None
    pct = doc_graph.get("reachability_pct")
    if not isinstance(pct, (int, float)) or pct >= REACHABILITY_FLOOR:
        return None
    unreachable = sorted(doc_graph.get("unreachable") or [])
    return {
        "signal": "doc_graph",
        "action": (
            f"Link the unreachable docs from README or an index page, or delete "
            f"them: only {pct:.0%} of the docs are reachable from the entry "
            f"points, below the {REACHABILITY_FLOOR:.0%} floor."
        ),
        "paths": unreachable[:MAX_UNREACHABLE_PATHS],
    }


def build_gap_actions(
    coverage_report: dict | None,
    doc_graph: dict | None,
    top_hotspots: list[dict] | None,
    archetype: dict | None,
) -> list[dict]:
    """Return the gap actions that fire, coverage first; ``[]`` when none do."""
    gaps = [
        _coverage_gap(coverage_report or {}, top_hotspots or [], archetype or {}),
        _reachability_gap(doc_graph or {}),
    ]
    return [g for g in gaps if g is not None]
