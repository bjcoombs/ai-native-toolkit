"""Hashed repository ids in every fleet output file (issue #909, parent #740).

Under ``--hash-paths`` or ``--hash-salt`` the fleet run hashes each repository id once,
at the output boundary, with ``lib.counts_only.hash_value`` under the run's salt
(``h:`` plus 16 hex digits). The ids are the same ones ``fleet-data.json`` already writes
as repository keys, so ``repos.jsonl``, ``groups.jsonl``, ``level.json``,
``doc_freshness.jsonl``, ``agent_clusters.json``, the run history entry, ``compare.json``,
the team pages and every diagram join with it and with each other.

The scan itself, the ``--discovered`` coverage and ``fleet-data.json`` still read the
real ids: the unit trees, CODEOWNERS owners and web URLs are keyed by them, and
``fleet_data.Privacy`` hashes its own keys.

- ``public_rows``: the rows with ``repo`` hashed, re-sorted by the new id.
- ``public_groups``: each team row's ``unknown_repos`` hashed and sorted.
- ``public_freshness``: ``docs_repo`` and ``repo`` hashed.
- ``public_ids``: the hashed id set the history entry is chosen and compared by.

With no salt every function returns its input unchanged, so the default counts-only
output is byte-identical to before.
"""
from __future__ import annotations

import copy
from collections.abc import Iterable, Sequence

from lib.counts_only import hash_value
from lib.fleet import FleetRow
from lib.fleet_doc_freshness import FreshnessRow
from lib.fleet_groups import GroupRow, RowLevel


def public_id(repo: str, salt: str | None) -> str:
    """``repo`` as the output files name it: hashed under ``salt``, or unchanged without one."""
    return repo if salt is None else hash_value(repo, salt)


def public_ids(repos: Iterable[str], salt: str | None) -> frozenset[str]:
    return frozenset(public_id(repo, salt) for repo in repos)


def public_rows(rows: Sequence[FleetRow], salt: str | None) -> list[FleetRow]:
    if salt is None:
        return list(rows)
    hashed: list[FleetRow] = [{**row, "repo": hash_value(row["repo"], salt)} for row in rows]
    return sorted(hashed, key=lambda r: r["repo"])


def public_groups(groups: Sequence[GroupRow], salt: str | None) -> list[GroupRow]:
    if salt is None:
        return list(groups)
    out: list[GroupRow] = []
    for group in groups:
        levels: list[RowLevel] = []
        for level in group["levels"]:
            hashed = copy.copy(level)
            hashed["unknown_repos"] = sorted(hash_value(r, salt) for r in level["unknown_repos"])
            levels.append(hashed)
        out.append({**group, "levels": levels})
    return out


def public_freshness(rows: Sequence[FreshnessRow], salt: str | None) -> list[FreshnessRow]:
    if salt is None:
        return list(rows)
    return [{**row, "docs_repo": hash_value(row["docs_repo"], salt),
             "repo": None if row["repo"] is None else hash_value(row["repo"], salt)} for row in rows]
