"""End-to-end test for the assess_core orchestrator.

We don't run lizard/scc here - we drive assess_core via its public functions
to exercise the deterministic plumbing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import assess_core
from assess_core import build_run_context


def _minimal_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 0, "loc": {}, "ccn": {},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }))
    return repo


_EMPTY_STATS = json.dumps({
    "files_scored": 0, "loc": {}, "ccn": {},
    "top_hotspots": [], "top_complex": [], "top_large": [],
})


def _seed_assess(repo: Path) -> None:
    (repo / ".assess").mkdir(exist_ok=True)
    (repo / ".assess" / "complexity-stats.json").write_text(_EMPTY_STATS)


def test_untracked_instruction_file_flagged_not_graded(git_repo, fixtures_dir: Path) -> None:
    """Issue #34 Gap 1: an on-disk-but-untracked instruction file isn't credited
    to the grade, and is surfaced as a finding."""
    repo, commit = git_repo
    good = (fixtures_dir / "good_instructions.md").read_text()
    _seed_assess(repo)
    (repo / ".github").mkdir()
    (repo / ".github" / "copilot-instructions.md").write_text(good, encoding="utf-8")
    commit("committed instructions")
    (repo / "CLAUDE.md").write_text(good, encoding="utf-8")  # untracked (after commit)

    ctx = build_run_context(repo_root=repo, run_date="2026-05-28")
    assert ".github/copilot-instructions.md" in ctx["instruction_files"]
    assert "CLAUDE.md" not in ctx["instruction_files"]          # untracked -> not graded
    assert "CLAUDE.md" in ctx["untracked_instruction_files"]    # but flagged


def test_dangling_symlink_instruction_is_broken_ref(git_repo) -> None:
    """Issue #34 Gap 2: a committed instruction file that is a dangling symlink
    is an advertised-but-broken reference."""
    import os
    repo, commit = git_repo
    _seed_assess(repo)
    (repo / "README.md").write_text("# Repo", encoding="utf-8")
    os.symlink("missing-target.md", repo / ".cursorrules")  # dangling symlink
    commit("init with dangling .cursorrules")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-28")
    refs = ctx["broken_instruction_refs"]
    assert any(r.get("path") == ".cursorrules" and "symlink" in r["reason"] for r in refs)


def test_broken_link_to_instruction_file_is_broken_ref(git_repo) -> None:
    """Issue #34 Gap 2: an entry doc linking a missing instruction file."""
    repo, commit = git_repo
    _seed_assess(repo)
    (repo / "README.md").write_text("see the [rules](AGENTS.md)", encoding="utf-8")
    commit("init; README links a missing AGENTS.md")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-28")
    refs = ctx["broken_instruction_refs"]
    assert any(Path(r.get("target", "")).name == "AGENTS.md" for r in refs)


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


def test_readside_blocks_present_in_ctx(tmp_path: Path) -> None:
    """run-context.json must carry the Layer 0/1 read-side blocks."""
    repo = _minimal_repo(tmp_path)
    (repo / "README.md").write_text("# Project\nsee [code](app.py)\n")
    (repo / "app.py").write_text("x = 1\n")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-27")
    for key in ("doc_graph", "doc_staleness", "stale_hubs", "dead_code", "observability"):
        assert key in ctx, f"missing read-side block: {key}"
    assert ctx["doc_graph"]["available"] is True
    assert ctx["doc_graph"]["doc_count"] == 1
    assert ctx["doc_staleness"]["available"] is True
    assert isinstance(ctx["stale_hubs"], list)
    assert "rung" in ctx["observability"]
    assert "candidate_count" in ctx["dead_code"]


def test_stale_hubs_join_centrality_and_staleness(tmp_path: Path) -> None:
    """stale_hubs ranks central docs by pagerank x staleness ratio."""
    repo = _minimal_repo(tmp_path)
    (repo / "hub.md").write_text("hub")
    for i in range(3):
        (repo / f"leaf{i}.md").write_text("see [hub](hub.md)")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-27")
    # Each stale-hub row carries both the centrality and staleness factors.
    if ctx["stale_hubs"]:
        row = ctx["stale_hubs"][0]
        assert {"path", "pagerank", "ratio", "priority"} <= set(row)


def test_readside_scan_failure_degrades_not_crashes(tmp_path: Path, monkeypatch) -> None:
    """A raising scan must degrade to an unavailable marker, not blow up the run."""
    repo = _minimal_repo(tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("simulated scan failure")

    monkeypatch.setattr(assess_core, "build_doc_graph", boom)
    ctx = build_run_context(repo_root=repo, run_date="2026-05-27")
    assert ctx["doc_graph"]["available"] is False
    assert "failed" in ctx["doc_graph"]["reason"]
    # downstream blocks still present
    assert "observability" in ctx
    assert ctx["stale_hubs"] == []  # can't join hubs without a graph


def test_failed_liveness_scan_is_not_scored_rung_0(tmp_path: Path, monkeypatch) -> None:
    """A failed liveness scan must read as 'not assessed' (rung null), not as a
    genuine rung 0 (no observability) - conflating them mis-scores Layer 1."""
    repo = _minimal_repo(tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("liveness blew up")

    monkeypatch.setattr(assess_core, "scan_liveness", boom)
    ctx = build_run_context(repo_root=repo, run_date="2026-05-27")
    assert ctx["observability"]["available"] is False
    assert ctx["observability"]["rung"] is None  # not 0
    assert "reason" in ctx["observability"]
    assert ctx["dead_code"]["available"] is False


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
