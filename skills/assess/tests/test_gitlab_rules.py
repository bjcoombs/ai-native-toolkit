"""Tests for lib/gitlab_rules.py and the GitLab rules half of lib/ci/ci_triggers.py (issue #884)."""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.ci.ci_checks import scan_ci_checks
from lib.ci.ci_triggers import gitlab_line_triggers
from lib.gitlab_rules import CONTEXTS, Context, Rule, Truth, evaluate, outcome

_NIGHTLY = '$CI_PIPELINE_SOURCE == "schedule" && $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
_TAG_ONLY = "$CI_COMMIT_TAG && $CI_COMMIT_BRANCH == null"


@pytest.mark.parametrize(("expression", "mr", "push"), [
    ('$CI_PIPELINE_SOURCE == "merge_request_event"', "true", "false"),
    ('$CI_PIPELINE_SOURCE == "push"', "false", "true"),
    ("$CI_PIPELINE_SOURCE == 'schedule'", "false", "false"),
    ('$CI_PIPELINE_SOURCE == "web"', "false", "false"),
    ('$CI_PIPELINE_SOURCE == "trigger"', "false", "false"),
    ('$CI_PIPELINE_SOURCE == "api"', "false", "false"),
    ('$CI_PIPELINE_SOURCE != "schedule"', "true", "true"),
    # A child or downstream pipeline can run for an ordinary change: never false.
    ('$CI_PIPELINE_SOURCE == "parent_pipeline"', "unknown", "unknown"),
    ('$CI_PIPELINE_SOURCE == "pipeline"', "unknown", "unknown"),
    ('$CI_PIPELINE_SOURCE == "made_up"', "unknown", "unknown"),
    ("$CI_PIPELINE_SOURCE =~ /^(push|merge_request_event)$/", "true", "true"),
    ("$CI_PIPELINE_SOURCE =~ /^schedule$/", "false", "false"),
    ("$CI_PIPELINE_SOURCE =~ /pipeline/", "unknown", "unknown"),
    (_NIGHTLY, "false", "false"),
    (_TAG_ONLY, "false", "false"),
    ("$CI_COMMIT_TAG", "false", "false"),
    ("$CI_COMMIT_TAG == null", "true", "true"),
    ("$CI_COMMIT_BRANCH", "false", "true"),
    ("$CI_COMMIT_BRANCH == null", "true", "false"),
    ("$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH", "false", "varies"),
    ('$CI_COMMIT_BRANCH == "main"', "false", "varies"),
    ("$CI_COMMIT_BRANCH =~ /^release/", "false", "varies"),
    ("$CI_MERGE_REQUEST_IID", "true", "false"),
    ("$CI_MERGE_REQUEST_LABELS =~ /run-e2e/", "varies", "false"),
    ("$CI_COMMIT_BRANCH && $CI_OPEN_MERGE_REQUESTS", "false", "varies"),
    ('$RUN_TESTS == "1"', "unknown", "unknown"),
    ("$CI_PROJECT_PATH == 'group/project'", "unknown", "unknown"),
    # && before ||, and parentheses.
    ('$CI_PIPELINE_SOURCE == "schedule" || $CI_PIPELINE_SOURCE == "merge_request_event"', "true", "false"),
    ('$CI_PIPELINE_SOURCE == "schedule" || $RUN_TESTS', "unknown", "unknown"),
    # Read left to right this would be false in a push pipeline; && binds first.
    ('$CI_PIPELINE_SOURCE == "push" || $RUN && $CI_COMMIT_TAG', "false", "true"),
    ('($CI_PIPELINE_SOURCE == "push" || $CI_PIPELINE_SOURCE == "schedule") && $CI_COMMIT_TAG', "false", "false"),
    ('!($CI_PIPELINE_SOURCE == "schedule")', "true", "true"),
    ("!$CI_COMMIT_TAG", "true", "true"),
    ("${CI_COMMIT_TAG}", "false", "false"),
    # Not parsed: unknown, never false.
    ("$CI_COMMIT_BRANCH ==", "unknown", "unknown"),
    ('($CI_PIPELINE_SOURCE == "push"', "unknown", "unknown"),
    ("$CI_COMMIT_BRANCH =~ /[/", "unknown", "unknown"),
    ("$CI_COMMIT_BRANCH =~ /x/x", "unknown", "unknown"),
    ("$CI_COMMIT_BRANCH =~ $PATTERN", "unknown", "unknown"),
    # Nested quantifiers backtrack without bound in Python re: not run.
    ("$CI_PIPELINE_SOURCE =~ /^((.*)*)*Z$/", "unknown", "unknown"),
    ("$CI_PIPELINE_SOURCE =~ /^(a+)+$/", "unknown", "unknown"),
    ("$CI_PIPELINE_SOURCE =~ /^(push){1}$/", "false", "true"),
])
def test_evaluate(expression: str, mr: Truth, push: Truth) -> None:
    assert (evaluate(expression, "merge_request"), evaluate(expression, "push")) == (mr, push)


def _rules(*items: dict[str, str]) -> list[Rule]:
    return [Rule(item) for item in items]


_SCHEDULE = '$CI_PIPELINE_SOURCE == "schedule"'
_MR = '$CI_PIPELINE_SOURCE == "merge_request_event"'


@pytest.mark.parametrize(("rules", "context", "expected"), [
    (_rules({"if": _MR}), "merge_request", "yes"),
    (_rules({"if": _MR}), "push", "no"),
    # The first matching rule wins: a never rule stops a later catch-all.
    (_rules({"if": '$CI_PIPELINE_SOURCE != "schedule"', "when": "never"}, {"when": "always"}), "push", "no"),
    (_rules({"if": _SCHEDULE, "when": "never"}, {"when": "always"}), "push", "yes"),
    (_rules({"if": _MR, "when": "manual"}, {"when": "always"}), "merge_request", "no"),
    (_rules({"if": _MR, "when": "on_failure"}), "merge_request", "no"),
    (_rules({"if": _MR, "when": "delayed", "start_in": "1 minute"}), "merge_request", "yes"),
    # An unknown rule before a decisive one.
    (_rules({"if": "$SKIP", "when": "never"}, {"when": "always"}), "push", "unknown"),
    (_rules({"if": "$RUN"}, {"when": "never"}), "push", "unknown"),
    (_rules({"if": "$RUN"}, {"when": "always"}), "push", "yes"),
    (_rules({"if": "$RUN"}), "push", "unknown"),
    # Branch-dependent rules: some pipelines run.
    (_rules({"if": '$CI_COMMIT_BRANCH == "main"', "when": "never"}, {"when": "always"}), "push", "some"),
    (_rules({"if": '$CI_COMMIT_BRANCH == "main"'}), "push", "some"),
    (_rules({"if": '$CI_COMMIT_BRANCH == "main"', "when": "never"}, {"if": "$CI_COMMIT_BRANCH =~ /main/"}),
     "push", "unknown"),
    # A child-pipeline-only job: never read as running in neither ordinary pipeline.
    (_rules({"if": '$CI_PIPELINE_SOURCE != "parent_pipeline"', "when": "never"}, {"when": "always"}),
     "push", "unknown"),
    (_rules({"if": _MR, "changes": "[src/**]"}), "merge_request", "some"),
    (_rules({"if": _MR, "exists": "[Dockerfile]"}), "merge_request", "unknown"),
    # A rule this reader cannot read (an anchor merge, a !reference) is unknown.
    (_rules({"": "!reference [.rules, mr]"}), "merge_request", "unknown"),
    # An unread rule may bring when: never: a later catch-all does not settle it.
    (_rules({"": "!reference [.stop, rules]"}, {"when": "always"}), "push", "unknown"),
    (_rules({"if": _MR, "when": "$WHEN"}), "merge_request", "unknown"),
    (_rules({"if": "|"}), "merge_request", "unknown"),
    (_rules(), "push", "no"),
])
def test_outcome(rules: list[Rule], context: Context, expected: str) -> None:
    assert outcome(rules, context) == expected


def test_the_job_level_when_is_each_rules_default() -> None:
    assert outcome(_rules({"if": _MR}), "merge_request", "manual") == "no"
    assert outcome(_rules({"if": _MR, "when": "on_success"}), "merge_request", "manual") == "yes"


def _job(rules: str, head: str = "") -> str:
    return f"{head}stages: [test]\ntest:\n  rules:\n{rules}  script:\n    - pytest\n"


def _trigger(text: str) -> str:
    return gitlab_line_triggers(text)[text.splitlines().index("    - pytest")]


@pytest.mark.parametrize(("rules", "trigger"), [
    (f"    - if: {_NIGHTLY}\n", "not_ordinary"),
    (f"    - if: {_TAG_ONLY}\n", "not_ordinary"),
    (f"    - if: '{_NIGHTLY}'\n", "not_ordinary"),
    (f'    - if: {_SCHEDULE} || $RUN_TESTS == "1"\n', "unknown"),
    (f"    - if: {_SCHEDULE} || {_MR}\n", "ordinary"),
    ('    - if: $CI_PIPELINE_SOURCE != "schedule"\n      when: never\n    - when: always\n', "not_ordinary"),
    ("    - if: $CI_COMMIT_TAG\n    - if: $CI_COMMIT_BRANCH\n      when: manual\n", "not_ordinary"),
    ("    - if: $CI_MERGE_REQUEST_IID\n      when: never\n    - if: $CI_COMMIT_BRANCH\n", "ordinary"),
    ("    - !reference [.default_rules, rules]\n", "unknown"),
    ("    - <<: *mr_rule\n", "unknown"),
    # A plain if: continued on the next line is not read whole.
    ('    - if: $CI_PIPELINE_SOURCE == "push"\n        && $CI_COMMIT_TAG\n', "unknown"),
    (f"    - if: {_SCHEDULE}\n        || $CI_PIPELINE_SOURCE == 'push'\n", "unknown"),
])
def test_gitlab_job_rules(rules: str, trigger: str) -> None:
    assert _trigger(_job(rules)) == trigger


def test_inline_rules_are_unknown() -> None:
    text = "stages: [test]\ntest:\n  rules: !reference [.rules, mr]\n  script:\n    - pytest\n"
    assert _trigger(text) == "unknown"


def test_job_level_when_manual_is_the_default_for_its_rules() -> None:
    text = f"test:\n  when: manual\n  rules:\n    - if: {_MR}\n  script:\n    - pytest\n"
    assert _trigger(text) == "not_ordinary"
    text = f"test:\n  when: manual\n  rules:\n    - if: {_MR}\n      when: on_success\n  script:\n    - pytest\n"
    assert _trigger(text) == "ordinary"


_WORKFLOW_SCHEDULE = f"workflow:\n  rules:\n    - if: {_SCHEDULE}\n"
_WORKFLOW_MR = (f"workflow:\n  rules:\n    - if: {_MR}\n"
                "    - if: $CI_COMMIT_BRANCH && $CI_OPEN_MERGE_REQUESTS\n      when: never\n"
                "    - if: $CI_COMMIT_BRANCH\n")


@pytest.mark.parametrize(("head", "job", "trigger"), [
    # workflow:rules that create only scheduled pipelines: no job runs on an ordinary change.
    (_WORKFLOW_SCHEDULE, "test:\n  script:\n    - pytest\n", "not_ordinary"),
    (_WORKFLOW_SCHEDULE, f"test:\n  rules:\n    - if: {_MR}\n  script:\n    - pytest\n", "not_ordinary"),
    # The duplicate-pipeline pattern: merge request pipelines, and branch pipelines without one.
    (_WORKFLOW_MR, "test:\n  script:\n    - pytest\n", "ordinary"),
    (_WORKFLOW_MR, f"test:\n  rules:\n    - if: {_MR}\n  script:\n    - pytest\n", "ordinary"),
    # Only merge request pipelines are created; a job without rules still runs in them.
    (f"workflow:\n  rules:\n    - if: {_MR}\n", "test:\n  script:\n    - pytest\n", "ordinary"),
    (f"workflow:\n  rules:\n    - if: {_MR}\n", "test:\n  only: [branches]\n  script:\n    - pytest\n",
     "not_ordinary"),
    ("workflow:\n  rules:\n    - if: $CREATE\n", "test:\n  script:\n    - pytest\n", "unknown"),
    ("workflow:\n  name: ci\n", "test:\n  script:\n    - pytest\n", "ordinary"),
    # A workflow limited to main and a job limited to release may never meet.
    ('workflow:\n  rules:\n    - if: $CI_COMMIT_BRANCH == "main"\n',
     'test:\n  rules:\n    - if: $CI_COMMIT_BRANCH == "release"\n  script:\n    - pytest\n', "unknown"),
    ('workflow:\n  rules:\n    - if: $CI_COMMIT_BRANCH == "main"\n', "test:\n  script:\n    - pytest\n", "ordinary"),
    ("workflow:\n  rules:\n    - if: $CI_COMMIT_BRANCH\n",
     'test:\n  rules:\n    - if: $CI_COMMIT_BRANCH == "release"\n  script:\n    - pytest\n', "ordinary"),
    # default: lines run only in the pipelines the workflow creates.
    (_WORKFLOW_SCHEDULE, "default:\n  before_script:\n    - pytest\n", "not_ordinary"),
    ("", "default:\n  before_script:\n    - pytest\n", "ordinary"),
])
def test_workflow_rules_gate_every_job(head: str, job: str, trigger: str) -> None:
    assert _trigger(head + job) == trigger


def _enforced(tmp_path: Path, rule: str) -> bool | None:
    text = f"tests:\n  rules:\n    - if: {rule}\n  script:\n    - pytest\n"
    (tmp_path / ".gitlab-ci.yml").write_text(text, encoding="utf-8")
    result: bool | None = scan_ci_checks(tmp_path)["enforced_check_runs"]
    return result


def test_a_nightly_only_tests_job_is_not_enforced(tmp_path: Path) -> None:
    assert _enforced(tmp_path, _NIGHTLY) is False


def test_a_tag_only_tests_job_is_not_enforced(tmp_path: Path) -> None:
    assert _enforced(tmp_path, _TAG_ONLY) is False


def test_an_or_mix_with_an_unread_variable_is_null(tmp_path: Path) -> None:
    assert _enforced(tmp_path, f'{_SCHEDULE} || $RUN_TESTS == "1"') is None


@pytest.mark.parametrize(("expression", "truth"), [
    ('$CI_PIPELINE_SOURCE == "schedule"', "true"),
    (_NIGHTLY, "varies"),  # which branch the schedule runs on
    ('$CI_PIPELINE_SOURCE == "push"', "false"),
    ("$CI_MERGE_REQUEST_IID", "false"),
    ("$CI_COMMIT_TAG", "false"),
    ('$CI_PIPELINE_SOURCE == "schedule" && $REFINE == "true"', "varies"),  # a schedule's own variable
])
def test_the_schedule_context(expression: str, truth: Truth) -> None:
    # #1406: asked for by name; the ordinary contexts leave it out.
    assert evaluate(expression, "schedule") == truth
    assert "schedule" not in CONTEXTS
    assert evaluate("$REFINE", "push") == "unknown"
