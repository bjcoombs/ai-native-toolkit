"""Tests for the agent-operations guardrail scan (Layer 8 evidence)."""
from __future__ import annotations

import json
from pathlib import Path

from lib.agents.agent_ops import scan_agent_ops


def _write_settings(repo: Path, rel: str, data: object) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ── empty / degraded cases ──────────────────────────────────────────────────

def test_no_claude_dir(git_repo) -> None:
    repo, commit = git_repo
    (repo / "README.md").write_text("hi", encoding="utf-8")
    commit("init")
    block = scan_agent_ops(repo)
    assert block["available"] is True
    assert block["settings"] == []
    assert block["hooks_dir"]["present"] is False
    assert block["summary"] == {
        "permissions_encoded": False,
        "hooks_present": False,
        "routines_present": False,
    }


def test_non_git_dir_counts_nothing_as_tracked(tmp_path: Path) -> None:
    _write_settings(tmp_path, ".claude/settings.json",
                    {"permissions": {"allow": ["Bash(ls:*)"]}})
    block = scan_agent_ops(tmp_path)
    assert block["available"] is True
    assert block["settings"][0]["tracked"] is False
    assert block["settings"][0]["allow_count"] == 1
    # Untracked evidence is reported but never credited.
    assert block["summary"]["permissions_encoded"] is False


def test_malformed_settings_json_degrades(git_repo) -> None:
    repo, commit = git_repo
    _write_settings(repo, ".claude/settings.json", "{not json")
    commit("add settings")
    block = scan_agent_ops(repo)
    entry = block["settings"][0]
    assert entry["parse_ok"] is False
    assert entry["allow_count"] == 0
    assert block["summary"]["permissions_encoded"] is False


# ── tracked-only credit ─────────────────────────────────────────────────────

def test_tracked_settings_credit_summary(git_repo) -> None:
    repo, commit = git_repo
    _write_settings(repo, ".claude/settings.json", {
        "permissions": {"allow": ["Bash(ls:*)", "Read"], "deny": ["WebFetch"]},
        "hooks": {"PreToolUse": [], "PostToolUse": []},
        "sandbox": {"enabled": True},
    })
    commit("add settings")
    block = scan_agent_ops(repo)
    entry = block["settings"][0]
    assert entry["tracked"] is True
    assert entry["parse_ok"] is True
    assert entry["allow_count"] == 2
    assert entry["deny_count"] == 1
    assert entry["ask_count"] == 0
    assert entry["hook_events"] == 2
    assert entry["sandbox_configured"] is True
    assert block["summary"]["permissions_encoded"] is True
    assert block["summary"]["hooks_present"] is True


def test_untracked_settings_reported_not_credited(git_repo) -> None:
    repo, commit = git_repo
    (repo / "README.md").write_text("hi", encoding="utf-8")
    commit("init")
    # Written after the commit, never staged - the settings.local.json case.
    _write_settings(repo, ".claude/settings.local.json",
                    {"permissions": {"allow": ["Bash(ls:*)"]}})
    block = scan_agent_ops(repo)
    assert [s["path"] for s in block["settings"]] == [".claude/settings.local.json"]
    assert block["settings"][0]["tracked"] is False
    assert block["summary"]["permissions_encoded"] is False


def test_hooks_dir_scripts(git_repo) -> None:
    repo, commit = git_repo
    hook = repo / ".claude" / "hooks" / "check.sh"
    hook.parent.mkdir(parents=True)
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    commit("add hook")
    block = scan_agent_ops(repo)
    assert block["hooks_dir"]["present"] is True
    assert block["hooks_dir"]["file_count"] == 1
    assert block["hooks_dir"]["tracked_count"] == 1
    assert block["summary"]["hooks_present"] is True


def test_routine_dirs(git_repo) -> None:
    repo, commit = git_repo
    wf = repo / ".claude" / "workflows" / "nightly-triage.md"
    wf.parent.mkdir(parents=True)
    wf.write_text("# nightly triage\n", encoding="utf-8")
    commit("add workflow")
    block = scan_agent_ops(repo)
    workflows = next(
        d for d in block["routine_dirs"] if d["path"] == ".claude/workflows"
    )
    assert workflows["tracked_count"] == 1
    assert block["summary"]["routines_present"] is True


# ── auto mode and managed settings (#1153) ─────────────────────────────────

def test_deny_rule_with_auto_mode_meets_the_practice(git_repo) -> None:
    """defaultMode is information only: a deny rule under auto mode still meets it."""
    repo, commit = git_repo
    _write_settings(repo, ".claude/settings.json", {"permissions": {
        "defaultMode": "auto", "deny": ["Bash(git push origin main)"]}})
    commit("add settings")
    block = scan_agent_ops(repo)
    assert block["settings"][0]["default_mode"] == "auto"
    assert block["summary"]["permissions_encoded"] is True
    assert block["summary"]["permissions_source"] == "repository"


def test_default_mode_alone_never_meets_or_fails(git_repo) -> None:
    repo, commit = git_repo
    _write_settings(repo, ".claude/settings.json", {"permissions": {"defaultMode": "yolo"}})
    commit("add settings")
    block = scan_agent_ops(repo)
    assert block["settings"][0]["default_mode"] == "other"  # never the file's own text
    assert block["summary"]["permissions_encoded"] is False


def _managed(monkeypatch, tmp_path: Path, data: object) -> None:
    directory = tmp_path / "managed"
    _write_settings(directory, "managed-settings.json", data)
    monkeypatch.setenv("ASSESS_MANAGED_SETTINGS_DIR", str(directory))


def test_managed_deny_rule_meets_a_repo_without_settings(git_repo, tmp_path, monkeypatch) -> None:
    repo, commit = git_repo
    (repo / "README.md").write_text("hi", encoding="utf-8")
    commit("init")
    _managed(monkeypatch, tmp_path, {"permissions": {"deny": ["Bash(git push origin main)"]}})
    block = scan_agent_ops(repo, managed_settings=True)
    assert block["managed"]["state"] == "found"
    assert block["summary"]["permissions_encoded"] is True
    assert block["summary"]["permissions_source"] == "managed"


def test_unreadable_managed_settings_read_not_measured(git_repo, tmp_path, monkeypatch) -> None:
    repo, commit = git_repo
    (repo / "README.md").write_text("hi", encoding="utf-8")
    commit("init")
    _managed(monkeypatch, tmp_path, "{not json")
    block = scan_agent_ops(repo, managed_settings=True)
    assert block["managed"]["state"] == "unreadable"
    assert block["summary"]["permissions_encoded"] is None
    assert block["summary"]["permissions_encoded_reason"] == "managed_settings_unreadable"


def test_repo_rule_meets_whatever_the_managed_state(git_repo, tmp_path, monkeypatch) -> None:
    repo, commit = git_repo
    _write_settings(repo, ".claude/settings.json", {"permissions": {"deny": ["WebFetch"]}})
    commit("add settings")
    _managed(monkeypatch, tmp_path, "{not json")
    summary = scan_agent_ops(repo, managed_settings=True)["summary"]
    assert summary["permissions_encoded"] is True
    assert "permissions_encoded_reason" not in summary


def test_managed_without_rules_leaves_the_repo_reading(git_repo, tmp_path, monkeypatch) -> None:
    repo, commit = git_repo
    (repo / "README.md").write_text("hi", encoding="utf-8")
    commit("init")
    _managed(monkeypatch, tmp_path, {"model": "opus"})
    assert scan_agent_ops(repo, managed_settings=True)["summary"]["permissions_encoded"] is False


def test_managed_settings_are_not_read_without_the_option(git_repo, tmp_path, monkeypatch) -> None:
    """#1153 decision: the machine read is opt-in, so by default a central rule is not seen."""
    repo, commit = git_repo
    (repo / "README.md").write_text("hi", encoding="utf-8")
    commit("init")
    _managed(monkeypatch, tmp_path, {"permissions": {"deny": ["Bash(git push origin main)"]}})
    block = scan_agent_ops(repo)
    assert block["managed"] == {"state": "not_read", "sources": []}
    assert block["summary"]["permissions_encoded"] is False
