"""Tests for the GitLab provider seam (lib/gitlab_cli.py) and its first
consumer, the GitLab path of lib/review_reality.py.

`glab` and `gh` are shell stubs in a private bin directory that is the whole
PATH (with symlinks to the few real tools the stubs and git need), so no real
`glab` or `gh` further down PATH can be reached and every test runs offline.
The `glab` stub logs each call and answers `glab api` from JSON files keyed by
the endpoint; a `<key>.fail` file makes that endpoint fail with its contents on
stderr.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import skip_without_posix_shell

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib import gitlab_cli  # noqa: E402
from lib.gh_cli import GhUnavailable  # noqa: E402
from lib.gitlab_cli import (  # noqa: E402
    GitlabProject, glab_api, normalize_host, open_gitlab, parse_remote,
)
from lib.review_reality import scan_review_reality  # noqa: E402

FAKE_GLAB = """#!/bin/sh
echo "$*" >> "$FAKE_GLAB/log"
case "$1" in
  auth)
    [ -f "$FAKE_GLAB/noauth" ] || exit 0
    cat "$FAKE_GLAB/noauth" >&2; exit 1 ;;
  api)
    key=$(echo "$4" | tr '/?&=%' '_____')
    [ -f "$FAKE_GLAB/$key.fail" ] && { cat "$FAKE_GLAB/$key.fail" >&2; exit 1; }
    [ -f "$FAKE_GLAB/$key" ] && { cat "$FAKE_GLAB/$key"; exit 0; }
    echo "glab: 404 Not Found (HTTP 404)" >&2; exit 1 ;;
esac
exit 1
"""

FAKE_GH = """#!/bin/sh
echo "$*" >> "$FAKE_GLAB/ghlog"
exit 1
"""

PROJECT = "grp/sub/app"
ENC = "grp%2Fsub%2Fapp"
MRS = f"projects/{ENC}/merge_requests?state=merged&order_by=created_at&sort=desc&per_page=30"

ALICE = {"username": "Alice"}
BOB = {"username": "bob"}


def _key(endpoint: str) -> str:
    return endpoint.translate(str.maketrans("/?&=%", "_____"))


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _mr(iid: int, merger: dict[str, str] | None = None,
        merged_at: str = "2026-09-01T00:00:00Z") -> dict[str, object]:
    return {"iid": iid, "title": f"SECRET-TITLE-{iid}", "author": ALICE,
            "merge_user": merger or ALICE, "merged_at": merged_at}


def _approvals(*users: dict[str, str]) -> dict[str, object]:
    return {"approved": bool(users), "approved_by": [{"user": u} for u in users]}


class World:
    def __init__(self, root: Path, data: Path, bindir: Path) -> None:
        self.root, self.data, self.bindir = root, data, bindir

    def remote(self, url: str) -> None:
        _git(self.root, "remote", "add", "origin", url)

    def serve(self, endpoint: str, doc: object) -> None:
        (self.data / _key(endpoint)).write_text(json.dumps(doc))

    def fail(self, endpoint: str, line: str) -> None:
        (self.data / f"{_key(endpoint)}.fail").write_text(line + "\n")

    def no_auth(self, line: str = "x gitlab.com: api call failed: 401 Unauthorized") -> None:
        (self.data / "noauth").write_text(line + "\n")

    def without(self, tool: str) -> None:
        (self.bindir / tool).unlink()

    def calls(self, log: str = "log") -> list[str]:
        path = self.data / log
        return path.read_text().splitlines() if path.exists() else []

    def merged(self, mrs: list[dict[str, object]], approvals: dict[int, object]) -> None:
        self.serve(MRS, mrs)
        for iid, doc in approvals.items():
            self.serve(f"projects/{ENC}/merge_requests/{iid}/approvals", doc)

    def policy(self, rules: object, branch: str = "main") -> None:
        self.serve(f"projects/{ENC}", {"default_branch": branch})
        self.serve(f"projects/{ENC}/approval_rules?per_page=100", rules)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    skip_without_posix_shell()
    repo, data, bindir = tmp_path / "repo", tmp_path / "glab", tmp_path / "bin"
    for d in (repo, data, bindir):
        d.mkdir()
    real_git = shutil.which("git")
    assert real_git
    _git(repo, "init", "-q", "-b", "main")
    for tool in ("git", "cat", "tr", "sh"):
        found = shutil.which(tool)
        assert found, tool
        (bindir / tool).symlink_to(found)
    for name, body in (("glab", FAKE_GLAB), ("gh", FAKE_GH)):
        stub = bindir / name
        stub.write_text(body)
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setenv("FAKE_GLAB", str(data))
    return World(repo, data, bindir)


# ---- remote parsing and host normalisation ---------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://gitlab.com/grp/sub/app.git", ("gitlab.com", "grp/sub/app")),
    ("https://oauth2:tok@gitlab.example.com:8443/grp/app/", ("gitlab.example.com:8443", "grp/app")),
    ("http://GitLab.Example.com/grp/app", ("gitlab.example.com", "grp/app")),
    ("git@gitlab.com:grp/.dotfiles.git", ("gitlab.com", "grp/.dotfiles")),
    ("ssh://git@gitlab.example.com:2222/grp/sub/app.git", ("gitlab.example.com", "grp/sub/app")),
    ("https://github.com/acme/widget.git", ("github.com", "acme/widget")),
])
def test_parse_remote_forms(url: str, expected: tuple[str, str]) -> None:
    assert parse_remote(url) == expected


@pytest.mark.parametrize("url", [
    "https://gitlab.com/app.git",           # one segment: not a project
    "https://gitlab.com/grp/../app",        # a dots-only segment
    "https://evil host/grp/app",            # host outside the strict charset
    "git@-oProxyCommand=x:grp/app",         # an option smuggled as a host
    "/srv/git/grp/app.git",                 # a local path
    "",
])
def test_parse_remote_rejects(url: str) -> None:
    assert parse_remote(url) is None


@pytest.mark.parametrize("raw,expected", [
    ("https://gitlab.example.com/", "gitlab.example.com"),
    ("http://gitlab.example.com/x/y", "gitlab.example.com"),
    ("GitLab.Example.com:8443", "gitlab.example.com:8443"),
    ("", ""),
])
def test_normalize_host_ports_ghsync(raw: str, expected: str) -> None:
    assert normalize_host(raw) == expected


def test_project_path_is_url_encoded() -> None:
    assert GitlabProject("gitlab.com", PROJECT).encoded == ENC


# ---- detection --------------------------------------------------------------

def test_github_remote_never_invokes_glab(world: World) -> None:
    world.remote("https://github.com/acme/widget.git")
    assert open_gitlab(world.root) is None
    assert world.calls() == []


def test_no_remote_never_invokes_glab(world: World) -> None:
    assert open_gitlab(world.root) is None
    assert world.calls() == []


def test_gitlab_com_remote_is_gitlab_after_auth(world: World) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    assert open_gitlab(world.root) == GitlabProject("gitlab.com", PROJECT)
    assert world.calls() == ["auth status --hostname gitlab.com"]


def test_self_managed_host_is_gitlab_when_glab_is_logged_in(world: World) -> None:
    world.remote(f"https://gitlab.example.com/{PROJECT}.git")
    assert open_gitlab(world.root) == GitlabProject("gitlab.example.com", PROJECT)
    assert world.calls() == ["auth status --hostname gitlab.example.com"]


@pytest.mark.parametrize("break_glab", ["noauth", "missing"])
def test_self_managed_host_without_glab_is_not_gitlab(world: World, break_glab: str) -> None:
    world.remote(f"https://git.example.com/{PROJECT}.git")
    if break_glab == "noauth":
        world.no_auth()
    else:
        world.without("glab")
    assert open_gitlab(world.root) is None


@pytest.mark.parametrize("stderr,reason", [
    ("x gitlab.com: api call failed: 401 Unauthorized", "not_authenticated"),
    ("dial tcp: lookup gitlab.com: no such host", "unreachable"),
])
def test_gitlab_com_auth_failure_is_unavailable(world: World, stderr: str, reason: str) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    world.no_auth(stderr)
    with pytest.raises(GhUnavailable) as e:
        open_gitlab(world.root)
    assert e.value.reason.startswith(reason)


def test_glab_api_maps_refusals(world: World) -> None:
    project = GitlabProject("gitlab.com", PROJECT)
    world.fail("projects/x", "glab: 403 Forbidden (HTTP 403)")
    world.fail("projects/y", "ERROR: 404 Project Not Found")
    world.fail("projects/z", "boom")
    (world.data / _key("projects/bad")).write_text("not json")
    for path, reason in (("projects/x", "no_access"), ("projects/y", "not_found"),
                         ("projects/z", "glab_error"), ("projects/bad", "glab_bad_json")):
        with pytest.raises(GhUnavailable) as e:
            glab_api(project, path)
        assert e.value.reason.startswith(reason), path
    assert all("--hostname gitlab.com" in c for c in world.calls())


def test_glab_timeout_is_unavailable(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="glab", timeout=1)
    monkeypatch.setattr(gitlab_cli.subprocess, "run", hang)
    with pytest.raises(GhUnavailable) as e:
        gitlab_cli.glab_auth("gitlab.com")
    assert e.value.reason.startswith("unreachable")
    with pytest.raises(GhUnavailable) as e:
        glab_api(GitlabProject("gitlab.com", PROJECT), "projects/x")
    assert e.value.reason.startswith("glab_timeout")


# ---- review_reality on GitLab ------------------------------------------------

def test_gitlab_review_reality_is_available(world: World) -> None:
    world.remote(f"https://gitlab.com/{PROJECT}.git")
    mrs = [_mr(i, merger=BOB if i < 4 else None) for i in range(1, 11)]
    approvals: dict[int, object] = {i: _approvals(BOB) for i in range(1, 4)}
    approvals.update({i: _approvals() for i in range(4, 10)})
    approvals[10] = _approvals({"username": "alice"})  # the author's own approval
    world.merged(mrs, approvals)
    world.policy([{"rule_type": "regular", "approvals_required": 1, "protected_branches": []}])
    block = scan_review_reality(world.root)
    del block["oldest_merged_days_ago"]
    assert block == {
        "available": True, "merged_count": 10, "reviewed_share": None,
        "approved_share": 0.3, "bot_review_share": None, "self_merged_share": 0.7,
        "review_required": True, "hollow_required_review": False,
        "required_approval_bypassed": False,
    }
    assert "SECRET" not in json.dumps(block) and "bob" not in json.dumps(block)
    assert world.calls("ghlog") == []


def test_gitlab_unapproved_required_review_is_bypassed(world: World) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    world.merged([_mr(i) for i in range(1, 6)], {i: _approvals() for i in range(1, 6)})
    world.policy([
        {"rule_type": "report_approver", "approvals_required": 2, "protected_branches": []},
        {"rule_type": "regular", "approvals_required": 1,
         "protected_branches": [{"name": "release/*"}, {"name": "ma*"}]},
    ])
    block = scan_review_reality(world.root)
    assert block["review_required"] is True
    assert block["required_approval_bypassed"] is True
    # Approvals alone cannot show nobody reviewed: unknown, not True.
    assert block["hollow_required_review"] is None


@pytest.mark.parametrize("rules,expected", [
    ([], False),
    ([{"rule_type": "regular", "approvals_required": 0, "protected_branches": []}], False),
    ([{"rule_type": "regular", "approvals_required": 1,
       "protected_branches": [{"name": "release/*"}]}], False),
    ([{"rule_type": "any_approver", "approvals_required": 1,
       "applies_to_all_protected_branches": True, "protected_branches": []}], None),
    ([{"approvals_required": 1, "protected_branches": [{"id": 3}]}], None),
    ([{"approvals_required": 1, "protected_branches": "main"}], None),
    (["junk"], None),
    ({"message": "not a list"}, None),
])
def test_gitlab_review_policy(world: World, rules: object, expected: bool | None) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    world.merged([_mr(i) for i in range(1, 6)], {i: _approvals(BOB) for i in range(1, 6)})
    world.policy(rules)
    block = scan_review_reality(world.root)
    assert block["review_required"] is expected
    if expected is False:
        assert block["required_approval_bypassed"] is False


def test_gitlab_refused_policy_read_is_unknown_not_false(world: World) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    world.merged([_mr(i) for i in range(1, 6)], {i: _approvals() for i in range(1, 6)})
    world.serve(f"projects/{ENC}", {"default_branch": "main"})
    world.fail(f"projects/{ENC}/approval_rules?per_page=100", "glab: 403 Forbidden (HTTP 403)")
    block = scan_review_reality(world.root)
    assert block["available"] is True
    assert block["review_required"] is None
    assert block["required_approval_bypassed"] is None


@pytest.mark.parametrize("code,reason", [("403", "no_access"), ("404", "not_found")])
def test_gitlab_refused_mr_list_is_unavailable(world: World, code: str, reason: str) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    world.fail(MRS, f"glab: refused (HTTP {code})")
    block = scan_review_reality(world.root)
    assert block["available"] is False
    assert block["reason"].startswith(reason)


@pytest.mark.parametrize("code,reason", [("403", "no_access"), ("404", "not_found")])
def test_gitlab_refused_approvals_read_is_unavailable(world: World, code: str, reason: str) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    world.merged([_mr(1), _mr(2)], {1: _approvals(BOB)})
    world.fail(f"projects/{ENC}/merge_requests/2/approvals", f"glab: refused (HTTP {code})")
    block = scan_review_reality(world.root)
    assert block == {"available": False, "reason": block["reason"]}
    assert block["reason"].startswith(reason)


@pytest.mark.parametrize("mrs,approvals", [
    ({"message": "x"}, {}),
    ([{"title": "no iid"}], {}),
    ([_mr(1)], {1: ["not", "an", "object"]}),
])
def test_gitlab_malformed_reads_are_unavailable(world: World, mrs: object,
                                                approvals: dict[int, object]) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    world.serve(MRS, mrs)
    for iid, doc in approvals.items():
        world.serve(f"projects/{ENC}/merge_requests/{iid}/approvals", doc)
    block = scan_review_reality(world.root)
    assert block["available"] is False
    assert block["reason"].startswith("glab_bad_json")


def test_gitlab_no_merged_mrs(world: World) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    world.merged([], {})
    world.policy([])
    block = scan_review_reality(world.root)
    assert block["available"] is True
    assert block["merged_count"] == 0
    assert block["approved_share"] is None
    assert block["oldest_merged_days_ago"] is None


def test_gitlab_merged_by_fallback_and_age(world: World) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    mr = {"iid": 1, "author": ALICE, "merged_by": ALICE, "merged_at": "2000-01-01T00:00:00Z"}
    world.merged([mr], {1: _approvals(BOB)})
    world.policy([])
    block = scan_review_reality(world.root)
    assert block["self_merged_share"] == 1.0
    assert block["oldest_merged_days_ago"] > 9000


def test_gitlab_com_without_glab_degrades_like_github_without_gh(world: World) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    world.without("glab")
    block = scan_review_reality(world.root)
    assert block["available"] is False
    assert block["reason"].startswith("glab_not_installed")
    assert set(block) == {"available", "reason"}


def test_gitlab_com_unauthenticated_is_unavailable(world: World) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    world.no_auth()
    block = scan_review_reality(world.root)
    assert block["reason"].startswith("not_authenticated")


@pytest.mark.parametrize("break_glab", ["noauth", "missing"])
def test_unknown_host_without_glab_degrades_exactly_as_before(world: World,
                                                              break_glab: str) -> None:
    world.remote(f"https://git.example.com/{PROJECT}.git")
    if break_glab == "noauth":
        world.no_auth()
    else:
        world.without("glab")
    assert scan_review_reality(world.root) == {
        "available": False, "reason": "no_remote: no github.com remote to compare against",
    }
    assert world.calls("ghlog") == []


def test_github_remote_still_uses_gh_only(world: World) -> None:
    world.remote("https://github.com/acme/widget.git")
    block = scan_review_reality(world.root)
    assert block["available"] is False
    assert world.calls() == []
    assert world.calls("ghlog")  # the gh path ran (the stub refuses everything)
