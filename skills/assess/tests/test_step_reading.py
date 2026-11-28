"""The not-measured Step reading (#1640, parent #1351): "Step N, Step N+1 not measured".

When every gate of Step N holds and each gate of Step N+1 that does not hold was not measured,
the run reads "Step 2, Step 3 not measured" in the badge summary, on the top card and in the
report, never as Step 3 and never as a gate not held; the fleet counts such repositories apart
from the ones an unmet gate holds back, and never as one change away. A next Step with any
unmet gate reads as it did before. The fixtures are invented level results under the default
model, whose Step 3 adds the ``ci_runs_tests`` gate to Step 2's ``instructions``.
"""
from __future__ import annotations

from typing import Any

from assess_report import render_report
from lib.card_lint import LINK
from lib.exec_summary import summarised
from lib.fleet.fleet_front import front_screen, front_section
from lib.fleet.fleet_step_funnel import funnel_view
from lib.fleet.fleet_step_lifts import lift_condition, step_lifts
from lib.scoring.headline import headline
from lib.scoring.level_tally import default_model
from lib.scoring.step_reading import next_unread, step_reading
from lib.top_card import GATE_GAP, card_lines
from test_top_card import KNOWN, _cond, _level

MODEL = default_model()
CI_UNREAD = {"kind": "gate", "gate": "ci_runs_tests", "condition": "layer-5-ci.1", "state": "not_measured",
             "reason": "needs_full_run"}
CI_UNMET = {"kind": "gate", "gate": "ci_runs_tests", "condition": "layer-5-ci.1", "state": "unmet"}


def _step2(ci: str = "not_measured", blockers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Step 2 held (an instruction file), the Step 3 CI gate ``ci``, the Step 4 coverage gate unmet."""
    conds = [_cond("layer-0-instructions.1"), _cond("layer-5-ci.1", ci), _cond("layer-6-coverage.1", "unmet")]
    gate = CI_UNREAD if ci == "not_measured" else CI_UNMET
    return _level(2, {"step": 3, "blockers": blockers if blockers is not None else [dict(gate)]}, conds)


# --- the reading -----------------------------------------------------------------------------


def test_the_next_step_reads_not_measured_only_when_every_open_gate_was_not_measured() -> None:
    assert next_unread(_step2()) == (2, 3, ["needs_full_run"])
    assert step_reading(_step2()) == "Step 2, Step 3 not measured"
    assert next_unread(_step2("unmet")) is None
    # one gate not measured and one unmet: the unmet gate holds the Step whatever the other reads
    mixed = _step2(blockers=[dict(CI_UNREAD), {"kind": "gate", "gate": "own", "condition": "x.1", "state": "unmet"}])
    assert next_unread(mixed) is None
    assert next_unread(_level(4, None)) is None  # the top Step
    assert next_unread(_level(None, {"step": 1, "blockers": [dict(CI_UNREAD)]})) is None  # no Step yet
    assert next_unread({"step": 2}) is None and next_unread(None) is None


def test_reasons_are_listed_once_each_in_gate_order() -> None:
    two = _step2(blockers=[dict(CI_UNREAD), {**CI_UNREAD, "gate": "own", "reason": "needs_platform"},
                           {**CI_UNREAD, "gate": "own2"}])
    assert next_unread(two) == (2, 3, ["needs_full_run", "needs_platform"])


# --- the headline, the card and the report ---------------------------------------------------


def test_the_summary_reads_step_n_and_the_next_step_not_measured() -> None:
    summary = headline(_step2(), MODEL)["summary"]  # type: ignore[arg-type]
    assert summary == "Step 2, Step 3 not measured (needs a full run) · next: CI runs tests"
    assert not summary.startswith("Step 3")


def test_the_card_reads_the_next_step_not_measured_and_never_a_gate_not_held() -> None:
    lines = card_lines(_step2(), MODEL, KNOWN)  # type: ignore[arg-type]
    assert lines[0] == "Step 2, Step 3 not measured · Step 2 of 4: supervised agent PRs."
    assert "Step 3 not measured: the CI runs tests gate, not measured here (needs a full run)." in lines
    text = "\n".join(lines)
    assert GATE_GAP["ci_runs_tests"] not in text
    assert "Blocks Step 3" not in text and "Step 3 of 4" not in text


def test_the_report_reads_the_next_step_not_measured() -> None:
    level = _step2()
    block = summarised(headline(level, MODEL), level, KNOWN, MODEL, None, None, None, None)  # type: ignore[arg-type]
    report = render_report({"headline": block, "level": level, "since_last_run": KNOWN}, "demo")
    assert "Step 2, Step 3 not measured" in LINK.sub(r"\1", report)  # the lead links its first "Step"
    assert GATE_GAP["ci_runs_tests"] not in report


def test_a_next_step_gate_unmet_reads_as_before() -> None:
    level = _step2("unmet")
    assert headline(level, MODEL)["summary"] == "Step 2 · next: CI runs tests"  # type: ignore[arg-type]
    lines = card_lines(level, MODEL, KNOWN)  # type: ignore[arg-type]
    assert lines[0] == "Step 2 of 4: supervised agent PRs."
    assert f"Blocks Step 3: {GATE_GAP['ci_runs_tests']}." in lines
    assert "not measured" not in "\n".join(lines)


def test_a_gate_not_measured_beside_an_unmet_one_never_reads_as_not_held() -> None:
    mixed = _step2(blockers=[dict(CI_UNREAD), {"kind": "gate", "gate": "coverage_gate",
                                               "condition": "layer-6-coverage.1", "state": "unmet"}])
    blocks = next(line for line in card_lines(mixed, MODEL, KNOWN) if line.startswith("Blocks Step 3"))  # type: ignore[arg-type]
    assert blocks == ("Blocks Step 3: the CI runs tests gate, not measured here (needs a full run); "
                      "no coverage gate is enforced.")
    # the badge names the measured gate the funnel counts the repository under
    assert headline(mixed, MODEL)["summary"] == "Step 2 · next: coverage gate"  # type: ignore[arg-type]


# --- the fleet -------------------------------------------------------------------------------


def _repo(level: dict[str, Any]) -> dict[str, Any]:
    return {"key": "x", "status": "ok", "reason": None, "step": level["step"], "next_step": level["next_step"]}


def _fleet() -> dict[str, Any]:
    levels = [_step2(), _step2(), _step2("unmet")]
    return {"repos": [_repo(lv) for lv in levels],
            "opportunities": {"step_lifts": step_lifts(MODEL, levels)}}  # type: ignore[arg-type]


def test_the_funnel_counts_the_next_step_not_measured_apart_by_reason() -> None:
    bar = next(b for b in funnel_view(MODEL, _fleet())["bars"] if b["step"] == 2)
    assert bar["count"] == 3 and bar["next_not_measured"] == 2
    assert [(s["gate"], s["kind"], s["reason"], s["count"]) for s in bar["segments"]] == [
        ("ci_runs_tests", "unmet", None, 1), ("ci_runs_tests", "not_measured", "needs_full_run", 2)]


def test_a_repository_with_an_unmet_gate_counts_under_it_not_under_a_gate_not_measured() -> None:
    mixed = _step2(blockers=[dict(CI_UNREAD), {"kind": "gate", "gate": "coverage_gate",
                                               "condition": "layer-6-coverage.1", "state": "unmet"}])
    bar = next(b for b in funnel_view(MODEL, {"repos": [_repo(mixed)]})["bars"] if b["step"] == 2)
    assert bar["next_not_measured"] == 0
    assert [(s["gate"], s["kind"]) for s in bar["segments"]] == [("coverage_gate", "unmet")]


def test_the_next_step_not_measured_is_never_one_change_away() -> None:
    assert lift_condition(MODEL, _step2()) is None  # type: ignore[arg-type]
    assert lift_condition(MODEL, _step2("unmet")) == ("layer-5-ci.1", "ci_runs_tests", 3, False)  # type: ignore[arg-type]
    assert [lift["repo_count"] for lift in _fleet()["opportunities"]["step_lifts"]] == [1]


def test_the_front_screen_counts_the_next_step_not_measured_apart() -> None:
    front = front_screen(MODEL, _fleet())
    step2 = next(s for s in front["steps"] if s["step"] == 2)
    assert step2["count"] == 3 and step2["next_not_measured"] == 2
    assert front["one_change"] == 1
    section = front_section(front, 3)
    assert "Step 2, Step 3 not measured: 2 repositories" in section
