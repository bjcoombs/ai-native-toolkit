"""Declared opt-outs and Step-blocking hazards in the level result (#828).

A decline record (the agent-level marker ``.assess/agents-declined.json``) reads the conditions it
covers as ``declined``: never met, never counted, never holding a Step gate. A hazard that names
``blocks`` holds that Step gate while it is met, is listed apart from the tally with its fix path,
and never moves the tally.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import lib.scoring.level_tally as lt
from lib.counts_only.content_free import counts_only
from lib.owner_exclusions import owner_exclusions_block
from lib.run.decline_markers import AGENT_DECLINE, AGENTS_MARKER, decline_badge, read_agent_decline
from lib.scoring.headline import badge_lines, gate_note, headline
from lib.scoring.level_result_types import DeclineRecord
from lib.scoring.levels import LevelsError, MaturityModel, parse_levels

DOC = "doc_staleness.summary.stale_pct"  # git_history: counted
INSTR = "layer_grades.instruction_file_present"
CI = "ci_checks.enforced_check_runs"
INSTRUCTIONS = "layer-0-instructions.1"


def _record(**over: Any) -> DeclineRecord:
    record: DeclineRecord = {"source": AGENTS_MARKER, "conditions": None, "declined_by": "maintainer-a",
                             "declined_at": "2026-11-20", "version": "3.0.0", "reason": "No agent contributions."}
    record.update(over)  # type: ignore[typeddict-item]
    return record


def _marker(repo: Path, payload: object) -> Path:
    assess = repo / ".assess"
    assess.mkdir(parents=True, exist_ok=True)
    path = assess / AGENT_DECLINE
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return assess


def _cond(result: lt.LevelResult, cid: str) -> lt.ConditionResult:
    return next(c for c in result["conditions"] if c["id"] == cid)


HELD = {"layer_grades": {"instruction_file_present": True}, "ci_checks": {"enforced_check_runs": True}}


# --- the agent-level marker ---------------------------------------------------------------


def test_no_marker_file_reads_no_record(tmp_path: Path) -> None:
    assert read_agent_decline(tmp_path / ".assess") is None
    assert read_agent_decline(_marker(tmp_path, {"declined_by": "x"}).parent / "elsewhere") is None


def test_the_marker_records_who_when_version_reason_and_scope(tmp_path: Path) -> None:
    assess = _marker(tmp_path, {"declined_by": "maintainer-a", "declined_at": "2026-11-20",
                                "plugin_version": "3.0.0", "reason": "No agent contributions.",
                                "conditions": [INSTRUCTIONS, 7, "", INSTRUCTIONS]})
    assert read_agent_decline(assess) == _record(conditions=[INSTRUCTIONS])


@pytest.mark.parametrize("payload", ["", "not json", "[1, 2]"])
def test_a_present_marker_without_provenance_still_declines(tmp_path: Path, payload: str) -> None:
    assert read_agent_decline(_marker(tmp_path, payload)) == {
        "source": AGENTS_MARKER, "conditions": None, "declined_by": None, "declined_at": None,
        "version": None, "reason": None}


def test_the_owner_exclusions_block_carries_the_record_only_when_present(tmp_path: Path) -> None:
    assert "declines" not in owner_exclusions_block(tmp_path)
    _marker(tmp_path, {"declined_by": "maintainer-a", "reason": "No agent contributions."})
    (record,) = owner_exclusions_block(tmp_path)["declines"]
    assert (record["declined_by"], record["reason"]) == ("maintainer-a", "No agent contributions.")


def test_counts_only_keeps_the_scope_and_drops_the_owner_and_reason(tmp_path: Path) -> None:
    _marker(tmp_path, {"declined_by": "maintainer-a", "declined_at": "2026-11-20", "reason": "Private words.",
                       "conditions": [INSTRUCTIONS]})
    (kept,) = counts_only({"owner_exclusions": owner_exclusions_block(tmp_path)})["owner_exclusions"]["declines"]
    assert kept["conditions"] == [INSTRUCTIONS] and kept["declined_at"] == "2026-11-20"
    assert "maintainer-a" not in json.dumps(kept) and "Private words." not in json.dumps(kept)


@pytest.mark.parametrize("written", [INSTRUCTIONS, [123], [], {"a": 1}])
def test_a_malformed_scope_never_widens_to_every_agent_condition(tmp_path: Path, written: object) -> None:
    record = read_agent_decline(_marker(tmp_path, {"conditions": written}))
    assert record is not None and record["conditions"] == []
    result = lt.level_result(lt.default_model(), HELD, declines=[record])
    assert "declined" not in result and result["step"] == 3


# --- declined in the level result ---------------------------------------------------------


def test_a_declined_gate_condition_is_never_met_counted_or_holding_its_gate() -> None:
    model = lt.default_model()
    before = lt.level_result(model, HELD)
    after = lt.level_result(model, HELD, declines=[_record()])
    cond = _cond(after, INSTRUCTIONS)
    assert (cond["state"], cond["counted"]) == ("declined", False)
    assert before["step"] == 3 and after["step"] == 1
    assert after["next_step"] is not None
    (blocker,) = after["next_step"]["blockers"]
    assert (blocker["gate"], blocker["condition"], blocker["state"]) == ("instructions", INSTRUCTIONS, "declined")
    assert {"id": INSTRUCTIONS, "source": AGENTS_MARKER} in after["declined"]
    assert after["tally"]["measured"] <= before["tally"]["measured"]
    assert after["tally"]["met"] <= before["tally"]["met"]


def test_the_default_scope_is_the_positive_agent_conditions_and_never_a_hazard() -> None:
    model = lt.default_model()
    declined = lt.declined_ids(model, [_record()])
    tagged = {lt.condition_id(r["id"], lv["level"]) for r in model["rows"] for lv in r["levels"]
              if lt.AGENT_TAGS & set(lv["truth"]) and lv.get("polarity") != "hazard"}
    assert set(declined) == tagged and INSTRUCTIONS in declined
    assert "agent-job-write-access.1" not in declined
    assert "layer-5-ci.1" not in declined  # behaviour only


def test_a_listed_scope_declines_only_those_conditions() -> None:
    result = lt.level_result(lt.default_model(), HELD, declines=[_record(source="detector", conditions=["layer-5-ci.1", "nope.9"])])
    assert result["declined"] == [{"id": "layer-5-ci.1", "source": "detector"}]
    assert _cond(result, INSTRUCTIONS)["state"] == "met" and result["step"] == 2


def test_the_owner_exclusion_wins_over_a_decline() -> None:
    result = lt.level_result(lt.default_model(), HELD, [{"id": INSTRUCTIONS, "kind": "other"}],
                             declines=[_record(conditions=[INSTRUCTIONS])])
    assert _cond(result, INSTRUCTIONS)["state"] == "not_applicable" and "declined" not in result


def test_a_condition_that_requires_a_declined_one_reads_declined() -> None:
    model = parse_levels({"version": "t-1", "rows": [
        {"id": "a", "title": "a", "scope": "team", "aggregation": "min",
         "levels": [{"level": 1, "truth": ["docs"], "criteria": [{"signal": DOC, "op": "lte", "value": 50}]}]},
        {"id": "b", "title": "b", "scope": "team", "aggregation": "min",
         "levels": [{"level": 1, "truth": ["docs"], "requires": ["a.1"],
                     "criteria": [{"signal": DOC, "op": "lte", "value": 50}]}]}]})
    result = lt.level_result(model, {"doc_staleness": {"summary": {"stale_pct": 0}}},
                             declines=[_record(source="detector", conditions=["a.1"])])
    assert (_cond(result, "b.1")["state"], _cond(result, "b.1")["reason"]) == ("declined", "requires:a.1")
    assert _cond(result, "b.1")["counted"] is False
    assert result["tally"]["met"] == 0 and result["declined"] == [{"id": "a.1", "source": "detector"}]


def test_declines_are_read_from_the_run_context_when_not_passed() -> None:
    ctx = {**HELD, "owner_exclusions": {"exclusions": [], "skipped": [], "disclosures": [], "declines": [_record()]}}
    assert lt.run_context_level(ctx)["step"] == 1
    assert lt.run_declines({"owner_exclusions": {"exclusions": []}}) == []


# --- hazards that block a Step gate --------------------------------------------------------


def _hazard_model(blocks: str | None = "ci_runs_tests", hazard: bool = True) -> MaturityModel:
    level: dict[str, Any] = {"level": 1, "truth": ["behaviour"], "criteria": [{"signal": DOC, "op": "lte", "value": 50}]}
    if hazard:
        level["polarity"] = "hazard"
    if blocks is not None:
        level["blocks"] = blocks
    row = lambda rid, signal: {"id": rid, "title": rid, "scope": "team", "aggregation": "min",  # noqa: E731
                               "levels": [{"level": 1, "truth": ["behaviour"],
                                           "criteria": [{"signal": signal, "op": "eq", "value": True}]}]}
    return parse_levels({"version": "t-1", "rows": [
        row("instr", INSTR), row("ci", CI),
        {"id": "skip", "title": "Checks skipped", "scope": "team", "aggregation": "min", "levels": [level]}],
        "steps": [{"step": 1, "name": "One"},
                  {"step": 2, "name": "Two", "gates": [{"id": "instructions", "row": "instr", "level": 1}]},
                  {"step": 3, "name": "Three", "gates": [{"id": "instructions", "row": "instr", "level": 1},
                                                         {"id": "ci_runs_tests", "row": "ci", "level": 1}]}]})


def _signals(stale: int | None) -> dict[str, Any]:
    out: dict[str, Any] = dict(HELD)
    if stale is not None:
        out["doc_staleness"] = {"available": True, "summary": {"stale_pct": stale}}
    return out


def test_a_met_hazard_holds_its_gate_and_leaves_the_tally_unchanged() -> None:
    clear, present = lt.level_result(_hazard_model(), _signals(90)), lt.level_result(_hazard_model(), _signals(10))
    assert clear["step"] == 3 and present["step"] == 2
    assert present["tally"] == clear["tally"]
    assert present["next_step"] == {"step": 3, "blockers": [
        {"kind": "gate", "gate": "ci_runs_tests", "condition": "skip.1", "state": "met", "polarity": "hazard"}]}
    assert present["hazards"] == [{"id": "skip.1", "fix": "", "gate": "ci_runs_tests"}]
    assert "hazards" not in clear
    assert _cond(present, "skip.1")["blocks"] == "ci_runs_tests"
    assert lt.gate_blockers(_hazard_model()["steps"], present["conditions"])[0]["condition"] == "skip.1"
    assert present["ceiling_step"] == 3 and present["held_above"] == []


def test_a_hazard_not_measured_blocks_nothing() -> None:
    result = lt.level_result(_hazard_model(), _signals(None))
    assert _cond(result, "skip.1")["state"] == "not_measured" and result["step"] == 3 and "hazards" not in result


def test_an_unmet_gate_condition_stays_the_one_blocker_and_the_hazard_is_still_listed() -> None:
    signals = {**_signals(10), "ci_checks": {"enforced_check_runs": False}}
    result = lt.level_result(_hazard_model(), signals)
    assert result["next_step"] is not None
    assert [b["condition"] for b in result["next_step"]["blockers"]] == ["ci.1"]
    assert result["hazards"] == [{"id": "skip.1", "fix": "", "gate": "ci_runs_tests"}]


def test_a_hazard_without_blocks_is_listed_and_moves_no_step() -> None:
    result = lt.level_result(_hazard_model(blocks=None), _signals(10))
    assert result["step"] == 3 and result["hazards"] == [{"id": "skip.1", "fix": ""}]


@pytest.mark.parametrize(("blocks", "hazard", "message"), [
    ("ci_runs_tests", False, "'blocks' belongs to a hazard"),
    ("no_such_gate", True, "'blocks' names no Step gate"),
    ("", True, "'blocks' must be one Step gate id"),
])
def test_a_bad_blocks_fails_the_load(blocks: str, hazard: bool, message: str) -> None:
    with pytest.raises(LevelsError, match=message):
        _hazard_model(blocks=blocks, hazard=hazard)


def test_the_default_models_hazard_carries_its_manifest_fix_path() -> None:
    """#849's hazard fits the list: met, it is listed with the manifest's fix, and gates nothing."""
    data = {**HELD, "agent_ops": {"available": True, "summary": {"agent_job_write_access": 1}}}
    before = lt.level_result(lt.default_model(), HELD)
    result = lt.level_result(lt.default_model(), data)
    (hazard,) = result["hazards"]
    assert hazard["id"] == "agent-job-write-access.1" and "gate" not in hazard
    assert "permissions:" in hazard["fix"]
    assert result["step"] == before["step"] and result["tally"]["met"] == before["tally"]["met"]
    assert counts_only({"level": result})["level"]["hazards"] == [{"id": "agent-job-write-access.1"}]


# --- the headline badges --------------------------------------------------------------------


def test_the_headline_shows_declined_with_its_owner_and_reason_and_the_step_stays_unpassed(tmp_path: Path) -> None:
    record = read_agent_decline(_marker(tmp_path, {"declined_by": "maintainer-a", "declined_at": "2026-11-20",
                                                   "reason": "No agent contributions."}))
    assert record is not None
    level = lt.level_result(lt.default_model(), HELD, declines=[record])
    assert _cond(level, INSTRUCTIONS)["state"] == "declined" and level["step"] == 1
    h = headline(level, lt.default_model(), [record])
    n = len(level["declined"])
    assert h["lines"][0] == (f"DECLINED: {n} conditions · agents declined by maintainer-a on 2026-11-20: "
                             "No agent contributions. · a declined condition never holds a Step gate")
    assert h["summary"].startswith("Step 1")
    assert decline_badge(_record(declined_by=None, declined_at=None, reason=None)) == (
        "agents declined by an unknown user: no reason recorded")


def test_the_headline_lists_each_hazard_with_its_gate_and_fix() -> None:
    level = lt.level_result(_hazard_model(), _signals(10))
    assert badge_lines(level) == ["HAZARD: skip.1 undermines the CI runs tests gate. Fix: no fix recorded in the check manifest"]
    assert gate_note(level["next_step"]["blockers"][0]) == " (held by the hazard skip.1; see HAZARD)"  # type: ignore[index]
    assert gate_note({"kind": "gate", "gate": "instructions", "state": "declined"}).startswith(" (declined")
    assert badge_lines(lt.level_result(_hazard_model(), _signals(90))) == []


def test_a_declined_need_never_hides_a_met_hazard() -> None:
    model = parse_levels({"version": "t-1", "rows": [
        {"id": "a", "title": "a", "scope": "team", "aggregation": "min",
         "levels": [{"level": 1, "truth": ["agent_context"], "criteria": [{"signal": DOC, "op": "lte", "value": 50}]}]},
        {"id": "h", "title": "h", "scope": "team", "aggregation": "min",
         "levels": [{"level": 1, "truth": ["agent_runtime"], "polarity": "hazard", "requires": ["a.1"],
                     "criteria": [{"signal": DOC, "op": "lte", "value": 50}]}]}]})
    result = lt.level_result(model, {"doc_staleness": {"summary": {"stale_pct": 0}}}, declines=[_record()])
    assert _cond(result, "a.1")["state"] == "declined" and _cond(result, "h.1")["state"] == "met"
    assert result["hazards"] == [{"id": "h.1", "fix": ""}]


def test_a_pack_hazard_must_block_a_gate_of_the_base_model() -> None:
    from lib.pack_layering import layer_packs
    from lib.packs import PackError
    base = _hazard_model(blocks=None)
    pack_row = {"id": "p", "title": "p", "scope": "team", "aggregation": {"rule": "min", "share": 1},
                "levels": [{"level": 1, "truth": ["behaviour"], "polarity": "hazard", "blocks": "no_such_gate",
                            "criteria": [{"signal": DOC, "op": "lte", "value": 50}]}]}
    with pytest.raises(PackError, match="'blocks' names no Step gate"):
        layer_packs(base, "default", [{"name": "team", "rows": [pack_row]}])  # type: ignore[list-item]
    pack_row["levels"][0]["blocks"] = "ci_runs_tests"  # type: ignore[index]
    layered = layer_packs(base, "default", [{"name": "team", "rows": [pack_row]}])  # type: ignore[list-item]
    assert lt.level_result(layered, _signals(10))["step"] == 2


def test_the_top_card_words_a_hazard_and_a_decline_as_what_they_are() -> None:
    from lib.top_card import _gap, start_lines
    hazard = lt.level_result(_hazard_model(), _signals(10))
    blocker = hazard["next_step"]["blockers"][0]  # type: ignore[index]
    assert _gap(blocker) == "the hazard skip.1 undermines the CI runs tests gate"
    assert start_lines(hazard, None) == ("Start here: Clear the hazard skip.1.",
                                         "Done when: the next `/assess` run no longer reads the hazard skip.1.")
    with_fix = {**hazard, "hazards": [{"id": "skip.1", "fix": "Remove the label rule.", "gate": "ci_runs_tests"}]}
    assert start_lines(with_fix, None)[0] == "Start here: Clear the hazard skip.1: remove the label rule."
    declined = lt.level_result(lt.default_model(), HELD, declines=[_record()])
    gate = declined["next_step"]["blockers"][0]  # type: ignore[index]
    assert _gap(gate) == f"the repository declined {INSTRUCTIONS}, so the instruction file gate cannot hold"
    start, done = start_lines(declined, None)
    assert "agents-declined.json" in start and "Commit an instruction file" not in start
    assert done.startswith("Done when: the decline is lifted")


def test_a_gate_lost_to_a_decline_or_a_hazard_is_a_measured_loss() -> None:
    """#828: gate_loss reads a declined gate and a hazard-held gate as lost, never as unread."""
    from lib.gate_loss import split_lost
    since = {"available": True, "gates_lost": ["instructions"]}
    declined = lt.level_result(lt.default_model(), HELD, declines=[_record()])
    assert split_lost({"level": declined, "since_last_run": since}) == (["instructions"], [])
    hazard_level = lt.level_result(lt.default_model(), HELD)
    hazard_level["conditions"].append({"id": "x.1", "row": "x", "level": 1, "grade": "measured", "counted": False,
                                       "state": "met", "polarity": "hazard", "blocks": "ci_runs_tests"})
    lost = {"available": True, "gates_lost": ["ci_runs_tests"]}
    assert split_lost({"level": hazard_level, "since_last_run": lost}) == (["ci_runs_tests"], [])


def test_the_agents_marker_never_takes_a_listed_non_agent_condition_out_of_the_denominator() -> None:
    """A listed coverage condition stays measured under the agents marker; a detector's record may list it."""
    data = {**HELD, "coverage_gate": {"available": True, "enforced_meets_minimum": True}}
    before = lt.level_result(lt.default_model(), data)
    marker = lt.level_result(lt.default_model(), data,
                             declines=[_record(conditions=["layer-6-coverage.1", INSTRUCTIONS])])
    assert _cond(marker, "layer-6-coverage.1")["state"] == "met"
    assert marker["declined"] == [{"id": INSTRUCTIONS, "source": AGENTS_MARKER}]
    assert marker["tally"]["measured"] == before["tally"]["measured"] - (1 if _cond(before, INSTRUCTIONS)["counted"] else 0)
    detector = lt.level_result(lt.default_model(), data,
                               declines=[_record(source="detector", conditions=["layer-6-coverage.1"])])
    assert _cond(detector, "layer-6-coverage.1")["state"] == "declined"
    assert lt.AGENTS_MARKER == AGENTS_MARKER
