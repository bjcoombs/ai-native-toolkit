"""The pruning ledger (lib/pruning_ledger.py, #1084): holds, resets, credit and its wording."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.gap_actions import REMOVAL_FINDING
from lib.prior_level import PruningEntry
from lib.pruning_ledger import HOLD_RUNS, ledger_lines, removal, update_ledger
from lib.since_last_run import pruning_record

SHA = "a" * 40
ROOT = Path(__file__).resolve().parent  # no CLAUDE.md here: an unlisted file reads gone
GATES = ["instructions", "ci_runs_tests"]


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


def _run(prior: list[PruningEntry], placement: Any, *, head: str, held_now: list[str] | None = GATES,
         held_before: list[str] | None = GATES, new: bool = True,
         root: Path = ROOT) -> tuple[list[PruningEntry], list[PruningEntry]]:
    return update_ledger(prior, [_new()], placement, held_now=held_now, held_before=held_before,
                         head_sha=head, new_commit=new, repo_root=root)


def test_hold_runs_is_two() -> None:
    assert HOLD_RUNS == 2


def test_a_removal_is_a_done_remove_action_with_a_measured_cost() -> None:
    assert _new() == {"path": "CLAUDE.md", "completed_sha": SHA, "cost_before": 312, "runs_held": 0,
                      "credited": False, "saved": None, "gates": None, "credited_at": None}
    for bad in ({**_done(), "status": "pending"}, {**_done(), "finding": "lying_map"},
                {**_done(), "cost": {"path": "CLAUDE.md", "value": "312"}}, {**_done(), "completed_sha": None}):
        assert removal(bad) is None


def test_two_held_runs_credit_the_measured_drop_once_and_a_rerun_shows_it_again() -> None:
    entries, credited = _run([], _placement(False, 180), head="c1")
    assert (entries[0]["runs_held"], entries[0]["gates"], credited) == (1, GATES, [])
    entries, credited = _run(entries, _placement(False, 170), head="c2")
    assert credited == entries and entries[0]["saved"] == 312 - 170 and entries[0]["credited_at"] == "c2"
    assert ledger_lines(entries, credited) == ["Pruned: always-on text in `CLAUDE.md`, saved 142 always-on lines"]
    # a re-run on the same commit shows the same credit; a later commit neither counts nor lists it
    assert _run(entries, _placement(False, 170), head="c2", new=False)[1] == entries
    later, credited = _run(entries, _placement(False, 170), head="c3")
    assert (later, credited, ledger_lines(later, credited)) == (entries, [], [])


def test_a_flag_or_a_missing_gate_resets_the_count() -> None:
    held, _ = _run([], _placement(False, 180), head="c1")
    flagged, _ = _run(held, _placement(True, 260), head="c2")
    lost, _ = _run(held, _placement(False, 180), head="c2", held_now=["instructions"])
    assert flagged[0]["runs_held"] == lost[0]["runs_held"] == 0


def test_a_gate_the_removal_broke_for_good_is_never_credited() -> None:
    """Review of #1134: the entry keeps the gates held before the removal, not "lost since last run"."""
    entries, _ = _run([], _placement(False, 180), head="c1", held_now=["instructions"])
    for head in ("c2", "c3", "c4"):
        entries, credited = _run(entries, _placement(False, 180), head=head, held_now=["instructions"])
        assert (entries[0]["runs_held"], credited) == (0, [])


def test_a_known_failure_resets_even_beside_an_unknown() -> None:
    held, _ = _run([], _placement(False, 180), head="c1")
    after, _ = _run(held, {"available": False}, head="c2", held_now=["instructions"])
    assert after[0]["runs_held"] == 0


def test_an_unknown_alone_or_the_same_commit_leaves_the_count_alone() -> None:
    held, _ = _run([], _placement(False, 180), head="c1")
    for placement, now, new in (({"available": False}, GATES, True), (_placement(False, 180), None, True),
                                (_placement(False, 180), GATES, False)):
        after, credited = _run(held, placement, head="c2", held_now=now, new=new)
        assert (after[0]["runs_held"], credited) == (1, [])


def test_a_deleted_file_saves_its_whole_cost_and_pending_removals_are_listed() -> None:
    entries, _ = _run([], _placement(False, 0, present=False), head="c1")
    assert ledger_lines(entries, []) == [
        "Pruning in progress: `CLAUDE.md` (1 of 2 runs with the file unflagged and no gate lost)"]
    entries, credited = _run(entries, _placement(False, 0, present=False), head="c2")
    assert credited[0]["saved"] == 312


def test_a_file_the_scan_does_not_list_but_still_on_disk_is_unknown(tmp_path: Path) -> None:
    """Review of #1134: a nested scan that did not run lists no file; that is no saving."""
    (tmp_path / "CLAUDE.md").write_text("still here\n", encoding="utf-8")
    held, _ = _run([], _placement(False, 180), head="c1", root=tmp_path)
    after, credited = _run(held, _placement(False, 0, present=False), head="c2", root=tmp_path)
    assert (after[0]["runs_held"], credited) == (1, [])


def test_a_degraded_scan_keeps_the_stored_ledger() -> None:
    """Review of #1134: dropping it would let the same removal be credited a second time."""
    stored = [{**_new(), "credited": True, "saved": 100, "credited_at": "c2", "gates": GATES}]
    ctx = {"since_last_run": {"available": False, "reason": "boom"}, "prior_level": {"pruning": stored}}
    assert pruning_record(ctx) == stored
    assert pruning_record({"since_last_run": {"available": True, "pruning": []}}) == []


def test_a_pending_removal_no_longer_done_on_head_is_dropped_and_a_credited_one_stays() -> None:
    """Review of #1134: a squashed-away commit must not keep counting beside a fresh entry."""
    pending, _ = _run([], _placement(False, 180), head="c1")
    after, _ = update_ledger(pending, [], _placement(False, 180), held_now=GATES, held_before=GATES,
                             head_sha="c2", new_commit=True, repo_root=ROOT)
    assert after == []
    credited = [{**pending[0], "credited": True, "saved": 100, "credited_at": "c2"}]
    kept, _ = update_ledger(credited, [], _placement(False, 180), held_now=GATES, held_before=GATES,  # type: ignore[arg-type]
                            head_sha="c3", new_commit=True, repo_root=ROOT)
    assert kept == credited


def test_unknown_done_removals_leave_the_ledger_as_it_was() -> None:
    """Review of #1134: an uncommitted actions.json or a shallow clone is not "every removal undone"."""
    pending, _ = _run([], _placement(False, 180), head="c1")
    after, credited = update_ledger(pending, None, _placement(False, 180), held_now=GATES, held_before=GATES,
                                    head_sha="c2", new_commit=True, repo_root=ROOT)
    assert (after, credited) == (pending, [])
    later, credited = _run(after, _placement(False, 170), head="c3")
    assert credited and later[0]["credited"]


def test_unflagged_by_share_alone_saves_nothing_and_is_not_held() -> None:
    """Review of #1134: no drop in always-on lines, no hold and no "saved 0" credit."""
    entries, _ = _run([], _placement(False, 312), head="c1")
    entries, credited = _run(entries, _placement(False, 312), head="c2")
    assert (entries[0]["runs_held"], credited) == (0, [])


def test_a_file_trimmed_in_two_rounds_is_credited_once_for_the_whole_drop() -> None:
    """#1168: 261 -> 150 (still flagged) -> 60 credits one removal of 201 lines, not 201 + 90."""
    def done(sha: str, value: int) -> PruningEntry:
        found = removal({**_done(value), "completed_sha": sha})
        assert found is not None
        return found
    first, second = done("1" * 40, 261), done("2" * 40, 150)

    def step(prior: list[PruningEntry], removals: list[PruningEntry], placement: Any,
             head: str) -> tuple[list[PruningEntry], list[PruningEntry]]:
        return update_ledger(prior, removals, placement, held_now=GATES, held_before=GATES, head_sha=head,
                             new_commit=True, repo_root=ROOT)
    entries, _ = step([], [first], _placement(True, 150), "c1")  # round one: still flagged
    assert entries[0]["runs_held"] == 0
    entries, _ = step(entries, [first, second], _placement(False, 60), "c2")  # round two lands
    assert len(entries) == 1 and entries[0]["cost_before"] == 261 and entries[0]["merged"] == ["1" * 40]
    entries, credited = step(entries, [first, second], _placement(False, 60), "c3")
    assert [(e["saved"], e["completed_sha"]) for e in credited] == [(201, "2" * 40)]
    assert ledger_lines(entries, credited) == ["Pruned: always-on text in `CLAUDE.md`, saved 201 always-on lines"]
    # the folded round is never added again
    later, credited = step(entries, [first, second], _placement(False, 60), "c4")
    assert (later, credited) == (entries, [])
