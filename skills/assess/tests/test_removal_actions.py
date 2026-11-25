"""A removal proposal end to end (#1084): an oversized always-on instruction file gets a
``remove`` action from the core, and finalize writes it to actions.json with its mode
and measured cost."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from lib.gap_actions import REMOVAL_FINDING, REMOVE_MODE
from lib.instructions.instruction_placement import ALWAYS_ON_FLAG_LINES
from test_headline import _commit, _run

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _repo(tmp_path: Path, lines: int) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
    # one heading-free file: a single unclassified section, counted always-on (share 1.0)
    body = "".join(f"Remember point {n} about how this project is laid out.\n" for n in range(lines))
    (repo / "CLAUDE.md").write_text("Run `make test` before every commit.\n" + body, encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _commit(repo, "2026-11-01T10:00:00Z", "one")
    (repo / ".assess").mkdir()
    (repo / ".assess" / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 1, "loc": {}, "ccn": {}, "top_hotspots": [], "top_complex": [], "top_large": [],
    }), encoding="utf-8")
    return repo


def test_an_oversized_always_on_instruction_file_gets_a_remove_action(tmp_path: Path) -> None:
    repo = _repo(tmp_path, ALWAYS_ON_FLAG_LINES + 60)
    ctx = _run(repo, "2026-11-21")
    removals = [g for g in ctx["gap_actions"] if g.get("mode") == REMOVE_MODE]
    assert len(removals) == 1
    gap = removals[0]
    assert gap["paths"] == ["CLAUDE.md"] and gap["finding"] == REMOVAL_FINDING
    assert gap["cost"]["metric"] == "always_on_lines" and gap["cost"]["value"] > ALWAYS_ON_FLAG_LINES
    # the writer transcribes it into the Top 3; finalize stamps the mode and keeps the cost
    cache = repo / ".assess" / ".cache"
    cache.mkdir(exist_ok=True)
    (cache / "finalize-input.json").write_text(json.dumps({
        "run_id": ctx["run_id"], "top_action": gap["action"],
        "actions": [{"rank": 1, "action": gap["action"], "done_when": "always_on_lines <= 200",
                     "scope_fence": "CLAUDE.md only", "finding": gap["finding"], "files": gap["paths"],
                     "cost": gap["cost"]}],
    }), encoding="utf-8")
    done = subprocess.run([sys.executable, str(SCRIPTS / "assess_finalize.py"), str(repo)],
                          capture_output=True, encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stderr
    (action,) = json.loads((repo / ".assess" / "actions.json").read_text(encoding="utf-8"))["actions"]
    assert (action["mode"], action["cost"]) == (REMOVE_MODE, gap["cost"])


def test_a_file_within_the_flag_gets_no_remove_action(tmp_path: Path) -> None:
    ctx = _run(_repo(tmp_path, ALWAYS_ON_FLAG_LINES - 50), "2026-11-21")
    assert not [g for g in ctx["gap_actions"] if g.get("mode") == REMOVE_MODE]


def test_a_shrinking_removal_keeps_its_lifecycle_by_finding_and_files(tmp_path: Path) -> None:
    """Review of #1129: the directive embeds the live line count, so identity is finding + files."""
    repo = _repo(tmp_path, ALWAYS_ON_FLAG_LINES + 60)
    ctx = _run(repo, "2026-11-21")
    (gap,) = [g for g in ctx["gap_actions"] if g.get("mode") == REMOVE_MODE]

    def finalize(action_text: str, run_id: str) -> dict[str, Any]:
        cache = repo / ".assess" / ".cache"
        cache.mkdir(exist_ok=True)
        (cache / "finalize-input.json").write_text(json.dumps({
            "run_id": run_id, "top_action": action_text,
            "actions": [{"rank": 1, "action": action_text, "done_when": "always_on_lines <= 200",
                         "scope_fence": "CLAUDE.md only", "finding": gap["finding"], "files": gap["paths"],
                         "cost": gap["cost"]}]}), encoding="utf-8")
        done = subprocess.run([sys.executable, str(SCRIPTS / "assess_finalize.py"), str(repo)],
                              capture_output=True, encoding="utf-8", errors="replace")
        assert done.returncode == 0, done.stderr
        entry: dict[str, Any] = json.loads((repo / ".assess" / "actions.json").read_text(encoding="utf-8"))["actions"][0]
        return entry

    finalize(gap["action"], ctx["run_id"])
    contract = json.loads((repo / ".assess" / "actions.json").read_text(encoding="utf-8"))
    contract["actions"][0].update({"status": "done", "completed_sha": "a" * 40})
    (repo / ".assess" / "actions.json").write_text(json.dumps(contract), encoding="utf-8")
    again = _run(repo, "2026-11-22")
    kept = finalize(gap["action"].replace(str(gap["cost"]["value"]), "999"), again["run_id"])
    assert (kept["status"], kept["completed_sha"], kept["mode"]) == ("done", "a" * 40, REMOVE_MODE)


def test_a_removal_that_keeps_every_gate_for_two_runs_is_credited_in_the_ledger(tmp_path: Path) -> None:
    """#1084 success criterion: propose, trim, and after two runs with the gates held the ledger
    credits it. run-context.json is deleted before each run: the count lives in the committed
    prior-level.json, so a fresh clone or CI keeps it."""
    from test_since_last_run import _git
    repo = _repo(tmp_path, ALWAYS_ON_FLAG_LINES + 60)
    first = _run(repo, "2026-11-21")
    (gap,) = [g for g in first["gap_actions"] if g.get("mode") == REMOVE_MODE]
    before = gap["cost"]["value"]
    (repo / "CLAUDE.md").write_text("Run `make test` before every commit.\n"
                                    + "Keep functions small.\n" * 40, encoding="utf-8")
    _commit(repo, "2026-11-22T10:00:00Z", "trim the always-on instructions")
    sha = _git(repo, "rev-parse", "HEAD")
    (repo / ".assess" / "actions.json").write_text(json.dumps({"schema": 2, "actions": [{
        "rank": 1, "action": gap["action"], "status": "done", "completed_sha": sha,
        "finding": gap["finding"], "files": gap["paths"], "cost": gap["cost"], "mode": REMOVE_MODE}]}),
        encoding="utf-8")
    _commit(repo, "2026-11-22T11:00:00Z", "mark the removal done")
    (repo / ".assess" / "run-context.json").unlink()
    second = _run(repo, "2026-11-22")["since_last_run"]
    assert second["pruned"] == [] and second["pruning"][0]["runs_held"] == 1
    assert any(line.startswith("Pruning in progress: `CLAUDE.md` (1 of 2") for line in second["lines"])
    (repo / "src" / "b.py").write_text("def g():\n    return 2\n", encoding="utf-8")
    _commit(repo, "2026-11-23T10:00:00Z", "more work")
    (repo / ".assess" / "run-context.json").unlink()
    third = _run(repo, "2026-11-23")["since_last_run"]
    (credit,) = third["pruned"]
    assert credit["credited"] and credit["saved"] == before - 41
    assert f"- Pruned: always-on text in `CLAUDE.md`, saved {before - 41} always-on lines" in third["markdown"]
    report = subprocess.run([sys.executable, str(SCRIPTS / "assess_report.py"), str(repo), "--stdout"],
                            capture_output=True, encoding="utf-8", errors="replace", check=True).stdout
    assert "pruned always-on text in `CLAUDE.md`" in report.split("\n## ", 1)[0]


def test_a_degraded_run_keeps_the_baseline_and_the_ledger(tmp_path: Path, monkeypatch: Any) -> None:
    """#1158 success criterion: good, degraded, good. The third run still credits the action closed
    since the first and continues the ledger's hold count from where the first run left it."""
    import lib.core_scans as cs
    from test_since_last_run import _git
    repo = _repo(tmp_path, ALWAYS_ON_FLAG_LINES + 60)
    proposal = _run(repo, "2026-11-20")
    (gap,) = [g for g in proposal["gap_actions"] if g.get("mode") == REMOVE_MODE]
    (repo / "CLAUDE.md").write_text("Run `make test` before every commit.\n" + "Keep functions small.\n" * 40,
                                    encoding="utf-8")
    _commit(repo, "2026-11-21T10:00:00Z", "trim")
    trim = _git(repo, "rev-parse", "HEAD")
    removal = {"rank": 1, "action": gap["action"], "status": "done", "completed_sha": trim,
               "finding": gap["finding"], "files": gap["paths"], "cost": gap["cost"], "mode": REMOVE_MODE}
    (repo / ".assess" / "actions.json").write_text(json.dumps({"schema": 2, "actions": [removal]}),
                                                   encoding="utf-8")
    _commit(repo, "2026-11-21T11:00:00Z", "mark the removal done")
    first = _run(repo, "2026-11-21")  # good: the removal holds once
    assert first["since_last_run"]["pruning"][0]["runs_held"] == 1
    stored = (repo / ".assess" / "prior-level.json").read_bytes()
    # one more action closed before the degraded run
    (repo / "src" / "b.py").write_text("def g():\n    return 2\n", encoding="utf-8")
    _commit(repo, "2026-11-22T10:00:00Z", "the fix")
    fix = _git(repo, "rev-parse", "HEAD")
    other = {"rank": 2, "action": "Add a helper", "status": "done", "completed_sha": fix}
    (repo / ".assess" / "actions.json").write_text(json.dumps({"schema": 2, "actions": [removal, other]}),
                                                   encoding="utf-8")
    _commit(repo, "2026-11-22T11:00:00Z", "mark the fix done")

    def boom(_ctx: Any, _choice: Any) -> Any:
        raise RuntimeError("scan timed out")
    with monkeypatch.context() as m:
        m.setattr(cs, "run_level", boom)  # the level scan's engine (lib.profiles.run_level, #1150)
        degraded = _run(repo, "2026-11-22")
    assert (repo / ".assess" / "prior-level.json").read_bytes() == stored  # untouched
    assert f"`{first['run_id']}`" in "\n".join(degraded["since_last_run"]["lines"])
    (repo / "src" / "c.py").write_text("def h():\n    return 3\n", encoding="utf-8")
    _commit(repo, "2026-11-23T10:00:00Z", "more work")
    third = _run(repo, "2026-11-23")["since_last_run"]
    assert third["previous_run_id"] == first["run_id"]
    assert third["closed_count"] == 1 and third["closed"][0]["completed_sha"] == fix
    assert "- Closed since last run: 1" in third["markdown"]
    (credit,) = third["pruned"]  # the hold continued from 1 to 2: credited
    assert credit["credited"] and credit["runs_held"] == 2
