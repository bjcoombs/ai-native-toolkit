"""What changed since the previous run (lib/since_last_run.py, #1083).

Unit cases build the previous run-context by hand; the end-to-end case runs the core
twice on the ci2 shape with one action closed in a commit between the runs, and reads
the block, the deterministic report and the log.md entry.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lib.counts_only import counts_only
from lib.level_tally import default_model
from lib.since_last_run import FIRST_RUN, gate_changes, since_last_run
from test_headline import _commit, _run, ci2  # noqa: F401  (ci2 is a fixture)

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
STEPS = default_model()["steps"]


def _cond(cid: str, state: str) -> dict[str, Any]:
    row, lv = cid.rsplit(".", 1)
    return {"id": cid, "row": row, "level": int(lv), "grade": "measured", "counted": True, "state": state}


def _level(instr: str, ci: str, cov: str = "unmet") -> dict[str, Any]:
    conditions = [_cond("layer-0-instructions.1", instr), _cond("layer-5-ci.1", ci),
                  _cond("layer-6-coverage.1", cov)]
    blockers = [{"kind": "gate", "gate": g, "condition": c["id"], "state": c["state"]}
                for g, c in zip(("instructions", "ci_runs_tests", "coverage_gate"), conditions)
                if c["state"] != "met"]
    return {"conditions": conditions, "next_step": {"step": 2, "blockers": blockers} if blockers else None}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          encoding="utf-8").stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True, capture_output=True)
    (root / "a.txt").write_text("1\n", encoding="utf-8")
    _commit(root, "2026-11-01T10:00:00Z", "one")
    (root / ".assess").mkdir()
    return root


def _previous(repo: Path, level: Any, sha: str | None) -> None:
    commit = {"available": True, "head_sha": sha} if sha else {"available": False, "reason": "x"}
    (repo / ".assess" / "run-context.json").write_text(json.dumps(
        {"run_id": "prev-run", "measured_commit": commit, "level": level}), encoding="utf-8")


def _actions(repo: Path, *entries: dict[str, Any]) -> None:
    (repo / ".assess" / "actions.json").write_text(json.dumps({"schema": 2, "actions": list(entries)}),
                                                   encoding="utf-8")


def test_a_first_run_says_so_and_names_the_next_blocker(repo: Path) -> None:
    block = since_last_run(_level("met", "unmet"), repo / ".assess", repo)
    assert block["first_run"] is True and block["previous_commit"] is None
    assert (block["closed"], block["closed_count"], block["gates_gained"], block["gates_lost"]) == (
        None, None, None, None)
    assert block["lines"] == [FIRST_RUN, "Next blocker: CI runs tests"]
    assert block["markdown"].startswith(f"- {FIRST_RUN}\n")


def test_only_actions_closed_after_the_previous_commit_are_credited(repo: Path) -> None:
    before = _git(repo, "rev-parse", "HEAD")
    _previous(repo, _level("met", "unmet"), before)
    (repo / "b.txt").write_text("2\n", encoding="utf-8")
    _commit(repo, "2026-11-02T10:00:00Z", "fix")
    after = _git(repo, "rev-parse", "HEAD")
    _actions(repo,
             {"action": "Run the tests in CI", "status": "done", "completed_sha": after,
              "condition": "layer-5-ci.1"},
             {"action": "Earlier work", "status": "done", "completed_sha": before},
             {"action": "Still open", "status": "pending", "completed_sha": None},
             {"action": "Unknown commit", "status": "done", "completed_sha": "deadbeef" * 5})
    _commit(repo, "2026-11-03T10:00:00Z", "record the done action")
    block = since_last_run(_level("met", "met"), repo / ".assess", repo)
    assert block["first_run"] is False and block["previous_run_id"] == "prev-run"
    assert block["closed"] == [{"action": "Run the tests in CI", "condition": "layer-5-ci.1",
                                "finding": None, "completed_sha": after}]
    assert (block["gates_gained"], block["gates_lost"]) == (["ci_runs_tests"], [])
    assert block["lines"][:2] == ["Closed since last run: 1", f"  `{after[:7]}` Run the tests in CI"]
    assert block["unresolved"] == 1 and block["closed_reason"] is None
    assert "  1 more done action(s) name a commit not in this clone, so they were not checked" in block["lines"]
    assert f"  - `{after[:7]}` Run the tests in CI" in block["markdown"]
    assert "Gates gained: CI runs tests" in block["lines"] and "Gates lost: none" in block["lines"]
    assert block["lines"][-1] == "Next blocker: coverage gate"


def test_actions_json_is_read_at_head_not_from_the_working_tree(repo: Path) -> None:
    _previous(repo, _level("met", "met"), _git(repo, "rev-parse", "HEAD"))
    (repo / "b.txt").write_text("2\n", encoding="utf-8")
    _commit(repo, "2026-11-02T10:00:00Z", "fix")
    _actions(repo, {"action": "x", "status": "done", "completed_sha": _git(repo, "rev-parse", "HEAD")})
    assert since_last_run(_level("met", "met"), repo / ".assess", repo)["closed_count"] == 0


def test_unknown_previous_state_reads_null_not_zero(repo: Path) -> None:
    _previous(repo, None, None)
    block = since_last_run(_level("met", "met", "met"), repo / ".assess", repo)
    assert block["first_run"] is False
    assert (block["closed"], block["closed_count"], block["gates_gained"]) == (None, None, None)
    assert block["closed_reason"] == "no_previous_commit"
    assert "Closed since last run: not known (the previous run recorded no commit)" in block["lines"]
    assert "Gates gained: not known (the previous run has no level result)" in block["lines"]
    assert block["lines"][-1] == "Next blocker: none, every gate is held"
    (repo / ".assess" / "run-context.json").write_text("{not json", encoding="utf-8")
    assert since_last_run(_level("met", "met"), repo / ".assess", repo)["first_run"] is False


def test_a_lost_gate_is_listed() -> None:
    assert gate_changes(STEPS, _level("unmet", "met"), _level("met", "met")) == ([], ["instructions"])


def test_counts_only_keeps_counts_and_tokens_and_drops_the_words(repo: Path) -> None:
    block = since_last_run(_level("met", "unmet"), repo / ".assess", repo)
    kept = counts_only({"since_last_run": block})["since_last_run"]
    assert kept["lines"] == 2 and "markdown" not in kept  # dropped text: a list reads as its length
    assert kept["first_run"] is True and kept["next_blocker"] == "ci_runs_tests"


def test_an_action_closed_between_two_runs_opens_the_report_and_the_log(ci2: Path) -> None:  # noqa: F811
    """#1083 success criterion: "Closed since last run: 1", with the commit, in the report."""
    first = _run(ci2, "2026-11-21")
    assert first["since_last_run"]["first_run"] is True
    (ci2 / "b.txt").write_text("closed\n", encoding="utf-8")
    _commit(ci2, "2026-11-21T12:00:00Z", "the fix")
    sha = _git(ci2, "rev-parse", "HEAD")
    _actions(ci2, {"action": "Enforce a coverage gate", "status": "done", "completed_sha": sha})
    _commit(ci2, "2026-11-21T13:00:00Z", "mark the action done")
    second = _run(ci2, "2026-11-22")
    block = second["since_last_run"]
    assert block["closed_count"] == 1 and block["closed"][0]["completed_sha"] == sha
    assert block["previous_run_id"] == first["run_id"]
    report = subprocess.run([sys.executable, str(SCRIPTS / "assess_report.py"), str(ci2), "--stdout"],
                            capture_output=True, encoding="utf-8", errors="replace", check=True).stdout
    since = report.index("## Since Last Run")
    assert since < report.index("## Headline")
    assert "- Closed since last run: 1" in report and f"`{sha[:7]}` Enforce a coverage gate" in report
    log = (ci2 / ".assess" / "log.md").read_text(encoding="utf-8")
    latest = log[log.rindex("## "):]
    assert "- Closed since last run: 1" in latest
    assert latest.index("Closed since last run") < latest.index("**Files scored:**")


def test_a_branch_commit_squashed_away_is_never_credited(repo: Path) -> None:
    """Review of #1097: a completed_sha off HEAD's history is never an ancestor of a later
    measured commit, so without the HEAD check it would be credited on every run."""
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "topic")
    (repo / "b.txt").write_text("2\n", encoding="utf-8")
    _commit(repo, "2026-11-02T10:00:00Z", "branch fix")
    branch_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    (repo / "b.txt").write_text("2\n", encoding="utf-8")
    _commit(repo, "2026-11-02T11:00:00Z", "squashed fix")
    _actions(repo, {"action": "Squashed work", "status": "done", "completed_sha": branch_sha})
    _commit(repo, "2026-11-03T10:00:00Z", "record")
    for previous in (base, _git(repo, "rev-parse", "HEAD")):
        _previous(repo, _level("met", "met"), previous)
        block = since_last_run(_level("met", "met"), repo / ".assess", repo)
        assert (block["closed"], block["unresolved"]) == ([], 0)


def test_a_previous_commit_not_in_this_clone_reads_not_known(repo: Path) -> None:
    _previous(repo, _level("met", "met"), "0123456789abcdef0123456789abcdef01234567")
    block = since_last_run(_level("met", "met"), repo / ".assess", repo)
    assert (block["closed"], block["closed_count"], block["closed_reason"]) == (
        None, None, "previous_commit_absent")
    assert "Closed since last run: not known (the previous run's commit is not in this clone)" in block["lines"]


def test_an_older_models_level_leaves_the_gates_unknown_and_keeps_the_credit(repo: Path) -> None:
    before = _git(repo, "rev-parse", "HEAD")
    _previous(repo, {"conditions": []}, before)
    (repo / "b.txt").write_text("2\n", encoding="utf-8")
    _commit(repo, "2026-11-02T10:00:00Z", "fix")
    _actions(repo, {"action": "Done", "status": "done", "completed_sha": _git(repo, "rev-parse", "HEAD")})
    _commit(repo, "2026-11-03T10:00:00Z", "record")
    block = since_last_run(_level("met", "met"), repo / ".assess", repo)
    assert block["available"] is True and block["closed_count"] == 1
    assert (block["gates_gained"], block["gates_lost"]) == (None, None)
