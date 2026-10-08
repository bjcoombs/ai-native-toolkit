"""Tests for lib/context_blocks.py: scan results serialised into run-context
blocks (keyhole, stale hubs, liveness, coverage report, accretion, structure
drift, exclusion and pruning disclosures).

Moved out of test_assess_core.py when assess_core's helpers split into
lib/ modules; shared repo-seeding helpers live in assess_core_helpers.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import assess_core
from assess_core import build_run_context
from assess_core_helpers import (
    _minimal_repo,
    _renamed_and_deleted_history,
    _seed_assess,
    _write_min_stats,
)
from lib import context_blocks


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
                "orphaned_understanding", "candidate_dead_weight",
                "override_contradicts_signals", "refactor_boundary"}
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
    from lib.context_blocks import build_stale_hubs

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
    hubs = build_stale_hubs(doc_graph, doc_staleness)
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
    hubs = build_stale_hubs(doc_graph, doc_staleness_b)
    # baseline raw = 80.0 -> sorted at 40; precise raw = 42.0 -> wins.
    assert hubs[0]["path"] == "precise.md"
    # Raw priority still reflects the unweighted composite (for transparency).
    assert hubs[0]["priority"] == 42.0


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


def test_failed_liveness_fallback_blocks_exact_shape(tmp_path: Path, monkeypatch) -> None:
    """Pins the full degrade shape of dead_code / observability when the liveness
    scan fails, the reason carried through from _safe, and that no capability
    keys appear - so moving the fallback into a helper cannot drift a field."""
    repo = _minimal_repo(tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("liveness blew up")

    monkeypatch.setattr(assess_core, "scan_liveness", boom)
    ctx = build_run_context(repo_root=repo, run_date="2026-05-27")
    reason = ctx["observability"]["reason"]
    assert reason and reason == ctx["dead_code"]["reason"]
    assert ctx["dead_code"] == {
        "available": False, "candidate_count": 0, "candidates": [],
        "tools": [], "reason": reason,
    }
    assert ctx["observability"] == {
        "rung": None, "available": False, "reason": reason,
        "instrumented": {"present": False, "signals": []},
        "discoverable": {"present": False, "signals": []},
        "reachable": {"present": False, "signals": []},
    }
    assert "capability_offers" not in ctx
    assert "language_capabilities" not in ctx


def test_coverage_report_block_absent_shape(tmp_path: Path) -> None:
    """With no coverage report on disk the block is the bare not-found shape."""
    repo = _minimal_repo(tmp_path)
    ctx = build_run_context(repo_root=repo, run_date="2026-05-27")
    assert ctx["coverage_report"] == {"available": False, "source": "none found"}


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


def test_accretion_skips_documentation_and_archive_paths_excluded_end_to_end(git_repo) -> None:
    """End to end: an append-only markdown plan in the top size band earns no
    accretion row, and an accreting file under archive/ stays out of attention
    and prescribed_actions, disclosed in excluded_as_archive rather than dropped
    silently. Append-only code outside archive/ still leads."""
    repo, commit = git_repo
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    _accreting_history(repo, commit, "notes/PLAN.md")
    _accreting_history(repo, commit, "src/big.py")
    _accreting_history(repo, commit, "tools/archive/legacy.py")

    rows = [
        {"path": "notes/PLAN.md", "loc": 3000, "ccn": 0.0, "max_fn_ccn": None,
         "commits": 5, "source": "scc"},
        {"path": "src/big.py", "loc": 50, "ccn": 5.0, "commits": 5, "source": "lizard"},
        {"path": "tools/archive/legacy.py", "loc": 50, "ccn": 5.0, "commits": 5,
         "source": "lizard"},
    ]
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 3, "loc": {"total": 3100}, "ccn": {"max": 5},
        "top_hotspots": rows, "top_complex": rows, "top_large": rows,
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-09-18")
    acc_paths = [f["path"] for f in ctx["accretion_ratchet"]["files"]]
    assert "notes/PLAN.md" not in acc_paths
    assert "src/big.py" in acc_paths
    attention = [u["path"] for u in ctx["attention"]]
    prescribed = [p["path"] for p in ctx["prescribed_actions"]]
    for ranked in (attention, prescribed):
        assert "src/big.py" in ranked
        assert "notes/PLAN.md" not in ranked
        assert "tools/archive/legacy.py" not in ranked
    block = ctx["excluded_as_archive"]
    assert "tools/archive/legacy.py" in block["affected_finding_paths"]
    assert block["affected_finding_paths"] == sorted(block["affected_finding_paths"])
    assert block["count"] == len(block["affected_finding_paths"])


def test_archive_paths_excluded_block_empty_without_archive(git_repo) -> None:
    """A repo with no archive path carries an empty excluded_as_archive block."""
    repo, commit = git_repo
    (repo / ".assess").mkdir()
    _write_min_stats(repo / ".assess")
    (repo / "README.md").write_text("# Repo\n")
    commit("init")

    ctx = build_run_context(repo_root=repo, run_date="2026-09-18")
    assert ctx["excluded_as_archive"] == {"affected_finding_paths": [], "count": 0}


def test_pruned_finding_paths_block_after_rename_and_delete(git_repo) -> None:
    """Co-change history folds onto new/, gone/ is pruned from every finding
    surface and disclosed in pruned_finding_paths, and no surface names a path
    that is missing on disk."""
    repo, commit = git_repo
    _renamed_and_deleted_history(repo, commit)

    ctx = build_run_context(repo_root=repo, run_date="2026-09-18")
    hidden = next(f["paths"] for f in ctx["derived_findings"]
                  if f["name"] == "hidden_coupling")
    assert "new" in hidden
    assert not {"old", "gone"} & set(hidden)
    named = {p for f in ctx["derived_findings"] for p in f["paths"]}
    named |= {u["path"] for u in ctx["attention"]}
    named |= {a["path"] for a in ctx["prescribed_actions"]}
    assert named and all((repo / p).exists() for p in named)
    items = [line[2:].split(" (")[0] for line in ctx["findings_markdown"].splitlines()
             if line.startswith("- ")]
    assert "new" in items
    assert not [i for i in items if i.split("/")[0] in ("old", "gone")]
    pair = [p["co_change_count"] for p in ctx["behaviour"]["change_coupling_pairs"]
            if (p["file_a"], p["file_b"]) == ("new/x.py", "new/y.py")]
    assert pair and pair[0] >= 6
    assert ctx["pruned_finding_paths"] == {
        "paths": ["gone"], "count": 1, "rename_map_complete": True}


def test_pruned_finding_paths_block_empty_without_dead_paths(git_repo) -> None:
    """A repo whose history names only live paths carries an empty block."""
    repo, commit = git_repo
    (repo / ".assess").mkdir()
    _write_min_stats(repo / ".assess")
    (repo / "README.md").write_text("# Repo\n")
    commit("init")

    ctx = build_run_context(repo_root=repo, run_date="2026-09-18")
    assert ctx["pruned_finding_paths"] == {
        "paths": [], "count": 0, "rename_map_complete": True}


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
    block = context_blocks._structure_drift_block(repo, tier_1)
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
    block = context_blocks._structure_drift_block(repo, {"available": False})
    assert block is not None
    assert block["tier_0"]["available"] is True
    assert block["tier_1"] == {"available": False}


def test_excluded_by_config_block_present_and_empty_without_excludes(git_repo) -> None:
    """A repo with no config excludes carries an empty excluded_by_config block."""
    repo, commit = git_repo
    (repo / ".assess").mkdir()
    _write_min_stats(repo / ".assess")
    (repo / "README.md").write_text("# Repo\n")
    commit("init")

    ctx = build_run_context(repo_root=repo, run_date="2026-07-07")
    block = ctx["excluded_by_config"]
    assert block["dirs"] == []
    assert block["patterns"] == []
    assert block["affected_finding_paths"] == []
    assert block["count"] == 0


def test_excluded_by_config_discloses_suppressed_finding(git_repo) -> None:
    """A config-excluded directory that would be a refactor_boundary is filtered
    from the findings but named + counted in excluded_by_config.

    The island/ directory is repeatedly touched alone (a self-contained
    refactor_boundary from the git-log containment view, which scan-level
    excludes never filter), so excluding it exercises the keyhole-level filter.
    """
    repo, commit = git_repo
    (repo / ".assess").mkdir()
    _write_min_stats(repo / ".assess")
    (repo / ".assess" / "config.toml").write_text('exclude_dirs = ["island"]\n')
    island = repo / "island"
    island.mkdir()
    (island / "a.py").write_text("x = 1\n")
    commit("c1")
    for i in range(2, 7):
        (island / "a.py").write_text(f"x = {i}\n")
        commit(f"c{i}")

    ctx = build_run_context(repo_root=repo, run_date="2026-07-07")
    block = ctx["excluded_by_config"]
    assert block["dirs"] == ["island"]
    assert block["count"] == len(block["affected_finding_paths"])
    assert block["count"] >= 1
    assert "island" in block["affected_finding_paths"]
    # The suppressed path is gone from the findings themselves.
    rb = next(f for f in ctx["derived_findings"] if f["name"] == "refactor_boundary")
    assert "island" not in rb["paths"]


# ════════════════════════════════════════════════════════════════════════════
# Generated-file disclosure (excluded_generated pass-through)
# ════════════════════════════════════════════════════════════════════════════

def test_excluded_generated_header_list_copied_from_stats(git_repo) -> None:
    """The treemap's excluded_generated list reaches run-context.json unchanged,
    with malformed rows dropped."""
    repo, commit = git_repo
    assess = repo / ".assess"
    assess.mkdir()
    rows = [{"path": "db/schema.sql", "reason": "generated-header"}]
    (assess / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 0, "loc": {}, "ccn": {},
        "top_hotspots": [], "top_complex": [], "top_large": [],
        "excluded_generated": rows + [{"path": "x"}, "junk"],
    }))
    (repo / "README.md").write_text("# Repo\n")
    commit("init")

    ctx = build_run_context(repo_root=repo, run_date="2026-09-18")
    assert ctx["excluded_generated"] == rows


def test_excluded_generated_header_list_empty_for_old_stats(git_repo) -> None:
    repo, commit = git_repo
    (repo / ".assess").mkdir()
    _write_min_stats(repo / ".assess")
    (repo / "README.md").write_text("# Repo\n")
    commit("init")
    ctx = build_run_context(repo_root=repo, run_date="2026-09-18")
    assert ctx["excluded_generated"] == []
