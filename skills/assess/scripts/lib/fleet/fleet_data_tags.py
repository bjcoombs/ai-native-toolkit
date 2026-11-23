"""The per-tag tally of ``fleet-data.json``: per repository and per unit (issue #1073, parent #740).

The fleet rolls up by truth tag, as the single-repository headline does, and no longer by layer.

**Per repository** (``repo_tags``): ``tags`` is the headline's per-tag strip
(``lib.headline.tag_cells``, #844) over the repository's level result
(``lib.fleet.fleet_level.repo_level``, the function ``level.json`` is written from), so it
equals the ``tags`` of that repository's own headline by construction. One cell per truth
tag a condition of the result carries, in ``TRUTH_TAGS`` order: ``met``, ``measured`` and
``verified`` count the counted conditions as the tally does, and ``not_measured``,
``hazards`` and ``declined`` are the NOT MEASURED, HAZARD and DECLINED counts. A condition
with several tags is in each of their cells, while the tally's N counts it once. ``tags``
is null when the repository was not measured: a failed scan, or a run without a maturity
model. Null means not measured, never zero.

**Per unit** (``tag_roll_up``), over every repository below the unit (#772 option A: no
average and no repository named): ``repos`` is every repository below, ``measured`` those
with a per-tag tally (an unmeasured one counts in ``repos`` only, never as zeros), and
``cells`` sums each count over the measured repositories, one cell per tag any of them
carries, in ``TRUTH_TAGS`` order. A unit with no measured repository has no cells.

**History** (``member_tags``, the reader the tag drawings of #940 use for stored runs): a
``fleet-data.json`` written before this field has no ``tags``; it reads as null, not measured,
never as a row of zeros. A stored tally with any malformed cell reads as null too, never as a
partial tally. ``fleet_rescore`` rebuilds
the field from the stored scans where the history allows a re-score.

**Privacy.** Tag names come from the fixed ``TRUTH_TAGS`` list and every other field is a
count, so the tally names no repository, path or owner (``FLEET_DATA_RULES``: the tag is a
token).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypedDict, cast

from lib.headline import tag_cells
from lib.level_result_types import LevelResult, TagCell
from lib.levels import MaturityModel
from lib.levels_fields import TRUTH_TAGS

COUNTS = ("met", "measured", "verified", "not_measured", "hazards", "declined")


class TagRollUp(TypedDict):
    measured: int  # repositories below with a per-tag tally
    repos: int  # every repository below, measured or not
    cells: list[TagCell]  # each count summed over the measured repositories, in TRUTH_TAGS order


def repo_tags(result: LevelResult | None, model: MaturityModel | None) -> list[TagCell] | None:
    """One repository's per-tag tally from its level result; None when it was not measured."""
    if result is None or model is None:
        return None
    return tag_cells(result, model)


def tag_roll_up(members: Sequence[list[TagCell] | None]) -> TagRollUp:
    """The per-tag tally over the repositories below a unit (see the module docstring)."""
    measured = [m for m in members if m is not None]
    sums: dict[str, TagCell] = {}
    for cells in measured:
        for cell in cells:
            total = sums.setdefault(cell["tag"], {"tag": cell["tag"], "met": 0, "measured": 0, "verified": 0,
                                                  "not_measured": 0, "hazards": 0, "declined": 0})
            total["met"] += cell["met"]
            total["measured"] += cell["measured"]
            total["verified"] += cell["verified"]
            total["not_measured"] += cell["not_measured"]
            total["hazards"] += cell["hazards"]
            total["declined"] += cell["declined"]
    return {"measured": len(measured), "repos": len(members),
            "cells": [sums[t] for t in TRUTH_TAGS if t in sums]}


def member_tags(repo: Mapping[str, Any]) -> list[TagCell] | None:
    """A stored repository's per-tag tally; None (not measured) for an entry written before #1073
    or one with any malformed cell."""
    tags = repo.get("tags")
    if not isinstance(tags, list) or not all(
            isinstance(cell, dict) and cell.get("tag") in TRUTH_TAGS
            and all(type(cell.get(count)) is int for count in COUNTS) for cell in tags):
        return None
    return [cast(TagCell, cell) for cell in tags]
