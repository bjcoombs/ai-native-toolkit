"""The sibling units that already hold a unit's next gate: the "Learn from" line (#1155).

A unit's next Step is held back by its first blocking gate. Another unit that already
holds that gate has solved the same problem, so naming it points a team at a working
example rather than a generic instruction. The callers pass results they already hold; this
module computes no Step:

- the ``/assess`` monorepo roll-up (``lib.unit_rollup``): the units of one repository;
- the fleet unit rows (``lib.fleet.fleet_data_units``): the units of one repository;
- the fleet repositories (``repo_learn_from``, from ``lib.fleet.fleet_data``): every measured
  repository of the run. A repository has no ecosystem or kind in ``fleet-data.json``, so
  its siblings are listed by key;
- ``report.md``'s Opportunities section (``lib.fleet.fleet_opportunities_section``) renders
  both fleet lists as written in ``fleet-data.json``, so it carries the file's privacy.

- **Which gate.** The unit's first blocking gate of its next Step, and only when it reads
  ``unmet`` with no owner disposition. A gate that is not measured, or one the owner
  excluded, is not a change to make, as ``lib.unit_rollup.rank_unlocks`` reads it.
- **Who holds it.** A sibling holds a gate when every condition the gate covers (its row's
  conditions up to the gate's level, as the blocked unit's next Step defines it) reads
  ``met``. Not measured, excluded or absent never holds, so unknown is never read as held.
- **Order.** Up to ``MAX_NAMES`` holders: the same ecosystem (language) and the same kind
  first, then the same ecosystem, then the same kind, then the rest; ties by name, so the
  line is the same for the same input. An unknown ecosystem or kind matches nothing.
- **Privacy.** This module returns names; the callers decide where they may be written.
  The text lines are dropped in counts-only output, and the structured names are written
  in clear in ``--full`` only, hashed under ``--hash-paths`` and dropped otherwise.

Tests: ``tests/test_learn_from.py``.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from lib.scoring.levels import MaturityModel

MAX_NAMES = 3
LABEL = "Learn from"


class Sibling(NamedTuple):
    """One unit as a candidate: its name, kind, ecosystem and condition states by id."""

    name: str
    kind: str | None
    ecosystem: str | None
    states: Mapping[str, str]


class NextGate(NamedTuple):
    """The gate a unit is blocked on: its id and the condition ids it covers."""

    gate: str
    conditions: tuple[str, ...]


def condition_states(conditions: Iterable[Any]) -> dict[str, str]:
    """``{id: state}`` over a level result's (or a ``unit_levels`` unit's) conditions."""
    return {str(c["id"]): str(c.get("state", "")) for c in conditions
            if isinstance(c, Mapping) and "id" in c}


def next_gate(next_step: Any, model: MaturityModel) -> NextGate | None:
    """The first blocking gate of ``next_step`` (a ``NextStep``) when it is a change to make:
    unmet and not excluded by the owner. None at the top Step or for any other blocker."""
    if not isinstance(next_step, Mapping):
        return None
    gates = [b for b in next_step.get("blockers") or [] if isinstance(b, Mapping) and b.get("kind") == "gate"]
    if not gates or gates[0].get("state") != "unmet" or gates[0].get("disposition"):
        return None
    gate = str(gates[0].get("gate", ""))
    for step in model.get("steps", []):
        if step["step"] != next_step.get("step"):
            continue
        for g in step["gates"]:
            if g["id"] == gate:
                return NextGate(gate, tuple(f"{g['row']}.{n}" for n in range(1, g["level"] + 1)))
    return None


def holds(states: Mapping[str, str], gate: NextGate) -> bool:
    """Whether every condition the gate covers reads ``met``."""
    return bool(gate.conditions) and all(states.get(c) == "met" for c in gate.conditions)


def learn_from(me: Sibling, gate: NextGate, siblings: Sequence[Sibling]) -> tuple[list[str], int]:
    """The names of up to ``MAX_NAMES`` siblings that hold ``gate``, best match first, and
    how many siblings hold it in all."""
    holders = [s for s in siblings if s.name != me.name and holds(s.states, gate)]

    def rank(s: Sibling) -> tuple[bool, bool, str]:
        same_eco = me.ecosystem is not None and s.ecosystem == me.ecosystem
        same_kind = me.kind is not None and s.kind == me.kind
        return (not same_eco, not same_kind, s.name)

    return [s.name for s in sorted(holders, key=rank)[:MAX_NAMES]], len(holders)


def line(names: Sequence[str]) -> str:
    """The user-facing line: ``Learn from: <unit>, <unit>``."""
    return f"{LABEL}: {', '.join(names)}"


def repo_learn_from(results: Mapping[str, Any], model: MaturityModel | None) -> dict[str, list[str]]:
    """Per repository id, the measured repositories of the run that hold its next gate;
    ``results`` maps each id to its level result (None when not measured)."""
    if model is None:
        return {}
    measured = {repo: r for repo, r in results.items() if isinstance(r, Mapping)}
    siblings = [Sibling(repo, None, None, condition_states(r.get("conditions") or []))
                for repo, r in sorted(measured.items())]
    out: dict[str, list[str]] = {}
    for me in siblings:
        gate = next_gate(measured[me.name].get("next_step"), model)
        out[me.name] = learn_from(me, gate, siblings)[0] if gate is not None else []
    return out


def ecosystems(units_block: Any) -> dict[str, str]:
    """``{path: ecosystem}`` from a ``units`` block (``lib.unit_discovery``); empty when absent."""
    entries = units_block.get("units") if isinstance(units_block, Mapping) else None
    return {str(u["path"]): str(u["ecosystem"]) for u in entries or []
            if isinstance(u, Mapping) and isinstance(u.get("path"), str) and isinstance(u.get("ecosystem"), str)}
