"""The bundle ``fleet-view.html`` embeds: a projection of one run's outputs for the page (#946).

``page_bundle`` reads ``fleet-data.json`` (#944), ``achievements.json`` (#945), the maturity
model (Step names and gates, row titles) and the drawn fleet SVGs, and returns the one JSON
document the page renders from. It copies; it computes no level, Step or achievement. Every
count on the page is a count in these inputs.

**Names.** How a repository or unit is shown follows the run's output mode, as the #940 tag
heatmap does: ``--full`` shows names (a unit's path in its view, which is unique); a hashed run
(``--hash-paths``, ``--hash-salt``) shows the hashed key; plain counts-only shows
``repository N`` and ``<kind> N`` (N by sorted key), and its keys are replaced by those
labels before they reach the page, so no repository, group or path name is written. Owners
and people are never copied. An exclusion's reason (the owner's words) is copied only under
``--full``, which is the only mode that keeps it. A web URL is copied only under ``--full``.

**Ids.** Maturity-model row ids and condition ids (which carry layer numbers in the shipped
model) never reach the page: a row is its index into ``rows`` and is shown by its title, and a
condition is shown as its row title and level. Gate ids are copied: they are the model's Step
gate names.

**Only this run's keys.** An achievement, a Recount line or a target whose unit or repository
is not in this run's ``fleet-data.json`` (a repository dropped since the earlier run, a target
for a unit this run did not build) is left out: it could only be shown by its raw key, which
would name it in counts-only output. No label ever falls back to a raw key.

**Gate ids** are copied as the model names them. The shipped model's gate ids carry no layer
number (``tests/test_fleet_page.py`` checks it); a caller's own model is shown as written.

**Achievements** are re-worded from their structured evidence with the same templates as
``fleet_achievements`` credit lines, so the unit and repository labels follow the mode above.

**Practice packs** (#1276, ``lib/fleet/fleet_packs.py``): a ``--pack`` run's ``packs`` roll-up
is copied as ``packs``, each pack's name and version and each practice's title, level and
counts; no condition id. Absent without a pack.

**Front screen** (#1255, ``lib/fleet/fleet_front.py``): copied as computed, counts and gate ids
only; its team links are replaced by the ownership view's team units under the labels above, so
a counts-only page shows ``team N``.

Deterministic: lists in input order (the inputs are sorted), labels by sorted key, the SVGs
as base64 ``data:`` URIs. `page_bundle(data, achievements, model, pictures)` -> `PageBundle`.
Tests: `tests/test_fleet_page.py`.
"""
from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from lib.levels import MaturityModel
from lib.levels_fields import TRUTH_TAG_TITLES

PAGE_VERSION = 1
FULL, HASHED, COUNTS_ONLY = "full", "hashed", "counts_only"


class Picture(TypedDict):
    file: str
    title: str
    question: str
    src: str  # a data: URI


class _PackKeys(TypedDict, total=False):
    packs: list[dict[str, Any]]  # a --pack run's roll-up (#1276), copied from fleet-data.json; absent without


class PageBundle(_PackKeys):
    page_version: int
    mode: str
    scan_date: str | None
    levels_version: str | None
    rows: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    tags: list[dict[str, str]]
    views: list[dict[str, Any]]
    repos: list[dict[str, Any]]
    achievements: list[dict[str, Any]]
    runs: list[str]
    note: str | None
    recount: dict[str, Any] | None
    targets: list[dict[str, Any]]
    pictures: list[Picture]
    front: dict[str, Any] | None  # the front screen (fleet_front.FrontScreen), team links by label


def _front(front: Mapping[str, Any] | None, data: Mapping[str, Any], label: Mapping[str, str]) -> dict[str, Any] | None:
    """The front screen with the page's team links: the ownership view's team units, by key
    (alphabetical in ``--full``, label order otherwise), each a unit button, never a file link."""
    if front is None:
        return None
    teams = sorted(u["key"] for v in data.get("views") or [] if v.get("view") == "ownership"
                   for u in v.get("units") or [] if u.get("kind") == "team")
    return {**front, "teams": [{"label": label[k], "href": None} for k in teams], "teams_index": None}


def mode_of(data: Mapping[str, Any]) -> str:
    run = data.get("run") or {}
    if not run.get("counts_only", True):
        return FULL
    return HASHED if run.get("paths_hashed") else COUNTS_ONLY


def labels(data: Mapping[str, Any], mode: str) -> dict[str, str]:
    """Each repository and unit key's label under ``mode`` (see the module docstring)."""
    out: dict[str, str] = {}
    repos = sorted(r["key"] for r in data.get("repos") or [])
    for i, key in enumerate(repos, 1):
        out[key] = key if mode != COUNTS_ONLY else f"repository {i}"
    for view in data.get("views") or []:
        counts: dict[str, int] = {}
        for unit in sorted(view.get("units") or [], key=lambda u: u["key"]):
            kind = str(unit.get("kind") or "unit")
            counts[kind] = counts.get(kind, 0) + 1
            if mode == FULL:
                out[unit["key"]] = unit["key"].split("/", 1)[-1]  # the unit's path: unique, unlike a name
            elif mode == HASHED:
                out[unit["key"]] = unit["key"]
            else:
                out[unit["key"]] = f"{kind} {counts[kind]}"
    return out


def _condition_text(cid: str, titles: Mapping[str, str]) -> str:
    row, _, level = cid.rpartition(".")
    return f"{titles.get(row, 'a condition')}, level {level}" if level.isdigit() else titles.get(cid, "a condition")


def _unit(unit: Mapping[str, Any], label: Mapping[str, str]) -> dict[str, Any]:
    step = unit.get("step") or {}
    nxt = step.get("next_step")
    tags = unit.get("tags") or {}
    return {
        "key": label[unit["key"]], "kind": unit.get("kind"),
        "parent": label.get(unit["parent"]) if unit.get("parent") else None,
        "repos": step.get("repos", unit.get("repos")), "measured": step.get("measured"),
        "lowest_step": step.get("lowest_step"),
        "next": None if not nxt else {
            "step": nxt["step"], "held": nxt["held"],
            "gates": [{k: g[k] for k in ("gate", "held", "not_measured", "excluded")} for g in nxt["gates"]]},
        "tags": [{k: c[k] for k in ("tag", "met", "measured", "not_measured")} for c in tags.get("cells") or []],
        "tags_measured": tags.get("measured"),
    }


def _repo(repo: Mapping[str, Any], label: Mapping[str, str], rows: Mapping[str, int],
          titles: Mapping[str, str], mode: str) -> dict[str, Any]:
    nxt = repo.get("next_step")
    return {
        "key": label[repo["key"]], "status": repo.get("status"), "reason": repo.get("reason"),
        "web_url": repo.get("web_url") if mode == FULL else None,
        "step": repo.get("step"), "tally": repo.get("tally"),
        "next": None if not nxt else {"step": nxt["step"], "blockers": [
            {"gate": b.get("gate"), "state": b.get("state"), "reason": b.get("reason"),
             "excluded": b.get("disposition") == "excluded_by_owner",
             "condition": _condition_text(str(b.get("condition") or ""), titles)} for b in nxt["blockers"]]},
        "exclusions": [{"condition": _condition_text(e["id"], titles), "kind": e.get("kind"),
                        "reason": e.get("reason") if mode == FULL else None} for e in repo.get("exclusions") or []],
        "cells": [{"row": rows[c["row"]], "status": c["status"], "level": c["level"],
                   # the level the cell's signal stands before: fleet-data names the next level's criterion
                   "next_level": c["level"] + 1 if c["status"] == "level" and c.get("signal") else None,
                   "signal": c.get("signal"),
                   "value": c.get("value"), "reason": c.get("reason"), "why": c.get("why")}
                  for c in repo.get("rows") or [] if c["row"] in rows],
        "units": {u["view"]: label[u["unit"]] for u in repo.get("units") or [] if u["unit"] in label},
        "tags": [{k: c[k] for k in ("tag", "met", "measured", "not_measured")} for c in repo.get("tags") or []],
    }


def _achievement(a: Mapping[str, Any], label: Mapping[str, str], titles: Mapping[str, str],
                 tops: Mapping[str, int]) -> dict[str, Any]:
    """One achievement, worded as ``fleet_achievements._credit`` words it, with the page's labels."""
    ev = a.get("evidence") or {}
    who = label[a["unit"]]
    title = titles.get(ev.get("row") or "", "")
    kind = a["kind"]
    if kind == "level_up":
        line = f"{who} reached level {ev.get('to_level')} of {tops.get(ev.get('row') or '', '?')} on {title}."
    elif kind == "first":
        what = "repository" if a.get("unit_kind") == "repo" else a.get("unit_kind")
        line = f"A first for this fleet: {who} is the first {what} at level {ev.get('level')} on {title}."
    elif kind == "target_hit":
        line = (f"{who} reached its target of Step {ev.get('target_step')}: Step {ev.get('from_step')} "
                f"to Step {ev.get('to_step')}.")
    else:
        line = f"{who} held every level for {ev.get('runs')} runs in a row."
    return {"kind": kind, "view": a["view"], "unit": who, "run": a["run"], "text": line}


def page_bundle(data: Mapping[str, Any], achievements: Mapping[str, Any] | None, model: MaturityModel,
                pictures: Sequence[Picture] = (), front: Mapping[str, Any] | None = None) -> PageBundle:
    """The page's bundle (see the module docstring)."""
    mode = mode_of(data)
    label = labels(data, mode)
    team = [r for r in model["rows"] if r["scope"] == "team"]
    rows = {r["id"]: i for i, r in enumerate(team)}
    titles = {r["id"]: r["title"] for r in model["rows"]}
    tops = {r["id"]: len(r["levels"]) for r in model["rows"]}
    ach = achievements or {}
    recount = ach.get("recount")
    bundle: PageBundle = {
        "page_version": PAGE_VERSION, "mode": mode,
        "scan_date": (data.get("run") or {}).get("scan_date"),
        "levels_version": (data.get("run") or {}).get("levels_version"),
        "rows": [{"title": r["title"], "top_level": len(r["levels"])} for r in team],
        "steps": [{"step": s["step"], "name": s["name"], "gates": [g["id"] for g in s["gates"]]}
                  for s in sorted(model.get("steps", []), key=lambda s: s["step"])],
        "tags": [{"tag": tag, "title": title} for tag, title in TRUTH_TAG_TITLES.items()],
        "views": [{"view": v["view"], "units": [_unit(u, label) for u in v["units"]]} for v in data.get("views") or []],
        "repos": [_repo(r, label, rows, titles, mode) for r in data.get("repos") or []],
        "achievements": [_achievement(a, label, titles, tops) for a in ach.get("achievements") or []
                         if a.get("run") == ach.get("run") and a["unit"] in label],
        "runs": list(ach.get("runs") or []), "note": ach.get("note"),
        "recount": None if not recount else {
            "from_version": recount.get("from_version"), "to_version": recount.get("to_version"),
            "lines": [{"repo": label[line["repo"]], "row": titles.get(line["row"], ""),
                       "cause": line["cause"], "from_level": line.get("from_level"), "to_level": line.get("to_level")}
                      for line in recount.get("lines") or [] if line["repo"] in label]},
        "targets": [{"view": t["view"], "unit": label[t["unit"]], "step": t["step"],
                     "lowest_step": t.get("lowest_step"), "measured": t.get("measured"), "repos": t.get("repos"),
                     "distance": t.get("distance"), "held": t.get("held"),
                     "missing": [{k: g[k] for k in ("gate", "held", "not_measured", "excluded")}
                                 for g in t.get("missing_gates") or []]} for t in ach.get("targets") or []
                    if t["unit"] in label],
        "pictures": list(pictures),
        "front": _front(front, data, label),
    }
    if data.get("packs"):  # each pack's practices fleet-wide: names, titles and counts only
        bundle["packs"] = [{"name": p["name"], "version": p["version"], "repos": p["repos"],
                            "practices": [{k: q[k] for k in ("title", "level", "met", "measured", "not_measured")}
                                          for q in p["practices"]]} for p in data["packs"]]
    return bundle


def picture(file: str, title: str, question: str, svg: str) -> Picture:
    """A drawn SVG as the page embeds it: an image ``data:`` URI, so each keeps its own styles."""
    return {"file": file, "title": title, "question": question,
            "src": "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")}
