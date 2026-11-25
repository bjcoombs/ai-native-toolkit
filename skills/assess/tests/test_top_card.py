"""The report's top card, each fact once (lib/top_card.py, lib/card_facts.py, #1252).

Fixtures for each card state: the top Step with advisories, below the top Step with the
blocking gate leading and a sibling unit that already holds it, a first run, the dark-cockpit
since line, and the one Start here and Done when from rank 1 of ``actions.json``. The end-to-end
case renders the deterministic report and checks the card comes before the first ``## ``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assess_report import render_report
from lib.card_facts import advisories, blocking_gates, step_gate_ids, step_name, top_step
from lib.exec_summary import summarised
from lib.scoring.headline import GLOSSARY_URL, headline
from lib.scoring.level_tally import default_model
from lib.plain_english import lint
from lib.since_last_run import FIRST_RUN
from lib.top_card import (GATE_GAP, GATE_TASK, STEP_GATES_URL, card_lines, card_markdown, first_action,
                          read_first_action)

MODEL = default_model()
GATE_ROWS = {"layer-0-instructions.1", "layer-5-ci.1", "layer-6-coverage.1"}
SHA = "600cca4ec653552f2e82d19d4dfd54fcab703ca4"
ACTION = {"start": '`rg -n "noqa: E402" skills/assess/scripts/assess_report.py`',
          "done_when": "cd skills/assess && uv run --locked ruff check . passes"}
KNOWN = {"available": True, "first_run": False, "closed_count": 0, "closed": [], "closed_reason": None,
         "gates_gained": [], "gates_lost": [], "pruned": [], "lines": []}
FIRST = {"available": True, "first_run": True, "closed_count": None, "gates_gained": None, "gates_lost": None}
ROLLUP = {"learn_from": [{"unit": "agents", "gate": "coverage_gate", "holders": 2,
                          "names": ["skills/assess", "scripts"]}]}


def _cond(cid: str, state: str = "met", grade: str = "measured", counted: bool = True) -> dict[str, Any]:
    return {"id": cid, "row": cid.rsplit(".", 1)[0], "level": int(cid.rsplit(".", 1)[1]), "grade": grade,
            "counted": counted, "state": state}


def _level(step: int | None, next_step: Any = None, conditions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    conds = conditions if conditions is not None else [_cond(c, grade="verified") for c in sorted(GATE_ROWS)]
    met = sum(1 for c in conds if c["counted"] and c["state"] == "met")
    measured = sum(1 for c in conds if c["counted"] and c["state"] in ("met", "unmet"))
    return {"level_result_version": 1, "levels_version": "default-x",
            "tally": {"met": met, "measured": measured, "not_measured": 0}, "counted": [], "listed": [],
            "not_measured": [], "conditions": conds, "step": step, "next_step": next_step}


def _top(advisory: int = 2) -> dict[str, Any]:
    conds = [_cond(c, grade="verified") for c in sorted(GATE_ROWS)]
    # unmet conditions no gate waits on are advisories; an uncounted or unmeasured one is not
    conds += [_cond(f"layer-3-linters.{n}", "unmet") for n in range(1, advisory + 1)]
    conds += [_cond("layer-9-x.1", "unmet", counted=False), _cond("layer-8-y.1", "not_measured")]
    return _level(4, None, conds)


def _below() -> dict[str, Any]:
    conds = [_cond("layer-0-instructions.1"), _cond("layer-5-ci.1"), _cond("layer-6-coverage.1", "unmet")]
    return _level(3, {"step": 4, "blockers": [{"kind": "gate", "gate": "coverage_gate",
                                               "condition": "layer-6-coverage.1", "state": "unmet"}]}, conds)


def _one(lines: list[str], prefix: str) -> list[str]:
    return [line for line in lines if line.startswith(prefix)]


def test_the_top_step_card_holds_the_step_and_counts_advisories() -> None:
    lines = card_lines(_top(), MODEL, FIRST, None, SHA, ACTION)  # type: ignore[arg-type]
    text = "\n".join(lines)
    assert lines[0] == "Holding Step 4 of 4 · 2 advisories, none blocks the Step."
    assert text.count("Holding Step 4 of 4") == 1 and text.count("none blocks the Step") == 1
    assert lines[1] == "Holding: 3 of 3 gates held at `600cca4` · 3 of 5 measured catalogue conditions met, 3 verified."
    assert lines[2:] == [FIRST_RUN, f"Start here: {ACTION['start']}",
                         "Done when: cd skills/assess && uv run --locked ruff check . passes."]


@pytest.mark.parametrize(("k", "tail"), [(0, "."), (1, " · 1 advisory, and it blocks no Step.")])
def test_the_top_step_lead_with_no_or_one_advisory(k: int, tail: str) -> None:
    assert card_lines(_top(k), MODEL, KNOWN)[0] == f"Holding Step 4 of 4{tail}"  # type: ignore[arg-type]


def test_below_the_top_step_the_blocking_gate_leads_and_names_the_sibling() -> None:
    lines = card_lines(_below(), MODEL, KNOWN, ROLLUP, None, ACTION)  # type: ignore[arg-type]
    assert lines[0] == "Step 3 of 4: unattended agent PRs."
    assert lines[1] == "Holding: 2 of 3 Step 4 gates held · 2 of 3 measured catalogue conditions met, 0 verified."
    gate_lines = [line for line in lines if "gate" in line.lower() and not line.startswith("Holding")]
    assert gate_lines[0] == ("Blocks Step 4: no coverage gate is enforced. "
                             "`skills/assess` already does this; copy it.")
    assert "already does this; copy it" in gate_lines[0]
    # no sibling in the roll-up: the line names the gate alone
    assert card_lines(_below(), MODEL, KNOWN)[2] == "Blocks Step 4: no coverage gate is enforced."  # type: ignore[arg-type]
    other = {"learn_from": ["not a mapping", {"unit": "a", "gate": "ci_runs_tests", "holders": 1, "names": ["b"]}]}
    assert "copy it" not in "\n".join(card_lines(_below(), MODEL, KNOWN, other))  # type: ignore[arg-type]


def test_a_gate_not_measured_carries_the_headline_note() -> None:
    level = _below()
    level["next_step"]["blockers"][0].update(state="not_measured", reason="needs_full_run")
    lines = card_lines(level, MODEL, KNOWN)  # type: ignore[arg-type]
    assert lines[2] == "Blocks Step 4: no coverage gate is enforced (not measured here: needs a full run)."
    assert lines[-2].endswith("(not measured here: needs a full run)")


def test_a_first_run_reads_the_baseline_once_and_never_not_known() -> None:
    for level in (_top(), _below()):
        text = "\n".join(card_lines(level, MODEL, FIRST, ROLLUP, SHA, None))  # type: ignore[arg-type]
        assert text.count("This run is your baseline") == 1
        assert text.count("not known") == 0


def test_the_since_line_follows_the_dark_cockpit_rule() -> None:
    def since(**over: Any) -> list[str]:
        return _one(card_lines(_below(), MODEL, {**KNOWN, **over}), "Since last run")  # type: ignore[arg-type]

    assert since() == []  # nothing moved: no line
    assert since(closed_count=1) == ["Since last run: 1 action closed."]
    assert since(closed_count=3, gates_gained=["ci_runs_tests"], gates_lost=["coverage_gate"]) == [
        "Since last run: 3 actions closed; now in place: CI turns red on a broken check; "
        "no longer in place: a coverage gate is enforced."]
    assert since(pruned=[{"path": "CLAUDE.md", "saved": 40}]) == [
        "Since last run: pruned always-on text in `CLAUDE.md`, saved 40 always-on lines."]
    assert since(pruned=[{"path": "CLAUDE.md"}]) == []  # no measured saving: no credit line
    shas = [{"completed_sha": f"{n}" * 40} for n in range(1, 5)]
    assert since(closed_count=4, closed=shas) == [
        "Since last run: 4 actions closed (`1111111`, `2222222`, `3333333`, ...)."]
    assert since(closed_count=None, closed_reason="shallow_clone") == [
        "Since last run: closed actions not known (this is a shallow clone, so commit ancestry cannot be checked)."]
    assert since(gates_gained=None, gates_lost=None) == ["Since last run: gate changes not known."]
    assert _one(card_lines(_below(), MODEL, None), "Since last run") == ["Since last run: not known."]  # type: ignore[arg-type]


@pytest.mark.parametrize("level", [_top(), _below(), _level(None, {"step": 1, "blockers": []})])
@pytest.mark.parametrize("action", [ACTION, None])
@pytest.mark.parametrize("since", [FIRST, KNOWN, None])
def test_every_card_has_one_start_here_and_one_done_when(level: Any, action: Any, since: Any) -> None:
    lines = card_lines(level, MODEL, since, ROLLUP, SHA, action)
    assert len(_one(lines, "Start here")) == 1 and len(_one(lines, "Done when")) == 1
    assert lines[-2].startswith("Start here") and lines[-1].startswith("Done when")


def test_without_an_open_action_the_card_falls_back_to_the_gate_or_the_next_run() -> None:
    below = card_lines(_below(), MODEL, KNOWN)  # type: ignore[arg-type]
    task = GATE_TASK["coverage_gate"]
    assert below[-2] == f"Start here: [{task[:1].upper()}{task[1:]}]({STEP_GATES_URL})"
    assert below[-1] == "Done when: the next `/assess` run reads that a coverage gate is enforced."
    top = card_lines(_top(), MODEL, KNOWN)  # type: ignore[arg-type]
    assert top[-2:] == ["Start here: run `/assess` again after your next change",
                        "Done when: the next `/assess` run still holds every gate."]


def test_first_action_reads_the_first_open_entry_by_rank(tmp_path: Path) -> None:
    payload = {"actions": [
        {"rank": 2, "status": "pending", "first_step": "two", "done_when": "b"},
        {"rank": 1, "status": "done", "first_step": "done one", "done_when": "x"},
        {"rank": 3, "status": "pending", "action": "three", "done_when": "c"},
        {"rank": 0, "status": "pending", "first_step": "no done_when"}]}
    assert first_action(payload) == {"start": "`two`", "done_when": "b"}
    assert first_action({"actions": [payload["actions"][2]]}) == {"start": "three", "done_when": "c"}
    assert first_action({"actions": [{"rank": 1, "first_step": "add `x` to y", "done_when": "z"}]}) == \
        {"start": "add `x` to y", "done_when": "z"}
    assert first_action({"actions": "nope"}) is None and first_action([]) is None
    assert read_first_action(None) is None and read_first_action(tmp_path) is None
    (tmp_path / "actions.json").write_text("{", encoding="utf-8")
    assert read_first_action(tmp_path) is None
    (tmp_path / "actions.json").write_text(json.dumps(payload), encoding="utf-8")
    assert read_first_action(tmp_path) == {"start": "`two`", "done_when": "b"}


def test_below_step_one_and_a_model_without_steps() -> None:
    model = {**MODEL, "steps": [{"step": 1, "name": "Own", "gates": [{"id": "own", "row": "c", "level": 1}]}]}
    level = _level(None, {"step": 1, "blockers": [{"kind": "gate", "gate": "own", "condition": "c.1",
                                                  "state": "unmet"}]}, [_cond("c.1", "unmet")])
    lines = card_lines(level, model, FIRST)  # type: ignore[arg-type]
    assert lines[:3] == ["Not yet at Step 1 of 1.",
                         "Holding: 0 of 1 Step 1 gates held · 0 of 1 measured catalogue conditions met, 0 verified.",
                         "Blocks Step 1: the own gate does not hold yet."]
    flat = {**MODEL, "steps": []}
    assert card_lines(_level(None, None), flat, FIRST)[0] == "This maturity model defines no Steps."  # type: ignore[arg-type]
    assert card_lines({"available": False}, MODEL, FIRST) == []  # type: ignore[arg-type]


def test_the_markdown_bolds_the_lead_and_links_the_glossary_terms_once() -> None:
    lines = card_lines(_top(), MODEL, FIRST, None, SHA, ACTION)  # type: ignore[arg-type]
    md = card_markdown(lines)
    assert md.startswith(f"**Holding [Step]({GLOSSARY_URL}#step) 4 of 4 ·")
    assert f"[measured catalogue conditions]({GLOSSARY_URL}#catalogue-coverage) met" in md
    assert f"[verified]({GLOSSARY_URL}#evidence-grade)" in md
    assert all("](" not in line for line in lines)
    # the no-Steps lead links "Steps" to the same entry (#1253: every line carries a link)
    assert card_markdown(["This maturity model defines no Steps."]) == \
        f"**This maturity model defines no [Steps]({GLOSSARY_URL}#step).**"


def test_the_card_lines_are_plain_english() -> None:
    for level in (_top(), _below()):
        for since in (FIRST, {**KNOWN, "closed_count": 2, "gates_gained": ["ci_runs_tests"]}):
            for line in card_lines(level, MODEL, since, ROLLUP, SHA, None):  # type: ignore[arg-type]
                assert lint(line) == [], line
    assert set(GATE_GAP) == set(GATE_TASK)


def test_the_facts() -> None:
    assert top_step(MODEL) == 4 and top_step({**MODEL, "steps": []}) is None
    assert step_gate_ids(MODEL, 4) == ["instructions", "ci_runs_tests", "coverage_gate"]
    assert step_gate_ids(MODEL, 9) == [] and step_name(MODEL, 9) == ""
    assert step_name(MODEL, 3) == "Unattended agent PRs"
    assert advisories(_top(2), MODEL) == 2 and advisories(None, MODEL) == 0
    assert [b["gate"] for b in blocking_gates(_below())] == ["coverage_gate"]
    assert blocking_gates(_top()) == [] and blocking_gates("x") == []


def test_summarised_puts_the_card_in_the_headline_block() -> None:
    level = _top()
    block = summarised(headline(level, MODEL), level, FIRST, MODEL, None, None, SHA, ACTION)  # type: ignore[arg-type]
    assert block["summary_lines"] == card_lines(level, MODEL, FIRST, None, SHA, ACTION)  # type: ignore[arg-type]
    assert block["call_to_action"] == f"Start here: {ACTION['start']}"
    assert block["summary_markdown"] == card_markdown(block["summary_lines"])
    # the badge and the log.md line read the same blocking-gate fact
    assert headline(_below(), MODEL)["summary"] == "Step 3 · next: coverage gate"  # type: ignore[arg-type]
    assert block["summary"] == "Step 4 · all gates held"


def test_the_rendered_report_opens_with_the_card_before_any_section() -> None:
    level = _below()
    block = summarised(headline(level, MODEL), level, KNOWN, MODEL, None, ROLLUP, SHA, ACTION)  # type: ignore[arg-type]
    report = render_report({"headline": block, "level": level, "since_last_run": KNOWN}, "demo")
    first_section = report.index("\n## ")
    assert report.index(block["summary_markdown"]) < first_section
    top = report[:first_section]
    assert top.count("Start here") == 1 and top.count("Done when") == 1
    assert "## Since Last Run" not in report and "Next blocker" not in report
    assert report.count("Step 3 of 4") == 1
