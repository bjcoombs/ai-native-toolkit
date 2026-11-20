"""Read fleet output written before the 2.0.0 maturity-model rename (issue #670).

Before 2.0.0 a run's ``run.json`` and each ``groups.jsonl`` line named the
maturity-model version ``rubric_version``, and a group line held its rows under
``rubric``. They are now ``levels_version`` and ``levels``. The envelope
versions (``fleet_stamp_version``, ``fleet_group_version``) were deliberately
left at 1, because the history readers reject any other version and a bump
would drop every earlier dated entry. ``upgrade`` maps the old names to the
new ones when a stamp or group line is read, so a ``--compare`` baseline, a
same-day history entry and the trend history written before the rename stay
comparable.

The old names below are the only place the fleet readers still accept them.
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
