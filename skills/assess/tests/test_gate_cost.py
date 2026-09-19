"""Tests for lib/gate_cost.py: the CI gate's Actions cost estimate.

GitHub is faked with a `gh` shell script first on PATH (the pattern in
test_config_drift.py), so the real subprocess path through lib/gh_cli.py runs.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib.gate_cost import MINUTES_PER_RUN, estimate_gate_cost  # noqa: E402

FAKE_GH = """#!/bin/sh
echo "$*" >> "$FAKE_GH/log"
serve() { [ -f "$FAKE_GH/$1" ] && cat "$FAKE_GH/$1" && exit 0; }
case "$1:$*" in
  auth:*) [ -f "$FAKE_GH/noauth" ] || exit 0 ;;
  repo:*) serve repo.json ;;
  pr:*) serve prs.json ;;
esac
cat "$FAKE_GH/fail" >&2
exit 1
"""

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _merged(n: int, when: datetime) -> list[dict]:
    stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    return [{"number": i + 1, "mergedAt": stamp, "title": f"t{i}"} for i in range(n)]


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
    (ghdir / "repo.json").write_text(json.dumps({"nameWithOwner": "acme/widget", "isPrivate": True}))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH", str(ghdir))

    class World:
        root = repo
        gh_dir = ghdir

        def serve(self, name: str, doc: object) -> None:
            (ghdir / name).write_text(json.dumps(doc))

        def calls(self) -> list[str]:
            return (ghdir / "log").read_text().splitlines()

    return World()


def test_gate_cost_forty_merged(world) -> None:
    world.serve("prs.json", _merged(40, NOW - timedelta(days=1)))
    block = estimate_gate_cost(world.root, now=NOW)
    assert block["available"] is True
    assert block["runs_per_month"] == 40
    assert block["minutes_per_run"] == MINUTES_PER_RUN == 5
    assert block["minutes_per_month"] == 200
    assert block["private"] is True
    assert "assum" in block["assumption"].lower() and "5 minutes" in block["assumption"]
    pr_call = next(c for c in world.calls() if c.startswith("pr list"))
    assert "--state merged" in pr_call and "--limit" in pr_call
    assert "--jq" not in pr_call and "--template" not in pr_call


def test_gate_cost_counts_only_the_last_thirty_days(world) -> None:
    world.serve("prs.json", _merged(3, NOW - timedelta(days=2)) + _merged(5, NOW - timedelta(days=45)))
    block = estimate_gate_cost(world.root, now=NOW)
    assert block["runs_per_month"] == 3
    assert block["minutes_per_month"] == 15


def test_gate_cost_no_merge_history(world) -> None:
    world.serve("prs.json", [])
    block = estimate_gate_cost(world.root, now=NOW)
    assert block["available"] is False
    assert block["reason"].startswith("no_merge_history")


def test_gate_cost_no_gh(world, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # PATH holds git and nothing else: the remote resolves, gh is missing.
    only_git = tmp_path / "only-git"
    only_git.mkdir()
    git = shutil.which("git")
    assert git is not None
    (only_git / "git").symlink_to(git)
    monkeypatch.setenv("PATH", str(only_git))
    block = estimate_gate_cost(world.root, now=NOW)
    assert block == {"available": False, "reason": block["reason"]}
    assert block["reason"].startswith("gh_not_installed")


def test_gate_cost_unauthenticated(world) -> None:
    (world.gh_dir / "noauth").write_text("")
    world.serve("prs.json", _merged(40, NOW))
    block = estimate_gate_cost(world.root, now=NOW)
    assert block["available"] is False
    assert block["reason"].startswith("not_authenticated")
    assert not any(c.startswith("pr ") for c in world.calls())


def test_gate_cost_no_remote_never_calls_gh(world) -> None:
    _git(world.root, "remote", "remove", "origin")
    block = estimate_gate_cost(world.root, now=NOW)
    assert block["available"] is False
    assert block["reason"].startswith("no_remote")
    assert world.calls() == []


def test_gate_cost_private_unknown_when_repo_view_fails(world) -> None:
    (world.gh_dir / "repo.json").unlink()
    world.serve("prs.json", _merged(2, NOW))
    block = estimate_gate_cost(world.root, now=NOW)
    assert block["available"] is True
    assert block["private"] is None


def test_gate_cost_capped_at_listing_limit(world, monkeypatch: pytest.MonkeyPatch) -> None:
    import lib.gate_cost as gate_cost

    monkeypatch.setattr(gate_cost, "PR_LIMIT", 3)
    world.serve("prs.json", _merged(3, NOW))
    block = estimate_gate_cost(world.root, now=NOW)
    assert block["capped"] is True
    assert "at least 3 merged" in block["assumption"]
    pr_call = next(c for c in world.calls() if c.startswith("pr list"))
    assert "--limit 3" in pr_call


def test_gate_cost_not_capped_below_limit(world) -> None:
    world.serve("prs.json", _merged(2, NOW))
    block = estimate_gate_cost(world.root, now=NOW)
    assert block["capped"] is False
    assert "at least" not in block["assumption"]


def test_gate_cost_non_list_answer_degrades(world) -> None:
    world.serve("prs.json", {"message": "unexpected"})
    block = estimate_gate_cost(world.root, now=NOW)
    assert block["available"] is False
    assert block["reason"].startswith("gh_bad_json")
