"""Read-side adapters for the scan table: one callable per registered block.

Each function adapts a lib scan to the table's calling convention (its reads,
positionally) and returns the run-context value ``build_run_context`` used to
assign by hand. They hold no logic of their own beyond picking fields out of a
tuple or calling a serialiser: the scan modules stay the single source of each
block's shape.

The scans that ran through ``safe`` before the migration still degrade, with
the same reason label (``ScanSpec.label``); the integrations that wrap their own
pieces (the keyhole integrate, the liveness fallback blocks, structure drift)
are declared gates in the table.

Read-side adapters for the scan table (#557): one callable per registered block,
taking its reads positionally and calling the scan module or `context_blocks`
serialiser that owns the block's shape (doc graph, liveness, structure, coverage,
test pressure and focus, promissory markers, accretion, the keyhole integration and
its disclosures, decline markers, offers, anomalies), plus the doc-staleness entries
(#558): `doc_staleness` (over the doc graph's doc->code edges, degrading under its own
label), `stale_hubs`, `churn_degenerate` and the private `doc_join`
(`analyze_doc_complexity_join`), which the keyhole integration reads for its
`documentation` block; a failed join degrades that block with reason
`documentation scan failed: ...`. `anomalies` reads the run header
only, which holds every key `detect_anomalies` inspects. No logic of its own. Tests:
`tests/test_run_setup.py`, and the run-context tests that patch a scan here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.accretion_ratchet import AccretionScan, scan_accretion_ratchet
from lib.anomaly_detector import detect_anomalies
from lib.archetype import analyze_archetype
from lib.since_last_run import SinceLastRun
from lib.since_last_run import since_last_run as since_last_run_block
from lib.assess_config import WorkingNotesConfig, load_structure_config, load_working_notes_config
from lib.change_coupling import RenameMap
from lib.context_blocks import (
    accretion_block,
    accretion_lookup,
    attach_exclusion_disclosures,
    attach_keyhole_blocks,
    attach_liveness_blocks,
    attach_structure_drift,
    build_stale_hubs,
    coverage_report_block,
    dict_or,
    doc_to_code_edges,
    excluded_generated,
)
from lib.coverage_report import detect_coverage_report, load_coverage_data
from lib.decline_markers import build_decline_block
from lib.doc_complexity_join import analyze_doc_complexity_join
from lib.doc_graph import build_doc_graph
from lib.doc_staleness import analyze_doc_staleness
from lib.gap_actions import build_gap_actions
from lib.instruction_files import (
    broken_instruction_refs,
    detect_ancestor_instructions,
    instruction_file_size,
)
from lib.interactivity import build_offers_block
from lib.keyhole_signals import integrate
from lib.headline import Headline, run_context_headline
from lib.level_result_types import LevelResult, OwnerExclusionsBlock
from lib.level_tally import LEVEL_READS, run_context_level
from lib.liveness_scan import scan_liveness
from lib.owner_exclusions import owner_exclusions_block
from lib.unit_discovery import UnitsBlock, scan_units
from lib.unit_levels import UnitLevelsBlock, unit_levels as unit_levels_block
from lib.unit_rollup import UnitRollUpBlock, unit_rollup as unit_rollup_block
from lib.unit_readings import UnitReadingsBlock, read_units, split_reads
from lib.mutation_cap import mutation_not_run_cap, normalize_test_pressure
from lib.promissory_markers import scan_promissory_markers
from lib.run_context_types import DocGraphBlock, KeyholeIntegration
from lib.sibling_tests import TestIndex, build_test_index
from lib.graph.structure_graph import StructureBlock, analyze_structure
from lib.test_focus import compute_test_focus
from lib.test_pressure import scan_test_pressure

Excludes = tuple[set[str], list[str]]


# -- setup-stage scans (feed the wiki written after the run) -----------------

def promissory_scan(repo_root: Path, excludes: Excludes, scope: Path | None) -> dict[str, Any]:
    """Promissory markers (stale TODO/FIXME, suppressions, disabled tests), summarised."""
    dirs, patterns = excludes
    return scan_promissory_markers(
        repo_root, extra_exclude_dirs=dirs, extra_exclude_patterns=patterns, scope=scope,
    ).summary()


def accretion_scan(repo_root: Path) -> AccretionScan:
    """Accretion ratchet over every file; never raises (degrades internally)."""
    return scan_accretion_ratchet(repo_root)


def hot_test_index(repo_root: Path, current: dict[str, Any]) -> TestIndex | None:
    """One repository test index for the hot files' parallel-tree probe, when any are hot."""
    return build_test_index(repo_root) if current.get("top_hotspots") else None


def working_notes(repo_root: Path) -> WorkingNotesConfig:
    """``.assess/config.toml`` working-notes settings for the doc graph."""
    return load_working_notes_config(repo_root)


def doc_graph(
    repo_root: Path, excludes: Excludes, scope: Path | None, notes: WorkingNotesConfig,
) -> DocGraphBlock:
    """The Layer 0 doc graph."""
    dirs, patterns = excludes
    return build_doc_graph(
        repo_root, extra_exclude_dirs=dirs, extra_exclude_patterns=patterns, scope=scope,
        working_notes_dirs=notes.dirs, working_notes_ignore=notes.ignore,
    ).as_dict()


# -- doc staleness and the doc x complexity join -----------------------------

def doc_staleness(
    repo_root: Path, doc_graph_block: dict[str, Any], excludes: Excludes, scope: Path | None,
) -> Any:
    """Doc staleness over the doc graph's doc->code edges (``lib.doc_staleness``)."""
    dirs, patterns = excludes
    return analyze_doc_staleness(
        repo_root, doc_to_code_edges=doc_to_code_edges(doc_graph_block),
        extra_exclude_dirs=dirs, extra_exclude_patterns=patterns, scope=scope,
    )


def stale_hubs(doc_graph_block: dict[str, Any], staleness: Any) -> list[dict[str, Any]]:
    """Doc-graph PageRank x doc staleness: the priority Layer 0 signal."""
    return build_stale_hubs(doc_graph_block, staleness)


def churn_degenerate(staleness: Any) -> bool:
    """True when every file shows ~1 commit (shallow clone, fresh import,
    squashed tree): churn-derived findings are discounted and the treemap
    saturation axis is inactive. Single source of truth: the doc-staleness
    block (``lib.git_churn.churn_is_degenerate``); False when that degraded.
    """
    return bool(dict_or(staleness, {}).get("churn_degenerate", False))


def doc_join(current: dict[str, Any], staleness: Any, repo_root: Path) -> Any:
    """The doc x complexity join (Signal C) the keyhole documentation block reads."""
    return analyze_doc_complexity_join(current, dict_or(staleness, {}), repo_root)


# -- instruction surface -----------------------------------------------------

def untracked_instruction_files(grading: tuple[Any, ...]) -> Any:
    """Instruction files present on disk but not committed."""
    return grading[2]


def instruction_refs(doc_graph_block: dict[str, Any], grading: tuple[Any, ...]) -> list[dict[str, Any]]:
    """Dangling instruction symlinks plus entry docs linking a missing instruction file."""
    return broken_instruction_refs(doc_graph_block, grading[3])


def sensitive_instruction_content(grading: tuple[Any, ...]) -> Any:
    """Redacted sensitive-content findings in candidate instruction files (#56)."""
    return grading[5]


def archetype(repo_root: Path) -> dict[str, Any]:
    """Software repo or knowledge base (#224); a knowledge base marks L2-L7 N/A."""
    return analyze_archetype(repo_root)


def ancestor_instructions(repo_root: Path) -> list[str]:
    """Instruction files above the repo root that reach no fresh clone (#57)."""
    return detect_ancestor_instructions(repo_root)


def skills_blocks(grading: tuple[Any, ...]) -> dict[str, Any]:
    """Progressive-disclosure signals: skills directories, count and files."""
    skills_info = grading[4]
    return {
        "skills_present": skills_info["skills_dirs_present"],
        "skills_count": skills_info["skills_count"],
        "skill_files": skills_info["skill_files"],
    }


def instruction_sizes(instruction_files: dict[str, Any]) -> Any:
    """Per-file size, always-on lines and size penalty."""
    return instruction_file_size(instruction_files)


# -- liveness, structure, coverage, tests ------------------------------------

def liveness_scan(repo_root: Path, excludes: Excludes, scope: Path | None) -> Any:
    """Layer 1 liveness: dead-code candidates, observability, capability blocks."""
    dirs, patterns = excludes
    return scan_liveness(
        repo_root, extra_exclude_dirs=dirs, extra_exclude_patterns=patterns, scope=scope,
    )


def liveness_blocks(liveness: Any) -> dict[str, Any]:
    """dead_code / observability, plus capability keys when a language is detected."""
    blocks: dict[str, Any] = {}
    attach_liveness_blocks(blocks, liveness)
    return blocks


def structure_scan(repo_root: Path, excludes: Excludes, scope: Path | None) -> StructureBlock:
    """Static import structure, read with the configured keyhole budget."""
    return analyze_structure(
        repo_root, keyhole_budget=load_structure_config(repo_root)["keyhole_budget"],
        extra_exclude_dirs=excludes[0], scope=scope,
    ).as_dict()


def coverage_data(repo_root: Path) -> Any:
    """Parsed line coverage from an existing report, or None (never runs the suite)."""
    return load_coverage_data(repo_root)


def coverage_report(repo_root: Path, coverage: Any) -> dict[str, Any]:
    """Provenance of the coverage report read, or "none found"."""
    return coverage_report_block(detect_coverage_report(repo_root), coverage)


def test_pressure_scan(repo_root: Path, current: dict[str, Any], coverage: Any) -> Any:
    """Read-only test-pressure scan on the hot files (mutation pass off)."""
    hot_files = [h.get("path") for h in current.get("top_hotspots", []) if h.get("path")]
    return scan_test_pressure(repo_root, hot_files=hot_files, opt_in=False, coverage_data=coverage)


def test_pressure_block(raw: Any) -> Any:
    """The test_pressure block with failure semantics preserved."""
    return normalize_test_pressure(raw)


def mutation_cap(test_pressure: Any) -> Any:
    """The Layer 6 cap that applies while the mutation tier has not run."""
    return mutation_not_run_cap(test_pressure)


def test_focus(
    repo_root: Path, current: dict[str, Any], coverage: Any, test_pressure: Any,
    index: TestIndex | None,
) -> dict[str, Any]:
    """Hotspot risk x coverage x hollow-test heuristics, ranked."""
    return compute_test_focus(
        current.get("top_hotspots", []), coverage, test_pressure.get("cheap_heuristics"),
        repo_root=repo_root, index=index,
    )


def promissory_block(promissory: Any) -> Any:
    """The promissory-marker summary scanned at setup, published in its place."""
    return promissory


def accretion_ratchet(scan: AccretionScan, current: dict[str, Any]) -> dict[str, Any]:
    """Growing files already in the top complexity/size band."""
    return accretion_block(scan, current)


def accretion_by_file(scan: AccretionScan) -> dict[str, dict[str, Any]]:
    """Per-file growth lines for the hotspot pages."""
    return accretion_lookup(scan)


# -- keyhole integration and its disclosures ---------------------------------

def keyhole(
    repo_root: Path, current: dict[str, Any], doc_staleness: Any, liveness: dict[str, Any],
    structure: Any, test_pressure: Any, promissory: Any, accretion: Any, archetype: Any,
    excludes: Excludes, scope: Path | None, rename_map: RenameMap, join: Any,
) -> KeyholeIntegration:
    """The five keyhole blocks, derived findings, attention and report products."""
    return integrate(
        repo_root=repo_root, complexity_stats=current,
        doc_staleness=dict_or(doc_staleness, {}),
        dead_code=liveness["dead_code"], observability=liveness["observability"],
        structure=structure, test_pressure=test_pressure,
        promissory_markers=dict_or(promissory, None), accretion_ratchet=accretion,
        archetype=dict_or(archetype, None),
        exclude_dirs=excludes[0], exclude_patterns=excludes[1], scope=scope,
        rename_map=rename_map, doc_join=join,
    )


def keyhole_blocks(integration: KeyholeIntegration) -> dict[str, Any]:
    """The eleven keyhole keys copied into the run context."""
    blocks: dict[str, Any] = {}
    attach_keyhole_blocks(blocks, integration)
    return blocks


def gap_actions(
    coverage: dict[str, Any], doc_graph_block: Any, current: dict[str, Any], archetype: Any,
    layer_grades: Any = None, nested_instructions: Any = None,
) -> Any:
    """Top 3 candidates read from the instruction-presence, coverage and doc-graph signals."""
    return build_gap_actions(
        coverage, doc_graph_block, current.get("top_hotspots"), dict_or(archetype, None),
        dict_or(layer_grades, None), dict_or(nested_instructions, None),
    )


def exclusion_disclosures(integration: KeyholeIntegration, excludes: Excludes) -> dict[str, Any]:
    """Config, archive and dead-path finding-exclusion disclosures."""
    blocks: dict[str, Any] = {}
    attach_exclusion_disclosures(blocks, integration, excludes[0], excludes[1])
    return blocks


def generated_exclusions(current: dict[str, Any]) -> list[dict[str, str]]:
    """Files the treemap excluded as generated, each with its reason."""
    return excluded_generated(current)


def structure_drift(repo_root: Path, integration: KeyholeIntegration) -> dict[str, Any]:
    """The structure_drift block, the ``no_ownership_map`` marker with no map, absent on a scan failure."""
    blocks: dict[str, Any] = {}
    attach_structure_drift(blocks, repo_root, integration["structure_drift_tier1"])
    return blocks


# -- run disclosures ---------------------------------------------------------

def decline_blocks(assess_dir: Path, plugin_version: str) -> dict[str, Any]:
    """Active permanent declines (.assess/.no-<tool>) and the mutation re-offer flag."""
    decline = build_decline_block(assess_dir, plugin_version)
    return {
        "decline_markers": decline["markers"],
        "reoffer_mutation": decline["reoffer_mutation"],
        "decline_disclosures": decline["disclosures"],
    }


def owner_exclusions(repo_root: Path) -> OwnerExclusionsBlock:
    """The owner's ``[checks] exclude`` entries from .assess/config.toml (#910)."""
    return owner_exclusions_block(repo_root)


def units(repo_root: Path, excludes: tuple[set[str], list[str]]) -> UnitsBlock:
    """The monorepo units (#862). Core-only: the headless runner and /fleet scope in #866."""
    return scan_units(repo_root, excludes)


def unit_readings(units_block: Any, nested: Any, *repo_wide: Any) -> UnitReadingsBlock:
    """Each unit's own readings of the scope-sensitive scans, conditions tagged by origin (#863).
    ``repo_wide`` are ``unit_readings.REPO_BLOCKS`` then ``UNIT_EXTERNAL_READS``, in order.
    Core-only, like ``units``."""
    blocks, external = split_reads(repo_wide)
    return read_units(units_block, nested, *external, repo_blocks=blocks)


def unit_levels(readings: Any, *blocks: Any) -> UnitLevelsBlock:
    """Each unit's Step, tally and not-measured count, inherited conditions labelled (#864):
    the ``unit_readings`` block, then ``level_tally.LEVEL_READS`` in order. Core-only."""
    return unit_levels_block(readings, *blocks)


def unit_rollup(levels: Any) -> UnitRollUpBlock:
    """The repository roll-up over the units and the unit-unlock ranking (#865): the lowest
    unit's Step, "N of M measured units" at the next Step, and the changes ranked by the
    units they lift. Core-only."""
    return unit_rollup_block(levels)


def offers_blocks(non_interactive: bool) -> dict[str, Any]:
    """The interactive flag and the pre-recorded offers of a headless run."""
    block = build_offers_block(non_interactive=non_interactive)
    return {"interactive": block["interactive"], "offers": block["offers"]}


def uninstall_instructions_path() -> str:
    """The end-of-run uninstall guide, relative to the assess skill directory."""
    return "references/uninstall.md"


def anomalies(header: dict[str, Any]) -> list[dict[str, Any]]:
    """Data-quality anomalies; ``detect_anomalies`` reads only run-header keys."""
    return [
        {"code": a.code, "description": a.description, "detail": a.detail}
        for a in detect_anomalies(header)
    ]


def level(*blocks: Any) -> LevelResult:
    """The run context's ``level`` block (#744): the tally over ``level_tally.LEVEL_READS``
    (the model's blocks, then the owner's exclusions), which the registry entry reads in that order."""
    return run_context_level(dict(zip(LEVEL_READS, blocks)))


def headline(level_block: Any) -> Headline:
    """The run context's ``headline`` block (#747): the Step, its next unlock and the
    verified tally, worded from the ``level`` block under the default model."""
    return run_context_headline(level_block)


def since_last_run(level_block: Any, assess_dir: Path, repo_root: Path) -> SinceLastRun:
    """The run context's ``since_last_run`` block (#1083): actions closed, gates gained or
    lost and the next blocker, against the run-context the previous run left."""
    return since_last_run_block(level_block, assess_dir, repo_root)
