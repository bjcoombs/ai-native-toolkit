"""Typed shapes of the run-context blocks a default maturity-model criterion reads (#793).

These blocks are built as dict literals by their scans, so until #793 they had no
TypedDict in ``counts_only_schema.ROOTS`` and ``level_signals.team_path_ok``
accepted any sub-path under them: a misspelt ``coverage_gate.enforcd`` in a
``--levels`` file loaded and evaluated ``unknown``. With a typed root every segment
is checked at load, as it already was for ``ci_checks`` and ``structure``.

Each shape records the keys its builder writes and their types; the builders stay as
they are. ``tests/test_block_shapes.py`` checks real blocks (the fleet golden and a
live run) against these shapes, so a builder that adds a key fails until the shape
names it. Every block may also degrade to ``{"available": false, "reason"}``, which
``team_path_ok`` accepts for any block.

- ``agent_ops`` (``lib.agents.agent_ops.scan_agent_ops``)
- ``coverage_gate`` (``lib.testing.coverage_gate.detect_coverage_gate``)
- ``instruction_claims`` (``lib.instruction_claims.scan_instruction_claims``)
- ``promissory_markers`` (``lib.keyhole.promissory_markers.MarkerScan.summary``)
- ``review_reality`` (``lib.hosting.review_reality.summarize`` and its GitLab twin)
- ``structure_drift`` (``lib.context_blocks._structure_drift_block``; the headless
  runner marks tier 1 ``{"available": false, "reason"}``)

TypedDicts for the run-context blocks a default maturity-model criterion reads that are
built as dict literals (#793): `AgentOpsBlock`, `CoverageGateBlock`,
`InstructionClaimsBlock`, `PromissoryMarkersBlock`, `ReviewRealityBlock` and
`StructureDriftBlock`, each a `counts_only_schema.ROOTS` entry and a `shapes` entry of
its collector in `checks/core.toml`. The builders are unchanged; the shapes record what
they write. Tests: `tests/test_block_shapes.py` runs each builder on a small repository
and checks every key and leaf type against its shape, checks the fleet golden's blocks,
and fails if a default criterion reads a block with no typed root.
"""
from __future__ import annotations

from typing import NotRequired, TypedDict

# Defined beside its builder (the return type of detect_coverage_gate); re-exported here.
from lib.testing.coverage_gate import CoverageGateBlock as CoverageGateBlock
from lib.keyhole.structure_drift import EmptyOwnershipPattern


# -- agent_ops ---------------------------------------------------------------

class AgentOpsSettingsFile(TypedDict):
    """One ``.claude/settings*.json`` file found on disk, tracked or not."""

    path: str
    tracked: bool
    parse_ok: bool
    allow_count: int
    deny_count: int
    ask_count: int
    hook_events: int
    sandbox_configured: bool
    default_mode: NotRequired[str]  # information only (#1153): never fails the practice


class AgentOpsDir(TypedDict):
    """A hooks or routine directory: how many files it holds, and how many are tracked."""

    path: str
    present: bool
    file_count: int
    tracked_count: int


class ManagedSource(TypedDict):
    """One managed settings source that exists on the machine, by kind token; counts only."""

    kind: str
    parse_ok: bool
    allow_count: int
    deny_count: int
    ask_count: int
    hook_events: int


class ManagedSettingsBlock(TypedDict):
    """``agent_ops.managed``: the machine's managed settings (``lib.agents.managed_settings``)."""

    state: str
    sources: list[ManagedSource]
    reason: NotRequired[str]


class AgentOpsSummary(TypedDict):
    """The three signals the maturity model reads. ``permissions_encoded`` is true on tracked
    repository evidence or a managed source's permission entry, and null with
    ``permissions_encoded_reason`` when neither holds and a managed source could not be read (#1153);
    the other two are true only on tracked evidence."""

    permissions_encoded: bool | None
    hooks_present: bool
    routines_present: bool
    permissions_source: NotRequired[str]  # "repository" or "managed", when encoded
    permissions_encoded_reason: NotRequired[str]  # the not-measured code, when null


class AgentOpsBlock(TypedDict):
    """``run-context.json`` ``agent_ops``."""

    available: bool
    settings: list[AgentOpsSettingsFile]
    hooks_dir: AgentOpsDir
    routine_dirs: list[AgentOpsDir]
    managed: ManagedSettingsBlock
    summary: AgentOpsSummary


# -- coverage_gate -------------------------------------------------------------

# -- instruction_claims ----------------------------------------------------------

class InstructionClaimFailure(TypedDict):
    """A claim in an instruction file that the tree contradicts; the kind adds its fields."""

    file: str
    line: int
    kind: str
    path: str
    reason: str
    version: NotRequired[str]  # a pin
    claimed: NotRequired[int]  # a count
    actual: NotRequired[int]  # a count


class InstructionClaimsBlock(TypedDict):
    """``run-context.json`` ``instruction_claims``."""

    total: int
    verified: int
    failed: int
    failures: list[InstructionClaimFailure]


# -- promissory_markers ----------------------------------------------------------

class MarkerFamilyCounts(TypedDict):
    """One marker family's counts (``todo``, ``suppression``, ``deprecation``, ...)."""

    total: int
    stale: int
    linked: int
    justified: int
    agent_introduced: int


class StaleMarkerFile(TypedDict):
    """One file's stale markers, keyed by its path in ``stale_by_file``."""

    count: int
    families: list[str]
    max_survived: int


class MarkerOffender(TypedDict):
    """One of the most severe stale markers."""

    path: str
    line: int
    family: str
    text: str
    linked: bool
    justified: bool
    commit: str
    agent_introduced: bool | None
    survived_touches: int
    severity: float


class PromissoryMarkersBlock(TypedDict):
    """``run-context.json`` ``promissory_markers``."""

    available: bool
    reason: str | None
    aging_reliable: bool
    stale_touches_threshold: int
    families: dict[str, MarkerFamilyCounts]
    todo_bare: int
    todo_linked: int
    total_markers: int
    total_stale: int
    stale_agent_introduced: int
    fixture_files_excluded: int
    stale_by_file: dict[str, StaleMarkerFile]
    top_offenders: list[MarkerOffender]


# -- review_reality --------------------------------------------------------------

class ReviewRealityBlock(TypedDict):
    """``run-context.json`` ``review_reality``: counts and shares only."""

    available: bool
    merged_count: int
    oldest_merged_days_ago: int | None
    reviewed_share: float | None
    approved_share: float | None
    bot_review_share: float | None
    self_merged_share: float | None
    review_required: bool | None
    hollow_required_review: bool | None
    required_approval_bypassed: bool | None
    # Per-change tokens, oldest merge first (#943); absent in blocks written before it.
    sequence: NotRequired[list[str]]
    bot_sequence: NotRequired[list[str]]


# -- structure_drift -------------------------------------------------------------

class StructureDriftTier0(TypedDict):
    """Declared ownership patterns that match no tracked file."""

    available: bool
    empty_ownership_patterns: list[EmptyOwnershipPattern]
    total_patterns: int
    matched_patterns: int
    boundary_patterns: int


class StructureDriftTier1(TypedDict):
    """Grouping disagreement counts, or ``{"available": false}`` (with a reason headless)."""

    available: bool
    reason: NotRequired[str]
    human_grouped_static_splits_count: NotRequired[int | None]
    human_split_static_fuses_count: NotRequired[int | None]
    human_grouped_never_cochange_count: NotRequired[int]
    human_split_but_cochange_count: NotRequired[int]
    human_static_agree_count: NotRequired[int | None]
    human_cochange_agree_count: NotRequired[int]
    static_lens_reason: NotRequired[str]
    seam_allowlist_applied: NotRequired[bool]
    allowlist_pairs_count: NotRequired[int]


class StructureDriftBlock(TypedDict):
    """``run-context.json`` ``structure_drift``; omitted (or a marker) with no ownership map."""

    tier_0: StructureDriftTier0
    tier_1: StructureDriftTier1
