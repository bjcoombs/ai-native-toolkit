"""The refiner's write guardrails, against recorded `gh` output (issue #1321).

Each test drives one `refine_write.Writer` operation with a `FakeGh` runner
(`refine_write_env.py`) and asserts which writes reached `gh`.
"""
from pathlib import Path
from typing import Dict

import pytest
from refine_write_env import actions, verdict, writer

import refine_write
from refine_write import Refused

OPEN_101 = {"issues/101": "issue_101_open.json", "issues/101/events": "events_bot_only.json"}
OPEN_103 = {"issues/103": "issue_103_open.json", "issues/103/events": "events_bot_only.json"}


def _plan(**kw: object) -> Dict[str, object]:
    plan: Dict[str, object] = {"issue": 101, "ready": True, "labels_add": [], "labels_remove": [], "size": 2,
                               "size_reason": "one module", "blocked_by": [], "questions": [], "summary": ""}
    plan.update(kw)
    return plan


# ── label allow-list and veto ────────────────────────────────────────────

def test_label_outside_allow_list_is_refused(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, OPEN_101)
    result = w.edit_labels(101, ["bug", "wontfix"], [], verdict())
    assert result["added"] == ["bug"]
    assert any("wontfix" in r for r in result["refused"])
    assert gh.writes == [["issue", "edit", "101", "--repo", "acme/widgets", "--add-label", "bug"]]


def test_refiner_never_removes_the_ready_label(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {"issues/100": "issue_100_open.json", "issues/100/events": "events_bot_only.json"})
    result = w.edit_labels(100, [], ["agent-ready"], verdict())
    assert result["removed"] == []
    assert gh.writes == []


def test_label_on_closed_issue_is_refused(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {"issues/106": "issue_106_closed.json"})
    with pytest.raises(Refused, match="not open"):
        w.edit_labels(106, ["bug"], [])
    assert gh.writes == []


# ── the agent-ready rule ─────────────────────────────────────────────────

def test_ready_applied_to_a_settled_ready_issue_and_triage_label_removed(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, OPEN_103)
    w.edit_labels(103, ["agent-ready"], [], verdict())
    assert gh.writes == [["issue", "edit", "103", "--repo", "acme/widgets",
                          "--add-label", "agent-ready", "--remove-label", "needs-triage"]]


def test_ready_never_reapplied_after_a_person_removed_it(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {"issues/103": "issue_103_open.json", "issues/103/events": "events_vetoed.json"})
    result = w.edit_labels(103, ["agent-ready"], [], verdict())
    assert any("a person removed it" in r for r in result["refused"])
    assert gh.writes == []


def test_ready_refused_when_unsettled_and_no_plan(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, OPEN_101)
    result = w.edit_labels(101, ["agent-ready"], [], verdict())
    assert any("no plan resolves them" in r for r in result["refused"])
    assert gh.writes == []


def test_ready_refused_when_the_plan_leaves_a_question(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, OPEN_101)
    result = w.edit_labels(101, ["agent-ready"], [], verdict(), _plan(questions=["Split it?"]))
    assert any("question" in r for r in result["refused"])
    assert gh.writes == []


def test_ready_applied_when_the_plan_resolves_judgement(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, OPEN_101)
    w.edit_labels(101, ["agent-ready"], [], verdict(), _plan())
    assert gh.writes[0][-4:] == ["--add-label", "agent-ready", "--remove-label", "needs-triage"]


def test_ready_refused_when_the_plan_size_exceeds_max_points(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, OPEN_101)
    result = w.edit_labels(101, ["agent-ready"], [], verdict(), _plan(size=5))
    assert any("not between 1 and 3" in r for r in result["refused"])
    assert gh.writes == []


@pytest.mark.parametrize("number, reason", [(104, "script-checked item fails"),
                                            (105, "open pull request overlaps"),
                                            (999, "no refinement-script verdict")])
def test_ready_refused_by_the_script_verdict(tmp_path: Path, number: int, reason: str) -> None:
    w, gh = writer(tmp_path, {f"issues/{number}": "issue_103_open.json",
                              f"issues/{number}/events": "events_bot_only.json"})
    result = w.edit_labels(number, ["agent-ready"], [], verdict(), _plan(issue=number))
    assert any(reason in r for r in result["refused"])
    assert gh.writes == []


def test_ready_refused_for_an_exact_duplicate() -> None:
    v = verdict()
    v["issues"][0]["duplicates"] = [{"number": 100, "kind": "exact", "basis": "title", "similarity": 1.0,  # type: ignore[index]
                                     "survivor": 100, "closable": True}]
    assert "exact duplicate" in refine_write.ready_blocker(101, v, _plan(), 3)


# ── duplicate closes ─────────────────────────────────────────────────────

DUP_ROUTES = {"issues/101": "issue_101_open.json", "issues/100": "issue_100_open.json",
              "issues/101/events": "events_bot_only.json", "issues/events": "repo_events_9_closes.json"}


def test_exact_duplicate_closed_with_comment_and_reopen_route(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, DUP_ROUTES)
    w.close_duplicate(101, 100, verdict())
    comment, close = gh.writes
    assert comment[:2] == ["issue", "comment"] and "#100" in comment[-1] and "/reopen" in comment[-1]
    assert close == ["api", "-X", "PATCH", "repos/acme/widgets/issues/101",
                     "-f", "state=closed", "-f", "state_reason=duplicate"]


def test_near_duplicate_never_closed(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, DUP_ROUTES)
    with pytest.raises(Refused, match="no exact, closable pair"):
        w.close_duplicate(105, 100, verdict())
    assert gh.writes == []


def test_eleventh_close_in_seven_days_refused(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {**DUP_ROUTES, "issues/events": "repo_events_10_closes.json"})
    with pytest.raises(Refused, match="weekly close cap reached \\(10 of 10\\)"):
        w.close_duplicate(101, 100, verdict())
    assert gh.writes == []


def test_closes_in_this_run_count_against_the_cap(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, DUP_ROUTES)
    w.close_duplicate(101, 100, verdict())
    with pytest.raises(Refused, match="weekly close cap"):
        w.close_duplicate(101, 100, verdict())
    assert len(gh.writes) == 2


def test_close_refused_when_the_survivor_is_closed(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {**DUP_ROUTES, "issues/100": "issue_106_closed.json"})
    with pytest.raises(Refused, match="#100 is not open"):
        w.close_duplicate(101, 100, verdict())
    assert gh.writes == []


def test_close_refused_after_a_person_reopened(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {**DUP_ROUTES, "issues/101/events": "events_reopened.json"})
    with pytest.raises(Refused, match="reopened"):
        w.close_duplicate(101, 100, verdict())
    assert gh.writes == []


def test_candidates_named_in_one_comment_and_never_closed(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {"issues/101/comments": "comments_none.json", "issues/100": "issue_100_open.json",
                              "issues/106": "issue_106_closed.json"})
    named = w.comment_on_duplicates(101, [{"number": 100, "reason": "same bug @someone"},
                                          {"number": 106, "reason": "closed"}])
    assert named == [100]
    assert len(gh.writes) == 1 and gh.writes[0][:2] == ["issue", "comment"]
    assert "@​someone" in gh.writes[0][-1]


def test_candidates_not_listed_twice(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {"issues/101/comments": "comments_dup_listed.json"})
    with pytest.raises(Refused, match="already listed"):
        w.comment_on_duplicates(101, [{"number": 100, "reason": "x"}])
    assert gh.writes == []


# ── size markers and blocked-by edges ────────────────────────────────────

def test_size_marker_is_the_first_line(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {"issues/101/comments": "comments_none.json"})
    assert w.write_size_marker(101, 5, "two modules\nand docs")
    assert gh.writes[0][-1] == "<!-- refine:size 5 -->\ntwo modules and docs"


def test_size_marker_skipped_when_unchanged(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {"issues/101/comments": "comments_size_3.json"})
    assert not w.write_size_marker(101, 3, "same")
    assert gh.writes == []


def test_size_marker_refuses_a_non_fibonacci_size(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {})
    with pytest.raises(Refused, match="Fibonacci"):
        w.write_size_marker(101, 4, "x")


def test_blocked_by_edge_posts_the_numeric_id(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {"issues/101/dependencies/blocked_by": "dependencies_none.json",
                              "issues/100/dependencies/blocked_by": "dependencies_none.json",
                              "issues/100": "issue_100_open.json"})
    assert w.add_blocked_by(101, 100)
    assert gh.writes == [["api", "-X", "POST", "repos/acme/widgets/issues/101/dependencies/blocked_by",
                          "-F", "issue_id=9000100"]]


def test_blocked_by_refuses_self_and_two_cycles(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {"issues/101/dependencies/blocked_by": "dependencies_none.json",
                              "issues/100/dependencies/blocked_by": "dependencies_101.json",
                              "issues/100": "issue_100_open.json"})
    with pytest.raises(Refused, match="itself"):
        w.add_blocked_by(101, 101)
    with pytest.raises(Refused, match="cycle"):
        w.add_blocked_by(101, 100)
    assert gh.writes == []


# ── questions and the ready-buffer report ────────────────────────────────

def test_questions_batched_into_one_comment_on_the_questions_issue(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {"issues/50/comments": "comments_none.json"}, REFINE_QUESTIONS_ISSUE="#50")
    assert w.post_questions([{"issue": 101, "question": "Split it?"}, {"issue": 104, "question": "Who owns it?"}]) == 2
    assert len(gh.writes) == 1
    assert gh.writes[0][:3] == ["issue", "comment", "50"]
    assert "- #101: Split it?" in gh.writes[0][-1] and "- #104: Who owns it?" in gh.writes[0][-1]


def test_questions_refused_without_a_questions_issue(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {})
    with pytest.raises(Refused, match="REFINE_QUESTIONS_ISSUE"):
        w.post_questions([{"issue": 101, "question": "Split it?"}])


def test_report_edits_the_bot_comment_in_place(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {"issues/50/comments": "comments_report.json"}, REFINE_QUESTIONS_ISSUE="50")
    w.post_report("### Ready buffer")
    assert gh.writes[0][:4] == ["api", "-X", "PATCH", "repos/acme/widgets/issues/comments/504"]


def test_report_created_when_none_exists(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {"issues/50/comments": "comments_none.json"}, REFINE_QUESTIONS_ISSUE="50")
    w.post_report("### Ready buffer")
    assert gh.writes[0][:3] == ["issue", "comment", "50"]


# ── /reopen ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("commenter, routes", [
    ("alice", {}),
    ("carol", {"collaborators/carol/permission": "permission_write.json"}),
])
def test_reopen_by_author_or_writer(tmp_path: Path, commenter: str, routes: Dict[str, str]) -> None:
    w, gh = writer(tmp_path, {"issues/102": "issue_102_closed_duplicate.json", **routes})
    w.reopen(102, commenter)
    assert gh.writes[0] == ["api", "-X", "PATCH", "repos/acme/widgets/issues/102",
                            "-f", "state=open", "-f", "state_reason=reopened"]
    assert ["--add-label", "needs-triage"] == gh.writes[1][-2:]


@pytest.mark.parametrize("number, commenter, routes, reason", [
    (102, "mallory", {"collaborators/mallory/permission": "permission_read.json"}, "neither the author"),
    (107, "alice", {}, "a person closed it"),
    (101, "alice", {}, "not closed as a duplicate"),
])
def test_reopen_refused(tmp_path: Path, number: int, commenter: str, routes: Dict[str, str], reason: str) -> None:
    files = {102: "issue_102_closed_duplicate.json", 107: "issue_107_closed_by_person.json", 101: "issue_101_open.json"}
    w, gh = writer(tmp_path, {f"issues/{number}": files[number], **routes})
    with pytest.raises(Refused, match=reason):
        w.reopen(number, commenter)
    assert gh.writes == []


# ── call caps and dry-run ────────────────────────────────────────────────

def test_call_cap_refuses_the_next_run(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {"issues/101/comments": "comments_none.json"}, caps={"write-size-marker.sh": 1})
    w.write_size_marker(101, 2, "small")
    with pytest.raises(Refused, match="call cap reached \\(1 of 1\\)"):
        w.write_size_marker(101, 3, "bigger")
    assert len(gh.writes) == 1


def test_call_cap_persists_across_processes(tmp_path: Path) -> None:
    routes = {"issues/101/comments": "comments_none.json"}
    first, _ = writer(tmp_path, routes, caps={"write-size-marker.sh": 1})
    first.write_size_marker(101, 2, "small")
    second, gh = writer(tmp_path, routes, caps={"write-size-marker.sh": 1})
    with pytest.raises(Refused, match="call cap"):
        second.write_size_marker(101, 3, "bigger")
    assert gh.writes == []


def test_script_missing_from_the_caps_is_refused(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {}, caps={"edit-issue-labels.sh": 1})
    with pytest.raises(Refused, match="not named in CLAUDE_CODE_SCRIPT_CAPS"):
        w.write_size_marker(101, 2, "small")


def test_dry_run_makes_no_write_and_logs_every_action(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {**DUP_ROUTES, **OPEN_103, "issues/101/comments": "comments_none.json",
                              "issues/50/comments": "comments_none.json"},
                   live=False, REFINE_QUESTIONS_ISSUE="50")
    w.edit_labels(103, ["agent-ready"], [], verdict())
    w.close_duplicate(101, 100, verdict())
    w.write_size_marker(101, 2, "small")
    w.post_questions([{"issue": 101, "question": "Split it?"}])
    assert gh.writes == []
    logged = [a["action"] for a in actions(tmp_path)]
    assert logged == ["edit-labels", "comment-close", "close-duplicate", "size-marker", "questions"]
    assert {a["mode"] for a in actions(tmp_path)} == {"dry-run"}


@pytest.mark.parametrize("mode", ["", "dry-run", "LIVE", "true"])
def test_only_live_is_live(tmp_path: Path, mode: str) -> None:
    assert not refine_write.Settings({"GH_REPO": "a/b", "REFINE_MODE": mode}).live


# ── the CLI ──────────────────────────────────────────────────────────────

def test_cli_exit_code_four_on_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_REPO", "acme/widgets")
    monkeypatch.setenv("REFINE_STATE_DIR", str(tmp_path))
    code = refine_write.main(["size-marker", "--issue", "101", "--points", "4", "--reason", "x"],
                             runner=lambda args: "[]")
    assert code == 4


def test_json_values_flattens_paginated_pages() -> None:
    assert refine_write.json_values('[{"a": 1}]\n[{"a": 2}]') == [{"a": 1}, {"a": 2}]
    with pytest.raises(refine_write.GhError):
        refine_write.json_values("not json")


# ── review round 1 (#1336) ───────────────────────────────────────────────

def test_triage_label_removed_only_with_the_ready_label(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, OPEN_101)
    result = w.edit_labels(101, ["bug"], ["needs-triage"], verdict())
    assert result["removed"] == [] and any("needs-triage" in r for r in result["refused"])
    assert gh.writes == [["issue", "edit", "101", "--repo", "acme/widgets", "--add-label", "bug"]]


def test_cap_counts_only_the_refiners_duplicate_closes(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {**DUP_ROUTES, "issues/events": "repo_events_mixed_closes.json"})
    assert w.bot_closes_in_window() == 9
    w.close_duplicate(101, 100, verdict())
    assert gh.writes[-1][-1] == "state_reason=duplicate"


def test_same_questions_not_posted_twice(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {"issues/50/comments": "comments_questions_asked.json"}, REFINE_QUESTIONS_ISSUE="50")
    assert w.post_questions([{"issue": 101, "question": "Split it?"}]) == 0
    assert gh.writes == []


def test_blocker_that_is_a_pull_request_refused_before_other_reads(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {"issues/101/dependencies/blocked_by": "dependencies_none.json",
                              "issues/90": '{"id": 1, "number": 90, "state": "open", "pull_request": {}}'})
    with pytest.raises(Refused, match="pull request"):
        w.add_blocked_by(101, 90)
    assert gh.writes == []
