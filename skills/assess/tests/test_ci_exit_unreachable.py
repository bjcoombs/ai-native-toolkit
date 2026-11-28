"""A check after a top-level ``exit`` never runs, so it earns no credit (#1622).

The shell leaves the step at a top-level ``exit 0``, a bare ``exit`` or ``exit`` with any
other status, so every later statement of that script body is masked. An ``exit`` inside a
conditional, a loop, a ``case``, a ``{ }`` group, a subshell or a function is left as is.
"""
from __future__ import annotations

import pytest

from lib.ci.ci_checks import check_commands, enforced_check_commands
from lib.ci.ci_masking import unmasked_lines


def _block(text: str, shell: str = "") -> str:
    body = "".join(f"          {line}\n" for line in text.splitlines())
    step_shell = f"        shell: {shell}\n" if shell else ""
    return f"on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n      - run: |\n{body}{step_shell}"


def _wf(run: str) -> str:
    return f"on: pull_request\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: {run}\n"


def _gitlab(*items: str) -> str:
    return "test:\n  script:\n" + "".join(f"    - {item}\n" for item in items)


@pytest.mark.parametrize("text", [
    pytest.param(_block("echo start\nexit 0\npytest"), id="block_exit_0"),
    pytest.param(_block("echo start\nexit\npytest"), id="block_bare_exit"),
    pytest.param(_block('echo start\nexit "0"\npytest'), id="block_quoted_zero"),
    pytest.param(_block("echo start\nexit 1\npytest"), id="block_exit_1"),
    pytest.param(_block("echo start\nexit 0\npytest\nexit 1"), id="later_reraise_never_runs"),
    pytest.param(_block("if [ -n \"$X\" ]; then echo x; fi\nexit 0\nruff check ."), id="after_a_closed_if"),
    pytest.param(_block("exit 0\npytest", shell="pwsh"), id="pwsh_block"),
    pytest.param(_wf("echo start; exit 0; pytest"), id="same_line"),
    pytest.param(_gitlab("echo start", "exit 0", "pytest"), id="gitlab_script_items"),
])
def test_a_check_after_a_top_level_exit_is_masked(text: str) -> None:
    assert check_commands(text) == 1
    assert enforced_check_commands(text) == (0, 1)
    assert not any("pytest" in line or "ruff" in line for line in unmasked_lines(text))


@pytest.mark.parametrize("text", [
    pytest.param(_block("if [ -z \"$X\" ]; then exit 0; fi\npytest"), id="one_line_if_guard"),
    pytest.param(_block("if [ -z \"$X\" ]; then\n  exit 0\nfi\npytest"), id="multi_line_if_guard"),
    pytest.param(_block("for f in a b; do\n  [ -f \"$f\" ] || exit 0\ndone\npytest"), id="loop"),
    pytest.param(_block('case "$X" in\n  skip) exit 0 ;;\nesac\npytest'), id="case_arm"),
    pytest.param(_block("bail() {\n  exit 0\n}\npytest"), id="function_body"),
    pytest.param(_block("( exit 0 )\npytest"), id="subshell"),
    pytest.param(_block("[ -n \"$X\" ] && exit 0\npytest"), id="guarded_exit"),
    pytest.param(_block("echo exit 0\npytest"), id="echoed_text"),
    pytest.param(_block("if ($env:SKIP) {\n  exit 0\n}\npytest", shell="pwsh"), id="pwsh_if_block"),
])
def test_a_check_after_a_nested_or_guarded_exit_still_counts(text: str) -> None:
    assert enforced_check_commands(text) == (1, 0)


def test_lines_before_the_exit_keep_their_index_and_text() -> None:
    text = _block("pytest\nexit 0\nruff check .")
    out = unmasked_lines(text)
    lines = text.splitlines()
    assert len(out) == len(lines)
    assert out[lines.index("          pytest")].strip() == "pytest"
    assert out[lines.index("          ruff check .")] == ""


def test_an_exit_in_one_step_leaves_the_next_step_alone() -> None:
    text = ("on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n"
            "      - run: |\n          exit 0\n          ruff check .\n"
            "      - run: pytest\n")
    assert enforced_check_commands(text) == (1, 1)
