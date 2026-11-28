"""Parity harness for the Part 3 SKILL.md decomposition.

The decomposition (orchestrator + layer-scorer agent + findings-writer sub-skill
+ pr-and-issues sub-skill) must be *behaviour-preserving*: it only reorganizes
Markdown and touches zero Python, so the deterministic pipeline
(``assess_core.build_run_context`` -> ``assess_report.render_report``) must
produce the byte-for-byte identical report it did before.

This module pins that invariant two ways:

1. **Report parity.** Build a fixed first-run fixture, render the deterministic
   report, normalize it (golden.normalize_report masks the version/commit
   provenance lines), and assert it equals the committed golden. A first-run
   fixture is used so the diff section and measured-commit provenance are
   deterministic (no prior sidecar, no git work-tree).
2. **Structural contract.** The decomposed units exist on disk and the
   orchestrator SKILL.md delegates to each of them, so a future edit that
   re-inlines or drops a unit fails loudly.

After a deliberate report change, regenerate the golden (``make regen`` runs this):

    cd skills/assess && PARITY_GOLDEN_REGEN=1 uv run --locked pytest tests/test_decomposition_parity.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from assess_core import build_and_publish_run
from assess_report import render_report
from golden import normalize_report
from lib.class_sizes import loc_by_class
from lib.skill_disclosure import _frontmatter_end

# skills/assess/tests/ -> repo root is three parents up from this file's dir.
REPO_ROOT = Path(__file__).resolve().parents[3]
ASSESS_SKILL = REPO_ROOT / "skills" / "assess" / "SKILL.md"
GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "decomposition-parity-report.md"


def _build_parity_fixture(repo: Path) -> Path:
    """Create the deterministic first-run fixture the golden was captured from.

    Plain dir (no git) + a fixed complexity-stats sidecar + a CLAUDE.md, so the
    pipeline output depends only on these inputs and the passed run_date.
    """
    repo.mkdir()
    assess = repo / ".assess"
    assess.mkdir()
    stats = {
        "files_scored": 3,
        "loc": {"p50": 30.0, "p95": 200.0, "max": 500.0, "total": 600},
        "loc_by_class": loc_by_class([("src/a.py", 500), ("src/b.py", 80), ("src/c.py", 20)]),
        "ccn": {"p50": 2.0, "p95": 8.0, "max": 20.0, "basis": "file-aggregate"},
        "top_hotspots": [
            {"path": "src/a.py", "loc": 500, "ccn": 20.0, "commits": 5},
            {"path": "src/b.py", "loc": 80, "ccn": 6.0, "commits": 2},
        ],
        "top_complex": [{"path": "src/a.py", "ccn": 20}],
        "top_large": [{"path": "src/a.py", "loc": 500}],
    }
    (assess / "complexity-stats.json").write_text(json.dumps(stats), encoding="utf-8")
    (repo / "CLAUDE.md").write_text("# Project\n\nDo X. Always Y. Never Z.\n", encoding="utf-8")
    return repo


def _render_parity_report(repo: Path) -> str:
    ctx = build_and_publish_run(repo_root=repo, run_date="2026-01-01")
    return normalize_report(render_report(ctx, "parity-fixture"))


def test_report_parity_matches_golden(tmp_path):
    """The deterministic report must reproduce the committed golden byte-for-byte."""
    repo = _build_parity_fixture(tmp_path / "repo")
    report = _render_parity_report(repo)
    if os.environ.get("PARITY_GOLDEN_REGEN") == "1":
        GOLDEN.write_text(report, encoding="utf-8", newline="\n")
    assert report == GOLDEN.read_text(encoding="utf-8")


def test_report_render_is_deterministic(tmp_path):
    """Two independent builds of the same fixture render identical reports."""
    a = _render_parity_report(_build_parity_fixture(tmp_path / "a"))
    b = _render_parity_report(_build_parity_fixture(tmp_path / "b"))
    assert a == b


# --- structural contract: the decomposed units exist and are wired up --------

def test_layer_scorer_agent_exists():
    agent = REPO_ROOT / "agents" / "assess-layer-scorer.md"
    assert agent.is_file()
    assert agent.read_text(encoding="utf-8").startswith("---")


def test_findings_and_pr_subskills_exist():
    # #939: "Why these three?" quotes the named entry's manifest `why`, then frames it here.
    findings = (REPO_ROOT / "skills" / "assess-findings" / "SKILL.md").read_text(encoding="utf-8")
    why = findings.split("### Why these three?", 1)[1].split("\n#", 1)[0]
    assert "quote that entry's `why` verbatim" in why
    assert "`level.conditions[]`" in why and "`gap_actions[]`" in why
    assert "the assess skill's `references/measures.json`" in why
    assert "framing after the quote" in why and "do not invent another reason" in why
    for name in ("assess-findings", "assess-pr"):
        skill = REPO_ROOT / "skills" / name / "SKILL.md"
        assert skill.is_file(), f"missing decomposed sub-skill: {name}"
        assert "TRIGGER" in skill.read_text(encoding="utf-8"), f"{name}: needs TRIGGER clause"


def test_orchestrator_delegates_to_each_unit():
    body = ASSESS_SKILL.read_text(encoding="utf-8")
    assert "assess-layer-scorer" in body, "orchestrator must delegate layer scoring"
    assert "assess-findings" in body, "orchestrator must delegate report writing"
    assert "assess-pr" in body, "orchestrator must delegate end-of-run offers"


THIN_BODY_LIMIT = 500


def _body_line_count(text: str) -> int:
    """Lines after the closed frontmatter block; the whole file if it has none.

    The limit guards the orchestrator's instructions, which live in the body, so
    frontmatter keys (name, description, any added later) do not count. Uses the
    same frontmatter reading as the /assess skill-disclosure scan's ``body_lines``.
    """
    lines = text.splitlines()
    return len(lines) - _frontmatter_end(lines)


def _assert_thin(text: str) -> None:
    count = _body_line_count(text)
    assert count < THIN_BODY_LIMIT, f"orchestrator grew to {count} body lines - re-check the seams"


def test_orchestrator_is_thin():
    """The monolith was ~1290 lines; the thin orchestrator's body must stay well under."""
    _assert_thin(ASSESS_SKILL.read_text(encoding="utf-8"))


def _skill_md(frontmatter: list[str], body_lines: int) -> str:
    """An invented SKILL.md: the given frontmatter keys, then a body of numbered lines."""
    body = [f"line {i}" for i in range(body_lines)]
    return "\n".join(["---", *frontmatter, "---", *body]) + "\n"


def test_thin_limit_ignores_frontmatter_keys():
    """Extra frontmatter keys leave the counted body, and so the verdict, unchanged."""
    short = _skill_md(["name: demo", "description: x"], 120)
    long = _skill_md(["name: demo", "description: x", "license: MIT", "allowed-tools: Read", "model: haiku"], 120)
    assert _body_line_count(short) == _body_line_count(long) == 120
    _assert_thin(long)


def test_thin_limit_fails_a_500_line_body():
    with pytest.raises(AssertionError, match="500 body lines"):
        _assert_thin(_skill_md(["name: demo"], 500))
    _assert_thin(_skill_md(["name: demo"], 499))


def test_attention_low_signal_is_read_before_the_rule_that_uses_it():
    """The findings sub-skill's run-context read loads every key its Top 3 rule branches on."""
    body = (REPO_ROOT / "skills" / "assess-findings" / "SKILL.md").read_text(encoding="utf-8")
    read = next(line for line in body.splitlines() if line.startswith("jq ") and ".prescribed_actions" in line)
    rule = next(line for line in body.splitlines() if line.startswith("**Mandatory attention rule"))
    for key in ("attention", "attention_low_signal", "prescribed_actions", "gap_actions"):
        assert f"`{key}`" in rule
        assert f".{key}," in read or read.rstrip().split("'")[1].endswith(f".{key}")

