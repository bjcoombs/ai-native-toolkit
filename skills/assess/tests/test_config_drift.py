"""Tests for lib/config_drift.py and the shared gh helper lib/gh_cli.py.

GitHub is faked with a `gh` shell script placed first on PATH: it logs every
call and answers from JSON files in a directory, so the tests exercise the real
subprocess path, the remote-then-auth-then-calls order and the 403 mapping.
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

from lib.config_drift import diff_values, find_snapshots, scan_config_drift  # noqa: E402
from lib.gh_cli import (  # noqa: E402
    GhUnavailable,
    gh_json,
    open_github,
    parse_github_remote,
)

FAKE_GH = """#!/bin/sh
echo "$*" >> "$FAKE_GH/log"
serve() { [ -f "$FAKE_GH/$1" ] && cat "$FAKE_GH/$1" && exit 0; }
case "$1:$*" in
  auth:*) [ -f "$FAKE_GH/noauth" ] || exit 0 ;;
  pr:*) serve prs.json ;;
  api:*rulesets/[0-9]*) serve ruleset.json ;;
  api:*rulesets*) serve rulesets.json ;;
  api:*/protection*) serve protection.json ;;
esac
cat "$FAKE_GH/fail" >&2
exit 1
"""


def _ruleset(strict: bool, **extra) -> dict:
    return {
        "id": 7, "name": "main", "target": "branch", "enforcement": "active",
        "updated_at": extra.pop("updated_at", "2026-01-01T00:00:00Z"),
        "rules": [
            {"type": "deletion"},
            {"type": "pull_request", "parameters": {"required_approving_review_count": 1}},
            {"type": "required_status_checks", "parameters": {
                "strict_required_status_checks_policy": strict,
                "required_status_checks": [{"context": "ci"}],
            }},
        ],
        **extra,
    }


def _protection(strict: bool) -> dict:
    return {
        "url": "https://api.github.com/repos/acme/widget/branches/main/protection",
        "required_status_checks": {"strict": strict, "contexts": ["ci"]},
        "enforce_admins": {"url": "https://x/enforce_admins", "enabled": True},
        "required_pull_request_reviews": {"required_approving_review_count": 1},
    }


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null"}
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        check=True, capture_output=True, env=env,
    )


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A git repo with a github.com origin and a fake gh first on PATH."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("x = 1\n")
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

        def track(self, rel: str, doc: object) -> None:
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(doc))
            _git(repo, "add", rel)

        def serve(self, name: str, doc: object) -> None:
            (ghdir / name).write_text(json.dumps(doc))

        def fail_with(self, line: str) -> None:
            (ghdir / "fail").write_text(line + "\n")

        def calls(self) -> list[str]:
            return (ghdir / "log").read_text().splitlines()

    return World()


# ---------------------------------------------------------------------------
# gh helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "https://github.com/acme/widget.git",
    "https://github.com/acme/widget",
    "git@github.com:acme/widget.git",
    "ssh://git@github.com/acme/widget.git",
])
def test_parse_github_remote_accepts_github_forms(url: str) -> None:
    repo = parse_github_remote(url)
    assert repo is not None and repo.slug == "acme/widget"


def test_parse_github_remote_rejects_other_hosts() -> None:
    assert parse_github_remote("https://gitlab.com/acme/widget.git") is None


def test_no_remote_never_invokes_gh(world) -> None:
    _git(world.root, "remote", "remove", "origin")
    with pytest.raises(GhUnavailable) as e:
        open_github(world.root)
    assert e.value.reason
    assert world.calls() == []


def test_unauthenticated_gh_degrades(world) -> None:
    (Path(os.environ["FAKE_GH"]) / "noauth").write_text("")
    with pytest.raises(GhUnavailable) as e:
        open_github(world.root)
    assert e.value.reason.startswith("not_authenticated")


def test_gh_json_parses_pr_list(world) -> None:
    world.serve("prs.json", [{"number": 1, "extra": "x"}])
    assert gh_json(["pr", "list", "--state", "merged", "--json", "number"])[0]["number"] == 1


# ---------------------------------------------------------------------------
# config_drift
# ---------------------------------------------------------------------------


def test_ruleset_drift_reports_one_entry(world) -> None:
    world.track(".github/rulesets/main.json", _ruleset(False))
    world.serve("rulesets.json", [{"id": 7, "name": "main"}])
    world.serve("ruleset.json", _ruleset(True, updated_at="2026-08-01T00:00:00Z"))
    block = scan_config_drift(world.root)
    assert block["available"] is True
    assert block["entries"] == [{
        "file": ".github/rulesets/main.json",
        "key": "rules[required_status_checks].parameters.strict_required_status_checks_policy",
        "tracked": False,
        "live": True,
    }]


def test_identical_ruleset_gives_empty_entries(world) -> None:
    world.track(".github/rulesets/main.json", _ruleset(False))
    world.serve("rulesets.json", [{"id": 7, "name": "main"}])
    world.serve("ruleset.json", _ruleset(False, updated_at="2026-08-01T00:00:00Z",
                                         node_id="X", _links={"self": {"href": "h"}}))
    block = scan_config_drift(world.root)
    assert block == {"available": True, "entries": [],
                     "snapshots": [{"file": ".github/rulesets/main.json", "kind": "ruleset"}]}


def test_ruleset_matched_by_name_outside_rulesets_dir(world) -> None:
    doc = _ruleset(False)
    del doc["id"]
    world.track("infra/github/protect-main.json", doc)
    world.serve("rulesets.json", [{"id": 99, "name": "main"}])
    world.serve("ruleset.json", _ruleset(True))
    block = scan_config_drift(world.root)
    assert [e["file"] for e in block["entries"]] == ["infra/github/protect-main.json"]


def test_rule_dropped_live_is_drift() -> None:
    tracked = _ruleset(False)
    live = _ruleset(False)
    live["rules"] = live["rules"][1:]
    assert diff_values(tracked, live) == [("rules[deletion]", {"type": "deletion"}, None)]


def test_missing_live_ruleset_is_drift(world) -> None:
    world.track(".github/rulesets/main.json", _ruleset(False))
    world.serve("rulesets.json", [])
    block = scan_config_drift(world.root)
    assert block["entries"] == [{"file": ".github/rulesets/main.json", "key": "ruleset",
                                 "tracked": "main", "live": None}]


def test_branch_protection_export_drift(world) -> None:
    world.track(".github/branch-protection/main.json", _protection(False))
    world.serve("protection.json", _protection(True))
    block = scan_config_drift(world.root)
    assert block["available"] is True
    assert block["entries"] == [{"file": ".github/branch-protection/main.json",
                                 "key": "required_status_checks.strict",
                                 "tracked": False, "live": True}]
    assert any("repos/acme/widget/branches/main/protection" in c for c in world.calls())


def test_branch_protection_branch_from_file_stem_and_write_shape(world) -> None:
    doc = _protection(True)
    del doc["url"]
    doc["enforce_admins"] = True  # the PUT payload shape
    world.track(".github/protection/release.json", doc)
    world.serve("protection.json", _protection(True))
    block = scan_config_drift(world.root)
    assert block["entries"] == []
    assert any("branches/release/protection" in c for c in world.calls())


def test_403_degrades_to_no_access(world) -> None:
    world.track(".github/rulesets/main.json", _ruleset(False))
    world.fail_with("gh: Resource not accessible by personal access token (HTTP 403)")
    block = scan_config_drift(world.root)
    assert block["available"] is False
    assert "no_access" in block["reason"]
    assert world.calls()


def test_no_remote_degrades_without_calling_gh(world) -> None:
    world.track(".github/rulesets/main.json", _ruleset(False))
    _git(world.root, "remote", "remove", "origin")
    block = scan_config_drift(world.root)
    assert block["available"] is False and block["reason"]
    assert world.calls() == []


def test_no_snapshots_is_clean_without_calling_gh(world) -> None:
    world.track("package.json", {"name": "x", "rules": "not a ruleset"})
    assert scan_config_drift(world.root) == {"available": True, "entries": [], "snapshots": []}
    assert world.calls() == []


def test_untracked_snapshot_is_ignored(world) -> None:
    path = world.root / ".github" / "rulesets" / "main.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_ruleset(False)))
    assert find_snapshots(world.root) == []
