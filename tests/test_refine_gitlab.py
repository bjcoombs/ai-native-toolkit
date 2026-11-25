"""The GitLab read side of the refiner, against recorded `glab` output (issue #1327).

`GitLabBacklog` (`refine_platform_gitlab.py`) behind `refine.py --platform
gitlab`, and the model's and CI jobs' reader (`refine_gitlab_read.py`,
reached through `gh.sh`). No network: `FakeGlab` (`refine_gitlab_env.py`)
replays `tests/fixtures/refine_gitlab/`.
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import jsonschema
import pytest
from refine_gitlab_env import BACKLOG_ROUTES, FIXTURES, P, PROJECT, SCRIPTS, FakeGlab

import refine  # noqa: E402  (refine_gitlab_env put the scripts on sys.path)
import refine_checks  # noqa: E402
import refine_gitlab_read  # noqa: E402
import refine_platform  # noqa: E402
import refine_platform_gitlab as gl  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DEFINITION = REPO / "docs" / "contributing" / "definition-of-ready.md"
NOW = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)


def backlog(**extra: str) -> "tuple[gl.GitLabBacklog, FakeGlab]":
    fake = FakeGlab({**BACKLOG_ROUTES, "P/issues?state=closed": "issues_closed.json", **extra})
    return gl.GitLabBacklog(PROJECT, runner=fake, bot_login=""), fake


# ── read-only guard ──────────────────────────────────────────────────────

@pytest.mark.parametrize("args", [["api", f"{P}/issues"], ["api", "--paginate", f"{P}/issues?state=opened"]])
def test_glab_reads_pass(args):
    gl.check_glab_read_only(args)


@pytest.mark.parametrize("args", [
    ["api", "-X", "PUT", f"{P}/issues/1"], ["api", "-XPOST", f"{P}/issues/1/notes"],
    ["api", "--method=DELETE", f"{P}/issues/1"], ["api", f"{P}/issues/1/notes", "-f", "body=x"],
    ["api", f"{P}/issues/1", "-Fstate_event=close"], ["api", f"{P}/issues", "--input", "x.json"],
    ["api", "graphql"], ["issue", "close", "1"], ["issue", "list"], [],
])
def test_glab_writes_are_refused_before_a_process_starts(args, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("subprocess started"))
    with pytest.raises(refine_platform.ReadOnlyViolation):
        gl.run_glab_readonly(args)


def test_glab_failure_and_absence_are_host_errors(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="404 Not Found"))
    with pytest.raises(refine_platform.GhError, match="404"):
        gl.run_glab_readonly(["api", f"{P}/issues"])

    def missing(*a, **k):
        raise FileNotFoundError("glab")

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(refine_platform.GhError, match="cannot run glab"):
        gl.run_glab_readonly(["api", f"{P}/issues"])


def test_paginated_pages_are_joined_and_bad_json_is_a_host_error():
    assert gl.json_stream('[{"a": 1}]\n[{"a": 2}]', "x") == [{"a": 1}, {"a": 2}]
    with pytest.raises(refine_platform.GhError, match="malformed"):
        gl.json_stream("[{", "x")


# ── the Backlog protocol ─────────────────────────────────────────────────

def test_open_issues_map_to_the_shared_shape():
    b, fake = backlog()
    one, two = b.open_issues()
    assert one["number"] == 1 and one["state"] == "open" and one["state_reason"] is None
    assert one["created_at"] == "2026-11-20T09:00:00Z"
    assert one["url"] == "https://gitlab.example.com/acme/widgets/-/issues/1"
    assert one["sub_issues_total"] == 0
    assert [(c["body"].splitlines()[0], c["trusted"]) for c in one["comments"]] == [
        ("<!-- refine:size 2 -->", True), ("<!-- refine:size 1 -->", False)], "system notes are dropped; guests untrusted"
    assert two["labels"] == ["enhancement", "refine::ready"], "scoped labels are plain names"
    assert two["created_at"] == "2026-11-21T09:30:00Z", "an offset is normalised to UTC"
    assert two["blocked_by"] == [{"number": 1, "state": "open"}], "only is_blocked_by links in this project"
    assert sum(p == f"{P}/members/all?per_page=100" for p in fake.paths()) == 1, "members are read once"
    assert not b.open_truncated


def test_closed_issues_are_filtered_to_the_window():
    b, fake = backlog()
    assert [i["number"] for i in b.closed_issues_since(date(2026, 8, 27))] == [5]
    assert any("updated_after=2026-08-27" in p for p in fake.paths())


def test_merge_requests_read_closes_and_files():
    b, _ = backlog()
    (mr,) = b.open_pull_requests()
    assert mr == {"number": 7, "title": "Fix save", "files": ["docs/new.md", "docs/old.md", "src/save.py"],
                  "closes": [1]}


def test_refiners_bot_marker_is_trusted_without_membership():
    b = gl.GitLabBacklog(PROJECT, runner=FakeGlab(BACKLOG_ROUTES), bot_login="refiner")
    assert b.trusted("refiner") and b.trusted("reporter") and not b.trusted("guest") and not b.trusted("")


def test_a_gitlab_issue_url_names_a_blocker():
    assert refine_checks._issue_refs("see https://gitlab.example.com/acme/widgets/-/issues/12 and #3", PROJECT) == {3, 12}
    assert refine_checks._issue_refs("https://gitlab.example.com/other/repo/-/issues/12", PROJECT) == set()


def test_refine_py_plans_a_gitlab_backlog(tmp_path):
    b, _ = backlog()
    out = tmp_path / "verdict.json"
    code = refine.main(["--repo", PROJECT, "--platform", "gitlab", "--now", "2026-11-25T12:00:00Z",
                        "--definition", str(DEFINITION), "--out", str(out)], backlog=b)
    assert code == 0
    plan = json.loads(out.read_text(encoding="utf-8"))
    schema = json.loads((SCRIPTS / "refine-output.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(plan, schema)
    assert plan["platform"] == "gitlab" and plan["repository"] == PROJECT
    by = {i["number"]: i for i in plan["issues"]}
    assert by[1]["verdict"] == "not_ready" and by[1]["overlaps"] == [{"pr": 7, "kind": "closes", "files": []}]
    assert by[1]["size_points"] == 2 and by[1]["size_source"] == "marker"
    blockers = next(c for c in by[2]["checklist"] if c["id"] == "blockers-recorded")
    assert blockers["status"] != "fail", "the URL names #1 and the link records the edge"


def test_refine_py_fails_a_prose_blocker_with_no_link(tmp_path):
    b, _ = backlog(**{"P/issues/2/links": "links_none.json"})
    out = tmp_path / "verdict.json"
    assert refine.main(["--repo", PROJECT, "--platform", "gitlab", "--now", "2026-11-25T12:00:00Z",
                        "--definition", str(DEFINITION), "--out", str(out)], backlog=b) == 0
    two = next(i for i in json.loads(out.read_text(encoding="utf-8"))["issues"] if i["number"] == 2)
    assert next(c for c in two["checklist"] if c["id"] == "blockers-recorded")["status"] == "fail"


def test_platform_comes_from_the_flag_then_the_environment(monkeypatch):
    monkeypatch.setenv("REFINE_PLATFORM", "gitlab")
    assert refine.parse_args(["--repo", PROJECT]).platform == "gitlab"
    assert refine.parse_args(["--repo", PROJECT, "--platform", "github"]).platform == "github"
    assert isinstance(refine._backlog("gitlab", PROJECT), gl.GitLabBacklog)
    monkeypatch.setenv("REFINE_PLATFORM", "bitbucket")
    with pytest.raises(SystemExit):
        refine.parse_args(["--repo", PROJECT])


def test_a_refused_or_failed_glab_read_exits_3(tmp_path):
    def broken(args):
        raise refine_platform.GhError("glab api exited 1: 401")

    b = gl.GitLabBacklog(PROJECT, runner=broken, bot_login="")
    assert refine.main(["--repo", PROJECT, "--platform", "gitlab", "--definition", str(DEFINITION),
                        "--out", str(tmp_path / "v.json")], backlog=b) == 3


# ── the reader behind gh.sh, and the CI helpers ──────────────────────────

ENV = {"GH_REPO": PROJECT, "CI_PIPELINE_ID": "300", "CI_DEFAULT_BRANCH": "main"}


def read(argv, routes, capsys, env=None):
    fake = FakeGlab(routes)
    code = refine_gitlab_read.main(argv, runner=fake, env=env or ENV, now=NOW)
    return code, capsys.readouterr().out, fake


def test_issue_view_prints_the_body_and_comments(capsys):
    code, out, _ = read(["issue", "view", "1", "--comments", "--json", "title,body"],
                        {"P/issues/1": "w_issue_1.json", "P/issues/1/notes": "notes_1.json"}, capsys)
    assert code == 0
    assert "title:\tCrash on save" in out and "## Success Criteria" in out
    assert "-- comment by maintainer" in out and "added ~needs-triage label" not in out


@pytest.mark.parametrize("argv", [["issue", "view", "1", "--repo", "evil/repo"],
                                  ["issue", "view", "https://gitlab.example.com/evil/repo/-/issues/1"],
                                  ["issue", "close", "1"], ["new-issues", "--repo", "evil/repo"]])
def test_reader_refuses_other_projects_and_writes(argv, capsys):
    code, _, fake = read(argv, {}, capsys)
    assert code == 4 and fake.reads == []


def test_search_and_labels(capsys):
    code, out, fake = read(["search", "issues", "crash", "save", "--state", "all", "--limit", "5"],
                           {"P/issues": "issues_open.json"}, capsys)
    assert code == 0 and out.splitlines()[0].startswith("1\topen\tCrash on save")
    assert "search=crash%20save" in fake.paths()[0] and "state=all" in fake.paths()[0]
    code, out, _ = read(["label", "list"], {"P/labels": "labels.json"}, capsys)
    assert code == 0 and out.splitlines()[0] == "refine::ready\tready"


def test_new_issues_from_a_webhook_payload(capsys):
    code, out, fake = read(["new-issues", "--payload", str(FIXTURES / "payload_open.json")], {}, capsys)
    assert (code, out.strip(), fake.reads) == (0, "2", [])
    code, out, _ = read(["new-issues", "--payload", str(FIXTURES / "payload_update.json")], {}, capsys)
    assert (code, out.strip()) == (0, "")


def test_new_issues_since_the_last_successful_apply_on_the_default_branch(capsys):
    routes = {"P/jobs": "jobs_success.json", "P/issues": "issues_new.json"}
    code, out, fake = read(["new-issues", "--max", "1"], routes, capsys)
    assert code == 0 and out.split() == ["1"], "oldest first, at most --max"
    # Not this pipeline (300), not a webhook pipeline (299), not another branch (298), not another job (297).
    assert "created_after=2026-11-24T18:17:00Z" in fake.paths()[1]
    assert "scope[]=success" in fake.paths()[0]


def test_new_issues_fall_back_to_a_window_with_no_earlier_run(capsys):
    routes = {"P/jobs": "[]", "P/issues": "issues_new.json"}
    code, out, fake = read(["new-issues", "--fallback-hours", "6"], routes, capsys)
    assert code == 0 and out.split() == ["1", "2"]
    assert "created_after=2026-11-25T06:00:00Z" in fake.paths()[1]


@pytest.mark.parametrize("fixture, expected", [("w_issue_1.json", "false"), ("w_issue_3.json", "false")])
def test_trusted_author_needs_reporter(fixture, expected, capsys, tmp_path):
    code, out, _ = read(["trusted-author", "--issue", "1"],
                        {"P/issues/1": fixture, "P/members/all": "members.json"}, capsys)
    assert (code, out.strip()) == (0, expected)
    issue = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
    issue["author"]["username"] = "reporter"
    code, out, _ = read(["trusted-author", "--issue", "1"],
                        {"P/issues/1": json.dumps(issue), "P/members/all": "members.json"}, capsys)
    assert out.strip() == "true"


needs_bash = pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None,
                                reason="gh.sh runs under bash on Linux runners")


@needs_bash
def test_gh_sh_on_gitlab_reads_through_the_python_reader(tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "glab").write_text(f'#!/bin/sh\necho "$@" >> "{tmp_path}/calls"\ncat "{FIXTURES / "w_issue_1.json"}"\n',
                               encoding="utf-8")
    (stub / "glab").chmod(0o755)
    (stub / "gh").write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    (stub / "gh").chmod(0o755)
    env = {"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}", "REFINE_PLATFORM": "gitlab", "GH_REPO": PROJECT}
    gh_sh = str(SCRIPTS / "gh.sh")
    ok = subprocess.run(["bash", gh_sh, "issue", "view", "1"], env=env, capture_output=True, text=True,
                        encoding="utf-8", errors="replace", check=False)
    assert ok.returncode == 0, ok.stderr
    assert "title:\tCrash on save" in ok.stdout
    assert (tmp_path / "calls").read_text(encoding="utf-8").split() == ["api", f"{P}/issues/1"]
    refused = subprocess.run(["bash", gh_sh, "issue", "close", "1"], env=env, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", check=False)
    assert refused.returncode == 4
