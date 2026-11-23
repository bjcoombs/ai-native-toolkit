"""The fleet targets file: the autonomy Step a unit means to reach, read by the achievements engine (issues #704, #945).

The stand-in for the fleet artifact's write-back (``targets`` rows, #692/#705) until
that exists. ``fleet.py --targets FILE`` reads it when given; an invalid file exits 2
before any scan. It is not part of the run stamp, so setting or changing a target never
stops two runs comparing. Format (TOML)::

    [[target]]
    view = "ownership"      # "ownership" (default), "system", or "repo"
    unit = "acme/payments"  # a unit id in that view, or a repository id for "repo"
    step = 3                # a [[steps]] entry of the maturity model above Step 1

A target names a Step (#705 decision), one per unit and view. ``parse_targets`` needs a
maturity model with ``[[steps]]`` (``--levels``) and raises ``TargetsError`` naming the
entry: on an unknown key or view, a ``step`` that is not a whole number from 2 to the top
Step, a second target on one unit, and on the keys of the retired format (``row``,
``level``, ``share``: #704 and #780 targets on a layer row, removed in #945), with a pointer
to ``step``. ``check_units`` refuses, before any scan, a unit that is not in the run's unit
trees (or a repository id that is not in the run for ``repo``).

``resolve`` turns each into a ``fleet_step_targets.Target`` keyed as ``fleet-data.json``
keys it (``<view>/<unit id>`` for a unit, the repository id for a repository, each hashed
under the run's salt when paths are hashed). The engine emits ``target_hit`` on the run the
unit's lowest Step crosses from below the target to at or above it, and reports each
target's progress (``lib/fleet_step_targets.py``).
"""
from __future__ import annotations

import tomllib
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from lib.fleet_achievements import REPO_VIEW
from lib.fleet_step_targets import Target
from lib.fleet_units import VIEWS, Tree
from lib.levels import MaturityModel

TARGET_VIEWS = (*VIEWS, REPO_VIEW)
_KEYS = frozenset({"view", "unit", "step"})
RETIRED_KEYS = ("row", "level", "share")  # the layer-row targets of #704 and #780, removed in #945


class TargetsError(ValueError):
    """The targets file is unusable; the message names the entry and the fault."""


class TargetSpec(NamedTuple):
    view: str
    unit: str  # as written in the file
    step: int


def parse_targets(data: Mapping[str, Any], model: MaturityModel | None) -> list[TargetSpec]:
    """Every ``[[target]]``; raises ``TargetsError`` on a retired or unknown key, view, step or duplicate."""
    extra = set(data) - {"target"}
    if extra:
        raise TargetsError(f"unknown top-level key(s): {', '.join(sorted(extra))}")
    entries = data.get("target", [])
    if not isinstance(entries, list):
        raise TargetsError("'target' must be an array of tables ([[target]])")
    top = max((s["step"] for s in (model.get("steps", []) if model is not None else [])), default=0)
    out: list[TargetSpec] = []
    for i, entry in enumerate(entries, 1):
        where = f"target {i}"
        if not isinstance(entry, dict):
            raise TargetsError(f"{where}: must be a table")
        retired = [k for k in RETIRED_KEYS if k in entry]
        if retired:
            raise TargetsError(f"{where}: {', '.join(repr(k) for k in retired)} no longer set a target; "
                               "a target names a Step: step = 2 to the top Step of the maturity model")
        unknown = set(entry) - _KEYS
        if unknown:
            raise TargetsError(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")
        view, unit, step = entry.get("view", VIEWS[0]), entry.get("unit"), entry.get("step")
        if view not in TARGET_VIEWS:
            raise TargetsError(f"{where}: view must be one of {', '.join(TARGET_VIEWS)}")
        if not isinstance(unit, str) or not unit.strip():
            raise TargetsError(f"{where}: 'unit' must be a non-empty string")
        if top < 2:
            raise TargetsError(f"{where}: a Step target needs a maturity model with [[steps]] above Step 1 "
                               "(--levels)")
        if type(step) is not int or not 2 <= step <= top:
            raise TargetsError(f"{where}: 'step' must be a whole number from 2 to {top} "
                               "(a [[steps]] entry of the maturity model above Step 1)")
        if any((s.view, s.unit) == (view, unit) for s in out):
            raise TargetsError(f"{where}: a second target for {view} {unit}; a unit has one Step target")
        out.append(TargetSpec(view, unit, step))
    return out


def load_targets(path: Path, model: MaturityModel | None) -> list[TargetSpec]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise TargetsError(f"cannot read {path}: {e}") from e
    return parse_targets(data, model)


def check_units(specs: Sequence[TargetSpec], trees: Sequence[Tree], repos: Collection[str]) -> None:
    """Raise ``TargetsError`` for a target whose unit is not in this run (before any scan)."""
    units = {tree["view"]: {u["id"] for u in tree["units"]} for tree in trees}
    for i, spec in enumerate(specs, 1):
        if spec.view == REPO_VIEW:
            if spec.unit not in repos:
                raise TargetsError(f"target {i}: repository {spec.unit!r} is not in this run")
        elif spec.unit not in units.get(spec.view, set()):
            raise TargetsError(f"target {i}: unit {spec.unit!r} is not a unit of the {spec.view} view in this run")


def resolve(specs: Sequence[TargetSpec], key: Callable[[str], str]) -> list[Target]:
    """Each spec keyed as ``fleet-data.json`` keys it; ``key`` is ``fleet_data.Privacy.key``."""
    return [Target(s.view, key(s.unit if s.view == REPO_VIEW else f"{s.view}/{s.unit}"), s.step) for s in specs]
