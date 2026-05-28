"""Tests for the doc-staleness metric and doc->code association."""
from __future__ import annotations

from pathlib import Path

from lib.doc_staleness import LARGE_REPO_CODE_FILES, analyze_doc_staleness


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_nearest_ancestor_respects_inner_base_doc(tmp_path: Path) -> None:
    """A base doc owns its subtree down to the next base doc, not past it."""
    _write(tmp_path, "src/payments/README.md", "payments")
    _write(tmp_path, "src/payments/pay.py", "x")
    _write(tmp_path, "src/payments/refund.py", "y")
    _write(tmp_path, "src/payments/ledger/ledger.md", "ledger")  # <dir>.md convention
    _write(tmp_path, "src/payments/ledger/l.py", "z")

    r = analyze_doc_staleness(tmp_path)
    by_path = {d["path"]: d for d in r["docs"]}
    # README owns pay.py + refund.py (2), but NOT ledger/l.py (claimed by ledger.md)
    assert by_path["src/payments/README.md"]["subject_code_count"] == 2
    assert by_path["src/payments/README.md"]["subject_method"] == "nearest-ancestor"
    assert by_path["src/payments/ledger/ledger.md"]["subject_code_count"] == 1


def test_parallel_docs_tree_fallback(tmp_path: Path) -> None:
    _write(tmp_path, "docs/auth.md", "auth docs")
    _write(tmp_path, "src/auth/a.py", "x")
    r = analyze_doc_staleness(tmp_path)
    auth = next(d for d in r["docs"] if d["path"] == "docs/auth.md")
    assert auth["subject_method"] == "parallel-docs-tree"
    assert auth["subject_code_count"] == 1


def test_explicit_links_fallback(tmp_path: Path) -> None:
    _write(tmp_path, "notes.md", "see [code](src/x.py)")
    _write(tmp_path, "src/x.py", "x")
    r = analyze_doc_staleness(
        tmp_path, doc_to_code_edges=[{"doc": "notes.md", "code": "src/x.py"}]
    )
    notes = next(d for d in r["docs"] if d["path"] == "notes.md")
    assert notes["subject_method"] == "explicit-links"


def test_repo_baseline_when_no_association(tmp_path: Path) -> None:
    _write(tmp_path, "floating.md", "no links, no co-location")
    _write(tmp_path, "src/x.py", "x")
    r = analyze_doc_staleness(tmp_path)
    floating = next(d for d in r["docs"] if d["path"] == "floating.md")
    assert floating["subject_method"] == "repo-baseline"


def test_boilerplate_is_not_a_base_doc(tmp_path: Path) -> None:
    _write(tmp_path, "src/LICENSE.md", "MIT")
    _write(tmp_path, "src/x.py", "x")
    r = analyze_doc_staleness(tmp_path)
    # LICENSE must not claim ownership of src/x.py as a base doc
    assert r["association"]["code_under_base_doc"] == 0


def test_modularity_large_repo_flag(tmp_path: Path) -> None:
    for i in range(LARGE_REPO_CODE_FILES + 1):
        _write(tmp_path, f"mod{i}/f.py", "x")
    r = analyze_doc_staleness(tmp_path)
    assert r["modularity"]["large_repo"] is True
    assert r["modularity"]["base_doc_coverage"] == 0.0  # no base docs anywhere


def test_no_git_degrades_to_zero_churn(tmp_path: Path) -> None:
    """Without git history, churn is zero and last_commit_days is None - no crash."""
    _write(tmp_path, "README.md", "doc")
    _write(tmp_path, "app.py", "x")
    r = analyze_doc_staleness(tmp_path)
    assert r["available"] is True
    readme = next(d for d in r["docs"] if d["path"] == "README.md")
    assert readme["last_commit_days"] is None
    assert readme["code_churn_in_window"] == 0


def test_stale_doc_beside_churny_code_has_high_ratio(git_repo) -> None:
    """The decaying-map signal: old doc + actively churning subject = high ratio."""
    repo, commit = git_repo
    (repo / "README.md").write_text("module map", encoding="utf-8")
    (repo / "app.py").write_text("v = 0", encoding="utf-8")
    # Doc committed long ago, outside the 12-month window.
    commit("initial docs+code", days_ago=500)
    # Code churns repeatedly and recently; the doc never moves again.
    for i in range(5):
        (repo / "app.py").write_text(f"v = {i + 1}", encoding="utf-8")
        commit(f"change {i}", days_ago=20 - i * 2)

    r = analyze_doc_staleness(repo)
    readme = next(d for d in r["docs"] if d["path"] == "README.md")
    assert readme["last_commit_days"] is not None and readme["last_commit_days"] >= 400
    assert readme["code_churn_in_window"] >= 5
    assert readme["doc_churn_in_window"] == 0  # didn't move inside the window
    assert readme["ratio"] >= 5  # frozen map of a churning module


def test_fresh_doc_beside_fresh_code_has_low_ratio(git_repo) -> None:
    repo, commit = git_repo
    (repo / "README.md").write_text("module map", encoding="utf-8")
    (repo / "app.py").write_text("v = 1", encoding="utf-8")
    commit("recent docs+code", days_ago=5)

    r = analyze_doc_staleness(repo)
    readme = next(d for d in r["docs"] if d["path"] == "README.md")
    assert readme["last_commit_days"] is not None and readme["last_commit_days"] <= 30
    # doc and code moved together -> ratio stays near 1 (not a decaying map)
    assert readme["ratio"] <= 2
