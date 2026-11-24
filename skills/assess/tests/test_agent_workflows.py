"""Tests for the agent workflow detector in ``lib.agents.agent_ops`` (#815, #847).

A workflow counts when it runs an agent on a schedule (a routine, #815) or on an issue, pull
request, review or discussion event (#847). Both feed one condition, ``agent_work_present``.
"""
from __future__ import annotations

from pathlib import Path

from lib.agents.agent_ops import scan_agent_ops, scan_agent_workflows
from lib.counts_only.content_free import counts_only

GH_AW_SCHEDULED = """---
on:
  schedule: weekly on monday
permissions:
  contents: read
engine: claude
---
# Docs drift

Read the docs and open an issue for each page that no longer matches the code.
"""

AGENT_STEPS = """jobs:
  agent:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: anthropics/claude-code-action@v1
        with:
          prompt: Triage this issue.
"""

PLAIN_STEPS = """jobs:
  label:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/github-script@v7
        with:
          script: console.log("labelled")
"""


def _repo(git_repo, files: dict[str, str]) -> Path:
    repo, commit = git_repo
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    commit("init")
    return repo


def _summary(repo: Path) -> dict[str, object]:
    return dict(scan_agent_ops(repo)["summary"])


# -- #815 success criteria -------------------------------------------------------------

def test_a_scheduled_gh_aw_workflow_is_a_routine_and_is_cited(git_repo) -> None:
    repo = _repo(git_repo, {".github/workflows/docs-drift.md": GH_AW_SCHEDULED})
    block = scan_agent_ops(repo)
    assert block["summary"]["routines_present"] is True
    assert block["summary"]["scheduled_agent_workflows"] == 1
    assert block["agent_workflows"] == [{
        "path": ".github/workflows/docs-drift.md", "triggers": ["schedule"], "signs": ["gh_aw"],
        "via": [], "scheduled": True, "event": False}]


def test_an_agent_workflow_on_issues_opened_only_is_not_a_routine(git_repo) -> None:
    repo = _repo(git_repo, {
        ".github/workflows/triage.yml": "on:\n  issues:\n    types: [opened]\n" + AGENT_STEPS})
    summary = _summary(repo)
    assert summary["routines_present"] is False
    assert summary["scheduled_agent_workflows"] == 0
    # ... but it is an event-triggered agent (#847), which counts toward the same condition.
    assert summary["event_agent_workflows"] == 1
    assert summary["agent_work_present"] is True


# -- scheduled workflows (#815) -----------------------------------------------------------

def test_a_scheduled_workflow_using_an_agent_action_is_a_routine(git_repo) -> None:
    repo = _repo(git_repo, {
        ".github/workflows/nightly.yml": "on:\n  schedule:\n    - cron: '0 3 * * *'\n" + AGENT_STEPS})
    summary = _summary(repo)
    assert (summary["routines_present"], summary["agent_work_present"]) == (True, True)


def test_a_scheduled_workflow_naming_a_model_in_a_container_is_a_routine(git_repo) -> None:
    text = """on:
  schedule:
    - cron: '0 9 * * 1'
jobs:
  digest:
    runs-on: ubuntu-latest
    steps:
      - run: |
          docker run --rm \\
            -e OZ_MODEL="claude-4-8-opus-high" \\
            agent-image:latest digest
"""
    repo = _repo(git_repo, {".github/workflows/weekly-digest.yml": text})
    flow = scan_agent_ops(repo)["agent_workflows"][0]
    assert flow["signs"] == ["model_name"]
    assert flow["scheduled"] is True


def test_a_scheduled_workflow_with_only_a_provider_secret_is_not_a_routine(git_repo) -> None:
    text = """on:
  schedule:
    - cron: '0 3 * * *'
jobs:
  tests:
    runs-on: ubuntu-latest
    env:
      OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
    steps:
      - run: pytest tests/integration
"""
    repo = _repo(git_repo, {".github/workflows/nightly.yml": text})
    summary = _summary(repo)
    assert (summary["routines_present"], summary["agent_work_present"]) == (False, False)


def test_a_scheduled_workflow_that_runs_codex_exec_is_a_routine(git_repo) -> None:
    text = """on:
  schedule:
    - cron: '0 3 * * *'
jobs:
  fix:
    runs-on: ubuntu-latest
    steps:
      - run: npm install -g @openai/codex
      - run: codex exec "fix the flaky tests"
"""
    repo = _repo(git_repo, {".github/workflows/nightly.yml": text})
    assert scan_agent_ops(repo)["agent_workflows"][0]["signs"] == ["agent_cli"]


def test_an_install_line_alone_is_not_a_sign(git_repo) -> None:
    text = """on:
  schedule:
    - cron: '0 3 * * *'
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: npm install -g @anthropic-ai/claude-code
      - run: claude --version
"""
    repo = _repo(git_repo, {".github/workflows/nightly.yml": text})
    assert scan_agent_ops(repo)["agent_workflows"] == []


def test_the_gh_aw_lock_file_is_not_counted_again(git_repo) -> None:
    lock = "on:\n  schedule:\n    - cron: '0 3 * * 1'\n" + AGENT_STEPS
    repo = _repo(git_repo, {".github/workflows/docs-drift.md": GH_AW_SCHEDULED,
                            ".github/workflows/docs-drift.lock.yml": lock})
    assert [w["path"] for w in scan_agent_ops(repo)["agent_workflows"]] == [
        ".github/workflows/docs-drift.md"]


def test_a_markdown_file_without_frontmatter_is_not_a_gh_aw_workflow(git_repo) -> None:
    repo = _repo(git_repo, {".github/workflows/README.md": "# Workflows\n\non: schedule\n"})
    assert scan_agent_ops(repo)["agent_workflows"] == []


def test_a_gh_aw_frequency_shorthand_is_a_schedule(git_repo) -> None:
    repo = _repo(git_repo, {".github/workflows/recap.md": "---\non: daily\n---\nRecap the day.\n"})
    assert _summary(repo)["routines_present"] is True


def test_a_routine_dir_still_counts(git_repo) -> None:
    repo = _repo(git_repo, {".claude/routines/nightly.md": "run the tests"})
    summary = _summary(repo)
    assert (summary["routines_present"], summary["agent_work_present"]) == (True, True)


def test_an_untracked_workflow_is_not_counted(git_repo) -> None:
    repo = _repo(git_repo, {"README.md": "hi"})
    path = repo / ".github/workflows/nightly.yml"
    path.parent.mkdir(parents=True)
    path.write_text("on:\n  schedule:\n    - cron: '0 3 * * *'\n" + AGENT_STEPS, encoding="utf-8")
    assert _summary(repo)["agent_work_present"] is False


# -- event-triggered workflows (#847) -----------------------------------------------------

def test_an_issues_triage_workflow_with_an_agent_step_counts(git_repo) -> None:
    repo = _repo(git_repo, {".github/workflows/triage.yml": "on: issues\n" + AGENT_STEPS})
    flows = scan_agent_ops(repo)["agent_workflows"]
    assert [(w["path"], w["triggers"], w["event"]) for w in flows] == [
        (".github/workflows/triage.yml", ["issues"], True)]


def test_an_issues_workflow_with_no_agent_does_not_count(git_repo) -> None:
    repo = _repo(git_repo, {".github/workflows/label.yml": "on: issues\n" + PLAIN_STEPS})
    summary = _summary(repo)
    assert (summary["event_agent_workflows"], summary["agent_work_present"]) == (0, False)


def test_an_agent_named_only_in_a_comment_or_an_if_does_not_count(git_repo) -> None:
    text = """on:
  issue_comment:
    types: [created]
jobs:
  respond:
    # Replaced anthropics/claude-code-action with a plain script; run claude -p by hand.
    if: contains(github.event.comment.body, 'claude -p') || contains(github.event.comment.body, 'claude-3')
    runs-on: ubuntu-latest
    steps:
      - name: Thank claude-opus users
        run: echo "thanks"  # codex exec is not run here
"""
    repo = _repo(git_repo, {".github/workflows/respond.yml": text})
    assert _summary(repo)["event_agent_workflows"] == 0


def test_a_push_only_agent_workflow_is_neither_kind(git_repo) -> None:
    repo = _repo(git_repo, {".github/workflows/ci.yml": "on: push\n" + AGENT_STEPS})
    assert scan_agent_ops(repo)["agent_workflows"] == []


def test_a_pull_request_review_workflow_counts(git_repo) -> None:
    repo = _repo(git_repo, {".github/workflows/review.yml": "on:\n  pull_request:\n" + AGENT_STEPS})
    assert _summary(repo)["event_agent_workflows"] == 1


# -- reusable workflows -------------------------------------------------------------------

CALLED_AGENT = "on:\n  workflow_call:\n" + AGENT_STEPS
CALLER = """on:
  issues:
    types: [opened]
jobs:
  triage:
    uses: ./.github/workflows/agent.yml
    secrets: inherit
"""


def test_the_detector_follows_a_local_reusable_workflow(git_repo) -> None:
    repo = _repo(git_repo, {".github/workflows/agent.yml": CALLED_AGENT,
                            ".github/workflows/triage.yml": CALLER})
    flows = scan_agent_ops(repo)["agent_workflows"]
    # The caller counts with its own trigger; the called workflow alone fires on no event.
    assert [(w["path"], w["via"], w["signs"]) for w in flows] == [
        (".github/workflows/triage.yml", [".github/workflows/agent.yml"], ["agent_action"])]


def test_reusable_workflows_are_followed_through_two_levels(git_repo) -> None:
    middle = "on:\n  workflow_call:\njobs:\n  go:\n    uses: ./.github/workflows/agent.yml\n"
    caller = CALLER.replace("agent.yml", "middle.yml")
    repo = _repo(git_repo, {".github/workflows/agent.yml": CALLED_AGENT,
                            ".github/workflows/middle.yml": middle,
                            ".github/workflows/triage.yml": caller})
    flow = scan_agent_ops(repo)["agent_workflows"][0]
    assert flow["via"] == [".github/workflows/agent.yml", ".github/workflows/middle.yml"]


def test_a_reusable_workflow_cycle_terminates(git_repo) -> None:
    a = "on:\n  workflow_call:\njobs:\n  go:\n    uses: ./.github/workflows/b.yml\n"
    b = "on:\n  workflow_call:\njobs:\n  go:\n    uses: ./.github/workflows/a.yml\n"
    caller = CALLER.replace("agent.yml", "a.yml")
    repo = _repo(git_repo, {".github/workflows/a.yml": a, ".github/workflows/b.yml": b,
                            ".github/workflows/triage.yml": caller})
    assert scan_agent_ops(repo)["agent_workflows"] == []


def test_a_local_composite_action_running_an_agent_counts(git_repo) -> None:
    action = """runs:
  using: composite
  steps:
    - run: claude -p "summarise the open issues"
      shell: bash
"""
    flow = """on:
  schedule:
    - cron: '0 3 * * *'
jobs:
  recap:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/recap
"""
    repo = _repo(git_repo, {".github/actions/recap/action.yml": action,
                            ".github/workflows/recap.yml": flow})
    assert scan_agent_ops(repo)["agent_workflows"][0]["signs"] == ["agent_cli"]


def test_a_remote_reusable_workflow_naming_an_agent_reads_not_measured(git_repo) -> None:
    caller = CALLER.replace("./.github/workflows/agent.yml", "acme/shared/.github/workflows/agent.yml@v1")
    repo = _repo(git_repo, {".github/workflows/triage.yml": caller})
    block = scan_agent_ops(repo)
    assert block["agent_workflows_unread"] == 1
    assert block["summary"]["agent_work_present"] is None
    assert block["summary"]["agent_work_present_reason"] == "agent_workflows_unread"
    assert block["summary"]["routines_present"] is False


def test_ordinary_shared_ci_from_another_repository_reads_unmet(git_repo) -> None:
    caller = CALLER.replace("./.github/workflows/agent.yml", "acme/shared/.github/workflows/ci.yml@main")
    block = scan_agent_ops(repo := _repo(git_repo, {".github/workflows/ci.yml": caller}))
    assert (block["agent_workflows_unread"], block["summary"]["agent_work_present"]) == (0, False)
    assert repo.is_dir()


def test_a_prompt_that_says_add_or_install_still_counts(git_repo) -> None:
    text = """on:
  issues:
    types: [opened]
jobs:
  triage:
    runs-on: ubuntu-latest
    steps:
      - run: codex exec "add a label to this issue"
  review:
    runs-on: ubuntu-latest
    steps:
      - run: claude -p 'install the deps and add tests'
"""
    repo = _repo(git_repo, {".github/workflows/triage.yml": text})
    assert scan_agent_ops(repo)["agent_workflows"][0]["signs"] == ["agent_cli"]


def test_a_workflow_read_inside_a_chain_is_read_whole_at_the_top(git_repo) -> None:
    a = "on:\n  workflow_call:\n" + AGENT_STEPS + "  go:\n    uses: ./.github/workflows/b.yml\n"
    b = "on:\n  issues:\n  workflow_call:\njobs:\n  go:\n    uses: ./.github/workflows/a.yml\n"
    caller = CALLER.replace("agent.yml", "a.yml")
    repo = _repo(git_repo, {".github/workflows/a.yml": a, ".github/workflows/b.yml": b,
                            ".github/workflows/triage.yml": caller})
    flows = {w["path"]: w["via"] for w in scan_agent_ops(repo)["agent_workflows"]}
    assert flows == {".github/workflows/b.yml": [".github/workflows/a.yml"],
                     ".github/workflows/triage.yml": [".github/workflows/a.yml"]}


def test_a_remote_call_does_not_hide_a_found_agent(git_repo) -> None:
    caller = CALLER.replace("./.github/workflows/agent.yml", "acme/shared/.github/workflows/agent.yml@v1")
    repo = _repo(git_repo, {".github/workflows/triage.yml": caller,
                            ".github/workflows/review.yml": "on: pull_request\n" + AGENT_STEPS})
    summary = _summary(repo)
    assert summary["agent_work_present"] is True
    assert "agent_work_present_reason" not in summary


def test_scan_agent_workflows_without_git_walks_the_tree(tmp_path: Path) -> None:
    path = tmp_path / ".github/workflows/triage.yml"
    path.parent.mkdir(parents=True)
    path.write_text("on: issues\n" + AGENT_STEPS, encoding="utf-8")
    flows, unread = scan_agent_workflows(tmp_path)
    assert ([w["path"] for w in flows], unread) == ([".github/workflows/triage.yml"], 0)


# -- counts-only --------------------------------------------------------------------------

def test_counts_only_keeps_every_agent_workflow_field_and_count(git_repo) -> None:
    repo = _repo(git_repo, {".github/workflows/agent.yml": CALLED_AGENT,
                            ".github/workflows/triage.yml": CALLER,
                            ".github/workflows/docs-drift.md": GH_AW_SCHEDULED})
    block = scan_agent_ops(repo)
    out = counts_only({"agent_ops": block})["agent_ops"]
    assert out["agent_workflows_unread"] == block["agent_workflows_unread"]
    for field in ("routines_present", "scheduled_agent_workflows", "event_agent_workflows",
                  "agent_work_present"):
        assert out["summary"][field] == block["summary"][field], field
    assert out["agent_workflows"] == block["agent_workflows"]


def test_counts_only_keeps_the_unread_reason(git_repo) -> None:
    caller = CALLER.replace("./.github/workflows/agent.yml", "acme/shared/.github/workflows/agent.yml@v1")
    repo = _repo(git_repo, {".github/workflows/triage.yml": caller})
    out = counts_only({"agent_ops": scan_agent_ops(repo)})["agent_ops"]
    assert (out["summary"]["agent_work_present"], out["summary"]["agent_work_present_reason"]) == (
        None, "agent_workflows_unread")
