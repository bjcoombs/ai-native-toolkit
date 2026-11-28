"""The fleet's shard plan: one artifact per top-level unit, plus an index, when one is too small (issue #950).

``shard_plan(bundle, limits)`` is pure: it reads one run's ``fleet-data.json`` bundle and returns
where each repository is published. It never calls the network and never publishes.

**One artifact or shards.** The bundle is projected whole (``fleet_publish.project``). When that
plan fits ``limits`` (every document within the per-document size and depth limits, and the
document count within the per-artifact limit), the plan is one artifact and no index
(``sharded`` false). Otherwise, or when the shard registry already records shards (a sharded
fleet stays sharded), the plan is one shard per top-level unit plus an index.

**Top-level units.** The ownership view's top-level units are the children of its roots (a root
is a unit with no parent). A repository belongs to the shard of the top-level unit above its
ownership unit. A repository with no ownership unit, or placed at a root itself, belongs to the
one ``unassigned`` shard (``UNASSIGNED``), which is planned only when it holds a repository.
Shards are ordered by key, ``unassigned`` last.

**A shard's bundle** (``shard_bundle``) is the run's bundle cut to the shard: its repositories;
in the ownership view the top-level unit, every unit below it and every unit above one of the
shard's repositories (so the root and its roll-up are in every shard); in the system view every
system unit above one of the shard's repositories. ``opportunities.biggest_lift.repos`` and each
``opportunities.shared_templates[].repos`` keep only the shard's repositories, while their
counts stay the fleet's: those lists are what made one ``runs`` document too large (#947).
Everything else is copied.

**Per-shard size guard.** Each shard's bundle is projected on its own, with that shard's stored
sizes, so #947's guard runs per shard: a document at 80% of the per-document limit is listed in
the shard's ``size.warnings``, and one above it raises ``fleet_publish.DocumentTooLarge`` naming
the shard and the document. A shard whose document count is over the per-artifact limit raises
``ShardTooLarge`` naming the unit and its estimate: one top-level unit cannot be split further.

**Registry** (``ShardRegistry``): ``{"index": <url or null>, "shards": {<shard key>: <url>}}``,
the artifact URL of the index and of each shard, kept in the fleet's history directory
(``lib/fleet/fleet_publish_shards.py`` reads and writes it). The plan reads it: a shard whose key
it records keeps its URL; a new top-level unit is a new shard (``added``), with no URL until it is
first published; a recorded key no longer in the fleet is ``retired``, keeps its URL and is
listed on the index as no longer in the fleet. The plan never removes an entry, so a shard
artifact is never deleted automatically.

**Links** (``links_complete``): each shard carries the index URL, and the index carries every
shard's URL. On a first publish neither is known, so the pages are published, their URLs
recorded, and the plan run again to relink the pages (``skills/fleet/references/publish.md``).

**Index** (``IndexDoc``): per shard its label (``fleet_page_data.labels``, so it reads as the
shard's own page does), its unit kind, its URL and the top-level unit's Step roll-up (the
lowest Step and "N of M measured"); for ``unassigned``, the same counts over its repositories.
It holds no repository document.

**Navigation** (``ShardView``): what the shard's page needs to link out, all by page label: the
index URL, the shard's top-level unit, the units and repositories it shows, and per system unit
it shows, the count of that system's repositories in each other shard with that shard's URL.

Deterministic: the same bundle, limits and registry give byte-identical ``dumps_shard_plan``
output. Reference: ``skills/fleet/references/publish.md`` ("Sharded fleets").
"""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from typing import Any, Final, TypedDict

from lib.fleet.fleet_data import FleetData
from lib.fleet.fleet_page_data import labels, mode_of
from lib.fleet.fleet_publish import (
    LIMITS,
    DocumentTooLarge,
    Limits,
    PublishError,
    SizeReport,
    doc_id,
    project,
)

UNASSIGNED: Final = "unassigned"
UNASSIGNED_LABEL: Final = "unassigned repositories"
OWNERSHIP, SYSTEM = "ownership", "system"


class ShardTooLarge(PublishError):
    """One top-level unit needs more than one artifact's documents."""


class ShardRegistry(TypedDict):
    index: str | None
    shards: dict[str, str]  # shard key -> artifact URL


class Elsewhere(TypedDict):
    unit: str  # the other shard's label
    url: str | None  # its artifact URL; None before its first publish
    repos: int  # the system's repositories in that shard


class ShardView(TypedDict):
    unit: str | None  # the top-level unit's page label; None for unassigned
    label: str
    index_url: str | None
    units: dict[str, list[str]]  # view -> the page labels of the units the shard shows
    repos: list[str]  # the page labels of its repositories
    elsewhere: dict[str, list[Elsewhere]]  # system unit label -> its repositories in other shards


class Shard(TypedDict):
    key: str  # the top-level unit's key, or UNASSIGNED
    folder: str  # a file-system-safe name for the shard's files (``shard_folder``)
    label: str
    kind: str | None  # the top-level unit's kind; None for unassigned
    url: str | None  # the registry's URL; None until first published
    repos: int
    measured: int  # repositories with a Step
    lowest_step: int | None
    size: SizeReport
    view: ShardView


class IndexEntry(TypedDict):
    label: str
    kind: str | None
    url: str | None
    repos: int
    measured: int
    lowest_step: int | None


class RetiredEntry(TypedDict):
    key: str
    url: str


class IndexDoc(TypedDict):
    scan_date: str | None
    mode: str
    shards: list[IndexEntry]
    retired: list[RetiredEntry]  # recorded shards no longer in the fleet: kept, never deleted


class ShardPlan(TypedDict):
    run_id: str
    sharded: bool
    index_url: str | None
    links_complete: bool
    shards: list[Shard]
    added: list[str]  # shard keys the registry does not record: published as new artifacts
    retired: list[str]  # recorded keys no longer in the fleet
    index: IndexDoc | None


def shard_folder(key: str) -> str:
    """``key`` as one folder name: its document id, with ``:`` (not allowed on Windows) escaped as
    ``~3A``, which ``doc_id`` never writes, so the name stays one-to-one with the key."""
    return doc_id(key).replace(":", "~3A")


def empty_registry() -> ShardRegistry:
    return {"index": None, "shards": {}}


def _units(bundle: Mapping[str, Any], view: str) -> dict[str, Mapping[str, Any]]:
    for v in bundle.get("views") or []:
        if v.get("view") == view:
            return {u["key"]: u for u in v.get("units") or []}
    return {}


def _ancestors(key: str | None, units: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """``key`` and every unit above it, nearest first."""
    out: list[str] = []
    while key is not None and key in units and key not in out:
        out.append(key)
        key = units[key].get("parent")
    return out


def _unit_of(repo: Mapping[str, Any], view: str) -> str | None:
    return next((u["unit"] for u in repo.get("units") or [] if u.get("view") == view), None)


def top_level_unit(repo: Mapping[str, Any], units: Mapping[str, Mapping[str, Any]]) -> str:
    """The shard key of ``repo``: its ownership top-level unit, or ``UNASSIGNED``."""
    chain = _ancestors(_unit_of(repo, OWNERSHIP), units)
    return chain[-2] if len(chain) >= 2 else UNASSIGNED


def assignment(bundle: Mapping[str, Any]) -> dict[str, list[str]]:
    """Shard key -> its repository keys, sorted; every top-level unit has a shard, even an empty one."""
    units = _units(bundle, OWNERSHIP)
    out: dict[str, list[str]] = {k: [] for k, u in units.items()
                                 if u.get("parent") is not None and units.get(u["parent"], {}).get("parent") is None
                                 and u["parent"] in units}
    for repo in bundle.get("repos") or []:
        out.setdefault(top_level_unit(repo, units), []).append(repo["key"])
    if not out.get(UNASSIGNED):
        out.pop(UNASSIGNED, None)
    return {k: sorted(v) for k, v in sorted(out.items(), key=lambda kv: (kv[0] == UNASSIGNED, kv[0]))}


def _subtree(top: str, units: Mapping[str, Mapping[str, Any]]) -> set[str]:
    return {k for k in units if top in _ancestors(k, units)}


def _shard_units(bundle: Mapping[str, Any], key: str, repos: list[Mapping[str, Any]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for v in bundle.get("views") or []:
        units = {u["key"]: u for u in v.get("units") or []}
        keep: set[str] = set()
        for repo in repos:
            keep.update(_ancestors(_unit_of(repo, v["view"]), units))
        if v["view"] == OWNERSHIP and key != UNASSIGNED:
            keep |= _subtree(key, units) | set(_ancestors(key, units))
        out[v["view"]] = keep
    return out


def shard_bundle(bundle: FleetData, key: str, repo_keys: list[str]) -> FleetData:
    """The run's bundle cut to one shard (see the module docstring)."""
    members = set(repo_keys)
    repos = [r for r in bundle["repos"] if r["key"] in members]
    keep = _shard_units(bundle, key, list(repos))
    out: FleetData = copy.deepcopy(bundle)
    out["repos"] = copy.deepcopy(repos)
    out["views"] = [{"view": v["view"], "units": [u for u in v["units"] if u["key"] in keep.get(v["view"], set())]}
                    for v in out["views"]]
    opp = out.get("opportunities")
    if opp is not None:
        lift = opp.get("biggest_lift")
        if lift is not None:
            lift["repos"] = [r for r in lift["repos"] if r in members]
        for template in opp.get("shared_templates") or []:
            template["repos"] = [r for r in template["repos"] if r in members]
    return out


def _fits(bundle: FleetData, run_id: str, limits: Limits) -> bool:
    try:
        return project(bundle, run_id, limits=limits)["size"]["fits"]
    except DocumentTooLarge:
        return False


def _steps(key: str, repos: Sequence[Mapping[str, Any]], units: Mapping[str, Mapping[str, Any]]) -> tuple[int, int, int | None]:
    """(repos, measured, lowest Step): the top-level unit's roll-up, or the same counts over ``repos``."""
    step = units[key].get("step") if key in units else None
    if isinstance(step, Mapping):
        return int(step.get("repos") or 0), int(step.get("measured") or 0), step.get("lowest_step")
    held = [r["step"] for r in repos if r.get("step") is not None]
    return len(repos), len(held), min(held) if held else None


def _elsewhere(bundle: Mapping[str, Any], shard_of: Mapping[str, str], key: str, shown: set[str],
               label: Mapping[str, str], urls: Mapping[str, str | None]) -> dict[str, list[Elsewhere]]:
    units = _units(bundle, SYSTEM)
    counts: dict[str, dict[str, int]] = {}
    for repo in bundle.get("repos") or []:
        other = shard_of[repo["key"]]
        if other == key:
            continue
        for system in _ancestors(_unit_of(repo, SYSTEM), units):
            if system in shown:
                counts.setdefault(system, {}).setdefault(other, 0)
                counts[system][other] += 1
    return {label[s]: [{"unit": label.get(o, UNASSIGNED_LABEL), "url": urls.get(o), "repos": n}
                       for o, n in sorted(by.items(), key=lambda kv: (kv[0] == UNASSIGNED, kv[0]))]
            for s, by in sorted(counts.items(), key=lambda kv: label[kv[0]])}


def shard_plan(bundle: FleetData, limits: Limits = LIMITS, *, run_id: str | None = None,
               registry: ShardRegistry | None = None,
               stored: Mapping[str, Mapping[str, int]] | None = None) -> tuple[ShardPlan, dict[str, FleetData]]:
    """The plan, and each shard's bundle by shard key (empty when not sharded).

    ``run_id`` defaults to the run's scan date; ``stored`` maps a shard key to that shard's
    stored document sizes (``fleet_publish.project``'s ``stored``)."""
    run_id = run_id or str(bundle["run"].get("scan_date") or "")
    registry = registry or empty_registry()
    stored = stored or {}
    if not registry["shards"] and _fits(bundle, run_id, limits):
        return ({"run_id": run_id, "sharded": False, "index_url": None, "links_complete": True, "shards": [],
                 "added": [], "retired": [], "index": None}, {})
    mode = mode_of(bundle)
    label = labels(bundle, mode)
    owners = _units(bundle, OWNERSHIP)
    by_shard = assignment(bundle)
    shard_of = {r: k for k, rs in by_shard.items() for r in rs}
    urls: dict[str, str | None] = {k: registry["shards"].get(k) for k in by_shard}
    index_url = registry["index"]
    shards: list[Shard] = []
    bundles: dict[str, FleetData] = {}
    for key, repo_keys in by_shard.items():
        sub = shard_bundle(bundle, key, repo_keys)
        name = UNASSIGNED_LABEL if key == UNASSIGNED else label[key]
        try:
            size = project(sub, run_id, stored=stored.get(key), limits=limits)["size"]
        except DocumentTooLarge as e:
            raise DocumentTooLarge(f"shard {name}: {e}") from e
        if not size["fits"]:
            raise ShardTooLarge(f"unit {name} needs about {size['documents']} documents, over one artifact's "
                                f"{size['document_limit']}; one top-level unit is one shard and is not split further")
        count, measured, lowest = _steps(key, sub["repos"], owners)
        shown = {view: sorted(label[u["key"]] for u in v["units"]) for v in sub["views"] for view in [v["view"]]}
        system_keys = {u["key"] for v in sub["views"] if v["view"] == SYSTEM for u in v["units"]}
        view: ShardView = {"unit": None if key == UNASSIGNED else label[key], "label": name, "index_url": index_url,
                           "units": shown, "repos": sorted(label[r] for r in repo_keys),
                           "elsewhere": _elsewhere(bundle, shard_of, key, system_keys, label, urls)}
        shards.append({"key": key, "folder": shard_folder(key), "label": name,
                       "kind": None if key == UNASSIGNED else owners[key].get("kind"), "url": urls[key],
                       "repos": count, "measured": measured, "lowest_step": lowest, "size": size, "view": view})
        bundles[key] = sub
    retired = sorted(k for k in registry["shards"] if k not in by_shard)
    index: IndexDoc = {
        "scan_date": bundle["run"].get("scan_date"), "mode": mode,
        "shards": [{"label": s["label"], "kind": s["kind"], "url": s["url"], "repos": s["repos"],
                    "measured": s["measured"], "lowest_step": s["lowest_step"]} for s in shards],
        "retired": [{"key": k, "url": registry["shards"][k]} for k in retired],
    }
    plan: ShardPlan = {"run_id": run_id, "sharded": True, "index_url": index_url,
                       "links_complete": index_url is not None and all(s["url"] is not None for s in shards),
                       "shards": shards, "added": [s["key"] for s in shards if s["url"] is None],
                       "retired": retired, "index": index}
    return plan, bundles


def dumps_shard_plan(plan: ShardPlan) -> str:
    """Canonical bytes: sorted keys, compact, trailing newline."""
    return json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
