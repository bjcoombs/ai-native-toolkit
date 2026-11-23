"""One maturity-model criterion's check manifest ``why`` for the fleet outputs (#1128).

``criterion_why(row, level, signal)`` finds the criterion on ``signal`` in the row's
``level`` and returns ``lib.level_tally.condition_why`` over that criterion alone: the
same manifest text the level result's conditions carry, read from the same core manifest
map (``manifest_whys.condition_whys``, built once per process here). ``None`` when the row
has no such level or criterion, or the manifest has no entry for it (a caller's own model):
never invented. ``fleet-data.json`` (``repos.rows.why``, ``opportunities.biggest_lift.why``)
and the team next-steps pages read it. Tests: ``tests/test_fleet_condition_why.py``.
"""
from __future__ import annotations

import functools
from collections.abc import Mapping

from lib.level_tally import condition_why
from lib.levels import Row


@functools.cache
def _core_whys() -> Mapping[str, str]:
    """``manifest_whys.condition_whys`` of the core manifest, built once per process."""
    from lib.manifest.check_manifest import core_manifest  # lazy: the manifest loads once
    from lib.manifest.manifest_whys import condition_whys
    return condition_whys(core_manifest())


def criterion_why(row: Row, level: int, signal: str) -> str | None:
    """The manifest why of the criterion on ``signal`` at ``level`` of ``row``; None when none."""
    criteria = next((lv["criteria"] for lv in row["levels"] if lv["level"] == level), [])
    match = [c for c in criteria if c["signal"] == signal][:1]
    return condition_why(row["id"], level, match, _core_whys()) or None
