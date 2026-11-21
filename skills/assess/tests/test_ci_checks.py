"""Tests for lib/ci_checks.py: does CI run a test or lint command (#698), and can it fail the job (#775)?"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.ci_checks import check_commands, count_check_lines, enforced_check_commands, scan_ci_checks
from lib.ci_masking import split_statements, unmasked_lines
from lib.shared_tooling import pulls_jobs_from_elsewhere

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("text", [
    "run: cd x && uv run pytest",
    "run: FOO=1 make test",
    'run: "pytest"',
    "script:\n  - npm test",
    "run: |\n  uv sync\n  uv run ruff check .",
    "- run: ./scripts/test.sh",
    "run: npm ci && npm run lint",
    "- make lint",
    "run: ruff check .",
    "run: npx eslint src",
    "run: cargo clippy -- -D warnings",
    "run: go vet ./...",
    "run: uv run --frozen mypy src",
    "- name: Don't skip tests\n  run: pytest",
    "run: |\n  echo '```' >> $GITHUB_STEP_SUMMARY\n  pytest",
    "run: pytest \\\n  -q",
    "script:\n  - ruff check .",
    "script:\n  - pytest",
    "test:\n  script:\n    - pytest -q",
    "test:\n  script:\n    - echo hi\n\n    # unit tests\n    - tox",
    "script:\n- pytest",
    "test:\n  before_script: &setup\n    - nox",
    "script:\n  - |\n    echo hi\n  - py.test",
])
def test_a_ci_line_that_runs_a_check_counts(text: str) -> None:
    assert check_commands(text) >= 1


@pytest.mark.parametrize("text", [
    "# run pytest",
    "name: Run pytest",
    "test:",
    "needs: test",
    "echo pytest",
    "- npm ci",
    "run: make deploy",
    "- uses: actions/checkout@v4",
    "lint:",
    "name: Lint with ruff",
    "echo lint",
    "run: make build",
    "uv sync --group lint",
    "npm install eslint",
    "python -m pip install ruff",
    "make build # then lint",
    "stages:\n  - lint\n  - build",
    "needs:\n  - lint",
    "uv sync --extra lint",
    "strategy:\n  matrix:\n    tool:\n      - mypy\n      - ruff",
    "needs:\n  - ruff",
    "needs:\n  - pytest",
    "strategy:\n  matrix:\n    tool:\n      - tox",
    "needs:\n- py.test\n- nox",
    "- pytest",
    "jobs:\n  pytest:\n    runs-on: ubuntu-latest",
])
def test_a_ci_line_that_runs_no_check_does_not_count(text: str) -> None:
    assert check_commands(text) == 0


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_no_ci_files_is_false_with_basis_no_ci(tmp_path: Path) -> None:
    block = scan_ci_checks(_write(tmp_path, {"README.md": "x\n"}))
    assert block == {"available": True, "ci_files": 0, "check_commands": 0, "included_files": 0,
                     "included_check_commands": 0, "resolved_includes": 0, "unresolved_includes": 0,
                     "includes": [], "test_or_lint_runs": False, "basis": "no_ci",
                     "enforced_check_runs": False, "masked_check_commands": 0}


def test_a_workflow_running_a_test_is_true(tmp_path: Path) -> None:
    block = scan_ci_checks(_write(tmp_path, {
        ".github/workflows/ci.yml": "jobs:\n  t:\n    steps:\n      - run: pytest -q\n",
        ".github/workflows/deploy.yaml": "jobs:\n  d:\n    steps:\n      - run: make deploy\n",
    }))
    assert (block["ci_files"], block["check_commands"]) == (2, 1)
    assert (block["test_or_lint_runs"], block["basis"]) == (True, "check_command")


def test_ci_with_no_check_is_false(tmp_path: Path) -> None:
    block = scan_ci_checks(_write(tmp_path, {".gitlab-ci.yml": "build:\n  script:\n    - make build\n"}))
    assert (block["test_or_lint_runs"], block["basis"]) == (False, "no_check_command")


@pytest.mark.parametrize("files", [
    {".gitlab-ci.yml": "include:\n  - project: acme/ci\n    file: python.yml\n"},
    {".gitlab-ci.yml": "include: 'ci/jobs.yml'\nstages: [test]\n"},
    {".github/workflows/ci.yml": "jobs:\n  test:\n    uses: acme/ci/.github/workflows/py.yml@v1\n"},
])
def test_jobs_only_from_includes_is_unknown(tmp_path: Path, files: dict[str, str]) -> None:
    block = scan_ci_checks(_write(tmp_path, files))
    assert (block["test_or_lint_runs"], block["basis"]) == (None, "included_jobs")
    assert block["included_files"] == 1


def test_a_local_check_beats_an_include(tmp_path: Path) -> None:
    block = scan_ci_checks(_write(tmp_path, {
        ".gitlab-ci.yml": "include:\n  - component: gitlab.com/acme/c@1\nlint:\n  script:\n    - make lint\n",
    }))
    assert (block["test_or_lint_runs"], block["included_files"]) == (True, 1)


_NEEDS = ("name: ci\non: [pull_request]\njobs:\n  pytest:\n    runs-on: ubuntu-latest\n"
          "    steps:\n      - run: echo hi\n  deploy:\n    needs:\n      - pytest\n"
          "    runs-on: ubuntu-latest\n    steps:\n      - run: echo deploy\n")
_MATRIX = ("name: ci\non: [pull_request]\njobs:\n  t:\n    runs-on: ubuntu-latest\n    strategy:\n"
           "      matrix:\n        tool:\n          - tox\n    steps:\n      - run: echo x\n")


@pytest.mark.parametrize("workflow", [_NEEDS, _MATRIX])
def test_a_lone_always_check_value_is_not_a_ci_run(tmp_path: Path, workflow: str) -> None:
    """Issue #725: ``needs: - pytest`` and ``matrix: ... - tox`` name a job or axis value."""
    block = scan_ci_checks(_write(tmp_path, {".github/workflows/ci.yml": workflow}))
    assert (block["check_commands"], block["test_or_lint_runs"], block["basis"]) == (
        0, False, "no_check_command")


def test_a_lone_always_check_value_with_an_include_is_unknown(tmp_path: Path) -> None:
    block = scan_ci_checks(_write(tmp_path, {".gitlab-ci.yml": (
        "include: 'ci/jobs.yml'\ndeploy:\n  needs:\n    - pytest\n  script:\n    - make deploy\n")}))
    assert (block["check_commands"], block["test_or_lint_runs"]) == (0, None)


def test_a_gitlab_script_item_running_pytest_counts(tmp_path: Path) -> None:
    block = scan_ci_checks(_write(tmp_path, {".gitlab-ci.yml": "test:\n  script:\n    - pytest -q\n"}))
    assert (block["check_commands"], block["test_or_lint_runs"]) == (1, True)


def test_parent_keys_come_from_the_context_lines() -> None:
    """A rewrite that blanks the ``script:`` line still reads its items' parent from the original."""
    original = ["test:", "  script:", "    - pytest"]
    assert count_check_lines(["test:", "", "    - pytest"], context=original) == 1
    assert count_check_lines(["needs:", "  - pytest"], context=["needs:", "  - pytest"]) == 0


def test_a_local_reusable_workflow_is_not_an_include() -> None:
    text = "jobs:\n  test:\n    uses: ./.github/workflows/tests.yml\n"
    assert pulls_jobs_from_elsewhere(text, gitlab=False) is False
    assert pulls_jobs_from_elsewhere("jobs:\n  t:\n    steps:\n      - uses: actions/checkout@v4\n",
                                     gitlab=False) is False
    assert pulls_jobs_from_elsewhere("test:\n  trigger:\n    include: child.yml\n", gitlab=True) is False



@pytest.mark.parametrize("text", [
    pytest.param("run: pytest || true", id="or_true"),
    pytest.param("run: pytest || exit 0", id="or_exit_0"),
    pytest.param("run: pytest || :", id="or_colon"),
    pytest.param("run: pytest || /bin/true", id="or_bin_true"),
    pytest.param("run: cd x && pytest || true", id="and_chain_or_true"),
    pytest.param("run: |\n  uv sync\n  pytest -q || true", id="block_or_true"),
    pytest.param("script:\n  - pytest || true", id="gitlab_or_true"),
    pytest.param("run: pytest \\\n  -q || true", id="continuation_or_true"),
    pytest.param("run: pytest --cov=x --cov-fail-under=1 || true", id="cov_fail_under_or_true"),
    pytest.param("run: ruff check . || true", id="lint_or_true"),
    pytest.param("- name: t\n  continue-on-error: true\n  run: pytest", id="step_continue_on_error_before"),
    pytest.param("- name: t\n  run: |\n    pytest\n  continue-on-error: true", id="step_continue_on_error_after"),
    pytest.param("- name: t\n  continue-on-error: true  # advisory\n  run: pytest",
                 id="step_continue_on_error_comment"),
    pytest.param("jobs:\n  t:\n    runs-on: x\n    continue-on-error: true\n    steps:\n      - run: pytest",
                 id="job_continue_on_error"),
    pytest.param("test:\n  allow_failure: true\n  script:\n    - pytest", id="gitlab_allow_failure"),
    pytest.param("run: |\n  set +e\n  pytest", id="set_plus_e_block"),
    pytest.param("test:\n  script:\n    - set +e\n    - pytest", id="gitlab_set_plus_e_list"),
    # A redirect with no later failing exit is still a mask, not a capture.
    pytest.param("run: ruff check . > out.txt || true", id="redirect_without_check_is_masked"),
    pytest.param("run: pytest > /dev/null || true\n", id="dev_null_is_masked"),
    pytest.param("run: pytest | tee -a $GITHUB_STEP_SUMMARY || true\n", id="step_summary_is_masked"),
    pytest.param("steps:\n  - run: pytest > out.txt || true\n  - run: |\n      make build\n      exit 1\n",
                 id="redirect_then_unrelated_exit_is_masked"),
    # Trimmed `.github/workflows/windows.yml`: job-level continue-on-error.
    pytest.param("jobs:\n  windows:\n    runs-on: windows-latest\n    continue-on-error: true\n"
                 "    steps:\n      - uses: actions/checkout@v4\n      - name: tests\n"
                 "        run: |\n          uv run --locked pytest -ra\n", id="windows_job_continue_on_error"),
    # Trimmed `.github/workflows/claude-review.yml`: an advisory comment after the value.
    pytest.param("      - name: review\n        continue-on-error: true  # advisory: never blocks\n"
                 "        run: npm test\n", id="claude_review_advisory_comment"),
])
def test_a_masked_ci_check_is_not_enforced(text: str) -> None:
    assert check_commands(text) == 1
    assert enforced_check_commands(text) == (0, 1)


_GRAFANA_CAPTURE = (
    "jobs:\n  lint:\n    runs-on: ubuntu-latest\n    steps:\n"
    "      - run: pnpm --silent run lint:circular &> /tmp/pr-circular.txt || true\n"
    "      - name: compare\n        run: |\n"
    "          if [ \"$(wc -l < /tmp/pr-circular.txt)\" -gt 0 ]; then\n"
    "            exit 1\n          fi\n"
)
_SUBSTITUTION_CAPTURE = (
    "steps:\n  - run: |\n"
    "      OUT=\"$(ruff check . 2>&1 || true)\"\n"
    "      if [ -n \"$OUT\" ]; then echo \"$OUT\"; exit 1; fi\n"
    "  - run: pytest\n"
)


@pytest.mark.parametrize("text", [
    pytest.param("run: pytest", id="plain"),
    pytest.param("run: pytest && true", id="and_true"),
    pytest.param("run: echo 'a || true' && pytest", id="quoted_operator"),
    pytest.param("run: pytest; make docs || true", id="mask_on_other_command"),
    pytest.param("- name: t\n  continue-on-error: ${{ matrix.experimental }}\n  run: pytest",
                 id="continue_on_error_expression"),
    pytest.param("- name: t\n  continue-on-error: false\n  run: pytest", id="continue_on_error_false"),
    pytest.param("test:\n  allow_failure:\n    exit_codes: [137]\n  script:\n    - pytest",
                 id="allow_failure_exit_codes"),
    pytest.param("run: |\n  set +e\n  pytest\n  status=$?\n  exit $status", id="set_plus_e_reraised"),
    pytest.param("run: |\n  set +e\n  make docs\n  set -e\n  pytest", id="set_plus_e_then_set_e"),
    pytest.param("steps:\n  - name: a\n    continue-on-error: true\n    run: make docs\n"
                 "  - name: b\n    run: pytest", id="continue_on_error_on_sibling_step"),
    pytest.param("test:\n  allow_failure: true\n  script:\n    - make docs\nlint:\n  script:\n    - pytest",
                 id="allow_failure_on_sibling_job"),
    pytest.param("- name: t\n  run: |\n    pytest\n- name: u\n  continue-on-error: true\n  run: make docs",
                 id="continue_on_error_on_next_step"),
    # Issue #775 calibration (grafana): `|| true` that captures output for a later explicit check.
    pytest.param(_GRAFANA_CAPTURE, id="capture_then_later_step_exits"),
    pytest.param("run: mkdir -p out || true && pytest", id="noop_before_check_leaves_it_enforced"),
    pytest.param("run: pytest || true && ruff check .", id="check_after_noop_is_enforced"),
])
def test_an_unmasked_ci_check_is_enforced(text: str) -> None:
    assert enforced_check_commands(text) == (1, 0)


def test_or_true_inside_a_substitution_is_a_capture_not_a_mask() -> None:
    """The substitution's `|| true` is not top level, so the step after it is enforced and nothing is masked."""
    assert enforced_check_commands(_SUBSTITUTION_CAPTURE) == (1, 0)


def test_the_floor_retry_loop_is_not_masked() -> None:
    """Trimmed `.github/workflows/floor.yml` canary step: `set +e`, then the status is re-raised."""
    text = (
        "      - name: Run the canary suite\n        run: |\n          set +e\n"
        "          for attempt in 1 2; do\n            uv run pytest tests\n            status=$?\n"
        "            if [ \"${status}\" -eq 0 ]; then\n              exit 0\n            fi\n"
        "          done\n          exit 1\n"
    )
    assert enforced_check_commands(text) == (1, 0)


def test_a_masked_continuation_of_a_non_check_counts_nothing() -> None:
    """Trimmed `.github/workflows/mutation.yml`: mutmut is not a check command, masked or not."""
    text = "        run: |\n          uv run mutmut results \\\n            > mutmut-results.txt || true\n"
    assert enforced_check_commands(text) == (0, 0)


def test_a_masked_and_an_unmasked_step_count_separately() -> None:
    text = ("jobs:\n  t:\n    runs-on: x\n    steps:\n      - name: tests\n        continue-on-error: true\n"
            "        run: pytest\n      - name: lint\n        run: ruff check .\n")
    assert enforced_check_commands(text) == (1, 1)


def test_unmasked_lines_keep_their_index_and_drop_only_masked_statements() -> None:
    lines = unmasked_lines("run: pytest || true; ruff check .\nrun: make docs\n")
    assert lines == ["run: ruff check .", "run: make docs"]


def test_split_statements_keeps_operators_outside_quotes_and_substitutions() -> None:
    assert split_statements("cd x && pytest || true; echo 'a;b' $(c || d) # x || true") == [
        [("", "cd x"), ("&&", "pytest"), ("||", "true")],
        [("", "echo 'a;b' $(c || d)")],
    ]


_WF = "name: ci\non: [pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n" \
      "      - uses: actions/checkout@v4\n      - run: {}\n"


def test_masked_only_ci_is_not_enforced_but_still_runs(tmp_path: Path) -> None:
    block = scan_ci_checks(_write(tmp_path, {".github/workflows/ci.yml": _WF.format("pytest || true")}))
    assert block["test_or_lint_runs"] is True
    assert block["enforced_check_runs"] is False
    assert block["masked_check_commands"] == 1
    assert block["basis"] == "check_command"


def test_masked_check_with_include_is_unknown(tmp_path: Path) -> None:
    block = scan_ci_checks(_write(tmp_path, {".gitlab-ci.yml": (
        "include:\n  - project: grp/tpl\n    file: /t.yml\ntest:\n  script:\n    - pytest || true\n")}))
    assert (block["test_or_lint_runs"], block["enforced_check_runs"]) == (True, None)


def test_allow_failure_job_with_a_coverage_gate_is_not_enforced(tmp_path: Path) -> None:
    block = scan_ci_checks(_write(tmp_path, {".gitlab-ci.yml": (
        "test:\n  allow_failure: true\n  script:\n    - pytest --cov-fail-under=80\n")}))
    assert (block["test_or_lint_runs"], block["enforced_check_runs"]) == (True, False)


def test_one_unmasked_check_is_enforced(tmp_path: Path) -> None:
    block = scan_ci_checks(_write(tmp_path, {
        ".github/workflows/a.yml": _WF.format("pytest || true"),
        ".github/workflows/b.yml": _WF.format("ruff check ."),
    }))
    assert (block["enforced_check_runs"], block["masked_check_commands"]) == (True, 1)


def test_no_ci_is_not_enforced(tmp_path: Path) -> None:
    block = scan_ci_checks(_write(tmp_path, {"README.md": "x\n"}))
    assert (block["enforced_check_runs"], block["masked_check_commands"]) == (False, 0)


def _is_checkout(root: Path) -> bool:
    probe = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True,
                           encoding="utf-8", errors="replace", check=False)
    return probe.returncode == 0 and (root / ".github" / "workflows" / "tests.yml").is_file()


@pytest.mark.skipif(not _is_checkout(REPO_ROOT), reason="needs this repository's checkout")
def test_this_repository_reads_enforced() -> None:
    assert scan_ci_checks(REPO_ROOT)["enforced_check_runs"] is True
