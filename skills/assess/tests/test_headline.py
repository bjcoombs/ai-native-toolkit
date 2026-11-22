"""The deterministic /assess headline (lib/headline.py, #747) and the X / 8 retirement (#741).

Pure cases pin the wording of each line over hand-built level results; the end to
end cases run the core on the contract's ci2 shape (FL5 + CI workflow + CLAUDE.md),
read the headline, the badge, the deterministic report and the log entry, and
finalize an action that names a condition id.
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lib.headline import GATE_CHANGE, as_headline, headline, run_context_headline
from lib.level_tally import default_model

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ROOT = Path(__file__).resolve().parents[3]
BANDS = ("Not Ready", "Basic", "Solid", "AI-Native")
FRACTION = re.compile(r"\d(\.\d+)? ?/ ?8\b|X ?/ ?8")


def _level(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "level_result_version": 1, "levels_version": "m-1",
        "tally": {"met": 4, "measured": 6, "not_measured": 3},
        "counted": [], "listed": [],
        "not_measured": [{"id": "a.1", "reason": "needs_full_run"},
                         {"id": "b.1", "reason": "no_remote"}, {"id": "b.2", "reason": "no_remote"}],
        "conditions": [{"id": "c.1", "row": "c", "level": 1, "grade": "measured", "counted": True,
                        "state": "unmet"}],
        "step": 3,
        "next_step": {"step": 4, "threshold": 5, "blockers": [
            {"kind": "gate", "gate": "coverage_gate", "condition": "layer-6-coverage.1", "state": "unmet"},
            {"kind": "threshold", "threshold": 5, "met": 4}]},
    }
    base.update(over)
    return base


def test_three_lines_step_next_and_tally():
    h = headline(_level(), default_model())
    assert h["lines"] == [
        "Step 3 · Unattended agent PRs · contracts in place",
        "Next: Step 4 · Autonomous loops unlocks when a coverage gate is enforced and "
        "1 more verified practice is met, for example `c.1`",
        "Verified practices: 4 of 6 measured · 3 not measured (needs a full run: 1, no remote: 2)"
        " · model m-1",
    ]
    assert h["summary"] == "Step 3 · 4 of 6 measured"
    assert h["markdown"].startswith("**Step 3 · Unattended agent PRs · contracts in place**\n\n")
    assert (h["step"], h["next_step"], h["levels_version"], h["available"]) == (3, 4, "m-1", True)


def test_not_measured_gate_says_so_and_threshold_only_names_the_count():
    gate = {"kind": "gate", "gate": "ci_runs_tests", "condition": "layer-5-ci.1",
            "state": "not_measured", "reason": "needs_platform"}
    h = headline(_level(step=2, next_step={"step": 3, "threshold": 3, "blockers": [gate]}), default_model())
    assert h["lines"][1] == ("Next: Step 3 · Unattended agent PRs unlocks when CI runs the tests "
                             "(not measured here: needs GitHub or GitLab access)")
    lvl = _level(step=1, conditions=[], next_step={"step": 2, "threshold": 1, "blockers": [
        {"kind": "threshold", "threshold": 3, "met": 1}]})
    assert headline(lvl, default_model())["lines"][1] == (
        "Next: Step 2 · Supervised agent PRs unlocks when 2 more verified practices are met")


def test_top_step_and_zero_not_measured():
    h = headline(_level(step=4, next_step=None, not_measured=[],
                        tally={"met": 9, "measured": 9, "not_measured": 0}), default_model())
    assert h["lines"][1] == "Next: Step 4 is the top Step; it holds while its gates hold"
    assert h["lines"][2] == "Verified practices: 9 of 9 measured · 0 not measured · model m-1"
    assert h["next_step"] is None


def test_unknown_gate_and_requires_reason_fall_back_to_their_ids():
    lvl = _level(not_measured=[{"id": "x.1", "reason": "requires:layer-5-ci.1"}],
                 next_step={"step": 4, "threshold": 0, "blockers": [
                     {"kind": "gate", "gate": "own_gate", "condition": "x.1", "state": "unmet"}]})
    h = headline(lvl, default_model())
    assert h["lines"][1].endswith("unlocks when the own_gate gate holds")
    assert "waits on layer-5-ci.1: 1" in h["lines"][2]


def test_every_default_gate_has_wording():
    gates = {g["id"] for s in default_model().get("steps", []) for g in s["gates"]}
    assert gates and gates <= set(GATE_CHANGE)


def test_unavailable_level_claims_no_step():
    for value in (None, {"available": False, "reason": "boom"}, "x"):
        h = run_context_headline(value)
        assert h["available"] is False and h["step"] is None
        assert h["summary"] == "Step not measured"
    assert as_headline({"available": False, "reason": "degraded"})["reason"] == "degraded"
    good = headline(_level(), default_model())
    assert as_headline(good) is good


def test_headline_has_no_layered_fraction_or_band_names():
    h = headline(_level(), default_model())
    text = "\n".join([*h["lines"], h["summary"], h["markdown"]])
    assert not FRACTION.search(text)
    for band in BANDS:
        assert band not in text


def test_retired_score_rule_is_gone_from_skills_and_agents():
    """#741: the X / 8 worked example and its 'subtract the partials' shortcut are retired
    with the layered headline (#747), so neither survives anywhere in skills/ or agents/."""
    # Built from pieces so this file does not match the contract's own grep.
    pattern = re.compile("|".join(("subtract the " + "half-points", "8 - 0" + r"\.5 ×",
                                   "displayed score " + r"is 7\.0")), re.IGNORECASE)
    hits = [f"{p.relative_to(ROOT).as_posix()}" for base in ("skills", "agents")
            for p in (ROOT / base).rglob("*") if p.is_file() and p.suffix in (".md", ".py", ".toml")
            and pattern.search(p.read_text(encoding="utf-8", errors="replace"))]
    assert hits == []
    findings = (ROOT / "skills" / "assess-findings" / "SKILL.md").read_text(encoding="utf-8")
    assert "min(raw_sum" not in findings and "X / <denominator>" not in findings


# --- end to end on the contract's ci2 shape -----------------------------------

CI2 = {
    "src/a.py": "def f():\n    return 1\n",
    "Makefile": "test:\n\tpython3 -m unittest discover -s tests\n",
    "tests/test_a.py": "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n"
                       "        self.assertTrue(True)\n",
    ".github/workflows/ci.yml": "name: ci\non: [pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
                               "    steps:\n      - uses: actions/checkout@v4\n      - run: make test\n",
    "CLAUDE.md": "# Project\n\nBefore every commit run `make test` and fix any failure.\n",
}


def _commit(repo: Path, date: str, message: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    for args in (("add", "-A"), ("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", message)):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def ci2(tmp_path: Path) -> Path:
    repo = tmp_path / "ci2"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
    for rel, text in CI2.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8", newline="\n")
    _commit(repo, "2026-11-01T10:00:00Z", "one")
    (repo / ".keep").write_text("2\n", encoding="utf-8")
    _commit(repo, "2026-11-02T10:00:00Z", "two")
    (repo / ".assess").mkdir()
    (repo / ".assess" / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 2, "loc": {}, "ccn": {}, "top_hotspots": [], "top_complex": [], "top_large": [],
    }), encoding="utf-8")
    return repo


def _run(repo: Path, day: str) -> dict[str, Any]:
    from assess_core import build_and_publish_run

    return build_and_publish_run(repo_root=repo, run_date=day, non_interactive=True)


def _finalize(repo: Path, run_id: str, condition: str) -> subprocess.CompletedProcess[str]:
    cache = repo / ".assess" / ".cache"
    cache.mkdir(exist_ok=True)
    (cache / "finalize-input.json").write_text(json.dumps({
        "run_id": run_id, "top_action": "Enforce a coverage gate",
        "actions": [{"rank": 1, "action": "Enforce a coverage gate", "done_when": "CI fails below 80%",
                     "scope_fence": "CI config only", "condition": condition}],
    }), encoding="utf-8")
    return subprocess.run([sys.executable, str(SCRIPTS / "assess_finalize.py"), str(repo)],
                          capture_output=True, encoding="utf-8", errors="replace")


def test_ci2_headline_badge_report_and_log_agree(ci2: Path):
    first = _run(ci2, "2026-11-21")
    badge1 = json.loads((ci2 / ".assess" / "badge.json").read_text(encoding="utf-8"))
    second = _run(ci2, "2026-11-22")
    badge2 = json.loads((ci2 / ".assess" / "badge.json").read_text(encoding="utf-8"))
    assert first["headline"] == second["headline"]
    assert badge1["message"] == badge2["message"]
    level, head = second["level"], second["headline"]
    assert badge2["message"] == f"Step {level['step']} · {level['tally']['met']} of {level['tally']['measured']} measured"
    assert head["lines"][0].startswith(f"Step {level['step']} · ") and "contracts in place" in head["lines"][0]
    assert head["lines"][1].startswith(f"Next: Step {level['next_step']['step']} · ")
    assert f"{level['tally']['not_measured']} not measured" in head["lines"][2]
    assert f"model {level['levels_version']}" in head["lines"][2]
    report = subprocess.run([sys.executable, str(SCRIPTS / "assess_report.py"), str(ci2), "--stdout"],
                            capture_output=True, encoding="utf-8", errors="replace", check=True).stdout
    assert head["markdown"] in report
    log = (ci2 / ".assess" / "log.md").read_text(encoding="utf-8")
    assert f"**Readiness:** {head['summary']}" in log
    for text in (badge2["message"], report, log):
        assert not FRACTION.search(text)
        for band in BANDS:
            assert band not in text


def test_ci2_finalize_checks_the_action_condition(ci2: Path):
    ctx = _run(ci2, "2026-11-22")
    assert "layer-5-ci.1" in {c["id"] for c in ctx["level"]["conditions"]}
    ok = _finalize(ci2, ctx["run_id"], "layer-5-ci.1")
    assert ok.returncode == 0, ok.stderr
    actions = ci2 / ".assess" / "actions.json"
    before = actions.read_bytes()
    assert json.loads(before)["actions"][0]["condition"] == "layer-5-ci.1"
    ctx = _run(ci2, "2026-11-22")
    bad = _finalize(ci2, ctx["run_id"], "layer-99-none.1")
    assert bad.returncode == 1
    assert "layer-99-none.1" in bad.stderr
    assert actions.read_bytes() == before


def test_mutation_recount_rewrites_headline_and_badge(ci2: Path, monkeypatch: pytest.MonkeyPatch):
    import assess_core

    _run(ci2, "2026-11-22")
    ctx_path = ci2 / ".assess" / "run-context.json"
    ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    stale = copy.deepcopy(ctx)
    stale["headline"]["summary"] = "stale"
    ctx_path.write_text(json.dumps(stale), encoding="utf-8")
    monkeypatch.setattr(assess_core, "mutation_scope", lambda focus: ["src/a.py"])
    monkeypatch.setattr(assess_core, "scan_test_pressure", lambda *a, **k: ctx["test_pressure"])
    assert assess_core.run_opt_in_mutation(ci2) == 0
    after = json.loads(ctx_path.read_text(encoding="utf-8"))
    assert after["headline"] == ctx["headline"]
    badge = json.loads((ci2 / ".assess" / "badge.json").read_text(encoding="utf-8"))
    assert badge["message"] == ctx["headline"]["summary"]
