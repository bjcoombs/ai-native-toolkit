"""ghsync's GitLab provider: `--from-list` and the project path rules.

The fixture and stubs live in `ghsync_gitlab_env.py`.
"""

from pathlib import Path

import pytest
from ghsync_gitlab_env import HOST, SYNCED, Env, _checkouts, _path_without_glab


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


# ---- --from-list ------------------------------------------------------------


def test_from_list_reproduces_layout_without_api(env: Env) -> None:
    listing = env.run("--host", HOST, "--porcelain")
    assert listing.returncode == 0, listing.stderr
    assert env.sync().returncode == 0

    saved = env.tmp / "list.txt"
    lines = listing.stdout.splitlines()
    saved.write_text(
        "# saved from another network\n\n"
        + "\n".join(f"  {ln}\r" if i == 0 else ln for i, ln in enumerate(lines))
        + "\n\n# trailing comment\n"
        + lines[0]
        + "\n",
        newline="\n",
    )
    env.glab_log.unlink()
    root2 = env.tmp / "root2"
    root2.mkdir()
    res = env.run(
        "--host",
        HOST,
        "--from-list",
        str(saved),
        "--quiet",
        env={"GLAB_API_FAIL": "1"},
        root=root2,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert _checkouts(root2) == _checkouts(env.root) == set(SYNCED)
    assert env.api_calls() == []


def test_from_list_works_without_glab(env: Env, tmp_path: Path) -> None:
    saved = tmp_path / "list.txt"
    saved.write_text("grp/alpha\nother/ns/shared\n", newline="\n")
    res = env.run(
        "--host",
        HOST,
        "--from-list",
        str(saved),
        "--quiet",
        env={"PATH": _path_without_glab(tmp_path)},
        root=env.root,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert _checkouts(env.root) == {"grp/alpha", "other/ns/shared"}


def test_from_list_respects_limit(env: Env, tmp_path: Path) -> None:
    saved = tmp_path / "list.txt"
    saved.write_text("\n".join(SYNCED) + "\n", newline="\n")
    res = env.run(
        "--host",
        HOST,
        "--from-list",
        str(saved),
        "--limit",
        "2",
        "--quiet",
        root=env.root,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert _checkouts(env.root) == set(SYNCED[:2])


def test_from_list_with_scheme_host(env: Env, tmp_path: Path) -> None:
    saved = tmp_path / "list.txt"
    saved.write_text("grp/sub/beta\n", newline="\n")
    res = env.run(
        "--host", f"https://{HOST}", "--from-list", str(saved), "--quiet", root=env.root
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert _checkouts(env.root) == {"grp/sub/beta"}


def test_from_list_skips_reserved_root_entry(env: Env, tmp_path: Path) -> None:
    """A path under ghsync's own root entry is never cloned, and nothing in
    that entry or in the other checkouts is removed by a later sync."""
    legacy = env.root / ".gh_sync_tmp"
    legacy.mkdir()
    (legacy / "keep").write_text("left by an older version\n", newline="\n")
    saved = tmp_path / "list.txt"
    saved.write_text(".gh_sync_tmp/proj\ngrp/alpha\n", newline="\n")
    res = env.run("--host", HOST, "--from-list", str(saved), "--quiet", root=env.root)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "skipping unsafe repository path: .gh_sync_tmp/proj" in res.stderr
    assert _checkouts(env.root) == {"grp/alpha"}
    assert not (legacy / "proj").exists()
    sentinel = env.root / "grp" / "alpha" / "alpha-main" / "uncommitted.txt"
    sentinel.write_text("work in progress\n", newline="\n")
    res = env.sync()
    assert res.returncode == 0, res.stdout + res.stderr
    assert (legacy / "keep").read_text() == "left by an older version\n"
    assert sentinel.read_text() == "work in progress\n"
    assert "grp/alpha" in _checkouts(env.root)


def test_from_list_skips_unsafe_paths(env: Env, tmp_path: Path) -> None:
    """A quote, ../ or absolute path never reaches the sync loop or the disk."""
    saved = tmp_path / "list.txt"
    saved.write_text(
        "grp/alpha\n"
        "grp/x'; touch pwned; '\n"
        "../escape\n"
        "grp/../../escape\n"
        "/abs/path\n"
        "grp/./alpha\n"
        "grp/has space\n",
        newline="\n",
    )
    res = env.run("--host", HOST, "--from-list", str(saved), "--quiet", root=env.root)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _checkouts(env.root) == {"grp/alpha"}
    assert res.stderr.count("skipping unsafe repository path") == 6
    assert not (env.tmp / "escape").exists()
    assert not list(env.tmp.rglob("pwned"))


def test_from_list_missing_file_fails(env: Env) -> None:
    res = env.run(
        "--host", HOST, "--from-list", str(env.tmp / "nope.txt"), root=env.root
    )
    assert res.returncode != 0
    assert "nope.txt" in res.stdout + res.stderr


# ---- path rules ---------------------------------------------------------------
#
# GitLab's lib/gitlab/path_regex.rb: PATH_START_CHAR = '[a-zA-Z0-9_\.]', the
# rest of a segment is [a-zA-Z0-9_\-\.], and NO_SUFFIX_REGEX forbids a
# segment ending in .git or .atom.


def test_dot_leading_project_is_synced(env: Env) -> None:
    res = env.sync()
    assert res.returncode == 0, res.stdout + res.stderr
    assert "grp/.dotfiles" in _checkouts(env.root)
    assert "unsafe repository path" not in res.stderr


@pytest.mark.parametrize(
    ("path", "kept"),
    [
        ("grp/alpha", True),
        ("grp/9lives", True),
        ("grp/_under", True),
        ("grp/.github", True),
        ("grp/...", True),
        (".hidden/proj", True),
        ("grp/a.b-c_d", True),
        ("grp/-dash", False),
        ("grp/.", False),
        ("grp/..", False),
        ("grp/.git", False),
        ("grp/.GIT", False),
        ("grp/Proj.GIT", True),
        ("grp/x.git", False),
        ("grp/x.atom", False),
        (".git/proj", False),
        ("grp.git/proj", False),
        # ghsync's own root entries, rejected as a first segment in any case.
        (".gh_sync_tmp/proj", False),
        (".gh_archived_repos/proj", False),
        (".gh_sync_tmp", False),
        (".gh_archived_repos", False),
        (".GH_SYNC_TMP/proj", False),
        (".Gh_Archived_Repos/proj", False),
        # Only root-level entries are reserved, so nested uses are safe.
        ("grp/.gh_sync_tmp", True),
        ("grp/.gh_archived_repos/proj", True),
        (".gh_sync_tmp2/proj", True),
    ],
)
def test_path_segment_rules(env: Env, tmp_path: Path, path: str, kept: bool) -> None:
    saved = tmp_path / "list.txt"
    saved.write_text(f"{path}\n", newline="\n")
    res = env.run("--host", HOST, "--from-list", str(saved), "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == ([path] if kept else [])
    assert ("skipping unsafe repository path" in res.stderr) is not kept
