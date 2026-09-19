"""Tests for lib/review_reality.py.

GitHub is faked with a `gh` shell script first on PATH that logs every call and
answers from JSON files, so the tests run the real subprocess path through
lib/gh_cli.py, including the 403 mapping.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib.review_reality import scan_review_reality  # noqa: E402

FAKE_GH = """#!/bin/sh
echo "$*" >> "$FAKE_GH/log"
serve() { [ -f "$FAKE_GH/$1" ] && cat "$FAKE_GH/$1" && exit 0; }
case "$1:$*" in
  auth:*) [ -f "$FAKE_GH/noauth" ] || exit 0 ;;
  pr:*) serve prs.json ;;
  api:*rules/branches*) serve branchrules.json ;;
  api:*/protection*) serve protection.json ;;
  api:*users/*) serve "user-$(echo "$2" | sed 's|.*/||')" ;;
  api:*repos/acme/widget) serve apirepo.json ;;
esac
cat "$FAKE_GH/fail" >&2
exit 1
"""

PERSON = {"login": "Alice", "is_bot": False}
REVIEWER = {"login": "bob", "is_bot": False}


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _pr(n: int, *, merger=PERSON, reviews=(), comments=(), state="APPROVED",
        merged_at="2026-09-01T00:00:00Z") -> dict:
    return {
        "number": n, "title": f"SECRET-TITLE-{n}", "reviewDecision": "",
        "author": PERSON, "mergedBy": merger, "mergedAt": merged_at,
        "reviews": [{"author": a, "state": state} for a in reviews],
        "comments": [{"author": a, "body": "SECRET-BODY"} for a in comments],
    }


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "remote", "add", "origin", "https://github.com/acme/widget.git")
    bindir, ghdir = tmp_path / "bin", tmp_path / "gh"
    bindir.mkdir()
    ghdir.mkdir()
    gh = bindir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    (ghdir / "log").write_text("")
    (ghdir / "fail").write_text("gh: Not Found (HTTP 404)\n")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH", str(ghdir))

    class World:
        root = repo

        def serve(self, name: str, doc: object) -> None:
            (ghdir / name).write_text(json.dumps(doc))

        def fail_with(self, line: str) -> None:
            (ghdir / "fail").write_text(line + "\n")

        def calls(self) -> list[str]:
            return (ghdir / "log").read_text().splitlines()

    w = World()
    w.serve("apirepo.json", {"full_name": "acme/widget", "default_branch": "main"})
    return w


def _require_by_ruleset(world) -> None:
    world.serve("branchrules.json", [
        {"type": "pull_request", "parameters": {"required_approving_review_count": 1}},
    ])


def test_review_reality_unreviewed_required_review_is_hollow(world) -> None:
    _require_by_ruleset(world)
    world.serve("prs.json", [_pr(n) for n in range(10)])
    block = scan_review_reality(world.root)
    del block["oldest_merged_days_ago"]
    assert block == {
        "available": True, "merged_count": 10, "reviewed_share": 0.0,
        "approved_share": 0.0, "bot_review_share": 0.0, "self_merged_share": 1.0,
        "review_required": True, "hollow_required_review": True,
        "required_approval_bypassed": True,
    }
    # The ruleset already said yes: the protection read is skipped.
    assert not any("/protection" in c for c in world.calls())


def test_review_reality_mixed_sample_counts_each_share(world) -> None:
    _require_by_ruleset(world)
    bot = {"login": "reviewbot", "is_bot": True, "type": "Bot"}
    actions = {"login": "github-actions", "is_bot": True, "type": "Bot"}
    human = {"login": "carol", "is_bot": False, "type": "User"}
    prs = [
        _pr(0, merger=REVIEWER, comments=[bot]),
        _pr(1, merger=REVIEWER, comments=[bot]),
        _pr(2, merger=REVIEWER, comments=[bot]),
        _pr(3, merger=REVIEWER, comments=[actions]),
        _pr(4, comments=[actions]),
        _pr(5, comments=[human]),
        _pr(6), _pr(7),
        _pr(8, reviews=[{"login": "alice"}]),  # the author's own review, case aside
        _pr(9, reviews=[REVIEWER]),
    ]
    world.serve("prs.json", prs)
    block = scan_review_reality(world.root)
    assert block["reviewed_share"] == 0.1
    assert block["bot_review_share"] == 0.3
    assert block["self_merged_share"] == 0.6
    assert block["hollow_required_review"] is True


def test_review_reality_half_reviewed_is_not_hollow(world) -> None:
    world.serve("branchrules.json", [])
    world.serve("protection.json", {"required_pull_request_reviews": {
        "required_approving_review_count": 2}})
    world.serve("prs.json", [_pr(n, reviews=[REVIEWER] if n < 5 else []) for n in range(10)])
    block = scan_review_reality(world.root)
    assert block["review_required"] is True
    assert block["reviewed_share"] == 0.5
    assert block["hollow_required_review"] is False


def test_review_reality_no_requirement_is_never_hollow(world) -> None:
    world.serve("branchrules.json", [])
    world.fail_with("gh: Branch not protected (HTTP 404)")
    world.serve("prs.json", [_pr(n) for n in range(3)])
    block = scan_review_reality(world.root)
    assert block["review_required"] is False
    assert block["hollow_required_review"] is False


def test_review_reality_refused_protection_leaves_requirement_unknown(world) -> None:
    world.serve("branchrules.json", [])
    world.fail_with("gh: Resource not accessible by integration (HTTP 403)")
    world.serve("prs.json", [_pr(n) for n in range(3)])
    block = scan_review_reality(world.root)
    assert block["available"] is True
    assert block["review_required"] is None
    assert block["hollow_required_review"] is None


def test_review_reality_403_degrades_to_unavailable(world) -> None:
    world.fail_with("gh: Resource not accessible by personal access token (HTTP 403)")
    block = scan_review_reality(world.root)
    assert block["available"] is False
    assert block["reason"].startswith("no_access")
    assert "hollow_required_review" not in block
    assert world.calls()


def test_review_reality_no_remote_never_calls_gh(world) -> None:
    _git(world.root, "remote", "remove", "origin")
    block = scan_review_reality(world.root)
    assert block["available"] is False and block["reason"]
    assert world.calls() == []


def test_review_reality_probes_unmarked_comment_logins_once(world) -> None:
    _require_by_ruleset(world)
    world.serve("user-reviewbot%5Bbot%5D", {"login": "reviewbot[bot]", "type": "Bot"})
    # "dave[bot]" is absent from the fake, so the probe 404s: dave is a person.
    prs = [
        _pr(0, comments=[{"login": "reviewbot"}]),
        _pr(1, comments=[{"login": "reviewbot"}, {"login": "dave"}]),
        _pr(2, comments=[{"login": "dave"}]),
        _pr(3, comments=[{"login": "github-actions"}]),
    ]
    world.serve("prs.json", prs)
    block = scan_review_reality(world.root)
    assert block["bot_review_share"] == 0.5
    probes = [c for c in world.calls() if "users/" in c]
    assert sorted(probes) == ["api users/dave%5Bbot%5D", "api users/reviewbot%5Bbot%5D"]


def test_review_reality_unknown_commenter_type_withholds_bot_share(world) -> None:
    _require_by_ruleset(world)
    world.fail_with("gh: Resource not accessible (HTTP 403)")
    world.serve("prs.json", [_pr(0, comments=[{"login": "mystery"}])])
    block = scan_review_reality(world.root)
    assert block["available"] is True
    assert block["bot_review_share"] is None


def test_review_reality_writes_no_title_or_login(world) -> None:
    _require_by_ruleset(world)
    bot = {"login": "reviewbot", "is_bot": True, "type": "Bot"}
    world.serve("prs.json", [_pr(0, merger=REVIEWER, reviews=[REVIEWER], comments=[bot])])
    text = json.dumps(scan_review_reality(world.root)).lower()
    for secret in ("secret", "alice", "bob", "reviewbot"):
        assert secret not in text


def test_review_reality_bot_comment_reviews_are_not_approvals(world) -> None:
    # An AI reviewer leaves a COMMENTED review on every PR; nobody approves.
    _require_by_ruleset(world)
    world.serve("prs.json", [_pr(n, reviews=[REVIEWER], state="COMMENTED") for n in range(10)])
    block = scan_review_reality(world.root)
    assert block["reviewed_share"] == 1.0
    assert block["approved_share"] == 0.0
    assert block["hollow_required_review"] is False
    assert block["required_approval_bypassed"] is True


def test_review_reality_small_sample_withholds_both_flags(world) -> None:
    _require_by_ruleset(world)
    world.serve("prs.json", [_pr(n) for n in range(4)])
    block = scan_review_reality(world.root)
    assert block["reviewed_share"] == 0.0
    assert block["hollow_required_review"] is None
    assert block["required_approval_bypassed"] is None


def test_review_reality_reports_age_of_oldest_merge() -> None:
    from datetime import datetime, timezone

    from lib.review_reality import summarize

    now = datetime(2026, 9, 19, tzinfo=timezone.utc)
    prs = [_pr(0, merged_at="2026-09-18T12:00:00Z"), _pr(1, merged_at="2026-08-20T00:00:00Z"),
           _pr(2, merged_at=None)]
    assert summarize(prs, True, now=now)["oldest_merged_days_ago"] == 30
    assert summarize([_pr(0, merged_at=None)], True, now=now)["oldest_merged_days_ago"] is None


def test_review_reality_comment_without_author_is_unknown(world) -> None:
    _require_by_ruleset(world)
    bot = {"login": "reviewbot", "is_bot": True, "type": "Bot"}
    # A confirmed bot comment still counts; an authorless comment alone is unknown.
    world.serve("prs.json", [_pr(0, comments=[None, bot]), _pr(1, comments=[None])])
    assert scan_review_reality(world.root)["bot_review_share"] is None
    world.serve("prs.json", [_pr(0, comments=[None, bot]), _pr(1)])
    assert scan_review_reality(world.root)["bot_review_share"] == 0.5
