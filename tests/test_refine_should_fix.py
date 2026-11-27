"""The refiner's should-fix items after live mode (issue #1345).

The workflow-side items (one close group, the sweep's close cap, hidden
files in every upload, the plan artifact name as a job output, no triage of
the questions issue) are pinned in `test_refine_workflows.py`. These cover
the script side: the unsettled issues in the ready-buffer report, questions
and the report reaching the job summary when no questions issue is set, the
questions issue skipped, and one reading of a size marker's first line.
"""
import json
from pathlib import Path
from typing import Dict, List

import pytest
from refine_env import DEFINITION
from refine_write_env import actions, verdict, writer

import refine_apply  # noqa: E402  (refine_write_env put the scripts on sys.path)
import refine_checks  # noqa: E402
import refine_definition  # noqa: E402
import refine_platform  # noqa: E402
import refine_settings  # noqa: E402
import refine_write  # noqa: E402


def _unsettled(number: int, created: str, items: List[str]) -> Dict[str, object]:
    return {"number": number, "settled": False, "created_at": created,
            "checklist": [{"id": i, "status": "needs_judgement"} for i in items]}


# ── item 2: the scheduled path moves unsettled issues forward ────────────

def test_report_lists_unsettled_issues_oldest_first_with_their_open_items() -> None:
    v = {"issues": [_unsettled(9, "2026-11-03T00:00:00Z", ["scope_split"]),
                    _unsettled(4, "2026-11-01T00:00:00Z", ["sized", "criteria_checkable"]),
                    {"number": 5, "settled": True, "created_at": "2026-10-01T00:00:00Z", "checklist": []}]}
    line = refine_apply.unsettled_line(v)
    assert line == ("- **Unsettled:** 2 issue(s) need a maintainer's call on the open item(s), oldest first: "
                    "#4 (sized, criteria_checkable), #9 (scope_split).")
    assert line in refine_apply.report_body(v)


def test_report_names_at_most_ten_unsettled_issues() -> None:
    v = {"issues": [_unsettled(n, f"2026-11-{n:02d}T00:00:00Z", []) for n in range(1, 14)]}
    line = refine_apply.unsettled_line(v)
    assert line.startswith("- **Unsettled:** 13 issue(s)") and line.endswith("#10, and 3 more.")
    assert "#11" not in line


def test_report_has_no_unsettled_line_when_every_issue_is_settled() -> None:
    v = {"issues": [{"number": 5, "settled": True, "checklist": []}]}
    assert refine_apply.unsettled_line(v) == ""
    assert "Unsettled" not in refine_apply.report_body(v)


def test_sweep_report_carries_the_fixture_s_unsettled_issues(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {
        "issues/103": "issue_103_open.json", "issues/103/events": "events_bot_only.json",
        "issues/103/comments": "comments_none.json",
        "issues/101": "issue_101_open.json", "issues/100": "issue_100_open.json",
        "issues/101/events": "events_bot_only.json", "issues/events": "repo_events_9_closes.json",
        "issues/50/comments": "comments_report.json",
    }, REFINE_QUESTIONS_ISSUE="50")
    refine_apply.apply_sweep(w, verdict())
    body = gh.writes[-1][-1]
    assert "- **Unsettled:** 2 issue(s)" in body and "#101 (sized), #108 (scope_split)" in body


# ── item 3: questions reach the job summary with no questions issue ──────

def test_questions_go_to_the_job_summary_without_a_questions_issue(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {})
    assert w.post_questions([{"issue": 101, "question": "Split it? cc @someone"},
                             {"issue": 104, "question": "Who owns it?"}]) == 2
    assert gh.writes == []
    rows = actions(tmp_path)
    assert [r["action"] for r in rows] == ["question", "question"]
    text = refine_apply.summary(w.s)
    assert "## Questions for the maintainer" in text
    assert "- #101: Split it? cc @​someone" in text and "- #104: Who owns it?" in text
    assert "| post-questions.sh |" not in text, "questions are listed, not put in the actions table"


def test_questions_still_go_to_the_questions_issue_when_set(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {"issues/50/comments": "comments_none.json"}, REFINE_QUESTIONS_ISSUE="50")
    w.post_questions([{"issue": 101, "question": "Split it?"}])
    assert len(gh.writes) == 1
    assert "Questions for the maintainer" not in refine_apply.summary(w.s)


def test_triage_questions_reach_the_summary(tmp_path: Path) -> None:
    w, _ = writer(tmp_path, {"issues/101": "issue_101_open.json", "issues/101/comments": "comments_none.json",
                             "issues/101/events": "events_bot_only.json",
                             "issues/101/dependencies/blocked_by": "dependencies_none.json"})
    plan = {"issue": 101, "ready": False, "labels_add": [], "labels_remove": [], "size": None,
            "size_reason": "", "blocked_by": [], "questions": ["Is the API stable?"], "summary": ""}
    refine_apply.apply_triage(w, verdict(), 101, plan)
    assert "- #101: Is the API stable?" in refine_apply.summary(w.s)


def test_report_goes_to_the_job_summary_without_a_questions_issue(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {})
    w.post_report("### Ready buffer\n\n- **Depth:** 0")
    assert gh.writes == []
    text = refine_apply.summary(w.s)
    assert "## Ready-buffer report" in text and "- **Depth:** 0" in text
    assert "No actions." in text


# ── item 4: the questions issue is never triaged ─────────────────────────

def test_route_never_sends_the_questions_issue_to_the_model() -> None:
    assert refine_apply.route(verdict(), 101) is True
    assert refine_apply.route(verdict(), 101, questions_issue=101) is False


def test_route_cli_reads_the_questions_issue(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                                            tmp_path: Path) -> None:
    path = tmp_path / "verdict.json"
    path.write_text(json.dumps(verdict()), encoding="utf-8")
    monkeypatch.setenv("REFINE_QUESTIONS_ISSUE", "#101")
    assert refine_apply.main(["route", "--verdict", str(path), "--issue", "101"]) == 0
    assert capsys.readouterr().out.strip() == "model=false"


def test_settled_skips_the_questions_issue(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {}, REFINE_QUESTIONS_ISSUE="103")
    refine_apply.apply_settled(w, verdict(), 103)
    assert gh.writes == [] and actions(tmp_path) == []


def test_sweep_does_not_label_the_questions_issue(tmp_path: Path) -> None:
    # #103 is settled and ready: without the skip the sweep labels it (test_sweep_labels_closes_and_reports).
    w, gh = writer(tmp_path, {
        "issues/101": "issue_101_open.json", "issues/100": "issue_100_open.json",
        "issues/101/events": "events_bot_only.json", "issues/events": "repo_events_9_closes.json",
        "issues/103/comments": "comments_report.json",
    }, REFINE_QUESTIONS_ISSUE="103")
    refine_apply.apply_sweep(w, verdict())
    assert not any(a[:3] == ["issue", "edit", "103"] for a in gh.writes)
    assert gh.writes[-1][:3] == ["api", "-X", "PATCH"], "the report is still posted to the questions issue"


def test_report_does_not_list_the_questions_issue_as_unsettled() -> None:
    assert "#108" in refine_apply.unsettled_line(verdict())
    line = refine_apply.unsettled_line(verdict(), 108)
    assert "#108" not in line and line.startswith("- **Unsettled:** 1 issue(s)")


# ── item 8: one reading of a size marker's first line ────────────────────

@pytest.mark.parametrize("body, line", [
    ("<!-- refine:size 3 -->\nwhy", "<!-- refine:size 3 -->"),
    ("\n\n  <!-- refine:size 3 -->  \nwhy", "<!-- refine:size 3 -->"),
    ("Context first.\n<!-- refine:size 2 -->", "Context first."),
    ("   \n", ""),
])
def test_marker_line(body: str, line: str) -> None:
    assert refine_definition.marker_line(body) == line


def test_verdict_and_writer_read_a_marker_after_a_blank_line_the_same(tmp_path: Path) -> None:
    body = "\n<!-- refine:size 5 -->\nbigger than it looks"
    definition = refine_definition.load_definition(DEFINITION)
    issue = refine_platform.github_issue({"number": 9, "createdAt": "2026-11-01T00:00:00Z", "comments": [
        {"author": {"login": "maintainer"}, "authorAssociation": "OWNER", "body": body,
         "createdAt": "2026-11-02T00:00:00Z"}]})
    assert refine_checks.size_reading(issue, definition) == (5, "marker")
    comments = json.dumps([{"id": 9, "user": {"login": "maintainer", "type": "User"},
                            "author_association": "OWNER", "body": body}])
    w, _ = writer(tmp_path, {"issues/101/comments": comments, "issues/101": "issue_101_open.json"})
    assert w.current_size(101, verdict()) == 5


# ── the comment on #1345: Settings moved out of refine_write.py ──────────

def test_settings_moved_and_still_reachable_from_refine_write() -> None:
    assert refine_write.Settings is refine_settings.Settings
    assert refine_write.DEFAULT_MAX_POINTS == refine_settings.DEFAULT_MAX_POINTS == 8
