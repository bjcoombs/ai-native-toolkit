"""The fleet targets file: levels or mean shares a unit means to reach, read by the achievements engine (issue #704).

The stand-in for the fleet artifact's write-back (``targets`` rows, #692/#705) until
that exists. ``fleet.py --targets FILE`` reads it when given; an invalid file exits 2
before any scan. It is not part of the run stamp, so setting or changing a target never
stops two runs comparing. Format (TOML)::

    [[target]]
    view = "ownership"      # "ownership" (default), "system", or "repo"
    unit = "acme/payments"  # a unit id in that view, or a repository id for "repo"
    row = "layer-5-ci"      # a team row of the maturity model
    level = 2               # a whole level, 1 to the row's top level
    # share = 0.5           # or instead: a mean share of top level on the row, in (0, 1]

Each target sets exactly one of ``level`` and ``share``.

``resolve`` turns each into a ``fleet_achievements.Target`` keyed as ``fleet-data.json``
keys it (``<view>/<unit id>`` for a unit, the repository id for a repository, each
hashed under the run's salt when paths are hashed). The engine emits ``target_hit`` on
the run a unit's level (or its mean share of top level) on the row crosses from below
the target to at or above it, and reports every target's distance: in levels for a
level target, in share for a share target.
"""
from __future__ import annotations

import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, NamedTuple

from lib.fleet_achievements import REPO_VIEW, Target
from lib.fleet_maturity import SCOPE
from lib.fleet_units import VIEWS
from lib.levels import MaturityModel

TARGET_VIEWS = (*VIEWS, REPO_VIEW)
_KEYS = frozenset({"view", "unit", "row", "level", "share"})


class TargetsError(ValueError):
    """The targets file is unusable; the message names the entry and the fault."""


class TargetSpec(NamedTuple):
    view: str
    unit: str  # as written in the file
    row: str
    level: int | None
    share: float | None = None


def parse_targets(data: Mapping[str, Any], model: MaturityModel | None) -> list[TargetSpec]:
    """Every ``[[target]]``; raises ``TargetsError`` on an unknown key, view, row or level."""
    extra = set(data) - {"target"}
    if extra:
        raise TargetsError(f"unknown top-level key(s): {', '.join(sorted(extra))}")
    entries = data.get("target", [])
    if not isinstance(entries, list):
        raise TargetsError("'target' must be an array of tables ([[target]])")
    tops = {row["id"]: len(row["levels"]) for row in (model["rows"] if model else []) if row["scope"] == SCOPE}
    out: list[TargetSpec] = []
    for i, entry in enumerate(entries, 1):
        where = f"target {i}"
        if not isinstance(entry, dict):
            raise TargetsError(f"{where}: must be a table")
        unknown = set(entry) - _KEYS
        if unknown:
            raise TargetsError(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")
        view, unit, row = entry.get("view", VIEWS[0]), entry.get("unit"), entry.get("row")
        level, share = entry.get("level"), entry.get("share")
        if view not in TARGET_VIEWS:
            raise TargetsError(f"{where}: view must be one of {', '.join(TARGET_VIEWS)}")
        if not isinstance(unit, str) or not unit.strip():
            raise TargetsError(f"{where}: 'unit' must be a non-empty string")
        if not isinstance(row, str) or row not in tops:
            raise TargetsError(f"{where}: 'row' must name a team row of the maturity model (--levels)")
        if (level is None) == (share is None):
            raise TargetsError(f"{where}: set exactly one of 'level' and 'share'")
        if level is not None and (type(level) is not int or not 1 <= level <= tops[row]):
            raise TargetsError(f"{where}: 'level' must be a whole number from 1 to {tops[row]}")
        if share is not None and (type(share) not in (int, float) or not 0 < share <= 1):
            raise TargetsError(f"{where}: 'share' must be a number above 0 and at most 1")
        spec = TargetSpec(view, unit, row, level, None if share is None else float(share))
        if spec in out:
            raise TargetsError(f"{where}: duplicate target")
        out.append(spec)
    return out


def load_targets(path: Path, model: MaturityModel | None) -> list[TargetSpec]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise TargetsError(f"cannot read {path}: {e}") from e
    return parse_targets(data, model)


def resolve(specs: list[TargetSpec], key: Callable[[str], str]) -> list[Target]:
    """Each spec keyed as ``fleet-data.json`` keys it; ``key`` is ``fleet_data.Privacy.key``."""
    return [Target(s.view, key(s.unit if s.view == REPO_VIEW else f"{s.view}/{s.unit}"), s.row, s.level, s.share)
            for s in specs]
