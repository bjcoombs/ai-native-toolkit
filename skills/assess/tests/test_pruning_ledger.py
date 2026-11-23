"""The pruning ledger (lib/pruning_ledger.py, #1084): holds, resets, credit and its wording."""
from __future__ import annotations

from typing import Any

from lib.gap_actions import REMOVAL_FINDING
from lib.prior_level import PruningEntry
from lib.pruning_ledger import HOLD_RUNS, ledger_lines, removal, update_ledger

SHA = "a" * 40


def _placement(flagged: bool, lines: int, present: bool = True) -> dict[str, Any]:
    files = [{"path": "CLAUDE.md", "flagged": flagged, "always_on_lines": lines}] if present else []
    return {"available": True, "files": files}


def _done(value: int = 312) -> dict[str, Any]:
    return {"finding": REMOVAL_FINDING, "status": "done", "completed_sha": SHA,
            "cost": {"metric": "always_on_lines", "path": "CLAUDE.md", "value": value}}


def _new() -> PruningEntry:
    found = removal(_done())
    assert found is not None
    return found


def test_hold_runs_is_two() -> None:
    assert HOLD_RUNS == 2


def test_a_removal_is_a_done_remove_action_with_a_measured_cost() -> None:
    assert _new() == {"path": "CLAUDE.md", "completed_sha": SHA, "cost_before": 312, "runs_held": 0,
                      "credited": False, "saved": None}
    for bad in ({**_done(), "status": "pending"}, {**_done(), "finding": "lying_map"},
                {**_done(), "cost": {"path": "CLAUDE.md", "value": "312"}}, {**_done(), "completed_sha": None}):
        assert removal(bad) is None


def test_two_held_runs_credit_the_measured_drop_and_score_nothing() -> None:
    entries, credited = update_ledger([], [_new()], _placement(False, 180), [], new_commit=True)
    assert (entries[0]["runs_held"], credited) == (1, [])
    entries, credited = update_ledger(entries, [_new()], _placement(False, 170), [], new_commit=True)
    assert entries[0]["credited"] and credited == entries
    assert entries[0]["saved"] == 312 - 170
    assert ledger_lines(entries, credited) == ["Pruned: always-on text in `CLAUDE.md`, saved 142 always-on lines"]
    # credited once: a later run neither counts it again nor lists it
    again, credited = update_ledger(entries, [_new()], _placement(False, 170), [], new_commit=True)
    assert (again, credited, ledger_lines(again, credited)) == (entries, [], [])


def test_a_flag_or_a_lost_gate_resets_the_count() -> None:
    held, _ = update_ledger([], [_new()], _placement(False, 180), [], new_commit=True)
    flagged, _ = update_ledger(held, [], _placement(True, 260), [], new_commit=True)
    lost, _ = update_ledger(held, [], _placement(False, 180), ["instructions"], new_commit=True)
    assert flagged[0]["runs_held"] == lost[0]["runs_held"] == 0


def test_an_unknown_or_the_same_commit_leaves_the_count_alone() -> None:
    held, _ = update_ledger([], [_new()], _placement(False, 180), [], new_commit=True)
    for placement, lost, new in (({"available": False}, [], True), (_placement(False, 180), None, True),
                                 (_placement(False, 180), [], False)):
        after, credited = update_ledger(held, [], placement, lost, new_commit=new)
        assert (after[0]["runs_held"], credited) == (1, [])


def test_a_deleted_file_saves_its_whole_cost_and_pending_removals_are_listed() -> None:
    entries, _ = update_ledger([], [_new()], _placement(False, 0, present=False), [], new_commit=True)
    assert ledger_lines(entries, []) == [
        "Pruning in progress: `CLAUDE.md` (1 of 2 runs with the file unflagged and every gate held)"]
    entries, credited = update_ledger(entries, [], _placement(False, 0, present=False), [], new_commit=True)
    assert credited[0]["saved"] == 312
