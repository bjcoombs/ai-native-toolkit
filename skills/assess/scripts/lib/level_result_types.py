"""The shape of one level result (``lib/level_tally.py``, #744).

Kept apart from the engine so ``lib.counts_only_schema`` can walk it without an
import cycle (the engine reads ``lib.level_signals``, which reads the counts-only
shapes). String fields hold condition ids, states, grades and reason codes only.

The `LevelResult` TypedDicts (`ConditionResult`, `Tally`, `NotMeasured`, `Blocker`,
`NextStep`) and the run context's `Headline` (#747), kept apart from `level_tally.py` so `counts_only_schema` walks them without an
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
    disposition: NotRequired[str]  # "excluded_by_owner": the owner excluded this condition (#910)
    exclusion_kind: NotRequired[str]  # the exclusion's kind (lib.owner_exclusions.KINDS)
    polarity: NotRequired[str]  # "hazard" only: a harm, never counted (#843); absent: positive


class NotMeasured(TypedDict):
    id: str
    reason: str


class Tally(TypedDict):
    met: int  # the level: counted conditions met
    measured: int  # counted conditions met or unmet
    not_measured: int  # conditions of any grade not measured


class Blocker(TypedDict):
    kind: str  # "gate" or "threshold"
    gate: NotRequired[str]
    condition: NotRequired[str]  # the gate's first condition that is not met
    state: NotRequired[str]
    reason: NotRequired[str]
    threshold: NotRequired[int]
    met: NotRequired[int]
    disposition: NotRequired[str]  # the gate condition's disposition, when the owner excluded it
    exclusion_kind: NotRequired[str]


class ExcludedCondition(TypedDict):
    """A condition the owner excluded, as the level result lists it (#910)."""

    id: str  # a condition id of the model
    kind: str  # one of lib.owner_exclusions.KINDS
    source: str  # "repo": .assess/config.toml


class NextStep(TypedDict):
    step: int
    threshold: int
    blockers: list[Blocker]


class LevelResult(TypedDict):
    level_result_version: int
    levels_version: str
    tally: Tally
    counted: list[str]  # counted conditions met
    listed: list[str]  # listed (not counted) conditions met
    not_measured: list[NotMeasured]
    conditions: list[ConditionResult]
    step: int | None  # None: the model defines no Steps
    next_step: NextStep | None  # None: no Steps, or the top Step is held
    excluded: NotRequired[list[ExcludedCondition]]  # present only when the owner excluded a condition


class OwnerExclusion(TypedDict):
    """One ``[checks] exclude`` entry in force (``lib/owner_exclusions.py``, #910).

    The provenance fields mirror a decline marker's (``lib/decline_markers.py``).
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


class OwnerExclusionsBlock(TypedDict):
    """The run context's ``owner_exclusions`` block (``lib/owner_exclusions.py``, #910)."""

    exclusions: list[OwnerExclusion]
    skipped: list[SkippedExclusion]
    disclosures: list[str]  # one line per exclusion in force, for the report


class Headline(TypedDict):
    """The run context's ``headline`` block (``lib/headline.py``, #747)."""

    headline_version: int
    available: bool
    levels_version: str | None
    step: int | None  # None: not measured, or the model defines no Steps
    next_step: int | None  # None: at the top Step, no Steps, or not measured
    lines: list[str]
    markdown: str  # the lines as Markdown, pasted verbatim into the report
    summary: str  # the one-line form: badge message and log entry
    reason: NotRequired[str]  # why the headline is unavailable
