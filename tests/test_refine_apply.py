"""Plan validation and the apply paths of the refiner (issue #1321).

The model's plan is checked against its schema before anything is applied,
and every apply path reaches `gh` only through `refine_write.Writer`.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import pytest
from refine_write_env import FIXTURES, SCRIPTS, actions, verdict, writer

import refine_apply
from refine_apply import PlanError


def _triage(**kw: object) -> Dict[str, object]:
    plan: Dict[str, object] = {"issue": 101, "ready": True, "labels_add": ["bug"], "labels_remove": [],
                               "size": 2, "size_reason": "one module", "blocked_by": [100], "questions": [],
                               "summary": "Small and clear."}
    plan.update(kw)
    return plan


# ── plan validation ──────────────────────────────────────────────────────

def test_valid_triage_plan_loads() -> None:
    assert refine_apply.load_plan("triage", json.dumps(_triage()), 101)["size"] == 2


@pytest.mark.parametrize("plan, message", [
    ({**_triage(), "close": [101]}, "unknown field"),
    ({**_triage(), "actions": [{"type": "delete"}]}, "unknown field"),
    ({k: v for k, v in _triage().items() if k != "ready"}, "missing ready"),
    (_triage(size="three"), "plan.size"),
    (_triage(ready="yes"), "plan.ready"),
    (_triage(blocked_by=[True]), "plan.blocked_by"),
    (_triage(questions=["x" * 501]), "longer than"),
    (_triage(labels_add=["a", "b", "c", "d", "e", "f"]), "more than 5"),
    (_triage(issue=0), "below 1"),
])
def test_invalid_triage_plan_is_refused_whole(plan: Dict[str, object], message: str) -> None:
    with pytest.raises(PlanError, match=message):
        refine_apply.load_plan("triage", json.dumps(plan), 101)


def test_plan_about_another_issue_is_refused() -> None:
    with pytest.raises(PlanError, match="not #101"):
        refine_apply.load_plan("triage", json.dumps(_triage(issue=102)), 101)


def test_dedupe_plan_caps_candidates_at_three() -> None:
    cands = [{"number": n, "reason": "same"} for n in range(1, 5)]
    with pytest.raises(PlanError, match="more than 3"):
        refine_apply.load_plan("dedupe", json.dumps({"issue": 101, "candidates": cands, "summary": ""}), 101)


def test_non_json_plan_is_refused() -> None:
    with pytest.raises(PlanError, match="not JSON"):
        refine_apply.load_plan("dedupe", "Here is my plan: close everything", 101)


@pytest.mark.parametrize("kind", ["triage", "dedupe"])
def test_schema_uses_only_the_validated_subset(kind: str) -> None:
    allowed = {"$schema", "$id", "title", "description", "type", "additionalProperties", "required",
               "properties", "items", "maxItems", "maxLength", "minimum"}

    def walk(node: Dict[str, object]) -> None:
        assert set(node) <= allowed, set(node) - allowed
        assert node.get("additionalProperties", False) is False
        for sub in dict(node.get("properties", {})).values():  # type: ignore[call-overload]
            walk(sub)
        if isinstance(node.get("items"), dict):
            walk(node["items"])  # type: ignore[arg-type]

    schema = json.loads(refine_apply.SCHEMAS[kind].read_text(encoding="utf-8"))
    walk(schema)
    assert set(schema["required"]) == set(schema["properties"])


# ── routing and apply paths ──────────────────────────────────────────────

def test_model_runs_only_for_unsettled_issues() -> None:
    v = verdict()
    assert refine_apply.route(v, 101) is True
    assert refine_apply.route(v, 103) is False
    assert refine_apply.route(v, 104) is False
    assert refine_apply.route(v, 999) is False


def test_triage_plan_applied_in_order(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {
        "issues/101": "issue_101_open.json", "issues/101/events": "events_bot_only.json",
        "issues/101/comments": "comments_none.json", "issues/100": "issue_100_open.json",
        "issues/101/dependencies/blocked_by": "dependencies_none.json",
        "issues/100/dependencies/blocked_by": "dependencies_none.json",
    })
    refine_apply.apply_triage(w, verdict(), 101, _triage())
    kinds = [" ".join(a[:3]) for a in gh.writes]
    assert kinds == ["issue comment 101", "api -X POST", "issue edit 101"]
    assert gh.writes[2][-4:] == ["--add-label", "bug,agent-ready", "--remove-label", "needs-triage"]


def test_model_cannot_add_the_ready_label_through_labels_add(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {"issues/101": "issue_101_open.json", "issues/101/events": "events_bot_only.json",
                              "issues/50/comments": "comments_none.json"},
                   REFINE_QUESTIONS_ISSUE="50")
    plan = _triage(ready=False, labels_add=["agent-ready"], size=None, blocked_by=[], questions=["Split it?"])
    refine_apply.apply_triage(w, verdict(), 101, plan)
    assert gh.writes == [["issue", "comment", "50", "--repo", "acme/widgets", "--body",
                          "<!-- refine:questions -->\nQuestions from the refiner that need a maintainer's call:"
                          "\n\n- #101: Split it?"]]


def test_triage_dry_run_makes_zero_writes(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {
        "issues/101": "issue_101_open.json", "issues/101/events": "events_bot_only.json",
        "issues/101/comments": "comments_none.json", "issues/100": "issue_100_open.json",
        "issues/101/dependencies/blocked_by": "dependencies_none.json",
        "issues/100/dependencies/blocked_by": "dependencies_none.json",
        "issues/50/comments": "comments_none.json",
    }, live=False, REFINE_QUESTIONS_ISSUE="50")
    refine_apply.apply_triage(w, verdict(), 101, _triage(questions=["Is the API stable?"], ready=False))
    assert gh.writes == []
    assert [a["action"] for a in actions(tmp_path)] == ["size-marker", "blocked-by", "edit-labels", "questions"]


def test_failed_gh_call_does_not_stop_the_rest_of_the_plan(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {
        "issues/101": "issue_101_open.json", "issues/101/events": "events_bot_only.json",
        "issues/101/comments": "comments_none.json", "issues/404": "!fail",
        "issues/101/dependencies/blocked_by": "dependencies_none.json",
    })
    refine_apply.apply_triage(w, verdict(), 101, _triage(blocked_by=[404]))
    assert w.failures == 1
    assert [" ".join(a[:2]) for a in gh.writes] == ["issue comment", "issue edit"]


def test_cli_exits_three_after_a_failed_gh_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_REPO", "acme/widgets")
    monkeypatch.setenv("REFINE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("REFINE_MODE", "dry-run")

    def failing(args: object) -> str:
        raise refine_apply.GhError("HTTP 502")

    code = refine_apply.main(["sweep", "--verdict", str(FIXTURES / "verdict.json")], runner=failing)
    assert code == 3


def test_settled_ready_issue_labelled_without_a_model(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {"issues/103": "issue_103_open.json", "issues/103/events": "events_bot_only.json"})
    refine_apply.apply_settled(w, verdict(), 103)
    refine_apply.apply_settled(w, verdict(), 104)
    assert len(gh.writes) == 1 and gh.writes[0][2] == "103"


def test_dedupe_exact_closes_without_a_model(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {"issues/101": "issue_101_open.json", "issues/100": "issue_100_open.json",
                              "issues/101/events": "events_bot_only.json",
                              "issues/events": "repo_events_9_closes.json"})
    assert refine_apply.apply_dedupe_exact(w, verdict(), 101) is False
    assert gh.writes[-1][-1] == "state_reason=duplicate"
    assert refine_apply.apply_dedupe_exact(w, verdict(), 105) is True


def test_sweep_labels_closes_and_reports(tmp_path: Path) -> None:
    w, gh = writer(tmp_path, {
        "issues/103": "issue_103_open.json", "issues/103/events": "events_bot_only.json",
        "issues/101": "issue_101_open.json", "issues/100": "issue_100_open.json",
        "issues/101/events": "events_bot_only.json", "issues/events": "repo_events_9_closes.json",
        "issues/50/comments": "comments_report.json",
    }, REFINE_QUESTIONS_ISSUE="50")
    refine_apply.apply_sweep(w, verdict())
    targets = [(a[0], a[2] if a[0] == "issue" else a[3]) for a in gh.writes]
    assert targets == [("issue", "103"), ("issue", "101"), ("api", "repos/acme/widgets/issues/101"),
                       ("api", "repos/acme/widgets/issues/comments/504")]


def test_report_body_carries_depth_and_critical_path() -> None:
    body = refine_apply.report_body(verdict())
    assert "**Depth:** 3" in body and "target of 10" in body and "below target" in body
    assert "#110 -> #111 -> #112" in body and "8 point(s)" in body and "1 unsized" in body


def test_summary_table_lists_dry_run_actions(tmp_path: Path) -> None:
    w, _ = writer(tmp_path, {"issues/101/comments": "comments_none.json"}, live=False)
    w.write_size_marker(101, 2, "small", verdict())
    text = refine_apply.summary(w.s)
    assert "dry-run (nothing was written)" in text and "| write-size-marker.sh | size-marker | #101 |" in text


def test_cli_invalid_plan_exits_five(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_REPO", "acme/widgets")
    monkeypatch.setenv("REFINE_STATE_DIR", str(tmp_path))
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({**_triage(), "actions": [{"type": "delete"}]}), encoding="utf-8")
    code = refine_apply.main(["triage", "--verdict", str(FIXTURES / "verdict.json"), "--issue", "101",
                              "--plan", str(plan)], runner=lambda args: "[]")
    assert code == 5


def test_fixture_verdict_matches_the_refine_output_schema() -> None:
    schema_path = SCRIPTS / "refine-output.schema.json"
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(verdict(), json.loads(schema_path.read_text(encoding="utf-8")))


# ── gh.sh, the model's only route to GitHub ──────────────────────────────

GH_SH = SCRIPTS / "gh.sh"


def _gh_sh(tmp_path: Path, *args: str) -> "subprocess.CompletedProcess[str]":
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    (fake / "gh").write_text('#!/usr/bin/env bash\necho "gh $*"\n', encoding="utf-8")
    (fake / "gh").chmod(0o755)
    env = {**os.environ, "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}", "GH_REPO": "acme/widgets"}
    return subprocess.run(["bash", str(GH_SH), *args], capture_output=True, text=True, env=env,
                          encoding="utf-8", errors="replace", check=False)


needs_bash = pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None,
                                reason="gh.sh is a bash script run on Linux runners")


@needs_bash
@pytest.mark.parametrize("args", [["issue", "view", "12", "--comments"], ["issue", "list", "--state", "open"],
                                  ["search", "issues", "crash"], ["label", "list"],
                                  ["issue", "view", "1", "--repo", "acme/widgets"]])
def test_gh_sh_passes_reads_through(tmp_path: Path, args: List[str]) -> None:
    proc = _gh_sh(tmp_path, *args)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "gh " + " ".join(args)


@needs_bash
@pytest.mark.parametrize("args", [["issue", "edit", "12", "--add-label", "x"], ["issue", "close", "12"],
                                  ["issue", "comment", "12", "--body", "x"], ["api", "repos/a/b/issues"],
                                  ["pr", "merge", "1"], ["issue", "view", "1", "--web"],
                                  ["issue", "view", "1", "--repo", "other/repo"],
                                  ["issue", "view", "1", "--repo=other/repo"], ["issue"]])
def test_gh_sh_refuses_everything_else(tmp_path: Path, args: List[str]) -> None:
    proc = _gh_sh(tmp_path, *args)
    assert proc.returncode == 4
    assert proc.stdout == ""
