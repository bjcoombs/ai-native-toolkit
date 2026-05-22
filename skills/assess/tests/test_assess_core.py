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
    assert ctx["instructions_grade"] is None  # no instructions = None (distinct from F)
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
    assert hotspots[0].name.startswith("src-foo-go-")
    assert hotspots[0].name.endswith(".md")


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


def test_instructions_grade_is_None_when_no_files(tmp_path: Path) -> None:
    """When no instruction file exists, instructions_grade is None (not 'F').

    Distinct from F: F means a file exists but scored badly. None means there's
    no file at all - different remediation ("create the file" vs "fix the file").
    """
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
    assert ctx["instructions_grade"] is None
    assert ctx["instruction_files"] == {}


def test_repo_root_not_in_ctx(tmp_path: Path) -> None:
    """ctx should not contain repo_root - it leaks the author's absolute path.

    The LLM consumer has $REPO_ROOT from its shell context; no need to serialize it.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 0, "loc": {}, "ccn": {},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert "repo_root" not in ctx


def test_plugin_version_in_ctx(tmp_path: Path) -> None:
    """ctx should include plugin_version so the LLM can surface it in the report.

    Mitigates the multi-version cache footgun: if /reload-plugins lands on an old
    cached version, the report shows that version and the user can spot the drift.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 0, "loc": {}, "ccn": {},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert "plugin_version" in ctx
    assert isinstance(ctx["plugin_version"], str)
    assert ctx["plugin_version"].count(".") >= 1


def test_scans_github_claude_instructions(tmp_path: Path, fixtures_dir: Path) -> None:
    """The scan finds .github/claude-instructions.md - a real-world non-canonical location.

    Surfaced by the v1.4 meridian run: .github/claude-review-instructions.md was a
    legitimate 795-line breadcrumb file that the canonical-paths-only scan missed.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    github_dir = repo / ".github"
    github_dir.mkdir()
    (github_dir / "claude-instructions.md").write_text(
        (fixtures_dir / "good_instructions.md").read_text()
    )

    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 0, "loc": {}, "ccn": {},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ".github/claude-instructions.md" in ctx["instruction_files"]


def test_scans_github_claude_review_instructions(tmp_path: Path, fixtures_dir: Path) -> None:
    """The scan finds .github/claude-review-instructions.md (used by claude-review bots)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    github_dir = repo / ".github"
    github_dir.mkdir()
    (github_dir / "claude-review-instructions.md").write_text(
        (fixtures_dir / "good_instructions.md").read_text()
    )

    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 0, "loc": {}, "ccn": {},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ".github/claude-review-instructions.md" in ctx["instruction_files"]


def test_scans_docs_subdirectory(tmp_path: Path, fixtures_dir: Path) -> None:
    """The scan finds docs/CLAUDE.md (some projects keep instruction files there)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    docs_dir = repo / "docs"
    docs_dir.mkdir()
    (docs_dir / "CLAUDE.md").write_text(
        (fixtures_dir / "good_instructions.md").read_text()
    )

    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 0, "loc": {}, "ccn": {},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert "docs/CLAUDE.md" in ctx["instruction_files"]


def test_briefing_includes_loc_ccn_commits_and_status(tmp_path: Path) -> None:
    """The auto-generated briefing should reflect the actual stats, not be vague."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 10, "loc": {"p50": 10, "p95": 30, "max": 50},
        "ccn": {"p50": 1, "p95": 3, "max": 5},
        "top_hotspots": [
            {"path": "src/foo.go", "loc": 500, "ccn": 20, "commits": 15},
        ],
        "top_complex": [{"path": "src/foo.go", "ccn": 20}],
        "top_large": [{"path": "src/foo.go", "loc": 500}],
    }))
    build_run_context(repo_root=repo, run_date="2026-05-22")

    page = next((assess_dir / "hotspots").iterdir())
    content = page.read_text(encoding="utf-8")
    assert "500 LOC" in content
    assert "max cyclomatic complexity 20" in content
    assert "15 commits" in content
    # has_tests should be "unknown" now, not "no"
    assert "Has test file | unknown" in content
