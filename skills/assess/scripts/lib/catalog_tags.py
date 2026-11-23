"""The truth-tag frame of the measures catalog: tags, shared conditions, Step gates and links (#870).

``lib.catalog`` builds one ``CatalogLevel`` per condition (``<row id>.<level>``) carrying its
``truth`` tags. This module groups those conditions by tag and states how the tags relate:

- ``tags``: one entry per tag in ``levels_fields.TRUTH_TAGS`` order, including a tag no
  condition carries yet. ``conditions`` lists every condition with the tag; ``listed`` the
  ones whose first tag it is, which the page shows in this tag's table, so each condition
  has one table row however many tags it carries (it counts once); ``shared`` the ones that
  carry another tag too.
- ``tag_pairs``: each pair of tags with the conditions both carry, in tag order, pairs with
  none left out.
- ``steps``: the model's autonomy Steps in order, each gate with the conditions it covers
  (its row's conditions 1..``level``) and their tags; ``added_gates`` names the gates new at
  that Step, since gates accumulate.
- ``links``: every ``requires``, ``guarded_by`` and ``eq_signal`` relation, in model order.
  ``source`` is a condition id (a criterion key for ``eq_signal``); ``target`` is a condition
  id (a signal path for ``eq_signal``).

Each tag carries its ``why`` and ``why_source`` from the check manifest's root ``tags`` table
(``lib.manifest_whys.tag_why``, #939; ``""`` from a version 1 manifest).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import combinations
from typing import Literal, TypedDict

from lib.levels import MaturityModel, condition_id
from lib.levels_fields import TRUTH_TAG_TITLES, TRUTH_TAGS

LinkKind = Literal["requires", "guarded_by", "eq_signal"]


class CatalogTag(TypedDict):
    id: str
    title: str
    conditions: list[str]  # every condition carrying the tag, in model order
    listed: list[str]  # those whose first tag this is: their table rows sit in this tag's section
    shared: list[str]  # those carrying another tag as well
    why: str  # what the tag keeps honest for an agent (#939)
    why_source: str


class TagPair(TypedDict):
    tags: list[str]  # two tags, in TRUTH_TAGS order
    conditions: list[str]  # the conditions carrying both


class CatalogGate(TypedDict):
    id: str
    row: str
    level: int
    conditions: list[str]  # the row's conditions 1..level, all of which must be met
    tags: list[str]  # the truth tags of those conditions, in TRUTH_TAGS order


class CatalogStep(TypedDict):
    step: int
    name: str
    gates: list[CatalogGate]  # the only thing a Step needs (#1083)
    added_gates: list[str]  # gate ids new at this Step


class CatalogLink(TypedDict):
    kind: LinkKind
    source: str
    target: str


class Tagged(TypedDict):
    """The fields of a catalog condition this module reads (``lib.catalog.CatalogLevel``)."""

    id: str
    truth: list[str]


def tag_title(tag: str) -> str:
    return TRUTH_TAG_TITLES.get(tag, tag)


def tags(conditions: Sequence[Tagged],
         whys: Mapping[str, tuple[str, str]] | None = None) -> list[CatalogTag]:
    """One entry per truth tag, each condition listed under its first tag; ``whys`` maps a tag
    to its ``(why, why_source)``."""
    out: list[CatalogTag] = []
    for tag in TRUTH_TAGS:
        having = [c for c in conditions if tag in c["truth"]]
        out.append(CatalogTag(id=tag, title=tag_title(tag), conditions=[c["id"] for c in having],
                              listed=[c["id"] for c in having if c["truth"][0] == tag],
                              shared=[c["id"] for c in having if len(c["truth"]) > 1],
                              why=(whys or {}).get(tag, ("", ""))[0],
                              why_source=(whys or {}).get(tag, ("", ""))[1]))
    return out


def tag_pairs(conditions: Sequence[Tagged]) -> list[TagPair]:
    """Each pair of tags that share a condition, with the conditions they share."""
    out: list[TagPair] = []
    for a, b in combinations(TRUTH_TAGS, 2):
        both = [c["id"] for c in conditions if a in c["truth"] and b in c["truth"]]
        if both:
            out.append(TagPair(tags=[a, b], conditions=both))
    return out


def steps(model: MaturityModel, truth: Mapping[str, list[str]]) -> list[CatalogStep]:
    """The Steps in order; ``truth`` maps each condition id to its tags."""
    out: list[CatalogStep] = []
    seen: set[str] = set()
    for step in model.get("steps", []):
        gates: list[CatalogGate] = []
        for gate in step["gates"]:
            covered = [condition_id(gate["row"], n) for n in range(1, gate["level"] + 1)]
            held = {t for cid in covered for t in truth.get(cid, [])}
            gates.append(CatalogGate(id=gate["id"], row=gate["row"], level=gate["level"], conditions=covered,
                                     tags=[t for t in TRUTH_TAGS if t in held]))
        added = [g["id"] for g in gates if g["id"] not in seen]
        seen.update(added)
        out.append(CatalogStep(step=step["step"], name=step["name"], gates=gates, added_gates=added))
    return out


def gates_by_condition(catalog_steps: Sequence[CatalogStep]) -> dict[str, list[tuple[str, int]]]:
    """Each gated condition id to its gates, as ``(gate id, first Step)`` in Step order."""
    out: dict[str, list[tuple[str, int]]] = {}
    for step in catalog_steps:
        for gate in step["gates"]:
            if gate["id"] not in step["added_gates"]:
                continue
            for cid in gate["conditions"]:
                out.setdefault(cid, []).append((gate["id"], step["step"]))
    return out


def links(model: MaturityModel) -> list[CatalogLink]:
    """Every ``requires``, ``guarded_by`` and ``eq_signal`` relation, in model order."""
    out: list[CatalogLink] = []
    for row in model["rows"]:
        for level in row["levels"]:
            cid = condition_id(row["id"], level["level"])
            out += [CatalogLink(kind="requires", source=cid, target=r) for r in level.get("requires", [])]
            if "guarded_by" in level:
                out.append(CatalogLink(kind="guarded_by", source=cid, target=level["guarded_by"]))
            out += [CatalogLink(kind="eq_signal", source=f"{cid}:{c['signal']}", target=c["eq_signal"])
                    for c in level["criteria"] if "eq_signal" in c]
    return out
