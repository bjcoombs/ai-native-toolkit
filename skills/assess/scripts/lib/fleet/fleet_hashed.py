"""Hashed repository and group ids in every fleet output file (issue #909, parent #740).

Under ``--hash-paths`` or ``--hash-salt`` the fleet run hashes each repository and group id once,
at the output boundary, with ``lib.counts_only.hash_value`` under the run's salt
(``h:`` plus 16 hex digits). The ids are the same ones ``fleet-data.json`` already writes
as repository keys, so ``repos.jsonl``, ``groups.jsonl``, ``level.json``,
``doc_freshness.jsonl``, ``agent_clusters.json``, the run history entry, ``compare.json``,
the team pages and every diagram join with it and with each other.

The scan itself, the ``--discovered`` coverage, the group roll-up and ``fleet-data.json``
still read the real ids: the ghreport settings join by group name and ancestor, the unit
trees, CODEOWNERS owners and web URLs are keyed by them, and ``fleet_data.Privacy``
hashes its own keys. A hashed group id is one segment, so it no longer derives from the
hashed ``repo`` by dropping the last segment.

- ``public_rows``: the rows with ``repo``, ``group`` and ``head_sha`` (#907) hashed, re-sorted by the new id.
- ``public_groups``: ``group`` and ``settings_scope`` hashed, and each team row's
  ``unknown_repos`` hashed and sorted; re-sorted by the new group id.
- ``public_freshness``: ``docs_repo`` and ``repo`` hashed.
- ``public_ids``: the hashed id set the history entry is chosen and compared by.

With no salt every function returns its input unchanged, so the default counts-only
output is byte-identical to before.

Hashed repository ids in every fleet output file under `--hash-paths` or `--hash-salt`
(#909). `public_rows` hashes each row's `repo`, `group` and `head_sha` (#907: a sha finds its repository) with `counts_only.hash_value`
under the run's salt and re-sorts by the new id; `public_groups` hashes `group`,
`settings_scope` and each team row's `unknown_repos`; `public_freshness` hashes `docs_repo` and `repo`; `public_ids` is the
hashed id set the history entry is chosen and compared by. The ids equal the
`fleet-data.json` repository keys, so every file joins on them. The scan, the coverage
reconcile, the group roll-up (the settings join) and `build_fleet_data` keep the real ids.
Without a salt every function is the identity, so default counts-only output is
unchanged. `fleet_next_steps.page_path` writes a hashed group as `teams/h-<hex>.md` and
`group_scopes` rejoins `--settings` through the recorded `settings_scope` under the run's
salt; `fleet_achievements_runs` passes over a re-score of an entry whose hashed ids share
none with this run (another salt). Tests:
`tests/test_fleet_hashed.py` (every file under `--out`, SVGs and team pages included,
holds no repository id; the joins; fixed-salt reruns are byte-identical).
"""
from __future__ import annotations

import copy
from collections.abc import Iterable, Sequence

from lib.counts_only import hash_value
from lib.fleet.runner import FleetRow
from lib.fleet.fleet_doc_freshness import FreshnessRow
from lib.fleet.fleet_groups import GroupRow, RowLevel


def public_id(repo: str, salt: str | None) -> str:
    """``repo`` as the output files name it: hashed under ``salt``, or unchanged without one."""
    return repo if salt is None else hash_value(repo, salt)


def public_ids(repos: Iterable[str], salt: str | None) -> frozenset[str]:
    return frozenset(public_id(repo, salt) for repo in repos)


def _hashed_sha(sha: str | None, salt: str) -> str | None:
    return None if sha is None else hash_value(sha, salt)


def public_rows(rows: Sequence[FleetRow], salt: str | None) -> list[FleetRow]:
    if salt is None:
        return list(rows)
    # A commit sha finds its repository by search, so it is hashed like the id (#907).
    hashed: list[FleetRow] = [{**row, "repo": hash_value(row["repo"], salt), "group": hash_value(row["group"], salt),
                               "head_sha": _hashed_sha(row.get("head_sha"), salt)}
                              for row in rows]
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
        scope = group["settings_scope"]
        out.append({**group, "group": hash_value(group["group"], salt), "levels": levels,
                    "settings_scope": None if scope is None else hash_value(scope, salt)})
    return sorted(out, key=lambda g: g["group"])


def public_freshness(rows: Sequence[FreshnessRow], salt: str | None) -> list[FreshnessRow]:
    if salt is None:
        return list(rows)
    return [{**row, "docs_repo": hash_value(row["docs_repo"], salt),
             "repo": None if row["repo"] is None else hash_value(row["repo"], salt)} for row in rows]
