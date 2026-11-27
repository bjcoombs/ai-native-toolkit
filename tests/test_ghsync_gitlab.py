"""ghsync's GitLab provider: discovery, fail-fast, flag validation and side effects.

The fixture and stubs live in `ghsync_gitlab_env.py`.
"""

import json
import subprocess
from pathlib import Path

import pytest
from ghsync_gitlab_env import (
    GHSYNC,
    HOST,
    SUB_TREE,
    SYNCED,
    Env,
    _member_of_sub_only,
    _path_without_glab,
    _project,
    _serve,
)
from gitbash import BASH


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


# ---- discovery --------------------------------------------------------------


def test_porcelain_lists_every_project_once(env: Env) -> None:
    res = env.run("--host", HOST, "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == SYNCED
    # Progress goes to stderr, and the paginated call carries --paginate.
    assert "Host:" in res.stderr
    assert all("--paginate" in call for call in env.api_calls())
    assert not env.gh_log.exists(), env.gh_log.read_text()


def test_list_only_runs_leave_no_root(env: Env) -> None:
    """--porcelain and --list-repos create no root and write no cache."""
    for flag in ("--porcelain", "--list-repos"):
        res = env.run("--host", HOST, flag)
        assert res.returncode == 0, res.stderr
    assert not (env.tmp / "dev").exists()
    res = env.run("--host", HOST, "--porcelain", root=env.root)
    assert res.returncode == 0, res.stderr
    assert list(env.root.iterdir()) == []


def test_default_root_is_dev_host(env: Env) -> None:
    res = env.run("--host", HOST, "--quiet", "--group", "grp/sub/deep")
    assert res.returncode == 0, res.stdout + res.stderr
    root = env.tmp / "dev" / HOST
    assert (env.checkout("grp/sub/deep/gamma", root) / ".git").is_dir()
    assert (root / ".gh_archived_repos").read_text() == ""


def test_explicit_missing_root_fails(env: Env) -> None:
    missing = env.tmp / "typo-root"
    res = env.sync_at(missing)
    assert res.returncode != 0
    out = res.stdout + res.stderr
    assert "root directory does not exist" in out
    assert f"mkdir -p \"{missing}\"" in out
    assert not missing.exists()


def test_ssh_clone_url_drops_web_port(env: Env, tmp_path: Path) -> None:
    """--host host:8443 keeps the port for HTTPS but not in the scp-style SSH URL."""
    saved = tmp_path / "list.txt"
    saved.write_text("grp/alpha\n", newline="\n")
    res = env.run(
        "--host",
        f"{HOST}:8443",
        "--from-list",
        str(saved),
        "--quiet",
        env={"GHSYNC_GIT_PROTOCOL": "ssh"},
        root=env.root,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    checkout = env.checkout("grp/alpha")
    assert (
        env.git("config", "remote.origin.url", cwd=checkout)
        == f"git@{HOST}:grp/alpha.git"
    )


def test_list_repos_names_each_project(env: Env) -> None:
    res = env.run("--host", HOST, "--list-repos")
    assert res.returncode == 0, res.stderr
    for path in SYNCED:
        assert res.stdout.count(f"  - {path}\n") == 1, path
    assert "grp/old" not in res.stdout


def test_host_with_scheme_is_normalised(env: Env) -> None:
    res = env.run("--host", f"https://{HOST}/", "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == SYNCED
    assert not (env.tmp / "dev").exists()


def test_group_narrows_to_subtree(env: Env) -> None:
    res = env.run("--host", HOST, "--group", "grp/sub", "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == SUB_TREE


def test_group_naming_a_leaf_subgroup(env: Env) -> None:
    res = env.run("--host", HOST, "--group=grp/sub/deep", "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == ["grp/sub/deep/gamma"]


def test_empty_group_finds_nothing(env: Env) -> None:
    res = env.run("--host", HOST, "--group", "grp/empty", "--list-repos")
    assert res.returncode == 0, res.stderr
    assert "No repositories found" in res.stdout


def test_group_outside_membership_is_looked_up_directly(env: Env) -> None:
    """A group the user is not a member of is still reachable by path."""
    groups = Path(env.env["GLAB_DATA"]) / "groups.1.json"
    groups.write_text("[]", newline="\n")
    res = env.run("--host", HOST, "--group", "grp/sub", "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == SUB_TREE


def test_unknown_group_fails(env: Env) -> None:
    res = env.run("--host", HOST, "--group", "nope", "--list-repos")
    assert res.returncode != 0
    assert "nope" in res.stdout + res.stderr


def test_missing_glab_fails_fast(env: Env, tmp_path: Path) -> None:
    res = env.run(
        "--host", HOST, "--list-repos", env={"PATH": _path_without_glab(tmp_path)}
    )
    assert res.returncode != 0
    out = res.stdout + res.stderr
    assert HOST in out and "glab" in out


@pytest.mark.parametrize(
    "host,reason",
    [
        ("gl.unreachable", "not authenticated"),
        ("gl.down", "unreachable"),
    ],
)
def test_failed_host_check_fails_fast(env: Env, host: str, reason: str) -> None:
    res = env.run("--host", host, "--list-repos")
    assert res.returncode != 0
    out = res.stdout + res.stderr
    assert host in out and reason in out, out
    assert len([ln for ln in out.splitlines() if ln.strip()]) == 1, out
    assert env.api_calls() == []
    auth_calls = [
        ln for ln in env.glab_log.read_text().splitlines() if ln.startswith("glab auth")
    ]
    assert len(auth_calls) == 1
    assert not (env.tmp / "dev" / host).exists()


def test_failed_membership_call_fails(env: Env) -> None:
    res = env.run("--host", HOST, "--list-repos", env={"GLAB_API_FAIL": "1"})
    assert res.returncode != 0
    assert HOST in res.stdout + res.stderr
    assert len(env.api_calls()) == 1


# ---- flag validation --------------------------------------------------------


@pytest.mark.parametrize(
    "args,needle",
    [
        (["--provider", "gitlab", "--org", "x"], "--org"),
        (["--provider", "gitlab", "--list-teams"], "--list-teams"),
        (["--provider", "github", "--from-list", "f"], "--from-list"),
        (["--provider", "github", "--group", "g"], "--group"),
        (["--provider", "github", "--host", "h"], "--host"),
        (["--provider", "gitlab", "--group", "g", "--from-list", "f"], "--group"),
        (["--provider", "github", "--include-starred"], "--include-starred"),
        (
            ["--provider", "gitlab", "--include-starred", "--from-list", "f"],
            "--include-starred",
        ),
        (["--provider", "bitbucket"], "bitbucket"),
    ],
)
def test_rejected_flag_combinations(env: Env, args: list[str], needle: str) -> None:
    res = subprocess.run(
        [BASH, str(GHSYNC), *args, "--list-repos"],
        capture_output=True,
        text=True,
        env=env.env,
        cwd=env.tmp,
        timeout=60,
    )
    assert res.returncode != 0
    assert needle in res.stdout + res.stderr
    assert env.api_calls() == []
    assert not env.gh_log.exists()


def test_help_documents_gitlab_flags(env: Env) -> None:
    res = subprocess.run(
        [BASH, str(GHSYNC), "--help"],
        capture_output=True,
        text=True,
        env=env.env,
        timeout=60,
    )
    assert res.returncode == 0
    for flag in (
        "--provider",
        "--host",
        "--group",
        "--from-list",
        "--porcelain",
        "--partial",
        "--filter",
        "--shallow-since",
        "--active-since",
        "--porcelain-json",
        "--include-starred",
    ):
        assert flag in res.stdout, flag
    # The whole header block prints, ending at its closing rule.
    assert res.stdout.rstrip().splitlines()[-1].startswith("# ====")
    assert "#\n# GitHub Enterprise" in res.stdout


# ---- side effects -------------------------------------------------------------


@pytest.mark.parametrize("flag", ["--dry-run", "--porcelain", "--list-repos"])
def test_preview_modes_leave_default_root_absent(env: Env, flag: str) -> None:
    res = env.run("--host", HOST, flag)
    assert res.returncode == 0, res.stdout + res.stderr
    assert not (env.tmp / "dev").exists()


@pytest.mark.parametrize("flag", ["--dry-run", "--porcelain", "--list-repos"])
def test_preview_modes_leave_explicit_root_empty(env: Env, flag: str) -> None:
    res = env.run("--host", HOST, flag, root=env.root)
    assert res.returncode == 0, res.stdout + res.stderr
    assert list(env.root.iterdir()) == []


@pytest.mark.parametrize("flag", ["--dry-run", "--porcelain", "--list-repos"])
def test_preview_modes_fail_on_missing_explicit_root(env: Env, flag: str) -> None:
    missing = env.tmp / "typo-root"
    res = env.run("--host", HOST, flag, root=missing)
    assert res.returncode != 0
    assert "root directory does not exist" in res.stdout + res.stderr
    assert not missing.exists()


def test_dry_run_reports_would_clone(env: Env) -> None:
    res = env.run("--host", HOST, "--dry-run")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "[DRY-RUN] Would clone grp/alpha" in res.stdout
    assert f"Updated ({len(SYNCED)})" in res.stdout


def test_dry_run_with_port_leaves_no_root(env: Env) -> None:
    res = env.run(
        "--host",
        f"{HOST}:8443",
        "--dry-run",
        env={"GLAB_OK_HOSTS": f"{HOST}:8443"},
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert not (env.tmp / "dev").exists()


def test_default_root_strips_port(env: Env) -> None:
    """--host gl.test:8443 syncs into ~/dev/gl.test, not ~/dev/gl.test:8443."""
    res = env.run(
        "--host",
        f"{HOST}:8443",
        "--quiet",
        "--group",
        "grp/sub/deep",
        env={"GLAB_OK_HOSTS": f"{HOST}:8443"},
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert [p.name for p in (env.tmp / "dev").iterdir()] == [HOST]
    checkout = env.checkout("grp/sub/deep/gamma", env.tmp / "dev" / HOST)
    assert (
        env.git("config", "remote.origin.url", cwd=checkout)
        == f"https://{HOST}:8443/grp/sub/deep/gamma.git"
    )


def test_group_above_membership_covers_accessible_projects(env: Env) -> None:
    """A member of grp/sub only, naming grp, gets everything visible under grp."""
    _member_of_sub_only(env)
    res = env.run("--host", HOST, "--group", "grp", "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == SYNCED
    assert "Warning" not in res.stderr


def test_group_above_membership_unreadable_names_what_is_skipped(env: Env) -> None:
    _member_of_sub_only(env)
    (Path(env.env["GLAB_DATA"]) / "groups__grp__descendant_groups.1.json").unlink()
    res = env.run("--host", HOST, "--group", "grp", "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == SUB_TREE
    warnings = [ln for ln in res.stderr.splitlines() if ln.startswith("Warning")]
    assert len(warnings) == 1, warnings
    assert f"could not read group grp on {HOST};" in warnings[0]
    assert "(grp/sub, grp/sub/deep)" in warnings[0]


def test_group_at_membership_level(env: Env) -> None:
    _member_of_sub_only(env)
    res = env.run("--host", HOST, "--group", "grp/sub", "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == SUB_TREE


def test_group_naming_a_sibling_of_membership(env: Env) -> None:
    _member_of_sub_only(env)
    res = env.run("--host", HOST, "--group", "grp/empty", "--list-repos")
    assert res.returncode == 0, res.stderr
    assert "No repositories found" in res.stdout


# ---- direct project memberships and starred projects ---------------------------


def test_direct_membership_outside_every_group_is_listed_once(env: Env) -> None:
    """A project the user was added to directly, in a namespace they hold no
    group membership in, is discovered through projects?membership=true."""
    direct = _project("solo/ns/direct")
    _serve(env, "projects/membership", [direct], [direct])
    res = env.run("--host", HOST, "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == sorted([*SYNCED, "solo/ns/direct"])
    calls = [c for c in env.api_calls() if "projects?membership=true" in c]
    assert len(calls) == 1 and "--paginate" in calls[0], calls
    assert "Warning" not in res.stderr


def test_project_reachable_by_group_and_membership_is_listed_once(env: Env) -> None:
    _serve(env, "projects/membership", [_project("grp/alpha"), _project("other/ns/shared")])
    res = env.run("--host", HOST, "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == SYNCED


def test_membership_skips_archived_and_empty_projects(env: Env) -> None:
    empty = {**_project("solo/empty"), "empty_repo": True}
    no_branch = {**_project("solo/nobranch"), "default_branch": None}
    _serve(env, "projects/membership", [_project("solo/gone", True), empty, no_branch])
    res = env.run("--host", HOST, "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == SYNCED
    assert "Found 2 archived repositories" in res.stderr


def test_group_leaves_out_membership_projects_outside_it(env: Env) -> None:
    _serve(
        env,
        "projects/membership",
        [
            _project("grp/sub/direct"),
            _project("grp/subway/direct"),
            _project("grp/alpha"),
            _project("solo/ns/direct"),
        ],
    )
    res = env.run("--host", HOST, "--group", "grp/sub", "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == sorted([*SUB_TREE, "grp/sub/direct"])


def test_unreadable_group_with_membership_under_it_syncs_those(env: Env) -> None:
    """A group the user cannot read and holds no group membership under
    still yields the projects they were added to directly inside it."""
    _serve(env, "projects/membership", [_project("hidden/team/direct"), _project("grp/alpha")])
    res = env.run("--host", HOST, "--group", "hidden", "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == ["hidden/team/direct"]
    warnings = [ln for ln in res.stderr.splitlines() if ln.startswith("Warning")]
    assert len(warnings) == 1, warnings
    assert f"could not read group hidden on {HOST}" in warnings[0]


def test_failed_membership_listing_warns_and_keeps_groups(env: Env) -> None:
    (Path(env.env["GLAB_DATA"]) / "projects__membership.1.json").unlink()
    res = env.run("--host", HOST, "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == SYNCED
    warnings = [ln for ln in res.stderr.splitlines() if ln.startswith("Warning")]
    assert len(warnings) == 1, warnings
    assert "project memberships" in warnings[0]


def test_starred_projects_only_with_include_starred(env: Env) -> None:
    starred = _project("fan/ns/starred")
    _serve(env, "projects/starred", [starred, _project("grp/alpha")], [starred])
    res = env.run("--host", HOST, "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == SYNCED
    assert not any("starred" in c for c in env.api_calls())

    res = env.run("--host", HOST, "--porcelain", "--include-starred")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == sorted([*SYNCED, "fan/ns/starred"])
    calls = [c for c in env.api_calls() if "projects?starred=true" in c]
    assert len(calls) == 1 and "--paginate" in calls[0], calls


def test_group_leaves_out_starred_projects_outside_it(env: Env) -> None:
    _serve(env, "projects/starred", [_project("grp/sub/fav"), _project("fan/ns/starred")])
    res = env.run("--host", HOST, "--group", "grp/sub", "--porcelain", "--include-starred")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == sorted([*SUB_TREE, "grp/sub/fav"])


def test_porcelain_json_carries_membership_projects(env: Env) -> None:
    _serve(env, "projects/membership", [_project("solo/ns/direct")])
    res = env.run("--host", HOST, "--porcelain-json")
    assert res.returncode == 0, res.stderr
    rows = [json.loads(ln) for ln in res.stdout.splitlines()]
    assert [r["path"] for r in rows] == sorted([*SYNCED, "solo/ns/direct"])
    direct = next(r for r in rows if r["path"] == "solo/ns/direct")
    assert direct["default_branch"] == "main" and direct["archived"] is False
