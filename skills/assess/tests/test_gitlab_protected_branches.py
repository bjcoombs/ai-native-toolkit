"""Tests for GitLab protected-branch reads: lib/gitlab_cli.branch_protection,
the GitLab path of lib/config_drift.py, and the "all protected branches"
approval rules of lib/review_reality.py.

Reuses the offline `glab` / `gh` stubs of test_review_reality_gitlab.py: the
stub directory is the whole PATH, `glab api` answers from JSON files keyed by
the endpoint, and any endpoint not served answers 404.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib.config_drift import find_snapshots, scan_config_drift  # noqa: E402
from lib.gh_cli import GhUnavailable  # noqa: E402
from lib.gitlab_cli import GitlabProject, branch_protection  # noqa: E402
from lib.review_reality import gitlab_review_required  # noqa: E402
from test_review_reality_gitlab import ENC, PROJECT, World, _git, world  # noqa: E402,F401

LIST = f"projects/{ENC}/protected_branches?per_page=100"
GITLAB_REMOTE = f"https://gitlab.com/{PROJECT}.git"
P = GitlabProject(host="gitlab.com", path=PROJECT)


def _levels(*levels: int) -> list[dict[str, object]]:
    return [{"id": 100 + lv, "access_level": lv, "access_level_description": f"L{lv}",
             "user_id": None, "group_id": None, "deploy_key_id": None} for lv in levels]


def _rule(name: str, push: int = 40, merge: int = 30, force: bool = False) -> dict[str, object]:
    return {"id": abs(hash(name)) % 1000, "name": name, "push_access_levels": _levels(push),
            "merge_access_levels": _levels(merge), "unprotect_access_levels": [],
            "allow_force_push": force, "code_owner_approval_required": False}


def _track(world: World, rel: str, doc: object) -> None:
    path = world.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))
    _git(world.root, "add", rel)


# ---- gitlab_cli.branch_protection ------------------------------------------

def test_exact_rule_reports_its_settings(world: World) -> None:
    world.serve(LIST, [_rule("ma*", push=0), _rule("main", force=True)])
    got = branch_protection(P, "main")
    assert got is not None and got["name"] == "main" and got["allow_force_push"] is True


def test_wildcard_rule_covers_the_branch_first_in_name_order(world: World) -> None:
    world.serve(LIST, [_rule("release/*"), _rule("m*", push=0), _rule("*", push=40)])
    got = branch_protection(P, "main")
    assert got is not None and got["name"] == "*"
    got = branch_protection(P, "release/1.2")
    assert got is not None and got["name"] == "*"
    world.serve(LIST, [_rule("release/*"), _rule("ma*n")])
    got = branch_protection(P, "main")
    assert got is not None and got["name"] == "ma*n"


def test_wildcard_metacharacters_are_literal(world: World) -> None:
    world.serve(LIST, [_rule("ma.n"), _rule("release/[0-9]*")])
    assert branch_protection(P, "main") is None
    assert branch_protection(P, "release/[0-9]-x") is not None


def test_complete_list_without_a_cover_is_unprotected(world: World) -> None:
    world.serve(LIST, [_rule("release/*")])
    assert branch_protection(P, "main") is None
    world.serve(LIST, [])
    assert branch_protection(P, "main") is None


@pytest.mark.parametrize("line,reason", [
    ("glab: 403 Forbidden (HTTP 403)", "no_access"),
    ("glab: 404 Not Found (HTTP 404)", "not_found"),
    ("glab: 500 Internal Server Error (HTTP 500)", "glab_error"),
])
def test_refused_list_is_unavailable_never_unprotected(world: World, line: str,
                                                       reason: str) -> None:
    world.fail(LIST, line)
    with pytest.raises(GhUnavailable) as e:
        branch_protection(P, "main")
    assert e.value.reason.startswith(reason)


@pytest.mark.parametrize("doc", [{"name": "main"}, [{"id": 1}], ["main"]])
def test_malformed_list_is_unavailable(world: World, doc: object) -> None:
    world.serve(LIST, doc)
    with pytest.raises(GhUnavailable) as e:
        branch_protection(P, "main")
    assert e.value.reason.startswith("glab_bad_json")


def test_full_page_without_a_cover_falls_back_to_the_single_read(world: World) -> None:
    world.serve(LIST, [_rule(f"feature/{i}") for i in range(100)])
    with pytest.raises(GhUnavailable) as e:
        branch_protection(P, "main")  # single read 404: still unknown
    assert e.value.reason.startswith("truncated")
    world.serve(f"projects/{ENC}/protected_branches/main", _rule("main", force=True))
    got = branch_protection(P, "main")
    assert got is not None and got["allow_force_push"] is True


# ---- config_drift on GitLab ------------------------------------------------

def test_gitlab_snapshot_discovery(world: World) -> None:
    _track(world, ".gitlab/protected/main.json", _rule("main"))
    _track(world, ".gitlab/protected/release.json", {"push_access_levels": _levels(40)})
    _track(world, "elsewhere/main.json", _rule("main"))
    snaps = find_snapshots(world.root)
    assert [(s["file"], s["kind"], s["branch"]) for s in snaps] == [
        (".gitlab/protected/main.json", "gitlab_protected_branch", "main"),
        (".gitlab/protected/release.json", "gitlab_protected_branch", "release"),
    ]


def test_matching_gitlab_snapshot_has_no_drift(world: World) -> None:
    world.remote(GITLAB_REMOTE)
    _track(world, ".gitlab/main.json", _rule("main"))
    live = _rule("main")
    live["id"] = 999
    live["merge_access_levels"] = [{**lv, "id": 1} for lv in _levels(30)]  # type: ignore[dict-item]
    world.serve(LIST, [live])
    assert scan_config_drift(world.root) == {
        "available": True, "entries": [], "dropped": 0,
        "snapshots": [{"file": ".gitlab/main.json", "kind": "gitlab_protected_branch"}],
    }
    assert world.calls("ghlog") == []


def test_gitlab_drift_is_reported_without_live_levels(world: World) -> None:
    world.remote(GITLAB_REMOTE)
    _track(world, ".gitlab/main.json", _rule("main"))
    live = _rule("main", push=0, force=True)
    live["merge_access_levels"] = _levels(30, 40)
    world.serve(LIST, [live])
    block = scan_config_drift(world.root)
    assert block["available"] is True
    got = {e["key"]: (e["tracked"], e["live"]) for e in block["entries"]}
    assert got == {
        "allow_force_push": (False, True),
        "push_access_levels.items": ("differs", "differs"),
        "merge_access_levels.count": (1, 2),
    }
    assert block["entries"][0]["key"] == "allow_force_push"


def test_gitlab_snapshot_against_a_wildcard_rule(world: World) -> None:
    world.remote(GITLAB_REMOTE)
    _track(world, ".gitlab/main.json", _rule("main"))
    world.serve(LIST, [_rule("ma*")])
    block = scan_config_drift(world.root)
    assert [(e["key"], e["tracked"], e["live"]) for e in block["entries"]] == [
        ("name", "main", "ma*"),
    ]


def test_unprotected_branch_is_drift(world: World) -> None:
    world.remote(GITLAB_REMOTE)
    _track(world, ".gitlab/main.json", _rule("main"))
    world.serve(LIST, [_rule("release/*")])
    block = scan_config_drift(world.root)
    assert block["available"] is True
    assert block["entries"] == [{"file": ".gitlab/main.json", "key": "branch_protection",
                                 "tracked": "present", "live": "absent"}]


@pytest.mark.parametrize("line,reason", [
    ("glab: 403 Forbidden (HTTP 403)", "no_access"),
    ("glab: 404 Not Found (HTTP 404)", "not_found"),
])
def test_refused_gitlab_read_is_unavailable_never_no_protection(world: World, line: str,
                                                                reason: str) -> None:
    world.remote(GITLAB_REMOTE)
    _track(world, ".gitlab/main.json", _rule("main"))
    world.fail(LIST, line)
    block = scan_config_drift(world.root)
    assert block["available"] is False
    assert block["reason"].startswith(reason)
    assert "entries" not in block
    assert block["snapshots"] == [{"file": ".gitlab/main.json", "kind": "gitlab_protected_branch"}]


def test_github_snapshot_on_a_gitlab_remote_is_not_compared(world: World) -> None:
    world.remote(GITLAB_REMOTE)
    _track(world, ".github/rulesets/main.json", {"name": "main", "rules": [{"type": "deletion"}]})
    block = scan_config_drift(world.root)
    assert block["available"] is False
    assert block["reason"].startswith("other_provider")
    assert world.calls("ghlog") == []


def test_gitlab_snapshot_on_a_github_remote_is_not_compared(world: World) -> None:
    world.remote("https://github.com/o/r.git")
    _track(world, ".gitlab/main.json", _rule("main"))
    block = scan_config_drift(world.root)
    assert block["available"] is False
    assert block["reason"].startswith("other_provider")
    assert world.calls() == [] and world.calls("ghlog") == []


def test_mixed_snapshots_on_gitlab_list_only_the_compared_kind(world: World) -> None:
    world.remote(GITLAB_REMOTE)
    _track(world, ".github/rulesets/main.json", {"name": "main", "rules": [{"type": "deletion"}]})
    _track(world, ".gitlab/main.json", _rule("main"))
    world.serve(LIST, [_rule("main")])
    block = scan_config_drift(world.root)
    assert block["available"] is True and block["entries"] == []
    assert block["snapshots"] == [{"file": ".gitlab/main.json", "kind": "gitlab_protected_branch"}]
    assert world.calls("ghlog") == []


def test_no_snapshots_makes_no_platform_call(world: World) -> None:
    world.remote(GITLAB_REMOTE)
    assert scan_config_drift(world.root)["entries"] == []
    assert world.calls() == [] and world.calls("ghlog") == []


# ---- review_reality: rules scoped to all protected branches ----------------

ALL_PROTECTED = {"rule_type": "regular", "approvals_required": 1,
                 "applies_to_all_protected_branches": True, "protected_branches": []}


@pytest.mark.parametrize("served,expected", [
    ([_rule("main")], True),
    ([_rule("m*")], True),
    ([_rule("release/*")], False),
    (None, None),  # list read refused (404): unknown, never False
])
def test_all_protected_branches_rule_reads_protection(world: World, served: object,
                                                      expected: bool | None) -> None:
    world.policy([ALL_PROTECTED])
    if served is not None:
        world.serve(LIST, served)
    assert gitlab_review_required(P) is expected


def test_protected_branch_list_is_read_once(world: World) -> None:
    world.policy([ALL_PROTECTED, {**ALL_PROTECTED, "approvals_required": 2}])
    world.serve(LIST, [_rule("release/*")])
    assert gitlab_review_required(P) is False
    assert sum("protected_branches" in c for c in world.calls()) == 1
