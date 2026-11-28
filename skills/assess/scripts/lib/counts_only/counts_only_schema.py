"""The typed run-context block shapes the counts-only allow-list is checked against.

``lib.counts_only.counts_only_rules`` is an allow-list by field pattern. This module walks
the TypedDicts that describe run-context blocks - the shared ones in
``lib/run_context_types.py`` and the block shapes kept beside their builders -
and lists every field that can hold a string, so a test can fail when a typed
string field has no rule. A shape that gains a text field then fails the
suite until the field is classified as a path, a token or dropped text,
instead of being dropped silently (or, worse, kept by a looser rule).

Fixture runs leave many blocks empty (``agent_harness.executes``,
``decline_markers``, ``claude_config.findings``); the type walk covers them
whether or not a run populated them.

Derives the string fields the allow-list must cover from the block TypedDicts in
`run_context_types.py` and beside their builders (`ROOTS` places each shape in
run-context.json). `typed_field_names` supplies the record keys `counts_only` keeps,
`typed_identifier_maps` the typed maps that must be declared, and `typed_string_fields`
walks them, so a typed shape that gains a
string field fails `tests/test_counts_only.py` until the field is classified, even when
no fixture run populates the block. `delivery.DeliveryBlock` is a root (#769), so counts-only output keeps
every delivery figure (`tests/test_delivery.py` checks a full block on both providers).
The blocks a default criterion reads are all roots since #793 (their shapes are in
`block_shapes.py`), which added one field name to the counts-only keys:
`promissory_markers.stale_by_file.{}.max_survived`, an integer, now survives reduction.
The newly typed string field `promissory_markers.stale_by_file.{}.families` is classified
`drop`, so it stays a count.
"""
from __future__ import annotations

import types
import typing
from collections.abc import Iterator
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from lib.agents.agent_building import AgentBuildingBlock
from lib.agents.agent_environment import AgentEnvironmentBlock
from lib.agents.agent_harness import AgentHarnessBlock
from lib.instructions.agent_instructions_grader import SensitiveFinding
from lib.ci.ci_checks import CiChecksBlock
from lib.agent_outcome import AgentOutcomeBlock
from lib.run_context_types_concurrency import ConcurrencyBlock
from lib.history.ai_usage import AiUsageBlock
from lib.counts_only.block_shapes import (
    AgentOpsBlock,
    CoverageGateBlock,
    InstructionClaimsBlock,
    PromissoryMarkersBlock,
    ReviewRealityBlock,
    StructureDriftBlock,
)
from lib.instructions.claude_config import ClaudeConfigBlock, ClaudeConfigFinding
from lib.counts_only.counts_only_rules import PATH_KEYED_MAPS
from lib.hosting.delivery import DeliveryBlock
from lib.docs.doc_claims import DocClaimsBlock
from lib.instructions.agent_consistency import AgentConsistencyBlock
from lib.docs.doc_complexity_join import DocUnit
from lib.docs.doc_staleness import DocStalenessBlock, DocStalenessRow
from lib.report.gap_actions import GapAction
from lib.instructions.guardrails import GuardrailsBlock
from lib.instructions.instruction_placement import InstructionPlacementBlock
from lib.skill_disclosure import SkillDisclosureBlock
from lib.secrets_guard import SecretsGuardBlock
from lib.skill_script_tests import SkillScriptTestsBlock
from lib.skill_personal_data import SkillPersonalDataBlock
from lib.skill_library import SkillLibraryBlock
from lib.ci.ci_model_jobs import CiModelJobsBlock
from lib.ci.ci_hazards import CiHazardsBlock
from lib.ai_governance import AiGovernanceBlock
from lib.backlog_ready_outcomes import BacklogReadyOutcomesBlock
from lib.contributor_files import ContributorFilesBlock
from lib.hosting.org_security_policy import OrgSecurityPolicyBlock
from lib.hosting.github_secret_scanning import GithubSecretScanningBlock
from lib.keyhole.liveness_scan import ObservabilityBlock
from lib.testing.mutation_cap import MutationNotRunCap
from lib.run_context_types_size import LocByClass
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
from lib.scoring.layer_grades import LayerGradesBlock
from lib.scoring.level_result_types import Headline, LevelResult, OwnerExclusionsBlock
from lib.run_context_types_since import SinceLastRun
from lib.prior_level import PriorLevelBlock
from lib.complexity.scc_coverage import UnreliableCcnRow
from lib.ci.review_automation import ReviewAutomationBlock, ReviewEvidenceBlock
from lib.ci.shared_tooling import SharedToolingBlock
from lib.unit_discovery import UnitsBlock
from lib.unit_levels import UnitLevelsBlock
from lib.unit_rollup import UnitRollUpBlock
from lib.unit_readings import UnitReadingsBlock
from lib.graph.structure_graph import StructureBlock
from lib.keyhole.understanding_analysis import UnderstandingModule

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
    ("agent_consistency", AgentConsistencyBlock),
    ("agent_harness", AgentHarnessBlock),
    ("agent_environment", AgentEnvironmentBlock),
    ("claude_config", ClaudeConfigBlock),
    ("claude_config.findings", ClaudeConfigFinding),
    ("coverage_gate", CoverageGateBlock),
    ("gap_actions", GapAction),
    ("structure", StructureBlock),
    ("structure_drift", StructureDriftBlock),
    ("sensitive_instruction_content.{}", SensitiveFinding),
    ("shared_tooling", SharedToolingBlock),
    ("review_automation", ReviewAutomationBlock),
    ("review_evidence", ReviewEvidenceBlock),
    ("ci_checks", CiChecksBlock),
    ("agent_building", AgentBuildingBlock),
    ("layer_grades", LayerGradesBlock),
    ("prior_findings_snapshot", FindingsSnapshot),
    ("prior_level", PriorLevelBlock),
    ("instruction_placement", InstructionPlacementBlock),
    ("ai_usage", AiUsageBlock),
    ("agent_outcome", AgentOutcomeBlock),
    ("concurrency", ConcurrencyBlock),
    ("skill_disclosure", SkillDisclosureBlock),
    ("secrets_guard", SecretsGuardBlock),
    ("skill_script_tests", SkillScriptTestsBlock),
    ("skill_personal_data", SkillPersonalDataBlock),
    ("skill_library", SkillLibraryBlock),
    ("ci_model_jobs", CiModelJobsBlock),
    ("ci_hazards", CiHazardsBlock),
    ("ai_governance", AiGovernanceBlock),
    ("contributor_files", ContributorFilesBlock),
    ("backlog_ready", BacklogReadyOutcomesBlock),
    ("org_security_policy", OrgSecurityPolicyBlock),
    ("github_secret_scanning", GithubSecretScanningBlock),
    ("delivery", DeliveryBlock),
    ("doc_claims", DocClaimsBlock),
    ("guardrails", GuardrailsBlock),
    ("test_pressure", TestPressureUnavailable),
    ("stats_summary.loc", SizeBlock),
    ("stats_summary.est_tokens", SizeBlock),
    ("stats_summary.est_tokens.budget", KeyholeBudget),
    ("stats_summary.loc_by_class", LocByClass),
    # The blocks a default criterion reads (#793), so a --levels path under them
    # is checked segment by segment at load.
    ("agent_ops", AgentOpsBlock),
    ("instruction_claims", InstructionClaimsBlock),
    ("promissory_markers", PromissoryMarkersBlock),
    ("review_reality", ReviewRealityBlock),
    ("level", LevelResult),
    ("owner_exclusions", OwnerExclusionsBlock),
    ("units", UnitsBlock),
    ("headline", Headline),
    ("since_last_run", SinceLastRun),
    ("unit_readings", UnitReadingsBlock),
    ("unit_levels", UnitLevelsBlock),
    ("unit_rollup", UnitRollUpBlock),
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
