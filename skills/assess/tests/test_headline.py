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

from lib.headline import GATE_CHANGE, GATE_NOUN, GLOSSARY_URL, as_headline, gate_change, headline, run_context_headline
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
        "next_step": {"step": 4, "blockers": [
            {"kind": "gate", "gate": "coverage_gate", "condition": "layer-6-coverage.1", "state": "unmet"}]},
    }
    base.update(over)
    return base


def test_three_lines_step_next_and_coverage():
    h = headline(_level(), default_model())
    assert h["lines"] == [
        "Step 3 · Unattended agent PRs · contracts in place",
        "Next: Step 4 · Autonomous loops unlocks when a coverage gate is enforced",
        "Catalogue coverage: 4 of 6 measured, 0 verified · 3 not measured (needs a full run: 1, no remote: 2)"
        " · model m-1",
    ]
    assert h["summary"] == "Step 3 · next: coverage gate"
    assert h["markdown"].startswith("**Step 3 · Unattended agent PRs · contracts in place**\n\n")
    assert (h["step"], h["next_step"], h["levels_version"], h["available"]) == (3, 4, "m-1", True)


def test_the_markdown_links_measured_and_verified_once_and_the_lines_stay_plain():
    """#1102: the catalogue coverage line in the markdown links each grade word to the
    glossary once; ``lines``, the badge summary and the log entry stay plain text."""
    h = headline(_level(), default_model())
    coverage, grade = f"{GLOSSARY_URL}#catalogue-coverage", f"{GLOSSARY_URL}#evidence-grade"
    assert (f"Catalogue coverage: 4 of 6 [measured]({coverage}), 0 [verified]({grade})"
            " · 3 not measured (needs a full run: 1, no remote: 2) · model m-1") in h["markdown"]
    assert h["markdown"].count("](") == 2
    assert all("](" not in line for line in [*h["lines"], h["summary"]])


def test_the_summary_and_next_line_carry_no_count():
    """#1083: the badge and log.md summary, and the Next line, name gates and never an "of" count."""
    for lvl in (_level(), _level(step=4, next_step=None), _level(step=1, next_step={"step": 2, "blockers": [
            {"kind": "gate", "gate": "instructions", "condition": "layer-0-instructions.1", "state": "unmet"}]})):
        h = headline(lvl, default_model())
        assert not re.search(r"\d+ (of|in place of) \d+", h["summary"]), h["summary"]
        assert "practice" not in h["lines"][1] and " of " not in h["lines"][1]
    assert headline(_level(step=4, next_step=None), default_model())["summary"] == "Step 4 · all gates held"
    assert headline(_level(step=1, next_step={"step": 2, "blockers": [
        {"kind": "gate", "gate": "instructions", "condition": "layer-0-instructions.1", "state": "unmet"}]}),
        default_model())["summary"] == "Step 1 · next: instruction file"


def test_the_summary_names_the_first_blocking_gate_and_an_unknown_gate_by_id():
    two = _level(step=2, next_step={"step": 4, "blockers": [
        {"kind": "gate", "gate": "ci_runs_tests", "condition": "layer-5-ci.1", "state": "unmet"},
        {"kind": "gate", "gate": "coverage_gate", "condition": "layer-6-coverage.1", "state": "unmet"}]})
    assert headline(two, default_model())["summary"] == "Step 2 · next: CI runs tests"
    own = _level(next_step={"step": 4, "blockers": [{"kind": "gate", "gate": "own_gate", "condition": "x.1",
                                                     "state": "unmet"}]})
    assert headline(own, default_model())["summary"] == "Step 3 · next: own_gate"


def test_a_model_without_steps_says_so_with_no_count():
    h = headline(_level(step=None, next_step=None), default_model())
    assert h["summary"] == "no Steps defined"
    assert h["lines"][0].startswith("Catalogue coverage: ")


def test_not_measured_gate_says_so():
    gate = {"kind": "gate", "gate": "ci_runs_tests", "condition": "layer-5-ci.1",
            "state": "not_measured", "reason": "needs_platform"}
    h = headline(_level(step=2, next_step={"step": 3, "blockers": [gate]}), default_model())
    assert h["lines"][1] == ("Next: Step 3 · Unattended agent PRs unlocks when CI turns red when a check breaks "
                             "(not measured here: needs GitHub or GitLab access)")


def test_top_step_and_zero_not_measured():
    h = headline(_level(step=4, next_step=None, not_measured=[],
                        tally={"met": 9, "measured": 9, "not_measured": 0}), default_model())
    assert h["lines"][1] == "Next: Step 4 is the top Step; it holds while its gates hold"
    assert h["lines"][2] == "Catalogue coverage: 9 of 9 measured, 0 verified · 0 not measured · model m-1"
    assert h["next_step"] is None


def test_unknown_gate_and_requires_reason_fall_back_to_their_ids():
    lvl = _level(not_measured=[{"id": "x.1", "reason": "requires:layer-5-ci.1"}],
                 next_step={"step": 4, "blockers": [
                     {"kind": "gate", "gate": "own_gate", "condition": "x.1", "state": "unmet"}]})
    h = headline(lvl, default_model())
    assert h["lines"][1].endswith("unlocks when the own_gate gate holds")
    assert "waits on layer-5-ci.1: 1" in h["lines"][2]


def test_every_default_gate_has_wording():
    gates = {g["id"] for s in default_model().get("steps", []) for g in s["gates"]}
    assert gates and gates <= set(GATE_CHANGE) and gates <= set(GATE_NOUN)


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
    # the executive summary's last line reads the since-last-run block, which differs by run (#1102)
    commit_only = ("summary_lines", "summary_markdown")
    assert ({k: v for k, v in first["headline"].items() if k not in commit_only}
            == {k: v for k, v in second["headline"].items() if k not in commit_only})
    assert first["headline"]["summary_lines"][:2] == second["headline"]["summary_lines"][:2]
    assert badge1["message"] == badge2["message"]
    level, head = second["level"], second["headline"]
    assert badge2["message"] == head["summary"]
    assert badge2["message"].startswith(f"Step {level['step']} · ") and " of " not in badge2["message"]
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
    from lib.run_wiki import refresh_log_readiness

    assert refresh_log_readiness(ci2 / ".assess", ctx["run_id"], "stale")
    monkeypatch.setattr(assess_core, "mutation_scope", lambda focus: ["src/a.py"])
    monkeypatch.setattr(assess_core, "scan_test_pressure", lambda *a, **k: ctx["test_pressure"])
    assert assess_core.run_opt_in_mutation(ci2) == 0
    after = json.loads(ctx_path.read_text(encoding="utf-8"))
    assert after["headline"] == ctx["headline"]
    badge = json.loads((ci2 / ".assess" / "badge.json").read_text(encoding="utf-8"))
    assert badge["message"] == ctx["headline"]["summary"]
    log = (ci2 / ".assess" / "log.md").read_text(encoding="utf-8")
    assert f"- **Readiness:** {ctx['headline']['summary']}" in log


def test_log_readiness_line_is_rewritten_and_rechained(ci2: Path):
    from lib.run_wiki import refresh_log_readiness
    from lib.wiki_writer import verify_log_chain

    ctx = _run(ci2, "2026-11-22")
    assess = ci2 / ".assess"
    assert refresh_log_readiness(assess, ctx["run_id"], "Step 9 · all gates held")
    assert "- **Readiness:** Step 9 · all gates held" in (assess / "log.md").read_text(encoding="utf-8")
    assert verify_log_chain(assess) == (True, None)
    assert not refresh_log_readiness(assess, ctx["run_id"], "Step 9 · all gates held")
    assert not refresh_log_readiness(assess, None, "x")
    assert not refresh_log_readiness(assess, "no-such-run", "x")


def test_counts_only_keeps_the_headline_numbers_and_drops_its_text():
    """#770's lesson: fail when a numeric headline field is dropped from counts-only output."""
    from lib.counts_only.content_free import counts_only

    head = headline(_level(), default_model())
    kept = counts_only({"headline": head})["headline"]
    # a dropped string list is kept as its length, a dropped string goes
    dropped = ("markdown", "summary", "next_why", "call_to_action", "summary_markdown")
    assert kept == {**{k: v for k, v in head.items() if k not in dropped},
                    "lines": len(head["lines"]), "summary_lines": len(head["summary_lines"])}


def test_next_why_holds_the_whys_of_the_unmet_gate_conditions_only() -> None:
    """#939: the Next line's why is each blocking gate's unmet conditions' whys, nothing else."""
    from lib.level_tally import level_result
    from lib.levels import load_levels
    from lib.signal_grades import DEFAULT_LEVELS

    model = load_levels(DEFAULT_LEVELS)
    level = level_result(model, {})
    h = headline(level, model)
    by_id = {c["id"]: c for c in level["conditions"]}
    blockers = [b for b in (level["next_step"] or {}).get("blockers", []) if b["kind"] == "gate"]
    assert blockers
    expected = [by_id[b["condition"]]["why"] for b in blockers]
    assert all(w in h["next_why"] for w in expected)
    gated = {b["condition"].rsplit(".", 1)[0] for b in blockers}
    others = [c["why"] for c in level["conditions"] if c["row"] not in gated]
    assert not any(w in h["next_why"] for w in others)
    assert headline(_level(next_step=None), default_model())["next_why"] == ""
    no_gate = _level(next_step={"step": 4, "blockers": []})
    assert headline(no_gate, default_model())["next_why"] == ""


def test_as_headline_backfills_next_why_on_an_older_block() -> None:
    old = {k: v for k, v in headline(_level(), default_model()).items() if k != "next_why"}
    assert as_headline(old)["next_why"] == ""
    assert as_headline(headline(_level(), default_model()))["next_why"] == headline(_level(), default_model())["next_why"]


# --- the verified share and the per-tag strip (#844) ---------------------------

def _cond(cid: str, state: str, grade: str = "measured", counted: bool = True, **extra: Any) -> dict[str, Any]:
    row, lv = cid.rsplit(".", 1)
    return {"id": cid, "row": row, "level": int(lv), "grade": grade, "counted": counted,
            "state": state, **extra}


def _tagged_model() -> dict[str, Any]:
    level = lambda n, truth, **x: {"level": n, "criteria": [], "truth": truth, **x}  # noqa: E731
    return {"version": "t-1", "rows": [
        {"id": "r", "title": "R", "scope": "repo", "aggregation": None,
         "levels": [level(1, ["behaviour", "docs"]), level(2, ["behaviour"]), level(3, ["docs"])]},
        {"id": "h", "title": "H", "scope": "repo", "aggregation": None,
         "levels": [level(1, ["governance"], polarity="hazard"), level(2, ["governance"])]},
    ], "steps": []}


def _tagged_level() -> dict[str, Any]:
    return _level(step=None, next_step=None, levels_version="t-1",
                  tally={"met": 2, "measured": 2, "not_measured": 1},
                  not_measured=[{"id": "r.3", "reason": "no_remote"}],
                  conditions=[_cond("r.1", "met", grade="verified"), _cond("r.2", "met"),
                              _cond("r.3", "not_measured"),
                              _cond("h.1", "met", counted=False, polarity="hazard"),
                              _cond("h.2", "declined")])


def test_tally_line_shows_the_verified_share_of_n():
    h = headline(_tagged_level(), _tagged_model())  # type: ignore[arg-type]
    assert h["lines"][0] == ("Catalogue coverage: 2 of 2 measured, 1 verified · 1 not measured "
                             "(no remote: 1) · model t-1")
    assert h["verified"] == 1
    assert h["summary"] == "no Steps defined"


def test_strip_counts_a_multi_tagged_condition_in_each_cell_and_once_in_n():
    h = headline(_tagged_level(), _tagged_model())  # type: ignore[arg-type]
    assert h["lines"][1] == ("By tag: behaviour 2 of 2 · docs 1 of 1 (NOT MEASURED: 1) · "
                             "governance 0 of 0 (HAZARD: 1, DECLINED: 1)")
    cells = {c["tag"]: c for c in h["tags"]}
    assert [c["tag"] for c in h["tags"]] == ["behaviour", "docs", "governance"]
    assert cells["behaviour"] == {"tag": "behaviour", "met": 2, "measured": 2, "verified": 1,
                                  "not_measured": 0, "hazards": 0, "declined": 0}
    assert cells["docs"]["verified"] == 1 and cells["docs"]["not_measured"] == 1
    # r.1 sits in two cells; the tally counts it once
    assert sum(c["met"] for c in h["tags"]) == 3 and h["lines"][0].startswith("Catalogue coverage: 2 of")


def test_no_tagged_condition_gives_no_strip_line():
    h = headline(_level(), default_model())
    assert h["tags"] == [] and not any(line.startswith("By tag:") for line in h["lines"])
    assert run_context_headline(None)["tags"] == [] and run_context_headline(None)["verified"] is None


def test_counts_only_keeps_the_strip_numbers_and_tag_tokens():
    from lib.counts_only.content_free import counts_only

    head = headline(_tagged_level(), _tagged_model())  # type: ignore[arg-type]
    kept = counts_only({"headline": head})["headline"]
    assert kept["verified"] == 1
    assert kept["tags"] == head["tags"]
    assert "summary" not in kept and "markdown" not in kept


def test_ci2_strip_and_verified_total_match_the_conditions(ci2: Path):
    """The #844 acceptance on a real run: a strip renders and V is the verified conditions met."""
    ctx = _run(ci2, "2026-11-22")
    level, head = ctx["level"], ctx["headline"]
    verified = [c for c in level["conditions"]
                if c["counted"] and c["state"] == "met" and c["grade"] == "verified"]
    assert head["verified"] == len(verified)
    assert f"measured, {len(verified)} verified · " in head["lines"][2]
    assert head["lines"][3].startswith("By tag: ") and head["tags"]
    for cell in head["tags"]:
        assert f"{cell['tag']} {cell['met']} of {cell['measured']}" in head["lines"][3]


def test_gate_change_is_public_and_words_a_gate_like_the_next_line():
    """The Step ladder imports it (#1104, #1099): the caption and the Next line agree."""
    blocker = {"kind": "gate", "gate": "coverage_gate", "condition": "layer-6-coverage.1", "state": "unmet"}
    assert gate_change(blocker) == "a coverage gate is enforced"
    assert headline(_level(), default_model())["lines"][1].endswith(gate_change(blocker))
