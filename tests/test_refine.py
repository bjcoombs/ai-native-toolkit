"""Behavioural tests for skills/refine/scripts/refine.py (issue #1320).

Every run replays the recorded `gh` output in tests/fixtures/refine/ (see
refine_env.py), checked against the real definition of ready in
docs/contributing/definition-of-ready.md. The fixture issues are built so
each check has a case that passes and one that fails; the issue numbers below
name them.
"""
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import pytest

from refine_env import DEFINITION, ENTRY, FIXTURE_REPO, NOW, SCHEMA, SCRIPTS, RecordedRunner, recorded_backlog

import refine  # noqa: E402  (refine_env put the scripts on sys.path)
import refine_checks  # noqa: E402
import refine_definition  # noqa: E402
import refine_platform  # noqa: E402

NOW_DT = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def definition():
    return refine_definition.load_definition(DEFINITION)


@pytest.fixture(scope="module")
def plan(definition):
    return refine.build_plan(recorded_backlog(), definition, "def.md", NOW_DT, True, None, 2)


def by_number(plan):
    return {p["number"]: p for p in plan["issues"]}


def item(issue_plan, item_id):
    return next(r for r in issue_plan["checklist"] if r["id"] == item_id)


def run_main(tmp_path, *extra, definition=DEFINITION):
    out = tmp_path / "plan.json"
    code = refine.main(["--repo", FIXTURE_REPO, "--definition", str(definition), "--now", NOW,
                        "--out", str(out), *extra], backlog=recorded_backlog())
    return code, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else None)


# --- Missing sections -------------------------------------------------------

def test_body_missing_one_section_is_reported(plan):
    issue = by_number(plan)[102]
    assert issue["missing_sections"] == ["Root Cause"]
    assert item(issue, "sections-present")["status"] == "fail"
    assert issue["verdict"] == "not_ready"
    assert any(r.startswith("sections-present: missing: Root Cause") for r in issue["reasons"])


def test_empty_form_field_counts_as_missing(plan):
    assert by_number(plan)[107]["missing_sections"] == ["Impact"]


def test_clarified_scope_comment_supplies_sections(plan):
    assert by_number(plan)[108]["missing_sections"] == []


PROSE_BODY = ("## Problem Statement\n\nbody problem\n\n"
              "## Success Criteria\n\nIt should work well once fixed.\n")


def clarified(created_at, text):
    return {"body": "<!-- triage:clarified-scope -->\n" + text, "createdAt": created_at}


def criteria_result(definition, comments):
    issue = refine_platform.github_issue({"number": 70, "body": PROSE_BODY,
                                          "createdAt": "2026-11-01T00:00:00Z", "comments": comments})
    return refine_checks.all_sections(issue, definition), refine_checks._criteria_item(issue, definition)


def test_later_clarified_checklist_replaces_prose_criteria(definition):
    sections, (status, _, _) = criteria_result(definition, [
        clarified("2026-11-02T00:00:00Z", "## Success Criteria\n\n- `make check` exits 0\n")])
    assert sections["Success Criteria"].strip() == "- `make check` exits 0"
    assert status == "needs_judgement"
    _, (prose_status, _, _) = criteria_result(definition, [])
    assert prose_status == "fail"


def test_newest_clarified_comment_wins(definition):
    # Listed newest first, so the creation time, not the list order, decides.
    sections, _ = criteria_result(definition, [
        clarified("2026-11-03T00:00:00Z", "## Success Criteria\n\n- newer `a`\n"),
        clarified("2026-11-02T00:00:00Z", "## Success Criteria\n\n- older `b`\n")])
    assert sections["Success Criteria"].strip() == "- newer `a`"


def test_clarified_comment_leaves_sections_it_lacks_alone(definition):
    sections, _ = criteria_result(definition, [
        clarified("2026-11-02T00:00:00Z", "## Success Criteria\n\n- `make check` exits 0\n")])
    assert sections["Problem Statement"].strip() == "body problem"


def test_heading_inside_code_fence_is_not_a_section(definition):
    text = "```\n## Impact\nfenced\n```\n## Problem Statement\n\nreal\n"
    assert set(refine_checks.section_texts(text, definition)) == {"Problem Statement"}


def test_deeper_heading_counts_as_content_and_level_one_does_not_match(definition):
    text = "## Impact\n### Detail\n# Root Cause\n\nbody\n"
    assert set(refine_checks.section_texts(text, definition)) == {"Impact"}


def test_missing_section_check_follows_the_document(tmp_path, definition):
    # Success Criteria 3: rename a heading in a copy of the block and the check follows.
    copy = tmp_path / "definition-of-ready.md"
    text = DEFINITION.read_text(encoding="utf-8")
    assert "  - Root Cause\n" in text
    copy.write_text(text.replace("  - Root Cause\n", "  - Root Causes\n"), encoding="utf-8")
    renamed = refine_definition.load_definition(copy)
    plan = refine.build_plan(recorded_backlog(), renamed, "copy", NOW_DT, False, None, 2)
    assert by_number(plan)[101]["missing_sections"] == ["Root Causes"]
    assert by_number(plan)[102]["missing_sections"] == ["Root Causes"]


# --- Success criteria shape -------------------------------------------------

def test_criteria_items_without_code_or_test_are_hints(plan):
    result = item(by_number(plan)[116], "criteria-checkable")
    assert result["status"] == "needs_judgement"
    assert result["hints"] == ["It works well.", "The knob turns, smoothly."]


def test_criteria_with_no_list_item_fails(plan):
    result = item(by_number(plan)[117], "criteria-checkable")
    assert result["status"] == "fail"
    assert result["detail"] == "Success Criteria holds no list item"


# --- Size -------------------------------------------------------------------

def test_size_over_the_limit_without_sub_issues_fails(plan):
    assert item(by_number(plan)[105], "sized")["status"] == "fail"


def test_decomposed_parent_is_sized_without_a_size_line(plan):
    issue = by_number(plan)[106]
    assert (issue["size_points"], issue["unsized"]) == (None, False)
    assert item(issue, "sized") == {"id": "sized", "check": "script", "status": "pass",
                                     "detail": "decomposed into 2 sub-issue(s)", "hints": []}


def test_size_within_the_limit_passes(plan):
    issue = by_number(plan)[101]
    assert item(issue, "sized")["status"] == "pass"
    assert (issue["size_points"], issue["size_source"], issue["unsized"]) == (2, "body", False)


def test_size_marker_comment_sizes_the_issue(plan):
    issue = by_number(plan)[108]
    assert (issue["size_points"], issue["size_source"]) == (3, "marker")
    assert item(issue, "sized")["detail"] == "3 point(s), from the marker"


def test_newest_size_marker_overrides_the_body_line(plan):
    # Body says 5, a marker said 8, a newer marker says 2; a size line in an
    # ordinary later comment is not a producer.
    issue = by_number(plan)[118]
    assert (issue["size_points"], issue["size_source"]) == (2, "marker")
    assert item(issue, "sized")["status"] == "pass"


def test_marker_must_be_the_first_line(definition):
    issue = refine_platform.github_issue({"number": 9, "createdAt": "2026-11-01T00:00:00Z", "comments": [
        {"body": "Context first.\n<!-- refine:size 2 -->", "createdAt": "2026-11-02T00:00:00Z"}]})
    assert refine_checks.size_reading(issue, definition) == (None, None)


def test_unsized_issue_needs_sizing_and_is_not_otherwise_held(plan):
    issue = by_number(plan)[119]
    assert issue["unsized"] is True
    sized = item(issue, "sized")
    assert sized["status"] == "needs_judgement"
    assert sized["detail"].startswith("unsized: needs sizing")
    assert issue["verdict"] == "needs_judgement"
    assert plan["summary"]["unsized"] == 1


# --- Blockers ---------------------------------------------------------------

def test_prose_blocker_without_an_edge_fails(plan):
    result = item(by_number(plan)[107], "blockers-recorded")
    assert result["status"] == "fail"
    assert "#101" in result["detail"]


def blockers_of(definition, body, open_numbers):
    issue = refine_platform.github_issue({"number": 50, "body": body, "createdAt": NOW})
    items = refine_checks.checklist(issue, definition, FIXTURE_REPO, open_numbers)
    return next(r for r in items if r["id"] == "blockers-recorded")


def test_negated_blocker_phrase_names_no_blocker(definition):
    result = blockers_of(definition, "Not blocked by anything; #12 was the old plan. No longer depends on #13.", {12, 13})
    assert result["status"] == "needs_judgement"
    assert result["hints"] == ["confirm no dependency is missing an edge"]


def test_closed_prose_blocker_is_a_hint_not_a_failure(definition):
    result = blockers_of(definition, "Blocked by #12 and #13.", {13})
    assert result["status"] == "fail"
    assert result["detail"] == "prose names open #13 as a blocker with no blocked-by edge"
    result = blockers_of(definition, "Depends on #12.", {13})
    assert result["status"] == "needs_judgement"
    assert result["hints"][0] == "prose names #12 as a blocker; it is not open, so no edge is needed"


def test_recorded_edges_still_need_judgement(plan):
    result = item(by_number(plan)[105], "blockers-recorded")
    assert result["status"] == "needs_judgement"
    assert result["detail"] == "1 blocked-by edge(s)"


# --- Duplicates -------------------------------------------------------------

def pair(plan, kept, dup):
    return next(p for p in plan["duplicates"] if p["kept"] == kept and p["duplicate"] == dup)


def test_exact_duplicate_pair_names_the_survivor(plan):
    found = pair(plan, 101, 103)
    assert (found["kind"], found["basis"], found["closable"]) == ("exact", "title_and_body", True)
    assert "exact duplicate of #101" in by_number(plan)[103]["reasons"]
    assert by_number(plan)[103]["verdict"] == "not_ready"


def test_open_issue_repeating_a_completed_closed_issue_is_not_closable(plan):
    # It may be a regression of a fixed issue; a reader decides.
    found = pair(plan, 90, 112)
    assert (found["kind"], found["closable"]) == ("exact", False)


def test_issue_closed_as_not_planned_does_not_survive(plan):
    found = pair(plan, 113, 91)
    assert found["kind"] == "exact"
    assert found["closable"] is False


def test_near_pairs_are_reported_and_never_closable(plan):
    title = pair(plan, 104, 105)
    body = pair(plan, 114, 115)
    assert (title["kind"], title["basis"], title["closable"]) == ("near", "title", False)
    assert (body["kind"], body["basis"], body["closable"]) == ("near", "body", False)
    assert body["similarity"] >= 0.85
    assert {p["kind"] for p in plan["duplicates"]} == {"exact", "near"}
    assert plan["summary"]["duplicate_pairs"] == {"exact": 3, "near": 2}


# --- Untriaged ---------------------------------------------------------------

def test_untriaged_counts_eight_days_not_six(plan):
    untriaged = {u["number"]: u for u in plan["untriaged"]}  # 118, 119: unlabelled, 1-2 days
    assert untriaged == {103: {"number": 103, "days": 8, "reason": "no_labels"}}
    assert by_number(plan)[117]["untriaged_days"] is None  # unlabelled, 6 days
    assert by_number(plan)[104]["untriaged_days"] is None  # needs-triage, 6 days


def test_triage_label_past_seven_days_is_untriaged(definition):
    issue = refine_platform.github_issue({"number": 1, "labels": [{"name": "needs-triage"}],
                                          "createdAt": "2026-11-01T00:00:00Z"})
    assert refine_checks.untriaged_days(issue, definition, NOW_DT) == 24


# --- Overlap with open PRs (/issues Overlap Sweep) ---------------------------

def test_open_pr_closing_the_issue_holds_it(plan):
    issue = by_number(plan)[108]
    assert issue["overlaps"] == [{"pr": 202, "kind": "closes", "files": []}]
    assert issue["verdict"] == "not_ready"


def test_dot_path_overlap_keeps_its_leading_dot():
    issue = refine_platform.github_issue({"number": 60, "createdAt": NOW,
                                          "body": "Edit `.github/workflows/tests.yml` and `./Makefile`."})
    prs = [refine_platform.github_pull_request({"number": 7, "files": [
        {"path": ".github/workflows/tests.yml"}, {"path": "Makefile"}, {"path": "github/workflows/tests.yml"}]})]
    assert refine_checks.overlaps(issue, prs) == [
        {"pr": 7, "kind": "files", "files": [".github/workflows/tests.yml", "Makefile"]}]


def test_named_file_and_directory_overlap(plan):
    assert by_number(plan)[109]["overlaps"] == [
        {"pr": 201, "kind": "files", "files": ["src/widgets/parser.py"]},
        {"pr": 203, "kind": "files", "files": ["docs/guide.md"]},
    ]


# --- Verdicts and settled ----------------------------------------------------

def test_verdicts_and_settled(plan):
    issues = by_number(plan)
    assert issues[101]["verdict"] == "needs_judgement"
    assert issues[101]["settled"] is False
    assert issues[102]["settled"] is True
    counts = plan["summary"]["verdicts"]
    assert sum(counts.values()) == len(plan["issues"]) == plan["summary"]["open_issues"] == 19
    assert counts["ready"] == 0


def test_script_only_checklist_can_settle_ready(tmp_path):
    text = DEFINITION.read_text(encoding="utf-8")
    block = refine_definition.extract_block(text)
    trimmed = re.sub(r"checklist:\n(?:  .*\n)*", "checklist:\n  - id: sections-present\n    check: script\n"
                     "  - id: sized\n    check: script\n", block + "\n")
    definition = refine_definition.definition_from_block(trimmed)
    plan = refine.build_plan(recorded_backlog(), definition, "x", NOW_DT, False, 101, 2)
    assert [(p["number"], p["verdict"], p["settled"]) for p in plan["issues"]] == [(101, "ready", True)]


def test_unknown_checklist_item_needs_judgement(definition):
    extended = dict(definition, checklist=definition["checklist"] + [{"id": "new-rule", "check": "script"}])
    issue = refine_platform.github_issue({"number": 5, "body": "", "createdAt": NOW})
    result = refine_checks.checklist(issue, extended, FIXTURE_REPO)[-1]
    assert (result["status"], result["detail"]) == ("needs_judgement", "no script check for this item")


# --- Buffer and critical path ------------------------------------------------

def test_ready_buffer_against_twice_the_builder_slots(plan):
    buffer = plan["buffer"]
    # 105, 110, 111: open blocker. 106: decomposed parent. 108: an open PR closes it.
    # 103, 104, 117, 118, 119: no ready label. 114's only blocker is closed.
    assert buffer["issues"] == [101, 102, 107, 109, 112, 113, 114, 115, 116]
    assert (buffer["depth"], buffer["builder_slots"], buffer["target"], buffer["meets_target"]) == (9, 2, 4, True)


def test_critical_path_and_cycles(plan):
    assert plan["critical_path"] == {"length": 3, "points": 7, "unsized": 1,
                                     "issues": [101, 105, 106], "cycle_members": [110, 111]}


# --- Output, CLI and schema --------------------------------------------------

def test_output_validates_against_the_schema(tmp_path):
    code, data = run_main(tmp_path, "--dry-run")
    assert code == 0
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(data, schema)
    assert data["dry_run"] is True
    assert data["generated_at"] == NOW
    assert data["closed_window"] == {"days": 90, "count": 2, "truncated": False}
    assert data["open_truncated"] is False


def test_without_dry_run_the_flag_is_false(tmp_path):
    assert run_main(tmp_path)[1]["dry_run"] is False


def test_single_issue_mode_reports_one_issue_with_backlog_wide_blocks(tmp_path):
    code, data = run_main(tmp_path, "--issue", "103")
    assert code == 0
    assert [p["number"] for p in data["issues"]] == [103]
    assert data["issue"] == 103
    assert data["summary"]["open_issues"] == 19
    assert data["buffer"]["depth"] == 9


def test_issue_that_is_not_open_exits_2(tmp_path):
    assert run_main(tmp_path, "--issue", "90") == (2, None)


def test_unreadable_definition_exits_2(tmp_path):
    assert run_main(tmp_path, definition=tmp_path / "absent.md") == (2, None)


def test_plan_goes_to_stdout_without_out(capsys):
    code = refine.main(["--repo", FIXTURE_REPO, "--definition", str(DEFINITION), "--now", NOW],
                       backlog=recorded_backlog())
    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out)["repository"] == FIXTURE_REPO
    assert "buffer 9/10" in captured.err


def test_host_failure_exits_3(tmp_path):
    def failing(args):
        raise refine_platform.GhError("gh auth status: not logged in")

    backlog = refine_platform.GitHubBacklog(FIXTURE_REPO, runner=failing)
    code = refine.main(["--repo", FIXTURE_REPO, "--definition", str(DEFINITION), "--now", NOW,
                        "--out", str(tmp_path / "p.json")], backlog=backlog)
    assert code == 3


def test_builder_slots_must_be_positive():
    with pytest.raises(SystemExit):
        refine.parse_args(["--repo", "a/b", "--builder-slots", "0"])


def test_definition_defaults_to_this_checkout(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert refine.resolve_definition(None) == DEFINITION
    local = tmp_path / "docs" / "contributing" / "definition-of-ready.md"
    local.parent.mkdir(parents=True)
    shutil.copy(DEFINITION, local)
    assert refine.resolve_definition(None) == Path("docs") / "contributing" / "definition-of-ready.md"


def test_the_adapter_only_makes_read_calls():
    runner = RecordedRunner()
    backlog = refine_platform.GitHubBacklog(FIXTURE_REPO, runner=runner)
    refine.build_plan(backlog, refine_definition.load_definition(DEFINITION), "d", NOW_DT, True, None, 2)
    assert [c[:2] for c in runner.calls] == [["issue", "list"], ["issue", "list"], ["pr", "list"]]
    assert "closed:>=2026-08-27" in runner.calls[1]


def test_no_write_path_exists_in_the_scripts():
    # Success Criteria 4, as the issue's rg command states it.
    pattern = re.compile(r"(--method|-X)\s*(POST|PATCH|PUT|DELETE)|issue (edit|close|comment)")
    hits = [f"{p.name}:{n}" for p in sorted(SCRIPTS.iterdir()) if p.is_file()
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if pattern.search(line)]
    assert hits == []


def test_script_runs_by_path(tmp_path):
    # The scheduled workflow calls the file directly; a by-path run must resolve its siblings.
    import subprocess
    import sys
    proc = subprocess.run([sys.executable, str(ENTRY), "--help"], capture_output=True,
                          encoding="utf-8", errors="replace", check=False)
    assert proc.returncode == 0
    assert "--dry-run" in proc.stdout
