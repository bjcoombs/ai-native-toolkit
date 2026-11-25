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

from lib.counts_only.content_free import counts_only
from lib.level_tally import default_model
from lib.since_last_run import FIRST_RUN, baseline_record, gate_changes, since_last_run
from test_headline import _commit, _run, ci2  # noqa: F401  (ci2 is a fixture)

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
STEPS = default_model()["steps"]
STEPS_VERSION = default_model()["version"]


def _cond(cid: str, state: str) -> dict[str, Any]:
    row, lv = cid.rsplit(".", 1)
    return {"id": cid, "row": row, "level": int(lv), "grade": "measured", "counted": True, "state": state}


def _level(instr: str, ci: str, cov: str = "unmet") -> dict[str, Any]:
    conditions = [_cond("layer-0-instructions.1", instr), _cond("layer-5-ci.1", ci),
                  _cond("layer-6-coverage.1", cov)]
    blockers = [{"kind": "gate", "gate": g, "condition": c["id"], "state": c["state"]}
                for g, c in zip(("instructions", "ci_runs_tests", "coverage_gate"), conditions)
                if c["state"] != "met"]
    # a level the run can record as its baseline: the default model's version and tags (#1256)
    return {"levels_version": STEPS_VERSION, "tags": [], "conditions": conditions,
            "next_step": {"step": 2, "blockers": blockers} if blockers else None}


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


CUSTOM = "custom-2026.11.24"  # a custom --levels model: no committed record, so it reads run-context.json


def _custom(level: dict[str, Any]) -> dict[str, Any]:
    return {**level, "levels_version": CUSTOM}


def _previous(repo: Path, level: Any, sha: str | None) -> None:
    """The previous run as the default model reads it, the committed baseline in prior-level.json
    (#1256), and as a custom model reads it, the local run-context.json."""
    commit = {"available": True, "head_sha": sha} if sha else {"available": False, "reason": "x"}
    ctx = {"run_id": "prev-run", "measured_commit": commit, "level": level}
    (repo / ".assess" / "run-context.json").write_text(json.dumps(ctx), encoding="utf-8")
    record = baseline_record(ctx)
    if record is not None:
        (repo / ".assess" / "prior-level.json").write_text(json.dumps(
            {"prior_level_version": 2, "levels_version": "x", "tags": [], "baseline": record}), encoding="utf-8")


def _actions(repo: Path, *entries: dict[str, Any]) -> None:
    (repo / ".assess" / "actions.json").write_text(json.dumps({"schema": 2, "actions": list(entries)}),
                                                   encoding="utf-8")


def test_a_first_run_says_so_and_keeps_the_next_blocker_as_data(repo: Path) -> None:
    block = since_last_run(_level("met", "unmet"), repo / ".assess", repo)
    assert block["first_run"] is True and block["previous_commit"] is None
    assert (block["closed"], block["closed_count"], block["gates_gained"], block["gates_lost"]) == (
        None, None, None, None)
    assert block["lines"] == [FIRST_RUN] and block["next_blocker"] == "ci_runs_tests"
    assert block["markdown"] == f"- {FIRST_RUN}"


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
    assert block["lines"][-1] == "Gates lost: none" and block["next_blocker"] == "coverage_gate"


def test_actions_json_is_read_at_head_not_from_the_working_tree(repo: Path) -> None:
    _previous(repo, _level("met", "met"), _git(repo, "rev-parse", "HEAD"))
    (repo / "b.txt").write_text("2\n", encoding="utf-8")
    _commit(repo, "2026-11-02T10:00:00Z", "fix")
    _actions(repo, {"action": "x", "status": "done", "completed_sha": _git(repo, "rev-parse", "HEAD")})
    block = since_last_run(_level("met", "met"), repo / ".assess", repo)
    # never committed: not known, never a zero that hides a working-tree done entry
    assert (block["closed"], block["closed_reason"]) == (None, "actions_not_committed")
    assert any("commit it for closed actions to count" in line for line in block["lines"])


def test_unknown_previous_state_reads_null_not_zero(repo: Path) -> None:
    """A custom --levels run reads the local run-context.json, so its gaps read not known."""
    _previous(repo, None, None)
    block = since_last_run(_custom(_level("met", "met", "met")), repo / ".assess", repo)
    assert block["first_run"] is False
    assert (block["closed"], block["closed_count"], block["gates_gained"]) == (None, None, None)
    assert block["closed_reason"] == "no_previous_commit"
    assert "Closed since last run: not known (the previous run recorded no commit)" in block["lines"]
    assert "Gates gained: not known (the previous run has no level result this model can compare)" in block["lines"]
    assert not any(line.startswith("Next blocker") for line in block["lines"])
    (repo / ".assess" / "run-context.json").write_text("{not json", encoding="utf-8")
    block = since_last_run(_custom(_level("met", "met")), repo / ".assess", repo)
    assert block["first_run"] is False and block["closed_reason"] == "previous_unreadable"
    assert ("Gates gained: not known (the previous run's local record could not be read; "
            "re-run /assess to rebuild it)") in block["lines"]


def test_an_unreadable_committed_baseline_reads_not_known_never_a_first_run(repo: Path) -> None:
    (repo / ".assess" / "prior-level.json").write_text("{not json", encoding="utf-8")
    block = since_last_run(_level("met", "met"), repo / ".assess", repo)
    assert (block["first_run"], block["closed"], block["closed_reason"]) == (False, None, "baseline_unreadable")
    assert "Gates lost: not known (the committed .assess/prior-level.json could not be read)" in block["lines"]


def test_a_lost_gate_is_listed() -> None:
    assert gate_changes(STEPS, _level("unmet", "met"), _level("met", "met")) == ([], ["instructions"])


def test_counts_only_keeps_counts_and_tokens_and_drops_the_words(repo: Path) -> None:
    block = since_last_run(_level("met", "unmet"), repo / ".assess", repo)
    kept = counts_only({"since_last_run": block})["since_last_run"]
    assert kept["lines"] == 1 and "markdown" not in kept  # dropped text: a list reads as its length
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
    card = report.split("\n## ", 1)[0]
    assert f"Since last run: 1 action closed (`{sha[:7]}`)." in card
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
    block = since_last_run(_custom(_level("met", "met")), repo / ".assess", repo)
    assert block["available"] is True and block["closed_count"] == 1
    assert (block["gates_gained"], block["gates_lost"]) == (None, None)


def test_unreadable_actions_json_reads_not_known_and_action_text_stays_on_one_line(repo: Path) -> None:
    """Review of #1097: a bad file is unknown, never 0; a newline cannot reach log.md structure."""
    before = _git(repo, "rev-parse", "HEAD")
    _previous(repo, _level("met", "met"), before)
    (repo / ".assess" / "actions.json").write_text("{not json", encoding="utf-8")
    _commit(repo, "2026-11-02T10:00:00Z", "bad")
    block = since_last_run(_level("met", "met"), repo / ".assess", repo)
    assert (block["closed"], block["closed_reason"]) == (None, "actions_unreadable")
    (repo / "b.txt").write_text("2\n", encoding="utf-8")
    _commit(repo, "2026-11-03T10:00:00Z", "fix")
    sha = _git(repo, "rev-parse", "HEAD")
    _actions(repo, {"action": "Fix\n## 2026-01-01\n<!-- chain:x -->", "status": "done", "completed_sha": sha})
    _commit(repo, "2026-11-04T10:00:00Z", "record")
    block = since_last_run(_level("met", "met"), repo / ".assess", repo)
    assert block["closed"] is not None and block["closed"][0]["action"] == "Fix ## 2026-01-01 &lt;!-- chain:x -->"
    assert "<!--" not in block["markdown"]
    assert all("\n" not in line for line in block["lines"])
    assert not any(line.startswith(("## ", "<!--")) for line in block["markdown"].splitlines())


def test_a_scoped_run_reads_the_whole_repo_actions_json(repo: Path) -> None:
    """finalize writes only .assess/actions.json, so a --scope run reads that file."""
    scoped = repo / ".assess" / "svc"
    scoped.mkdir()
    before = _git(repo, "rev-parse", "HEAD")
    (scoped / "prior-level.json").write_text(json.dumps(
        {"prior_level_version": 2, "levels_version": "x", "tags": [],
         "baseline": {"run_id": "p", "head_sha": before, "held_gates": []}}), encoding="utf-8")
    (repo / "b.txt").write_text("2\n", encoding="utf-8")
    _commit(repo, "2026-11-02T10:00:00Z", "fix")
    _actions(repo, {"action": "Scoped work", "status": "done", "completed_sha": _git(repo, "rev-parse", "HEAD")})
    _commit(repo, "2026-11-03T10:00:00Z", "record")
    assert since_last_run(_level("met", "met"), scoped, repo)["closed_count"] == 1


def test_a_superseding_run_keeps_the_superseded_runs_baseline(repo: Path) -> None:
    """A same-day re-run on the same commit compares against the run before the one it replaces."""
    before = _git(repo, "rev-parse", "HEAD")
    (repo / "b.txt").write_text("2\n", encoding="utf-8")
    _commit(repo, "2026-11-02T10:00:00Z", "fix")
    _actions(repo, {"action": "Work", "status": "done", "completed_sha": _git(repo, "rev-parse", "HEAD")})
    _commit(repo, "2026-11-03T10:00:00Z", "record")
    _previous(repo, _custom(_level("met", "unmet")), before)
    current = _custom(_level("met", "met"))  # a custom model: the read order still reaches run-context.json
    replaced = since_last_run(current, repo / ".assess", repo)
    head = _git(repo, "rev-parse", "HEAD")
    (repo / ".assess" / "run-context.json").write_text(json.dumps(
        {"run_id": "b", "measured_commit": {"available": True, "head_sha": head}, "since_last_run": replaced}),
        encoding="utf-8")
    superseded = json.loads((repo / ".assess" / "run-context.json").read_text(encoding="utf-8"))
    again = since_last_run(current, repo / ".assess", repo, superseded)
    assert (again["closed_count"], again["previous_commit"], again["previous_run_id"]) == (1, before, "prev-run")
    assert (again["gates_gained"], again["lines"]) == (["ci_runs_tests"], replaced["lines"])
    # without the supersede input the same files would compare against run b: nothing closed
    assert since_last_run(current, repo / ".assess", repo)["closed_count"] == 0


def _log(repo: Path) -> None:
    (repo / ".assess" / "log.md").write_text("# Assessment log\n\n## 2026-11-01\n\n- **Files scored:** 3\n",
                                             encoding="utf-8")


def test_a_log_without_a_committed_baseline_makes_this_run_the_baseline(repo: Path) -> None:
    """#1256: log.md and no prior-level.json baseline (a fresh clone, CI, or the run that first
    commits one) is a baseline run, read once, never four "not known" lines."""
    _log(repo)
    for prior in (None, {"prior_level_version": 2, "levels_version": "x", "tags": []}):  # no file; no baseline
        if prior is not None:
            (repo / ".assess" / "prior-level.json").write_text(json.dumps(prior), encoding="utf-8")
        block = since_last_run(_level("met", "unmet"), repo / ".assess", repo)
        assert (block["first_run"], block["closed_reason"]) == (True, None)
        assert block["lines"] == [FIRST_RUN]
        assert FIRST_RUN == "This run is your baseline; the next `/assess` run reports what moved."
        assert not any("not known" in line for line in block["lines"])


def test_a_run_that_cannot_record_a_baseline_never_claims_to_be_one(repo: Path, tmp_path: Path) -> None:
    """Review of #1256: with no level tags, no gate conditions or no HEAD commit, publish writes
    no baseline, so "This run is your baseline." would repeat on every run: unknown instead."""
    untagged = {k: v for k, v in _level("met", "met").items() if k != "tags"}
    no_conditions = {**_level("met", "met"), "conditions": []}
    empty = tmp_path / "empty"
    subprocess.run(["git", "init", "-q", str(empty)], check=True, capture_output=True)
    (empty / ".assess").mkdir()
    for level, root in ((untagged, repo), (no_conditions, repo), (_level("met", "met"), empty)):
        block = since_last_run(level, root / ".assess", root)
        assert (block["first_run"], block["closed_reason"]) == (False, "baseline_not_recorded")
        assert FIRST_RUN not in block["lines"]
        assert ("Gates gained: not known (no baseline is committed yet, and this run's result cannot be "
                "recorded as one)") in block["lines"]


def test_a_local_run_context_is_never_the_default_models_baseline(repo: Path) -> None:
    """#1256: the default model reads the committed prior-level.json only, so a run-context.json
    left on one machine cannot make its report differ from a fresh clone's."""
    _log(repo)
    (repo / ".assess" / "run-context.json").write_text(json.dumps(
        {"run_id": "local", "measured_commit": {"available": True, "head_sha": _git(repo, "rev-parse", "HEAD")},
         "level": _level("met", "met")}), encoding="utf-8")
    block = since_last_run(_level("met", "unmet"), repo / ".assess", repo)
    assert (block["first_run"], block["previous_run_id"], block["gates_lost"]) == (True, None, None)
    profile = {**_level("met", "unmet"), "levels_version": "profile-citizen-2026.11.23"}
    assert since_last_run(profile, repo / ".assess", repo)["first_run"] is True


def test_a_committed_baseline_without_a_run_context_compares_the_gates(repo: Path) -> None:
    """#1256 success criterion: prior-level.json alone (no run-context.json) gives the gate changes."""
    _log(repo)
    _previous(repo, _level("met", "met"), _git(repo, "rev-parse", "HEAD"))
    (repo / ".assess" / "run-context.json").unlink()
    block = since_last_run(_level("unmet", "met"), repo / ".assess", repo)
    assert (block["first_run"], block["previous_run_id"]) == (False, "prev-run")
    assert (block["gates_gained"], block["gates_lost"]) == ([], ["instructions"])
    assert "Gates lost: instruction file" in block["lines"]


def test_a_custom_models_log_with_earlier_runs_but_no_context_is_not_a_first_run(repo: Path) -> None:
    """A custom --levels run keeps no committed record, so it keeps the older read order (#1256)."""
    _log(repo)
    block = since_last_run(_custom(_level("met", "met")), repo / ".assess", repo)
    assert block["first_run"] is False and block["closed_reason"] == "previous_state_missing"
    assert (block["closed"], block["gates_gained"]) == (None, None)
    assert FIRST_RUN not in block["lines"]
    assert any("log.md records an earlier run" in line for line in block["lines"])
    assert not any("run-context.json" in line for line in block["lines"])


def test_an_unavailable_level_never_claims_every_gate_is_held(repo: Path) -> None:
    block = since_last_run({"available": False, "reason": "boom"}, repo / ".assess", repo)
    assert block["next_blocker"] is None
    assert not any("every gate is held" in line for line in block["lines"])


def test_a_malformed_actions_json_and_a_shallow_clone_read_not_known(repo: Path, tmp_path: Path) -> None:
    before = _git(repo, "rev-parse", "HEAD")
    _previous(repo, _level("met", "met"), before)
    (repo / ".assess" / "actions.json").write_text(json.dumps({"schema": 2, "actions": {"x": 1}}), encoding="utf-8")
    _commit(repo, "2026-11-02T10:00:00Z", "malformed")
    assert since_last_run(_level("met", "met"), repo / ".assess", repo)["closed_reason"] == "actions_unreadable"
    _actions(repo, {"action": "Done", "status": "done", "completed_sha": _git(repo, "rev-parse", "HEAD")})
    _commit(repo, "2026-11-03T10:00:00Z", "record")
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", repo.as_uri(), str(shallow)], check=True, capture_output=True)
    (shallow / ".assess" / "prior-level.json").write_text(json.dumps(
        {"prior_level_version": 2, "levels_version": "x", "tags": [],
         "baseline": {"run_id": "p", "head_sha": _git(shallow, "rev-parse", "HEAD"), "held_gates": []}}),
        encoding="utf-8")
    block = since_last_run(_level("met", "met"), shallow / ".assess", shallow)
    assert (block["closed"], block["closed_reason"]) == (None, "shallow_clone")


def test_the_committed_baseline_survives_a_deleted_run_context(ci2: Path) -> None:  # noqa: F811
    """#1122: a fresh clone or CI has prior-level.json (committed) and no run-context.json."""
    first = _run(ci2, "2026-11-21")
    prior = json.loads((ci2 / ".assess" / "prior-level.json").read_text(encoding="utf-8"))
    assert prior["prior_level_version"] == 3
    assert prior["baseline"]["head_sha"] == first["measured_commit"]["head_sha"]
    assert prior["baseline"]["run_id"] == first["run_id"]
    (ci2 / "b.txt").write_text("closed\n", encoding="utf-8")
    _commit(ci2, "2026-11-21T12:00:00Z", "the fix")
    sha = _git(ci2, "rev-parse", "HEAD")
    _actions(ci2, {"action": "Enforce a coverage gate", "status": "done", "completed_sha": sha})
    _commit(ci2, "2026-11-21T13:00:00Z", "mark the action done")
    (ci2 / ".assess" / "run-context.json").unlink()
    block = _run(ci2, "2026-11-22")["since_last_run"]
    assert (block["first_run"], block["closed_reason"], block["closed_count"]) == (False, None, 1)
    assert block["closed"][0]["completed_sha"] == sha and block["previous_run_id"] == first["run_id"]
    assert (block["gates_gained"], block["gates_lost"]) == ([], [])
    report = subprocess.run([sys.executable, str(SCRIPTS / "assess_report.py"), str(ci2), "--stdout"],
                            capture_output=True, encoding="utf-8", errors="replace", check=True).stdout
    assert f"Since last run: 1 action closed (`{sha[:7]}`)." in report


def test_a_committed_baseline_compares_the_held_gates(repo: Path) -> None:
    (repo / ".assess" / "prior-level.json").write_text(json.dumps({
        "prior_level_version": 2, "levels_version": "x", "tags": [],
        "baseline": {"run_id": "p", "head_sha": _git(repo, "rev-parse", "HEAD"),
                     "held_gates": ["instructions", "ci_runs_tests"]}}), encoding="utf-8")
    block = since_last_run(_level("met", "unmet"), repo / ".assess", repo)
    assert (block["previous_run_id"], block["gates_gained"], block["gates_lost"]) == ("p", [], ["ci_runs_tests"])


def test_a_baseline_from_another_branch_or_without_a_run_id_reads_safely(repo: Path) -> None:
    """Review of #1130: a baseline commit not in HEAD's history is unknown; a missing run_id is None."""
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "other")
    (repo / "o.txt").write_text("o\n", encoding="utf-8")
    _commit(repo, "2026-11-02T10:00:00Z", "other branch")
    other = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    for sha, reason in ((other, "previous_not_ancestor"), (base, "actions_not_committed")):
        (repo / ".assess" / "prior-level.json").write_text(json.dumps({
            "prior_level_version": 2, "levels_version": "x", "tags": [],
            "baseline": {"head_sha": sha, "held_gates": []}}), encoding="utf-8")
        block = since_last_run(_level("met", "met"), repo / ".assess", repo)
        assert (block["previous_run_id"], block["closed"], block["closed_reason"]) == (None, None, reason)


def test_the_kept_baseline_line_names_the_stored_run_only(repo: Path) -> None:
    """#1158 review: with no stored baseline, a degraded run names no kept run; with one, that run."""
    _previous(repo, _level("met", "met"), _git(repo, "rev-parse", "HEAD"))
    (repo / ".assess" / "prior-level.json").unlink()  # run-context only
    lines = since_last_run({"available": False, "reason": "x"}, repo / ".assess", repo)["lines"]
    assert not any(line.startswith("Baseline kept") for line in lines)
    (repo / ".assess" / "prior-level.json").write_text(json.dumps({
        "prior_level_version": 2, "levels_version": "x", "tags": [],
        "baseline": {"run_id": "stored-run", "head_sha": _git(repo, "rev-parse", "HEAD"), "held_gates": []}}),
        encoding="utf-8")
    lines = since_last_run({"available": False, "reason": "x"}, repo / ".assess", repo)["lines"]
    assert any(line.startswith("Baseline kept") and "`stored-run`" in line for line in lines)


def test_the_kept_line_follows_the_write_side_rule(repo: Path) -> None:
    """#1158 review: the line appears exactly when prior-level.json is not rebuilt."""
    (repo / ".assess" / "prior-level.json").write_text(json.dumps({
        "prior_level_version": 2, "levels_version": "x", "tags": [],
        "baseline": {"run_id": "stored-run", "head_sha": _git(repo, "rev-parse", "HEAD"), "held_gates": []}}),
        encoding="utf-8")
    recorded = _level("met", "unmet")  # the writer rebuilds the file
    untagged = {k: v for k, v in recorded.items() if k != "tags"}  # conditions but no tags: the writer keeps the file
    assert any(line.startswith("Baseline kept") for line in since_last_run(untagged, repo / ".assess", repo)["lines"])
    assert not any(line.startswith("Baseline kept")
                   for line in since_last_run(recorded, repo / ".assess", repo)["lines"])


def test_a_profile_run_keeps_the_pruning_ledger_as_stored(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#1184: the ledger is the default record's alone, so a profile run neither advances nor
    credits it; only a default run writes it."""
    import lib.since_last_run as sl

    entry = {"path": "CLAUDE.md", "completed_sha": "b" * 40, "cost_before": 3, "runs_held": 1,
             "credited": False, "saved": None, "gates": None, "credited_at": None}
    (repo / ".assess" / "prior-level.json").write_text(json.dumps({
        "prior_level_version": 3, "levels_version": "default-2026.11.10", "tags": [],
        "baseline": {"run_id": "p", "head_sha": _git(repo, "rev-parse", "HEAD"), "held_gates": []},
        "pruning": [entry]}), encoding="utf-8")
    called: list[bool] = []
    real = sl.update_ledger
    monkeypatch.setattr(sl, "update_ledger", lambda *a, **k: (called.append(True), real(*a, **k))[1])
    profile = {**_level("met", "met"), "levels_version": "profile-citizen-2026.11.23"}
    block = since_last_run(profile, repo / ".assess", repo)
    assert (block["pruned"], block["pruning"], called) == ([], [entry], [])
    since_last_run(_level("met", "met"), repo / ".assess", repo)
    assert called == [True]
