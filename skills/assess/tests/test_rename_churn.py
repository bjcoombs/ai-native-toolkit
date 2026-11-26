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

import lib.history.change_coupling as cc
import lib.docs.doc_staleness as ds
import lib.history.git_churn as gc
from lib.run.run_scope import Scope
from lib.docs.doc_complexity_join import analyze_doc_complexity_join
from lib.docs.doc_staleness import analyze_doc_staleness
from lib.history.git_churn import commit_epochs_by_file, git_churn_scores, pick_churn_window

Step = Callable[[Path], None]


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    import os

    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="surrogateescape", check=True,
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
    # Five commits as a.py and one as b.py: `git log --follow` less the
    # rename-only commit, which changes no content (contract I425-2).
    assert follow == 7
    assert git_churn_scores(repo, renames=renames)[b] == follow - 1 == 6
    assert git_churn_scores(repo)[b] == 2
    epochs = commit_epochs_by_file(repo, None, renames)
    assert epochs is not None and len(epochs[b]) == 6
    # pick_churn_window builds the map itself when none is passed.
    churn, _label = pick_churn_window(repo, [b])
    assert churn == {b: 6}


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
        if rename:  # a rename with an edit: a content change under the new name
            _git(repo, "mv", "pkg/pkg.md", "pkg/README.md")
            doc = "pkg/README.md"
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


def _doc_history(repo: Path, rename: bool) -> None:
    """Contract I425-2: ``docs/guide.md`` created 100 days ago and edited in
    four commits 90 to 60 days ago. With ``rename`` it is created as
    ``docs/old.md`` and moved to ``docs/guide.md`` in a commit of its own 70
    days ago, between the edits, with no content change."""
    doc = "docs/old.md" if rename else "docs/guide.md"
    _append(repo, doc, "# Guide\n")
    _append(repo, "src/app.py", "x = 0\n")
    _commit(repo, "create", 100)
    for days in (90, 80):
        _append(repo, doc, f"Edit at {days}.\n")
        _commit(repo, "doc edit", days)
    if rename:
        _git(repo, "mv", doc, "docs/guide.md")
        _commit(repo, "rename only", 70)
        doc = "docs/guide.md"
    for days in (65, 60):
        _append(repo, doc, f"Edit at {days}.\n")
        _commit(repo, "doc edit", days)
    for i, days in enumerate((50, 40, 30, 20, 10, 5)):
        _append(repo, "src/app.py", f"x = {i}\n")
        _commit(repo, "code edit", days)


def test_a_rename_only_commit_is_not_doc_churn(tmp_path: Path) -> None:
    """Contract I425-2: the twin with a pure ``git mv`` reports the same doc
    churn, last-change age and lying_map paths as the twin without one."""
    rows, lying = {}, {}
    for rename in (False, True):
        repo = _repo(tmp_path / f"i{rename}")
        _doc_history(repo, rename)
        block = analyze_doc_staleness(repo)
        row = next(d for d in block["docs"] if d["path"] == "docs/guide.md")
        rows[rename] = (row["doc_churn_in_window"], row["last_commit_days"])
        stats = {"ccn": {"p95": 8.0}, "top_complex": [{"path": "src/app.py", "ccn": 30.0}]}
        joined = analyze_doc_complexity_join(stats, block, repo)
        lying[rename] = joined["findings"]["lying_maps"]
    assert rows[True] == rows[False]
    assert rows[False][0] == 5
    assert lying[True] == lying[False]


def test_a_trailing_rename_only_commit_does_not_reset_the_doc_age(tmp_path: Path) -> None:
    ages = {}
    for rename in (False, True):
        repo = _repo(tmp_path / f"t{rename}")
        _append(repo, "docs/old.md", "# Guide\n")
        _commit(repo, "create", 100)
        _append(repo, "docs/old.md", "Edit.\n")
        _commit(repo, "edit", 90)
        if rename:
            _git(repo, "mv", "docs/old.md", "docs/guide.md")
            _commit(repo, "rename only", 10)
        _append(repo, "notes.txt", "x\n")
        _commit(repo, "head", 0)  # ages count to HEAD (#773): the same HEAD date for both
        row = next(d for d in analyze_doc_staleness(repo)["docs"]
                   if d["path"] in ("docs/old.md", "docs/guide.md"))
        ages[rename] = row["last_commit_days"]
    assert ages[True] == ages[False] == 90


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
    assert len(epochs[(sub / "new.py").resolve()]) == 2  # the pure rename is not churn
    assert git_churn_scores(sub, renames=renames) == {(sub / "new.py").resolve(): 2}


def test_a_rename_into_a_scope_is_not_churn_under_a_pathspec(tmp_path: Path) -> None:
    """A pure move from outside the scope or sub-directory root into it: the
    pathspec alone would hide the delete side and count the add (contract I425-2)."""
    repo = _repo(tmp_path / "p")
    _append(repo, "other/a.py", "a = 1\n")
    _append(repo, "pkg/z.py", "z = 1\n")
    _commit(repo, "init", 50)
    _append(repo, "other/a.py", "a = 2\n")
    _commit(repo, "edit", 40)
    (repo / "pkg").mkdir(exist_ok=True)
    _git(repo, "mv", "other/a.py", "pkg/a.py")
    _commit(repo, "move only", 30)
    renames = cc.build_rename_map(repo).paths
    assert renames == {"other/a.py": "pkg/a.py"}
    pkg = (repo / "pkg").resolve()
    moved = (pkg / "a.py").resolve()
    z = (pkg / "z.py").resolve()
    # The move is the only commit the pathspec reaches for a.py, and it is not
    # churn; its history under other/ stays outside the scope, as before.
    assert git_churn_scores(repo, scope=Scope.of(repo, pkg), renames=renames) == {z: 1}
    # A sub-directory root without a scope walks the whole history: two edits.
    assert git_churn_scores(pkg, renames=renames) == {moved: 2, z: 1}
    # commit_epochs_by_file walks with the root as its pathspec, like a scope.
    epochs = commit_epochs_by_file(pkg, None, renames)
    assert epochs is not None and set(epochs) == {z}
    # Unscoped, the whole history follows the rename: two edits, no move.
    assert git_churn_scores(repo, renames=renames)[moved] == 2


def test_commit_epochs_outside_git(tmp_path: Path) -> None:
    assert commit_epochs_by_file(tmp_path, None) is None
