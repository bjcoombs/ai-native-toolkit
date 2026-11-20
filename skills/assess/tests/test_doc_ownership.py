"""Tests for the one doc-to-code ownership rule (issues #424, #555).

Unit tests drive ``resolve_subjects`` from hand-built specs; the integration
tests run the real ``analyze_doc_staleness`` on a git repo and feed its block to
``analyze_doc_complexity_join``, proving both blocks describe the same code.
"""
from __future__ import annotations

from pathlib import Path

from lib.doc_complexity_join import analyze_doc_complexity_join, doc_subjects
from lib.doc_ownership import (
    SUBJECT_PATHS_CAP,
    SubjectSpec,
    bounded_subject_paths,
    rel_posix,
    resolve_subjects,
)
from lib.doc_staleness import analyze_doc_staleness


def _na(root: str) -> SubjectSpec:
    return {"method": "nearest-ancestor", "roots": [root], "base_dir": root}


def test_nearest_ancestor_owns_down_to_next_base_doc() -> None:
    specs = {"README.md": _na(""), "pkg/README.md": _na("pkg")}
    subjects = resolve_subjects(
        ["top.py", "pkg/a.py", "pkg/sub/b.py", "other/c.py"], specs,
    )
    assert subjects == {
        "README.md": ["other/c.py", "top.py"],
        "pkg/README.md": ["pkg/a.py", "pkg/sub/b.py"],
    }


def test_parallel_explicit_and_baseline_specs() -> None:
    specs: dict[str, SubjectSpec] = {
        "README.md": _na(""),
        "docs/billing.md": {"method": "parallel-docs-tree", "roots": ["billing"],
                            "base_dir": None},
        "notes.md": {"method": "explicit-links", "roots": ["auth/login.py", "gone.py"],
                     "base_dir": None},
        "floating.md": {"method": "repo-baseline", "roots": [], "base_dir": None},
        "old.md": {"method": "", "roots": [], "base_dir": None},
    }
    code = ["billing/x.py", "billing/deep/y.py", "billingx/z.py", "auth/login.py"]
    subjects = resolve_subjects(code, specs)
    # A parallel doc and an explicit link describe code a base doc also owns.
    assert subjects["docs/billing.md"] == ["billing/deep/y.py", "billing/x.py"]
    assert subjects["notes.md"] == ["auth/login.py"]
    assert subjects["README.md"] == sorted(code)
    assert subjects["floating.md"] == []
    assert subjects["old.md"] == []


def test_base_doc_without_discovered_code_still_owns_scored_files() -> None:
    """A base doc that owned no discovered code (a fallback method) is still
    the nearest ancestor of a scored file the discovery missed (a language it
    does not list), so that file is never handed to a doc further up."""
    specs: dict[str, SubjectSpec] = {
        "README.md": _na(""),
        "lua/README.md": {"method": "repo-baseline", "roots": [], "base_dir": "lua"},
    }
    subjects = resolve_subjects(["lua/x.lua", "top.py"], specs)
    assert subjects == {"README.md": ["top.py"], "lua/README.md": ["lua/x.lua"]}


def test_bounded_subject_paths_and_rel_posix(tmp_path: Path) -> None:
    many = [f"m/f{i:03d}.py" for i in range(SUBJECT_PATHS_CAP + 5)]
    assert bounded_subject_paths(list(reversed(many))) == many[:SUBJECT_PATHS_CAP]
    assert rel_posix(tmp_path, tmp_path) == ""
    assert rel_posix(tmp_path / "a" / "b.py", tmp_path) == "a/b.py"
    outside = tmp_path.parent / "elsewhere.py"
    assert rel_posix(outside, tmp_path) == outside.as_posix()


def test_join_reads_subjects_through_row_specs() -> None:
    docs = [
        {"path": "README.md", "subject_method": "nearest-ancestor", "subject_roots": [""]},
        {"path": "pkg/README.md", "subject_method": "nearest-ancestor",
         "subject_roots": ["pkg"]},
        # An older run-context row with no roots: an empty subject, no guess.
        {"path": "pkg/design.md", "subject_method": "nearest-ancestor"},
    ]
    assert doc_subjects(["pkg/a.py", "b.py"], docs) == {
        "README.md": ["b.py"], "pkg/README.md": ["pkg/a.py"], "pkg/design.md": [],
    }


def _stale_pair_repo(repo: Path, commit) -> None:
    """`pkg/` holds README.md and design.md beside one churning complex file;
    both docs froze before the churn."""
    (repo / "pkg").mkdir()
    (repo / "pkg" / "README.md").write_text("module map", encoding="utf-8")
    (repo / "pkg" / "design.md").write_text("design notes", encoding="utf-8")
    (repo / "pkg" / "engine.py").write_text("v = 0", encoding="utf-8")
    commit("initial", days_ago=200)
    for i in range(8):
        (repo / "pkg" / "engine.py").write_text(f"v = {i + 1}", encoding="utf-8")
        commit(f"change {i}", days_ago=100 - i * 5)


def test_readme_beside_design_doc_owns_the_code_in_both_blocks(git_repo) -> None:
    """The issue's tie-break fixture: the old join gave the directory to
    `design.md` (ASCII order); the shared rule gives it to `README.md` in the
    staleness block and the complexity join alike."""
    repo, commit = git_repo
    _stale_pair_repo(repo, commit)
    staleness = analyze_doc_staleness(repo, generated_sources=[])
    rows = {d["path"]: d for d in staleness["docs"]}
    assert rows["pkg/README.md"]["subject_paths"] == ["pkg/engine.py"]
    assert rows["pkg/README.md"]["subject_roots"] == ["pkg"]
    assert rows["pkg/README.md"]["base_doc_dir"] == "pkg"
    assert rows["pkg/design.md"]["subject_paths"] == []
    assert rows["pkg/design.md"]["base_doc_dir"] is None

    stats = {"ccn": {"p95": 10.0}, "top_complex": [{"path": "pkg/engine.py", "ccn": 30.0}]}
    joined = analyze_doc_complexity_join(dict(stats), dict(staleness), repo)
    units = {u["path"]: u for u in joined["docs"]}
    assert units["pkg/README.md"]["complexity_summarised"] == 30.0
    assert units["pkg/README.md"]["finding"] == "lying_map"
    assert units["pkg/design.md"]["complexity_summarised"] == 0.0
    assert [u["path"] for u in joined["findings"]["lying_maps"]] == ["pkg/README.md"]
    # One subject_code_count per document across both blocks.
    for path, row in rows.items():
        assert units[path]["subject_code_count"] == row["subject_code_count"]


def test_join_sees_owned_file_past_the_export_cap(git_repo) -> None:
    """The row lists only SUBJECT_PATHS_CAP paths, but the join expands the
    rule's roots, so a complex file sorting after the cap still counts as
    owned (never a false unexplained_complexity)."""
    repo, commit = git_repo
    (repo / "README.md").write_text("map", encoding="utf-8")
    for i in range(SUBJECT_PATHS_CAP + 3):
        (repo / f"a{i:03d}.py").write_text("x = 1", encoding="utf-8")
    (repo / "zz_last.py").write_text("x = 1", encoding="utf-8")
    commit("initial", days_ago=10)
    staleness = analyze_doc_staleness(repo, generated_sources=[])
    row = next(d for d in staleness["docs"] if d["path"] == "README.md")
    assert row["subject_code_count"] == SUBJECT_PATHS_CAP + 4
    assert len(row["subject_paths"]) == SUBJECT_PATHS_CAP
    assert "zz_last.py" not in row["subject_paths"]

    stats = {"ccn": {"p95": 10.0}, "top_complex": [{"path": "zz_last.py", "ccn": 40.0}]}
    joined = analyze_doc_complexity_join(dict(stats), dict(staleness), repo)
    assert joined["findings"]["unexplained_complexity"] == []
    readme = next(u for u in joined["docs"] if u["path"] == "README.md")
    assert readme["complexity_summarised"] == 40.0
    assert readme["subject_code_count"] == row["subject_code_count"]


def test_scoped_run_keys_match_stats_keys(git_repo) -> None:
    """Under `/assess <path>` the stats keys stay repo-relative, and so do the
    staleness subject keys, so the join matches them."""
    repo, commit = git_repo
    (repo / "svc" / "core").mkdir(parents=True)
    (repo / "svc" / "README.md").write_text("svc", encoding="utf-8")
    (repo / "svc" / "core" / "x.py").write_text("x = 1", encoding="utf-8")
    (repo / "other.py").write_text("y = 1", encoding="utf-8")
    commit("initial", days_ago=10)
    staleness = analyze_doc_staleness(repo, generated_sources=[], scope=repo / "svc")
    row = next(d for d in staleness["docs"] if d["path"] == "svc/README.md")
    assert row["subject_paths"] == ["svc/core/x.py"]
    stats = {"ccn": {"p95": 10.0}, "top_complex": [{"path": "svc/core/x.py", "ccn": 20.0}]}
    joined = analyze_doc_complexity_join(dict(stats), dict(staleness), repo)
    unit = next(u for u in joined["docs"] if u["path"] == "svc/README.md")
    assert unit["complexity_summarised"] == 20.0
