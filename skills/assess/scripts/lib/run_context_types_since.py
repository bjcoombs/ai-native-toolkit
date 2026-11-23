"""The run context's ``since_last_run`` block (``lib/since_last_run.py``, #1083).

Kept in its own module because ``run_context_types.py`` sits at its size ceiling.
Tests: ``tests/test_since_last_run.py``.
"""
from __future__ import annotations

from typing import NotRequired, TypedDict


class ClosedAction(TypedDict):
    """An action marked done in ``actions.json`` whose commit landed after the previous run."""

    action: str  # the directive, folded onto one line: dropped from counts-only output
    condition: str | None  # the maturity-model condition id it moves, when it names one
    finding: str | None  # the finding type it addresses, when it names one
    completed_sha: str  # the commit that closed it


class SinceLastRun(TypedDict):
    """What changed between the previous run and this one: credit, gates and the next blocker."""

    available: bool
    first_run: bool  # true when no earlier run-context.json was found
    previous_run_id: str | None
    previous_commit: str | None  # the commit the previous run measured; None when unknown
    closed: list[ClosedAction] | None  # None: unknown, ``closed_reason`` says why
    closed_reason: str | None  # why ``closed`` is unknown (``since_last_run.UNKNOWN_CLOSED``)
    closed_count: int | None
    unresolved: int | None  # done entries naming a commit not in this clone; None when not read
    gates_gained: list[str] | None  # gate ids, in Step order; None: the previous run has no level
    gates_lost: list[str] | None
    next_blocker: str | None  # the first gate blocking the next Step; None at the top Step
    lines: list[str]  # the section's lines, for the report and the log.md entry
    markdown: str  # the lines as Markdown bullets
    reason: NotRequired[str]  # why the block is unavailable
