"""floor_anchor.py's branch-protection read, pinned from FLOOR.md's header.

FLOOR.md says both floor checks are required status checks on the default
branch. These tests cover the two ways that read can go that the main suite
leaves unstubbed: protection that cannot be read at all, and a requirement
that lives only in a repository ruleset (#410).
"""
import re

import pytest

import floor_anchor
from floor_anchor import AnchorError, check_required_check, main

REPO = "owner/repo"
FLOOR = "floor enforcement"
ANCHOR = "floor self-anchor"


def _recording_get(protection_status, calls, ruleset_contexts=()):
    """A fake ``_get`` that logs each path and answers protection with
    ``protection_status`` and the ruleset API with ``ruleset_contexts``."""

    def _get(path, token):
        calls.append(path)
        if path == f"/repos/{REPO}":
            return 200, {"default_branch": "main", "owner": {"login": "owner"}}
        if path.endswith("/protection/required_status_checks"):
            return protection_status, {"message": "stubbed"}
        if path.endswith("/rulesets"):
            return 200, [{"id": 7}]
        if path.endswith("/rulesets/7"):
            checks = [{"context": ctx} for ctx in ruleset_contexts]
            rule = {
                "type": "required_status_checks",
                "parameters": {"required_status_checks": checks},
            }
            return 200, {"rules": [rule]}
        return 404, {}

    return _get


@pytest.fixture()
def anchor_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.delenv("FLOOR_ANCHOR_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    monkeypatch.setattr(floor_anchor, "FLOOR_CONTEXT", FLOOR)
    monkeypatch.setattr(floor_anchor, "ANCHOR_CONTEXT", ANCHOR)


@pytest.mark.parametrize("status", [401, 403, 500])
def test_unreadable_branch_protection_fails_before_later_checks(
    anchor_env, monkeypatch, status
):
    # The ruleset supplies both checks, so only the unreadable protection
    # response can make this run fail.
    calls: list[str] = []
    monkeypatch.setattr(
        floor_anchor, "_get", _recording_get(status, calls, (FLOOR, ANCHOR))
    )
    later: list[str] = []
    for name in (
        "check_contexts_have_workflows",
        "check_signoff_environment",
        "check_workflow_wiring",
    ):
        monkeypatch.setattr(
            floor_anchor, name, lambda *a, _n=name, **k: later.append(_n)
        )

    assert main() == 1
    assert later == []
    assert not any("/environments/" in path for path in calls)


@pytest.mark.parametrize("status", [401, 403, 500])
def test_unreadable_branch_protection_raises(anchor_env, monkeypatch, status):
    calls: list[str] = []
    monkeypatch.setattr(
        floor_anchor, "_get", _recording_get(status, calls, (FLOOR, ANCHOR))
    )
    with pytest.raises(AnchorError, match=f"HTTP {status}"):
        check_required_check(REPO, "main", "t")


@pytest.mark.parametrize("status", [401, 403])
def test_a_permission_denied_protection_read_is_unreadable(anchor_env, monkeypatch, status):
    # Contract I410-3: a 403 is a token without admin:read, the same as a 401.
    # It must fail closed as unreadable protection with the FLOOR_ANCHOR_TOKEN
    # remedy, not fall through to the generic unexpected-response path.
    calls: list[str] = []
    monkeypatch.setattr(
        floor_anchor, "_get", _recording_get(status, calls, (FLOOR, ANCHOR))
    )
    with pytest.raises(AnchorError) as exc:
        check_required_check(REPO, "main", "t")
    message = str(exc.value)
    assert message.startswith(f"cannot read branch protection (HTTP {status})")
    assert "FLOOR_ANCHOR_TOKEN" in message
    assert "unexpected" not in message


def test_ruleset_alone_can_require_both_floor_checks(anchor_env, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        floor_anchor, "_get", _recording_get(404, calls, (FLOOR, ANCHOR))
    )
    assert {FLOOR, ANCHOR} <= check_required_check(REPO, "main", "t")


@pytest.mark.parametrize("kept", [FLOOR, ANCHOR])
def test_ruleset_missing_one_floor_check_fails(anchor_env, monkeypatch, kept):
    calls: list[str] = []
    monkeypatch.setattr(floor_anchor, "_get", _recording_get(404, calls, (kept,)))
    dropped = ANCHOR if kept == FLOOR else FLOOR
    with pytest.raises(AnchorError, match=re.escape(f"{dropped!r} NOT required")):
        check_required_check(REPO, "main", "t")
