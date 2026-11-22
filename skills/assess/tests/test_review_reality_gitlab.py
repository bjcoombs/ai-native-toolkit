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
from lib.fleet_config import parse_fleet_config  # noqa: E402
from lib.platform_cli import CACHE_ENV  # noqa: E402
from lib.review_reality import scan_review_reality  # noqa: E402
from lib import merged_sample as merged_sample_module  # noqa: E402
from lib.merged_sample import merged_sample  # noqa: E402
from lib.reason_codes import reason_code  # noqa: E402

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
# The shared sample's listing (lib/merged_sample.py) with no window: newest updated first.
MRS = f"projects/{ENC}/merge_requests?state=merged&order_by=updated_at&sort=desc&per_page=20&page=1"

ALICE = {"username": "Alice"}
BOB = {"username": "bob"}


def notes_endpoint(iid: int) -> str:
    return f"projects/{ENC}/merge_requests/{iid}/notes?sort=asc&order_by=created_at&per_page=100"


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
        (self.data / _key(endpoint)).write_text(json.dumps(doc), encoding="utf-8")

    def fail(self, endpoint: str, line: str) -> None:
        (self.data / f"{_key(endpoint)}.fail").write_text(line + "\n", encoding="utf-8")

    def no_auth(self, line: str = "x gitlab.com: api call failed: 401 Unauthorized") -> None:
        (self.data / "noauth").write_text(line + "\n", encoding="utf-8")

    def without(self, tool: str) -> None:
        (self.bindir / tool).unlink()

    def calls(self, log: str = "log") -> list[str]:
        path = self.data / log
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def merged(self, mrs: list[dict[str, object]], approvals: dict[int, object],
               notes: dict[int, object] | None = None) -> None:
        """Serve the merged list, approvals and each listed merge request's notes
        (an empty list unless ``notes`` gives one)."""
        self.serve(MRS, mrs)
        for iid, doc in approvals.items():
            self.serve(f"projects/{ENC}/merge_requests/{iid}/approvals", doc)
        for mr in mrs:
            iid = mr.get("iid")
            if isinstance(iid, int):
                self.serve(notes_endpoint(iid), (notes or {}).get(iid, []))

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
        stub.write_text(body, encoding="utf-8")
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


@pytest.mark.parametrize("url,code", [
    (None, "no_remote"),
    ("not a url", "no_remote"),
    # a github.com host parse_remote reads but parse_github_remote rejects
    ("https://github.com/acme/widget/extra", "no_remote"),
    # a self-managed host glab does not reach: a platform gap, not no_remote
    ("https://git.example.com/team/app.git", "network"),
])
def test_full_and_offline_samples_give_one_reason_per_remote(world: World, url: str | None,
                                                             code: str) -> None:
    """``merged_sample`` and ``offline_merged_sample`` read the same reason from one
    remote (#908); the full read probes glab only for the self-managed host."""
    if url is not None:
        world.remote(url)
    world.no_auth()
    full = merged_sample(world.root, None, "2026-11-22")
    offline = merged_sample_module.offline_merged_sample(world.root, None, "2026-11-22")
    assert isinstance(full, dict) and isinstance(offline, dict)
    assert reason_code(full["reason"]) == reason_code(offline["reason"]) == code
    probes = [c for c in world.calls() if c.startswith("auth")]
    assert probes == (["auth status --hostname git.example.com"] if code == "network" else [])


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
    (world.data / _key("projects/bad")).write_text("not json", encoding="utf-8")
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
        "available": True, "merged_count": 10, "reviewed_share": 0.3,
        "approved_share": 0.3, "bot_review_share": 0.0, "self_merged_share": 0.7,
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
    # No approval and no note by anyone: the notes show nobody reviewed.
    assert block["hollow_required_review"] is True


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
    ([{"title": "no iid", "merged_at": "2026-09-01T00:00:00Z"}], {}),
    ([{"iid": 1, "title": "no merge time"}], {}),
    ([_mr(1)], {1: ["not", "an", "object"]}),
])
def test_gitlab_malformed_reads_are_unavailable(world: World, mrs: object,
                                                approvals: dict[int, object]) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    world.serve(MRS, mrs)
    for iid, doc in approvals.items():
        world.serve(f"projects/{ENC}/merge_requests/{iid}/approvals", doc)
        world.serve(notes_endpoint(iid), [])
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
def test_unknown_host_without_glab_is_a_platform_gap(world: World, break_glab: str) -> None:
    """The remote exists, so it reads ``network`` as the offline read does, never
    ``no_remote`` (#908); gh is still never called."""
    world.remote(f"https://git.example.com/{PROJECT}.git")
    if break_glab == "noauth":
        world.no_auth()
    else:
        world.without("glab")
    assert scan_review_reality(world.root) == {
        "available": False, "reason": merged_sample_module.UNREACHED_HOST_REASON,
    }
    assert world.calls("ghlog") == []


def test_github_remote_still_uses_gh_only(world: World) -> None:
    world.remote("https://github.com/acme/widget.git")
    block = scan_review_reality(world.root)
    assert block["available"] is False
    assert world.calls() == []
    assert world.calls("ghlog")  # the gh path ran (the stub refuses everything)


# ---- notes: AI review, human comment (#716) ---------------------------------

TOKEN_USER = {"username": "project_42_bot_4ffc0de1a2b3", "name": "SECRET-TOKEN-NAME"}
SENTINEL = "SECRET-NOTE-BODY"


def _note(author: dict[str, str], body: str, system: bool = False) -> dict[str, object]:
    return {"author": author, "body": body, "system": system,
            "created_at": "2026-09-01T10:00:00Z"}


def test_gitlab_token_user_ai_review_on_every_mr(world: World, tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv(CACHE_ENV, str(cache))
    world.remote(f"https://gitlab.example.com/{PROJECT}.git")
    mrs = [_mr(i) for i in range(1, 6)]
    notes: dict[int, object] = {
        i: [_note(ALICE, "merged", system=True), _note(TOKEN_USER, f"## AI Code Review\n{SENTINEL} {i}")]
        for i in range(1, 6)
    }
    notes[1] = [_note(TOKEN_USER, f"## AI Code Review\n{SENTINEL} 1"), _note(BOB, f"{SENTINEL} human")]
    world.merged(mrs, {i: _approvals() for i in range(1, 6)}, notes)
    world.policy([{"rule_type": "regular", "approvals_required": 1, "protected_branches": []}])
    block = scan_review_reality(world.root)
    assert block["bot_review_share"] == 1.0
    assert block["reviewed_share"] == 1.0
    assert block["approved_share"] == 0.0
    assert block["hollow_required_review"] is False
    assert block["required_approval_bypassed"] is True
    text = json.dumps(block)
    assert "SECRET" not in text and "project_42" not in text and "bob" not in text
    # Notes are read in memory only: never written to the response cache.
    cached = [f.read_text(encoding="utf-8") for f in cache.iterdir()]
    assert cached, "other platform reads are cached"
    assert not any(SENTINEL in c for c in cached)
    # Every notes call names its host and project (the cache key is argv alone).
    note_calls = [c for c in world.calls() if "/notes" in c]
    assert len(note_calls) == 5
    assert all(c.startswith("api --hostname gitlab.example.com ") and f"projects/{ENC}/" in c
               for c in note_calls)


def test_gitlab_marker_from_config(world: World) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    world.merged([_mr(1), _mr(2)], {1: _approvals(), 2: _approvals()},
                 {1: [_note({"username": "svc"}, "Reviewed by OurBot")], 2: []})
    world.policy([])
    config = parse_fleet_config('[review]\nai_review_markers = ["reviewed by ourbot"]\n')
    assert scan_review_reality(world.root, fleet_config=config)["bot_review_share"] == 0.5
    assert scan_review_reality(world.root)["bot_review_share"] == 0.0
    assert scan_review_reality(world.root)["reviewed_share"] == 0.5  # svc's note is a human comment


def _full_pages(world: World, iid: int, pages: int) -> None:
    for page in range(2, pages + 1):
        world.serve(f"{notes_endpoint(iid)}&page={page}", [_note(ALICE, "rebased")] * 100)


def test_gitlab_full_notes_pages_without_a_decision_are_unknown(world: World) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    world.merged([_mr(1)], {1: _approvals()}, {1: [_note(ALICE, "rebased")] * 100})
    _full_pages(world, 1, 3)
    world.policy([])
    block = scan_review_reality(world.root)
    assert block["available"] is True
    assert block["bot_review_share"] is None
    assert block["reviewed_share"] is None
    assert len([c for c in world.calls() if "/notes" in c]) == 3  # NOTES_MAX_PAGES, no more


def test_gitlab_second_notes_page_decides(world: World) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    world.merged([_mr(1)], {1: _approvals()}, {1: [_note(TOKEN_USER, "## AI Code Review")] * 100})
    world.serve(f"{notes_endpoint(1)}&page=2", [_note(BOB, "agreed")])
    world.policy([])
    block = scan_review_reality(world.root)
    assert block["bot_review_share"] == 1.0 and block["reviewed_share"] == 1.0


def test_gitlab_notes_stop_paging_once_decided(world: World) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    world.merged([_mr(1)], {1: _approvals()},
                 {1: [_note(TOKEN_USER, "## AI Code Review"), *[_note(BOB, "ok")] * 99]})
    world.policy([])
    assert scan_review_reality(world.root)["bot_review_share"] == 1.0
    assert len([c for c in world.calls() if "/notes" in c]) == 1


def test_gitlab_approval_decides_reviewed_despite_full_pages(world: World) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    world.merged([_mr(1)], {1: _approvals(BOB)}, {1: [_note(ALICE, "rebased")] * 100})
    _full_pages(world, 1, 3)
    world.policy([])
    assert scan_review_reality(world.root)["reviewed_share"] == 1.0


@pytest.mark.parametrize("code,reason", [("403", "no_access"), ("404", "not_found")])
def test_gitlab_refused_notes_read_is_unavailable(world: World, code: str, reason: str) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    world.merged([_mr(1)], {1: _approvals(BOB)})
    world.fail(notes_endpoint(1), f"glab: refused (HTTP {code})")
    block = scan_review_reality(world.root)
    assert block == {"available": False, "reason": block["reason"]}
    assert block["reason"].startswith(reason)


def test_gitlab_notes_not_a_list_is_unavailable(world: World) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    world.merged([_mr(1)], {1: _approvals(BOB)}, {1: {"message": "x"}})
    block = scan_review_reality(world.root)
    assert block["reason"].startswith("glab_bad_json")


def test_gitlab_delivery_and_review_reality_share_one_notes_read(world: World) -> None:
    from datetime import datetime, timedelta, timezone

    from lib.merged_sample import sample_since
    from lib.signals import collect

    now = datetime.now(timezone.utc).replace(microsecond=0)

    def stamp(t: datetime) -> str:
        return t.strftime("%Y-%m-%dT%H:%M:%SZ")

    opened = now - timedelta(days=5)
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    mrs = [{**_mr(i, merger=BOB, merged_at=stamp(opened + timedelta(hours=10 + i))),
            "created_at": stamp(opened)} for i in (1, 2)]
    since = sample_since("6mo", now.date().isoformat())
    assert since is not None
    world.serve(f"projects/{ENC}/merge_requests?state=merged&updated_after={since.isoformat()}"
                f"T00:00:00Z&order_by=updated_at&sort=desc&per_page=20&page=1", mrs)
    for i in (1, 2):
        world.serve(f"projects/{ENC}/merge_requests/{i}/approvals", _approvals())
    world.policy([])

    def note(author: dict[str, str], hours: int, body: str = "ok") -> dict[str, object]:
        return {**_note(author, body), "created_at": stamp(opened + timedelta(hours=hours))}

    world.serve(notes_endpoint(1), [note(TOKEN_USER, 1, "## AI Code Review findings"), note(BOB, 2)])
    world.serve(notes_endpoint(2), [note(ALICE, 1), note(BOB, 4)])
    scans = collect(world.root, ["review_reality", "delivery"], window="6mo", platform=True)
    review, delivery = scans["review_reality"], scans["delivery"]
    assert review["bot_review_share"] == 0.5 and review["reviewed_share"] == 1.0
    assert delivery["first_human_comment_hours_p50"] == 2.0
    assert delivery["first_human_comment_hours_p90"] == 4.0
    assert delivery["human_commented_share"] == 1.0
    assert delivery["ai_review_share"] == 0.5
    assert len([c for c in world.calls() if "/notes" in c]) == 2  # once per MR, for both blocks


def test_gitlab_coverage_bot_token_notes_are_not_review(world: World) -> None:
    world.remote(f"git@gitlab.com:{PROJECT}.git")
    world.merged([_mr(i) for i in range(1, 6)], {i: _approvals() for i in range(1, 6)},
                 {i: [_note(TOKEN_USER, "Coverage: 87.2% (+0.4%)")] for i in range(1, 6)})
    world.policy([{"rule_type": "regular", "approvals_required": 1, "protected_branches": []}])
    block = scan_review_reality(world.root)
    assert block["bot_review_share"] == 0.0
    assert block["reviewed_share"] == 0.0
    assert block["hollow_required_review"] is True  # nobody looked
