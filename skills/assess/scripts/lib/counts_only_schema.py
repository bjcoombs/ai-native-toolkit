"""The typed run-context block shapes the counts-only allow-list is checked against.

``lib.counts_only_rules`` is an allow-list by field pattern. This module walks
the TypedDicts that describe run-context blocks - the shared ones in
``lib/run_context_types.py`` and the block shapes kept beside their builders -
and lists every field that can hold a string, so a test can fail when a typed
string field has no rule. A shape that gains a text field then fails the
suite until the field is classified as a path, a token or dropped text,
instead of being dropped silently (or, worse, kept by a looser rule).

Fixture runs leave many blocks empty (``agent_harness.executes``,
``decline_markers``, ``claude_config.findings``); the type walk covers them
whether or not a run populated them.
"""
from __future__ import annotations

import types
import typing
from collections.abc import Iterator
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from lib.agent_environment import AgentEnvironmentBlock
from lib.agent_harness import AgentHarnessBlock
from lib.agent_instructions_grader import SensitiveFinding
from lib.ai_usage import AiUsageBlock
from lib.claude_config import ClaudeConfigBlock, ClaudeConfigFinding
from lib.counts_only_rules import PATH_KEYED_MAPS
from lib.coverage_gate import CoverageGate
from lib.doc_claims import DocClaimsBlock
from lib.doc_complexity_join import DocUnit
from lib.doc_staleness import DocStalenessBlock, DocStalenessRow
from lib.gap_actions import GapAction
from lib.instruction_placement import InstructionPlacementBlock
from lib.liveness_scan import ObservabilityBlock
from lib.mutation_cap import MutationNotRunCap
from lib.run_context_types import (
    AttentionRow,
    DeadCodeBlock,
    DerivedFinding,
    DocGraphBlock,
    DocumentationBlock,
    FindingsSnapshot,
    FnCcnBlock,
    GeneratedExclusion,
    HotspotRow,
    KeyholeBudget,
    KeyholeSummary,
    NestedInstructionsBlock,
    PrescribedAction,
    RuntimeBlock,
    SizeBlock,
    TestPressureBlock,
    TestPressureUnavailable,
    UnderstandingBlock,
)
from lib.run_context_types import (
    BehaviourBlock as _BehaviourBlock,
)
from lib.layer_grades import LayerGradesBlock
from lib.scc_coverage import UnreliableCcnRow
from lib.shared_tooling import SharedToolingBlock
from lib.structure_drift import EmptyOwnershipPattern
from lib.structure_graph import StructureBlock
from lib.understanding_analysis import UnderstandingModule

# Where each typed shape sits in run-context.json, as a field pattern. A list
# shape is rooted at the list's pattern (list levels add no segment).
ROOTS: tuple[tuple[str, Any], ...] = (
    ("dead_code", DeadCodeBlock),
    ("observability", ObservabilityBlock),
    ("doc_graph", DocGraphBlock),
    ("doc_staleness", DocStalenessBlock),
    ("stale_hubs", DocStalenessRow),
    ("test_pressure", TestPressureBlock),
    ("mutation_not_run_cap", MutationNotRunCap),
    ("behaviour", _BehaviourBlock),
    ("documentation", DocumentationBlock),
    ("documentation.stale_doc_on_complexity", DocUnit),
    ("documentation.unexplained_complexity", DocUnit),
    ("documentation.good_contracts", DocUnit),
    ("understanding", UnderstandingBlock),
    ("understanding.modules", UnderstandingModule),
    ("runtime", RuntimeBlock),
    ("derived_findings", DerivedFinding),
    ("attention", AttentionRow),
    ("prescribed_actions", PrescribedAction),
    ("keyhole_summary", KeyholeSummary),
    ("stats_summary.fn_ccn", FnCcnBlock),
    ("stats_summary.top_hotspots", HotspotRow),
    ("stats_summary.top_complex", HotspotRow),
    ("stats_summary.top_large", HotspotRow),
    ("stats_summary.complexity_unreliable", UnreliableCcnRow),
    ("excluded_generated", GeneratedExclusion),
    ("nested_instructions", NestedInstructionsBlock),
    ("agent_harness", AgentHarnessBlock),
    ("agent_environment", AgentEnvironmentBlock),
    ("claude_config", ClaudeConfigBlock),
    ("claude_config.findings", ClaudeConfigFinding),
    ("coverage_gate.gates", CoverageGate),
    ("gap_actions", GapAction),
    ("structure", StructureBlock),
    ("structure_drift.tier_0.empty_ownership_patterns", EmptyOwnershipPattern),
    ("sensitive_instruction_content.{}", SensitiveFinding),
    ("shared_tooling", SharedToolingBlock),
    ("layer_grades", LayerGradesBlock),
    ("prior_findings_snapshot", FindingsSnapshot),
    ("instruction_placement", InstructionPlacementBlock),
    ("ai_usage", AiUsageBlock),
    ("doc_claims", DocClaimsBlock),
    ("test_pressure", TestPressureUnavailable),
    ("stats_summary.loc", SizeBlock),
    ("stats_summary.est_tokens", SizeBlock),
    ("stats_summary.est_tokens.budget", KeyholeBudget),
)

_UNIONS = (Union, types.UnionType)


def _is_typeddict(tp: Any) -> bool:
    return isinstance(tp, type) and typing.is_typeddict(tp)


def _walk(pattern: str, tp: Any, seen: frozenset[Any]) -> Iterator[str]:
    origin = get_origin(tp)
    if tp is str or tp is Any or tp is object:
        yield pattern
    elif origin is Literal:
        if any(isinstance(a, str) for a in get_args(tp)):
            yield pattern
    elif origin in _UNIONS:
        for arm in get_args(tp):
            yield from _walk(pattern, arm, seen)
    elif origin is list:
        yield from _walk(pattern, get_args(tp)[0], seen)
    elif origin is dict:
        value = get_args(tp)[1]
        if pattern in PATH_KEYED_MAPS:
            yield from _walk(f"{pattern}.{{}}", value, seen)
        elif value is not Any:
            # A map with identifier keys (a kind, a tool): rules use ``*``.
            yield from _walk(f"{pattern}.*", value, seen)
    elif _is_typeddict(tp) and tp not in seen:
        for name, field_tp in get_type_hints(tp).items():
            yield from _walk(f"{pattern}.{name}", field_tp, seen | {tp})


def typed_string_fields() -> list[str]:
    """Every field pattern a typed run-context block can put a string at."""
    found: set[str] = set()
    for root, tp in ROOTS:
        found.update(_walk(root, tp, frozenset()))
    return sorted(found)


def _typeddicts_in(tp: Any) -> Iterator[Any]:
    if _is_typeddict(tp):
        yield tp
        for field_tp in get_type_hints(tp).values():
            yield from _typeddicts_in(field_tp)
    else:
        for arg in get_args(tp):
            yield from _typeddicts_in(arg)


def typed_field_names() -> frozenset[str]:
    """Every field name of a typed block shape: the keys a record may carry."""
    return frozenset(
        name for td in reached_typeddicts() for name in get_type_hints(td)
    )


def typed_identifier_maps() -> list[str]:
    """Patterns of typed ``dict[str, X]`` fields that are not path-keyed."""
    found: set[str] = set()

    def walk(pattern: str, tp: Any, seen: frozenset[Any]) -> None:
        origin = get_origin(tp)
        if origin in _UNIONS or origin is list:
            for arg in get_args(tp):
                walk(pattern, arg, seen)
        elif origin is dict:
            if pattern not in PATH_KEYED_MAPS and get_args(tp)[1] is not Any:
                found.add(pattern)
        elif _is_typeddict(tp) and tp not in seen:
            for name, field_tp in get_type_hints(tp).items():
                walk(f"{pattern}.{name}", field_tp, seen | {tp})

    for root, tp in ROOTS:
        walk(root, tp, frozenset())
    return sorted(found)


def reached_typeddicts() -> set[Any]:
    """Every TypedDict the ``ROOTS`` walk visits, nested shapes included."""
    return {td for _root, tp in ROOTS for td in _typeddicts_in(tp)}
