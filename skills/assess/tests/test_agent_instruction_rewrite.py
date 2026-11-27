"""Tests for the agent instruction-rewrite hazard (#858, ``lib.agents.agent_instruction_rewrite``).

An agent job that can write files, while an instruction file or its own workflow has no
CODEOWNERS owner, holds the ``ci_runs_tests`` Step gate and never moves the tally. Kept apart
from ``test_agent_workflows.py``, which sits at the file-size limit.
"""
from __future__ import annotations

from pathlib import Path

import lib.scoring.level_tally as lt
from lib.agents.agent_ops import scan_agent_ops
from lib.counts_only.content_free import counts_only
from lib.docs.ownership_parser import codeowned

WORKFLOW = ".github/workflows/docs-sync.yml"

SCHEDULED_WRITER = """on:
  schedule:
    - cron: "0 3 * * 1"
jobs:
  sync:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - run: claude -p "Update AGENTS.md to match the code" && git commit -am sync && git push
"""

GH_AW_PR = """---
on:
  schedule: weekly on monday
permissions:
  contents: read
engine: claude
safe-outputs:
  create-pull-request:
---
# Refresh the agent guide
"""

OWNERS = "* @example-org/maintainers\n"
FILES_OWNED = f"/AGENTS.md @example-org/maintainers\n/CLAUDE.md @example-org/maintainers\n/{WORKFLOW} @example-org/maintainers\n"


def _repo(git_repo, files: dict[str, str]) -> Path:
    repo, commit = git_repo
    for rel, text in {"AGENTS.md": "# Agents\n", **files}.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    commit("init")
    return repo


def _level(block: dict[str, object]) -> lt.LevelResult:
    data = {"layer_grades": {"instruction_file_present": True}, "ci_checks": {"enforced_check_runs": True},
            "agent_ops": block}
    return lt.level_result(lt.default_model(), data)


def test_a_scheduled_writer_with_no_codeowners_holds_the_ci_gate_and_leaves_the_tally(git_repo) -> None:
    block = scan_agent_ops(_repo(git_repo, {WORKFLOW: SCHEDULED_WRITER}))
    assert block["summary"]["agent_instruction_rewrite"] == 1
    assert block["agent_instruction_rewrite_files"] == [WORKFLOW]
    held = _level(dict(block))  # type: ignore[arg-type]
    clean = _level({**block, "summary": {**block["summary"], "agent_instruction_rewrite": 0}})
    assert clean["step"] == 3 and held["step"] == 2
    assert held["tally"] == clean["tally"]
    assert held["next_step"] is not None
    assert {"kind": "gate", "gate": "ci_runs_tests", "condition": "agent-rewrites-instructions.1",
            "state": "met", "polarity": "hazard"} in held["next_step"]["blockers"]
    entry = next(h for h in held["hazards"] if h["id"] == "agent-rewrites-instructions.1")
    assert entry["gate"] == "ci_runs_tests" and "CODEOWNERS" in entry["fix"]


def test_codeowners_on_the_instruction_files_and_the_workflow_clears_it(git_repo) -> None:
    for owners in (OWNERS, FILES_OWNED):
        repo = _repo(git_repo, {WORKFLOW: SCHEDULED_WRITER, ".github/CODEOWNERS": owners})
        summary = scan_agent_ops(repo)["summary"]
        assert summary["agent_instruction_rewrite"] == 0
        assert "agent_instruction_rewrite_reason" not in summary


def test_codeowners_that_leaves_the_workflow_unowned_still_raises_it(git_repo) -> None:
    owners = "/AGENTS.md @example-org/maintainers\n/CLAUDE.md @example-org/maintainers\n"
    repo = _repo(git_repo, {WORKFLOW: SCHEDULED_WRITER, "CODEOWNERS": owners})
    assert scan_agent_ops(repo)["summary"]["agent_instruction_rewrite"] == 1


def test_a_read_only_agent_job_does_not_raise_it(git_repo) -> None:
    text = SCHEDULED_WRITER.replace("contents: write", "contents: read")
    summary = scan_agent_ops(_repo(git_repo, {WORKFLOW: text}))["summary"]
    assert summary["agent_instruction_rewrite"] == 0


def test_a_write_scope_without_contents_does_not_raise_it(git_repo) -> None:
    text = SCHEDULED_WRITER.replace("contents: write", "contents: read\n      issues: write")
    assert scan_agent_ops(_repo(git_repo, {WORKFLOW: text}))["summary"]["agent_instruction_rewrite"] == 0


def test_a_gh_aw_source_that_opens_pull_requests_raises_it_until_owned(git_repo) -> None:
    rel = ".github/workflows/guide.md"
    repo = _repo(git_repo, {rel: GH_AW_PR})
    block = scan_agent_ops(repo)
    assert block["summary"]["agent_instruction_rewrite"] == 1
    assert block["agent_instruction_rewrite_files"] == [rel]
    comment_only = GH_AW_PR.replace("create-pull-request:", "add-comment:")
    assert scan_agent_ops(_repo(git_repo, {rel: comment_only}))["summary"]["agent_instruction_rewrite"] == 0


def test_no_agent_job_or_an_unread_scope_is_not_measured(git_repo) -> None:
    summary = scan_agent_ops(_repo(git_repo, {}))["summary"]
    assert summary["agent_instruction_rewrite"] is None
    assert summary["agent_instruction_rewrite_reason"] == "no_agent_jobs"
    text = SCHEDULED_WRITER.replace("      contents: write\n", "      contents: ${{ inputs.scope }}\n")
    summary = scan_agent_ops(_repo(git_repo, {WORKFLOW: text}))["summary"]
    assert summary["agent_instruction_rewrite"] is None
    assert summary["agent_instruction_rewrite_reason"] == "agent_job_scope_unread"


def test_a_not_measured_hazard_holds_no_gate(git_repo) -> None:
    block = scan_agent_ops(_repo(git_repo, {}))
    assert _level(dict(block))["step"] == 3  # type: ignore[arg-type]


def test_the_count_and_files_survive_counts_only(git_repo) -> None:
    block = scan_agent_ops(_repo(git_repo, {WORKFLOW: SCHEDULED_WRITER}))
    kept = counts_only({"agent_ops": block})["agent_ops"]
    assert kept["summary"]["agent_instruction_rewrite"] == 1
    assert kept["agent_instruction_rewrite_files"] == [WORKFLOW]
    null = {**block, "summary": {**block["summary"], "agent_instruction_rewrite": None,
                                 "agent_instruction_rewrite_reason": "no_agent_jobs"}}
    assert counts_only({"agent_ops": null})["agent_ops"]["summary"]["agent_instruction_rewrite_reason"] == "no_agent_jobs"


def test_codeowned_reads_the_last_matching_line() -> None:
    text = "# owners\n* @org/all\n/docs/ @org/docs\n/AGENTS.md\n"
    assert codeowned(text, "src/a.py") and codeowned(text, "docs/x.md")
    assert not codeowned(text, "AGENTS.md")  # a later line with no owner clears it
    assert not codeowned("", "AGENTS.md")
    assert codeowned("CLAUDE.md @org/a  # trailing comment\n", "sub/CLAUDE.md")
