"""Each monorepo unit's Step, verified tally and not-measured count, inherited conditions labelled (#864, #800 step 3).

``lib.unit_readings`` (#863) gives every unit of the ``units`` block its own readings of
the scope-sensitive scans and tags each condition of the default maturity model
``unit`` or ``inherited``. This module runs the #744 engine once per unit: it overlays
the unit's own ``blocks`` on the repository-wide model blocks the ``level`` entry reads
(``level_tally.LEVEL_READS``) and calls ``level_tally.level_result`` with the owner's
exclusions, so a unit's result is the repository's result with its own readings in
place. An inherited condition (root CI, review, agent settings, the root instruction
file, a parent build's coverage gate) counts toward the unit's tally and its Step gates
as #800 decision 2 says, and is labelled ``inherited`` on the unit's condition.

**The block** (``unit_levels``, core-only, after ``unit_readings``):

- ``available``: false when ``unit_readings`` is absent or unavailable, with no units;
- ``unit_count`` and ``unit_count_reason``: copied from ``unit_readings``, so a null
  count stays null (#1030) and is never turned into the number of units listed;
- ``levels_version``: the model the units were scored against;
- ``units``: per unit, in ``unit_readings`` order:

  - ``path``: the unit's folder (``.`` for the root unit);
  - ``step`` and ``next_step``: as in the ``level`` block (``LevelResult``);
  - ``tally``: ``met``, ``measured`` and ``not_measured`` as in the ``level`` block, plus
    ``verified``, the met counted conditions graded ``verified``
    (``headline.verified_count``, the headline's V);
  - ``conditions``: every model condition with its ``state``, ``counted``, ``origin``
    (``unit`` or ``inherited``, from ``unit_readings``; a condition ``unit_readings`` did
    not tag reads ``inherited``, the repository-wide reading), its ``reason`` when it has
    one, and its ``why`` as the ``level`` block carries it (#939);
  - ``inherited``: the ids of the conditions met on an inherited reading, which the
    report labels "inherited from root". It lists listed (uncounted) conditions too, such
    as the declared instruction file, since they hold Step gates; ``counted`` on the
    condition says which of them count toward ``tally.met``.

Unknown stays unknown: a unit's own reading that is unavailable reads ``not_measured``
with its reason code, as in the ``level`` block, never unmet. Per-unit CI is not read
yet (``unit_readings``), so the CI conditions always read ``inherited``.

Every value is a count, a boolean, a model id, a state, an origin or a reason code;
counts-only output drops the unit paths (``checks/core/unit_levels.toml``). The roll-up
across units is #865. Tests: ``tests/test_unit_levels.py``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NotRequired, TypedDict

from lib.headline import verified_count
from lib.level_result_types import NextStep
from lib.level_tally import LEVEL_READS, default_model, level_result, run_exclusions
from lib.levels import MaturityModel
from lib.unit_readings import Origin

SECTION = "unit_levels"
READS = ("unit_readings", *LEVEL_READS)


class UnitTally(TypedDict):
    """A unit's tally: the ``level`` block's three counts and the verified share of ``met``."""

    met: int
    measured: int
    not_measured: int
    verified: int


class UnitConditionLevel(TypedDict):
    """One model condition of a unit: its state and where its reading comes from."""

    id: str
    state: str
    counted: bool
    origin: Origin
    reason: NotRequired[str]
    why: NotRequired[str]  # the level block's why (#939), for the report


class UnitLevel(TypedDict):
    """One unit's Step and tally."""

    path: str
    step: int | None
    next_step: NextStep | None
    tally: UnitTally
    conditions: list[UnitConditionLevel]
    inherited: list[str]


class UnitLevelsBlock(TypedDict):
    """The run context's ``unit_levels`` block (#864)."""

    available: bool
    unit_count: int | None
    unit_count_reason: str | None
    levels_version: str
    units: list[UnitLevel]


def unit_level(path: str, reading: Mapping[str, Any], repo: Mapping[str, Any],
               model: MaturityModel) -> UnitLevel:
    """One unit's result: ``reading`` (a ``unit_readings`` unit) over the ``repo`` blocks."""
    own = reading.get("blocks")
    data = {**repo, **(own if isinstance(own, Mapping) else {})}
    result = level_result(model, data, run_exclusions(repo))
    tags = reading.get("conditions")
    origins = {c.get("id"): c.get("origin") for c in tags if isinstance(c, Mapping)} \
        if isinstance(tags, list) else {}
    conditions: list[UnitConditionLevel] = []
    for c in result["conditions"]:
        entry: UnitConditionLevel = {"id": c["id"], "state": c["state"], "counted": c["counted"],
                                     "origin": "unit" if origins.get(c["id"]) == "unit" else "inherited"}
        if "reason" in c:
            entry["reason"] = c["reason"]
        if "why" in c:
            entry["why"] = c["why"]
        conditions.append(entry)
    verified = verified_count(result["conditions"])
    return {"path": path, "step": result["step"], "next_step": result["next_step"],
            "tally": {**result["tally"], "verified": verified},
            "conditions": conditions,
            "inherited": [c["id"] for c in conditions
                          if c["origin"] == "inherited" and c["state"] == "met"]}


def unit_levels(readings: Any, *blocks: Any, model: MaturityModel | None = None) -> UnitLevelsBlock:
    """The ``unit_levels`` block: ``readings`` is the ``unit_readings`` block and ``blocks``
    the values of ``level_tally.LEVEL_READS``, in order (see the module docstring)."""
    model = model if model is not None else default_model()
    if not isinstance(readings, Mapping) or readings.get("available") is not True:
        return {"available": False, "unit_count": None, "unit_count_reason": "units_unavailable",
                "levels_version": model["version"], "units": []}
    repo = dict(zip(LEVEL_READS, blocks))
    units: Sequence[Any] = readings.get("units") or []
    out = [unit_level(str(u.get("path")), u, repo, model)
           for u in units if isinstance(u, Mapping) and u.get("path")]
    return {"available": True, "unit_count": readings.get("unit_count"),
            "unit_count_reason": readings.get("unit_count_reason"),
            "levels_version": model["version"], "units": out}
