"""A member's failed scan never loses or doubles a row level_up (lib/fleet/fleet_achievements.py, issue #1041).

Each run is compared with the latest earlier run measuring the same repositories
(``same_set``, shared with the Step ``target_hit`` of #945). The team ``grp/sub`` holds
``a`` and ``b``; its docs level is the lowest of theirs (``min``).
"""
from __future__ import annotations

import pytest

import lib.fleet.fleet_achievements as fa
from test_fleet_achievements import BASE, FAILED, Spec, _build

TEAM = "ownership/grp/sub"
UP = {**BASE, "grp/sub/a": (2, True), "grp/sub/b": (2, True)}  # the team at docs 2
GONE = (FAILED, True)


def _ups(history: list[tuple[str, Spec]], unit: str = TEAM, row: str = "docs") -> list[tuple[str, str, int | None]]:
    return [(a["run"], a["previous_run"], a["evidence"]["from_level"]) for a in _build(history)["achievements"]
            if a["kind"] == fa.LEVEL_UP and a["unit"] == unit and a["evidence"]["row"] == row]


@pytest.mark.parametrize(("history", "expected"), [
    # b fails on the run where a alone already reads docs 2, and returns at docs 2
    ([("2026-11-01", BASE), ("2026-11-08", BASE),
      ("2026-11-15", {**BASE, "grp/sub/a": (2, True), "grp/sub/b": GONE}), ("2026-11-22", UP)],
     [("2026-11-22", "2026-11-08", 1)]),
    # b fails on the run before the move and returns at docs 2
    ([("2026-11-01", BASE), ("2026-11-08", {**BASE, "grp/sub/b": GONE}), ("2026-11-15", UP)],
     [("2026-11-15", "2026-11-01", 1)]),
])
def test_a_failed_scan_before_the_move_never_loses_the_level_up(history: list[tuple[str, Spec]],
                                                                 expected: list[tuple[str, str, int]]) -> None:
    later = [*history, ("2026-11-29", UP), ("2026-12-06", UP)]
    assert _ups(later) == expected  # exactly once, on the run the move shows, never again


def test_a_move_credited_on_a_subset_is_not_credited_again_when_the_member_returns() -> None:
    history: list[tuple[str, Spec]] = [
        ("2026-11-01", BASE),  # {a, b} at docs 1
        ("2026-11-08", {**BASE, "grp/sub/b": GONE}),  # {a} at docs 1
        ("2026-11-15", {**BASE, "grp/sub/a": (2, True), "grp/sub/b": GONE}),  # {a} at docs 2: credited
        ("2026-11-22", UP),  # {a, b} at docs 2, compared with 11-01: the same move
    ]
    assert _ups(history) == [("2026-11-15", "2026-11-08", 1)]


def test_a_real_regression_and_recovery_fires_twice_and_a_failure_gap_once() -> None:
    drop = [("2026-11-01", BASE), ("2026-11-08", UP), ("2026-11-15", {**UP, "grp/sub/b": (1, True)}),
            ("2026-11-22", UP)]
    assert _ups(drop) == [("2026-11-08", "2026-11-01", 1), ("2026-11-22", "2026-11-15", 1)]
    gap = [("2026-11-01", BASE), ("2026-11-08", UP), ("2026-11-15", {**UP, "grp/sub/b": GONE}),
           ("2026-11-22", UP)]
    assert _ups(gap) == [("2026-11-08", "2026-11-01", 1)]


def test_a_slip_after_a_gap_still_resets_the_streak() -> None:
    """The run after a gap compares with the last run measuring the same repositories, both ways:
    a drop shows as a slip, so the streak starts again."""
    runs = [("2026-11-01", BASE), ("2026-11-08", BASE), ("2026-11-15", BASE),
            ("2026-11-22", {**BASE, "grp/c": GONE}), ("2026-11-29", {**BASE, "grp/sub/a": (0, True)}),
            ("2026-12-06", {**BASE, "grp/sub/a": (0, True)}), ("2026-12-13", {**BASE, "grp/sub/a": (0, True)})]
    streaks = [(a["run"], a["evidence"]["runs"]) for a in _build(runs)["achievements"]
               if a["kind"] == fa.STREAK and a["unit"] == "grp/sub/a"]
    assert streaks == [("2026-11-15", 3), ("2026-12-13", 3)]  # 11-29 slipped against 11-15


def test_two_runs_give_identical_bytes() -> None:
    history = [("2026-11-01", BASE), ("2026-11-08", {**BASE, "grp/sub/b": GONE}), ("2026-11-15", UP)]
    assert fa.dumps_achievements(_build(history)) == fa.dumps_achievements(_build(history))


def test_a_recovery_after_a_slip_on_a_subset_is_credited() -> None:
    """The slip on the subset run is the move the return compares from, so the recovery to the
    credited level is a new level_up, not lost to the earlier credit."""
    history: list[tuple[str, Spec]] = [
        ("2026-11-01", BASE),  # {a, b} at docs 1
        ("2026-11-08", {**BASE, "grp/sub/b": GONE}),  # {a} at docs 1
        ("2026-11-15", {**BASE, "grp/sub/a": (2, True), "grp/sub/b": GONE}),  # {a} at docs 2: credited
        ("2026-11-22", {**BASE, "grp/sub/b": GONE}),  # {a} at docs 1: a slip
        ("2026-11-29", UP),  # {a, b} at docs 2, compared with 11-01: a recovery
    ]
    assert _ups(history) == [("2026-11-15", "2026-11-08", 1), ("2026-11-29", "2026-11-01", 1)]


def test_one_drop_seen_on_two_runs_is_one_slip() -> None:
    """a drops once while b flaps: the drop shows against the full set on one run and against the
    subset on the next, but the slip after the first sets where the second starts."""
    flap: list[tuple[str, Spec]] = [
        ("2026-11-01", UP),  # {a, b} at docs 2
        ("2026-11-08", {**UP, "grp/sub/b": GONE}),  # {a} at docs 2
        ("2026-11-15", {**UP, "grp/sub/a": (1, True)}),  # {a, b} at docs 1: the slip
        ("2026-11-22", {**UP, "grp/sub/a": (1, True), "grp/sub/b": GONE}),  # {a} at docs 1: no second slip
        ("2026-11-29", {**UP, "grp/sub/a": (1, True)}), ("2026-12-06", {**UP, "grp/sub/a": (1, True)}),
    ]
    streaks = [a["run"] for a in _build(flap)["achievements"] if a["kind"] == fa.STREAK and a["unit"] == TEAM]
    # reset to 1 on 11-15 only; 11-22, 11-29 count (2, 3); a second reset on 11-22 would move it to 12-06
    assert streaks == ["2026-11-29"]


def test_a_member_returning_at_the_level_before_a_two_run_gap_is_no_slip() -> None:
    """#1060: b misses two runs, the subset {a} is credited up to docs 2, and b returns at docs 1.
    The full set reads docs 1, as before the gap: nothing moved, so no slip and no second
    level_up, and the team streak runs on."""
    history: list[tuple[str, Spec]] = [
        ("2026-11-01", BASE),  # {a, b} at docs 1
        ("2026-11-08", {**BASE, "grp/sub/b": GONE}),  # {a} at docs 1
        ("2026-11-15", {**BASE, "grp/sub/a": (2, True), "grp/sub/b": GONE}),  # {a} at docs 2: credited
        ("2026-11-22", {**BASE, "grp/sub/a": (2, True)}),  # {a, b} at docs 1, as on 11-01
        ("2026-11-29", {**BASE, "grp/sub/a": (2, True)}), ("2026-12-06", {**BASE, "grp/sub/a": (2, True)}),
    ]
    assert _ups(history) == [("2026-11-15", "2026-11-08", 1)]
    streaks = [a["run"] for a in _build(history)["achievements"] if a["kind"] == fa.STREAK and a["unit"] == TEAM]
    # 11-15 (2), 11-22 (3), 11-29 (4), 12-06 (5); a false slip on 11-22 would give 12-06 only
    assert streaks == ["2026-11-22", "2026-12-06"]
    # once the full set has read docs 1 again, b really reaching docs 2 is the team's own move
    on = [*history, ("2026-12-13", UP)]
    assert _ups(on) == [("2026-11-15", "2026-11-08", 1), ("2026-12-13", "2026-12-06", 1)]


def test_a_return_below_the_level_before_the_gap_is_a_slip() -> None:
    history: list[tuple[str, Spec]] = [
        ("2026-11-01", BASE),
        ("2026-11-08", {**BASE, "grp/sub/b": GONE}),
        ("2026-11-15", {**BASE, "grp/sub/a": (2, True), "grp/sub/b": GONE}),  # {a} credited at 2
        ("2026-11-22", {**BASE, "grp/sub/a": (2, True), "grp/sub/b": (0, True)}),  # {a, b} at 0: below both
        ("2026-11-29", {**BASE, "grp/sub/a": (2, True), "grp/sub/b": (0, True)}),
        ("2026-12-06", {**BASE, "grp/sub/a": (2, True), "grp/sub/b": (0, True)}),
    ]
    streaks = [a["run"] for a in _build(history)["achievements"] if a["kind"] == fa.STREAK and a["unit"] == TEAM]
    assert streaks == ["2026-12-06"]  # reset on 11-22, then 2 and 3


def test_two_job_counts_give_identical_bytes_on_the_two_run_gap() -> None:
    history = [("2026-11-01", BASE), ("2026-11-08", {**BASE, "grp/sub/b": GONE}),
               ("2026-11-15", {**BASE, "grp/sub/a": (2, True), "grp/sub/b": GONE}),
               ("2026-11-22", {**BASE, "grp/sub/a": (2, True)})]
    assert fa.dumps_achievements(_build(history)) == fa.dumps_achievements(_build(history))
