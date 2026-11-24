"""Issues #1164 and #1165: a command or ``continue-on-error`` given as an expression or a
shell variable reads unknown with a reason, never a false 0 and never enforced
(``lib.ci_expressions``, ``lib.ci_matrix``, ``lib.ci_composite``)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.ci_checks import scan_ci_checks
from lib.ci_expressions import settle_expressions


def _scan(root: Path, files: dict[str, str]) -> tuple[object, ...]:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    for args in (("init", "-q"), ("add", "-A")):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                       encoding="utf-8", errors="replace")
    block = scan_ci_checks(root)
    return (block["test_or_lint_configured"], block["enforced_check_runs"], block["enforced_basis"],
            block["lint_runs"], block["lint_basis"])


def _wf(steps: str, job: str = "", top: str = "", on: str = "pull_request") -> dict[str, str]:
    return {".github/workflows/ci.yml": (f"on: {on}\n{top}jobs:\n  t:\n    runs-on: ubuntu-latest\n{job}"
                                         f"    steps:\n      - uses: actions/checkout@v4\n{steps}")}


_CREDIT = (True, True, "enforced_check", True, "lint_command")
_TESTS = (True, True, "enforced_check")
_NULL = (None, None, "check_target", None, "check_target")
_NONE = (False, False, "no_check_command", False, "no_lint_command")
_MAY = (True, None, "check_may_pass")


# --- #1164: commands ----------------------------------------------------------------------------

@pytest.mark.parametrize("run", [
    "${{ inputs.cmd }}", "${{ env.CMD }}", "$CMD", "${CMD}", '"$CMD"', "${{ steps.c.outputs.cmd }}",
    "cd src && $CMD", "|\n          set -e\n          $CMD --strict",
])
def test_an_unresolved_command_reads_null(tmp_path: Path, run: str) -> None:
    assert _scan(tmp_path, _wf(f"      - run: {run}\n", on="workflow_call")) == _NULL


def test_a_command_set_by_an_input_env_reads_null(tmp_path: Path) -> None:
    steps = "      - run: $CMD\n        env:\n          CMD: ${{ inputs.cmd }}\n"
    assert _scan(tmp_path, _wf(steps)) == _NULL


@pytest.mark.parametrize(("steps", "job", "top"), [
    ("      - run: $CMD\n        env:\n          CMD: ruff check .\n", "", ""),
    ("      - run: ${{ env.CMD }}\n", "    env:\n      CMD: 'ruff check .'\n", ""),
    ("      - run: ${CMD}\n", "", "env:\n  CMD: ruff check .\n"),
])
def test_a_literal_env_command_is_read(tmp_path: Path, steps: str, job: str, top: str) -> None:
    assert _scan(tmp_path, _wf(steps, job=job, top=top)) == _CREDIT


def test_the_step_env_wins_over_the_job_env(tmp_path: Path) -> None:
    steps = "      - run: $CMD\n        env:\n          CMD: echo hi\n"
    assert _scan(tmp_path, _wf(steps, job="    env:\n      CMD: pytest\n")) == _NONE


def test_a_quoted_variable_of_several_words_is_unknown() -> None:
    text = "jobs:\n  t:\n    env:\n      CMD: ruff check .\n    steps:\n      - run: \"$CMD\"\n"
    assert settle_expressions(text).unknown == {5}


@pytest.mark.parametrize("run", ["pytest $ARGS", "echo $CMD", "$GITHUB_WORKSPACE/build.sh",
                                 "echo ${{ inputs.cmd }}", "${{ github.workspace }}/ci.sh"])
def test_an_argument_or_part_of_a_word_keeps_its_reading(tmp_path: Path, run: str) -> None:
    text = f"jobs:\n  t:\n    steps:\n      - run: {run}\n"
    assert settle_expressions(text) == (text, frozenset(), frozenset())


def test_a_check_beside_an_unknown_command_keeps_the_credit(tmp_path: Path) -> None:
    assert _scan(tmp_path, _wf("      - run: $CMD\n      - run: ruff check . && pytest\n")) == _CREDIT


def test_a_masked_unknown_command_reads_false(tmp_path: Path) -> None:
    steps = "      - run: $CMD\n        continue-on-error: true\n"
    assert _scan(tmp_path, _wf(steps))[1:] == _NONE[1:]


def test_a_composite_env_input_takes_the_callers_value(tmp_path: Path) -> None:
    action = ("runs:\n  using: composite\n  steps:\n    - shell: bash\n      run: $CMD\n"
              "      env:\n        CMD: ${{ inputs.cmd }}\n")
    call = "      - uses: ./.github/actions/c\n        with:\n          cmd: {}\n"
    act = {".github/actions/c/action.yml": action}
    assert _scan(tmp_path / "a", _wf(call.format("ruff check .")) | act) == _CREDIT
    assert _scan(tmp_path / "b", _wf(call.format("echo hi")) | act) == _NONE
    assert _scan(tmp_path / "c", _wf(call.format("${{ github.ref }}")) | act) == _NULL


def test_a_forwarded_run_input_is_not_an_unknown_command(tmp_path: Path) -> None:
    action = "runs:\n  using: composite\n  steps:\n    - shell: bash\n      run: ${{ inputs.run }}\n"
    call = "      - uses: ./.github/actions/c\n        with:\n          run: echo hi\n"
    assert _scan(tmp_path, _wf(call) | {".github/actions/c/action.yml": action}) == _NONE


# --- #1165: continue-on-error -------------------------------------------------------------------

_PYTEST = "      - run: pytest\n"


@pytest.mark.parametrize(("job", "step"), [
    ("    continue-on-error: ${{ github.event_name == 'schedule' }}\n", ""),
    ("", "        continue-on-error: ${{ needs.a.outputs.flaky }}\n"),
    ("    continue-on-error: ${{ matrix.experimental }}\n", ""),   # no matrix: not settled
    ("    strategy:\n      matrix:\n        experimental: ${{ fromJSON(x) }}\n"
     "    continue-on-error: ${{ matrix.experimental }}\n", ""),
])
def test_an_unresolved_continue_on_error_reads_may_pass(tmp_path: Path, job: str, step: str) -> None:
    assert _scan(tmp_path, _wf(_PYTEST + step, job=job))[:3] == _MAY


_EXPERIMENTAL = "    strategy:\n      matrix:\n        experimental: [{}]\n    continue-on-error: ${{{{ matrix.experimental }}}}\n"


@pytest.mark.parametrize(("values", "reading"), [
    ("true", (True, False, "not_enforced")),
    ("false, true", (True, False, "not_enforced")),   # any true leg masks (maintainer triage)
    ("false", _TESTS),
])
def test_a_job_level_matrix_continue_on_error_is_resolved(
        tmp_path: Path, values: str, reading: tuple[object, ...]) -> None:
    assert _scan(tmp_path, _wf(_PYTEST, job=_EXPERIMENTAL.format(values)))[:3] == reading


def test_a_copied_step_takes_each_combinations_own_value(tmp_path: Path) -> None:
    job = ("    strategy:\n      matrix:\n        include:\n          - cmd: pytest\n            flaky: false\n"
           "          - cmd: ruff check .\n            flaky: true\n")
    steps = "      - run: ${{ matrix.cmd }}\n        continue-on-error: ${{ matrix.flaky }}\n"
    reading = _scan(tmp_path, _wf(steps, job=job))
    assert reading == (True, True, "enforced_check", False, "not_enforced")   # pytest enforced, ruff masked


@pytest.mark.parametrize(("value", "reading"), [
    ("false", _TESTS), ("${{ false }}", _TESTS), ("${{ true }}", (True, False, "not_enforced")),
    ("true", (True, False, "not_enforced")),
])
def test_a_literal_continue_on_error_keeps_its_reading(
        tmp_path: Path, value: str, reading: tuple[object, ...]) -> None:
    steps = f"      - run: pytest\n        continue-on-error: {value}\n"
    assert _scan(tmp_path, _wf(steps))[:3] == reading


def test_an_expression_on_one_step_leaves_the_others_enforced(tmp_path: Path) -> None:
    steps = "      - run: ruff check .\n        continue-on-error: ${{ inputs.soft }}\n      - run: pytest\n"
    assert _scan(tmp_path, _wf(steps)) == (True, True, "enforced_check", None, "check_may_pass")


# --- pre-review round 1: the shell's own reading of a variable -----------------------------------

@pytest.mark.parametrize(("run", "reading"), [
    ("|\n          CMD=echo\n          $CMD", _NULL),
    ("|\n          export CMD=echo\n          ${CMD}", _NULL),
    ("CMD=echo; $CMD", _NULL),
    ("|\n          OTHER=x\n          $CMD", _CREDIT),         # another name: the env literal stands
])
def test_a_variable_the_script_sets_itself_is_unknown(
        tmp_path: Path, run: str, reading: tuple[object, ...]) -> None:
    steps = f"      - run: {run}\n        env:\n          CMD: ruff check .\n"
    assert _scan(tmp_path, _wf(steps)) == reading


@pytest.mark.parametrize(("write", "reading"), [
    ('echo "CMD=echo hi" >> $GITHUB_ENV', _NULL),
    ("echo CMD=echo >> \"$GITHUB_ENV\"", _NULL),
    ('echo "OTHER=x" >> $GITHUB_ENV', _CREDIT),
])
def test_a_github_env_write_makes_later_steps_unknown(
        tmp_path: Path, write: str, reading: tuple[object, ...]) -> None:
    steps = f"      - run: {write}\n      - run: ${{{{ env.CMD }}}}\n"
    assert _scan(tmp_path, _wf(steps, job="    env:\n      CMD: ruff check .\n")) == reading


def test_a_github_env_write_does_not_reach_back() -> None:
    text = ("jobs:\n  t:\n    env:\n      CMD: pytest\n    steps:\n      - run: $CMD\n"
            "      - run: echo CMD=x >> $GITHUB_ENV\n")
    assert settle_expressions(text).unknown == frozenset()


@pytest.mark.parametrize(("run", "value", "reading"), [
    ("$CMD", "'cd app && pytest'", _NULL[:3]),          # word-split: && is an argument, never run
    ("${CMD}", "'pytest | tee log'", _NULL[:3]),
    ("${{ env.CMD }}", "'cd app && pytest'", _TESTS),    # text substitution: the shell parses it
    ("$CMD", "pytest -q", _TESTS),
])
def test_a_shell_operator_in_a_variable_value_is_unknown(
        tmp_path: Path, run: str, value: str, reading: tuple[object, ...]) -> None:
    steps = f"      - run: {run}\n        env:\n          CMD: {value}\n"
    assert _scan(tmp_path, _wf(steps))[:3] == reading


def test_a_declared_input_under_a_continue_on_error_expression_keeps_check_target(tmp_path: Path) -> None:
    steps = ("      - uses: someone/runner@v1\n        continue-on-error: ${{ inputs.soft }}\n"
             "        with:\n          run: pytest\n")
    assert _scan(tmp_path, _wf(steps))[:3] == (True, None, "check_target")


@pytest.mark.parametrize(("job", "step"), [
    ("", "        continue-on-error: ${{ inputs.soft }}\n"),
    ("    continue-on-error: ${{ inputs.soft }}\n", ""),
])
def test_an_unknown_command_under_a_continue_on_error_expression_reads_null(
        tmp_path: Path, job: str, step: str) -> None:
    reading = _scan(tmp_path, _wf("      - run: $CMD\n" + step, job=job))
    assert reading[1] is None and reading[3] is None
    assert reading[2] in ("check_may_pass", "check_target") and reading[4] in ("check_may_pass", "check_target")
