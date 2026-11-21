"""Behavioural contract for ghsync's discovery metadata: --porcelain-json
and --active-since, on both providers.

Runs the real `ghsync.sh` offline against `gh` / `glab` stubs that serve
canned JSON from a data directory, keyed on the API endpoint. The `gh`
stub applies `--jq` with the real jq, as gh does, so the plain discovery
path sees exactly what it would see live. Dates are generated relative to
the real clock: "recent" is days ago, "old" is years ago, so a 6mo window
separates them with a wide margin and no clock hook is needed.
"""
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from gitbash import BASH, base_env

REPO = Path(__file__).resolve().parent.parent
GHSYNC = REPO / "skills" / "ghsync" / "scripts" / "ghsync.sh"
ORG = "testorg"
HOST = "gl.test"
KEYS = {
    "path", "default_branch", "last_activity_at", "head_committed_at",
    "archived", "forked_from", "mirror", "head_sha",
}

NOW = datetime.now(timezone.utc)
RECENT = (NOW - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
# GitLab style: fractional seconds and an offset.
RECENT_GL = (NOW - timedelta(days=2)).astimezone(
    timezone(timedelta(hours=-7))).strftime("%Y-%m-%dT%H:%M:%S.000-07:00")
OLD = (NOW - timedelta(days=800)).strftime("%Y-%m-%dT%H:%M:%SZ")

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="requires bash and jq on PATH",
)

# Serves $DATA/<endpoint with / as __>.json; a missing file is a 404.
# Query strings are dropped, %2F is decoded. --jq is applied with jq.
STUB = r"""#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$STUB_LOG"
cmd=$1; shift
case "$cmd" in
  auth) exit 0 ;;
  repo)
    [ "$1" = list ] && { cat "$DATA/repo_list.json"; exit 0; }
    exit 1 ;;
  api)
    endpoint=""; jqexpr=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --paginate) shift ;;
        --hostname|-h) shift 2 ;;
        --jq) jqexpr=$2; shift 2 ;;
        *) endpoint=$1; shift ;;
      esac
    done
    endpoint=${endpoint%%\?*}
    endpoint=${endpoint//%2F//}
    key=${endpoint//\//__}
    f="$DATA/$key.json"
    [ -f "$f" ] || { echo "404 Not Found: $endpoint" >&2; exit 1; }
    if [ -n "$jqexpr" ]; then (set -o pipefail; jq -r "$jqexpr" "$f" | tr -d '\r'); else cat "$f"; fi ;;
  *) exit 1 ;;
esac
"""


def gh_commit(sha: str, date: str) -> dict:
    return {"sha": sha, "commit": {"committer": {"date": date}}}


def gl_branch(sha: str, date: str) -> dict:
    return {"name": "main", "commit": {"id": sha, "committed_date": date}}


def gh_list_entry(name: str, pushed: str, **extra: object) -> dict:
    entry = {
        "name": name, "isArchived": False, "defaultBranchRef": {"name": "main"},
        "pushedAt": pushed, "isFork": False, "isMirror": False, "parent": None,
    }
    entry.update(extra)
    return entry


GITHUB_DATA = {
    "users__testorg": {"type": "Organization"},
    "user__teams": [{"slug": "core", "organization": {"login": ORG}}],
    "orgs__testorg__teams__core__repos": [
        # Only in the team: a fork whose parent the team listing omits.
        {"name": "team-fork", "archived": False, "default_branch": "main",
         "pushed_at": RECENT, "fork": True, "mirror_url": None},
        # Also in the org list: one output line, merged metadata.
        {"name": "both", "archived": False, "default_branch": "main",
         "pushed_at": RECENT, "fork": False, "mirror_url": None},
        {"name": "team-archived", "archived": True, "default_branch": "main",
         "pushed_at": OLD, "fork": False, "mirror_url": None},
    ],
    "repo_list": [
        gh_list_entry("active", RECENT),
        gh_list_entry("both", RECENT),
        # Pushed recently (another branch), default-branch head is old.
        gh_list_entry("busy-old-head", RECENT),
        gh_list_entry("dormant", OLD),
        gh_list_entry("mirror-one", RECENT, isMirror=True),
        gh_list_entry("forked", RECENT, isFork=True,
                      parent={"name": "forked", "owner": {"login": "upstream"}}),
        gh_list_entry("nohead", RECENT),
        gh_list_entry("arch-one", RECENT, isArchived=True),
        gh_list_entry("empty-one", RECENT, defaultBranchRef=None),
    ],
    "repos__testorg__team-fork": {"parent": {"full_name": "upstream/team-fork"}},
    "repos__testorg__active__commits__main": gh_commit("a1", RECENT),
    "repos__testorg__both__commits__main": gh_commit("b1", RECENT),
    "repos__testorg__busy-old-head__commits__main": gh_commit("c1", OLD),
    "repos__testorg__dormant__commits__main": gh_commit("d1", OLD),
    "repos__testorg__mirror-one__commits__main": gh_commit("e1", RECENT),
    "repos__testorg__forked__commits__main": gh_commit("f1", RECENT),
    "repos__testorg__team-fork__commits__main": gh_commit("g1", RECENT),
}
GITHUB_ALL = ["active", "both", "busy-old-head", "dormant", "forked",
              "mirror-one", "nohead", "team-fork"]
GITHUB_ACTIVE = ["active", "both", "forked", "mirror-one", "team-fork"]


def gl_project(pid: int, path: str, activity: str, **extra: object) -> dict:
    project = {"id": pid, "path_with_namespace": path, "default_branch": "main",
               "last_activity_at": activity, "archived": False,
               "empty_repo": False}
    project.update(extra)
    return project


SHARED_FORK = gl_project(4, "grp/sub/fork", RECENT, mirror=False,
                         forked_from_project={"path_with_namespace": "up/fork"})
GITLAB_DATA = {
    "groups": [{"id": 1, "full_path": "grp"}],
    "groups__1__descendant_groups": [{"id": 2, "full_path": "grp/sub"}],
    "groups__1__projects": [
        # No mirror key: the role cannot read it, so it is null.
        gl_project(1, "grp/active", RECENT),
        # Issue activity is recent; the default branch is not.
        gl_project(2, "grp/busy", RECENT, mirror=False),
        gl_project(3, "grp/dormant", OLD, mirror=False),
        gl_project(9, "grp/old", RECENT, archived=True),
    ],
    "groups__1__projects__shared": [SHARED_FORK],
    "groups__2__projects": [SHARED_FORK, gl_project(5, "grp/sub/quiet", RECENT, mirror=True)],
    "groups__2__projects__shared": [],
    "projects__1__repository__branches__main": gl_branch("a1", RECENT_GL),
    "projects__2__repository__branches__main": gl_branch("b1", OLD),
    "projects__3__repository__branches__main": gl_branch("c1", OLD),
    "projects__4__repository__branches__main": gl_branch("d1", RECENT),
    "projects__5__repository__branches__main": gl_branch("e1", OLD),
}
GITLAB_ALL = ["grp/active", "grp/busy", "grp/dormant", "grp/sub/fork", "grp/sub/quiet"]
GITLAB_ACTIVE = ["grp/active", "grp/sub/fork"]


class Env:
    def __init__(self, tmp_path: Path, provider: str) -> None:
        self.tmp = tmp_path
        self.provider = provider
        self.root = tmp_path / "root"
        self.root.mkdir()
        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        for name in ("gh", "glab"):
            stub = stub_dir / name
            stub.write_text(STUB, newline="\n")
            stub.chmod(0o755)
        data = tmp_path / "data"
        data.mkdir()
        for key, body in (GITHUB_DATA if provider == "github" else GITLAB_DATA).items():
            (data / f"{key}.json").write_text(json.dumps(body), newline="\n")
        self.log = tmp_path / "stub.log"
        self.env = {
            **base_env(),
            "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "DATA": str(data),
            "STUB_LOG": str(self.log),
        }

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        if self.provider == "github":
            base = ["--org", ORG]
        else:
            base = ["--provider", "gitlab", "--host", HOST]
        return subprocess.run(
            [BASH, str(GHSYNC), *base, "--root", str(self.root), *args],
            capture_output=True, text=True, env=self.env, cwd=self.tmp, timeout=120,
        )

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def head_calls(self) -> list[str]:
        return [c for c in self.calls()
                if "/commits/" in c or "/repository/branches/" in c]


@pytest.fixture(params=["github", "gitlab"])
def env(request: pytest.FixtureRequest, tmp_path: Path) -> Env:
    return Env(tmp_path, request.param)


def _expected(env: Env, active: bool) -> list[str]:
    if env.provider == "github":
        return GITHUB_ACTIVE if active else GITHUB_ALL
    return GITLAB_ACTIVE if active else GITLAB_ALL


def _json_lines(stdout: str) -> list[dict]:
    return [json.loads(line) for line in stdout.splitlines()]


def test_plain_porcelain_is_unchanged_and_reads_no_heads(env: Env) -> None:
    res = env.run("--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == _expected(env, active=False)
    assert env.head_calls() == []


def test_active_since_is_a_strict_nonempty_subset(env: Env) -> None:
    plain = env.run("--porcelain")
    active = env.run("--porcelain", "--active-since", "6mo")
    assert active.returncode == 0, active.stderr
    kept = active.stdout.splitlines()
    assert kept == _expected(env, active=True)
    assert kept and set(kept) < set(plain.stdout.splitlines())
    assert kept == [p for p in plain.stdout.splitlines() if p in kept]


def test_recent_activity_with_old_head_is_excluded(env: Env) -> None:
    busy = "busy-old-head" if env.provider == "github" else "grp/busy"
    res = env.run("--porcelain", "--active-since", "90d")
    assert res.returncode == 0, res.stderr
    assert busy not in res.stdout.splitlines()
    # It passed the prefilter, so its head was read.
    assert any(busy in c or "projects/2/" in c for c in env.head_calls())


def test_old_activity_is_excluded_without_a_head_call(env: Env) -> None:
    res = env.run("--porcelain", "--active-since", "6mo")
    assert res.returncode == 0, res.stderr
    dormant = "dormant" if env.provider == "github" else "grp/dormant"
    assert dormant not in res.stdout.splitlines()
    marker = "testorg/dormant/" if env.provider == "github" else "projects/3/"
    assert not [c for c in env.head_calls() if marker in c]


def test_porcelain_json_lines_carry_every_field(env: Env) -> None:
    res = env.run("--porcelain-json")
    assert res.returncode == 0, res.stderr
    rows = _json_lines(res.stdout)
    assert [r["path"] for r in rows] == _expected(env, active=False)
    for row in rows:
        assert set(row) == KEYS, row
        assert row["archived"] is False
        assert row["default_branch"] == "main"
    by_path = {r["path"]: r for r in rows}
    if env.provider == "github":
        assert by_path["active"]["head_sha"] == "a1"
        assert by_path["active"]["head_committed_at"] == RECENT
        assert by_path["active"]["last_activity_at"] == RECENT
        assert by_path["active"]["mirror"] is False
        assert by_path["active"]["forked_from"] is None
        assert by_path["mirror-one"]["mirror"] is True
        assert by_path["forked"]["forked_from"] == "upstream/forked"
        assert by_path["team-fork"]["forked_from"] == "upstream/team-fork"
        assert by_path["nohead"]["head_sha"] is None
        assert by_path["nohead"]["head_committed_at"] is None
    else:
        assert by_path["grp/active"]["head_sha"] == "a1"
        assert by_path["grp/active"]["head_committed_at"] == RECENT_GL
        assert by_path["grp/active"]["mirror"] is None
        assert by_path["grp/busy"]["mirror"] is False
        assert by_path["grp/sub/quiet"]["mirror"] is True
        assert by_path["grp/sub/fork"]["forked_from"] == "up/fork"
        assert by_path["grp/active"]["forked_from"] is None
    assert "Reading the default-branch head" in res.stderr


def test_porcelain_json_with_active_since(env: Env) -> None:
    res = env.run("--porcelain-json", "--active-since", "6mo")
    assert res.returncode == 0, res.stderr
    rows = _json_lines(res.stdout)
    assert [r["path"] for r in rows] == _expected(env, active=True)
    assert all(set(r) == KEYS for r in rows)


def test_github_union_and_team_only_fork_parent_lookup(tmp_path: Path) -> None:
    env = Env(tmp_path, "github")
    res = env.run("--porcelain-json")
    assert res.returncode == 0, res.stderr
    paths = [r["path"] for r in _json_lines(res.stdout)]
    assert paths.count("both") == 1
    lookups = [c for c in env.calls() if c.startswith("gh api repos/testorg/")
               and "/commits/" not in c]
    assert lookups == ["gh api repos/testorg/team-fork"], lookups


def test_unreadable_head_is_excluded_with_a_warning(tmp_path: Path) -> None:
    env = Env(tmp_path, "github")
    res = env.run("--porcelain", "--active-since", "6mo")
    assert res.returncode == 0, res.stderr
    assert "nohead" not in res.stdout.splitlines()
    assert "could not read the default-branch head of nohead" in res.stderr


def test_dry_run_counts_per_group(tmp_path: Path) -> None:
    env = Env(tmp_path, "gitlab")
    res = env.run("--dry-run", "--quiet", "--active-since", "6mo")
    assert res.returncode == 0, res.stdout + res.stderr
    # grp lists active, busy, dormant and the shared fork; grp/sub lists
    # the fork and quiet. The fork counts in both groups.
    assert "  grp: 2 of 4 active" in res.stdout
    assert "  grp/sub: 1 of 2 active" in res.stdout
    assert "2 of 5 repositories have a default-branch commit" in res.stdout
    assert {p.name for p in env.root.iterdir()} == set()


def test_dry_run_counts_one_line_for_github_org(tmp_path: Path) -> None:
    env = Env(tmp_path, "github")
    res = env.run("--dry-run", "--quiet", "--active-since", "6mo")
    assert res.returncode == 0, res.stdout + res.stderr
    counts = [ln for ln in res.stdout.splitlines() if ln.endswith(" active")]
    assert counts == [f"  {ORG}: 5 of 8 active"]


def test_gitlab_preview_with_new_flags_leaves_root_empty(tmp_path: Path) -> None:
    env = Env(tmp_path, "gitlab")
    for args in (["--porcelain-json"], ["--porcelain", "--active-since", "6mo"]):
        assert env.run(*args).returncode == 0
    assert list(env.root.iterdir()) == []


def test_nothing_active_says_so(tmp_path: Path) -> None:
    env = Env(tmp_path, "gitlab")
    res = env.run("--porcelain", "--active-since", "1d")
    assert res.returncode == 0, res.stderr
    assert res.stdout == ""
    assert "No repositories have a default-branch commit since" in res.stderr


@pytest.mark.parametrize(
    "args",
    [["--active-since"], ["--active-since="], ["--active-since", "6"],
     ["--active-since", "6m"], ["--active-since", "0d"],
     ["--active-since", "--porcelain"], ["--active-since", "-3d"]],
)
def test_bad_duration_fails_before_any_api_call(tmp_path: Path, args: list[str]) -> None:
    env = Env(tmp_path, "github")
    res = env.run(*args)
    assert res.returncode != 0
    assert "--active-since needs a duration" in res.stdout + res.stderr
    assert env.calls() == []


@pytest.mark.parametrize("flag", [["--porcelain-json"], ["--active-since", "6mo"]])
def test_from_list_rejects_metadata_flags(tmp_path: Path, flag: list[str]) -> None:
    env = Env(tmp_path, "gitlab")
    listing = tmp_path / "list.txt"
    listing.write_text("grp/active\n", newline="\n")
    res = env.run("--from-list", str(listing), *flag)
    assert res.returncode != 0
    assert "--from-list" in res.stderr
    assert env.calls() == []
