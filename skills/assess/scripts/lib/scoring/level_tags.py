"""The per-tag tally of one level result: the headline's strip cells and the level's tag rows (#844, #940).

One function counts a level result by truth tag, ``tag_cells``: one ``TagCell`` per tag a
condition of the result carries, in ``TRUTH_TAGS`` order, counted like the tally (``met`` and
``measured`` over the counted conditions, ``verified`` of the met ones) with the NOT MEASURED,
HAZARD and DECLINED counts. A condition with several tags is in each of their cells, while
the tally counts it once. The headline's strip (``lib/scoring/headline.py``) and the fleet's per-tag
tally (``lib/fleet/fleet_data_tags.py``, #1073) read it; ``lib.scoring.headline`` re-exports it.

``tag_rows`` is the level result's ``tags`` (#940): one ``TagRow`` per tag of ``TRUTH_TAGS``,
always all of them in that fixed order, so two profiles always compare row for row. ``met``
and ``measured`` are copied from ``tag_cells``, never counted a second time; ``conditions``
is the number of the model's conditions with the tag (0 for a tag no condition carries yet,
such as ``contracts``), ``countable`` those the tally can count by grade (``measured`` or
``verified``, not a hazard; ``COUNTABLE_GRADES`` is ``level_tally.COUNTED_GRADES``), ``set_aside``
the countable ones the owner excluded, or that require one the owner excluded (both read
``not_applicable``), or that were declined, and
``not_measured`` lists each condition with the
tag that was not measured, with its reason code (``{id, reason}``, as the result's own
``not_measured``), and ``countable_not_measured`` the countable ones among them: a countable
condition is measured, not measured or set aside, so when ``measured`` is 0 these say why the
row has no share (a listed condition's reason never does). A row with ``measured`` 0 is not measured, never a share of 0: the tag
profile strip (``lib/assess_diagrams_tags.py``) draws it dashed with its reason.

Every value is a tag token, a condition id, a reason code or a count, so both survive
counts-only output. Tests: ``tests/test_level_tags.py``.
"""
from __future__ import annotations

from lib.scoring.level_result_types import LevelResult, TagCell, TagRow
from lib.scoring.levels import MaturityModel
from lib.scoring.levels_fields import TRUTH_TAGS

__all__ = ["COUNTABLE_GRADES", "conditions_label", "tag_cells", "tag_rows", "truth_map"]

# The grades the tally counts (``lib.scoring.level_tally.COUNTED_GRADES``; kept here, below the engine,
# so the engine can import this module; a test holds the two equal).
COUNTABLE_GRADES = frozenset({"measured", "verified"})


def conditions_label(n: int) -> str:
    """``n`` with its unit, for a tag row label: ``1 condition``, ``3 conditions``."""
    return f"{n} condition" if n == 1 else f"{n} conditions"


def truth_map(model: MaturityModel) -> dict[str, list[str]]:
    """Each condition id of the model and its truth tags."""
    return {f"{row['id']}.{lv['level']}": lv.get("truth", [])
            for row in model["rows"] for lv in row["levels"]}


def tag_cells(level: LevelResult, model: MaturityModel) -> list[TagCell]:
    """One cell per truth tag a condition in the result carries, in ``TRUTH_TAGS`` order."""
    truth = truth_map(model)
    cells: dict[str, TagCell] = {}
    for c in level["conditions"]:
        for tag in truth.get(c["id"], []):
            cell = cells.setdefault(tag, {"tag": tag, "met": 0, "measured": 0, "verified": 0,
                                          "not_measured": 0, "hazards": 0, "declined": 0})
            state = c["state"]
            if c["counted"] and state in ("met", "unmet"):
                cell["measured"] += 1
                if state == "met":
                    cell["met"] += 1
                    cell["verified"] += c["grade"] == "verified"
            cell["not_measured"] += state == "not_measured"
            cell["hazards"] += c.get("polarity") == "hazard" and state == "met"
            cell["declined"] += state == "declined"
    return [cells[t] for t in TRUTH_TAGS if t in cells]  # the model load admits no other tag


def tag_rows(level: LevelResult, model: MaturityModel) -> list[TagRow]:
    """Every truth tag in ``TRUTH_TAGS`` order, with ``met`` and ``measured`` from ``tag_cells``."""
    truth = truth_map(model)
    cells = {cell["tag"]: cell for cell in tag_cells(level, model)}
    rows: list[TagRow] = []
    for tag in TRUTH_TAGS:
        mine = [c for c in level["conditions"] if tag in truth.get(c["id"], [])]
        countable = [c for c in mine if c["grade"] in COUNTABLE_GRADES and c.get("polarity") != "hazard"]
        cell = cells.get(tag)
        rows.append({
            "tag": tag,
            "conditions": len(mine),
            "countable": len(countable),
            "set_aside": sum(c["state"] in ("not_applicable", "declined") for c in countable),
            "met": cell["met"] if cell else 0,
            "measured": cell["measured"] if cell else 0,
            "not_measured": [{"id": c["id"], "reason": c.get("reason", "no_value")}
                             for c in mine if c["state"] == "not_measured"],
            "countable_not_measured": [{"id": c["id"], "reason": c.get("reason", "no_value")}
                                       for c in countable if c["state"] == "not_measured"],
        })
    return rows
