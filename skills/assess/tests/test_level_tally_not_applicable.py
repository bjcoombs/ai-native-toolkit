"""#1407: a sibling ``<leaf>_reason`` in ``level_tally.NOT_APPLICABLE_REASONS`` reads the condition
``not_applicable`` (the signal does not apply here), any other code ``not_measured`` (the scan
could not read it). A not-applicable condition is never counted and never holds a gate."""
from __future__ import annotations

from typing import Any

import pytest

import lib.scoring.level_tally as lt
from lib.assess_diagrams_ladder import gate_state
from lib.instructions.agent_consistency import NOT_APPLICABLE_REASON
from lib.instructions.guardrails import NO_GATES_REASON
from lib.scoring.headline import gate_note
from lib.scoring.levels import MaturityModel, parse_levels

SIG = "guardrails.stated_pct"  # head_structure: counted
DOC = "doc_staleness.summary.stale_pct"  # git_history: counted


def _model(*criteria: list[dict[str, Any]], steps: bool = False) -> MaturityModel:
    data: dict[str, Any] = {"version": "fixture-1407", "rows": [
        {"id": "stated", "title": "stated", "scope": "team", "aggregation": "min",
         "levels": [{"level": n, "criteria": c} for n, c in enumerate(criteria, 1)]}]}
    if steps:
        data["steps"] = [{"step": 1, "name": "Assisted"},
                         {"step": 2, "name": "Gated", "gates": [{"id": "stated", "row": "stated", "level": 1}]}]
    return parse_levels(data)


def _block(pct: float | None, reason: str | None = None, stale: int | None = None) -> dict[str, Any]:
    guard: dict[str, Any] = {"available": True, "stated_pct": pct}
    if reason is not None:
        guard["stated_pct_reason"] = reason
    out: dict[str, Any] = {"guardrails": guard}
    if stale is not None:
        out["doc_staleness"] = {"available": True, "summary": {"stale_pct": stale}}
    return out


def _cond(result: lt.LevelResult, cid: str = "stated.1") -> Any:
    return next(c for c in result["conditions"] if c["id"] == cid)


GTE = [{"signal": SIG, "op": "gte", "value": 100}]


def test_the_reason_set_names_the_scans_own_codes():
    assert lt.NOT_APPLICABLE_REASONS == {NOT_APPLICABLE_REASON, NO_GATES_REASON}


@pytest.mark.parametrize("reason", sorted(lt.NOT_APPLICABLE_REASONS))
def test_a_not_applicable_reason_reads_not_applicable_and_is_not_counted(reason):
    result = lt.level_result(_model(GTE), _block(None, reason))
    cond = _cond(result)
    assert (cond["state"], cond["reason"], cond["counted"]) == ("not_applicable", reason, False)
    assert "disposition" not in cond
    assert result["tally"] == {"met": 0, "measured": 0, "not_measured": 0}
    assert result["not_measured"] == [] and result["counted"] == [] and result["listed"] == []


@pytest.mark.parametrize("block, reason", [
    (_block(None), "no_value"),                                  # no sibling code
    (_block(None, "managed_settings_unreadable"), "managed_settings_unreadable"),  # a read failure
    ({"guardrails": {"available": False, "reason": "not_registered"}}, "needs_full_run"),
])
def test_any_other_reason_stays_not_measured(block, reason):
    cond = _cond(lt.level_result(_model(GTE), block))
    assert (cond["state"], cond["reason"], cond["counted"]) == ("not_measured", reason, True)


def test_a_real_unmet_sibling_criterion_still_reads_unmet():
    model = _model(GTE + [{"signal": DOC, "op": "lte", "value": 10}])
    cond = _cond(lt.level_result(model, _block(None, NO_GATES_REASON, stale=50)))
    assert cond["state"] == "unmet"


def test_a_not_applicable_condition_never_holds_its_gate():
    """The gate reads met only: not applicable is not a pass, so the Step stays unreached."""
    model = _model(GTE, steps=True)
    result = lt.level_result(model, _block(None, NO_GATES_REASON))
    assert result["step"] == 1
    assert result["next_step"] == {"step": 2, "blockers": [
        {"kind": "gate", "gate": "stated", "condition": "stated.1", "state": "not_applicable",
         "reason": NO_GATES_REASON}]}
    assert result["held_above"] == []
    assert result["in_place"] == []
    gate = model["steps"][1]["gates"][0]
    assert gate_state(result, gate) == "not applicable"
    note = gate_note(result["next_step"]["blockers"][0])
    assert "not applicable here" in note and "excluded by owner" not in note
    # The same gate holds once the signal is measured and met.
    assert lt.level_result(model, _block(100.0))["step"] == 2


def test_a_not_applicable_condition_never_unlocks_a_dependent():
    model = parse_levels({"version": "fixture-1407", "rows": [
        {"id": "stated", "title": "stated", "scope": "team", "aggregation": "min",
         "levels": [{"level": 1, "criteria": GTE}]},
        {"id": "fresh", "title": "fresh", "scope": "team", "aggregation": "min",
         "levels": [{"level": 1, "requires": ["stated.1"],
                     "criteria": [{"signal": DOC, "op": "lte", "value": 10}]}]}]})
    result = lt.level_result(model, _block(None, NO_GATES_REASON, stale=0))
    cond = _cond(result, "fresh.1")
    assert (cond["state"], cond["reason"]) == ("not_applicable", "requires:stated.1")
    assert result["tally"]["met"] == 0
