"""Deterministic gap actions: Top 3 candidates read from signals the core holds.

``prescribed_actions`` fills the Top 3 from the attention ranking. When that
ranking is low signal (or empty) it leaves slots free, and the report writer
used to fill them by judgement alone. ``build_gap_actions`` turns three measured
gaps into ready-made actions for those slots, so a free slot is filled from a
signal before it is filled from judgement:

- ``layer_grades`` (issue #821): the only instruction files are nested
  (``layer_grades.instruction_presence`` is ``nested_only``), so an agent
  starting at the root reads none of them. Advises a root ``AGENTS.md`` or
  ``CLAUDE.md`` that names the check command, and names the nested files.

- ``coverage_report``: no line-coverage report was found in a software repo,
  so test depth on the hotspots is unmeasured. Names the top non-archive
  hotspots to measure first; silent when none remain, and silent on a
  knowledge base, whose test layers are N/A.
- ``doc_graph``: fewer than ``REACHABILITY_FLOOR`` of the docs are reachable
  from the entry points. Names the unreachable docs.

Each entry is ``{signal, action, paths}``, where ``signal`` is the run-context
block the gap was read from. The root-instructions entry, when present, comes
first, then coverage.
A lint complexity-rule gap is deliberately absent: the core has no detector for
it, and the layer scorer owns that check.
"""
from __future__ import annotations

from typing import TypedDict

from lib.keyhole_signals import is_archive_path
from lib.run_context_types import JsonDict


class GapAction(TypedDict):
    """A Top 3 candidate read from a measured gap: ``{signal, action, paths}``."""

    signal: str
    action: str
    paths: list[str]

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
    coverage_report: JsonDict, top_hotspots: list[JsonDict], archetype: JsonDict,
) -> GapAction | None:
    # A knowledge base marks the test layers N/A, so no coverage remediation is
    # proposed there; an unknown archetype is not assumed to be software.
    if archetype.get("archetype") != "software":
        return None
    # Only the "none found" block proposes measuring: a failed scan degrades to
    # available: false *with* a reason, and a failed read is not a missing report.
    if coverage_report.get("available") is not False or coverage_report.get("reason"):
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


# How many nested instruction files the root-instructions action names; the rest
# are in nested_instructions.files.
MAX_NESTED_PATHS = 5


def _root_instructions_gap(layer_grades: JsonDict, nested: JsonDict) -> GapAction | None:
    if layer_grades.get("instruction_presence") != "nested_only":
        return None
    listed = nested.get("files")
    files = listed if isinstance(listed, list) else []
    paths = sorted(f["path"] for f in files if isinstance(f, dict) and isinstance(f.get("path"), str))
    return {
        "signal": "layer_grades",
        "action": (
            "Add a root instruction file (`AGENTS.md` or `CLAUDE.md` at the repository "
            "root) that names the check command an agent runs before every commit: "
            "the only instruction files are nested, so they load only when an agent "
            "works in their directory, and a session that starts at the root reads none."
        ),
        "paths": paths[:MAX_NESTED_PATHS],
    }


def _reachability_gap(doc_graph: JsonDict) -> GapAction | None:
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
    coverage_report: JsonDict | None,
    doc_graph: JsonDict | None,
    top_hotspots: list[JsonDict] | None,
    archetype: JsonDict | None,
    layer_grades: JsonDict | None = None,
    nested_instructions: JsonDict | None = None,
) -> list[GapAction]:
    """Return the gap actions that fire, root instructions then coverage first; ``[]`` when none do."""
    gaps = [
        _root_instructions_gap(layer_grades or {}, nested_instructions or {}),
        _coverage_gap(coverage_report or {}, top_hotspots or [], archetype or {}),
        _reachability_gap(doc_graph or {}),
    ]
    return [g for g in gaps if g is not None]
