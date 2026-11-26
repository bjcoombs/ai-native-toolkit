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


def _chain(stale: int = 0, exclusions: tuple[dict[str, Any], ...] = ()) -> lt.LevelResult:
    model = parse_levels({"version": "fixture-1407", "rows": [
        {"id": "stated", "title": "stated", "scope": "team", "aggregation": "min",
         "levels": [{"level": 1, "criteria": GTE}]},
        {"id": "fresh", "title": "fresh", "scope": "team", "aggregation": "min",
         "levels": [{"level": 1, "requires": ["stated.1"],
                     "criteria": [{"signal": DOC, "op": "lte", "value": 10}]}]}],
        "steps": [{"step": 1, "name": "Assisted"},
                  {"step": 2, "name": "Gated", "gates": [{"id": "fresh", "row": "fresh", "level": 1}]}]})
    return lt.level_result(model, _block(None, NO_GATES_REASON, stale=stale), exclusions)


def test_a_dependent_of_a_scan_not_applicable_reads_as_the_scans_not_the_owners():
    result = _chain()
    by_id = {c["id"]: c for c in result["conditions"]}
    assert lt.scan_not_applicable(by_id["fresh.1"], by_id) == NO_GATES_REASON
    assert gate_state(result, {"id": "fresh", "row": "fresh", "level": 1}) == "not applicable"
    assert result["step"] == 1


def test_a_dependent_of_an_owner_exclusion_still_reads_excluded_by_owner():
    result = _chain(exclusions=({"id": "stated.1", "kind": "not_applicable"},))
    by_id = {c["id"]: c for c in result["conditions"]}
    assert lt.scan_not_applicable(by_id["fresh.1"], by_id) is None
    assert lt.scan_not_applicable(by_id["stated.1"], by_id) is None
    assert gate_state(result, {"id": "fresh", "row": "fresh", "level": 1}) == "excluded by owner"


def test_scan_not_applicable_is_none_for_other_states():
    result = _chain()
    by_id: dict[str, Any] = {c["id"]: c for c in result["conditions"]}
    assert lt.scan_not_applicable({"state": "not_measured", "reason": NO_GATES_REASON}, by_id) is None
    assert lt.scan_not_applicable({"state": "not_applicable", "reason": "requires:gone.1"}, by_id) is None


def test_gate_note_words_only_a_scans_own_code():
    owner_chain = {"kind": "gate", "gate": "g", "condition": "fresh.1", "state": "not_applicable",
                   "reason": "requires:stated.1"}
    assert gate_note(owner_chain) == ""
    direct = {**owner_chain, "reason": NO_GATES_REASON}
    assert "not applicable here" in gate_note(direct)
