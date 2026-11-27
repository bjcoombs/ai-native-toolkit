"""Tests for each group's lowest Step (issues #684, #806): lib/fleet/fleet_maturity.py."""
from __future__ import annotations

import lib.fleet.fleet_maturity as fm
from lib.fleet.fleet_data_steps import NO_STEPS, step_roll_up


def test_a_groups_point_is_its_lowest_measured_step_never_the_mean():
    points = fm.group_steps([("g", "a"), ("g", "b"), ("g", "c")], {"a": 1, "b": 3, "c": 3})
    assert points == {"g": {"step": 1, "measured": 3, "repos": 3}}  # the mean would be 2.33


def test_an_unmeasured_repository_counts_in_m_only_and_none_measured_is_unknown():
    points = fm.group_steps([("g", "a"), ("g", "b"), ("h", "c")], {"a": 2, "b": None})
    assert points == {"g": {"step": 2, "measured": 1, "repos": 2},
                      "h": {"step": None, "measured": 0, "repos": 1}}  # c has no Step at all
    assert fm.measured_text(points["g"]) == "1 of 2 measured"


def test_a_boolean_is_not_a_step():
    assert fm.group_steps([("g", "a")], {"a": True})["g"]["step"] is None


def test_the_rule_is_the_fleet_data_unit_roll_up():
    """#772 option A: the same lowest Step and counts a fleet-data.json unit carries."""
    steps = {"a": 2, "b": None, "c": 4}
    roll = step_roll_up((), [{**NO_STEPS, "step": s} for s in steps.values()])  # type: ignore[typeddict-item]  # NO_STEPS spread with a Step
    point = fm.group_steps([("g", r) for r in steps], steps)["g"]
    assert (point["step"], point["measured"], point["repos"]) == (roll["lowest_step"], roll["measured"], roll["repos"])


def test_data_steps_reads_each_repository_by_key():
    assert fm.data_steps(None) is None
    assert fm.data_steps({"repos": [{"key": "a", "step": 2}, {"key": "b", "step": None}, {"step": 1}]}) == {
        "a": 2, "b": None}


def test_the_row_set_is_team_rows():
    assert fm.SCOPE == "team"
