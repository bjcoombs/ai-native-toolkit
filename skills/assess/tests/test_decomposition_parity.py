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
"""
from __future__ import annotations

import json
from pathlib import Path

from assess_core import build_run_context
from assess_report import render_report
from golden import normalize_report

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
        "ccn": {"p50": 2.0, "p95": 8.0, "max": 20.0, "basis": "file-aggregate"},
        "top_hotspots": [
            {"path": "src/a.py", "loc": 500, "ccn": 20.0, "commits": 5},
            {"path": "src/b.py", "loc": 80, "ccn": 6.0, "commits": 2},
        ],
        "top_complex": [{"path": "src/a.py", "ccn": 20}],
        "top_large": [{"path": "src/a.py", "loc": 500}],
    }
    (assess / "complexity-stats.json").write_text(json.dumps(stats))
    (repo / "CLAUDE.md").write_text("# Project\n\nDo X. Always Y. Never Z.\n")
    return repo


def _render_parity_report(repo: Path) -> str:
    ctx = build_run_context(repo_root=repo, run_date="2026-01-01")
    return normalize_report(render_report(ctx, "parity-fixture"))


def test_report_parity_matches_golden(tmp_path):
    """The deterministic report must reproduce the committed golden byte-for-byte."""
    repo = _build_parity_fixture(tmp_path / "repo")
    assert _render_parity_report(repo) == GOLDEN.read_text(encoding="utf-8")


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
    for name in ("assess-findings", "assess-pr"):
        skill = REPO_ROOT / "skills" / name / "SKILL.md"
        assert skill.is_file(), f"missing decomposed sub-skill: {name}"
        assert "TRIGGER" in skill.read_text(encoding="utf-8"), f"{name}: needs TRIGGER clause"


def test_orchestrator_delegates_to_each_unit():
    body = ASSESS_SKILL.read_text(encoding="utf-8")
    assert "assess-layer-scorer" in body, "orchestrator must delegate layer scoring"
    assert "assess-findings" in body, "orchestrator must delegate report writing"
    assert "assess-pr" in body, "orchestrator must delegate end-of-run offers"


def test_orchestrator_is_thin():
    """The monolith was ~1290 lines; the thin orchestrator must stay well under."""
    lines = ASSESS_SKILL.read_text(encoding="utf-8").splitlines()
    assert len(lines) < 500, f"orchestrator grew to {len(lines)} lines - re-check the seams"
