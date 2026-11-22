"""A done review in the committed actions.json clears self_referential_tests (#721).

The finding keys on commit origin, so no code change clears it. A ``done``
entry with a ``completed_sha`` takes the path out of the attention list and the
Top 3 while the source is unchanged since that commit; the path stays in
``derived_findings`` with a reviewed note, and a later change re-flags it.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from assess_core import build_and_publish_run
from assess_finalize import _write_actions_contract
from assess_gate import check_finding_regressions
from lib import reviewed_findings as rf
from lib.badge import concern_count_from_findings
from lib.counts_only import counts_only
from lib.keyhole_signals import finding_display_name, finding_products
from lib.mutation_refresh import _rebuilt_findings
from lib.run_context_types import DerivedFinding

ACTION = "request human review - tests verify internal consistency, not truth"


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout.strip()


def _stats() -> dict[str, Any]:
    row = {"path": "src/calc.py", "loc": 4, "ccn": 2, "churn": 1, "score": 1.0}
    return {"files_scored": 1, "loc": {}, "ccn": {},
            "top_hotspots": [row], "top_complex": [row], "top_large": [row]}


def _self_ref_repo(git_repo: Any) -> tuple[Path, Any, str]:
    """src/calc.py co-committed with its test, then an unrelated commit (sha S2)."""
    repo, commit = git_repo
    (repo / "src").mkdir()
    (repo / "src" / "calc.py").write_text(
        "def add(a, b):\n    if a > b:\n        return a + b\n    return b + a\n",
        encoding="utf-8",
    )
    (repo / "src" / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# x\n", encoding="utf-8")
    (repo / ".assess").mkdir()
    (repo / ".assess" / "complexity-stats.json").write_text(json.dumps(_stats()))
    commit("one")
    (repo / "README.md").write_text("# x y\n", encoding="utf-8")
    commit("two")
    return repo, commit, _head(repo)


def _done_entry(sha: str, files: list[str] | None = None, **over: Any) -> dict[str, Any]:
    return {
        "rank": 1, "action": ACTION, "done_when": "reviewed", "scope_fence": "tests only",
        "status": "done", "claimed_by": "reviewer", "completed_sha": sha,
        "mode": "verify_then_retire", "finding": "self_referential_tests",
        "files": files if files is not None else ["src/calc.py"], **over,
    }


def _commit_actions(repo: Path, commit: Any, entries: list[dict[str, Any]],
                    key: str = "actions") -> None:
    (repo / ".assess" / "actions.json").write_text(
        json.dumps({"schema": 2, "run_id": None, key: entries}), encoding="utf-8",
    )
    commit("record review")


def _self_ref(ctx: dict[str, Any]) -> dict[str, Any]:
    return next(f for f in ctx["derived_findings"] if f["name"] == "self_referential_tests")


def _prescribed_paths(ctx: dict[str, Any]) -> list[str]:
    return [p["path"] for p in ctx["prescribed_actions"]]


# --- the full run ---------------------------------------------------------------

def test_no_actions_json_keeps_the_finding_prescribed(git_repo: Any) -> None:
    repo, _, _ = _self_ref_repo(git_repo)
    ctx = build_and_publish_run(repo_root=repo, run_date="2026-11-22")
    assert _self_ref(ctx)["paths"] == ["src/calc.py"]
    assert "reviewed" not in _self_ref(ctx)
    assert _prescribed_paths(ctx) == ["src/calc.py"]


def test_done_review_with_unchanged_source_clears_the_top_3(git_repo: Any) -> None:
    repo, commit, s2 = _self_ref_repo(git_repo)
    _commit_actions(repo, commit, [_done_entry(s2[:12])])
    ctx = build_and_publish_run(repo_root=repo, run_date="2026-11-22")
    finding = _self_ref(ctx)
    assert finding["paths"] == ["src/calc.py"]
    assert finding["reviewed"] == [
        {"path": "src/calc.py", "completed_sha": s2, "note": rf.review_note(s2)},
    ]
    assert s2 in finding["reviewed"][0]["note"]
    assert "src/calc.py" not in _prescribed_paths(ctx)
    assert all(a["path"] != "src/calc.py" for a in ctx["attention"])
    assert "### Reviewed" in ctx["findings_markdown"]
    assert s2 in ctx["findings_markdown"]


def test_a_later_source_change_re_flags_the_finding(git_repo: Any) -> None:
    repo, commit, s2 = _self_ref_repo(git_repo)
    _commit_actions(repo, commit, [_done_entry(s2)])
    with (repo / "src" / "calc.py").open("a", encoding="utf-8") as fh:
        fh.write("# changed\n")
    commit("change calc")
    ctx = build_and_publish_run(repo_root=repo, run_date="2026-11-22")
    assert "reviewed" not in _self_ref(ctx)
    assert _prescribed_paths(ctx) == ["src/calc.py"]


def test_done_entry_carried_under_completed_also_clears(git_repo: Any) -> None:
    repo, commit, s2 = _self_ref_repo(git_repo)
    _commit_actions(repo, commit, [_done_entry(s2)], key="completed")
    ctx = build_and_publish_run(repo_root=repo, run_date="2026-11-22")
    assert _self_ref(ctx)["reviewed"][0]["completed_sha"] == s2
    assert _prescribed_paths(ctx) == []


def test_an_uncommitted_actions_json_is_not_read(git_repo: Any) -> None:
    repo, _, s2 = _self_ref_repo(git_repo)
    (repo / ".assess" / "actions.json").write_text(
        json.dumps({"schema": 2, "actions": [_done_entry(s2)]}), encoding="utf-8",
    )
    ctx = build_and_publish_run(repo_root=repo, run_date="2026-11-22")
    assert _prescribed_paths(ctx) == ["src/calc.py"]


# --- the rule's refusals ----------------------------------------------------------

def _reviews_for(repo: Path, entry: dict[str, Any]) -> list[DerivedFinding]:
    findings: list[DerivedFinding] = [
        {"name": "self_referential_tests", "paths": ["src/calc.py"], "action": ACTION},
    ]
    (repo / ".assess" / "actions.json").write_text(
        json.dumps({"schema": 2, "actions": [entry]}), encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "a"],
                   check=True, capture_output=True)
    rf.mark_reviewed(findings, repo)
    return findings


def test_entries_that_do_not_clear(git_repo: Any) -> None:
    repo, _, s2 = _self_ref_repo(git_repo)
    refused = [
        _done_entry(s2, status="pending"),
        _done_entry(s2, finding="hidden_coupling"),
        _done_entry(s2, files=["src/test_calc.py"]),
        _done_entry("not-a-sha"),
        _done_entry("0" * 40),  # unknown commit
        _done_entry(None),
        _done_entry(s2, files="src/calc.py"),
    ]
    for entry in refused:
        assert "reviewed" not in _reviews_for(repo, entry)[0], entry


def test_a_sha_that_is_not_an_ancestor_does_not_clear(git_repo: Any) -> None:
    repo, commit, _ = _self_ref_repo(git_repo)
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a],  # noqa: E731
                                    check=True, capture_output=True)
    run("checkout", "-q", "-b", "side")
    (repo / "side.txt").write_text("s\n", encoding="utf-8")
    commit("side")
    side = _head(repo)
    run("checkout", "-q", "-")
    assert "reviewed" not in _reviews_for(repo, _done_entry(side))[0]


def test_no_git_and_bad_json_leave_the_finding_open(tmp_path: Path, git_repo: Any) -> None:
    findings: list[DerivedFinding] = [
        {"name": "self_referential_tests", "paths": ["src/calc.py"], "action": ACTION},
    ]
    rf.mark_reviewed(findings, tmp_path / "missing")
    assert "reviewed" not in findings[0]
    repo, commit, _ = _self_ref_repo(git_repo)
    (repo / ".assess" / "actions.json").write_text("{not json", encoding="utf-8")
    commit("bad")
    assert rf.committed_reviews(repo) == {}
    assert rf.action_entries(["not", "a", "dict"]) == []


# --- the products and the mutation refresh -----------------------------------------

def _reviewed_finding() -> DerivedFinding:
    return {
        "name": "self_referential_tests", "paths": ["src/a.py", "src/b.py"], "action": ACTION,
        "reviewed": [{"path": "src/a.py", "completed_sha": "abc1234", "note": rf.review_note("abc1234")}],
    }


def test_finding_products_read_reviewed_paths_out() -> None:
    products = finding_products([_reviewed_finding()], {}, None, {})
    assert [a["path"] for a in products["attention"]] == ["src/b.py"]
    assert [p["path"] for p in products["prescribed_actions"]] == ["src/b.py"]
    assert products["keyhole_summary"]["total_concerns"] == 1
    md = products["findings_markdown"]
    assert "- src/b.py" in md and "- src/a.py" not in md
    assert "`src/a.py` (self-referential tests): reviewed at abc1234" in md
    assert rf.render_reviewed_markdown([], finding_display_name) == ""


def test_mutation_refresh_keeps_the_reviewed_list() -> None:
    rebuilt = _rebuilt_findings([_reviewed_finding()], [])
    row = next(f for f in rebuilt if f["name"] == "self_referential_tests")
    assert row["reviewed"] == _reviewed_finding()["reviewed"]


# --- assess_finalize keeps done entries that left the Top 3 ------------------------

def test_finalize_carries_a_done_entry_that_left_the_top_3(tmp_path: Path) -> None:
    assess_dir = tmp_path / ".assess"
    assess_dir.mkdir()
    done = _done_entry("abc1234")
    (assess_dir / "actions.json").write_text(
        json.dumps({"schema": 2, "actions": [done]}), encoding="utf-8",
    )
    fresh = {"rank": 1, "action": "split the parser", "done_when": "x", "scope_fence": "y",
             "finding": "hidden_coupling", "files": ["src/parse.py"]}
    _write_actions_contract(assess_dir, [fresh])
    out = json.loads((assess_dir / "actions.json").read_text(encoding="utf-8"))
    assert [a["action"] for a in out["actions"]] == ["split the parser"]
    assert out["completed"] == [done]
    # A second finalize run keeps it: completed entries are read back as prior entries.
    _write_actions_contract(assess_dir, [fresh])
    out = json.loads((assess_dir / "actions.json").read_text(encoding="utf-8"))
    assert out["completed"] == [done]


def test_finalize_writes_no_completed_when_the_done_entry_is_still_listed(tmp_path: Path) -> None:
    assess_dir = tmp_path / ".assess"
    assess_dir.mkdir()
    done = _done_entry("abc1234")
    (assess_dir / "actions.json").write_text(
        json.dumps({"schema": 2, "actions": [done]}), encoding="utf-8",
    )
    again = {k: done[k] for k in ("rank", "action", "done_when", "scope_fence", "finding", "files")}
    _write_actions_contract(assess_dir, [again])
    out = json.loads((assess_dir / "actions.json").read_text(encoding="utf-8"))
    assert "completed" not in out
    assert out["actions"][0]["status"] == "done"
    assert rf.carried_completed({"k": [{"status": "pending"}]}, []) == []


def test_counts_only_keeps_the_reviewed_path_and_drops_the_note() -> None:
    out = counts_only({"derived_findings": [_reviewed_finding()]})
    reviewed = out["derived_findings"][0]["reviewed"]
    assert reviewed == [{"path": "src/a.py"}]


def test_badge_counts_only_findings_with_an_open_path() -> None:
    all_reviewed = {**_reviewed_finding(), "paths": ["src/a.py"]}
    assert concern_count_from_findings([all_reviewed]) == 0
    assert concern_count_from_findings([_reviewed_finding()]) == 1


def test_gate_does_not_fire_on_a_reviewed_path() -> None:
    gate: Any = {"fail_on": ["self_referential_tests"]}
    all_reviewed = {**_reviewed_finding(), "paths": ["src/a.py"]}
    assert check_finding_regressions({"derived_findings": [all_reviewed]}, gate) == ([], [])
    failures, _ = check_finding_regressions({"derived_findings": [_reviewed_finding()]}, gate)
    assert failures == [{"finding": "self_referential_tests", "count": 1, "paths": ["src/b.py"]}]
