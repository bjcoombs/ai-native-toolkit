"""Shallow history is reported honestly in the staleness signals (issue #544).

A shallow clone shows its boundary commit as a root commit that touches every
file, so a doc last changed before the boundary looks as if it changed at the
boundary. The fixtures build one history, then read it as a full clone and as a
``--depth`` clone of the same repository.
"""
from __future__ import annotations

import datetime as _dt
import os
import subprocess
from pathlib import Path

from assess_report import _render_churn_window
from lib.run.context_blocks import build_stale_hubs
from lib.docs.doc_staleness import analyze_doc_staleness

DAY = 86400


def _git(repo: Path, *args: str, days_ago: int | None = None) -> str:
    env = dict(os.environ)
    if days_ago is not None:
        ts = int(_dt.datetime.now().timestamp()) - days_ago * DAY
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"{ts} +0000"
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="surrogateescape",
        check=True, env=env,
    ).stdout


def _commit(repo: Path, files: dict[str, str], msg: str, days_ago: int) -> None:
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg, days_ago=days_ago)


def _init(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    return path


def _shallow(src: Path, dest: Path, depth: int) -> Path:
    # A plain local path ignores --depth; a file:// URL honours it.
    subprocess.run(
        ["git", "clone", "-q", f"--depth={depth}", src.resolve().as_uri(), str(dest)],
        capture_output=True, text=True, encoding="utf-8", errors="surrogateescape", check=True,
    )
    return dest


def _history(tmp_path: Path) -> Path:
    """src/README.md written 200 days ago; the boundary of a depth-3 clone is
    the 100-days-ago commit; src/a.py churns after it, lib/b.py never does."""
    src = _init(tmp_path / "src-repo")
    _commit(src, {"src/README.md": "doc", "src/a.py": "a = 1\n",
                  "lib/README.md": "lib doc", "lib/b.py": "b = 1\n"}, "init", 200)
    _commit(src, {"other.txt": "x"}, "boundary", 100)
    _commit(src, {"src/a.py": "a = 2\n"}, "code", 50)
    _commit(src, {"other.txt": "y"}, "head", 10)
    return src


def _rows(block: dict[str, object]) -> dict[str, dict[str, object]]:
    docs = block["docs"]
    assert isinstance(docs, list)
    return {d["path"]: d for d in docs}


def test_shallow_clone_reports_lower_bound_age(tmp_path: Path) -> None:
    clone = _shallow(_history(tmp_path), tmp_path / "shallow", depth=3)
    block = analyze_doc_staleness(clone)
    readme = _rows(block)["src/README.md"]

    assert readme["last_commit_days_bound"] == "lower"
    # The boundary age (100 days ago) counted to HEAD (10 days ago, #773): a floor.
    assert 89 <= readme["last_commit_days"] <= 91
    assert readme["confidence"] == "low"
    assert block["history_truncated"] is True
    boundary = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=100)).date()
    assert block["history_boundary_date"] in {
        str(boundary), str(boundary - _dt.timedelta(days=1)),
        str(boundary + _dt.timedelta(days=1))}
    # The boundary commit is not a bulk sweep, so no doc falls back to creation.
    assert block["bulk_commits_skipped_total"] == 0
    assert "last_change_basis" not in readme


def test_lower_bound_doc_is_stale_only_on_post_boundary_churn(tmp_path: Path) -> None:
    """The boundary lists every code file; that artefact alone must not make a
    doc stale. Subject commits after the boundary do."""
    clone = _shallow(_history(tmp_path), tmp_path / "shallow", depth=3)
    rows = _rows(analyze_doc_staleness(clone))

    assert rows["src/README.md"]["code_churn_since_doc_change"] == 1
    assert rows["src/README.md"]["ratio"] > 0
    assert rows["lib/README.md"]["last_commit_days_bound"] == "lower"
    assert rows["lib/README.md"]["code_churn_since_doc_change"] == 0
    assert rows["lib/README.md"]["ratio"] == 0


def test_full_clone_reports_exact_age_unchanged(tmp_path: Path) -> None:
    block = analyze_doc_staleness(_history(tmp_path))
    readme = _rows(block)["src/README.md"]

    assert 189 <= readme["last_commit_days"] <= 191  # 200 days ago, to HEAD 10 days ago
    assert "last_commit_days_bound" not in readme
    assert readme["confidence"] == "high"
    assert block["history_truncated"] is False
    assert block["history_boundary_date"] is None


def test_boundary_is_never_a_bulk_commit(tmp_path: Path) -> None:
    """With ten or more docs the boundary touches every one, which the bulk
    rule would otherwise skip, turning every age into a creation date."""
    src = _init(tmp_path / "src-repo")
    _commit(src, {f"d{i}/README.md": f"doc {i}" for i in range(12)}
            | {f"d{i}/m.py": "x = 1\n" for i in range(12)}, "init", 300)
    _commit(src, {"other.txt": "x"}, "boundary", 100)
    _commit(src, {"d0/README.md": "doc 0 v2"}, "edit", 20)
    block = analyze_doc_staleness(_shallow(src, tmp_path / "shallow", depth=2))
    rows = _rows(block)

    assert block["creation_date_fallback_count"] == 0
    assert block["bulk_commits_skipped_total"] == 0
    assert "last_commit_days_bound" not in rows["d0/README.md"]
    assert rows["d0/README.md"]["last_commit_days"] == 0  # the edit is HEAD (#773)
    for i in range(1, 12):
        assert rows[f"d{i}/README.md"]["last_commit_days_bound"] == "lower"
        assert "last_change_basis" not in rows[f"d{i}/README.md"]


def test_stale_hubs_carry_the_bound() -> None:
    doc_graph = {"available": True, "hubs": [
        {"path": "a.md", "pagerank": 0.5}, {"path": "b.md", "pagerank": 0.5}]}
    staleness = {"available": True, "docs": [
        {"path": "a.md", "last_commit_days": 90, "code_churn_in_window": 3,
         "ratio": 3.0, "subject_method": "nearest-ancestor", "confidence": "low",
         "last_commit_days_bound": "lower"},
        {"path": "b.md", "last_commit_days": 9, "code_churn_in_window": 3,
         "ratio": 1.0, "subject_method": "nearest-ancestor", "confidence": "high"},
    ]}
    hubs = {h["path"]: h for h in build_stale_hubs(doc_graph, staleness)}
    assert hubs["a.md"]["last_commit_days_bound"] == "lower"
    assert "last_commit_days_bound" not in hubs["b.md"]


def test_report_names_the_truncation() -> None:
    ctx = {"doc_staleness": {"churn_window": "90d", "history_truncated": True,
                             "history_boundary_date": "2026-08-01"}}
    assert _render_churn_window(ctx) == (
        "90d - shallow clone, history before 2026-08-01 not in the clone")
    ctx["doc_staleness"]["history_truncated"] = False
    assert _render_churn_window(ctx) == "90d"


def test_boundary_of_another_shallow_ref_is_ignored(tmp_path: Path) -> None:
    """``$GIT_DIR/shallow`` also lists the boundary of a second shallow-fetched
    branch; only HEAD's boundary dates HEAD's history (issue #633)."""
    src = _history(tmp_path)
    _git(src, "checkout", "-q", "-b", "other")
    _commit(src, {"other.txt": "z"}, "other tip", 5)
    _git(src, "checkout", "-q", "-")
    clone = _shallow(src, tmp_path / "shallow", depth=3)
    _git(clone, "fetch", "-q", "--depth=1", "origin", "other:other")
    other_tip = _git(clone, "rev-parse", "other").strip()
    shallow_file = clone / _git(clone, "rev-parse", "--git-path", "shallow").strip()
    assert other_tip in shallow_file.read_text(encoding="utf-8").split()  # the newer, unreachable boundary

    block = analyze_doc_staleness(clone)
    boundary = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=100)).date()
    assert block["history_boundary_date"] in {
        str(boundary), str(boundary - _dt.timedelta(days=1)),
        str(boundary + _dt.timedelta(days=1))}
