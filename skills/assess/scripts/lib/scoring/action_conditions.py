"""The condition an action moves, checked against the run's level result (#747).

An action in ``finalize-input.json`` may name the maturity-model condition it
moves in ``condition`` (``<row id>.<level>``, as ``level.conditions[].id``
lists it: ``layer-5-ci.1``). The advice then runs on the same conditions as the
headline. ``assess_finalize`` refuses an action whose condition is not one this
run's ``level`` block lists, the same way it refuses a fabricated hotspot path,
before anything is written. An action without ``condition`` is accepted as
before (a finding-driven action moves no single condition).

A met condition is accepted: an action can keep a condition met (or deepen the
practice behind it), and "met at this commit" is not proof it cannot change.

The condition an action moves (#747): `condition_errors(actions, level)` returns one
message per `finalize-input.json` action whose optional `condition` is not an id the
run's `level.conditions` lists (or when the run has no level result to check it
against). `assess_finalize` raises on any, before writing, so `actions.json` is
unchanged; a met condition is accepted. Tests: `tests/test_headline.py`.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

CONDITION_KEY = "condition"


def known_conditions(level: Any) -> set[str] | None:
    """The condition ids the ``level`` block lists, or None when it has none."""
    if not isinstance(level, Mapping) or not isinstance(level.get("conditions"), list):
        return None
    return {c["id"] for c in level["conditions"] if isinstance(c, Mapping) and "id" in c}


def condition_errors(actions: Any, level: Any) -> list[str]:
    """One message per action whose ``condition`` this run's level result does not list."""
    if not isinstance(actions, Sequence) or isinstance(actions, str):
        return []
    named = [(a.get("rank"), a[CONDITION_KEY]) for a in actions
             if isinstance(a, Mapping) and CONDITION_KEY in a]
    if not named:
        return []
    known = known_conditions(level)
    errors = []
    for rank, cid in named:
        if known is None:
            errors.append(f"action rank {rank} names condition {cid!r} but run-context.json "
                          "has no level result to check it against")
        elif not isinstance(cid, str) or cid not in known:
            errors.append(f"action rank {rank} names unknown condition {cid!r}: it is not "
                          "one of the run's level.conditions ids")
    return errors
