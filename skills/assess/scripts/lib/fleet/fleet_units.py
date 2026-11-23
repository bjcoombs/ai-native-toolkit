"""The fleet's org units: one tree per view, each repository placed once per view (issue #703).

A unit is an org, division, department, team or system, with a parent and
owners; repositories are the leaves. Two views exist, ``ownership`` and
``system``, and a repository sits in at most one unit of each.

**Ownership view.** Inferred, then overridden by the mapping file:

1. **Repository path** (GitLab nested groups): ``grp/sub/proj`` sits in unit
   ``grp/sub``, whose parent is ``grp``. Every prefix of a group path is a unit.
2. **GitHub teams**: a settings fragment (``fleet.py --settings``) may carry an
   optional ``teams`` list, ``[{slug, parent, repos}]`` (``parent`` a team slug
   or null, ``repos`` the repository names). A repository in the fragment's
   scope that a team lists moves into unit ``<scope>/<slug>`` under its parent
   team's unit (or the scope's). In several teams it takes the deepest, then
   the first slug in sort order. A team unit is owned by ``@<scope>/<slug>``.
3. **CODEOWNERS** (fallback, ``lib/fleet/fleet_repo_facts.py``): a repository in a
   one-segment group (no nested groups to place it) that no team placed, whose
   catch-all owners name a team handle under its group (``@acme/team`` for a
   repository in ``acme``, matched ignoring case), moves into unit
   ``acme/team`` (the team part lower-cased), the first such handle in file
   order.
4. **Mapping file** (``--units``): declared units replace an inferred unit of
   the same id (kind, parent, name, owners) or add one, and a declared
   placement moves a repository. The mapping always wins.

An inferred unit left with no repository below it (the mapping moved them all)
is dropped; a declared one is kept. An inferred unit's kind follows its place:
a root is ``org``, a unit holding a repository directly is ``team``, any other
is ``division`` one level down and ``department`` deeper.

**System view.** From the mapping file only. A repository the mapping gives no
``system`` is not in the system view, and the view is empty without a mapping.

**Mapping file** (TOML)::

    [[unit]]
    view = "ownership"        # or "system"; default "ownership"
    id = "acme/platform"      # unique within its view
    kind = "division"         # org, division, department, team, system
    parent = "acme"           # optional: a unit of the same view
    name = "Platform"         # optional display name
    owners = ["@acme/leads"]  # optional

    [[repo]]
    repo = "acme/svc1"        # a repos.jsonl id
    ownership = "acme/platform"
    system = "checkout"
    owners = ["@acme/payments"]  # optional: replaces the CODEOWNERS fallback

Any fault (an unknown key or kind, a duplicate unit or repository, a missing
parent or unit, a cycle) raises ``UnitsError`` naming the entry; ``fleet.py``
exits 2. A mapped repository the run did not scan is skipped and reported.

Pure apart from ``load_units`` reading the file; output sorted, so the same
inputs give the same tree.

The fleet's org units (#703, parent #692): one `Tree` per view (`ownership`, `system`),
each a sorted list of `Unit` (`id`, `name`, `kind`, `parent`, `owners`, `source`) and a
`placement` map, repository id to unit id, so a repository sits once per view.
`build_trees` infers the ownership view from repository paths (every prefix of a GitLab
`path_with_namespace` group is a unit), then GitHub teams from an optional `teams` list on
a GitHub settings fragment (written by `ghreport`; deepest team wins, then the first slug), then, for a
repository in a one-segment group that no team placed, a CODEOWNERS catch-all team handle
under that group (matched ignoring case, the unit id's team part lower-cased; #733), and
finally applies the `--units` mapping (`load_units` / `parse_units`, TOML `[[unit]]` and
`[[repo]]`), which always wins. The system view comes from the mapping only. Inferred units
with no repository left are dropped; their kind follows their place (root `org`, a unit
holding a repository `team`, else `division` then `department`). Every fault raises
`UnitsError` naming the entry (an unknown key or kind, a duplicate, a missing parent or
unit, a cycle). `repo_owners` gives each repository the mapping's owners, else its
CODEOWNERS ones; `unmatched_repos` names mapped repositories the run did not include.
"""
from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, TypedDict

from lib.run_context_types import JsonDict

OWNERSHIP = "ownership"
SYSTEM = "system"
VIEWS = (OWNERSHIP, SYSTEM)
KINDS = ("org", "division", "department", "team", "system")
SRC_PATH, SRC_TEAMS, SRC_CODEOWNERS, SRC_MAPPING = "path", "github_teams", "codeowners", "mapping"

_UNIT_KEYS = frozenset({"view", "id", "kind", "parent", "name", "owners"})
_REPO_KEYS = frozenset({"repo", "owners", *VIEWS})
_ROOT_KEYS = frozenset({"unit", "repo"})
_ID_RE = re.compile(r"^(?![/~\\])(?!.*://)\S{1,200}$")


class UnitsError(ValueError):
    """The mapping file is unusable; the message names the entry and the fault."""


class UnitSpec(TypedDict):
    view: str
    id: str
    kind: str
    parent: str | None
    name: str | None
    owners: list[str]


class RepoSpec(TypedDict):
    repo: str
    ownership: str | None
    system: str | None
    owners: list[str] | None


class UnitsMap(TypedDict):
    units: list[UnitSpec]
    repos: list[RepoSpec]


class Unit(TypedDict):
    id: str
    name: str
    kind: str
    parent: str | None  # the parent unit's id in the same view
    owners: list[str]
    source: str  # path | github_teams | codeowners | mapping


class Tree(TypedDict):
    view: str
    units: list[Unit]  # sorted by id
    placement: dict[str, str]  # repository id -> unit id


class Team(TypedDict):
    slug: str
    parent: str | None
    repos: list[str]


# --- mapping file ----------------------------------------------------------


def _str(table: Mapping[str, Any], key: str, where: str, *, required: bool = False) -> str | None:
    value = table.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise UnitsError(f"{where}: '{key}' must be a non-empty string")
    return value


def _id(table: Mapping[str, Any], key: str, where: str, *, required: bool = False) -> str | None:
    value = _str(table, key, where, required=required)
    if value is not None and not _ID_RE.match(value):
        raise UnitsError(f"{where}: '{key}' {value!r} must be one path-like id (no spaces, not absolute)")
    return value


def _owners(table: Mapping[str, Any], where: str) -> list[str] | None:
    value = table.get("owners")
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(o, str) and o.strip() for o in value):
        raise UnitsError(f"{where}: 'owners' must be a list of non-empty strings")
    return list(value)


def _keys(table: Any, allowed: frozenset[str], where: str) -> Mapping[str, Any]:
    if not isinstance(table, dict):
        raise UnitsError(f"{where}: must be a table")
    extra = sorted(set(table) - allowed)
    if extra:
        raise UnitsError(f"{where}: unknown key {extra[0]!r}")
    return table


def parse_units(data: Mapping[str, Any]) -> UnitsMap:
    """Validate a parsed mapping table (shape only; references are checked in ``build_trees``)."""
    _keys(dict(data), _ROOT_KEYS, "units file")
    units: list[UnitSpec] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(data.get("unit", [])):
        where = f"unit[{index}]"
        table = _keys(raw, _UNIT_KEYS, where)
        view = _str(table, "view", where) or OWNERSHIP
        if view not in VIEWS:
            raise UnitsError(f"{where}: unknown view {view!r} (one of {', '.join(VIEWS)})")
        uid = _id(table, "id", where, required=True)
        assert uid is not None
        where = f"unit {uid!r}"
        kind = _str(table, "kind", where, required=True)
        if kind not in KINDS:
            raise UnitsError(f"{where}: unknown kind {kind!r} (one of {', '.join(KINDS)})")
        if (view, uid) in seen:
            raise UnitsError(f"{where}: declared twice in view {view!r}")
        seen.add((view, uid))
        units.append({"view": view, "id": uid, "kind": kind, "parent": _id(table, "parent", where),
                      "name": _str(table, "name", where), "owners": _owners(table, where) or []})
    repos: list[RepoSpec] = []
    placed: set[str] = set()
    for index, raw in enumerate(data.get("repo", [])):
        table = _keys(raw, _REPO_KEYS, f"repo[{index}]")
        repo = _str(table, "repo", f"repo[{index}]", required=True)
        assert repo is not None
        where = f"repo {repo!r}"
        if repo in placed:
            raise UnitsError(f"{where}: listed twice (a repository sits once per view)")
        placed.add(repo)
        repos.append({"repo": repo, "ownership": _id(table, OWNERSHIP, where), "system": _id(table, SYSTEM, where),
                      "owners": _owners(table, where)})
    return {"units": units, "repos": repos}


def load_units(path: Path) -> UnitsMap:
    """Read and validate a mapping file; raise ``UnitsError`` on any fault."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise UnitsError(f"cannot read units file {path.name}: {exc}") from exc
    for key in ("unit", "repo"):
        if not isinstance(data.get(key, []), list):
            raise UnitsError(f"units file: '{key}' must be an array of tables ([[{key}]])")
    return parse_units(data)


def empty_map() -> UnitsMap:
    return {"units": [], "repos": []}


# --- inference -------------------------------------------------------------


def group_of(repo: str) -> str:
    return repo.rsplit("/", 1)[0] if "/" in repo else ""


def _add_chain(units: dict[str, Unit], uid: str, source: str, floor: str = "") -> None:
    """``uid`` and each ancestor prefix below ``floor`` (already present), as inferred units."""
    parts = uid.split("/")
    for depth in range(1, len(parts) + 1):
        current = "/".join(parts[:depth])
        if current in units or (floor and len(current) <= len(floor)):
            continue
        parent = "/".join(parts[:depth - 1]) or None
        units[current] = {"id": current, "name": parts[depth - 1], "kind": "", "parent": parent,
                          "owners": [], "source": source}


def fragment_teams(fragment: Mapping[str, Any] | None) -> list[Team]:
    """The well-formed entries of a settings fragment's optional ``teams`` list."""
    raw = fragment.get("teams") if fragment is not None else None
    teams: list[Team] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("slug"), str) or not entry["slug"]:
            continue
        parent = entry.get("parent")
        repos = entry.get("repos")
        teams.append({"slug": entry["slug"], "parent": parent if isinstance(parent, str) and parent else None,
                      "repos": sorted(r for r in repos if isinstance(r, str)) if isinstance(repos, list) else []})
    return sorted(teams, key=lambda t: t["slug"])


def _team_chain(slug: str, by_slug: Mapping[str, Team]) -> list[str]:
    """``slug`` and its ancestors, root first; a cycle or a missing parent ends the chain."""
    chain: list[str] = []
    current: str | None = slug
    while current is not None and current in by_slug and current not in chain:
        chain.append(current)
        current = by_slug[current]["parent"]
    return list(reversed(chain))


def _place_by_teams(units: dict[str, Unit], placement: dict[str, str], repos: Iterable[str],
                    settings: Mapping[str, JsonDict]) -> set[str]:
    placed: set[str] = set()
    for repo in sorted(repos):
        group = group_of(repo)
        fragment = settings.get(group)
        if not group or fragment is None or fragment.get("provider") != "github":
            continue
        teams = fragment_teams(fragment)
        by_slug = {t["slug"]: t for t in teams}
        name = repo.rsplit("/", 1)[-1]
        chains = [_team_chain(t["slug"], by_slug) for t in teams if name in t["repos"] or repo in t["repos"]]
        if not chains:
            continue
        chain = min(chains, key=lambda c: (-len(c), c[-1]))
        parent = group
        for slug in chain:
            uid = f"{group}/{slug}"
            if uid not in units:
                units[uid] = {"id": uid, "name": slug, "kind": "", "parent": parent,
                              "owners": [f"@{group}/{slug}"], "source": SRC_TEAMS}
            parent = uid
        placement[repo] = parent
        placed.add(repo)
    return placed


def _place_by_codeowners(units: dict[str, Unit], placement: dict[str, str], repo: str,
                         owners: Iterable[str]) -> None:
    group = group_of(repo)
    for owner in owners:
        handle = owner[1:] if owner.startswith("@") else ""
        # Handles are case-insensitive on GitHub and GitLab: ``@Acme/Platform``
        # in group ``acme`` is unit ``acme/platform`` (team slugs are lower case).
        if group and handle.casefold().startswith(group.casefold() + "/") and _ID_RE.match(handle):
            uid = f"{group}/{handle[len(group) + 1:].casefold()}"
            _add_chain(units, uid, SRC_CODEOWNERS, floor=group)
            units[uid]["owners"] = sorted(set(units[uid]["owners"]) | {owner})
            placement[repo] = uid
            return


# --- trees -----------------------------------------------------------------


def _check_tree(view: str, units: Mapping[str, Unit]) -> None:
    for uid in sorted(units):
        child, parent = uid, units[uid]["parent"]
        seen = {uid}
        while parent is not None:
            if parent not in units:  # at any depth: a missing grandparent is a fault, not a KeyError
                raise UnitsError(f"unit {child!r} ({view}): parent {parent!r} is not a unit of that view")
            if parent in seen:
                raise UnitsError(f"unit {uid!r} ({view}): parent chain has a cycle")
            seen.add(parent)
            child, parent = parent, units[parent]["parent"]


def _depth(uid: str, units: Mapping[str, Unit]) -> int:
    depth, parent = 0, units[uid]["parent"]
    while parent is not None:
        depth, parent = depth + 1, units[parent]["parent"]
    return depth


def _finish(view: str, units: dict[str, Unit], placement: dict[str, str]) -> Tree:
    """Check the tree, drop empty inferred units and give inferred units their kind."""
    _check_tree(view, units)
    occupied: set[str] = set()
    for uid in [*placement.values(), *(u for u, unit in units.items() if unit["source"] == SRC_MAPPING)]:
        current: str | None = uid
        while current is not None and current not in occupied:
            occupied.add(current)
            current = units[current]["parent"]
    kept = {uid: unit for uid, unit in units.items() if uid in occupied}
    holders = set(placement.values())
    for uid, unit in kept.items():
        if unit["source"] != SRC_MAPPING:
            depth = _depth(uid, kept)
            unit["kind"] = ("org" if depth == 0 else "team" if uid in holders
                            else "division" if depth == 1 else "department")
    return {"view": view, "units": [kept[uid] for uid in sorted(kept)],
            "placement": dict(sorted(placement.items()))}


def _apply_mapping(view: str, units: dict[str, Unit], placement: dict[str, str], mapping: UnitsMap,
                   repos: frozenset[str]) -> None:
    for spec in mapping["units"]:
        if spec["view"] == view:
            units[spec["id"]] = {"id": spec["id"], "name": spec["name"] or spec["id"].rsplit("/", 1)[-1],
                                 "kind": spec["kind"], "parent": spec["parent"],
                                 "owners": sorted(spec["owners"]), "source": SRC_MAPPING}
    for repo_spec in mapping["repos"]:
        target = repo_spec["ownership"] if view == OWNERSHIP else repo_spec["system"]
        if target is None or repo_spec["repo"] not in repos:
            continue
        if target not in units:
            raise UnitsError(f"repo {repo_spec['repo']!r}: {view} unit {target!r} is not a unit of that view")
        placement[repo_spec["repo"]] = target


def build_trees(repos: Iterable[str], mapping: UnitsMap, settings: Mapping[str, JsonDict] | None = None,
                codeowners: Mapping[str, Iterable[str]] | None = None) -> list[Tree]:
    """The ownership and system trees for the run's repository ids; raises ``UnitsError``."""
    ids = frozenset(repos)
    units: dict[str, Unit] = {}
    placement: dict[str, str] = {}
    for repo in sorted(ids):
        group = group_of(repo)
        if group:
            _add_chain(units, group, SRC_PATH)
            placement[repo] = group
    by_team = _place_by_teams(units, placement, ids, settings or {})
    for repo in sorted(r for r in ids - by_team if "/" not in group_of(r)):
        _place_by_codeowners(units, placement, repo, (codeowners or {}).get(repo, ()))
    _apply_mapping(OWNERSHIP, units, placement, mapping, ids)
    ownership = _finish(OWNERSHIP, units, placement)
    system_units: dict[str, Unit] = {}
    system_placement: dict[str, str] = {}
    _apply_mapping(SYSTEM, system_units, system_placement, mapping, ids)
    return [ownership, _finish(SYSTEM, system_units, system_placement)]


def unmatched_repos(mapping: UnitsMap, repos: Iterable[str]) -> list[str]:
    """Mapped repository ids the run did not include (skipped, reported on stderr)."""
    ids = frozenset(repos)
    return sorted(spec["repo"] for spec in mapping["repos"] if spec["repo"] not in ids)


def repo_owners(mapping: UnitsMap, codeowners: Mapping[str, Iterable[str]],
                repos: Iterable[str]) -> dict[str, list[str]]:
    """Each repository's owners: the mapping's, else its CODEOWNERS catch-all owners."""
    mapped = {spec["repo"]: spec["owners"] for spec in mapping["repos"] if spec["owners"] is not None}
    return {repo: sorted(mapped[repo] if repo in mapped else codeowners.get(repo, ())) for repo in sorted(repos)}
