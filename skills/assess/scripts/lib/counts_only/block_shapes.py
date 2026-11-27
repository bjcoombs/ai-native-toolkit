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
- ``instruction_claims`` (``lib.instructions.instruction_claims.scan_instruction_claims``)
- ``promissory_markers`` (``lib.keyhole.promissory_markers.MarkerScan.summary``)
- ``review_reality`` (``lib.hosting.review_reality.summarize`` and its GitLab twin)
- ``structure_drift`` (``lib.run.context_blocks._structure_drift_block``; the headless
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
    routines_present: bool  # a routine dir file or a scheduled agent workflow (#815)
    scheduled_agent_workflows: int
    event_agent_workflows: int
    agent_work_present: bool | None  # routines or event-triggered agents (#847); null: unread
    permissions_source: NotRequired[str]  # "repository" or "managed", when encoded
    permissions_encoded_reason: NotRequired[str]  # the not-measured code, when null
    agent_work_present_reason: NotRequired[str]  # agent_workflows_unread, when null
    agent_job_count: int  # jobs that run an agent (#849)
    agent_jobs_read_only: int  # of those, the jobs whose effective token scope has no write
    agent_jobs_least_privilege: bool | None  # every agent job read-only; null: no job, or unread
    agent_job_write_access: int | None  # agent jobs with a write scope and no writer split (hazard)
    agent_jobs_least_privilege_reason: NotRequired[str]  # the not-measured code, when null
    agent_job_write_access_reason: NotRequired[str]  # the not-measured code, when null


class AgentWorkflow(TypedDict):
    """A committed workflow that runs an agent on a schedule or an issue, PR or discussion event
    (``lib.agents.agent_ops.scan_agent_workflows``, #815, #847)."""

    path: str
    triggers: list[str]  # the workflow's ``on`` event names
    signs: list[str]  # agent_action, agent_cli, model_name, gh_aw
    via: list[str]  # local reusable workflows the signs came through
    scheduled: bool
    event: bool


class AgentJob(TypedDict):
    """A committed workflow job that runs an agent, with its effective token scope
    (``lib.agents.agent_job_scope``, #849)."""

    path: str
    job: str  # the job id; empty when the agent sign sits outside every job
    scope: str  # read_only, write, undeclared or unread
    write_scopes: list[str]  # permission names granted write (``write-all``; ``other`` if unknown)
    split: bool  # writes go through another job: gh-aw safe-outputs, or a job that needs this one
    spend_cap: bool  # max-turns or a budget limit: supporting evidence, never counted


class AgentOpsBlock(TypedDict):
    """``run-context.json`` ``agent_ops``."""

    available: bool
    settings: list[AgentOpsSettingsFile]
    hooks_dir: AgentOpsDir
    routine_dirs: list[AgentOpsDir]
    agent_workflows: list[AgentWorkflow]
    agent_workflows_unread: int
    agent_jobs: list[AgentJob]
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
