"""Tests for AI review committed in CI and the Layer 7 review evidence (issue #696)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from lib.delivery import AI_REVIEWERS
from lib.fleet import STATUS_OK, FleetRow
from lib.fleet_config import empty_config, parse_fleet_config
from lib.fleet_groups import platform_only
from lib.fleet_platform import failed_blocks, join
from lib.level_signals import load_default_levels
from lib.levels import evaluate_row
from lib.review_automation import (
    REVIEW_CONFIG_FILES,
    is_review_action,
    is_review_component,
    review_evidence,
    scan_review_automation,
)
from lib.scan_registry import SCANS
from lib.shared_tooling import action_repo, step_uses, workflow_events
from lib.signals import NETWORK_REASON, PLATFORM_SCANS, build_document

WORKFLOW = """\
name: review
on: pull_request
jobs:
  shared:
    uses: org/templates/.github/workflows/ci.yml@v1   # a reusable workflow, not a step
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Review
        uses: anthropics/claude-code-action@v1
        with:
          prompt: |
            uses: coderabbitai/fake@v1
      - run: |
          echo "uses: anthropics/printed@v1"
  flat:
    steps:
    - uses: "./local-action"
    - uses: docker://alpine:3
    - uses: Qodo-AI/pr-agent/sub@main
"""

GITLAB_CI = """\
include:
  - component: $CI_SERVER_FQDN/platform/components/ai-code-review@1.2.0
  - component: gitlab.example.com/platform/components/sast@1.0
  - project: 'tools/review-kit'
    file: '/review.yml'
build:
  script: make
"""


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(repo: Path, *files: str) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    if files:
        subprocess.run(["git", "add", *files], cwd=repo, check=True)


# --- the parsers --------------------------------------------------------------


def test_step_uses_reads_steps_only_and_skips_block_scalars() -> None:
    assert step_uses(WORKFLOW) == [
        (9, "actions/checkout@v4"),
        (11, "anthropics/claude-code-action@v1"),
        (19, "./local-action"),
        (20, "docker://alpine:3"),
        (21, "Qodo-AI/pr-agent/sub@main"),
    ]


def test_step_uses_ignores_uses_nested_under_with() -> None:
    text = ("jobs:\n  r:\n    steps:\n      - name: x\n        with:\n"
            "          uses: anthropics/foo@v1\n        uses: actions/setup-node@v4\n"
            "      - with:\n          uses: anthropics/bar@v1\n")
    assert step_uses(text) == [(7, "actions/setup-node@v4")]


@pytest.mark.parametrize(("on", "events"), [
    ("on: pull_request\n", {"pull_request"}),
    ("on: [push, 'pull_request_target']\n", {"push", "pull_request_target"}),
    ("on: {issue_comment: {types: [created]}}\n", {"issue_comment"}),
    ("on:\n  - push\n  - merge_group\n", {"push", "merge_group"}),
    ("'on':\n  issues:\n    types: [opened]\n  issue_comment:\n", {"issues", "issue_comment"}),
    ("name: x\n", None),
])
def test_workflow_events(on: str, events: set[str] | None) -> None:
    text = on + "jobs:\n  a:\n    steps:\n      - uses: x/y@v1\n"
    assert workflow_events(text) == (None if events is None else frozenset(events))


def _claude_workflow(on: str) -> str:
    return f"{on}jobs:\n  claude:\n    steps:\n      - uses: anthropics/claude-code-action@v1\n"


def test_a_mention_responder_is_listed_but_not_credited(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/claude.yml",
           _claude_workflow("on:\n  issue_comment:\n    types: [created]\n  issues:\n"))
    _git(tmp_path, ".")
    block = scan_review_automation(tmp_path)
    assert block["present"] is False and block["kinds"] == []
    assert [(m["reviewer"], m["on_changes"]) for m in block["matches"]] == [("claude-code", False)]


def test_a_pull_request_workflow_is_credited(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/claude.yml", _claude_workflow("on: [issue_comment, pull_request]\n"))
    _git(tmp_path, ".")
    block = scan_review_automation(tmp_path)
    assert block["present"] is True and block["matches"][0]["on_changes"] is True


def test_action_repo_names_the_repository() -> None:
    assert action_repo("anthropics/claude-code-action@v1") == "anthropics/claude-code-action"
    assert action_repo("Qodo-AI/pr-agent/sub@main") == "qodo-ai/pr-agent"
    assert action_repo("'actions/checkout@v4'") == "actions/checkout"
    assert action_repo("./local-action") is None
    assert action_repo("docker://alpine:3") is None
    assert action_repo("not a ref") is None


@pytest.mark.parametrize(("repo", "slug"), [
    ("anthropics/claude-code-action", "claude-code"),
    ("anthropics/claude-code-base-action", "claude-code"),
    ("coderabbitai/ai-pr-reviewer", "coderabbit"),
    ("codium-ai/pr-agent", "qodo"),
    ("actions/checkout", None),
    ("anthropics-fork/claude-code-action", None),
])
def test_pinned_review_actions(repo: str, slug: str | None) -> None:
    assert is_review_action(repo) == slug


@pytest.mark.parametrize(("source", "matched"), [
    ("platform/components/ai-code-review", True),
    ("ci/claude_review", True),
    ("ci/llm-review", True),
    ("ci/code-review-ai", True),
    ("ci/review-bot", False),
    ("ci/pr-agent", True),
    ("platform/components/sast", False),
    ("tools/review-kit", False),
    ("ai-code-review/templates", False),  # only the last segment is the component
    ("ci/preview", False),
])
def test_pinned_review_components(source: str, matched: bool) -> None:
    assert is_review_component(source) is matched


def test_review_config_files_name_known_ai_reviewers() -> None:
    assert set(REVIEW_CONFIG_FILES.values()) <= AI_REVIEWERS


# --- the scan ------------------------------------------------------------------


def test_github_workflow_step_sets_present(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/review.yml", WORKFLOW)
    _git(tmp_path, ".")
    block = scan_review_automation(tmp_path)
    assert block["available"] is True and block["present"] is True
    assert block["kinds"] == ["ci_action"]
    assert [(m["reviewer"], m["line"], m["rule"]) for m in block["matches"]] == [
        ("claude-code", 11, "pinned"), ("qodo", 21, "pinned")]


def test_gitlab_component_sets_present_and_config_list_extends_it(tmp_path: Path) -> None:
    _write(tmp_path, ".gitlab-ci.yml", GITLAB_CI)
    _git(tmp_path, ".")
    block = scan_review_automation(tmp_path)
    assert block["present"] is True and block["kinds"] == ["ci_component"]
    assert [m["line"] for m in block["matches"]] == [2]
    config = parse_fleet_config('[review]\nai_review_components = ["gitlab.example.com/Tools/review-kit@2"]\n')
    configured = scan_review_automation(tmp_path, config)
    assert [(m["line"], m["rule"], m["reviewer"]) for m in configured["matches"]] == [
        (2, "pinned", "ai-review-component"), (4, "config", "configured")]


def test_a_config_project_path_covers_its_components(tmp_path: Path) -> None:
    _write(tmp_path, ".gitlab-ci.yml", "include:\n  - component: gl.example/acme/ci/lint@1\n")
    _git(tmp_path, ".")
    config = parse_fleet_config('[review]\nai_review_components = ["acme/ci"]\n')
    assert scan_review_automation(tmp_path, config)["present"] is True
    variable = parse_fleet_config('[review]\nai_review_components = ["$CI_SERVER_FQDN/acme/ci@1"]\n')
    assert scan_review_automation(tmp_path, variable)["present"] is True
    assert scan_review_automation(tmp_path, empty_config())["present"] is False


def test_review_app_config_file(tmp_path: Path) -> None:
    _write(tmp_path, ".coderabbit.yaml", "reviews: {}\n")
    _git(tmp_path, ".")
    block = scan_review_automation(tmp_path)
    assert block["kinds"] == ["config_file"]
    assert block["matches"][0]["reviewer"] == "coderabbitai"


def test_untracked_files_are_listed_but_not_credited(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/review.yml", WORKFLOW)
    _write(tmp_path, "README.md", "x\n")
    _git(tmp_path, "README.md")
    block = scan_review_automation(tmp_path)
    assert block["present"] is False and block["kinds"] == []
    assert all(m["tracked"] is False for m in block["matches"]) and block["matches"]


def test_no_review_step_reads_false(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/ci.yml", "jobs:\n  t:\n    steps:\n      - uses: actions/checkout@v4\n")
    _git(tmp_path, ".")
    assert scan_review_automation(tmp_path)["present"] is False


def test_unknown_tracking_is_unavailable(tmp_path: Path) -> None:
    _write(tmp_path, ".coderabbit.yaml", "x\n")
    block = scan_review_automation(tmp_path)
    assert block["available"] is False and block["present"] is False
    assert block["reason"].startswith("git_tracking_unknown:")


def test_symlinked_config_is_skipped(tmp_path: Path) -> None:
    outside = tmp_path / "outside.yaml"
    outside.write_text("x\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".coderabbit.yaml").symlink_to(outside)
    _git(repo, ".")
    assert scan_review_automation(repo)["matches"] == []


# --- review_evidence ---------------------------------------------------------------

AUTO_YES: dict[str, Any] = {"available": True, "present": True, "kinds": ["ci_action"]}
AUTO_CONFIG: dict[str, Any] = {"available": True, "present": True, "kinds": ["config_file"]}
AUTO_NO: dict[str, Any] = {"available": True, "present": False}
AUTO_UNKNOWN: dict[str, Any] = {"available": False, "present": False, "reason": "git_tracking_unknown: x"}
OFFLINE: dict[str, Any] = {"available": False, "reason": NETWORK_REASON}


def _reality(share: float | None) -> dict[str, Any]:
    return {"available": True, "reviewed_share": share}


@pytest.mark.parametrize(("automation", "reality", "present"), [
    (AUTO_YES, OFFLINE, True),
    (AUTO_YES, _reality(0.0), True),
    (AUTO_NO, _reality(0.5), True),
    (AUTO_UNKNOWN, _reality(0.5), True),
    (AUTO_NO, _reality(0.0), False),
    (AUTO_CONFIG, OFFLINE, True),  # nothing measured: the config file counts
    (AUTO_CONFIG, _reality(0.0), False),  # measured 0: a config file alone is contradicted
    (AUTO_CONFIG, {"available": True, "reviewed_share": 0.0, "bot_review_share": 0.3}, True),
    (AUTO_NO, {"available": True, "reviewed_share": None, "approved_share": 0.4}, True),
])
def test_review_evidence_known(automation: dict[str, Any], reality: dict[str, Any], present: bool) -> None:
    block = review_evidence(automation, reality)
    assert block["available"] is True and block["present"] is present


@pytest.mark.parametrize(("automation", "reality", "code"), [
    (AUTO_NO, OFFLINE, "network"),
    (AUTO_UNKNOWN, OFFLINE, "network"),
    (AUTO_NO, _reality(None), "not_measured"),
    (AUTO_NO, {"available": True, "reviewed_share": None, "approved_share": 0.0}, "not_measured"),
    (AUTO_UNKNOWN, _reality(0.0), "git_tracking_unknown"),
    (None, None, "not_measured"),
])
def test_review_evidence_unknown_carries_the_reason(automation: Any, reality: Any, code: str) -> None:
    block = review_evidence(automation, reality)
    assert block["available"] is False and block["present"] is None
    assert block["reason"].split(":", 1)[0] == code


def test_review_evidence_is_registered_after_its_inputs_and_rebuilt_by_platform() -> None:
    keys = [spec.key for spec in SCANS]
    assert keys.index("review_reality") < keys.index("review_evidence")
    assert keys.index("review_automation") < keys.index("review_evidence")
    assert "review_evidence" in PLATFORM_SCANS
    assert platform_only({"review_evidence": review_evidence(AUTO_NO, OFFLINE)}, "review_evidence.present")


# --- Layer 7 in a headless run --------------------------------------------------------


def _layer7(scans: dict[str, Any]) -> dict[str, Any]:
    row = next(r for r in load_default_levels()["rows"] if r["id"] == "layer-7-review")
    return dict(evaluate_row(row, scans))


@pytest.mark.parametrize(("rel", "text"), [
    (".github/workflows/review.yml", WORKFLOW),
    (".gitlab-ci.yml", GITLAB_CI),
])
def test_headless_run_gives_layer7_a_floor_of_one(tmp_path: Path, rel: str, text: str) -> None:
    _write(tmp_path, rel, text)
    _git(tmp_path, ".")
    scans = build_document(tmp_path, ["review_automation", "review_evidence", "review_reality"])["scans"]
    assert scans["review_automation"]["present"] is True
    assert scans["review_evidence"]["present"] is True
    evaluation = _layer7(scans)
    assert (evaluation["status"], evaluation.get("floor")) == ("unknown", 1)


def test_selecting_review_reality_builds_the_level_one_evidence(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/review.yml", WORKFLOW)
    _git(tmp_path, ".")
    scans = build_document(tmp_path, ["review_reality"])["scans"]
    assert set(scans) == {"review_automation", "review_evidence", "review_reality"}
    assert scans["review_evidence"]["present"] is True
    assert _layer7(scans).get("floor") == 1


def test_headless_run_without_review_step_stays_unknown(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "x\n")
    _git(tmp_path, ".")
    scans = build_document(tmp_path, ["review_evidence"])["scans"]
    assert scans["review_evidence"]["available"] is False
    evaluation = _layer7(scans)
    assert evaluation["status"] == "unknown" and "floor" not in evaluation


def test_human_review_does_not_regress() -> None:
    reality = {"available": True, "reviewed_share": 0.9, "approved_share": 0.6}
    scans = {"review_reality": reality, "review_evidence": review_evidence(AUTO_NO, reality)}
    assert _layer7(scans)["level"] == 2
    low = {"available": True, "reviewed_share": 0.0, "approved_share": 0.0}
    assert _layer7({"review_reality": low, "review_evidence": review_evidence(AUTO_NO, low)})["level"] == 0


def _fleet_row(scans: dict[str, Any]) -> FleetRow:
    return {"fleet_row_version": 2, "repo": "g/a", "group": "g", "status": STATUS_OK, "reason": None,
            "signals_schema_version": 1, "scans": scans, "active": True}


def test_a_failed_platform_read_keeps_the_offline_ai_review_credit() -> None:
    offline = review_evidence(AUTO_YES, OFFLINE)
    joined = join(_fleet_row({"review_evidence": offline, "review_reality": OFFLINE}),
                  failed_blocks(["review_reality", "review_evidence"], "scan_timeout", counts_only=True))
    assert joined["scans"] is not None
    assert joined["scans"]["review_evidence"] == offline
    assert joined["scans"]["review_reality"]["reason"] == "platform_timeout"
    measured = {"available": True, "present": True}
    replaced = join(_fleet_row({"review_evidence": offline}), {"review_evidence": measured})
    assert replaced["scans"] is not None and replaced["scans"]["review_evidence"] == measured
