"""AI governance (#851): an enforced policy counts, a declared one is listed, an opt-out reads DECLINED.

Every fixture is invented: workflow names, policy text and instruction files exist only here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import lib.scoring.level_tally as lt
from lib.ai_governance import (COPILOT_OPT_OUT, NO_PR_WORKFLOWS, declines_review,
                               disclosure_checkboxes, enforces_disclosure, read_copilot_opt_out, scan_ai_governance)
from lib.counts_only.content_free import counts_only
from lib.owner_exclusions import owner_exclusions_block
from lib.run.decline_markers import decline_badge
from lib.scoring.headline import badge_lines
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


@pytest.mark.parametrize("script", ["tools/check_trailers.py", "./tools/check_trailers.py", ".github/scripts/check_trailers.py"])
def test_a_trailer_check_in_a_script_the_workflow_runs_meets_it(tmp_path: Path, script: str) -> None:
    root = _repo(tmp_path, {".github/workflows/trailers.yml": TRAILERS.replace("tools/check_trailers.py", script),
                            script.removeprefix("./"): TRAILER_SCRIPT})
    assert scan_ai_governance(root)["enforced"] is True


@pytest.mark.parametrize("text", [
    ENFORCE.replace("exit 1", "echo noted").replace("::error::", ""),  # reads, never fails or labels
    ENFORCE.replace("github.event.pull_request.body", "github.event.pull_request.title"),  # reads no body
    ENFORCE.replace("AI assistance:", "Fixes #").replace("AI tools", "an issue").replace("an AI", "a"),  # no AI marker
])
def test_a_workflow_missing_one_part_does_not_enforce(text: str) -> None:
    assert enforces_disclosure(text) is False


CLAUDE_REVIEW = """name: review
on: [pull_request]
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: anthropics/claude-code-action@v1
        with:
          prompt: "Review this change. Context: ${{ github.event.pull_request.body }}"
      - run: test -f out || exit 1
"""


def test_a_read_marker_and_act_in_different_steps_do_not_enforce(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/review.yml": CLAUDE_REVIEW})
    assert scan_ai_governance(root)["enforced"] is False


def test_a_body_read_set_in_the_job_env_reaches_its_step(tmp_path: Path) -> None:
    text = ENFORCE.replace("        env:\n          BODY: ${{ github.event.pull_request.body }}\n", "").replace(
        "    runs-on: ubuntu-latest\n", "    runs-on: ubuntu-latest\n    env:\n      BODY: ${{ github.event.pull_request.body }}\n")
    assert scan_ai_governance(_repo(tmp_path, {".github/workflows/disclosure.yml": text}))["enforced"] is True


def test_running_an_ai_tool_on_the_body_is_not_checking_for_disclosure() -> None:
    step = 'run: |\n  echo "${{ github.event.pull_request.body }}" > b.txt\n  claude --print summary || exit 1\n'
    assert enforces_disclosure(step) is False


def test_a_wrapped_whole_refusal_still_reads_as_one() -> None:
    assert declines_review("Copilot should not review\npull requests in this repository.\n- Use type hints.\n") is True


SAME_COLUMN = """name: disclosure
on: pull_request
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
    - name: Require disclosure
      run: |
        printf '%s' "${{ github.event.pull_request.body }}" | grep -qi "Assisted-by" || exit 1
"""


def test_steps_in_the_same_column_as_steps_are_read(tmp_path: Path) -> None:
    assert scan_ai_governance(_repo(tmp_path, {".github/workflows/d.yml": SAME_COLUMN}))["enforced"] is True


def test_a_body_read_in_another_jobs_env_does_not_reach_a_step(tmp_path: Path) -> None:
    text = """name: two
on: pull_request
jobs:
  a:
    runs-on: ubuntu-latest
    env:
      BODY: ${{ github.event.pull_request.body }}
    steps:
      - run: echo "$BODY"
  b:
    runs-on: ubuntu-latest
    steps:
      - run: grep -q "AI" trailers.txt || exit 1
"""
    assert scan_ai_governance(_repo(tmp_path, {".github/workflows/two.yml": text}))["enforced"] is False


@pytest.mark.parametrize("where", ["step", "job"])
def test_a_check_that_cannot_turn_the_run_red_does_not_count_unless_it_labels(tmp_path: Path, where: str) -> None:
    flag = ("    steps:\n    - name: Require disclosure\n      continue-on-error: true\n" if where == "step"
            else "    continue-on-error: true\n    steps:\n    - name: Require disclosure\n")
    text = SAME_COLUMN.replace("    steps:\n    - name: Require disclosure\n", flag)
    assert scan_ai_governance(_repo(tmp_path, {".github/workflows/d.yml": text}))["enforced"] is False
    labelled = text.replace("|| exit 1", "|| gh pr edit --add-label needs-disclosure")
    assert scan_ai_governance(_repo(tmp_path, {".github/workflows/d.yml": labelled}))["enforced"] is True


def test_a_marker_tested_against_another_value_is_not_a_disclosure_check(tmp_path: Path) -> None:
    text = """name: linked
on: pull_request
jobs:
  linked:
    runs-on: ubuntu-latest
    env:
      BODY: ${{ github.event.pull_request.body }}
      AUTHOR: ${{ github.event.pull_request.user.login }}
    steps:
      - run: |
          if [[ "$AUTHOR" == "copilot-swe-agent[bot]" ]]; then exit 0; fi
          printf '%s' "$BODY" | grep -qE "(Fixes|Closes) #[0-9]+" || exit 1
"""
    assert scan_ai_governance(_repo(tmp_path, {".github/workflows/linked.yml": text}))["enforced"] is False


def test_an_error_annotation_alone_is_not_an_act() -> None:
    step = ('run: printf "%s" "${{ github.event.pull_request.body }}" | grep -qi "Assisted-by"'
            ' || echo "::error::add the Assisted-by trailer"\n')
    assert enforces_disclosure(step) is False
    assert enforces_disclosure(step.replace('trailer"\n', 'trailer"; exit 1\n')) is True
    assert enforces_disclosure(step.replace('::error::add the Assisted-by trailer', 'please exit 1')) is False


@pytest.mark.parametrize("exit_call", ["sys.exit(0)", "sys.exit()", "raise SystemExit", "raise SystemExit(0)"])
def test_an_exit_that_ends_the_run_green_is_not_an_act(exit_call: str) -> None:
    step = f'run: |\n  body = "${{{{ github.event.pull_request.body }}}}"\n  if "Assisted-by" in body: {exit_call}\n'
    assert enforces_disclosure(step) is False


@pytest.mark.parametrize("exit_call", ["sys.exit(1)", 'sys.exit("add the trailer")', "raise SystemExit(2)",
                                       "raise SystemExit(f'add it')"])
def test_a_non_zero_or_message_exit_is_an_act(exit_call: str) -> None:
    step = f'run: |\n  body = "${{{{ github.event.pull_request.body }}}}"\n  if "Assisted-by" not in body: {exit_call}\n'
    assert enforces_disclosure(step) is True


def test_a_body_written_to_a_file_and_tested_there_counts() -> None:
    step = "run: |\n  gh pr view 1 --json body > body.json\n  grep -q 'AI assistance' body.json || exit 1\n"
    assert enforces_disclosure(step) is True


@pytest.mark.parametrize("step", [
    'run: |\n  echo "AI disclosure scan"\n  gh pr view --json body > body.json\n  test -s body.json || exit 1\n',
    'run: |\n  BODY="${{ github.event.pull_request.body }}"\n  echo "Running AI review in CI"\n'
    '  if [ -z "$BODY" ]; then exit 1; fi\n',
    'script: |\n  const body = context.payload.pull_request.body;\n'
    '  if (!body.includes("## Summary")) core.setFailed("Summary needed (template v2 for AI agents too)");\n',
])
def test_a_log_or_failure_message_naming_ai_is_not_a_check(step: str) -> None:
    assert enforces_disclosure(step) is False


@pytest.mark.parametrize("check", ["if (!/Assisted-by:/i.test(body)) core.setFailed('add the trailer');",
                                   "if (!body.includes('AI assistance')) core.setFailed('add it');"])
def test_a_script_check_on_the_body_counts(check: str) -> None:
    assert enforces_disclosure(f"script: |\n  const body = context.payload.pull_request.body;\n  {check}\n") is True


def test_a_script_is_read_from_the_directory_the_line_changes_to(tmp_path: Path) -> None:
    flow = TRAILERS.replace("python3 tools/check_trailers.py", "cd tools && python3 check.py")
    root = _repo(tmp_path, {".github/workflows/t.yml": flow, "check.py": TRAILER_SCRIPT, "tools/check.py": "print(1)\n"})
    assert scan_ai_governance(root)["enforced"] is False
    (tmp_path / "tools/check.py").write_text(TRAILER_SCRIPT, encoding="utf-8")
    assert scan_ai_governance(root)["enforced"] is True


def test_a_pagination_cursor_is_not_an_ai_marker() -> None:
    assert enforces_disclosure("gh api graphql -f cursor=$cursor --jq .body pull_request.body\nexit 1\n") is False


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
    assert record is not None and (record["source"], record["conditions"]) == (COPILOT_OPT_OUT, [])
    assert scan_ai_governance(root)["opt_out_files"] == [".github/copilot-instructions.md"]
    assert owner_exclusions_block(root)["declines"] == [record]
    result = _level(root, scan_ai_governance(root))
    assert "declined" not in result  # no condition is about Copilot review, so none is set aside
    lines = badge_lines(result, [record])
    assert lines == ["DECLINED: Copilot review declined in .github/copilot-instructions.md · no condition set aside"]


def test_a_copilot_record_names_itself_when_counts_only_drops_its_reason() -> None:
    record: DeclineRecord = {"source": COPILOT_OPT_OUT, "conditions": [], "declined_by": None,
                             "declined_at": None, "version": None, "reason": None}
    assert decline_badge(record) == "Copilot review declined"
    assert badge_lines(lt.level_result(lt.default_model(), {}), [record]) == [
        "DECLINED: Copilot review declined · no condition set aside"]


def test_the_copilot_record_stays_off_the_counted_declines_line(tmp_path: Path) -> None:
    _repo(tmp_path, {".github/copilot-instructions.md": DECLINE, ".assess/agents-declined.json":
                     '{"declined_by": "owner", "reason": "not yet"}'})
    records = owner_exclusions_block(tmp_path)["declines"]
    assert [r["source"] for r in records] == ["agents_marker", COPILOT_OPT_OUT]
    result = lt.level_result(lt.default_model(), {"layer_grades": {"instruction_file_present": True}}, declines=records)
    counted, shown = badge_lines(result, records)
    assert counted.startswith(f"DECLINED: {len(result['declined'])} condition") and "Copilot" not in counted
    assert "agents declined by owner: not yet" in counted
    assert shown == "DECLINED: Copilot review declined in .github/copilot-instructions.md · no condition set aside"


def test_the_opt_out_never_takes_a_condition_out_of_the_denominator() -> None:
    data = {"layer_grades": {"instruction_file_present": True}, "coverage_gate": {"enforced_meets_minimum": True},
            "agent_ops": {"summary": {"agent_jobs_least_privilege": False}}}
    before = lt.level_result(lt.default_model(), data)
    for conditions in (["layer-6-coverage.1", "layer-0-instructions.1", "agent-least-privilege.1"], None):
        record: DeclineRecord = {"source": COPILOT_OPT_OUT, "conditions": conditions, "declined_by": None,
                                 "declined_at": None, "version": None, "reason": "x"}
        after = lt.level_result(lt.default_model(), data, declines=[record])
        assert "declined" not in after
        assert (after["tally"], after["step"]) == (before["tally"], before["step"])
        assert _cond(after, "agent-least-privilege.1")["state"] == "unmet"  # another agent's result stays


def test_another_agents_met_condition_stays_met_and_counted(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/copilot-instructions.md": DECLINE})
    data = {"agent_ops": {"summary": {"agent_jobs_least_privilege": True}}, "owner_exclusions": owner_exclusions_block(root)}
    cond = _cond(lt.level_result(lt.default_model(), data), "agent-least-privilege.1")
    assert (cond["state"], cond["counted"]) == ("met", True)


@pytest.mark.parametrize("text", [
    "Do not review generated files.\n",  # a part of the repository, not the whole
    "Do not review code in vendor/.\n",
    "Review pull requests for clarity.\n",
    "```\nDo not review pull requests.\n```\n",  # inside a code fence
    "Do not review.\n",  # names nothing
    "Do not review changes\nto generated files under vendor/.\n",  # a scoped refusal wrapped onto two lines
    "Never comment on.\n",
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
