"""Tests for lib/ci/ci_triggers.py and the trigger half of ci_checks.enforced_check_runs (issue #775)."""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.ci.ci_checks import scan_ci_checks
from lib.ci.ci_triggers import gitlab_line_triggers, workflow_events, workflow_trigger
from lib.level_signals import load_default_levels
from lib.levels import Group, evaluate

_JOB = "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest\n"


def _workflow(on: str, job: str = _JOB) -> str:
    return f"name: ci\n{on}\n{job}"


@pytest.mark.parametrize(("on", "events"), [
    ("on: push", ["push"]),
    ("on: [push, pull_request]", ["push", "pull_request"]),
    ('"on": pull_request', ["pull_request"]),
    ("true:\n  pull_request:\n    branches: [main]", ["pull_request"]),
    ("on:\n  push:\n    branches: [main]\n  workflow_dispatch:", ["push", "workflow_dispatch"]),
    ("on:\n  - push\n  - schedule", ["push", "schedule"]),
    ("on: {push: {branches: [main]}, workflow_dispatch: {}}", ["push", "workflow_dispatch"]),
    ("on:  # comment\n  pull_request:", ["pull_request"]),
    # A block sequence may sit at its parent key's column (#958).
    ("on:\n- push\n- schedule", ["push", "schedule"]),
    ("on:\n\n- pull_request  # c\n", ["pull_request"]),
])
def test_workflow_events_reads_every_on_form(on: str, events: list[str]) -> None:
    assert workflow_events(_workflow(on)) == events


@pytest.mark.parametrize(("on", "trigger"), [
    ("on: [push]", "ordinary"),
    ("on:\n  merge_group:", "ordinary"),
    ("on: pull_request_target", "ordinary"),
    ("on: workflow_dispatch", "not_ordinary"),
    ("on:\n  schedule:\n    - cron: '0 0 * * *'\n  workflow_dispatch:", "not_ordinary"),
    ("on: release", "not_ordinary"),
    ("on: workflow_call", "called"),
    ("", "unknown"),
    ("on:\n- push", "ordinary"),
    ("on:\n- workflow_dispatch", "not_ordinary"),
    # Column-0 items only open the block: after indented children, one ends it.
    ("on:\n  - workflow_dispatch\n- push", "not_ordinary"),
])
def test_workflow_trigger(on: str, trigger: str) -> None:
    assert workflow_trigger(_workflow(on)) == trigger


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _block(tmp_path: Path, files: dict[str, str]) -> tuple[bool | None, str, int]:
    block = scan_ci_checks(_write(tmp_path, files))
    assert block["test_or_lint_configured"] is True
    return block["enforced_check_runs"], block["enforced_basis"], block["off_trigger_check_commands"]


def test_a_dispatch_only_workflow_is_not_enforced(tmp_path: Path) -> None:
    files = {".github/workflows/ci.yml": _workflow("on: workflow_dispatch")}
    assert _block(tmp_path, files) == (False, "not_enforced", 1)


def test_a_column_zero_on_list_is_enforced(tmp_path: Path) -> None:
    files = {".github/workflows/ci.yml": _workflow("on:\n- pull_request")}
    assert _block(tmp_path, files) == (True, "enforced_check", 0)


def test_a_workflow_with_no_readable_on_is_unknown(tmp_path: Path) -> None:
    assert _block(tmp_path, {".github/workflows/ci.yml": _workflow("")}) == (None, "unknown_trigger", 0)


def test_a_reusable_workflow_called_by_an_ordinary_one_is_enforced(tmp_path: Path) -> None:
    caller = _workflow("on: pull_request", "jobs:\n  t:\n    uses: ./.github/workflows/tests.yml\n")
    files = {".github/workflows/ci.yml": caller, ".github/workflows/tests.yml": _workflow("on: workflow_call")}
    assert _block(tmp_path, files) == (True, "enforced_check", 0)


def test_a_reusable_workflow_nobody_calls_is_unknown(tmp_path: Path) -> None:
    files = {".github/workflows/tests.yml": _workflow("on: workflow_call")}
    assert _block(tmp_path, files) == (None, "unknown_trigger", 0)


@pytest.mark.parametrize(("job", "trigger"), [
    ("test:\n  script:\n    - pytest\n", "ordinary"),
    ("test:\n  when: manual\n  script:\n    - pytest\n", "not_ordinary"),
    ("test:\n  only:\n    - tags\n  script:\n    - pytest\n", "not_ordinary"),
    ("test:\n  only: [schedules, web]\n  script:\n    - pytest\n", "not_ordinary"),
    ("test:\n  only:\n    - merge_requests\n  script:\n    - pytest\n", "ordinary"),
    ("test:\n  only:\n    refs:\n      - main\n  script:\n    - pytest\n", "ordinary"),
    ("test:\n  only:\n    variables:\n      - $X\n  script:\n    - pytest\n", "unknown"),
    ('test:\n  rules:\n    - if: $CI_PIPELINE_SOURCE == "merge_request_event"\n  script:\n    - pytest\n',
     "ordinary"),
    ("test:\n  rules:\n    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH\n      when: always\n"
     "  script:\n    - pytest\n", "ordinary"),
    ('test:\n  rules:\n    - if: $CI_PIPELINE_SOURCE == "schedule"\n  script:\n    - pytest\n',
     "not_ordinary"),
    ('test:\n  rules:\n    - if: $CI_PIPELINE_SOURCE == "merge_request_event"\n      when: manual\n'
     "  script:\n    - pytest\n", "not_ordinary"),
    ('test:\n  rules:\n    - if: $RUN_TESTS == "1"\n  script:\n    - pytest\n', "unknown"),
    ("test:\n  rules:\n    - changes:\n        - src/**/*\n  script:\n    - pytest\n", "ordinary"),
])
def test_gitlab_job_trigger(job: str, trigger: str) -> None:
    text = "stages: [test]\n" + job
    lines = text.splitlines()
    assert gitlab_line_triggers(text)[lines.index("    - pytest")] == trigger


def test_a_manual_gitlab_job_is_not_enforced(tmp_path: Path) -> None:
    files = {".gitlab-ci.yml": "test:\n  when: manual\n  script:\n    - pytest\n"}
    assert _block(tmp_path, files) == (False, "not_enforced", 1)


def test_one_ordinary_job_beside_a_manual_one_is_enforced(tmp_path: Path) -> None:
    files = {".gitlab-ci.yml": ("slow:\n  when: manual\n  script:\n    - pytest -m slow\n"
                                "lint:\n  script:\n    - ruff check .\n")}
    assert _block(tmp_path, files) == (True, "enforced_check", 1)


# Issue #725 shapes (#836): a lone always-check item under needs: or matrix: is a value, not a run,
# on the enforced path too.
_NEEDS_PYTEST = ("on: pull_request\njobs:\n  report:\n    runs-on: ubuntu-latest\n    needs:\n      - pytest\n"
                 "    steps:\n      - run: echo done\n")
_MATRIX_TOX = ("on: pull_request\njobs:\n  t:\n    runs-on: ubuntu-latest\n    strategy:\n      matrix:\n"
               "        tool:\n          - tox\n    steps:\n      - run: echo ${{ matrix.tool }}\n")


@pytest.mark.parametrize("workflow", [_NEEDS_PYTEST, _MATRIX_TOX], ids=["needs_pytest", "matrix_tox"])
def test_a_lone_list_item_is_not_an_enforced_run(tmp_path: Path, workflow: str) -> None:
    block = scan_ci_checks(_write(tmp_path, {".github/workflows/ci.yml": workflow}))
    assert (block["test_or_lint_configured"], block["enforced_check_runs"]) == (False, False)
    group: Group = {"name": "g", "settings": None, "repos": {"r": {"ci_checks": block}}}
    row = next(r for r in evaluate(load_default_levels(), group)["rows"] if r["id"].startswith("layer-5-"))
    assert row["level"] == 0


def test_a_dispatch_only_workflow_reads_layer_five_level_zero(tmp_path: Path) -> None:
    block = scan_ci_checks(_write(tmp_path, {".github/workflows/ci.yml": _workflow("on: workflow_dispatch")}))
    group: Group = {"name": "g", "settings": None, "repos": {"r": {"ci_checks": block}}}
    row = next(r for r in evaluate(load_default_levels(), group)["rows"] if r["id"].startswith("layer-5-"))
    assert row["level"] == 0
