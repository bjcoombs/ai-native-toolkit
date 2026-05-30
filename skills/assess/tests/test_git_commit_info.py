"""Tests for git_churn.git_commit_info - the measured-commit snapshot that lets
the /assess report pin its absolute LOC/CCN figures to a SHA and warn when the
snapshot is stale (issue #59)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_LIB = Path(__file__).resolve().parents[1] / "scripts" / "lib" / "git_churn.py"
_spec = importlib.util.spec_from_file_location("git_churn", _LIB)
git_churn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(git_churn)


def test_returns_unavailable_outside_git_repo(tmp_path):
    """A plain directory (no .git) degrades to available:False with a reason,
    so the report omits the snapshot line rather than inventing a SHA."""
    info = git_churn.git_commit_info(tmp_path)
    assert info["available"] is False
    assert "reason" in info


def test_pins_head_sha_and_clean_tree(git_repo):
    repo, commit = git_repo
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    commit("initial commit")

    info = git_churn.git_commit_info(repo)
    assert info["available"] is True
    assert len(info["head_sha"]) == 40
    assert info["head_short"] == info["head_sha"][:12]
    assert info["subject"] == "initial commit"
    assert info["committed_date"]  # ISO short date, non-empty
    # Clean working tree, and a fresh repo has no upstream configured.
    assert info["dirty"] is False
    assert info["upstream"] is None
    assert info["behind"] is None


def test_flags_dirty_working_tree(git_repo):
    """Uncommitted edits to a tracked file mean the measured numbers reflect
    the working tree, not HEAD - the report must warn on this."""
    repo, commit = git_repo
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    commit("initial commit")
    # Modify the tracked file without committing.
    (repo / "a.py").write_text("x = 2\nprint(x)\n", encoding="utf-8")

    info = git_churn.git_commit_info(repo)
    assert info["dirty"] is True


def test_untracked_file_does_not_mark_dirty(git_repo):
    """Only tracked-file changes count as dirty - a stray untracked file (e.g.
    a contributor's scratch note) must not flip the snapshot warning."""
    repo, commit = git_repo
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    commit("initial commit")
    (repo / "scratch.txt").write_text("notes\n", encoding="utf-8")

    info = git_churn.git_commit_info(repo)
    assert info["dirty"] is False


def test_reports_behind_count_vs_upstream(git_repo, tmp_path):
    """When HEAD trails its upstream, `behind` is the commit gap - that is the
    staleness signal that explains absolute figures drifting low (#59)."""
    import subprocess

    repo, commit = git_repo
    (repo / "a.py").write_text("v = 1\n", encoding="utf-8")
    commit("c1")
    (repo / "a.py").write_text("v = 2\n", encoding="utf-8")
    commit("c2")

    # Stand up a local "remote" two commits ahead, then point the branch's
    # upstream at it while leaving HEAD one commit back.
    def _g(*args, cwd=repo):
        subprocess.run(["git", "-C", str(cwd), *args],
                       check=True, capture_output=True, text=True)

    remote = tmp_path / "remote.git"
    _g("clone", "--bare", str(repo), str(remote), cwd=tmp_path)
    branch = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        check=True, capture_output=True, text=True).stdout.strip()
    _g("remote", "add", "origin", str(remote))
    _g("fetch", "-q", "origin")
    _g("branch", f"--set-upstream-to=origin/{branch}", branch)

    # Advance the remote by one commit so HEAD is exactly 1 behind.
    (repo / "a.py").write_text("v = 3\n", encoding="utf-8")
    commit("c3")
    _g("push", "-q", "origin", branch)
    _g("reset", "-q", "--hard", "HEAD~1")  # move HEAD back behind upstream

    info = git_churn.git_commit_info(repo)
    assert info["upstream"] == f"origin/{branch}"
    assert info["behind"] == 1
