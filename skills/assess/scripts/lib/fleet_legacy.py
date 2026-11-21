"""Read fleet output written before the 2.0.0 maturity-model rename (issue #670).

Before 2.0.0 a run's ``run.json`` and each ``groups.jsonl`` line named the
maturity-model version ``rubric_version``, and a group line held its rows under
``rubric``. They are now ``levels_version`` and ``levels``. The envelope
versions (``fleet_stamp_version``, ``fleet_group_version``) were deliberately
left at 1, because the history readers reject any other version and a bump
would drop every earlier dated entry. ``upgrade`` maps the old names to the
new ones when a stamp or group line is read. A run written before the rename
then loads, matches as a same-day or baseline history entry, and counts in the
trend history. ``--compare`` judges it by the usual rules: a run from another
plugin major (every 1.x run seen from 2.x) is refused for that reason alone,
never as a malformed stamp.

The old names below are the only place the fleet readers still accept them.

A stamp key added later with a default that is not None goes in
``STAMP_DEFAULTS``: ``stamp_value`` reads it so for a stamp written before
it, so an earlier run still matches and compares. ``platform`` (#714) reads as
``False`` in a stamp written before ``--platform`` existed (a key that defaults
to None, such as ``fleet_config_sha256``, needs no entry: ``.get`` gives None).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Old key -> new key, for run.json stamps and groups.jsonl lines.
RENAMED_KEYS: Mapping[str, str] = {"rubric_version": "levels_version", "rubric": "levels"}


def upgrade(data: dict[str, Any]) -> dict[str, Any]:
    """``data`` with each pre-rename key moved to its new name; a copy only when one moved.

    A key already present under its new name wins, and the old key is dropped
    either way, so the result never carries both.
    """
    if not any(old in data for old in RENAMED_KEYS):
        return data
    upgraded = {key: value for key, value in data.items() if key not in RENAMED_KEYS}
    for old, new in RENAMED_KEYS.items():
        if old in data and new not in data:
            upgraded[new] = data[old]
    return upgraded


# Stamp key -> the value a stamp written before the key existed means.
STAMP_DEFAULTS: Mapping[str, Any] = {"platform": False}


def stamp_value(stamp: Mapping[str, Any], key: str) -> Any:
    """``stamp[key]``, or what its absence means: the ``STAMP_DEFAULTS`` value, else None.

    Every comparability check reads stamp keys through it, on both sides."""
    return stamp.get(key, STAMP_DEFAULTS.get(key))
