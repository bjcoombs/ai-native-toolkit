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


def test_dirty_excludes_assess_outputs(git_repo):
    """A repository that tracks `.assess/` gets that directory rewritten by the
    run itself before the snapshot is taken, so a modified file there is the
    tool's own output and must not raise the uncommitted-edits warning (#414)."""
    repo, commit = git_repo
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    assess = repo / ".assess"
    assess.mkdir()
    (assess / "complexity-stats.json").write_text("{}\n", encoding="utf-8")
    commit("initial commit")

    # The run rewrites its own sidecar.
    (assess / "complexity-stats.json").write_text(
        '{"files_scored": 1}\n', encoding="utf-8")

    assert git_churn.git_commit_info(repo)["dirty"] is False


def test_dirty_excludes_assess_scoped_subdirectory(git_repo):
    """A scoped run writes under `.assess/<slug>/`; that subdirectory is
    excluded on the same terms as the top-level wiki."""
    repo, commit = git_repo
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    scoped = repo / ".assess" / "backend"
    scoped.mkdir(parents=True)
    (scoped / "assess-report.md").write_text("report\n", encoding="utf-8")
    commit("initial commit")

    (scoped / "assess-report.md").write_text("rewritten\n", encoding="utf-8")

    assert git_churn.git_commit_info(repo)["dirty"] is False


def test_dirty_excludes_assess_by_pathspec_not_status_code(git_repo):
    """The exclusion is a git pathspec, so a staged edit and a `git rm` under
    `.assess/` drop out on the same terms as an unstaged modification."""
    import subprocess

    repo, commit = git_repo
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    assess = repo / ".assess"
    assess.mkdir()
    (assess / "notes.md").write_text("notes\n", encoding="utf-8")
    (assess / "log.md").write_text("log\n", encoding="utf-8")
    commit("initial commit")

    (assess / "notes.md").write_text("staged edit\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".assess/notes.md"],
                   check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo), "rm", "-q", ".assess/log.md"],
                   check=True, capture_output=True, text=True)

    assert git_churn.git_commit_info(repo)["dirty"] is False


def test_dirty_excludes_assess_but_still_flags_source_edits(git_repo):
    """The pathspec narrows the check rather than disabling it: a modified
    tracked file outside `.assess/` still reports dirty."""
    repo, commit = git_repo
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    assess = repo / ".assess"
    assess.mkdir()
    (assess / "complexity-stats.json").write_text("{}\n", encoding="utf-8")
    commit("initial commit")

    (assess / "complexity-stats.json").write_text(
        '{"files_scored": 1}\n', encoding="utf-8")
    (repo / "a.py").write_text("x = 2\nprint(x)\n", encoding="utf-8")

    assert git_churn.git_commit_info(repo)["dirty"] is True


def test_dirty_flags_assess_config_edits(git_repo):
    """`.assess/config.toml` is an input to the scan, not one of its outputs:
    `lib.assess_config.load_config` reads it and its excludes reach every
    scan, so an uncommitted edit there really does move the measured figures
    off HEAD and must still report dirty."""
    repo, commit = git_repo
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    assess = repo / ".assess"
    assess.mkdir()
    (assess / "config.toml").write_text('exclude_dirs = ["vendor"]\n',
                                        encoding="utf-8")
    commit("initial commit")

    (assess / "config.toml").write_text(
        'exclude_dirs = ["vendor", "generated"]\n', encoding="utf-8")

    assert git_churn.git_commit_info(repo)["dirty"] is True


def test_dirty_excludes_assess_outputs_beside_the_config(git_repo):
    """The config carve-out is that one path and no more: a rewritten wiki
    page beside an untouched `config.toml` still reads clean."""
    repo, commit = git_repo
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    assess = repo / ".assess"
    assess.mkdir()
    (assess / "config.toml").write_text('exclude_dirs = ["vendor"]\n',
                                        encoding="utf-8")
    (assess / "log.md").write_text("# Run log\n", encoding="utf-8")
    commit("initial commit")

    (assess / "log.md").write_text("# Run log\n\n- a run\n", encoding="utf-8")

    assert git_churn.git_commit_info(repo)["dirty"] is False


def test_assess_pathspecs_derive_from_assess_config(tmp_path):
    """Both `dirty` pathspecs and `load_config`'s own path are built from the
    same two constants in `assess_config`, so a rename of the directory or the
    file moves them together. Were the directory a separate literal here, a
    rename there would leave `ASSESS_CONFIG_PATHSPEC` naming a path that no
    longer exists: `git status` exits 0 empty on it, and an uncommitted config
    edit would silently stop flagging `dirty` - a false clean."""
    from lib import assess_config

    assert git_churn.ASSESS_OUTPUT_DIR == assess_config.ASSESS_DIR
    assert git_churn.ASSESS_EXCLUDE_PATHSPEC == (
        f":(exclude){assess_config.ASSESS_DIR}")
    assert git_churn.ASSESS_CONFIG_PATHSPEC == (
        f"{assess_config.ASSESS_DIR}/{assess_config.CONFIG_FILE}")

    # The other end of the seam: the path the pathspec points at is the one
    # `load_config` actually reads.
    config_dir = tmp_path / assess_config.ASSESS_DIR
    config_dir.mkdir()
    (config_dir / assess_config.CONFIG_FILE).write_text(
        'exclude_dirs = ["vendor"]\n', encoding="utf-8")
    assert assess_config.load_config(tmp_path) == {"exclude_dirs": ["vendor"]}
