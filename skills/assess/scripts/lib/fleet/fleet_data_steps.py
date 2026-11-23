"""The Step parts of ``fleet-data.json``: per repository and per unit (issue #944, parent #705).

**Per repository** (``repo_steps``): ``step``, ``next_step`` and ``tally`` copied from the
repository's level result (``lib.fleet.fleet_level.repo_level``, the function ``level.json``
is written from), so the two files agree for the same commit by construction; all three
are null for a failed repository or a run without a maturity model. ``exclusions`` lists
the conditions the owner excluded in the repository's ``.assess/config.toml`` (#910), in
the order the level result lists them: ``id``, ``kind``, ``source`` and the owner's
``reason``. They are listed, never counted. The reason is the owner's own words, so
``lib.fleet.fleet_data`` writes it only in ``--full`` output (``FLEET_DATA_RULES``: drop); a
counts-only scan has already dropped it. ``gate_blockers`` lists every gate of the model's
Steps that does not hold (``level_tally.gate_blockers``: once per gate id, in Step order),
each as tokens only: its ``gate`` id, the blocking condition's ``state`` and its
``disposition`` (``excluded_by_owner``, else null), so a Step target above the repository's next Step
reads which of its gates are missing (#945); empty when every gate holds or not measured.

**Per unit** (``step_roll_up``), over every repository below the unit (#772 option A: no
average and no repository named):

- ``repos``: every repository below; ``measured``: those with a Step (scanned, under a
  model with ``[[steps]]``). An unmeasured repository counts in ``repos`` only, never as
  Step 1;
- ``lowest_step``: the lowest Step a measured repository holds; null when none is measured;
- ``next_step``: the Step above ``lowest_step``, null at the top Step or when none is
  measured. ``held`` is the measured repositories below it (a repository already at or
  above it is held back by nothing). Each gate of that Step,
  in model order and with zero counts kept, gives ``held``: the repositories the gate holds
  back, and of those ``not_measured`` (the gate's condition could not be measured) and
  ``excluded`` (the owner excluded it, so the Step stays unreached while the exclusion
  stands). A gate condition that is ``unmet``, or ``not_applicable`` through a
  ``requires:<id>`` on an excluded condition, counts in ``held`` and in neither
  sub-count, so ``held`` minus the two is not always "unmet". A Step reads its gates only
  (#1083), so the gates are the whole of what holds a repository back.

Exclusions apply where the fleet's model shares condition ids with the default model:
``.assess/config.toml`` entries are checked against the default model, and the engine
ignores an id the ``--levels`` model does not define.

``step_hold`` gives the same counts for any Step (a Step target, #945); the roll-up's
``next_step`` is ``step_hold`` of the Step above ``lowest_step``.

Every count comes from the level results, so the page computes no Step.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from lib.level_result_types import LevelResult, NextStep, Tally
from lib.level_tally import EXCLUDED_BY_OWNER, gate_blockers
from lib.levels import StepDef, StepGate


class RepoExclusion(TypedDict):
    id: str  # a condition id of the maturity model
    kind: str  # lib.owner_exclusions.KINDS
    source: str  # "repo": the repository's .assess/config.toml
    reason: str | None  # the owner's words; None in counts-only output


class GateBlocker(TypedDict):
    """A Step gate that does not hold, as tokens only (#945)."""

    gate: str  # the gate id of the model's [[steps]]
    state: str  # the blocking condition's state (lib.level_tally.STATES)
    disposition: str | None  # "excluded_by_owner" when the owner excluded it, else None


class RepoSteps(TypedDict):
    step: int | None
    next_step: NextStep | None
    tally: Tally | None
    exclusions: list[RepoExclusion]
    gate_blockers: list[GateBlocker]  # every Step gate that does not hold, in model order (#945)


class GateHold(TypedDict):
    gate: str
    row: str
    level: int
    held: int  # measured repositories this gate holds back from the Step
    not_measured: int  # of those, the gate's condition was not measured
    excluded: int  # of those, the owner excluded the gate's condition


class StepHold(TypedDict):
    step: int
    held: int  # measured repositories below this Step
    gates: list[GateHold]


class StepRollUp(TypedDict):
    measured: int  # repositories below with a Step
    repos: int  # every repository below, measured or not
    lowest_step: int | None  # None when none was measured
    next_step: StepHold | None  # None at the top Step or when none was measured


NO_STEPS: RepoSteps = {"step": None, "next_step": None, "tally": None, "exclusions": [], "gate_blockers": []}


def _reasons(scans: Mapping[str, Any] | None) -> dict[str, str]:
    """The owner's reason per excluded condition id, as the scan wrote it (first entry per id)."""
    block = (scans or {}).get("owner_exclusions")
    entries = block.get("exclusions") if isinstance(block, Mapping) else None
    out: dict[str, str] = {}
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, Mapping) and isinstance(entry.get("id"), str) and isinstance(entry.get("reason"), str):
            out.setdefault(entry["id"], entry["reason"])
    return out


def repo_steps(result: LevelResult | None, scans: Mapping[str, Any] | None,
               steps: Sequence[StepDef] = ()) -> RepoSteps:
    """One repository's Step fields from its level result (None: not measured) under ``steps``,
    the model's ``[[steps]]``."""
    if result is None:
        return {**NO_STEPS, "exclusions": [], "gate_blockers": []}
    reasons = _reasons(scans)
    return {
        "step": result["step"],
        "next_step": result["next_step"],
        "tally": result["tally"],
        "exclusions": [{"id": e["id"], "kind": e["kind"], "source": e["source"], "reason": reasons.get(e["id"])}
                       for e in result.get("excluded", [])],
        "gate_blockers": [{"gate": b.get("gate", ""), "state": b.get("state", ""),
                           "disposition": b.get("disposition")} for b in gate_blockers(steps, result["conditions"])],
    }


def _gate_hold(gate: StepGate, held: Sequence[RepoSteps]) -> GateHold:
    blockers = [b for m in held for b in m["gate_blockers"] if b["gate"] == gate["id"]]
    return {"gate": gate["id"], "row": gate["row"], "level": gate["level"], "held": len(blockers),
            "not_measured": sum(b["state"] == "not_measured" for b in blockers),
            "excluded": sum(b["disposition"] == EXCLUDED_BY_OWNER for b in blockers)}


def step_roll_up(steps: Sequence[StepDef], members: Sequence[RepoSteps]) -> StepRollUp:
    """The Step roll-up over the repositories below a unit (see the module docstring)."""
    measured = [m for m in members if m["step"] is not None]
    lowest = min(m["step"] for m in measured if m["step"] is not None) if measured else None
    definition = next((s for s in steps if lowest is not None and s["step"] == lowest + 1), None)
    hold = step_hold(definition, members) if definition is not None else None
    return {"measured": len(measured), "repos": len(members), "lowest_step": lowest, "next_step": hold}


def step_hold(definition: StepDef, members: Sequence[RepoSteps]) -> StepHold:
    """How the measured repositories among ``members`` stand against one Step: those below it,
    and per gate how many it holds back."""
    held = [m for m in members if m["step"] is not None and m["step"] < definition["step"]]
    return {"step": definition["step"], "held": len(held),
            "gates": [_gate_hold(g, held) for g in definition["gates"]]}
