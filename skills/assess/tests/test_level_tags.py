"""The level result's per-tag rows (``lib/level_tags.py``, #940) and the one tally they share with the headline strip."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from lib.headline import headline, tag_cells as headline_tag_cells
from lib.level_result_types import LevelResult
from lib.level_tags import tag_cells, tag_rows
from lib.level_tally import default_model, dumps_level, level_result, run_context_level
from lib.levels import MaturityModel
from lib.levels_fields import TRUTH_TAGS

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden"


def _cond(cid: str, state: str, grade: str = "measured", counted: bool = True, **extra: Any) -> dict[str, Any]:
    row, lv = cid.rsplit(".", 1)
    return {"id": cid, "row": row, "level": int(lv), "grade": grade, "counted": counted, "state": state, **extra}


def _model() -> MaturityModel:
    level = lambda n, truth, **x: {"level": n, "criteria": [], "truth": truth, **x}  # noqa: E731
    return cast(MaturityModel, {"version": "t-1", "rows": [
        {"id": "r", "title": "R", "scope": "repo", "aggregation": None,
         "levels": [level(1, ["behaviour", "docs"]), level(2, ["behaviour"]), level(3, ["docs"])]},
        {"id": "s", "title": "S", "scope": "repo", "aggregation": None,
         "levels": [level(1, ["supply_chain"]), level(2, ["governance"]), level(3, ["governance"])]},
    ], "steps": []})


def _level() -> LevelResult:
    return cast(LevelResult, {
        "level_result_version": 3, "levels_version": "t-1",
        "tally": {"met": 2, "measured": 3, "not_measured": 1}, "counted": [], "listed": [],
        "not_measured": [{"id": "r.3", "reason": "no_remote"}],
        "conditions": [_cond("r.1", "met", grade="verified"), _cond("r.2", "unmet"),
                       _cond("r.3", "not_measured", reason="no_remote"),
                       _cond("s.1", "met", grade="declared", counted=False),
                       _cond("s.2", "not_applicable", counted=False, disposition="excluded_by_owner"),
                       _cond("s.3", "met")],
        "step": None, "next_step": None})


def test_every_tag_has_a_row_in_the_fixed_order() -> None:
    rows = tag_rows(_level(), _model())
    assert [r["tag"] for r in rows] == list(TRUTH_TAGS)
    assert all(r == {"tag": r["tag"], "conditions": 0, "countable": 0, "set_aside": 0, "met": 0, "measured": 0,
                     "not_measured": []}
               for r in rows if r["tag"] in ("contracts", "structure", "agent_context", "agent_runtime"))


def test_a_multi_tagged_condition_is_on_each_row_and_the_tally_is_unchanged() -> None:
    rows = {r["tag"]: r for r in tag_rows(_level(), _model())}
    # r.1 carries behaviour and docs: met on both rows
    assert rows["behaviour"] == {"tag": "behaviour", "conditions": 2, "countable": 2, "set_aside": 0, "met": 1,
                               "measured": 2, "not_measured": []}
    assert rows["docs"] == {"tag": "docs", "conditions": 2, "countable": 2, "set_aside": 0, "met": 1, "measured": 1,
                            "not_measured": [{"id": "r.3", "reason": "no_remote"}]}
    assert sum(r["met"] for r in rows.values()) == 3  # r.1 twice, s.3 once
    assert rows["supply_chain"]["countable"] == 0 and rows["supply_chain"]["measured"] == 0
    # s.2 is excluded by the owner: countable by grade, set aside
    assert rows["governance"]["countable"] == 2 and rows["governance"]["set_aside"] == 1
    assert rows["governance"]["met"] == 1


def test_a_multi_tagged_condition_leaves_the_real_tally_unchanged() -> None:
    """Through ``level_result``: adding a second tag moves the rows, never ``tally``."""
    model = default_model()
    ctx = json.loads((GOLDEN / "run-context-baseline.json").read_text(encoding="utf-8"))
    before = level_result(model, ctx)
    tagged = json.loads(json.dumps(model))
    first = tagged["rows"][0]["levels"][0]
    first["truth"] = [*first["truth"], *(t for t in ("contracts",) if t not in first["truth"])]
    after = level_result(cast(MaturityModel, tagged), ctx)
    assert after["tally"] == before["tally"]
    rows = {r["tag"]: r for r in after["tags"]}
    assert rows["contracts"]["conditions"] == 1


def test_met_and_measured_are_the_headline_strip_cells() -> None:
    """One function: every row with a condition equals the #844 strip's cell."""
    level, model = _level(), _model()
    cells = {c["tag"]: c for c in headline(level, model)["tags"]}
    for row in tag_rows(level, model):
        cell = cells.get(row["tag"])
        if cell is None:
            assert row["conditions"] == 0
        else:
            assert (row["met"], row["measured"]) == (cell["met"], cell["measured"])
    assert headline_tag_cells is tag_cells


@pytest.mark.parametrize("name", sorted(p.name for p in GOLDEN.glob("run-context-*.json")))
def test_corpus_rows_equal_the_strip_cells(name: str) -> None:
    ctx = json.loads((GOLDEN / name).read_text(encoding="utf-8"))
    level = run_context_level(ctx)
    cells = {c["tag"]: c for c in headline(level, default_model())["tags"]}
    assert [r["tag"] for r in level["tags"]] == list(TRUTH_TAGS)
    for row in level["tags"]:
        cell = cells.get(row["tag"], {"met": 0, "measured": 0})
        assert (row["met"], row["measured"]) == (cell["met"], cell["measured"])
        assert row["conditions"] >= row["countable"] >= row["measured"] + row["set_aside"]
        assert row["measured"] >= row["met"]


def test_contracts_has_no_condition_in_the_shipped_model() -> None:
    rows = {r["tag"]: r for r in level_result(default_model(), {})["tags"]}
    assert rows["contracts"]["conditions"] == 0


def test_rows_are_byte_identical_on_rerun_and_survive_counts_only() -> None:
    from lib.counts_only import counts_only

    model = default_model()
    a, b = level_result(model, {}), level_result(model, {})
    assert dumps_level(a) == dumps_level(b)
    kept = counts_only({"level": a})["level"]
    assert kept["tags"] == a["tags"]  # fails if a field of a tag row is dropped
