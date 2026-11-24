"""ghsync's GitLab provider: sync, fast-forward and the analysis-only clone flags.

The fixture and stubs live in `ghsync_gitlab_env.py`.
"""

import json
from pathlib import Path

import pytest
from ghsync_gitlab_env import (
    HOST,
    SYNCED,
    Env,
    _checkouts,
    _config,
    _missing_objects,
    _project,
    _section,
)
from gitbash import rmtree


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


# ---- sync -------------------------------------------------------------------


def test_dry_run_creates_no_checkout(env: Env) -> None:
    res = env.sync("--dry-run")
    assert res.returncode == 0, res.stdout + res.stderr
    created = {p.name for p in env.root.iterdir()} - {".gh_archived_repos"}
    assert created == set(), created


def test_first_run_clones_nested_layout_then_up_to_date(env: Env) -> None:
    res = env.sync()
    assert res.returncode == 0, res.stdout + res.stderr
    for path in SYNCED:
        checkout = env.checkout(path)
        assert (checkout / ".git").is_dir(), f"{path} not cloned at {checkout}"
        assert (env.root / path / "worktree").is_dir()
        assert env.git("branch", "--show-current", cwd=checkout) == "main"
        assert (checkout / "README").read_text() == f"{path}\n"
    assert not (env.root / "grp" / "old").exists()
    assert not (env.root / "grp" / "other").exists()
    assert f"Up-to-date: 0 | Updated: {len(SYNCED)} | Failed: 0" in res.stdout
    assert "GitLab Sync Summary" in res.stdout

    before = {p: env.head(p) for p in SYNCED}
    res = env.sync()
    assert res.returncode == 0, res.stdout + res.stderr
    assert {p: env.head(p) for p in SYNCED} == before
    assert f"Up-to-date: {len(SYNCED)} | Updated: 0 | Failed: 0" in res.stdout
    assert not env.gh_log.exists()


def test_uncommitted_change_is_fetched_not_pulled(env: Env) -> None:
    assert env.sync().returncode == 0
    checkout = env.checkout("grp/alpha")
    head = env.head("grp/alpha")
    new = env.advance("grp/alpha")
    (checkout / "README").write_text("local edit\n", newline="\n")
    res = env.sync()
    assert res.returncode == 0, res.stdout + res.stderr
    assert env.head("grp/alpha") == head
    assert env.git("rev-parse", "origin/main", cwd=checkout) == new
    assert (checkout / "README").read_text() == "local edit\n"
    assert _section(res.stdout, "Uncommitted changes") == ["grp/alpha"]


def test_untracked_file_does_not_block_fast_forward(env: Env) -> None:
    """Pins the documented rule: only tracked changes count as uncommitted."""
    assert env.sync().returncode == 0
    checkout = env.checkout("grp/sub/beta")
    new = env.advance("grp/sub/beta")
    (checkout / "scratch.txt").write_text("untracked\n", newline="\n")
    res = env.sync()
    assert res.returncode == 0, res.stdout + res.stderr
    assert env.head("grp/sub/beta") == new
    assert (checkout / "scratch.txt").read_text() == "untracked\n"
    assert _section(res.stdout, "Uncommitted changes") == []
    assert _section(res.stdout, "Updated") == ["grp/sub/beta"]


def test_behind_checkout_fast_forwards(env: Env) -> None:
    assert env.sync().returncode == 0
    new = env.advance("grp/sub/deep/gamma")
    res = env.sync()
    assert res.returncode == 0, res.stdout + res.stderr
    assert env.head("grp/sub/deep/gamma") == new
    assert _section(res.stdout, "Updated") == ["grp/sub/deep/gamma"]


def test_checkout_on_another_branch_is_fetched_not_pulled(env: Env) -> None:
    """A checkout off the default branch is fetched, left where it is, and
    listed under Not on default branch."""
    assert env.sync().returncode == 0
    checkout = env.checkout("grp/alpha")
    env.git("checkout", "-q", "-b", "feature", cwd=checkout)
    head = env.head("grp/alpha")
    new = env.advance("grp/alpha")
    res = env.sync()
    assert res.returncode == 0, res.stdout + res.stderr
    assert env.git("branch", "--show-current", cwd=checkout) == "feature"
    assert env.head("grp/alpha") == head
    assert env.git("rev-parse", "main", cwd=checkout) == head
    assert env.git("rev-parse", "origin/main", cwd=checkout) == new
    assert _section(res.stdout, "Not on default branch") == ["grp/alpha (feature)"]
    assert "grp/alpha" not in _section(res.stdout, "Updated")


def test_limit_applies_to_discovered_projects(env: Env) -> None:
    """--limit without --from-list takes the first N discovered projects."""
    res = env.sync("--limit", "2")
    assert res.returncode == 0, res.stdout + res.stderr
    assert _checkouts(env.root) == set(SYNCED[:2])
    assert "Up-to-date: 0 | Updated: 2 | Failed: 0" in res.stdout


def test_group_on_second_membership_page_is_synced(env: Env) -> None:
    """A group returned only on page 2 of the groups call is still found."""
    (env.data / "ids" / "5").write_text("solo", newline="\n")
    (env.data / "groups.2.json").write_text(json.dumps([{"id": 5, "full_path": "solo", "path": "solo"}]), newline="\n")
    for key, page in (
        ("groups__solo__descendant_groups", []),
        ("groups__solo__projects", [_project("solo/proj")]),
        ("groups__solo__projects__shared", []),
    ):
        (env.data / f"{key}.1.json").write_text(json.dumps(page), newline="\n")
    env._make_bare("solo/proj")
    res = env.sync()
    assert res.returncode == 0, res.stdout + res.stderr
    assert _checkouts(env.root) == {*SYNCED, "solo/proj"}


def test_quiet_drops_clone_progress_lines(env: Env) -> None:
    """--quiet suppresses Cloning / Cloned; a failed clone is still named."""
    rmtree(env.bare / "grp" / "sub" / "beta.git")
    res = env.sync()
    assert res.returncode == 0, res.stdout + res.stderr
    assert "*** Cloning" not in res.stdout
    assert "*** Cloned" not in res.stdout
    assert "*** Failed to clone grp/sub/beta" in res.stdout
    assert _section(res.stdout, "Failed") == ["grp/sub/beta"]


def test_without_quiet_clone_progress_lines_print(env: Env) -> None:
    res = env.run("--host", HOST, root=env.root)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "*** Cloning grp/alpha" in res.stdout
    assert "*** Cloned grp/alpha" in res.stdout


def test_partial_clone_keeps_history_without_historic_blobs(env: Env) -> None:
    env.advance("grp/sub/beta")
    env.advance("grp/sub/beta")
    res = env.sync("--partial")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "filtering not recognized" not in res.stdout + res.stderr
    checkout = env.checkout("grp/sub/beta")
    assert _config(env, checkout, "remote.origin.partialclonefilter") == "blob:none"
    assert _missing_objects(env, checkout)
    seed = env.tmp / "seed" / "grp/sub/beta"
    log = ("log", "--name-only", "--format=%H %s")
    assert env.git(*log, cwd=checkout) == env.git(*log, cwd=seed)


def test_shallow_since_truncates_history(env: Env) -> None:
    new = env.advance("grp/alpha", date="2099-01-01T00:00:00")
    res = env.sync("--shallow-since=2098-01-01")
    assert res.returncode == 0, res.stdout + res.stderr
    checkout = env.checkout("grp/alpha")
    assert env.git("rev-parse", "--is-shallow-repository", cwd=checkout) == "true"
    assert env.git("rev-list", "HEAD", cwd=checkout).split() == [new]


def test_clone_flags_never_convert_an_existing_full_clone(env: Env) -> None:
    assert env.sync().returncode == 0
    new = env.advance("grp/alpha", date="2099-01-01T00:00:00")
    res = env.sync("--filter", "blob:none", "--shallow-since", "2098-01-01")
    assert res.returncode == 0, res.stdout + res.stderr
    checkout = env.checkout("grp/alpha")
    assert env.head("grp/alpha") == new
    assert env.git("rev-parse", "--is-shallow-repository", cwd=checkout) == "false"
    assert _config(env, checkout, "remote.origin.partialclonefilter") is None
    assert _config(env, checkout, "remote.origin.promisor") is None
    assert _missing_objects(env, checkout) == []
