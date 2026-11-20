"""Readers for one fleet run's output: ``repos.jsonl`` and ``groups.jsonl``.

The views built on a fleet run (the per-team next-steps pages, #571, and the
leadership heat map, #570) read the files ``skills/fleet/scripts/fleet.py``
wrote and never rescan. This module is the one place that reads them back:

- ``read_repos(out_dir)``: the ``repos.jsonl`` rows (``lib.fleet.FleetRow``).
- ``read_groups(out_dir)``: the ``groups.jsonl`` rows (``lib.fleet_groups.GroupRow``).

Each line must be a JSON object carrying its envelope version key at the
version this code reads; anything else raises ``FleetInputError`` naming the
file and line, so a view never renders from a half-read or foreign file. Rows
come back sorted by their id, whatever order the file holds.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from lib.fleet import FLEET_ROW_VERSION, REPOS_FILE, FleetRow
from lib.fleet_groups import FLEET_GROUP_VERSION, GROUPS_FILE, GroupRow


class FleetInputError(ValueError):
    """A fleet output file is missing or malformed; the message names file and line."""


def read_jsonl(path: Path, version_key: str, version: int) -> list[dict[str, Any]]:
    """Every line of ``path`` as a JSON object at envelope ``version``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FleetInputError(f"{path.name}: cannot read ({exc.strerror})") from exc
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise FleetInputError(f"{path.name}:{number}: not JSON ({exc})") from exc
        if not isinstance(row, dict):
            raise FleetInputError(f"{path.name}:{number}: not a JSON object")
        if row.get(version_key) != version:
            raise FleetInputError(
                f"{path.name}:{number}: {version_key} is {row.get(version_key)!r}, expected {version}")
        rows.append(row)
    return rows


def read_repos(out_dir: Path) -> list[FleetRow]:
    """The ``repos.jsonl`` rows of a fleet run, sorted by repository id."""
    rows = read_jsonl(out_dir / REPOS_FILE, "fleet_row_version", FLEET_ROW_VERSION)
    for number, row in enumerate(rows, start=1):
        if not isinstance(row.get("repo"), str) or not isinstance(row.get("group"), str):
            raise FleetInputError(f"{REPOS_FILE}: row {number} has no repo or group id")
    return sorted((cast(FleetRow, row) for row in rows), key=lambda r: r["repo"])


def read_groups(out_dir: Path) -> list[GroupRow]:
    """The ``groups.jsonl`` rows of a fleet run, sorted by group id."""
    rows = read_jsonl(out_dir / GROUPS_FILE, "fleet_group_version", FLEET_GROUP_VERSION)
    for number, row in enumerate(rows, start=1):
        if not isinstance(row.get("group"), str) or not isinstance(row.get("rubric"), list):
            raise FleetInputError(f"{GROUPS_FILE}: row {number} has no group id or rubric list")
    return sorted((cast(GroupRow, row) for row in rows), key=lambda r: r["group"])
