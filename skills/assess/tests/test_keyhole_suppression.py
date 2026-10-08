"""Keyhole-signal unit tests for one finding family.

Covers what drops or discloses finding paths: degenerate churn, config
excludes, archive paths, and pruning paths that renamed or deleted history
no longer has.

The pure, git-free derivation core; end-to-end wiring (build_run_context)
is covered in test_assess_core.py. Split from the former
test_keyhole_signals.py by finding family; shared integrate() fixtures live
in keyhole_helpers.py.
"""
from __future__ import annotations

from pathlib import Path

from keyhole_helpers import (
    _BLEEDING_COMMIT_SETS,
    _COMPLEXITY_STATS,
    _finding_paths,
    _integrate,
    _MODULAR_STRUCTURE,
    _stale_doc_staleness,
)
from lib import keyhole_signals as ks


# --- Issue #172: degenerate churn drops churn-derived findings ----------------


def test_real_churn_produces_churn_derived_findings() -> None:
    """Baseline: with real churn (not degenerate), the lying_map and
    hidden_coupling findings fire and are counted in the keyhole summary."""
    result = _integrate(churn_degenerate=False)
    assert _finding_paths(result, "lying_map") == ["pkg/README.md"]
    assert _finding_paths(result, "hidden_coupling") == ["looksmodular"]
    counted = {c["name"] for c in result["keyhole_summary"]["concerns"]}
    assert {"lying_map", "hidden_coupling"} <= counted


def test_degenerate_churn_drops_churn_derived_findings_from_summary() -> None:
    """Issue #172: on a degenerate churn history the same inputs yield zero
    churn-derived findings - lying_map (confidence capped in the join) and
    hidden_coupling (dropped here) are absent from derived_findings AND the
    keyhole_summary, so a reader sees 0 lying maps from a meaningless signal."""
    result = _integrate(churn_degenerate=True)
    assert _finding_paths(result, "lying_map") == []
    assert _finding_paths(result, "hidden_coupling") == []
    counted = {c["name"] for c in result["keyhole_summary"]["concerns"]}
    assert "lying_map" not in counted
    assert "hidden_coupling" not in counted


# --- config-exclusion disclosure (apply_config_excludes) ---------------------

def test_apply_config_excludes_filters_and_counts() -> None:
    """Excluded finding paths are dropped from findings and returned separately."""
    findings = [
        {"name": "hidden_coupling", "paths": ["vendor/x", "src/a"], "action": "z"},
        {"name": "refactor_boundary", "paths": ["vendor/y"], "action": "z"},
    ]
    filtered, dropped = ks.apply_config_excludes(findings, {"vendor"}, [])
    kept = [p for f in filtered for p in f["paths"]]
    assert kept == ["src/a"]
    assert dropped == ["vendor/x", "vendor/y"]


def test_apply_config_excludes_pattern_match() -> None:
    """A basename glob pattern suppresses matching finding paths."""
    findings = [{"name": "lying_map", "paths": ["docs/gen.md", "docs/hand.md"],
                 "action": "z"}]
    filtered, dropped = ks.apply_config_excludes(findings, set(), ["gen.md"])
    assert filtered[0]["paths"] == ["docs/hand.md"]
    assert dropped == ["docs/gen.md"]


def test_apply_config_excludes_noop_without_config() -> None:
    """No excludes -> findings untouched, nothing dropped."""
    findings = [{"name": "hidden_coupling", "paths": ["a"], "action": "z"}]
    filtered, dropped = ks.apply_config_excludes(findings, set(), [])
    assert filtered == findings
    assert dropped == []


# A self-contained directory: repeatedly touched alone, so it reads as a
# refactor_boundary (the git-log containment view, which scan-level excludes
# never filter).
_ISLAND_COMMIT_SETS = [
    {Path("island/a.py")},
    {Path("island/b.py")},
    {Path("island/a.py")},
    {Path("island/c.py")},
    {Path("island/b.py")},
]


def test_integrate_excludes_suppress_finding_and_report_paths() -> None:
    """A config-excluded finding path is filtered from findings but disclosed.

    The refactor_boundary comes from the git-log containment view, which the
    scan-level exclude never touches - so integrate() is where it is filtered.
    """
    result = ks.integrate(
        repo_root=Path("/nonexistent"),
        complexity_stats=_COMPLEXITY_STATS,
        doc_staleness=_stale_doc_staleness(churn_degenerate=False),
        dead_code={"available": False, "candidate_count": 0,
                   "candidates": [], "tools": []},
        observability={"rung": None, "reachable": {"present": False}},
        structure=None,
        commit_sets=_ISLAND_COMMIT_SETS,
        exclude_dirs={"island"},
    )
    # island/ was a refactor_boundary; the exclude filters it out of findings.
    assert "island" not in _finding_paths(result, "refactor_boundary")
    # ...but it is disclosed as a suppressed finding path.
    assert "island" in result["excluded_finding_paths"]


def test_integrate_no_excludes_reports_empty_suppression() -> None:
    """Without excludes, excluded_finding_paths is empty."""
    result = ks.integrate(
        repo_root=Path("/nonexistent"),
        complexity_stats=_COMPLEXITY_STATS,
        doc_staleness=_stale_doc_staleness(churn_degenerate=False),
        dead_code={"available": False, "candidate_count": 0,
                   "candidates": [], "tools": []},
        observability={"rung": None, "reachable": {"present": False}},
        structure=None,
        commit_sets=_ISLAND_COMMIT_SETS,
    )
    assert result["excluded_finding_paths"] == []
    assert "island" in _finding_paths(result, "refactor_boundary")


# --- archive exclusion from attention ------------------------------------------

def test_archive_paths_excluded_from_attention_and_disclosed() -> None:
    """A path with an archive/archived/attic component never ranks in attention.

    The archived plan scores 2 (two negative findings) and would lead the list;
    it is excluded from attention and prescribed actions, and returned for the
    disclosure. A file whose name merely contains "archive" is not excluded.
    """
    findings = ks.assemble_findings({
        "accretion_ratchet": ["docs/archive/PLAN.md", "src/app.py"],
        "unactioned_intent": ["docs/archive/PLAN.md", "old/Attic/x.py"],
        "lying_map": ["legacy/archived/notes.py", "src/archive_writer.py"],
        "refactor_boundary": ["archive"],
    })
    attention, archived = ks.exclude_archive_from_attention(findings)
    ranked = [u["path"] for u in attention]
    assert ranked == ["src/app.py", "src/archive_writer.py"]
    assert archived == [
        "docs/archive/PLAN.md", "legacy/archived/notes.py", "old/Attic/x.py",
    ]
    prescribed = ks.build_prescribed_actions(attention, findings)
    assert [p["path"] for p in prescribed] == ["src/app.py", "src/archive_writer.py"]
    # The findings themselves still name the archived path (a true observation).
    acc = next(f for f in findings if f["name"] == "accretion_ratchet")
    assert "docs/archive/PLAN.md" in acc["paths"]


def test_archive_paths_excluded_noop_without_archive() -> None:
    """No archive path -> attention equals build_attention_list, nothing disclosed."""
    findings = ks.assemble_findings({"accretion_ratchet": ["src/a.py"]})
    attention, archived = ks.exclude_archive_from_attention(findings)
    assert attention == ks.build_attention_list(findings)
    assert archived == []


# --- prune_missing_finding_paths (renamed / deleted history) -----------------

def test_pruned_finding_paths_only_git_history_findings(tmp_path: Path) -> None:
    """A git-history finding path absent from disk is dropped and returned
    sorted; paths that exist, and findings read from the working tree, are
    untouched."""
    (tmp_path / "live").mkdir()
    findings = ks.assemble_findings({
        "hidden_coupling": ["live", "gone", "also_gone"],
        "refactor_boundary": ["gone_island"],
        "unactioned_intent": ["not/on/disk.py"],
    })
    pruned, dropped = ks.prune_missing_finding_paths(findings, tmp_path)
    by_name = {f["name"]: f["paths"] for f in pruned}
    assert by_name["hidden_coupling"] == ["live"]
    assert by_name["refactor_boundary"] == []
    assert by_name["unactioned_intent"] == ["not/on/disk.py"]
    assert dropped == ["also_gone", "gone", "gone_island"]
    assert [f["name"] for f in pruned] == [f["name"] for f in findings]


def test_pruned_finding_paths_stand_down_when_rename_map_incomplete(tmp_path: Path) -> None:
    """With git history read, a hidden_coupling dir absent from disk is pruned.
    When the rename map could not be built (git failed), a missing path may be
    an unfolded old name rather than a deletion, so nothing is pruned."""
    import subprocess

    from lib.change_coupling import RenameMap

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    def run(complete: bool) -> dict:
        return ks.integrate(
            repo_root=tmp_path,
            complexity_stats=_COMPLEXITY_STATS,
            doc_staleness=_stale_doc_staleness(churn_degenerate=False),
            dead_code={"available": False, "candidate_count": 0,
                       "candidates": [], "tools": []},
            observability={"rung": None, "reachable": {"present": False}},
            structure=_MODULAR_STRUCTURE,
            commit_sets=_BLEEDING_COMMIT_SETS,
            rename_map=RenameMap({}, complete=complete),
        )

    stood_down = run(complete=False)
    assert _finding_paths(stood_down, "hidden_coupling")
    assert stood_down["pruned_finding_paths"] == []
    assert stood_down["rename_map_complete"] is False
    pruned = run(complete=True)
    assert _finding_paths(pruned, "hidden_coupling") == []
    assert pruned["pruned_finding_paths"] == sorted(
        _finding_paths(stood_down, "hidden_coupling"))


def test_rename_map_incomplete_when_ancestry_check_fails(tmp_path: Path, monkeypatch) -> None:
    """A chain hop needs `git merge-base --is-ancestor`. Exit 128 (an object git
    cannot resolve, as at a shallow boundary) is a failure, not "unrelated": the
    map comes back empty and incomplete, and the prune stands down, so a live
    finding is never reported as deleted."""
    import subprocess

    import lib.change_coupling as cc

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@example.com",
                        "-c", "user.name=T", *args], check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "a.py").write_text("a = 1\n" * 5)
    git("add", "-A")
    git("commit", "-q", "-m", "a")
    git("mv", "a.py", "b.py")
    git("commit", "-q", "-m", "a -> b")
    git("mv", "b.py", "c.py")
    git("commit", "-q", "-m", "b -> c")

    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if "--is-ancestor" in cmd:
            return subprocess.CompletedProcess(cmd, 128, b"", b"fatal: bad object")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(cc.subprocess, "run", fake_run)
    rename_map = cc.build_rename_map(tmp_path)
    assert rename_map == cc.RenameMap({}, complete=False)

    result = ks.integrate(
        repo_root=tmp_path,
        complexity_stats=_COMPLEXITY_STATS,
        doc_staleness=_stale_doc_staleness(churn_degenerate=False),
        dead_code={"available": False, "candidate_count": 0,
                   "candidates": [], "tools": []},
        observability={"rung": None, "reachable": {"present": False}},
        structure=_MODULAR_STRUCTURE,
        commit_sets=_BLEEDING_COMMIT_SETS,
        rename_map=rename_map,
    )
    assert _finding_paths(result, "hidden_coupling")
    assert result["pruned_finding_paths"] == []
    assert result["rename_map_complete"] is False
