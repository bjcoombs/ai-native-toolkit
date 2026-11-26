"""Step targets for the achievements engine: a unit means to reach an autonomy Step (issue #945, parent #705).

A target names a view, a unit and a ``step`` of the maturity model's ``[[steps]]`` above
Step 1 (#705 decision: "a target names a Step"). The targets file is read by
``lib/fleet/fleet_targets.py``; this module holds the engine's side, which reads only
``fleet-data.json``.

**A subject's Step state** in one run (``step_state``): over its members (the
repositories at or below the unit; a repository is its own member), ``measured`` is the
set with a Step (``fleet-data.json`` ``step``, #944) and ``lowest`` the lowest Step among
them; None when none is measured. An unmeasured repository neither earns nor blocks. A
stored run written before #944 has no ``step``: every member reads unmeasured, so a move
from or to it is neutral.

**Hit** (``hit``): a clean move (both runs have a lowest Step and the measured set is
identical, as ``fleet_achievements.clean`` asks of a row) from below the target to at or
above it. ``fleet_achievements`` emits it as ``target_hit``, comparing each run with the
latest earlier run whose measured set is the same (``fleet_achievements.same_set``): a
member that fails its scan on the run before the crossing delays the comparison to the
last fully comparable run instead of losing the hit.

**Progress** (``target_progress``, this run only): the unit's ``lowest_step``, ``measured``
of ``repos``, ``distance`` (Steps to go, 0 when met, None when nothing is measured), the
``held`` (measured repositories below the target Step) and
``missing_gates``: each gate of the target Step that holds back at least one measured
repository, in model order, with ``held`` and, of those, ``not_measured`` and ``excluded``
(``lib.fleet.fleet_data_steps.step_hold``, over each repository's ``gate_blockers``). The counts
are repositories, not practices: a Step reads its gates only (#1083), so the missing gates
are the whole of what a repository still needs.

Deterministic: targets sorted by view, unit and Step.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple, TypedDict, cast

from lib.fleet.fleet_data_steps import GateBlocker, GateHold, RepoSteps, step_hold
from lib.scoring.level_result_types import Tally
from lib.scoring.levels import MaturityModel, StepDef


class Target(NamedTuple):
    """One Step target, keyed as ``fleet-data.json`` keys the unit."""

    view: str
    unit: str  # key as fleet-data.json writes it (hashed under the run's salt when hashing)
    step: int  # a [[steps]] entry above Step 1


def target_order(t: Target) -> tuple[str, str, int]:
    return (t.view, t.unit, t.step)


@dataclass(frozen=True)
class StepState:
    lowest: int | None
    measured: frozenset[str]


class TargetProgress(TypedDict):
    view: str
    unit: str
    step: int  # the target Step
    lowest_step: int | None  # None when no member is measured this run
    measured: int  # members with a Step this run
    repos: int  # every member
    distance: int | None  # Steps to go; 0 when met; None when nothing is measured
    held: int  # measured members below the target Step
    missing_gates: list[GateHold]  # the target Step's gates holding back a measured member


def member_steps(repo: Mapping[str, Any]) -> RepoSteps:
    """A repository's Step fields as the engine reads them; unmeasured when written before #944."""
    step, tally, blockers = repo.get("step"), repo.get("tally"), repo.get("gate_blockers")
    return {"step": step if type(step) is int else None, "next_step": None,
            "tally": cast(Tally, tally) if isinstance(tally, dict) else None,
            "exclusions": [], "gate_blockers": cast(list[GateBlocker], blockers) if isinstance(blockers, list) else [],
            "in_place": []}


def step_state(members: Sequence[str], steps: Mapping[str, RepoSteps]) -> StepState:
    known = {m: s["step"] for m in members if m in steps and (s := steps[m])["step"] is not None}
    return StepState(min(v for v in known.values() if v is not None) if known else None, frozenset(known))


def hit(target: Target, then: StepState | None, now: StepState) -> bool:
    """Whether a clean move took the subject from below ``target`` to at or above it."""
    if then is None or then.lowest is None or now.lowest is None or not now.measured \
            or then.measured != now.measured:
        return False
    return then.lowest < target.step <= now.lowest


def step_defs(model: MaturityModel | None) -> dict[int, StepDef]:
    return {s["step"]: s for s in (model.get("steps", []) if model is not None else [])}


def target_progress(members: Mapping[tuple[str, str], Sequence[str]], steps: Mapping[str, RepoSteps],
                    targets: Sequence[Target], model: MaturityModel | None) -> list[TargetProgress]:
    """Each target's progress this run (see the module docstring); ``members`` by ``(view, key)``."""
    defs = step_defs(model)
    out: list[TargetProgress] = []
    for t in sorted(targets, key=target_order):
        mine = members.get((t.view, t.unit), [])
        state = step_state(mine, steps)
        definition = defs[t.step]
        hold = step_hold(definition, [steps[m] for m in mine if m in steps])
        out.append({"view": t.view, "unit": t.unit, "step": t.step, "lowest_step": state.lowest,
                    "measured": len(state.measured), "repos": len(mine),
                    "distance": None if state.lowest is None else max(0, t.step - state.lowest),
                    "held": hold["held"],
                    "missing_gates": [g for g in hold["gates"] if g["held"] > 0]})
    return out
