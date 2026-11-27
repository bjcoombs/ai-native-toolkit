"""AI governance (#851): an enforced policy counts, a declared one is listed, an opt-out reads DECLINED.

Every fixture is invented: workflow names, policy text and instruction files exist only here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import lib.scoring.level_tally as lt
from lib.ai_governance import (COPILOT_OPT_OUT, NO_PR_WORKFLOWS, OPT_OUT_CONDITIONS, declines_review,
                               disclosure_checkboxes, enforces_disclosure, read_copilot_opt_out, scan_ai_governance)
from lib.counts_only.content_free import counts_only
from lib.owner_exclusions import owner_exclusions_block
from lib.scoring.level_result_types import DeclineRecord

ENFORCE = """name: disclosure
on:
  pull_request:
    types: [opened, edited, synchronize]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - name: Require an AI disclosure
        env:
          BODY: ${{ github.event.pull_request.body }}
        run: |
          if ! printf '%s' "$BODY" | grep -qi 'AI assistance:'; then
            echo "::error::state whether AI tools were used"
            exit 1
          fi
"""

TRAILERS = """name: trailers
on: [pull_request]
jobs:
  trailers:
    runs-on: ubuntu-latest
    steps:
      - run: python3 tools/check_trailers.py
"""

TRAILER_SCRIPT = """import subprocess, sys
log = subprocess.run(["git", "log", "--format=%(trailers)", "origin/main..HEAD"],
                     capture_output=True, text=True).stdout
if "Co-authored-by: Claude" in log or "Generated-by:" in log:
    sys.exit("remove the AI attribution trailer")
"""

PLAIN = """name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: pytest
"""

TEMPLATE = """## Summary

- [ ] Tests added
- [ ] I disclose any AI assistance used to write this change
"""

CHANGE_TYPE = """## Type of change

- [ ] Bug fix
- [ ] AI instructions (agent instruction files)
"""

DECLINE = """# Notes for Copilot

Please do not review pull requests in this repository.
"""


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def _level(tmp_path: Path, block: object) -> lt.LevelResult:
    data = {"layer_grades": {"instruction_file_present": True}, "ci_checks": {"enforced_check_runs": True},
            "ai_governance": block, "owner_exclusions": owner_exclusions_block(tmp_path)}
    return lt.level_result(lt.default_model(), data)


def _cond(result: lt.LevelResult, cid: str) -> lt.ConditionResult:
    return next(c for c in result["conditions"] if c["id"] == cid)


# --- counted: enforcement ------------------------------------------------------------------


def test_a_policy_a_checkbox_and_an_enforcing_workflow_meet_the_condition(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AI_POLICY.md": "# Policy\n", ".github/pull_request_template.md": TEMPLATE,
                            ".github/workflows/disclosure.yml": ENFORCE, ".github/workflows/ci.yml": PLAIN})
    block = scan_ai_governance(root)
    assert (block["enforced"], block["enforcing_workflows"]) == (True, [".github/workflows/disclosure.yml"])
    assert (block["policy_declared"], block["disclosure_checkboxes"]) == (True, 1)
    cond = _cond(_level(root, block), "ai-policy-enforced.1")
    assert (cond["state"], cond["grade"], cond["counted"]) == ("met", "measured", True)


def test_a_trailer_check_in_a_script_the_workflow_runs_meets_it(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/trailers.yml": TRAILERS, "tools/check_trailers.py": TRAILER_SCRIPT})
    assert scan_ai_governance(root)["enforced"] is True


@pytest.mark.parametrize("text", [
    ENFORCE.replace("exit 1", "echo noted").replace("::error::", ""),  # reads, never fails or labels
    ENFORCE.replace("github.event.pull_request.body", "github.event.pull_request.title"),  # reads no body
    ENFORCE.replace("AI assistance:", "Fixes #").replace("AI tools", "an issue").replace("an AI", "a"),  # no AI marker
])
def test_a_workflow_missing_one_part_does_not_enforce(text: str) -> None:
    assert enforces_disclosure(text) is False


def test_a_disclosure_check_off_pull_requests_does_not_count(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/disclosure.yml": ENFORCE.replace("  pull_request:\n", "  push:\n"),
                            ".github/workflows/ci.yml": PLAIN})
    assert scan_ai_governance(root)["enforced"] is False


def test_no_workflow_on_pull_requests_is_not_measured(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AI_POLICY.md": "# Policy\n"})
    block = scan_ai_governance(root)
    assert (block["enforced"], block["enforced_reason"]) == (None, NO_PR_WORKFLOWS)
    assert _cond(_level(root, block), "ai-policy-enforced.1")["state"] == "not_measured"


# --- listed: a declared policy ---------------------------------------------------------------


def test_a_policy_file_alone_is_listed_and_not_counted(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/AI_POLICY.md": "# Policy\n", ".github/workflows/ci.yml": PLAIN})
    block = scan_ai_governance(root)
    assert (block["policy_declared"], block["policy_files"], block["enforced"]) == (True, [".github/AI_POLICY.md"], False)
    result = _level(root, block)
    declared = _cond(result, "ai-policy-declared.1")
    assert (declared["state"], declared["grade"], declared["counted"]) == ("met", "declared", False)
    without = _level(root, {**block, "policy_declared": False})
    assert (result["tally"]["met"], result["tally"]["measured"]) == (without["tally"]["met"], without["tally"]["measured"])


def test_a_policy_heading_elsewhere_is_not_a_policy(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"docs/guide/AI_POLICY.md": "# Policy\n", "README.md": "## AI policy\n"})
    assert scan_ai_governance(root)["policy_declared"] is False


# --- false positives -----------------------------------------------------------------------


def test_a_change_type_checkbox_naming_ai_instructions_is_not_disclosure(tmp_path: Path) -> None:
    assert disclosure_checkboxes(CHANGE_TYPE) == 0
    root = _repo(tmp_path, {".github/PULL_REQUEST_TEMPLATE.md": CHANGE_TYPE})
    assert scan_ai_governance(root)["policy_declared"] is False


def test_a_changelog_line_that_does_not_accept_is_not_an_opt_out(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"CHANGELOG.md": "- The parser does not accept tabs; do not review this entry.\n"})
    assert (scan_ai_governance(root)["opt_out_files"], read_copilot_opt_out(root)) == ([], None)


# --- the opt-out reads DECLINED ------------------------------------------------------------


def test_a_copilot_file_that_declines_review_reads_declined(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/copilot-instructions.md": DECLINE})
    record = read_copilot_opt_out(root)
    assert record is not None and (record["source"], record["conditions"]) == (COPILOT_OPT_OUT, list(OPT_OUT_CONDITIONS))
    assert scan_ai_governance(root)["opt_out_files"] == [".github/copilot-instructions.md"]
    result = _level(root, scan_ai_governance(root))
    assert {d["id"] for d in result["declined"]} == set(OPT_OUT_CONDITIONS)
    for cid in OPT_OUT_CONDITIONS:
        cond = _cond(result, cid)
        assert (cond["state"], cond["counted"]) == ("declined", False)


def test_the_opt_out_never_declines_a_gate_or_an_unrelated_condition(tmp_path: Path) -> None:
    record: DeclineRecord = {"source": COPILOT_OPT_OUT, "conditions": ["layer-6-coverage.1", "layer-0-instructions.1",
                             "agent-least-privilege.1"], "declined_by": None, "declined_at": None,
                             "version": None, "reason": "x"}
    data = {"layer_grades": {"instruction_file_present": True}, "coverage_gate": {"enforced_meets_minimum": True}}
    result = lt.level_result(lt.default_model(), data, declines=[record])
    assert [d["id"] for d in result["declined"]] == ["agent-least-privilege.1"]
    assert _cond(result, "layer-6-coverage.1")["state"] == "met"
    assert _cond(result, "layer-0-instructions.1")["state"] == "met"
    whole: DeclineRecord = {**record, "conditions": None}
    scoped = lt.level_result(lt.default_model(), data, declines=[whole])
    runtime = {lt.condition_id(r["id"], lv["level"]) for r in lt.default_model()["rows"] for lv in r["levels"]
               if "agent_runtime" in lv["truth"]}
    assert scoped["declined"] and {d["id"] for d in scoped["declined"]} <= runtime
    assert _cond(scoped, "layer-0-instructions.1")["state"] == "met"


def test_a_declined_condition_never_counts_as_met(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/copilot-instructions.md": DECLINE})
    data = {"agent_ops": {"summary": {"agent_jobs_least_privilege": True}}, "owner_exclusions": owner_exclusions_block(root)}
    cond = _cond(lt.level_result(lt.default_model(), data), "agent-least-privilege.1")
    assert (cond["state"], cond["counted"]) == ("declined", False)


@pytest.mark.parametrize("text", [
    "Do not review generated files.\n",  # a part of the repository, not the whole
    "Do not review code in vendor/.\n",
    "Review pull requests for clarity.\n",
    "```\nDo not review pull requests.\n```\n",  # inside a code fence
])
def test_a_scoped_or_quoted_refusal_is_not_an_opt_out(text: str) -> None:
    assert declines_review(text) is False


def test_an_instruction_file_counts_only_when_it_applies_to_every_file(tmp_path: Path) -> None:
    body = "Copilot should not review this repository.\n"
    root = _repo(tmp_path, {
        ".github/instructions/all.instructions.md": f"---\napplyTo: \"**\"\n---\n{body}",
        ".github/instructions/docs.instructions.md": f"---\napplyTo: \"docs/**\"\n---\n{body}",
        ".github/instructions/coding.instructions.md": f"---\napplyTo: \"**\"\nexcludeAgent: \"code-review\"\n---\n{body}",
    })
    assert scan_ai_governance(root)["opt_out_files"] == [".github/instructions/all.instructions.md"]


def test_absence_never_declines(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/copilot-instructions.md": "Use type hints.\n"})
    assert read_copilot_opt_out(root) is None
    assert "declines" not in owner_exclusions_block(root)
    assert "declined" not in _level(root, scan_ai_governance(root))


def test_the_block_survives_counts_only_output(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AI_POLICY.md": "x\n", ".github/pull_request_template.md": TEMPLATE,
                            ".github/workflows/disclosure.yml": ENFORCE, ".github/copilot-instructions.md": DECLINE})
    block = scan_ai_governance(root)
    kept = counts_only({"ai_governance": block})["ai_governance"]
    assert kept == block
