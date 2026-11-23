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
- ``instruction_placement`` (issue #1084): a removal. Each instruction file the scan
  flags (always-on lines over ``ALWAYS_ON_FLAG_LINES`` and share over
  ``ALWAYS_ON_FLAG_SHARE``, the scan's own #419 thresholds, never a second size) gets an
  entry with ``finding`` ``REMOVAL_FINDING``, ``mode`` ``remove`` and a measured ``cost``
  (``RemovalCost``: the always-on lines). It trims text and never deletes the file, so
  the instruction-file gate, which reads presence, keeps holding. ``action_mode`` is the
  mode finalize stamps: ``remove`` for it, the keyhole mode for every other finding.

Each entry is ``{signal, action, paths, why}``, where ``signal`` is the run-context
block the gap was read from and ``why`` is that scan's why from the check manifest
(``lib.manifest.manifest_whys.scan_whys``, #939): static text, the same in every run. The root-instructions entry, when present, comes
first, then coverage.
A lint complexity-rule gap is deliberately absent: the core has no detector for
it, and the layer scorer owns that check.

Builds the run-context `gap_actions` list: Top 3 candidates for the slots
`prescribed_actions` leaves free, read from blocks `assess_core` already holds. Each
entry is `{signal, action, paths, why}`; `why` is the check manifest's why of the scan
`signal` names (#939). A `layer_grades` entry (issue #821) fires when
`layer_grades.instruction_presence` is `nested_only`: its action is to add a root
instruction file (`AGENTS.md` or `CLAUDE.md`) that names the check command, and it names
up to five nested files (`MAX_NESTED_PATHS`); it comes first. The registry runs the
scan at the post-offers stage, after `layer_grades` and `nested_instructions`.
A `coverage_report` entry fires when no coverage
report was found in a repo whose archetype is `software` and names up to three
`top_hotspots` to measure, skipping `archive/`, `archived/` and `attic/` paths (via
`keyhole_signals.is_archive_path`); it is silent on a knowledge base (test layers N/A)
and when no hotspot remains, and when it fires it comes next.
A `doc_graph` entry fires when `reachability_pct` is below `REACHABILITY_FLOOR` (0.5, with
its rationale beside it) and names up to ten unreachable docs. An `instruction_placement`
removal (#1084) follows for each instruction file the scan flags, with `finding`, `mode`
`remove` and `cost` `{metric: "always_on_lines", path, value}`. A repo with no markdown
reports reachability 0.0, but nothing is unreachable there, so no `doc_graph` entry fires.
`[]` when none fires. There is no lint complexity-rule gap: the core has no detector
for it, and the layer scorer owns that check.
"""
from __future__ import annotations

from typing import NotRequired, TypedDict

from lib.instruction_placement import ALWAYS_ON_FLAG_LINES
from lib.keyhole.keyhole_signals import is_archive_path, mode_for_finding
from lib.run_context_types import JsonDict


class RemovalCost(TypedDict):
    """What a practice costs, as measured: the metric, the file it is measured on and the
    amount (#1084). A removal is credited with the amount it saves."""

    metric: str  # "always_on_lines": instruction lines every session loads
    path: str
    value: int


class GapAction(TypedDict):
    """A Top 3 candidate read from a measured gap: ``{signal, action, paths, why}``, plus
    ``finding``, ``mode`` and ``cost`` on a removal proposal (#1084)."""

    signal: str
    action: str
    paths: list[str]
    why: str  # the manifest why of the scan ``signal`` names (#939)
    finding: NotRequired[str]  # REMOVAL_FINDING on a removal proposal
    mode: NotRequired[str]  # REMOVE_MODE on a removal proposal
    cost: NotRequired[RemovalCost]


# A removal proposal (#1084): the finding it names and the mode finalize gives it. The
# finding is not a keyhole finding; finalize maps it to ``remove`` (``action_mode``).
REMOVAL_FINDING = "instruction_always_on"
REMOVE_MODE = "remove"
REMOVAL_MODES = {REMOVAL_FINDING: REMOVE_MODE}

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
        "paths": paths, "why": "",
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
        "paths": paths[:MAX_NESTED_PATHS], "why": "",
    }


def _removal_gaps(placement: JsonDict) -> list[GapAction]:
    """A removal proposal per instruction file ``instruction_placement`` flags (#1084).

    The scan's own flag is the trigger, never a second size: always-on lines over
    ``ALWAYS_ON_FLAG_LINES`` and a share over ``ALWAYS_ON_FLAG_SHARE`` (the #419 decisions).
    The cost is the always-on lines every session loads. The proposal trims the always-on
    part and never deletes the file, so the instruction-file gate, which reads presence,
    keeps holding; the relocatable sections the scan names are where to start.
    """
    if placement.get("available") is not True:
        return []
    out: list[GapAction] = []
    for f in placement.get("files") or []:
        if not isinstance(f, dict) or f.get("flagged") is not True or not isinstance(f.get("path"), str):
            continue
        lines = f.get("always_on_lines")
        if not isinstance(lines, int):
            continue
        sections = [r["heading"] for r in f.get("relocatable") or []
                    if isinstance(r, dict) and isinstance(r.get("heading"), str) and r["heading"]]
        start = (" Start with the sections that belong elsewhere: "
                 + ", ".join(f"`{h}`" for h in sections[:3]) + ".") if sections else ""
        out.append({
            "signal": "instruction_placement",
            "action": (
                f"Remove always-on text from `{f['path']}` until at most {ALWAYS_ON_FLAG_LINES} "
                f"lines load on every session: {lines} do now. Delete what no agent needs, and "
                f"move what one needs only sometimes to the home the report names. Keep the "
                f"file and its check command, so the instruction-file gate still holds.{start}"
            ),
            "paths": [f["path"]], "why": "",
            "finding": REMOVAL_FINDING, "mode": REMOVE_MODE,
            "cost": {"metric": "always_on_lines", "path": f["path"], "value": lines},
        })
    return out


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
        "paths": unreachable[:MAX_UNREACHABLE_PATHS], "why": "",
    }


def build_gap_actions(
    coverage_report: JsonDict | None,
    doc_graph: JsonDict | None,
    top_hotspots: list[JsonDict] | None,
    archetype: JsonDict | None,
    layer_grades: JsonDict | None = None,
    nested_instructions: JsonDict | None = None,
    instruction_placement: JsonDict | None = None,
) -> list[GapAction]:
    """Return the gap actions that fire, root instructions then coverage first; ``[]`` when none do."""
    gaps = [
        _root_instructions_gap(layer_grades or {}, nested_instructions or {}),
        _coverage_gap(coverage_report or {}, top_hotspots or [], archetype or {}),
        _reachability_gap(doc_graph or {}),
        *_removal_gaps(instruction_placement or {}),
    ]
    from lib.manifest.check_manifest import core_manifest  # lazy: the manifest loads once
    from lib.manifest.manifest_whys import scan_whys
    whys = scan_whys(core_manifest())
    out = [g for g in gaps if g is not None]
    for g in out:
        g["why"] = whys.get(g["signal"], "")
    return out


def action_mode(finding: str | None) -> str:
    """The execution mode finalize stamps on an action: ``remove`` for a removal proposal
    (#1084), else the keyhole finding's mode (``keyhole_signals.mode_for_finding``)."""
    return REMOVAL_MODES.get(finding or "", "") or mode_for_finding(finding)
