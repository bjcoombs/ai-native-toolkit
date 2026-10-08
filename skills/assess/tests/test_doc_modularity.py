"""Tests for the doc-staleness modularity block (`doc_staleness.modularity`).

Split from `test_doc_staleness.py` to keep that file under its size ceiling.
"""
from __future__ import annotations

from pathlib import Path

from lib.doc_staleness import LARGE_REPO_CODE_FILES, analyze_doc_staleness


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_modularity_large_repo_flag(tmp_path: Path) -> None:
    for i in range(LARGE_REPO_CODE_FILES + 1):
        _write(tmp_path, f"mod{i}/f.py", "x")
    r = analyze_doc_staleness(tmp_path)
    assert r["modularity"]["large_repo"] is True
    assert r["modularity"]["base_doc_coverage_when_present"] == 0.0  # no base docs anywhere
    assert r["modularity"]["base_doc_dir_ratio"] == 0.0


def test_base_doc_coverage_is_size_weighted(tmp_path: Path) -> None:
    """One 30-file service with a base doc should not be drowned by ten
    1-file utility dirs without one. Size-weighting reflects what an agent
    actually needs to navigate; the un-weighted dir ratio is reported alongside
    for transparency.
    """
    # A large module (30 files) with a base doc...
    _write(tmp_path, "services/payments/README.md", "payments")
    for i in range(30):
        _write(tmp_path, f"services/payments/f{i}.py", "x")
    # ...and ten utility/leaf dirs (1 file each) with no doc.
    for i in range(10):
        _write(tmp_path, f"internal/util{i}/u.py", "x")

    r = analyze_doc_staleness(tmp_path)
    # Un-weighted dir ratio is low (1 doc'd dir out of 11), but the size-weighted
    # coverage reflects that ~75% of code sits under a maintained base doc.
    assert r["modularity"]["base_doc_dir_ratio"] < 0.2
    assert r["modularity"]["base_doc_coverage_when_present"] >= 0.7
    # Sanity: the when-present number matches what the association block reports.
    assert r["modularity"]["base_doc_coverage_when_present"] == r["association"]["pct_code_under_base_doc"]


def test_docs_only_base_doc_dir_does_not_raise_dir_ratio(tmp_path: Path) -> None:
    """A README in a directory with no code (`docs/`, `agents/`) is not module
    documentation: the numerator counts only code directories, like the
    denominator, so adding or moving such a README leaves the ratio unchanged.
    """
    _write(tmp_path, "src/a/README.md", "a")
    _write(tmp_path, "src/a/x.py", "x")
    _write(tmp_path, "src/b/y.py", "y")
    before = analyze_doc_staleness(tmp_path)["modularity"]
    _write(tmp_path, "docs/README.md", "docs")
    _write(tmp_path, "agents/README.md", "agents")
    after = analyze_doc_staleness(tmp_path)["modularity"]
    assert before["module_dir_count"] == after["module_dir_count"] == 2
    assert before["module_dirs_with_base_doc"] == after["module_dirs_with_base_doc"] == 1
    assert before["base_doc_dir_ratio"] == after["base_doc_dir_ratio"] == 0.5


def test_base_doc_dir_ratio_never_exceeds_one(tmp_path: Path) -> None:
    """More docs-only base-doc dirs than code dirs must not push the ratio past 1."""
    _write(tmp_path, "src/README.md", "src")
    _write(tmp_path, "src/x.py", "x")
    for i in range(5):
        _write(tmp_path, f"docs/topic{i}/README.md", "t")
    m = analyze_doc_staleness(tmp_path)["modularity"]
    assert m["module_dirs_with_base_doc"] == 1
    assert m["base_doc_dir_ratio"] == 1.0


def test_code_dir_with_base_doc_counts(tmp_path: Path) -> None:
    """Every code directory holding a base doc counts, the repo root included."""
    _write(tmp_path, "README.md", "root")
    _write(tmp_path, "setup.py", "x")
    _write(tmp_path, "pkg/README.md", "pkg")
    _write(tmp_path, "pkg/m.py", "x")
    _write(tmp_path, "pkg/sub/n.py", "x")
    m = analyze_doc_staleness(tmp_path)["modularity"]
    assert m["module_dir_count"] == 3
    assert m["module_dirs_with_base_doc"] == 2
    assert m["base_doc_dir_ratio"] == 0.667
