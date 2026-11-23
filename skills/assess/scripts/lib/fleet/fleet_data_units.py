"""The monorepo unit rows of ``fleet-data.json``: each repository's units, one row each (issue #866, parent #800).

A fleet run with ``--monorepo-units`` passes ``assess_signals.py --units``, so each scanned row carries
the ``unit_readings`` block (``lib.unit_readings``, #863): every unit's own readings of the
scoped scans. ``unit_rows`` turns that block into the rows a repository expands into on the
fleet page, under the run's own maturity model, as ``level.json`` and the repository's Step
fields are computed from the row (``lib.fleet.fleet_level.repo_level``):

- ``key``: the unit's folder. The scan writes it in clear only in ``--full`` output;
  counts-only drops it, and ``--hash-paths`` replaces it with its salted hash, the join key
  of a unit across runs (class ``hashed_path``). So ``key`` is null in plain counts-only;
- ``kind``: the unit's kind token (#1098): ``skill`` for a skill folder, ``agents`` and
  ``commands`` for a plugin's components, ``package`` for a build-manifest unit; null from a
  stored reading written before units had a kind. A marketplace repository's rows are its
  skills, so an estate of them reads skill by skill;
- ``step``: the unit's Step, from ``lib.unit_levels.unit_result`` (the unit's readings
  overlaid on the row's scans, the repository's owner exclusions applied), the function
  ``/assess`` runs for its ``unit_levels`` block, so one commit gives one Step in both;
- ``next_step``: the Step above, null at the top Step or under a model with no Steps;
- ``next_blocker``: the first gate of that Step that does not hold, as tokens (``gate``,
  ``state``, ``disposition``, as ``gate_blockers`` writes them, #945), null when no gate
  blocks it;
- ``tags``: the unit's per-tag tally, the headline strip (``lib.headline.tag_cells``) over the
  unit's level result, the shape of the repository's ``tags`` (#1073);
- ``learn_from``: up to three sibling units of the same repository that already hold the
  unit's next blocking gate (#1155, ``lib.fleet.fleet_learn_from``), the same ecosystem and
  kind first, by their folder. Empty when no sibling holds it or the gate is not one to make
  (not measured, owner-excluded, none). Written like ``key``: clear in ``--full``, hashed under
  ``--hash-paths``, null in plain counts-only, so a published file names no unit.

No row carries a condition tally (``tally``, "N of M measured"): the unit Steps, their next
blockers and the per-tag counts are the comparison inside one repository (#800 decision 3).
``unit_rows`` is null when the repository was not measured by unit: its scans carry no
available ``unit_readings`` block (no ``--monorepo-units`` or ``--scans unit_readings``, a
failed scan, an unavailable block) or the run has no model. Any row whose scans carry the
block gets rows, a re-score of stored rows included. Null is not measured, never an empty
list; a measured repository with no unit lists none.

Every value is a Step, a count, a token or the unit's hashed folder, so a counts-only file
names no unit (``FLEET_DATA_RULES``). Tests: ``tests/test_fleet_data_units.py``.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

from lib.fleet.fleet_data_steps import GateBlocker
from lib.fleet.fleet_learn_from import Sibling, condition_states, ecosystems, learn_from, next_gate
from lib.headline import tag_cells
from lib.level_result_types import LevelResult, TagCell
from lib.levels import MaturityModel
from lib.unit_components import KINDS, Kind
from lib.unit_levels import unit_result

READINGS = "unit_readings"


class UnitRow(TypedDict):
    """One monorepo unit of a repository (#866)."""

    key: str | None  # the unit's folder: clear in --full, hashed under --hash-paths, else None
    kind: Kind | None  # package, skill, agents or commands (#1098); None from a reading without one
    step: int | None  # None: the model defines no Steps
    next_step: int | None  # the Step above; None at the top Step
    next_blocker: GateBlocker | None  # the first gate of next_step that does not hold
    tags: list[TagCell]  # the headline's per-tag strip over the unit's result
    learn_from: list[str] | None  # #1155: siblings holding next_blocker; None where names are not written


def next_blocker(result: LevelResult) -> GateBlocker | None:
    """The first gate of the result's next Step that does not hold, as tokens."""
    nxt = result["next_step"]
    for b in nxt["blockers"] if nxt is not None else []:
        if b["kind"] == "gate":
            return {"gate": b.get("gate", ""), "state": b.get("state", ""),
                    "disposition": b.get("disposition")}
    return None


def unit_row(reading: Mapping[str, Any], scans: Mapping[str, Any], model: MaturityModel) -> UnitRow:
    """One unit's row: its reading overlaid on the repository's scans (see the module docstring)."""
    return _row(reading, unit_result(reading, scans, model), model)


def _row(reading: Mapping[str, Any], result: LevelResult, model: MaturityModel) -> UnitRow:
    path, kind = reading.get("path"), reading.get("kind")
    nxt = result["next_step"]
    return {"key": path if isinstance(path, str) else None,
            "kind": kind if kind in KINDS else None, "step": result["step"],
            "next_step": nxt["step"] if nxt is not None else None,
            "next_blocker": next_blocker(result), "tags": tag_cells(result, model), "learn_from": []}


def _learn_from(rows: list[UnitRow], results: list[LevelResult], scans: Mapping[str, Any],
                model: MaturityModel) -> None:
    """Fill each row's ``learn_from`` from its siblings' results (#1155)."""
    eco = ecosystems(scans.get("units"))
    siblings = [Sibling(row["key"], row["kind"], eco.get(row["key"]), condition_states(result["conditions"]))
                for row, result in zip(rows, results) if row["key"] is not None]
    for row, result in zip(rows, results):
        gate = next_gate(result["next_step"], model)
        if gate is not None and row["key"] is not None:
            me = Sibling(row["key"], row["kind"], eco.get(row["key"]), {})
            row["learn_from"] = learn_from(me, gate, siblings)[0]


def unit_rows(model: MaturityModel | None, scans: Mapping[str, Any] | None) -> list[UnitRow] | None:
    """A repository's unit rows from its scans; None when it was not measured by unit."""
    block = (scans or {}).get(READINGS)
    if model is None or not isinstance(block, Mapping) or block.get("available") is not True:
        return None
    units = block.get("units")
    if not isinstance(units, list):
        return None
    readings = [u for u in units if isinstance(u, Mapping)]
    results = [unit_result(u, scans or {}, model) for u in readings]
    rows = [_row(u, r, model) for u, r in zip(readings, results)]
    _learn_from(rows, results, scans or {}, model)
    return rows
