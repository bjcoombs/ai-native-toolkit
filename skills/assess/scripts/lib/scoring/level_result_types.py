"""The shape of one level result (``lib/scoring/level_tally.py``, #744).

Kept apart from the engine so ``lib.counts_only.counts_only_schema`` can walk it without an
import cycle (the engine reads ``lib.scoring.level_signals``, which reads the counts-only
shapes). String fields hold condition ids, states, grades and reason codes only.

The `LevelResult` TypedDicts (`ConditionResult`, `Tally`, `NotMeasured`, `Blocker`,
`NextStep`, `InPlace`, and #828's `HazardEntry`, `DeclinedCondition` and `DeclineRecord`) and the run context's `Headline` (#747) with its per-tag `TagCell` (#844) and the level's per-tag `TagRow` (#940), kept apart from `level_tally.py` so `counts_only_schema` walks them without an
import cycle. Tests: `tests/test_level_tally.py`.
"""
from __future__ import annotations

from typing import NotRequired, TypedDict


class ConditionResult(TypedDict):
    id: str  # <row id>.<level>
    row: str
    level: int
    grade: str | None  # None: a criterion signal has no producer input class
    counted: bool
    state: str  # one of STATES
    reason: NotRequired[str]  # not_measured, or a requires that is not met
    # "excluded_by_owner": the owner excluded it (#910); "demoted_by_exit_rule": the fleet exit rule (#1086)
    disposition: NotRequired[str]
    exclusion_kind: NotRequired[str]  # the exclusion's kind (lib.owner_exclusions.KINDS)
    polarity: NotRequired[str]  # "hazard" only: a harm, never counted (#843); absent: positive
    blocks: NotRequired[str]  # a hazard only: the Step gate it holds while met (#828)
    why: NotRequired[str]  # the manifest whys of its criteria, joined by a space (#939)
    # the base model or practice pack its row came from (lib/pack_layering.py, #1274); present
    # only when a pack added rows to the model
    source: NotRequired[str]


class NotMeasured(TypedDict):
    id: str
    reason: str


class Tally(TypedDict):
    met: int  # the level: counted conditions met
    measured: int  # counted conditions met or unmet
    not_measured: int  # conditions of any grade not measured


class Blocker(TypedDict):
    kind: str  # "gate": the only kind since Steps read gates only (#1083)
    gate: NotRequired[str]
    # the gate's first condition that is not met, or the met hazard that holds it (polarity set, #828)
    condition: NotRequired[str]
    polarity: NotRequired[str]  # "hazard": ``condition`` is a hazard that undermines the gate (#828)
    state: NotRequired[str]
    reason: NotRequired[str]
    disposition: NotRequired[str]  # the gate condition's disposition, when the owner excluded it
    exclusion_kind: NotRequired[str]


class ExcludedCondition(TypedDict):
    """A condition the owner excluded, as the level result lists it (#910)."""

    id: str  # a condition id of the model
    kind: str  # one of lib.owner_exclusions.KINDS
    source: str  # "repo": .assess/config.toml


class HazardEntry(TypedDict):
    """One hazard present, as the level result lists it apart from the tally (#828)."""

    id: str  # the hazard condition's id
    fix: str  # the check manifest's fix for it: the change that clears it ("" when none; dropped from counts-only)
    gate: NotRequired[str]  # the Step gate it holds; absent when it blocks none


class DeclinedCondition(TypedDict):
    """A condition the repository declined, as the level result lists it (#828)."""

    id: str  # a condition id of the model
    source: str  # the decline record's source, such as "agents_marker"


class NextStep(TypedDict):
    step: int
    blockers: list[Blocker]


class InPlace(TypedDict):
    """One practice of a level result's ``in_place`` list (``lib/scoring/level_tally.py``, #1334).

    A row is listed when it is at its top level (every condition counted and met; a hazard
    holds when it reads unmet) or reads a gate of a Step already held. A row with a condition
    not measured, excluded, unmet or only listed (config-only, declared, demoted) is never top.
    """

    row: str  # a row id of the model
    level: int  # the row's top level when ``top``, else the highest level a held gate reads
    top: bool  # every condition of the row is met: the practice is fully in place
    gates: list[str]  # gate ids of the Steps already held that read this row, once each, in Step order
    why: NotRequired[str]  # the condition at ``level``'s why (#939); absent when it has none


class TagRow(TypedDict):
    """One truth tag of a level result's ``tags`` (``lib/scoring/level_tags.py``, #940).

    Every tag of ``TRUTH_TAGS`` has a row, in that order. ``met`` and ``measured`` are the
    headline strip cell's (``TagCell``); ``measured`` 0 means not measured, never a share of 0.
    """

    tag: str  # one of lib.scoring.levels_fields.TRUTH_TAGS
    conditions: int  # conditions with this tag, any grade or state; 0: no condition carries it yet
    countable: int  # of those, the ones the tally can count by grade: measured or verified, not a hazard
    set_aside: int  # of the countable ones: not applicable (owner-excluded, or requires one that is) or declined
    met: int  # counted conditions with this tag that are met
    measured: int  # counted conditions with this tag, met or unmet
    not_measured: list[NotMeasured]  # conditions with this tag not measured, with the reason code
    countable_not_measured: list[NotMeasured]  # of those, the countable ones: why the row has no share


class LevelResult(TypedDict):
    level_result_version: int
    levels_version: str
    tally: Tally
    counted: list[str]  # counted conditions met
    listed: list[str]  # listed (not counted) conditions met
    not_measured: list[NotMeasured]
    conditions: list[ConditionResult]
    step: int | None  # None: the model defines no Steps, or below Step 1 (next_step set, #889)
    next_step: NextStep | None  # None: no Steps, or the top Step is held
    ceiling_step: int | None  # #1374: the Step reached once next_step's blockers hold; None with no next_step
    held_above: list[str]  # #1374: gate ids already held above the current Step, once each, in Step order
    in_place: list[InPlace]  # #1334: rows at their top level and rows a held Step's gates read, in model order
    excluded: NotRequired[list[ExcludedCondition]]  # present only when the owner excluded a condition
    declined: NotRequired[list[DeclinedCondition]]  # present only when a decline record applies (#828)
    hazards: NotRequired[list[HazardEntry]]  # present only when a hazard is met (#828); never in the tally
    tags: NotRequired[list[TagRow]]  # #940: every truth tag, in TRUTH_TAGS order; absent before #940


class OwnerExclusion(TypedDict):
    """One ``[checks] exclude`` entry in force (``lib/owner_exclusions.py``, #910).

    The provenance fields mirror a decline marker's (``lib/run/decline_markers.py``).
    """

    id: str
    kind: str
    reason: str  # the owner's own words: dropped from counts-only output
    source: str  # "repo"
    excluded_by: str | None
    excluded_at: str | None
    version: str | None  # the plugin version recorded with the entry


class SkippedExclusion(TypedDict):
    """A ``[checks] exclude`` entry not applied, so its condition stays in force."""

    index: int  # the entry's position in the list, from 0
    id: str | None  # as written: dropped from counts-only output
    problem: str  # a reason code (lib.owner_exclusions.PROBLEMS)


class DeclineRecord(TypedDict):
    """A declared opt-out that sets conditions ``declined`` (``lib/run/decline_markers.py``, #828).

    The agent-level marker gives one; a detector may add more. Never inferred from absence.
    """

    source: str  # "agents_marker": .assess/agents-declined.json
    conditions: list[str]  # condition ids as declared; []: every positive agent_context or agent_runtime one
    declined_by: str | None  # dropped from counts-only output
    declined_at: str | None
    version: str | None  # the plugin version recorded with the record
    reason: str | None  # the owner's own words: dropped from counts-only output


class OwnerExclusionsBlock(TypedDict):
    """The run context's ``owner_exclusions`` block (``lib/owner_exclusions.py``, #910)."""

    exclusions: list[OwnerExclusion]
    skipped: list[SkippedExclusion]
    disclosures: list[str]  # one line per exclusion in force, for the report
    declines: NotRequired[list[DeclineRecord]]  # #828: the declared opt-outs in force; absent before #828


class TagCell(TypedDict):
    """One cell of the headline's per-tag strip (``lib/scoring/headline.py``, #844).

    A condition with several tags is in each of their cells; the tally counts it once.
    """

    tag: str  # one of lib.scoring.levels_fields.TRUTH_TAGS
    met: int  # counted conditions with this tag that are met
    measured: int  # counted conditions with this tag, met or unmet
    verified: int  # of ``met``, those graded verified
    not_measured: int  # conditions of any grade with this tag not measured
    hazards: int  # hazard conditions with this tag that are met (the harm is present)
    declined: int  # conditions with this tag the repository declined


class Headline(TypedDict):
    """The run context's ``headline`` block (``lib/scoring/headline.py``, #747)."""

    headline_version: int
    available: bool
    levels_version: str | None
    step: int | None  # None: not measured, no Steps, or below Step 1 (next_step set, #889)
    next_step: int | None  # None: at the top Step, no Steps, or not measured
    lines: list[str]
    markdown: str  # the lines as Markdown, pasted verbatim into the report
    summary: str  # the one-line form: badge message and log entry
    verified: int | None  # counted conditions met and graded verified (#844); None: not measured
    tags: list[TagCell]  # the per-tag strip, in TRUTH_TAGS order; empty when not measured
    next_why: str  # the whys of the conditions the Next line's gates wait on (#939); "" for none
    summary_lines: list[str]  # the executive summary (lib/exec_summary.py, #1102); [] when not filled
    call_to_action: str  # the first blocking gate as a linked task; "" at the top Step or not filled
    summary_markdown: str  # the summary lines and the call to action, pasted at the top of the report
    reason: NotRequired[str]  # why the headline is unavailable
