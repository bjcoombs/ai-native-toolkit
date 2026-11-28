"""The not-measured Step reading: "Step N, Step N+1 not measured" when the next Step's open gates were not read (#1640).

A run holds Step N when every gate of Step N holds. Its next Step reads **not measured**
when each gate of Step N+1 that does not hold reads ``not_measured`` (the level result's
gate blockers, ``lib/scoring/level_tally.py``): the scan could not read that gate, so the
run can say neither that the Step is reached nor that a gate is missing. One unmet gate
among them settles it the other way: that gate holds the Step back whatever the others
read, and the run reads as before.

``next_unread(level)`` returns ``(step, next_step, reasons)`` for that case, the reason
codes once each in gate order; None at the top Step, below Step 1, when any open gate is
unmet, set aside, declined or held by a hazard, or for anything that is not a level result.
It reads only ``step`` and ``next_step.blockers``, so a ``fleet-data.json`` repository entry
(``lib/fleet/fleet_data_steps.py``) reads the same as the level result it was copied from.
``step_reading(level)`` is the words, ``Step 2, Step 3 not measured``, and ``unread_gap``
how one not-measured gate reads beside the others: ``the CI runs tests gate, not measured
here (needs a full run)``, never as a gate not held.

Readers: the badge summary and ``log.md`` readiness line (``lib/scoring/headline.py``),
the top card's lead and gate lines (``lib/top_card.py``), the fleet's Step funnel
(``lib/fleet/fleet_step_funnel.py``, which counts these repositories in its
``next_not_measured``) and the fleet front screen (``lib/fleet/fleet_front.py``). The
one-change-away count never lists them: ``headline.lone_condition`` needs a condition unmet
on its own criteria. No gate changes here; the Step a run holds is the level result's.
Tests: ``tests/test_step_reading.py``.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["next_unread", "step_reading", "unread_gap"]


def next_unread(level: Any) -> tuple[int, int, list[str]] | None:
    """``(step, next_step, reasons)`` when the next Step reads not measured; None otherwise."""
    if not isinstance(level, Mapping):
        return None
    step, nxt = level.get("step"), level.get("next_step")
    if not isinstance(step, int) or not isinstance(nxt, Mapping) or not isinstance(nxt.get("step"), int):
        return None
    blockers = nxt.get("blockers")
    gates = [b for b in blockers if isinstance(b, Mapping) and b.get("kind") == "gate"] \
        if isinstance(blockers, list) else []
    if not gates or any(b.get("state") != "not_measured" or b.get("polarity") == "hazard" for b in gates):
        return None
    reasons = [str(b["reason"]) for b in gates if b.get("reason")]
    return step, nxt["step"], list(dict.fromkeys(reasons))


def step_reading(level: Any) -> str | None:
    """``Step N, Step N+1 not measured`` for a run ``next_unread`` finds; None otherwise."""
    unread = next_unread(level)
    return None if unread is None else f"Step {unread[0]}, Step {unread[1]} not measured"


def unread_gap(noun: str, reason: str) -> str:
    """A gate that was not measured, as a gate line names it: ``reason`` is already in words."""
    gate = noun if noun.endswith("gate") else f"{noun} gate"  # GATE_NOUN's "coverage gate" names itself
    return f"the {gate}, not measured here" + (f" ({reason})" if reason else "")
