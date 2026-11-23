"""The repository roll-up over monorepo units and the unit-unlock ranking (#865, #800 step 4).

``lib.unit_levels`` (#864) gives every unit its own Step and labels each condition ``unit``
or ``inherited``. This module rolls those results up for the repository, as #800 decision 3
and the #772 decision (option A) ask: the lowest unit's Step plus "N of M measured", never
a mean and never a unit named. The repository's own ``level`` and ``headline`` blocks are
untouched, so a single-unit repository reads exactly as before.

**The block** (``unit_rollup``, core-only, after ``unit_levels``):

- ``available``: false when ``unit_levels`` is absent or unavailable;
- ``unit_count`` and ``unit_count_reason``: copied from ``unit_levels``, so a null count
  stays null (#1030);
- ``units``: the units scored; ``measured``: those with a Step. An unmeasured unit counts
  in ``units`` only, never as Step 1;
- ``lowest_step``: the lowest Step a measured unit holds, null when none is measured;
- ``next_step``: the Step above it (null at the top Step or when none is measured) and
  ``at_next_step``: the measured units already at or above it;
- ``unlocks``: the changes that would move units to their own next Step, ranked (#747).
  Each is a Step gate whose condition reads ``unmet`` in at least one unit (a gate that is
  not measured or that the owner excluded is no change to make). ``scope`` is
  ``repo_wide`` when every gate condition not yet met in the unit rests on the
  repository-wide reading (origin ``inherited``; a gate holds only when all of them are met,
  so one with origin ``unit`` makes the change unit-local, and a gate with no condition read
  reads ``unit``): one change at the root reaches all those units, so it counts once
  (``changes`` 1), never once per unit. ``unit`` means each unit needs its own change
  (``changes`` is the number of units). ``held`` counts the units the gate holds back and
  ``lifted`` those for which it is the last blocker: their only blocking gate, with the
  threshold reached once its counted conditions are met. ``lift_per_change`` is the most
  units one change of the entry lifts (``lifted`` for ``repo_wide``, at most 1 for
  ``unit``). The ranking is by ``lift_per_change``, then repository-wide first, then
  ``held``, then model gate order, so a repository-wide change that lifts many units leads;
- ``lines`` and ``markdown``: the roll-up line ("Units: Step 1 (lowest unit) · 2 of 3
  measured units at Step 2") and, when a change lifts a unit, the next-unlock line naming
  the top-ranked change. Empty for fewer than two units, so a single-unit repository's
  report is unchanged.

Every value is a count, a Step, a gate id or a token; counts-only output drops the text
lines (``checks/core/unit_rollup.toml``) and the block names no unit. Tests:
``tests/test_unit_rollup.py``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, TypedDict

from lib.headline import GATE_CHANGE
from lib.level_tally import default_model
from lib.levels import MaturityModel, StepDef

SECTION = "unit_rollup"
READS = ("unit_levels",)

Scope = Literal["repo_wide", "unit"]


class UnitUnlock(TypedDict):
    """One change ranked by the units it moves to their next Step."""

    gate: str
    scope: Scope
    changes: int
    held: int
    lifted: int
    lift_per_change: int


class UnitRollUpBlock(TypedDict):
    """The run context's ``unit_rollup`` block (#865)."""

    available: bool
    unit_count: int | None
    unit_count_reason: str | None
    units: int
    measured: int
    lowest_step: int | None
    next_step: int | None
    at_next_step: int | None
    unlocks: list[UnitUnlock]
    lines: list[str]
    markdown: str


def _gate_conditions(step: StepDef | None, gate: str) -> list[str]:
    """The condition ids a gate of ``step`` covers: its row's conditions up to its level."""
    for g in (step["gates"] if step else []):
        if g["id"] == gate:
            return [f"{g['row']}.{n}" for n in range(1, g["level"] + 1)]
    return []


def _unit_unlocks(unit: Mapping[str, Any], steps: Mapping[int, StepDef]) -> list[tuple[str, Scope, bool]]:
    """Each unmet gate of the unit's next Step: its id, its scope and whether it alone lifts the unit."""
    nxt = unit.get("next_step")
    if not isinstance(nxt, Mapping):
        return []
    number = nxt.get("step")
    step = steps.get(number) if isinstance(number, int) else None
    blockers = [b for b in nxt.get("blockers") or [] if isinstance(b, Mapping)]
    gates = [b for b in blockers if b.get("kind") == "gate"]
    threshold = next((b for b in blockers if b.get("kind") == "threshold"), None)
    by_id = {c.get("id"): c for c in unit.get("conditions") or [] if isinstance(c, Mapping)}
    out: list[tuple[str, Scope, bool]] = []
    for b in gates:
        if b.get("state") != "unmet" or b.get("disposition"):
            continue
        gate = str(b.get("gate", ""))
        waiting = [by_id[c] for c in _gate_conditions(step, gate)
                   if c in by_id and by_id[c].get("state") != "met"]
        # no condition read behind the gate: never claim a change at the root
        scope: Scope = "repo_wide" if waiting and all(c.get("origin") == "inherited" for c in waiting) \
            else "unit"
        gain = sum(1 for c in waiting if c.get("counted"))
        short = (threshold.get("threshold", 0) - threshold.get("met", 0) - gain) if threshold else 0
        out.append((gate, scope, len(gates) == 1 and short <= 0))
    return out


def _gate_order(model: MaturityModel) -> dict[str, int]:
    order: dict[str, int] = {}
    for step in model.get("steps", []):
        for g in step["gates"]:
            order.setdefault(g["id"], len(order))
    return order


def rank_unlocks(units: Sequence[Mapping[str, Any]], model: MaturityModel) -> list[UnitUnlock]:
    """The changes that move units to their next Step, ranked (see the module docstring)."""
    steps = {s["step"]: s for s in model.get("steps", [])}
    found: dict[tuple[str, Scope], UnitUnlock] = {}
    for unit in units:
        for gate, scope, lifts in _unit_unlocks(unit, steps):
            entry = found.setdefault((gate, scope), {"gate": gate, "scope": scope, "changes": 0,
                                                     "held": 0, "lifted": 0, "lift_per_change": 0})
            entry["held"] += 1
            entry["lifted"] += lifts
            entry["changes"] = 1 if scope == "repo_wide" else entry["held"]
    for entry in found.values():
        entry["lift_per_change"] = entry["lifted"] if entry["scope"] == "repo_wide" else min(entry["lifted"], 1)
    order = _gate_order(model)
    return sorted(found.values(), key=lambda e: (-e["lift_per_change"], e["scope"] != "repo_wide",
                                                 -e["held"], order.get(e["gate"], len(order)), e["gate"]))


def _lines(block: UnitRollUpBlock) -> list[str]:
    if block["units"] < 2:
        return []
    lowest, measured = block["lowest_step"], block["measured"]
    if lowest is None:
        return [f"Units: Step not measured for any of {block['units']} units"]
    if block["next_step"] is None:
        line = f"Units: Step {lowest} (lowest unit) · {measured} of {measured} measured units at the top Step"
    else:
        line = (f"Units: Step {lowest} (lowest unit) · {block['at_next_step']} of {measured} "
                f"measured units at Step {block['next_step']}")
    if block["units"] > measured:
        line += f" · {block['units'] - measured} not measured"
    lines = [line]
    top = block["unlocks"][0] if block["unlocks"] else None
    if top is not None and top["lift_per_change"] > 0:
        change = GATE_CHANGE.get(top["gate"], f"the {top['gate']} gate holds")
        where = "once at the root" if top["scope"] == "repo_wide" else "in one unit"
        noun = "unit to its" if top["lift_per_change"] == 1 else "units to their"
        lines.append(f"Next unlock across units: {change}, {where}, moves "
                     f"{top['lift_per_change']} {noun} next Step")
    return lines


def unit_rollup(levels: Any, model: MaturityModel | None = None) -> UnitRollUpBlock:
    """The ``unit_rollup`` block over the ``unit_levels`` block (see the module docstring)."""
    model = model if model is not None else default_model()
    if not isinstance(levels, Mapping) or levels.get("available") is not True:
        return {"available": False, "unit_count": None, "unit_count_reason": "units_unavailable",
                "units": 0, "measured": 0, "lowest_step": None, "next_step": None,
                "at_next_step": None, "unlocks": [], "lines": [], "markdown": ""}
    units = [u for u in levels.get("units") or [] if isinstance(u, Mapping)]
    held = [u["step"] for u in units if isinstance(u.get("step"), int)]
    lowest = min(held) if held else None
    known = {s["step"] for s in model.get("steps", [])}
    nxt = lowest + 1 if lowest is not None and lowest + 1 in known else None
    block: UnitRollUpBlock = {
        "available": True, "unit_count": levels.get("unit_count"),
        "unit_count_reason": levels.get("unit_count_reason"),
        "units": len(units), "measured": len(held), "lowest_step": lowest, "next_step": nxt,
        "at_next_step": sum(s >= nxt for s in held) if nxt is not None else None,
        "unlocks": rank_unlocks(units, model), "lines": [], "markdown": ""}
    block["lines"] = _lines(block)
    block["markdown"] = "\n\n".join(block["lines"])
    return block
