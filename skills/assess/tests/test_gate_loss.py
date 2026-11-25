"""lib/gate_loss.py: the gates and Step a run lost against the committed baseline (#1152)."""
from __future__ import annotations

from typing import Any

from lib.gate_loss import (
    baseline_step,
    held_at_baseline,
    lost_gates,
    no_baseline,
    split_lost,
    step_change,
)
from lib.scoring.level_tally import default_model


def _conditions(states: dict[str, str]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for step in default_model().get("steps", []):
        for g in step["gates"]:
            for n in range(1, g["level"] + 1):
                cid = f"{g['row']}.{n}"
                out[cid] = {"id": cid, "state": states.get(g["id"], "met")}
    return list(out.values())


def _ctx(lost: list[str] | None, held: list[str] | None, step: int,
         states: dict[str, str] | None = None, first_run: bool = False) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "since_last_run": {"available": True, "first_run": first_run, "gates_lost": lost},
        "level": {"step": step, "conditions": _conditions(states or {})},
    }
    if held is not None:
        ctx["prior_level"] = {"baseline": {"run_id": "r", "head_sha": "a" * 40, "held_gates": held}}
    return ctx


def test_baseline_step_follows_the_gates_in_step_order():
    assert baseline_step([]) == 1
    assert baseline_step(["instructions"]) == 2
    assert baseline_step(["instructions", "ci_runs_tests"]) == 3
    assert baseline_step(["instructions", "ci_runs_tests", "coverage_gate"]) == 4
    # A higher gate without the lower ones reaches no further than the lower Step.
    assert baseline_step(["ci_runs_tests", "coverage_gate"]) == 1
    assert baseline_step([], steps=[]) is None


def test_step_change_only_when_the_step_dropped():
    held = ["instructions", "ci_runs_tests"]
    assert step_change(_ctx(["ci_runs_tests"], held, 2, {"ci_runs_tests": "unmet"})) == (3, 2)
    assert step_change(_ctx([], held, 3)) is None
    assert step_change(_ctx([], None, 2)) is None
    no_level = _ctx([], held, 2)
    no_level["level"] = {"available": False}
    assert step_change(no_level) is None


def test_split_lost_unmet_and_unread():
    ctx = _ctx(["instructions", "ci_runs_tests"], None, 1,
               {"instructions": "unmet", "ci_runs_tests": "not_measured"})
    assert split_lost(ctx) == (["instructions"], ["ci_runs_tests"])
    ctx["level"] = {"conditions": [{"id": "x.1", "state": "met"}]}  # another model
    assert split_lost(ctx) == ([], ["instructions", "ci_runs_tests"])


def test_lost_gates_unknown_reads_empty():
    assert lost_gates({}) == []
    assert lost_gates({"since_last_run": {"available": False}}) == []
    assert lost_gates(_ctx(None, None, 2)) == []
    mixed: list[Any] = ["ci_runs_tests", 3]
    assert lost_gates(_ctx(mixed, None, 2)) == ["ci_runs_tests"]


def test_no_baseline():
    assert no_baseline(_ctx(None, None, 2, first_run=True)) is True
    assert no_baseline(_ctx(None, None, 2)) is True
    assert no_baseline({}) is True
    assert no_baseline(_ctx([], None, 2)) is True  # only an earlier run-context.json: no committed baseline
    assert no_baseline(_ctx(None, ["instructions"], 2)) is False
    assert no_baseline({"prior_level": {"baseline": {"held_gates": []}}}) is False


def test_held_at_baseline_keeps_only_gates_the_baseline_held():
    ctx = _ctx(["ci_runs_tests"], ["instructions"], 2)
    assert held_at_baseline(ctx, ["instructions", "ci_runs_tests"]) == ["instructions"]
    assert held_at_baseline(_ctx([], None, 2), ["instructions"]) == []
