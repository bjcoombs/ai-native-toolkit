"""Tests for lib/instruction_files.py: instruction-file discovery and grading, alias
grade inheritance, broken references, sensitive content and the ancestor
cascade, driven through build_run_context.

Moved out of test_assess_core.py when assess_core's helpers split into
lib/ modules; shared repo-seeding helpers live in assess_core_helpers.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from assess_core import build_run_context
from assess_core_helpers import _minimal_repo, _seed_assess


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


def test_sensitive_content_surfaced_for_committed_file(git_repo, fixtures_dir: Path) -> None:
    """Issue #56: a committed instruction file carrying an IP / home path is
    surfaced (redacted) so the remediation can warn before any further commit."""
    repo, commit = git_repo
    good = (fixtures_dir / "good_instructions.md").read_text()
    _seed_assess(repo)
    (repo / "CLAUDE.md").write_text(
        good + "\n\n## Demo\nServer 203.0.113.7, ssh root@demo.example.com\n"
        "Config at /Users/ben/.config/app.yaml\n",
        encoding="utf-8",
    )
    commit("instructions with infra detail")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-28")
    flagged = ctx["sensitive_instruction_content"]
    assert "CLAUDE.md" in flagged
    cats = {f["category"] for f in flagged["CLAUDE.md"]}
    assert {"ip_address", "ssh_or_host", "home_path"} <= cats
    # Evidence must be redacted - no raw secret survives into run-context.
    blob = json.dumps(flagged)
    assert "203.0.113.7" not in blob and "/Users/ben" not in blob


def test_sensitive_content_surfaced_for_untracked_file(git_repo, fixtures_dir: Path) -> None:
    """Issue #56: the file the remediation might tell you to commit (an
    untracked CLAUDE.md) is scanned even though it isn't graded."""
    repo, commit = git_repo
    good = (fixtures_dir / "good_instructions.md").read_text()
    _seed_assess(repo)
    (repo / "README.md").write_text("# Repo", encoding="utf-8")
    commit("init")
    (repo / "CLAUDE.md").write_text(  # untracked
        good + "\nAWS key AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8"
    )

    ctx = build_run_context(repo_root=repo, run_date="2026-05-28")
    assert "CLAUDE.md" in ctx["untracked_instruction_files"]   # not graded
    assert "CLAUDE.md" in ctx["sensitive_instruction_content"]  # but scanned
    assert any(f["category"] == "cloud_key"
               for f in ctx["sensitive_instruction_content"]["CLAUDE.md"])


def test_agents_md_symlink_alias_inherits_claude_grade(git_repo, fixtures_dir: Path) -> None:
    """Issue #57: AGENTS.md as a symlink to CLAUDE.md is the single-source-of-
    truth shape - it inherits CLAUDE.md's grade, not a standalone score."""
    import os
    repo, commit = git_repo
    good = (fixtures_dir / "good_instructions.md").read_text()
    _seed_assess(repo)
    (repo / "CLAUDE.md").write_text(good, encoding="utf-8")
    os.symlink("CLAUDE.md", repo / "AGENTS.md")
    commit("CLAUDE.md + AGENTS.md alias")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-28")
    files = ctx["instruction_files"]
    assert files["AGENTS.md"]["is_alias"] is True
    assert files["AGENTS.md"]["alias_target"] == "CLAUDE.md"
    assert files["AGENTS.md"]["grade"] == files["CLAUDE.md"]["grade"]


def test_agents_md_thin_stub_alias_inherits_grade(git_repo, fixtures_dir: Path) -> None:
    """Issue #57: a thin AGENTS.md stub pointing at CLAUDE.md inherits its grade
    instead of scoring low as a bespoke doc the remediation would rewrite."""
    repo, commit = git_repo
    good = (fixtures_dir / "good_instructions.md").read_text()
    _seed_assess(repo)
    (repo / "CLAUDE.md").write_text(good, encoding="utf-8")
    (repo / "AGENTS.md").write_text(
        "# AGENTS.md\n\nSee [CLAUDE.md](./CLAUDE.md) for all instructions.\n",
        encoding="utf-8",
    )
    commit("CLAUDE.md + thin AGENTS.md stub")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-28")
    files = ctx["instruction_files"]
    assert files["AGENTS.md"]["is_alias"] is True
    assert files["AGENTS.md"]["alias_target"] == "CLAUDE.md"
    assert files["AGENTS.md"]["grade"] == files["CLAUDE.md"]["grade"]


def test_ancestor_instruction_files_key_present(tmp_path: Path) -> None:
    """Issue #57: the ancestor-cascade signal is always surfaced as a list."""
    repo = _minimal_repo(tmp_path)
    ctx = build_run_context(repo_root=repo, run_date="2026-05-28")
    assert isinstance(ctx["ancestor_instruction_files"], list)


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
