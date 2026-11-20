"""Churn counts follow renames (issue #425).

`git_churn_scores` and `commit_epochs_by_file` credit a commit made under an
old name to the file's current name, through `change_coupling.build_rename_map`.
Each staleness case builds twin histories, one with a rename and one without,
and checks that they yield the same finding.
"""
from __future__ import annotations

import datetime as _dt
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

import lib.change_coupling as cc
import lib.doc_staleness as ds
import lib.git_churn as gc
from lib.doc_complexity_join import analyze_doc_complexity_join
from lib.doc_staleness import analyze_doc_staleness
from lib.git_churn import commit_epochs_by_file, git_churn_scores, pick_churn_window

Step = Callable[[Path], None]


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    import os

    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
        env={**os.environ, **(env or {})},
    ).stdout


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    return path


def _commit(repo: Path, message: str, days_ago: int) -> None:
    stamp = (_dt.datetime.now() - _dt.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message,
         env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})


def _append(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(text)


def _five_then_rename(repo: Path, rename: bool) -> str:
    """`pkg/a.py` committed five times beside an untouched `pkg/README.md`, then
    renamed to `pkg/b.py` (or not), then committed once more. Returns the code
    file's current repo-relative path."""
    _append(repo, "pkg/README.md", "# pkg\n\nWhat pkg does.\n")
    _append(repo, "pkg/a.py", "x = 0\n")
    _commit(repo, "init", 200)
    for i, days in enumerate((150, 140, 130, 120), start=1):
        _append(repo, "pkg/a.py", f"x = {i}\n")
        _commit(repo, f"edit {i}", days)
    current = "pkg/a.py"
    if rename:
        _git(repo, "mv", "pkg/a.py", "pkg/b.py")
        _commit(repo, "rename", 100)
        current = "pkg/b.py"
    _append(repo, current, "y = 1\n")
    _commit(repo, "edit after", 90)
    return current


def _join_finding(repo: Path, code_rel: str, doc_rel: str) -> str | None:
    """The doc-complexity finding for `doc_rel`, with `code_rel` complex enough
    to be classified."""
    stats = {"ccn": {"p95": 8.0}, "top_complex": [{"path": code_rel, "ccn": 30.0}]}
    joined = analyze_doc_complexity_join(stats, analyze_doc_staleness(repo), repo)
    return next(d["finding"] for d in joined["docs"] if d["path"] == doc_rel)


def test_code_churn_follows_a_rename(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    current = _five_then_rename(repo, rename=True)
    b = (repo / current).resolve()
    follow = len(_git(repo, "log", "--follow", "--format=%H", "--", current).split())
    renames = cc.build_rename_map(repo).paths

    assert renames == {"pkg/a.py": "pkg/b.py"}
    # Five commits as a.py, the rename commit and one as b.py: `git log --follow`.
    assert git_churn_scores(repo, renames=renames)[b] == follow == 7
    assert git_churn_scores(repo)[b] == 2
    epochs = commit_epochs_by_file(repo, None, renames)
    assert epochs is not None and len(epochs[b]) == 7
    # pick_churn_window builds the map itself when none is passed.
    churn, _label = pick_churn_window(repo, [b])
    assert churn == {b: 7}


def test_renamed_code_file_yields_the_same_finding(tmp_path: Path) -> None:
    findings = {}
    for rename in (False, True):
        repo = _repo(tmp_path / f"r{rename}")
        current = _five_then_rename(repo, rename)
        row = next(d for d in analyze_doc_staleness(repo)["docs"]
                   if d["path"] == "pkg/README.md")
        assert row["code_churn_in_window"] >= 6
        assert row["confidence"] == "high"
        findings[rename] = _join_finding(repo, current, "pkg/README.md")
    assert findings[True] == findings[False] == "lying_map"


def test_renamed_document_yields_no_new_lying_map(tmp_path: Path) -> None:
    """A doc renamed inside the window keeps its earlier commits, so its churn
    does not drop to the rename commit and inflate the ratio."""
    findings = {}
    for rename in (False, True):
        repo = _repo(tmp_path / f"d{rename}")
        doc = "pkg/pkg.md" if rename else "pkg/README.md"
        _append(repo, doc, "# pkg\n")
        _append(repo, "pkg/a.py", "x = 0\n")
        _commit(repo, "init", 200)
        for days in (190, 180, 170, 160):
            _append(repo, doc, f"Edit at {days}.\n")
            _commit(repo, "doc edit", days)
        if rename:
            _git(repo, "mv", "pkg/pkg.md", "pkg/README.md")
        else:
            _append(repo, doc, "Another edit.\n")
        _commit(repo, "rename or edit", 150)
        for i, days in enumerate((140, 130, 120, 110, 100, 90)):
            _append(repo, "pkg/a.py", f"x = {i}\n")
            _commit(repo, "code edit", days)
        row = next(d for d in analyze_doc_staleness(repo)["docs"]
                   if d["path"] == "pkg/README.md")
        assert row["doc_churn_in_window"] == 6
        findings[rename] = _join_finding(repo, "pkg/a.py", "pkg/README.md")
    assert findings[True] == findings[False]
    assert findings[True] != "lying_map"


def test_rename_map_failure_marks_docs_low_confidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "f")
    current = _five_then_rename(repo, rename=True)
    monkeypatch.setattr(cc, "build_rename_map",
                        lambda *a, **k: cc.RenameMap({}, complete=False))
    ds._renames_at.cache_clear()
    ds._clock_at.cache_clear()
    try:
        block = analyze_doc_staleness(repo)
    finally:
        ds._renames_at.cache_clear()
        ds._clock_at.cache_clear()
    assert block["docs"] and {d["confidence"] for d in block["docs"]} == {"low"}
    assert block["bulk_commit_scan_complete"] is False
    # Low confidence suppresses the lying_map the followed history produces.
    stats = {"ccn": {"p95": 8.0}, "top_complex": [{"path": current, "ccn": 30.0}]}
    joined = analyze_doc_complexity_join(stats, block, repo)
    assert joined["findings"]["lying_maps"] == []


def test_rename_paths_notes_an_incomplete_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(gc, "build_rename_map",
                        lambda *a, **k: cc.RenameMap({}, complete=False))
    assert gc.rename_paths(tmp_path) == {}
    assert "does not follow renames" in capsys.readouterr().err


def test_commit_epochs_from_a_subdirectory_use_top_relative_renames(tmp_path: Path) -> None:
    """The rename map is relative to the git top; a root below it still maps."""
    repo = _repo(tmp_path / "s")
    _append(repo, "sub/old.py", "a = 1\n")
    _append(repo, "other/z.py", "z = 1\n")
    _commit(repo, "init", 50)
    _append(repo, "sub/old.py", "a = 2\n")
    _commit(repo, "edit", 40)
    _git(repo, "mv", "sub/old.py", "sub/new.py")
    _commit(repo, "rename", 30)
    renames = cc.build_rename_map(repo).paths
    sub = repo / "sub"
    epochs = commit_epochs_by_file(sub, None, renames)
    assert epochs is not None
    assert set(epochs) == {(sub / "new.py").resolve()}
    assert len(epochs[(sub / "new.py").resolve()]) == 3
    assert git_churn_scores(sub, renames=renames) == {(sub / "new.py").resolve(): 3}


def test_commit_epochs_outside_git(tmp_path: Path) -> None:
    assert commit_epochs_by_file(tmp_path, None) is None
