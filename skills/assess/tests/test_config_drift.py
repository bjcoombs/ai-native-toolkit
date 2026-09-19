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
    assert block == {"available": True, "entries": [], "dropped": 0,
                     "snapshots": [{"file": ".github/rulesets/main.json", "kind": "ruleset"}]}


def test_ruleset_matched_by_name_outside_rulesets_dir(world) -> None:
    doc = _ruleset(False)
    del doc["id"]
    world.track("infra/github/protect-main.json", doc)
    world.serve("rulesets.json", [{"id": 99, "name": "main"}])
    world.serve("ruleset.json", _ruleset(True))
    block = scan_config_drift(world.root)
    assert [e["file"] for e in block["entries"]] == ["infra/github/protect-main.json"]


def test_rule_dropped_or_added_live_is_drift_without_the_live_object() -> None:
    tracked = _ruleset(False)
    live = _ruleset(False)
    live["rules"] = live["rules"][1:] + [{"type": "non_fast_forward", "parameters": {"x": 1}}]
    assert diff_values(tracked, live) == [
        ("rules[deletion]", "present", "absent"),
        ("rules[non_fast_forward]", "absent", "present"),
    ]


def test_reordered_lists_are_not_drift() -> None:
    tracked = {"required_status_checks": {"contexts": ["ci", "lint"]},
               "bypass_actors": [{"actor_id": 1, "actor_type": "Team"},
                                 {"actor_id": 2, "actor_type": "Integration"}]}
    live = {"required_status_checks": {"contexts": ["lint", "ci"]},
            "bypass_actors": [{"actor_id": 2, "actor_type": "Integration", "bypass_mode": "always"},
                              {"actor_id": 1, "actor_type": "Team", "bypass_mode": "always"}]}
    assert diff_values(tracked, live) == []


def test_changed_scalar_list_is_one_bounded_entry() -> None:
    tracked = {"required_status_checks": {"contexts": ["ci"]}}
    live = {"required_status_checks": {"contexts": ["lint", "ci"]}}
    assert diff_values(tracked, live) == [
        ("required_status_checks.contexts",
         {"count": 1, "removed": 0, "sample": []},
         {"count": 2, "added": 1, "sample": ["lint"]})]


def test_scalar_list_drift_never_stores_the_whole_live_list() -> None:
    tracked = {"required_status_checks": {"contexts": ["ci", "old-a", "old-b", "old-c", "old-d"]}}
    live_names = ["ci"] + [f"zzlive-{i:02d}" for i in range(30)]
    live = {"required_status_checks": {"contexts": live_names}}
    [(key, was, now)] = diff_values(tracked, live)
    assert key == "required_status_checks.contexts"
    assert was == {"count": 5, "removed": 4, "sample": ["old-a", "old-b", "old-c"]}
    assert now == {"count": 31, "added": 30, "sample": ["zzlive-00", "zzlive-01", "zzlive-02"]}
    assert json.dumps(now).count("zzlive") == 3


def test_identity_less_object_list_reports_counts_only() -> None:
    tracked = {"x": [{"a": 1}]}
    live = {"x": [{"a": 1}, {"a": 2, "secret_ish": "org detail"}]}
    assert diff_values(tracked, live) == [("x.count", 1, 2)]


@pytest.mark.parametrize("doc", [
    {"description": "not an export"},                       # no rules at all
    {"rules": [{"type": "lint-rule"}]},                     # rules but no name/id
])
def test_non_export_json_is_not_a_snapshot(world, doc) -> None:
    world.track(".github/rulesets/schema.json", doc)
    world.track("config/eslint-ish.json", doc)
    assert find_snapshots(world.root) == []
    assert scan_config_drift(world.root)["entries"] == []
    assert world.calls() == []


def test_deleted_branch_is_drift_not_outage(world) -> None:
    world.track(".github/branch-protection/old.json", _protection(True) | {"url": ""})
    world.track(".github/rulesets/main.json", _ruleset(False))
    world.serve("rulesets.json", [{"id": 7, "name": "main"}])
    world.serve("ruleset.json", _ruleset(True))
    world.fail_with("gh: Branch not found (HTTP 404)")
    block = scan_config_drift(world.root)
    assert block["available"] is True
    assert {"file": ".github/branch-protection/old.json", "key": "branch",
            "tracked": "old", "live": "absent"} in block["entries"]
    assert any(e["file"] == ".github/rulesets/main.json" for e in block["entries"])


def test_unprotected_branch_is_drift(world) -> None:
    world.track(".github/branch-protection/main.json", _protection(True))
    world.fail_with("gh: Branch not protected (HTTP 404)")
    block = scan_config_drift(world.root)
    assert block["entries"] == [{"file": ".github/branch-protection/main.json",
                                 "key": "branch_protection", "tracked": "present",
                                 "live": "absent"}]


def test_other_404_degrades_the_block(world) -> None:
    world.track(".github/branch-protection/main.json", _protection(True))
    block = scan_config_drift(world.root)  # default fail: "gh: Not Found (HTTP 404)"
    assert block["available"] is False and block["reason"].startswith("not_found")


def test_missing_live_ruleset_is_drift(world) -> None:
    world.track(".github/rulesets/main.json", _ruleset(False))
    world.serve("rulesets.json", [])
    block = scan_config_drift(world.root)
    assert block["entries"] == [{"file": ".github/rulesets/main.json", "key": "ruleset",
                                 "tracked": "main", "live": "absent"}]


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
    assert scan_config_drift(world.root) == {"available": True, "entries": [], "dropped": 0,
                                              "snapshots": []}
    assert world.calls() == []


def test_untracked_snapshot_is_ignored(world) -> None:
    path = world.root / ".github" / "rulesets" / "main.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_ruleset(False)))
    assert find_snapshots(world.root) == []


def test_key_omitted_live_is_absent_not_null() -> None:
    tracked = {"required_status_checks": {"strict": True},
               "required_pull_request_reviews": {"required_approving_review_count": 1}}
    live = {"required_status_checks": {"strict": True}}
    assert diff_values(tracked, live) == [("required_pull_request_reviews", "present", "absent")]
    assert diff_values({"a": {"b": False}}, {"a": {}}) == [("a.b", False, "absent")]
    assert diff_values({"a": None}, {"a": None}) == []


def test_ruleset_list_excludes_inherited_rulesets(world) -> None:
    world.track(".github/rulesets/main.json", _ruleset(False))
    world.serve("rulesets.json", [{"id": 7, "name": "main"}])
    world.serve("ruleset.json", _ruleset(False))
    scan_config_drift(world.root)
    listing = [c for c in world.calls() if "rulesets" in c and "rulesets/" not in c]
    assert listing and all("includes_parents=false" in c for c in listing)


def test_json_without_a_snapshot_key_is_not_parsed(world, monkeypatch) -> None:
    import lib.config_drift as cd

    world.track("data/big.json", {"items": list(range(100))})
    parsed: list[str] = []
    real = cd.json.loads
    monkeypatch.setattr(cd.json, "loads", lambda t: parsed.append(t) or real(t))
    assert find_snapshots(world.root) == []
    assert parsed == []


def test_entries_rank_worst_first(world) -> None:
    tracked = _protection(True)  # required_status_checks precedes enforce_admins
    live = _protection(True)
    live["required_status_checks"]["contexts"] = ["ci-renamed"]
    live["enforce_admins"] = {"url": "u", "enabled": False}
    del live["required_pull_request_reviews"]
    world.track(".github/branch-protection/main.json", tracked)
    world.serve("protection.json", live)
    keys = [e["key"] for e in scan_config_drift(world.root)["entries"]]
    assert keys == ["required_pull_request_reviews", "enforce_admins",
                    "required_status_checks.contexts"]


def test_entries_are_capped_with_a_dropped_count(world) -> None:
    from lib.config_drift import MAX_ENTRIES
    n = MAX_ENTRIES + 7
    tracked = _ruleset(False)
    tracked["rules"] = [{"type": f"rule-{i:02d}", "parameters": {"v": 1}} for i in range(n)]
    live = json.loads(json.dumps(tracked))
    for rule in live["rules"]:
        rule["parameters"]["v"] = 2
    world.track(".github/rulesets/main.json", tracked)
    world.serve("rulesets.json", [{"id": 7, "name": "main"}])
    world.serve("ruleset.json", live)
    block = scan_config_drift(world.root)
    assert len(block["entries"]) == MAX_ENTRIES
    assert block["dropped"] == 7


def test_no_drift_reports_zero_dropped(world) -> None:
    world.track(".github/rulesets/main.json", _ruleset(False))
    world.serve("rulesets.json", [{"id": 7, "name": "main"}])
    world.serve("ruleset.json", _ruleset(False))
    block = scan_config_drift(world.root)
    assert block["entries"] == [] and block["dropped"] == 0


def test_absent_key_reports_the_normalised_tracked_value() -> None:
    tracked = {"enforce_admins": {"url": "u", "enabled": False}}
    assert diff_values(tracked, {}) == [("enforce_admins", False, "absent")]


def test_type_mismatch_never_emits_the_live_object() -> None:
    tracked = {"required_pull_request_reviews": None, "restrictions": None}
    live = {"required_pull_request_reviews": {"dismissal_restrictions": {
                "users": [{"login": "zzuser"}], "teams": [{"slug": "zzteam"}]}},
            "restrictions": {"users": [], "teams": [{"slug": "zzteam"}]}}
    out = diff_values(tracked, live)
    assert out == [("required_pull_request_reviews", None, "present"),
                   ("restrictions", None, "present")]
    assert "zz" not in json.dumps(out)
    assert diff_values({"a": {"b": 1}}, {"a": 3}) == [("a", "present", 3)]


def test_tracked_null_against_omitted_live_key_is_not_drift() -> None:
    tracked = {"required_status_checks": {"strict": True},
               "required_pull_request_reviews": None, "restrictions": None}
    live = {"required_status_checks": {"strict": True}}
    assert diff_values(tracked, live) == []
    live_flip = {"required_status_checks": {"strict": False}}
    assert diff_values(tracked, live_flip) == [("required_status_checks.strict", True, False)]


def _user(login: str, uid: int) -> dict:
    # A GitHub simple-user object: `type` is the class discriminator, not an identity.
    return {"login": login, "id": uid, "type": "User", "site_admin": False}


@pytest.mark.parametrize("path", ["restrictions", "required_pull_request_reviews.dismissal_restrictions"])
def test_single_user_swap_is_one_removed_and_one_added(path: str) -> None:
    def shape(user: dict) -> dict:
        doc: dict = {"users": [user], "teams": []}
        for part in reversed(path.split(".")):
            doc = {part: doc}
        return doc
    out = diff_values(shape(_user("alice", 1)), shape(_user("bob", 2)))
    assert out == [(f"{path}.users[alice]", "present", "absent"),
                   (f"{path}.users[bob]", "absent", "present")]
    assert all(v in ("present", "absent") for _, was, now in out for v in (was, now))


def test_single_team_swap_is_one_removed_and_one_added() -> None:
    # Team objects carry `type` ("organization" / "enterprise") as a discriminator.
    tracked = {"restrictions": {"users": [], "teams": [
        {"slug": "core", "name": "Core", "id": 1, "type": "organization"}]}}
    live = {"restrictions": {"users": [], "teams": [
        {"slug": "infra", "name": "Infra", "id": 2, "type": "organization"}]}}
    assert diff_values(tracked, live) == [
        ("restrictions.teams[core]", "present", "absent"),
        ("restrictions.teams[infra]", "absent", "present")]


def test_ruleset_rules_still_pair_on_type() -> None:
    tracked = {"rules": [{"type": "pull_request",
                          "parameters": {"required_approving_review_count": 1}}]}
    live = {"rules": [{"type": "pull_request",
                       "parameters": {"required_approving_review_count": 2}}]}
    assert diff_values(tracked, live) == [
        ("rules[pull_request].parameters.required_approving_review_count", 1, 2)]


_WRITE_VS_READ = [
    ("restrictions.users", ["octocat"], [_user("octocat", 1)]),
    ("restrictions.teams", ["core"],
     [{"slug": "core", "name": "Core", "id": 1, "type": "organization"}]),
    ("restrictions.apps", ["deploy-bot"],
     [{"slug": "deploy-bot", "name": "Deploy Bot", "id": 9, "owner": {"login": "acme"}}]),
    ("required_pull_request_reviews.dismissal_restrictions.users", ["octocat"],
     [_user("octocat", 1)]),
    ("required_pull_request_reviews.dismissal_restrictions.teams", ["core"],
     [{"slug": "core", "name": "Core", "id": 1, "type": "organization"}]),
]


def _nest(path: str, value: object) -> dict:
    doc: object = value
    for part in reversed(path.split(".")):
        doc = {part: doc}
    assert isinstance(doc, dict)
    return doc


@pytest.mark.parametrize("path,write,read", _WRITE_VS_READ)
def test_write_shape_names_match_read_shape_objects(path: str, write: list, read: list) -> None:
    assert diff_values(_nest(path, write), _nest(path, read)) == []


@pytest.mark.parametrize("path,write,read", _WRITE_VS_READ)
def test_write_shape_names_report_a_real_change(path: str, write: list, read: list) -> None:
    [(key, was, now)] = diff_values(_nest(path, ["someone-else"]), _nest(path, read))
    assert key == path
    assert was["removed"] == 1 and now["added"] == 1


def test_write_shape_protection_snapshot_is_clean_against_the_read(world) -> None:
    doc = _protection(True)
    doc["restrictions"] = {"users": ["octocat"], "teams": ["core"], "apps": ["deploy-bot"]}
    live = _protection(True)
    live["restrictions"] = {"users": [_user("octocat", 1)],
                            "teams": [{"slug": "core", "name": "Core", "id": 1}],
                            "apps": [{"slug": "deploy-bot", "name": "Deploy Bot", "id": 9}]}
    world.track(".github/branch-protection/main.json", doc)
    world.serve("protection.json", live)
    block = scan_config_drift(world.root)
    assert block["available"] is True and block["entries"] == []
