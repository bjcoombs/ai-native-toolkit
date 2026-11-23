"""The executive summary and the one call to action that open the /assess report (lib/exec_summary.py, #1102).

Pure cases pin the three lines at every Step, at the top Step, on a first run and when the
since-last-run block says the change is not known, and check that no line names a layer, a
condition id or a count of practices. The end-to-end case runs the core on the contract's
ci2 shape and reads the summary at the very top of the deterministic report.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lib.assess_diagrams_ladder import STEP_MEANING
from lib.exec_summary import GATE_TASK, STEP_GATES_URL, call_to_action, since_line, summarised, summary_lines
from lib.headline import GATE_CHANGE, GLOSSARY_URL, as_headline, headline
from lib.level_tally import default_model
from test_headline import SCRIPTS, _level, _run, ci2  # noqa: F401 - ci2 is a fixture

ROOT = Path(__file__).resolve().parents[3]
CONDITION_ID = re.compile(r"\b[a-z0-9]+(-[a-z0-9]+)+\.\d\b")
COUNT = re.compile(r"\b\d+ (of|in place of) \d+\b")
GATES = {2: "instructions", 3: "ci_runs_tests", 4: "coverage_gate"}

KNOWN = {"available": True, "first_run": False, "closed_count": 2, "closed": [], "closed_reason": None,
         "gates_gained": ["ci_runs_tests"], "gates_lost": [], "next_blocker": "coverage_gate"}


def _at(step: int, **blocker: Any) -> dict[str, Any]:
    """A level result at ``step`` whose next Step is blocked by its own new gate."""
    if step == 4:
        return _level(step=4, next_step=None)
    gate = GATES[step + 1]
    return _level(step=step, next_step={"step": step + 1, "blockers": [
        {"kind": "gate", "gate": gate, "condition": "x.1", "state": "unmet", **blocker}]})


def _summary(step: int, since: Any = None) -> dict[str, Any]:
    level = _at(step)
    return dict(summarised(headline(level, default_model()), level, since, default_model()))


def _linked(first: str) -> str:
    """Summary line 1 as the markdown carries it: its leading "Step" links the glossary (#1102)."""
    assert first.startswith("Step "), first
    return f"[Step]({GLOSSARY_URL}#step){first[4:]}"


def test_the_markdown_links_step_once_and_the_lines_stay_plain() -> None:
    h = _summary(2)
    assert h["summary_markdown"].count("](") == 2  # the Step link and the call to action
    assert h["summary_markdown"].startswith(f"[Step]({GLOSSARY_URL}#step) 2 of 4: ")
    assert all("](" not in line for line in h["summary_lines"])


def _plain_checks(text: str) -> None:
    assert "layer" not in text.lower(), text
    assert not CONDITION_ID.search(text), text
    assert not any(gate in text for gate in GATES.values()), text
    assert "practice" not in text, text
    # the only "N of M" is the Step's place on the ladder, at the start of the first line
    assert not COUNT.search(re.sub(r"^Step \d of \d", "", text)), text


@pytest.mark.parametrize("step", [1, 2, 3])
def test_steps_one_to_three_name_the_step_the_next_task_and_what_it_allows(step: int) -> None:
    h = _summary(step, KNOWN)
    task = GATE_TASK[GATES[step + 1]]
    assert h["summary_lines"] == [
        f"Step {step} of 4: {STEP_MEANING[step]}.",
        f"Next: {task}. Then you reach Step {step + 1}: {STEP_MEANING[step + 1]}.",
        "Since last run: 2 actions closed; now in place: CI runs the tests.",
    ]
    assert h["call_to_action"] == f"**Start here:** [{task[:1].upper()}{task[1:]}]({STEP_GATES_URL})."
    assert h["summary_markdown"] == "\n\n".join([_linked(h["summary_lines"][0]), *h["summary_lines"][1:],
                                                  h["call_to_action"]])
    for line in h["summary_lines"]:
        _plain_checks(line)


def test_the_top_step_says_every_gate_holds_and_has_no_call_to_action() -> None:
    h = _summary(4, KNOWN)
    assert h["summary_lines"][:2] == [f"Step 4 of 4: {STEP_MEANING[4]}.",
                                      "Next: every gate holds; keep them in place to stay at Step 4."]
    assert h["call_to_action"] == ""
    assert h["summary_markdown"] == "\n\n".join([_linked(h["summary_lines"][0]), *h["summary_lines"][1:]])
    for line in h["summary_lines"]:
        _plain_checks(line)


def test_a_first_run_says_so() -> None:
    first = {**KNOWN, "first_run": True, "closed_count": None, "gates_gained": None, "gates_lost": None}
    assert _summary(2, first)["summary_lines"][2] == "First run."


@pytest.mark.parametrize("since", [
    None,
    {"available": False, "reason": "scan_failed"},
    {**KNOWN, "closed_count": None, "closed_reason": "previous_state_missing", "gates_gained": None,
     "gates_lost": None},
])
def test_since_last_run_not_known_says_not_known(since: Any) -> None:
    assert _summary(2, since)["summary_lines"][2] == "Since last run: not known."


def test_since_last_run_words_each_part_and_says_which_part_is_not_known() -> None:
    assert since_line({**KNOWN, "closed_count": 0, "gates_gained": [], "gates_lost": []}) == \
        "Since last run: no actions closed; nothing gained or lost."
    assert since_line({**KNOWN, "closed_count": 1, "gates_gained": [], "gates_lost": ["coverage_gate"]}) == \
        f"Since last run: 1 action closed; no longer in place: {GATE_CHANGE['coverage_gate']}."
    assert since_line({**KNOWN, "closed_count": None}) == \
        "Since last run: now in place: CI runs the tests; closed actions not known."
    assert since_line({**KNOWN, "gates_gained": None, "gates_lost": None}) == \
        "Since last run: 2 actions closed; gate changes not known."


def test_a_gate_not_measured_or_excluded_carries_the_headline_note() -> None:
    level = _at(2, state="not_measured", reason="needs_full_run")
    lines = summary_lines(level, KNOWN, default_model())  # type: ignore[arg-type]
    assert lines[1].startswith("Next: make CI run the tests (not measured here: needs a full run). Then")
    assert call_to_action(level).endswith("(not measured here: needs a full run).")  # type: ignore[arg-type]
    excluded = _at(2, disposition="excluded_by_owner", exclusion_kind="not_applicable")
    assert "(excluded by owner: not applicable;" in summary_lines(excluded, KNOWN, default_model())[1]  # type: ignore[arg-type]


def test_two_blocking_gates_are_both_tasks_and_the_call_to_action_names_the_first() -> None:
    level = _level(step=2, next_step={"step": 4, "blockers": [
        {"kind": "gate", "gate": "ci_runs_tests", "condition": "x.1", "state": "unmet"},
        {"kind": "gate", "gate": "own_gate", "condition": "y.1", "state": "unmet"}]})
    lines = summary_lines(level, KNOWN, default_model())  # type: ignore[arg-type]
    assert lines[1].startswith("Next: make CI run the tests and make the own_gate gate hold. Then you reach Step 4")
    assert "[Make CI run the tests]" in call_to_action(level)  # type: ignore[arg-type]


def test_an_unavailable_headline_or_a_model_without_steps_keeps_the_fields_empty() -> None:
    down = headline({"available": False, "reason": "x"}, default_model())
    assert summarised(down, {"available": False}, KNOWN, default_model())["summary_markdown"] == ""
    no_steps = _level(step=None, next_step=None)
    model = {**default_model(), "steps": []}
    filled = summarised(headline(no_steps, model), no_steps, KNOWN, model)  # type: ignore[arg-type]
    assert (filled["summary_lines"], filled["call_to_action"], filled["summary_markdown"]) == ([], "", "")


def test_as_headline_backfills_the_summary_on_an_older_block() -> None:
    old = {k: v for k, v in headline(_at(2), default_model()).items()
           if k not in ("summary_lines", "call_to_action", "summary_markdown")}
    assert (as_headline(old)["summary_lines"], as_headline(old)["summary_markdown"]) == ([], "")


def test_counts_only_drops_the_summary_text() -> None:
    from lib.counts_only.content_free import counts_only

    kept = counts_only({"headline": _summary(2, KNOWN)})["headline"]
    assert "call_to_action" not in kept and "summary_markdown" not in kept
    assert kept["summary_lines"] == 3


def test_the_catalog_has_the_step_gates_entry_the_link_names() -> None:
    catalog = (ROOT / "docs" / "what-we-measure.md").read_text(encoding="utf-8")
    assert STEP_GATES_URL.endswith("/docs/what-we-measure.md#step-gates")
    assert "\n### Step gates\n" in catalog


def test_every_default_gate_has_a_task() -> None:
    gates = {g["id"] for s in default_model().get("steps", []) for g in s.get("gates", [])}
    assert gates <= set(GATE_TASK)


def test_ci2_report_opens_with_the_summary(ci2: Path) -> None:  # noqa: F811 - the fixture
    ctx = _run(ci2, "2026-11-22")
    head = ctx["headline"]
    assert len(head["summary_lines"]) == 3
    assert head["summary_lines"][0].startswith(f"Step {ctx['level']['step']} of 4: ")
    assert head["summary_lines"][2] == "First run."
    report = subprocess.run([sys.executable, str(SCRIPTS / "assess_report.py"), str(ci2), "--stdout"],
                            capture_output=True, encoding="utf-8", errors="replace", check=True).stdout
    top = report.split("## ", 2)[0] if report.startswith("# ") else report
    assert head["summary_markdown"] in top
    assert report.index(head["summary_markdown"]) < report.index("## Headline")
    for line in head["summary_lines"]:
        _plain_checks(line)


def test_the_landing_page_shows_the_summary_the_code_prints() -> None:
    """The site's "What the report says first" example is the code's own output, word for word."""
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    since = {**KNOWN, "closed_count": 1, "gates_gained": [], "gates_lost": []}
    for line in _summary(2, since)["summary_lines"]:
        assert f"<div>{line}</div>" in page, line
