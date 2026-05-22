"""End-to-end test for the assess_core orchestrator.

We don't run lizard/scc here - we drive assess_core via its public functions
to exercise the deterministic plumbing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from assess_core import build_run_context


def test_build_run_context_first_run(tmp_path: Path) -> None:
    """No prior .assess/, no instruction files - 'new' diff, empty instructions."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()

    current_stats = {
        "files_scored": 50,
        "loc": {"p50": 30, "p95": 200, "max": 500},
        "ccn": {"p50": 2, "p95": 8, "max": 20},
        "top_hotspots": [
            {"path": "src/a.go", "loc": 500, "ccn": 20, "commits": 5},
        ],
        "top_complex": [{"path": "src/a.go", "ccn": 20}],
        "top_large": [{"path": "src/a.go", "loc": 500}],
    }
    (assess_dir / "complexity-stats.json").write_text(json.dumps(current_stats))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")

    assert ctx["run_date"] == "2026-05-22"
    assert ctx["stats_summary"]["files_scored"] == 50
    assert ctx["instruction_files"] == {}  # nothing found
    assert ctx["instructions_grade"] == "F"  # no instructions = F
    assert ctx["diff"]["new"] == 1
    assert ctx["diff"]["graduated"] == 0
    assert (assess_dir / "log.md").exists()
    assert (assess_dir / "index.md").exists()


def test_build_run_context_with_claude_md(tmp_path: Path, fixtures_dir: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text((fixtures_dir / "good_instructions.md").read_text())
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 10, "loc": {"p50": 10, "p95": 30, "max": 50},
        "ccn": {"p50": 1, "p95": 3, "max": 5},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert "CLAUDE.md" in ctx["instruction_files"]
    assert ctx["instruction_files"]["CLAUDE.md"]["grade"] in {"A", "A-", "B+", "B"}
    assert ctx["instruction_files"]["CLAUDE.md"]["subscores"]["positive_directives"] >= 5
    # Top-level instructions_grade reflects the best of the present files
    assert ctx["instructions_grade"] in {"A", "A-", "B+", "B"}


def test_build_run_context_with_agents_md(tmp_path: Path, fixtures_dir: Path) -> None:
    """The grader is filename-agnostic - works for AGENTS.md too."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text((fixtures_dir / "good_instructions.md").read_text())
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 10, "loc": {"p50": 10, "p95": 30, "max": 50},
        "ccn": {"p50": 1, "p95": 3, "max": 5},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert "AGENTS.md" in ctx["instruction_files"]
    assert ctx["instruction_files"]["AGENTS.md"]["grade"] in {"A", "A-", "B+", "B"}


def test_build_run_context_with_multiple_instruction_files(tmp_path: Path, fixtures_dir: Path) -> None:
    """A repo can have CLAUDE.md AND AGENTS.md AND GEMINI.md (all pointing at the same content)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    good = (fixtures_dir / "good_instructions.md").read_text()
    bad = (fixtures_dir / "bad_instructions.md").read_text()
    (repo / "CLAUDE.md").write_text(good)
    (repo / "AGENTS.md").write_text(good)
    (repo / "GEMINI.md").write_text(bad)
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 10, "loc": {"p50": 10, "p95": 30, "max": 50},
        "ccn": {"p50": 1, "p95": 3, "max": 5},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    keys = set(ctx["instruction_files"].keys())
    assert {"CLAUDE.md", "AGENTS.md", "GEMINI.md"} <= keys
    # Top-level grade reflects the BEST of the present files
    assert ctx["instructions_grade"] in {"A", "A-", "B+", "B"}


def test_build_run_context_second_run_sees_diff(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()

    # First run state - persist prior stats
    prior_stats = {
        "files_scored": 50, "loc": {"p50": 30, "p95": 200, "max": 500},
        "ccn": {"p50": 2, "p95": 8, "max": 20},
        "top_hotspots": [
            {"path": "src/legacy.go", "loc": 500, "ccn": 20, "commits": 5},
        ],
        "top_complex": [{"path": "src/legacy.go", "ccn": 20}],
        "top_large": [{"path": "src/legacy.go", "loc": 500}],
    }
    (assess_dir / "complexity-stats.prior.json").write_text(json.dumps(prior_stats))

    current_stats = {
        "files_scored": 55, "loc": {"p50": 30, "p95": 220, "max": 550},
        "ccn": {"p50": 2, "p95": 9, "max": 22},
        "top_hotspots": [
            {"path": "src/new.go", "loc": 400, "ccn": 18, "commits": 4},
        ],
        "top_complex": [{"path": "src/new.go", "ccn": 18}],
        "top_large": [{"path": "src/new.go", "loc": 400}],
    }
    (assess_dir / "complexity-stats.json").write_text(json.dumps(current_stats))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ctx["diff"]["graduated"] == 1
    assert ctx["diff"]["new"] == 1


def test_build_run_context_writes_hotspot_pages(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 10, "loc": {"p50": 10, "p95": 30, "max": 50},
        "ccn": {"p50": 1, "p95": 3, "max": 5},
        "top_hotspots": [
            {"path": "src/foo.go", "loc": 500, "ccn": 20, "commits": 5},
        ],
        "top_complex": [{"path": "src/foo.go", "ccn": 20}],
        "top_large": [{"path": "src/foo.go", "loc": 500}],
    }))

    build_run_context(repo_root=repo, run_date="2026-05-22")
    hotspots = list((assess_dir / "hotspots").iterdir())
    assert len(hotspots) == 1
    assert hotspots[0].name == "src-foo-go.md"


def test_build_run_context_includes_anomalies_field(tmp_path: Path) -> None:
    """Every run-context.json must have an anomalies array (possibly empty)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 0,
        "loc": {}, "ccn": {},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert "anomalies" in ctx
    codes = {a["code"] for a in ctx["anomalies"]}
    assert "ZERO_FILES_SCORED" in codes
