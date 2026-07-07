"""End-to-end test for the assess_core orchestrator.

We don't run lizard/scc here - we drive assess_core via its public functions
to exercise the deterministic plumbing.
"""
from __future__ import annotations

import json
from pathlib import Path


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


def test_archetype_block_emitted_for_software_repo(git_repo) -> None:
    """Issue #224: run-context carries an archetype block; a code repo is software."""
    repo, commit = git_repo
    _seed_assess(repo)
    src = repo / "src"
    src.mkdir()
    for i in range(12):
        (src / f"mod_{i}.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    commit("software repo")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-28")
    arch = ctx["archetype"]
    assert arch["available"] is True
    assert arch["archetype"] == "software"
    assert arch["na_layers"] == []
    assert arch["denominator"] == 8


def test_archetype_block_detects_knowledge_base(git_repo) -> None:
    """Issue #224: a markdown-only repo is detected as a knowledge base with
    write-side layers N/A and a renormalised denominator."""
    repo, commit = git_repo
    _seed_assess(repo)
    notes = repo / "notes"
    notes.mkdir()
    for i in range(15):
        (notes / f"note-{i}.md").write_text(f"# Note {i}\n\nbody\n", encoding="utf-8")
    (repo / "CLAUDE.md").write_text(
        "# KB\nRaw sources are immutable. A periodic consolidation pass lints the wiki.\n",
        encoding="utf-8",
    )
    commit("knowledge base")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-28")
    arch = ctx["archetype"]
    assert arch["archetype"] == "knowledge-base"
    assert arch["na_layers"] == [2, 3, 4, 5, 6, 7]
    assert arch["denominator"] == 3
    assert arch["kb_maintenance"]["documented"] is True


def test_archetype_override_marker_suppresses(git_repo) -> None:
    """Issue #224: an `assess-archetype: software` marker forces software even
    on a markdown-only repo."""
    repo, commit = git_repo
    _seed_assess(repo)
    notes = repo / "notes"
    notes.mkdir()
    for i in range(15):
        (notes / f"note-{i}.md").write_text(f"# Note {i}\n", encoding="utf-8")
    (repo / "CLAUDE.md").write_text(
        "# Docs repo\n\n<!-- assess-archetype: software -->\n", encoding="utf-8"
    )
    commit("override to software")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-28")
    arch = ctx["archetype"]
    assert arch["archetype"] == "software"
    assert arch["detected_via"] == "override"


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


def test_unfinalized_hotspot_page_uses_neutral_pointer_not_placeholder(tmp_path: Path) -> None:
    """A hotspot page the deterministic core writes - before any LLM finalize -
    must carry the neutral out-of-Top-3 pointer, never a TODO-style placeholder.

    assess_finalize only rewrites the pages it's handed actions for (at minimum
    the Top 3), so a flagged-but-not-Top-3 page can ship un-finalized. Its default
    "Suggested actions" body must read as intentional, not as unfinished work
    (issue #165).
    """
    from lib.wiki_writer import UNFINALIZED_ACTIONS_POINTER, slug_for_path

    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 50,
        "loc": {"p50": 30, "p95": 200, "max": 500},
        "ccn": {"p50": 2, "p95": 8, "max": 20},
        "top_hotspots": [{"path": "src/a.go", "loc": 500, "ccn": 20, "commits": 5}],
        "top_complex": [{"path": "src/a.go", "ccn": 20}],
        "top_large": [{"path": "src/a.go", "loc": 500}],
    }))

    build_run_context(repo_root=repo, run_date="2026-05-22")

    page = (assess_dir / "hotspots" / f"{slug_for_path('src/a.go')}.md").read_text(encoding="utf-8")
    # The "## Suggested actions" heading the finalizer keys off must survive.
    assert "## Suggested actions" in page
    # The neutral pointer is present...
    assert UNFINALIZED_ACTIONS_POINTER in page
    # ...and no TODO/placeholder marker leaks into the committed page.
    for marker in ("Pending LLM-generated suggestions", "TODO", "placeholder", "FIXME"):
        assert marker not in page


def test_hotspot_page_carries_growth_profile_when_file_accretes(git_repo) -> None:
    """A top-hotspot file that the accretion scan flags (monotonic growth, low
    deletion across several commits) gets the growth-profile line wired into its
    generated hotspot page - tasks 4+5 end-to-end."""
    from lib.wiki_writer import slug_for_path

    repo, commit = git_repo
    src = repo / "src"
    src.mkdir()
    grower = src / "grower.go"
    # Five commits that only add lines and never delete: pure accretion.
    for i in range(1, 6):
        grower.write_text("\n".join(f"line {n}" for n in range(i * 40)) + "\n")
        commit(f"grow {i}", days_ago=(6 - i) * 20)

    assess_dir = repo / ".assess"
    assess_dir.mkdir(exist_ok=True)
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 50,
        "loc": {"p50": 30, "p95": 200, "max": 500},
        "ccn": {"p50": 2, "p95": 8, "max": 20},
        "top_hotspots": [{"path": "src/grower.go", "loc": 200, "ccn": 20, "commits": 5}],
        "top_complex": [{"path": "src/grower.go", "ccn": 20}],
        "top_large": [{"path": "src/grower.go", "loc": 200}],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-06-17")
    # Precondition: the scan actually flagged this file (else the test proves nothing).
    flagged = {f["path"] for f in ctx["accretion_ratchet"].get("files", [])}
    assert "src/grower.go" in flagged

    # The block must also reach the keyhole: a populated accretion_ratchet block
    # is wired into the derived finding (assess_core passes it to integrate), so
    # the cross-layer finding lists the path - not just the run-context block.
    finding = next(
        f for f in ctx["derived_findings"] if f["name"] == "accretion_ratchet"
    )
    assert "src/grower.go" in finding["paths"]

    page = (assess_dir / "hotspots" / f"{slug_for_path('src/grower.go')}.md").read_text(
        encoding="utf-8"
    )
    assert "Growth profile: monotonic" in page
    assert "0 net reductions over" in page


def test_hotspot_page_omits_growth_profile_when_scan_unavailable(tmp_path: Path) -> None:
    """Graceful degradation: outside a git repo the accretion scan is
    unavailable, so the hotspot page is still written - just without a growth
    line. Hotspot generation never depends on the scan succeeding."""
    from lib.wiki_writer import slug_for_path

    repo = tmp_path / "repo"  # not a git repo
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 50,
        "loc": {"p50": 30, "p95": 200, "max": 500},
        "ccn": {"p50": 2, "p95": 8, "max": 20},
        "top_hotspots": [{"path": "src/a.go", "loc": 500, "ccn": 20, "commits": 5}],
        "top_complex": [{"path": "src/a.go", "ccn": 20}],
        "top_large": [{"path": "src/a.go", "loc": 500}],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-06-17")
    assert ctx["accretion_ratchet"]["available"] is False

    page = (assess_dir / "hotspots" / f"{slug_for_path('src/a.go')}.md").read_text(
        encoding="utf-8"
    )
    # Page exists and is complete, but carries no growth profile.
    assert "## Suggested actions" in page
    assert "Growth profile" not in page


def test_unfinalized_actions_pointer_carries_no_placeholder_marker() -> None:
    """The neutral pointer text itself must be free of TODO-style markers - it is
    the default that ships when a page is never finalized."""
    from lib.wiki_writer import UNFINALIZED_ACTIONS_POINTER

    lowered = UNFINALIZED_ACTIONS_POINTER.lower()
    for marker in ("pending", "todo", "placeholder", "fixme", "tbd"):
        assert marker not in lowered


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


def test_graduated_index_row_carries_current_metrics(tmp_path: Path) -> None:
    """Issue #52 Bug 1: when a file graduates off top_hotspots[:10] but is
    still present in top_complex or top_large, the index row must show its
    *current* CCN and LOC, not 0. Zero in those columns reads as "the file
    was emptied," contradicts assess-report.md, and misleads reviewers."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()

    # Prior run: src/legacy.go was a top hotspot.
    prior_stats = {
        "files_scored": 50, "loc": {}, "ccn": {},
        "top_hotspots": [
            {"path": "src/legacy.go", "loc": 1096, "ccn": 172, "commits": 5},
        ],
        "top_complex": [{"path": "src/legacy.go", "ccn": 172}],
        "top_large": [{"path": "src/legacy.go", "loc": 1096}],
    }
    (assess_dir / "complexity-stats.prior.json").write_text(json.dumps(prior_stats))

    # Current run: src/legacy.go dropped off top_hotspots (a bigger file
    # took its slot) but still appears in top_complex and top_large at its
    # actual current metrics. This is the case CodeRabbit caught.
    current_stats = {
        "files_scored": 55, "loc": {}, "ccn": {},
        "top_hotspots": [
            {"path": "src/giant.go", "loc": 2500, "ccn": 200, "commits": 8},
        ],
        "top_complex": [
            {"path": "src/giant.go", "ccn": 200},
            {"path": "src/legacy.go", "ccn": 172},
        ],
        "top_large": [
            {"path": "src/giant.go", "loc": 2500},
            {"path": "src/legacy.go", "loc": 1096},
        ],
    }
    (assess_dir / "complexity-stats.json").write_text(json.dumps(current_stats))

    build_run_context(repo_root=repo, run_date="2026-05-29")
    index = (assess_dir / "index.md").read_text(encoding="utf-8")

    # The graduated row must reflect reality: 1,096 LOC, ccn 172. NEVER 0.
    legacy_row = next(line for line in index.splitlines() if "src/legacy.go" in line)
    assert "graduated" in legacy_row
    assert "| 172 | 1096 |" in legacy_row, (
        f"expected current ccn/loc, got: {legacy_row}"
    )
    # The active row stays intact.
    assert "src/giant.go" in index


def test_graduated_index_row_uses_dash_when_metrics_unknown(tmp_path: Path) -> None:
    """When a graduated file fell off every top-N list (no current metrics
    available anywhere), the row renders `-` rather than misleading zeros."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()

    # Prior: src/legacy.go was a hotspot.
    prior_stats = {
        "files_scored": 50, "loc": {}, "ccn": {},
        "top_hotspots": [
            {"path": "src/legacy.go", "loc": 800, "ccn": 90, "commits": 3},
        ],
        "top_complex": [{"path": "src/legacy.go", "ccn": 90}],
        "top_large": [{"path": "src/legacy.go", "loc": 800}],
    }
    (assess_dir / "complexity-stats.prior.json").write_text(json.dumps(prior_stats))

    # Current: src/legacy.go fell off ALL top-N lists (none of them mention it).
    current_stats = {
        "files_scored": 55, "loc": {}, "ccn": {},
        "top_hotspots": [
            {"path": "src/new.go", "loc": 600, "ccn": 80, "commits": 4},
        ],
        "top_complex": [{"path": "src/new.go", "ccn": 80}],
        "top_large": [{"path": "src/new.go", "loc": 600}],
    }
    (assess_dir / "complexity-stats.json").write_text(json.dumps(current_stats))

    build_run_context(repo_root=repo, run_date="2026-05-29")
    index = (assess_dir / "index.md").read_text(encoding="utf-8")
    legacy_row = next(line for line in index.splitlines() if "src/legacy.go" in line)
    # Sentinel "-" not "0" - never lie about the size.
    assert "| - | - |" in legacy_row


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


def test_run_context_has_deterministic_keyhole_products(tmp_path: Path) -> None:
    """assess-dogfooded Part 1: run-context.json carries the deterministic
    report-skeleton products - the pre-rendered findings markdown, the keyhole
    readiness summary, and the prescribed Top-3 actions - plus the eight derived
    findings (six original + E1/E2 trust axis)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 1, "loc": {}, "ccn": {},
        "top_hotspots": [{"path": "src/a.py", "loc": 100, "ccn": 12, "commits": 3}],
        "top_complex": [{"path": "src/a.py", "ccn": 12}],
        "top_large": [{"path": "src/a.py", "loc": 100}],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")

    # Task 2: deterministic findings markdown is present and well-formed.
    assert "findings_markdown" in ctx
    assert ctx["findings_markdown"].startswith(
        "## Cross-Layer Findings (Keyhole Readiness)"
    )
    # Task 3: keyhole readiness summary reported alongside the 0-8 score.
    assert "keyhole_summary" in ctx
    assert set(ctx["keyhole_summary"]) == {
        "concerns", "safe_zones", "total_concerns", "summary_text"
    }
    # Task 4: prescribed actions array exists (possibly empty for a clean repo).
    assert "prescribed_actions" in ctx
    assert isinstance(ctx["prescribed_actions"], list)
    # Task 5: derived findings now carry the nine named axes in fixed order.
    names = [f["name"] for f in ctx["derived_findings"]]
    assert names == [
        "hidden_coupling", "lying_map", "unexplained_complexity",
        "untrusted_hotspot", "self_referential_tests",
        "unactioned_intent", "accretion_ratchet",
        "orphaned_understanding", "candidate_dead_weight", "refactor_boundary",
    ]


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


def test_keyhole_blocks_present_and_backward_compatible(git_repo) -> None:
    """Task #5 integration barrier: build_run_context emits the five new keyhole
    blocks + derived findings while leaving every existing block intact."""
    repo, commit = git_repo
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    # A small history so the change-coupling / containment / authorship signals
    # have real git data to chew on.
    (repo / "README.md").write_text("# Project\nsee [code](src/app.py)\n")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("def f():\n    return 1\n")
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 1, "loc": {"p50": 2, "p95": 2, "max": 2},
        "ccn": {"p50": 1, "p95": 1, "max": 1},
        "top_hotspots": [{"path": "src/app.py", "loc": 2, "ccn": 1, "commits": 2}],
        "top_complex": [{"path": "src/app.py", "ccn": 1}],
        "top_large": [{"path": "src/app.py", "loc": 2}],
    }))
    commit("init")
    (repo / "src" / "app.py").write_text("def f():\n    return 2\n")
    commit("change")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-29")

    # (1) All five new blocks present.
    for key in ("structure", "behaviour", "documentation", "understanding", "runtime"):
        assert key in ctx, f"missing keyhole block: {key}"
    # structure carries available/reason so the report can say "grimp absent".
    assert "available" in ctx["structure"]
    assert "containment_by_dir" in ctx["behaviour"]
    assert "freshness_by_doc" in ctx["documentation"]
    assert "authorship_class_by_path" in ctx["understanding"]
    assert "static_reachability" in ctx["runtime"]

    # (2) derived_findings populated; every finding has name/paths/action.
    assert "derived_findings" in ctx
    findings = ctx["derived_findings"]
    assert findings, "derived_findings must not be empty"
    expected = {"hidden_coupling", "lying_map", "unexplained_complexity",
                "untrusted_hotspot", "self_referential_tests",
                "unactioned_intent", "accretion_ratchet",
                "orphaned_understanding", "candidate_dead_weight", "refactor_boundary"}
    assert {f["name"] for f in findings} == expected
    for f in findings:
        assert set(f) == {"name", "paths", "action"}
        assert isinstance(f["paths"], list)
        assert isinstance(f["action"], str) and f["action"]
    assert "attention" in ctx
    assert isinstance(ctx["attention"], list)

    # (3) Existing blocks unchanged (backward-compat): the pre-existing shape
    # is all still there alongside the additions.
    for key in ("run_date", "stats_summary", "instruction_files", "diff",
                "doc_graph", "doc_staleness", "stale_hubs", "dead_code",
                "observability", "anomalies", "plugin_version"):
        assert key in ctx, f"existing block dropped: {key}"


def test_keyhole_signal_failure_degrades_not_crashes(tmp_path: Path, monkeypatch) -> None:
    """A raising keyhole signal must degrade to available:false, not crash the
    run or disturb the existing blocks (defensive-wiring constraint)."""
    repo = _minimal_repo(tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("simulated structure failure")

    monkeypatch.setattr(assess_core, "analyze_structure", boom)
    ctx = build_run_context(repo_root=repo, run_date="2026-05-29")
    assert ctx["structure"]["available"] is False
    assert "failed" in ctx["structure"]["reason"]
    # The rest of the run is intact, including the other keyhole blocks.
    assert "behaviour" in ctx
    assert "derived_findings" in ctx
    assert "observability" in ctx


def test_stale_hubs_join_centrality_and_staleness(tmp_path: Path) -> None:
    """stale_hubs ranks central docs by pagerank x staleness ratio."""
    repo = _minimal_repo(tmp_path)
    (repo / "hub.md").write_text("hub")
    for i in range(3):
        (repo / f"leaf{i}.md").write_text("see [hub](hub.md)")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-27")
    # Each stale-hub row carries both the centrality and staleness factors,
    # plus the subject_method + confidence that surface coarse-proxy entries.
    if ctx["stale_hubs"]:
        row = ctx["stale_hubs"][0]
        assert {"path", "pagerank", "ratio", "priority",
                "subject_method", "confidence"} <= set(row)
        assert row["confidence"] in {"low", "high"}


def test_stale_hubs_confidence_low_for_repo_baseline(tmp_path: Path) -> None:
    """Hubs whose subject_method is repo-baseline must surface confidence=low.

    Without a derivable subject, the staleness ratio shares a denominator with
    every other baseline entry - the priority composite looks comparable when
    it isn't. The confidence flag lets the report discount accordingly.
    """
    repo = _minimal_repo(tmp_path)
    (repo / "hub.md").write_text("hub with no association")
    for i in range(3):
        (repo / f"leaf{i}.md").write_text("see [hub](hub.md)")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-28")
    # All docs here are floating (no co-location, no parallel docs/, no explicit
    # links), so every staleness entry falls back to repo-baseline.
    assert ctx["doc_staleness"]["available"] is True
    for d in ctx["doc_staleness"]["docs"]:
        if d["subject_method"] == "repo-baseline":
            assert d["confidence"] == "low"
    for h in ctx["stale_hubs"]:
        if h["subject_method"] == "repo-baseline":
            assert h["confidence"] == "low"


def test_stale_hubs_sort_deweights_low_confidence(tmp_path: Path) -> None:
    """A precise-subject hub at half the raw priority of a baseline hub still
    outranks it. The sort multiplies low-confidence priority by 0.5.
    """
    from assess_core import _build_stale_hubs  # type: ignore[import-not-found]

    doc_graph = {
        "available": True,
        "hubs": [
            {"path": "baseline.md", "pagerank": 1.0},
            {"path": "precise.md", "pagerank": 0.6},
        ],
    }
    doc_staleness = {
        "available": True,
        "docs": [
            {"path": "baseline.md", "last_commit_days": 100,
             "code_churn_in_window": 500, "ratio": 100.0,
             "subject_method": "repo-baseline", "confidence": "low"},
            {"path": "precise.md", "last_commit_days": 100,
             "code_churn_in_window": 20, "ratio": 80.0,
             "subject_method": "nearest-ancestor", "confidence": "high"},
        ],
    }
    hubs = _build_stale_hubs(doc_graph, doc_staleness)
    # Raw priorities: baseline = 100.0 * 1.0 = 100; precise = 80.0 * 0.6 = 48.
    # After the 0.5x low-confidence multiplier in the sort: baseline -> 50,
    # precise -> 48; baseline still wins. Test the inverse case directly.
    doc_staleness_b = dict(doc_staleness)
    doc_staleness_b["docs"] = [
        {"path": "baseline.md", "last_commit_days": 100,
         "code_churn_in_window": 200, "ratio": 80.0,
         "subject_method": "repo-baseline", "confidence": "low"},
        {"path": "precise.md", "last_commit_days": 100,
         "code_churn_in_window": 20, "ratio": 70.0,
         "subject_method": "nearest-ancestor", "confidence": "high"},
    ]
    hubs = _build_stale_hubs(doc_graph, doc_staleness_b)
    # baseline raw = 80.0 -> sorted at 40; precise raw = 42.0 -> wins.
    assert hubs[0]["path"] == "precise.md"
    # Raw priority still reflects the unweighted composite (for transparency).
    assert hubs[0]["priority"] == 42.0


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


def test_has_sibling_test_detects_colocated_and_adjacent(tmp_path: Path) -> None:
    """Co-located and adjacent-dir test files lift has_tests from unknown to
    yes/no cheaply (issue #47, observation 6)."""
    repo = tmp_path / "repo"
    (repo / "go").mkdir(parents=True)
    (repo / "go" / "foo.go").write_text("package foo")
    (repo / "go" / "foo_test.go").write_text("package foo")  # co-located
    (repo / "ts").mkdir()
    (repo / "ts" / "bar.ts").write_text("export const x = 1")
    (repo / "ts" / "bar.test.ts").write_text("test")          # co-located .test.
    (repo / "py").mkdir()
    (repo / "py" / "baz.py").write_text("x = 1")              # no test
    (repo / "svc").mkdir()
    (repo / "svc" / "api.py").write_text("x = 1")
    (repo / "svc" / "__tests__").mkdir()
    (repo / "svc" / "__tests__" / "test_api.py").write_text("t")  # adjacent dir

    assert assess_core._has_sibling_test(repo, "go/foo.go") is True
    assert assess_core._has_sibling_test(repo, "ts/bar.ts") is True
    assert assess_core._has_sibling_test(repo, "py/baz.py") is False
    assert assess_core._has_sibling_test(repo, "svc/api.py") is True
    # The file is itself a test -> counts as covered.
    assert assess_core._has_sibling_test(repo, "go/foo_test.go") is True
    # Not on disk (e.g. a since-deleted path in a stats snapshot) -> unknown.
    assert assess_core._has_sibling_test(repo, "go/gone.go") is None


def test_hotspot_page_shows_yes_when_sibling_test_exists(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "foo.go").write_text("package foo")
    (repo / "src" / "foo_test.go").write_text("package foo")
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 10, "loc": {"p50": 10, "p95": 30, "max": 50},
        "ccn": {"p50": 1, "p95": 3, "max": 5},
        "top_hotspots": [{"path": "src/foo.go", "loc": 500, "ccn": 20, "commits": 5}],
        "top_complex": [], "top_large": [],
    }))
    build_run_context(repo_root=repo, run_date="2026-05-22")
    content = next((assess_dir / "hotspots").iterdir()).read_text(encoding="utf-8")
    assert "Has test file | yes" in content


def test_commits_read_from_legacy_churn_field(tmp_path: Path) -> None:
    """A stats snapshot using the legacy `churn` key still shows real commits in
    the hotspot page, not 0 (issue #47, observation 5)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 10, "loc": {"p50": 10, "p95": 30, "max": 50},
        "ccn": {"p50": 1, "p95": 3, "max": 5},
        "top_hotspots": [{"path": "src/foo.go", "loc": 500, "ccn": 20, "churn": 33}],
        "top_complex": [], "top_large": [],
    }))
    build_run_context(repo_root=repo, run_date="2026-05-22")
    content = next((assess_dir / "hotspots").iterdir()).read_text(encoding="utf-8")
    assert "33 commits" in content
    assert "Commits in churn window | 33" in content


def test_diff_is_reliable_pure_matrix() -> None:
    """Direct unit coverage of the pure reliability decision, independent of the
    full pipeline."""
    from assess_core import _diff_is_reliable  # type: ignore[import-not-found]

    # Missing prior version stamp.
    assert _diff_is_reliable(None, "1.0.0", 1, 1) == (
        False, "version not stamped in prior snapshot"
    )
    # Unparseable version.
    ok, note = _diff_is_reliable("not-a-version", "1.0.0", 1, 1)
    assert ok is False and "unparseable" in note
    # Schema delta.
    assert _diff_is_reliable("1.0.0", "1.0.1", 1, 2) == (
        False, "schema version changed 1->2"
    )
    # Major delta.
    assert _diff_is_reliable("1.9.9", "2.0.0", 1, 1) == (
        False, "major version changed 1.9.9->2.0.0"
    )
    # Minor/patch only -> reliable.
    assert _diff_is_reliable("1.2.3", "1.5.0", 1, 1) == (True, None)
    assert _diff_is_reliable("1.2.3", "1.2.3", 1, 1) == (True, None)


def test_diff_unreliable_when_prior_snapshot_lacks_version_stamp(tmp_path: Path) -> None:
    """A prior snapshot that never stamped a plugin version can't establish
    comparability, so the diff is flagged unreliable to suppress phantom
    transitions (issue #47, observation 4). The note names the exact reason."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.prior.json").write_text(json.dumps({
        # No plugin_version: seeded by hand / written by an older plugin.
        "files_scored": 50, "loc": {}, "ccn": {},
        "top_hotspots": [{"path": "src/old.go", "loc": 500, "ccn": 20, "commits": 5}],
    }))
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "plugin_version": "1.12.0",
        "files_scored": 55, "loc": {}, "ccn": {},
        "top_hotspots": [{"path": "src/new.go", "loc": 400, "ccn": 18, "commits": 4}],
    }))
    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ctx["diff_reliable"] is False
    assert ctx["diff_version_note"] == "version not stamped in prior snapshot"
    assert ctx["diff_trend_reset"] is False


def test_diff_reliable_when_plugin_versions_match(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    stats = {
        "plugin_version": "1.12.0", "files_scored": 50, "loc": {}, "ccn": {},
        "top_hotspots": [{"path": "src/a.go", "loc": 500, "ccn": 20, "commits": 5}],
    }
    (assess_dir / "complexity-stats.prior.json").write_text(json.dumps(stats))
    (assess_dir / "complexity-stats.json").write_text(json.dumps(stats))
    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ctx["diff_reliable"] is True
    assert ctx["diff_version_note"] is None


def _seed_prior_current(
    tmp_path: Path, prior_extra: dict, current_extra: dict,
) -> Path:
    """Seed a repo with a prior and current stats sidecar sharing one hotspot,
    each merged with the caller's version/schema/tool stamps."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    base = {
        "files_scored": 50, "loc": {}, "ccn": {},
        "top_hotspots": [{"path": "src/a.go", "loc": 500, "ccn": 20, "commits": 5}],
    }
    (assess_dir / "complexity-stats.prior.json").write_text(
        json.dumps({**base, **prior_extra})
    )
    (assess_dir / "complexity-stats.json").write_text(
        json.dumps({**base, **current_extra})
    )
    return repo


def test_diff_reliable_across_patch_bump_keeps_gate_armed(tmp_path: Path) -> None:
    """A patch/minor plugin bump with an unchanged schema and toolchain keeps the
    diff reliable so the trend and regression gate stay armed."""
    stamp = {"schema_version": 1, "lizard_version": "1.23.0"}
    repo = _seed_prior_current(
        tmp_path,
        {"plugin_version": "1.54.0", **stamp},
        {"plugin_version": "1.54.1", **stamp},
    )
    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ctx["diff_reliable"] is True
    assert ctx["diff_version_note"] is None
    assert ctx["diff_trend_reset"] is False


def test_diff_unreliable_on_lizard_version_change_names_tool(tmp_path: Path) -> None:
    """A complexity-backend version change voids the diff and names the tool, so
    a score shift from the tool isn't read as a real regression."""
    repo = _seed_prior_current(
        tmp_path,
        {"plugin_version": "1.54.1", "schema_version": 1, "lizard_version": "1.22.0"},
        {"plugin_version": "1.54.1", "schema_version": 1, "lizard_version": "1.23.0"},
    )
    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ctx["diff_reliable"] is False
    assert "lizard" in ctx["diff_version_note"]
    assert "1.22.0->1.23.0" in ctx["diff_version_note"]
    assert ctx["diff_trend_reset"] is False


def test_diff_major_bump_resets_trend(tmp_path: Path) -> None:
    """A MAJOR plugin bump voids the diff and flags a trend reset, which the
    report discloses explicitly."""
    stamp = {"schema_version": 1, "lizard_version": "1.23.0"}
    repo = _seed_prior_current(
        tmp_path,
        {"plugin_version": "1.54.1", **stamp},
        {"plugin_version": "2.0.0", **stamp},
    )
    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ctx["diff_reliable"] is False
    assert ctx["diff_trend_reset"] is True
    assert "major version changed 1.54.1->2.0.0" in ctx["diff_version_note"]


def test_diff_unreliable_on_schema_change(tmp_path: Path) -> None:
    """A stats schema-version delta voids the diff: the sidecar shape the diff
    reads moved, so the comparison isn't trustworthy."""
    repo = _seed_prior_current(
        tmp_path,
        {"plugin_version": "1.54.1", "schema_version": 1, "lizard_version": "1.23.0"},
        {"plugin_version": "1.54.2", "schema_version": 2, "lizard_version": "1.23.0"},
    )
    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ctx["diff_reliable"] is False
    assert "schema version changed 1->2" in ctx["diff_version_note"]
    assert ctx["diff_trend_reset"] is False


def test_tool_versions_surface_in_run_context(tmp_path: Path) -> None:
    """The toolchain the snapshot was produced with is surfaced for the report
    and gate to reason about comparability."""
    repo = _seed_prior_current(
        tmp_path,
        {"plugin_version": "1.54.1", "schema_version": 1, "lizard_version": "1.23.0"},
        {"plugin_version": "1.54.1", "schema_version": 1, "lizard_version": "1.23.0",
         "scc_version": "3.7.0"},
    )
    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ctx["tool_versions"] == {"lizard": "1.23.0", "scc": "3.7.0"}
    assert ctx["prior_tool_versions"] == {"lizard": "1.23.0"}
    assert ctx["schema_version"] == 1


def test_first_flagged_unknown_when_prior_seeded_without_history(tmp_path: Path) -> None:
    """When prior stats are seeded but first-flagged.json isn't, a hotspot that
    predates this run must read 'unknown', not today's date
    (issue #47, observation 7)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    # Same hotspot in prior and current -> persistent, predates this run.
    (assess_dir / "complexity-stats.prior.json").write_text(json.dumps({
        "plugin_version": "1.12.0", "files_scored": 50, "loc": {}, "ccn": {},
        "top_hotspots": [{"path": "src/old.go", "loc": 500, "ccn": 20, "commits": 5}],
    }))
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "plugin_version": "1.12.0", "files_scored": 50, "loc": {}, "ccn": {},
        "top_hotspots": [{"path": "src/old.go", "loc": 510, "ccn": 21, "commits": 6}],
    }))
    # No first-flagged.json on disk.
    build_run_context(repo_root=repo, run_date="2026-05-22")
    page = next((assess_dir / "hotspots").iterdir()).read_text(encoding="utf-8")
    assert "First flagged: unknown" in page
    assert "First flagged: 2026-05-22" not in page


def test_first_flagged_stamps_today_for_genuinely_new_hotspot(tmp_path: Path) -> None:
    """A hotspot absent from the prior snapshot is genuinely new -> today."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.prior.json").write_text(json.dumps({
        "plugin_version": "1.12.0", "files_scored": 50, "loc": {}, "ccn": {},
        "top_hotspots": [{"path": "src/old.go", "loc": 500, "ccn": 20, "commits": 5}],
    }))
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "plugin_version": "1.12.0", "files_scored": 50, "loc": {}, "ccn": {},
        "top_hotspots": [{"path": "src/fresh.go", "loc": 400, "ccn": 18, "commits": 4}],
    }))
    build_run_context(repo_root=repo, run_date="2026-05-22")
    fresh_page = next(p for p in (assess_dir / "hotspots").iterdir()
                      if p.name.startswith("src-fresh-go-"))
    assert "First flagged: 2026-05-22" in fresh_page.read_text(encoding="utf-8")


def test_config_excludes_apply_to_all_scans(tmp_path: Path) -> None:
    """`.assess/config.toml` excludes are loaded once by the orchestrator
    and applied uniformly to the doc graph, doc staleness, and liveness
    scan. A `regulatory-raw/` dir vanishes from every layer's view, not
    just the treemap. This is the single load-bearing test for the
    consistent-excludes design - if it passes, the schema rename and the
    per-scan plumbing are wired correctly end-to-end."""
    repo = _minimal_repo(tmp_path)
    # Config opt-in.
    (repo / ".assess" / "config.toml").write_text(
        'exclude_dirs = ["regulatory-raw"]\n',
        encoding="utf-8",
    )
    # Two docs (one in scope, one excluded) and two code files (same).
    (repo / "README.md").write_text("see [main](./src/app.py)\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("def used(): pass\n", encoding="utf-8")
    (repo / "regulatory-raw").mkdir()
    (repo / "regulatory-raw" / "notes.md").write_text("ref data note\n", encoding="utf-8")
    (repo / "regulatory-raw" / "loader.py").write_text("x = 1\n", encoding="utf-8")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-29")

    # Doc graph: only README.md is counted.
    assert ctx["doc_graph"]["doc_count"] == 1
    # Doc staleness: only the in-scope doc + code file are counted.
    assert ctx["doc_staleness"]["association"]["doc_count"] == 1
    assert ctx["doc_staleness"]["association"]["code_file_count"] == 1
    # Liveness: any candidate paths from the dead-code scan must not
    # mention regulatory-raw (vulture etc. would either skip the dir
    # via --exclude or get post-filtered).
    for c in ctx["dead_code"].get("candidates", []):
        assert "regulatory-raw" not in c.get("path", "")


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


def test_test_pressure_passes_hotspots_as_hot_files(tmp_path: Path, monkeypatch) -> None:
    """The wiring threads the current top-hotspot paths into scan_test_pressure
    as hot_files, so an opt-in mutation run would target the files that matter."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 10, "loc": {"p50": 10, "p95": 30, "max": 50},
        "ccn": {"p50": 1, "p95": 3, "max": 5},
        "top_hotspots": [
            {"path": "src/hot.py", "loc": 500, "ccn": 20, "commits": 5},
        ],
        "top_complex": [{"path": "src/hot.py", "ccn": 20}],
        "top_large": [{"path": "src/hot.py", "loc": 500}],
    }))
    captured = {}

    def fake_scan(repo_root, hot_files=None, opt_in=False, coverage_data=None):
        captured["hot_files"] = hot_files
        captured["opt_in"] = opt_in
        return {"mutation_config_present": False, "cheap_heuristics": {}}

    monkeypatch.setattr(assess_core, "scan_test_pressure", fake_scan)
    build_run_context(repo_root=repo, run_date="2026-05-29")
    assert captured["hot_files"] == ["src/hot.py"]
    assert captured["opt_in"] is False  # read-only by default


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


def test_core_writes_fallback_badge_only_when_absent(tmp_path: Path) -> None:
    """A never-scored repo gets the deterministic findings badge; a finalized
    score badge survives deterministic-only reruns (no downgrade)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 1, "loc": {}, "ccn": {},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }), encoding="utf-8")

    build_run_context(repo_root=repo, run_date="2026-06-12")
    badge = json.loads((assess_dir / "badge.json").read_text(encoding="utf-8"))
    assert badge["label"] == "AI-readiness"
    assert "findings" in badge["message"]  # deterministic fallback form

    # Simulate a finalized score badge, then rerun the deterministic core.
    (assess_dir / "badge.json").write_text(json.dumps({
        "schemaVersion": 1, "label": "AI-readiness",
        "message": "7.0/8 · AI-Native", "color": "brightgreen",
    }), encoding="utf-8")
    build_run_context(repo_root=repo, run_date="2026-06-13")
    badge2 = json.loads((assess_dir / "badge.json").read_text(encoding="utf-8"))
    assert badge2["message"] == "7.0/8 · AI-Native"


# --------------------------------------------------------------------------
# Accretion ratchet block (files that only ever grow)
# --------------------------------------------------------------------------

def _accreting_history(repo: Path, commit, rel_path: str, *, commits: int = 5) -> None:
    """Grow ``rel_path`` monotonically across ``commits`` commits, never deleting.

    Each commit appends lines and removes none, so the file's running net-delta
    is non-decreasing and its deletion fraction is zero - the pure-accretion
    fingerprint the scanner flags (it needs >= MIN_COMMITS_FOR_ACCRETION commits).
    Commits are backdated in descending order so author time advances forward.
    """
    src = repo / rel_path
    src.parent.mkdir(parents=True, exist_ok=True)
    body = ""
    for i in range(commits):
        body += "".join(f"line_{i}_{j} = {j}\n" for j in range(10))
        src.write_text(body)
        commit(f"grow {rel_path} step {i}", days_ago=commits - i)


def test_accretion_ratchet_block_present_and_well_formed(git_repo) -> None:
    """run-context.json carries a well-formed accretion_ratchet block, and a
    monotonically-growing file that is also a complexity/size hotspot earns a row."""
    repo, commit = git_repo
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    _accreting_history(repo, commit, "src/grower.py")

    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 1, "loc": {"p50": 50, "p95": 50, "max": 50},
        "ccn": {"p50": 1, "p95": 1, "max": 1},
        "top_hotspots": [{"path": "src/grower.py", "loc": 50, "ccn": 1, "commits": 5}],
        "top_complex": [{"path": "src/grower.py", "ccn": 1}],
        "top_large": [{"path": "src/grower.py", "loc": 50}],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-06-17")

    assert "accretion_ratchet" in ctx
    block = ctx["accretion_ratchet"]
    assert set(block) >= {"available", "reliable", "deletion_fraction_threshold", "files"}
    assert block["available"] is True
    assert isinstance(block["files"], list)
    paths = [f["path"] for f in block["files"]]
    assert "src/grower.py" in paths
    flagged = next(f for f in block["files"] if f["path"] == "src/grower.py")
    assert set(flagged) == {
        "path", "net_additions", "commit_count", "deletion_fraction", "time_span_months"
    }
    assert flagged["net_additions"] > 0
    assert flagged["deletion_fraction"] == 0.0


def test_accretion_ratchet_noise_budget_filters_non_hotspots(git_repo) -> None:
    """A file that grows but is NOT in the top complexity/size band earns no row -
    growth alone doesn't cry wolf; only band-resident files are surfaced."""
    repo, commit = git_repo
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    # Two accreting files, but only one is declared a hotspot in the stats.
    _accreting_history(repo, commit, "src/in_band.py")
    _accreting_history(repo, commit, "src/out_of_band.py")

    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 2, "loc": {"p50": 50, "p95": 50, "max": 50},
        "ccn": {"p50": 1, "p95": 1, "max": 1},
        "top_hotspots": [{"path": "src/in_band.py", "loc": 50, "ccn": 1, "commits": 5}],
        "top_complex": [{"path": "src/in_band.py", "ccn": 1}],
        "top_large": [{"path": "src/in_band.py", "loc": 50}],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-06-17")
    paths = [f["path"] for f in ctx["accretion_ratchet"]["files"]]
    assert "src/in_band.py" in paths
    assert "src/out_of_band.py" not in paths  # grew, but not a hotspot -> filtered


def test_accretion_ratchet_degenerate_history_unreliable(tmp_path: Path) -> None:
    """A non-git target degrades to available:false; the block is still emitted
    with the degrade shape (a failed scan is never read as 'no accretion')."""
    repo = _minimal_repo(tmp_path)  # plain dir, not a git repo

    ctx = build_run_context(repo_root=repo, run_date="2026-06-17")
    block = ctx["accretion_ratchet"]
    assert block["available"] is False
    assert block["files"] == []
    assert block["reason"]


def test_accretion_ratchet_files_sorted_worst_first(git_repo) -> None:
    """Serialized files are a total, deterministic order: net additions desc,
    path tie-break - so the deterministic core stays byte-identical per repo."""
    repo, commit = git_repo
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    _accreting_history(repo, commit, "src/big.py", commits=8)    # more growth
    _accreting_history(repo, commit, "src/small.py", commits=4)  # less growth

    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 2, "loc": {"p50": 50, "p95": 80, "max": 80},
        "ccn": {"p50": 1, "p95": 1, "max": 1},
        "top_hotspots": [
            {"path": "src/big.py", "loc": 80, "ccn": 1, "commits": 8},
            {"path": "src/small.py", "loc": 40, "ccn": 1, "commits": 4},
        ],
        "top_complex": [{"path": "src/big.py", "ccn": 1}],
        "top_large": [{"path": "src/big.py", "loc": 80}, {"path": "src/small.py", "loc": 40}],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-06-17")
    files = ctx["accretion_ratchet"]["files"]
    nets = [f["net_additions"] for f in files]
    assert nets == sorted(nets, reverse=True)  # worst (largest growth) first
    assert files[0]["path"] == "src/big.py"


# --- structure_drift block (Tier 0 + Tier 1 orchestration) -------------------

def test_structure_drift_block_omitted_without_ownership_map(git_repo) -> None:
    """A repo with no CODEOWNERS / boundary doc emits no structure_drift block.

    Tier 0 degrades to "no ownership map", so the orchestrator omits the block
    entirely rather than emitting a half-block - the contract that absence means
    "nothing to drift against", never "no drift".
    """
    repo, commit = git_repo
    _seed_assess(repo)
    (repo / "a.py").write_text("x = 1\n")
    commit("init")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-28")
    assert "structure_drift" not in ctx


def test_structure_drift_block_present_with_codeowners(git_repo) -> None:
    """A repo with a CODEOWNERS map emits a Tier 0 block.

    Tier 1 is present-or-absent depending on whether the static import graph is
    available in the test env; either way the block carries a tier_0 with the
    documented aggregate fields and a tier_1 with an explicit availability flag.
    """
    repo, commit = git_repo
    _seed_assess(repo)
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n")
    (repo / "CODEOWNERS").write_text("src/** @team\nghost/** @nobody\n")
    commit("init")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-28")
    assert "structure_drift" in ctx
    sd = ctx["structure_drift"]
    assert sd["tier_0"]["available"] is True
    assert sd["tier_0"]["total_patterns"] == 2
    assert sd["tier_0"]["matched_patterns"] == 1
    # The stale glob is the single empty pattern.
    patterns = [e["pattern"] for e in sd["tier_0"]["empty_ownership_patterns"]]
    assert patterns == ["ghost/**"]
    # tier_1 always carries an availability flag (true or a graceful false).
    assert "available" in sd["tier_1"]


def test_structure_drift_block_builder_tier1_available() -> None:
    """_structure_drift_block surfaces the six Tier 1 counts when available.

    Pure builder test: a Tier 1 result with counts is rendered into the tier_1
    sub-block with the seam-allowlist metadata, independent of any git/grimp.
    """
    repo = Path(__file__).resolve().parents[3]  # has an ownership map (lib README)
    tier_1 = {
        "available": True,
        "human_grouped_static_splits_count": 3,
        "human_split_static_fuses_count": 0,
        "human_grouped_never_cochange_count": 4,
        "human_split_but_cochange_count": 1,
        "human_static_agree_count": 2,
        "human_cochange_agree_count": 1,
    }
    block = assess_core._structure_drift_block(repo, tier_1)
    assert block is not None
    assert block["tier_1"]["available"] is True
    assert block["tier_1"]["human_grouped_static_splits_count"] == 3
    assert block["tier_1"]["human_split_but_cochange_count"] == 1
    assert block["tier_1"]["seam_allowlist_applied"] is True
    assert block["tier_1"]["allowlist_pairs_count"] >= 1


def test_structure_drift_block_builder_tier1_unavailable() -> None:
    """When Tier 1 is unavailable, tier_1 degrades to a bare available:False.

    The static lens being out (no import graph) yields an unavailable Tier 1;
    the block still carries the cheap Tier 0 data and a half-block-free tier_1.
    """
    repo = Path(__file__).resolve().parents[3]
    block = assess_core._structure_drift_block(repo, {"available": False})
    assert block is not None
    assert block["tier_0"]["available"] is True
    assert block["tier_1"] == {"available": False}


# ── opt-in mutation affordance (run_opt_in_mutation) ──────────────────────────

def _seed_run_context(repo: Path, *, test_focus: dict) -> Path:
    """Write a minimal run-context.json carrying a test_focus block."""
    assess_dir = repo / ".assess"
    assess_dir.mkdir(parents=True, exist_ok=True)
    ctx_path = assess_dir / "run-context.json"
    ctx_path.write_text(json.dumps({
        "run_date": "2026-06-19",
        "test_focus": test_focus,
        "test_pressure": {
            "available": True,
            "mutation_run": False,
            "mutation_config_present": False,
            "cheap_heuristics": {
                "assertion_on_internal": [],
                "untested_boundaries": [],
                "duplicate_truth": [],
            },
        },
    }), encoding="utf-8")
    return ctx_path


def test_run_opt_in_mutation_rewrites_test_pressure(tmp_path: Path, monkeypatch) -> None:
    """An accepted mutation pass re-runs scan_test_pressure scoped to the
    test_focus targets with opt_in=True and rewrites the test_pressure block."""
    repo = tmp_path / "repo"
    repo.mkdir()
    ctx_path = _seed_run_context(repo, test_focus={
        "available": True,
        "coverage_present": True,
        "entries": [
            {"path": "src/a.py", "risk_band": "high", "test_signal": "no_covering_test"},
            {"path": "src/b.py", "risk_band": "medium", "test_signal": "covered_but_hollow"},
        ],
        "total_focus_targets": 2,
    })

    captured: dict = {}

    def fake_scan(repo_root, hot_files=None, opt_in=False, coverage_data=None):
        captured["hot_files"] = hot_files
        captured["opt_in"] = opt_in
        return {
            "available": True,
            "mutation_run": True,
            "mutation_config_present": False,
            "mutation_scope": hot_files,
            "per_file": [{"file": "src/a.py", "survived": 3, "total": 10}],
            "cheap_heuristics": {
                "assertion_on_internal": [],
                "untested_boundaries": [],
                "duplicate_truth": [],
            },
        }

    monkeypatch.setattr(assess_core, "scan_test_pressure", fake_scan)
    rc = assess_core.run_opt_in_mutation(repo)
    assert rc == 0
    # Scoped to the focus targets, with the bounded pass enabled.
    assert captured["opt_in"] is True
    assert captured["hot_files"] == ["src/a.py", "src/b.py"]
    # test_pressure block rewritten in place with the mutation results.
    ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    assert ctx["test_pressure"]["mutation_run"] is True
    assert ctx["test_pressure"]["per_file"] == [
        {"file": "src/a.py", "survived": 3, "total": 10}
    ]


def test_run_opt_in_mutation_no_focus_targets(tmp_path: Path, monkeypatch) -> None:
    """Empty test_focus.entries means nothing to mutate - the scan never runs
    and the context is left untouched."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_run_context(repo, test_focus={
        "available": True, "coverage_present": True, "entries": [],
        "total_focus_targets": 0,
    })

    called = {"ran": False}

    def fake_scan(*a, **k):
        called["ran"] = True
        return {}

    monkeypatch.setattr(assess_core, "scan_test_pressure", fake_scan)
    rc = assess_core.run_opt_in_mutation(repo)
    assert rc == 0
    assert called["ran"] is False


def test_run_opt_in_mutation_missing_context(tmp_path: Path) -> None:
    """No prior run-context.json -> non-zero return, no crash."""
    repo = tmp_path / "repo"
    repo.mkdir()
    rc = assess_core.run_opt_in_mutation(repo)
    assert rc == 1


def test_run_opt_in_mutation_degrades_on_scan_failure(tmp_path: Path, monkeypatch) -> None:
    """A raising scan degrades to the unavailable shape rather than crashing,
    preserving the cheap_heuristics schema so the consumer's shape is stable."""
    repo = tmp_path / "repo"
    repo.mkdir()
    ctx_path = _seed_run_context(repo, test_focus={
        "available": True, "coverage_present": True,
        "entries": [{"path": "src/a.py", "risk_band": "high",
                     "test_signal": "no_covering_test"}],
        "total_focus_targets": 1,
    })

    def boom(*a, **k):
        raise RuntimeError("mutation exploded")

    monkeypatch.setattr(assess_core, "scan_test_pressure", boom)
    rc = assess_core.run_opt_in_mutation(repo)
    assert rc == 0
    ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    tp = ctx["test_pressure"]
    assert tp["available"] is False
    assert tp["mutation_config_present"] is None
    assert tp["cheap_heuristics"] == {
        "assertion_on_internal": [],
        "untested_boundaries": [],
        "duplicate_truth": [],
    }
