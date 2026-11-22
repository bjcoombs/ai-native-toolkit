"""The Step parts of ``fleet-data.json``: per repository and per unit (issue #944, parent #705).

**Per repository** (``repo_steps``): ``step``, ``next_step`` and ``tally`` copied from the
repository's level result (``lib.fleet_level.repo_level``, the function ``level.json``
is written from), so the two files agree for the same commit by construction; all three
are null for a failed repository or a run without a maturity model. ``exclusions`` lists
the conditions the owner excluded in the repository's ``.assess/config.toml`` (#910), in
the order the level result lists them: ``id``, ``kind``, ``source`` and the owner's
``reason``. They are listed, never counted. The reason is the owner's own words, so
``lib.fleet_data`` writes it only in ``--full`` output (``FLEET_DATA_RULES``: drop); a
counts-only scan has already dropped it.

**Per unit** (``step_roll_up``), over every repository below the unit (#772 option A: no
average and no repository named):

- ``repos``: every repository below; ``measured``: those with a Step (scanned, under a
  model with ``[[steps]]``). An unmeasured repository counts in ``repos`` only, never as
  Step 1;
- ``lowest_step``: the lowest Step a measured repository holds; null when none is measured;
- ``next_step``: the Step above ``lowest_step``, null at the top Step or when none is
  measured. ``held`` is the measured repositories below it (those whose own next Step it
  is; a repository already at or above it is held back by nothing). Each gate of that Step,
  in model order and with zero counts kept, gives ``held``: the repositories the gate holds
  back, and of those ``not_measured`` (the gate's condition could not be measured) and
  ``excluded`` (the owner excluded it, so the Step stays unreached while the exclusion
  stands). ``below_threshold`` counts the held repositories whose met tally is below the
  Step's ``threshold``.

Every count comes from the level results, so the page computes no Step.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from lib.level_result_types import LevelResult, NextStep, Tally
from lib.level_tally import EXCLUDED_BY_OWNER
from lib.levels import StepDef


class RepoExclusion(TypedDict):
    id: str  # a condition id of the maturity model
    kind: str  # lib.owner_exclusions.KINDS
    source: str  # "repo": the repository's .assess/config.toml
    reason: str | None  # the owner's words; None in counts-only output


class RepoSteps(TypedDict):
    step: int | None
    next_step: NextStep | None
    tally: Tally | None
    exclusions: list[RepoExclusion]


class GateHold(TypedDict):
    gate: str
    row: str
    level: int
    held: int  # measured repositories this gate holds back from the Step
    not_measured: int  # of those, the gate's condition was not measured
    excluded: int  # of those, the owner excluded the gate's condition


class StepHold(TypedDict):
    step: int
    threshold: int
    held: int  # measured repositories below this Step
    gates: list[GateHold]
    below_threshold: int  # of those, the met tally is below the threshold


class StepRollUp(TypedDict):
    measured: int  # repositories below with a Step
    repos: int  # every repository below, measured or not
    lowest_step: int | None  # None when none was measured
    next_step: StepHold | None  # None at the top Step or when none was measured


NO_STEPS: RepoSteps = {"step": None, "next_step": None, "tally": None, "exclusions": []}


def _reasons(scans: Mapping[str, Any] | None) -> dict[str, str]:
    """The owner's reason per excluded condition id, as the scan wrote it (first entry per id)."""
    block = (scans or {}).get("owner_exclusions")
    entries = block.get("exclusions") if isinstance(block, Mapping) else None
    out: dict[str, str] = {}
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, Mapping) and isinstance(entry.get("id"), str) and isinstance(entry.get("reason"), str):
            out.setdefault(entry["id"], entry["reason"])
    return out


def repo_steps(result: LevelResult | None, scans: Mapping[str, Any] | None) -> RepoSteps:
    """One repository's Step fields from its level result (None: not measured)."""
    if result is None:
        return {**NO_STEPS, "exclusions": []}
    reasons = _reasons(scans)
    return {
        "step": result["step"],
        "next_step": result["next_step"],
        "tally": result["tally"],
        "exclusions": [{"id": e["id"], "kind": e["kind"], "source": e["source"], "reason": reasons.get(e["id"])}
                       for e in result.get("excluded", [])],
    }


def _gate_hold(gate: Mapping[str, Any], held: Sequence[NextStep]) -> GateHold:
    blockers = [b for ns in held for b in ns["blockers"] if b["kind"] == "gate" and b.get("gate") == gate["id"]]
    return {"gate": gate["id"], "row": gate["row"], "level": gate["level"], "held": len(blockers),
            "not_measured": sum(b.get("state") == "not_measured" for b in blockers),
            "excluded": sum(b.get("disposition") == EXCLUDED_BY_OWNER for b in blockers)}


def step_roll_up(steps: Sequence[StepDef], members: Sequence[RepoSteps]) -> StepRollUp:
    """The Step roll-up over the repositories below a unit (see the module docstring)."""
    measured = [m for m in members if m["step"] is not None]
    lowest = min(m["step"] for m in measured if m["step"] is not None) if measured else None
    hold: StepHold | None = None
    definition = next((s for s in steps if lowest is not None and s["step"] == lowest + 1), None)
    if definition is not None:
        held = [m["next_step"] for m in measured
                if m["next_step"] is not None and m["next_step"]["step"] == definition["step"]]
        hold = {"step": definition["step"], "threshold": definition["threshold"], "held": len(held),
                "gates": [_gate_hold(g, held) for g in definition["gates"]],
                "below_threshold": sum(any(b["kind"] == "threshold" for b in ns["blockers"]) for ns in held)}
    return {"measured": len(measured), "repos": len(members), "lowest_step": lowest, "next_step": hold}
