"""Tests for lib/mutation_cap.py: the run-context test_pressure block shape and
the Layer 6 mutation-not-run cap.

Moved out of test_assess_core.py when assess_core's helpers split into
lib/ modules; shared repo-seeding helpers live in assess_core_helpers.py.
"""
from __future__ import annotations

from pathlib import Path

import assess_core
from assess_core import build_run_context
from assess_core_helpers import _minimal_repo


# ════════════════════════════════════════════════════════════════════════════
# Task 4 - test_pressure wiring into build_run_context / run-context.json
# ════════════════════════════════════════════════════════════════════════════

def test_test_pressure_block_present_in_ctx(tmp_path: Path) -> None:
    """run-context.json carries a test_pressure block with the required fields.

    A hollow test (asserts on a private field, no public assertion) must surface
    as an assertion_on_internal candidate so the LLM can fold it into Layer 1."""
    repo = _minimal_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "guard.py").write_text("class Guard:\n    pass\n", encoding="utf-8")
    (repo / "test_guard.py").write_text(
        "def test_resume():\n"
        "    g = Guard()\n"
        "    assert g._resume_count == 1\n",
        encoding="utf-8",
    )

    ctx = build_run_context(repo_root=repo, run_date="2026-05-29")
    assert "test_pressure" in ctx
    tp = ctx["test_pressure"]
    assert "mutation_config_present" in tp
    assert "cheap_heuristics" in tp
    # opt_in defaults off: /assess stays read-only, no mutation run.
    assert tp["mutation_run"] is False
    assert tp["cheap_heuristics"]["assertion_on_internal"]


def test_test_pressure_mutation_not_run_by_default(tmp_path: Path) -> None:
    """The bounded mutation pass is opt-in - a default run must never invoke it.

    scan_test_pressure is called with opt_in=False, so even a repo whose language
    is present and whose tool is on PATH does not get mutated by /assess."""
    repo = _minimal_repo(tmp_path)
    (repo / "app.py").write_text("def f(): return 1\n", encoding="utf-8")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-29")
    tp = ctx["test_pressure"]
    assert tp["mutation_run"] is False
    assert tp["per_file"] == []


def test_test_pressure_scan_failure_degrades_not_crashes(tmp_path: Path, monkeypatch) -> None:
    """A raising test_pressure scan must degrade to an unavailable marker that
    preserves failure semantics: mutation_config_present is None (not False) so
    the LLM never reads a failed scan as 'no mutation setup', and the cheap
    heuristic buckets are present-but-empty so the consumer's shape is stable."""
    repo = _minimal_repo(tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("simulated test_pressure failure")

    monkeypatch.setattr(assess_core, "scan_test_pressure", boom)
    ctx = build_run_context(repo_root=repo, run_date="2026-05-29")
    tp = ctx["test_pressure"]
    assert tp["available"] is False
    assert "reason" in tp
    assert tp["mutation_config_present"] is None  # NOT False - "not assessed"
    assert tp["cheap_heuristics"] == {
        "assertion_on_internal": [],
        "untested_boundaries": [],
        "duplicate_truth": [],
    }
    # downstream blocks still present - one failed scan never blocks the run.
    assert "anomalies" in ctx
    assert "plugin_version" in ctx


def test_mutation_not_run_cap_applies_on_default_scan(tmp_path: Path) -> None:
    """The default read-only pass never runs mutation, so Layer 6 is capped at
    Partial and the required annotation is present for the LLM to read."""
    repo = _minimal_repo(tmp_path)
    ctx = build_run_context(repo_root=repo, run_date="2026-07-07")
    cap = ctx["mutation_not_run_cap"]
    assert cap["applies"] is True
    assert cap["mutation_run"] is False
    assert cap["max_layer6_band"] == "Partial"
    assert cap["annotation"] == "truth-pressure unproven (mutation not run)"


def test_mutation_run_requires_parsed_mutants_for_cap_lift() -> None:
    """mutation_not_run_cap trusts mutation_run only when per_file carries a real
    mutant record (#317). An empty per_file keeps the cap; a record lifts it."""
    from lib.mutation_cap import mutation_not_run_cap

    empty = mutation_not_run_cap(
        {"mutation_run": True, "mutation_scope": ["src/a.ts"], "per_file": []})
    assert empty["applies"] is True
    assert empty["mutation_run"] is False
    assert empty["max_layer6_band"] == "Partial"
    assert empty["annotation"] == "truth-pressure unproven (mutation not run)"

    missing = mutation_not_run_cap({"mutation_run": True})
    assert missing["applies"] is True

    real = mutation_not_run_cap({
        "mutation_run": True, "mutation_scope": ["src/a.ts"],
        "per_file": [{"path": "src/a.ts", "killed": 3, "survived": 1, "total": 4}],
    })
    assert real["applies"] is False
    assert real["max_layer6_band"] == "Present"
    assert real["annotation"] is None
