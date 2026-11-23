"""Tests for the Step ladder (issue #1102): lib/assess_diagrams_ladder.py.

The golden ``tests/fixtures/assess_diagrams/expected-step-ladder.svg`` locks the render at
Step 3 (``test_assess_diagrams.py`` checks it); these tests place the run at each Step of
the shipped model and read the rungs and gates back out of the SVG.
"""
from __future__ import annotations

import copy
import xml.etree.ElementTree as ET

import pytest

from assess_diagrams_fixture import LEVEL
from lib import assess_diagrams_ladder as ladder
from lib.headline import GATE_CHANGE
from lib.level_result_types import ConditionResult, LevelResult
from lib.level_tally import default_model
from lib.levels import StepDef
from lib.vocabulary import IN_PLACE, banned_hits
from svg_cascade import overridden_text_attributes

SVG_NS = "{http://www.w3.org/2000/svg}"
STEPS: list[StepDef] = default_model()["steps"]
# The condition each shipped gate needs, the Step whose gate it is.
GATE_CONDITIONS = {"layer-0-instructions.1": 2, "layer-5-ci.1": 3, "layer-6-coverage.1": 4}


def _at(step: int) -> LevelResult:
    """A level result at ``step``: the gates below it met, the ones above not yet."""
    conditions: list[ConditionResult] = [
        {"id": cid, "row": cid.rpartition(".")[0], "level": 1, "grade": "verified", "counted": True,
         "state": "met" if gate_step <= step else "unmet"}
        for cid, gate_step in GATE_CONDITIONS.items()]
    top = max(s["step"] for s in STEPS)
    gate = {2: "instructions", 3: "ci_runs_tests", 4: "coverage_gate"}.get(step + 1, "")
    nxt = None if step == top else {"step": step + 1,
                                     "blockers": [{"kind": "gate", "gate": gate}]}
    level = copy.deepcopy(LEVEL)
    level.update({"conditions": conditions, "step": step, "next_step": nxt})  # type: ignore[typeddict-item]
    return level


def _rungs(content: str) -> dict[int, str]:
    root = ET.fromstring(content.encode("utf-8"))
    return {int(r.attrib["data-step"]): r.attrib["data-state"]
            for r in root.iter(f"{SVG_NS}rect") if "data-step" in r.attrib}


def _gates(content: str) -> dict[int, bool]:
    root = ET.fromstring(content.encode("utf-8"))
    return {int(p.attrib["data-gate-to"]): p.attrib.get("data-next") == "true"
            for p in root.iter(f"{SVG_NS}path") if "data-gate-to" in p.attrib}


@pytest.mark.parametrize("step", [1, 2, 3, 4])
def test_the_run_is_marked_on_its_step_and_the_next_gate_is_highlighted(step: int) -> None:
    content = ladder.render_ladder(_at(step), STEPS)
    rungs = _rungs(content)
    assert sorted(rungs) == [1, 2, 3, 4]
    assert [n for n, state in rungs.items() if state == "here"] == [step]
    assert all(state == "reached" for n, state in rungs.items() if n < step)
    assert all(state == "ahead" for n, state in rungs.items() if n > step)
    assert content.count("You are here") == 1
    gates = _gates(content)
    assert sorted(gates) == [2, 3, 4]  # one gate band between each pair of rungs
    assert [to for to, is_next in gates.items() if is_next] == ([] if step == 4 else [step + 1])
    assert overridden_text_attributes(content) == []
    assert not banned_hits(content)


@pytest.mark.parametrize("step", [1, 2, 3, 4])
def test_each_rung_names_its_step_and_what_an_agent_can_do(step: int) -> None:
    content = ladder.render_ladder(_at(step), STEPS)
    for s in STEPS:
        assert f"Step {s['step']} · {s['name']}" in content
        sentence = ladder.STEP_MEANING[s["step"]]
        assert sentence[:1].upper() + sentence[1:] in content


@pytest.mark.parametrize("step", [1, 2, 3, 4])
def test_gate_words_come_from_gate_change_and_say_whether_they_hold(step: int) -> None:
    content = ladder.render_ladder(_at(step), STEPS)
    for gate_id, words in GATE_CHANGE.items():
        gate_step = {"instructions": 2, "ci_runs_tests": 3, "coverage_gate": 4}[gate_id]
        state = "in place" if gate_step <= step else "not yet"
        assert f"{words} ({state})" in content


@pytest.mark.parametrize("step, caption", [
    (1, "Step 2, Supervised agent PRs, opens when an instruction file an agent reads is committed."),
    (2, "Step 3, Unattended agent PRs, opens when CI turns red when a check breaks."),
    (3, "Step 4, Autonomous loops, opens when a coverage gate is enforced."),
    (4, "This is the top Step; it holds while its gates hold."),
])
def test_in_place_and_caption(step: int, caption: str) -> None:
    level = _at(step)
    name = next(s["name"] for s in STEPS if s["step"] == step)
    assert ladder.ladder_in_place(level, STEPS) == (
        f"{IN_PLACE} Step {step} of 4, {name}: {ladder.STEP_MEANING[step]}.")
    assert ladder.ladder_caption(level, STEPS) == caption


def test_the_caption_names_gates_and_never_a_count() -> None:
    """#1083: a Step reads its gates only, so the caption, like the headline, names no count."""
    level = _at(3)
    assert level["next_step"] is not None
    assert ladder.ladder_caption(level, STEPS) == "Step 4, Autonomous loops, opens when a coverage gate is enforced."
    assert "practice" not in ladder.ladder_caption(level, STEPS)
    level["next_step"]["blockers"] = []
    assert ladder.ladder_caption(level, STEPS) == "Step 4, Autonomous loops, opens when its gates hold."


@pytest.mark.parametrize("state, read", [
    ("not_measured", "not measured"), ("not_applicable", "excluded by owner"),
    ("declined", "not yet"), ("unmet", "not yet"), ("met", "in place"),
])
def test_a_gate_the_run_could_not_read_never_reads_not_yet(state: str, read: str) -> None:
    level = _at(2)
    ci = next(c for c in level["conditions"] if c["id"] == "layer-5-ci.1")
    ci["state"] = state
    assert ladder.gate_state(level, ladder.new_gates(STEPS, 3)[0]) == read
    assert f"CI turns red when a check breaks ({read})" in ladder.render_ladder(level, STEPS)


def test_a_gate_above_level_one_reads_its_first_condition_not_met_like_the_engine() -> None:
    level = _at(1)
    level["conditions"] += [
        {"id": "r.1", "row": "r", "level": 1, "grade": "verified", "counted": True, "state": "not_measured"},
        {"id": "r.2", "row": "r", "level": 2, "grade": "verified", "counted": True, "state": "unmet"},
    ]
    assert ladder.gate_state(level, {"id": "g", "row": "r", "level": 2}) == "not measured"
    level["conditions"][-2]["state"] = "met"
    assert ladder.gate_state(level, {"id": "g", "row": "r", "level": 2}) == "not yet"


def test_the_caption_says_why_a_next_gate_is_open_as_the_headline_does() -> None:
    level = _at(2)
    assert level["next_step"] is not None
    level["next_step"]["blockers"] = [{"kind": "gate", "gate": "ci_runs_tests", "state": "not_measured",
                                       "reason": "needs_platform"}]
    assert ladder.ladder_caption(level, STEPS) == ("Step 3, Unattended agent PRs, opens when CI turns red when a check breaks "
                                                   "(not measured here: needs GitHub or GitLab access).")


def test_new_gates_are_the_ones_a_step_adds() -> None:
    assert [g["id"] for g in ladder.new_gates(STEPS, 2)] == ["instructions"]
    assert [g["id"] for g in ladder.new_gates(STEPS, 3)] == ["ci_runs_tests"]
    assert [g["id"] for g in ladder.new_gates(STEPS, 4)] == ["coverage_gate"]


def test_a_model_of_its_own_draws_its_own_steps() -> None:
    steps: list[StepDef] = [
        {"step": 1, "name": "Start", "gates": []},
        {"step": 2, "name": "Further",
         "gates": [{"id": "own_gate", "row": "r", "level": 1}]},
    ]
    level = _at(1)
    level["next_step"] = {"step": 2, "blockers": [{"kind": "gate", "gate": "own_gate"}]}
    content = ladder.render_ladder(level, steps)
    assert sorted(_rungs(content)) == [1, 2]
    assert "Step 2 · Further" in content and "the own_gate gate holds (not yet)" in content
    assert ladder.meaning(2) and ladder.meaning(9) == ""
    assert ladder.ladder_caption(level, steps) == "Step 2, Further, opens when the own_gate gate holds."


def test_the_report_skips_the_ladder_without_steps() -> None:
    from lib import assess_diagrams as ad
    [drawn] = [d for d in ad.build_diagrams({"level": LEVEL}, steps=[]) if d["file"] == ladder.LADDER_FILE]
    assert drawn["svg"] is None and drawn["skipped"] == "the maturity model defines no Steps."
    [none] = [d for d in ad.build_diagrams({}) if d["file"] == ladder.LADDER_FILE]
    assert none["svg"] is None and "no level result" in (none["skipped"] or "")


def test_two_renders_are_byte_identical() -> None:
    assert ladder.render_ladder(_at(2), STEPS) == ladder.render_ladder(_at(2), STEPS)
