"""Issue #1194: a known lint action inside a local composite action is read as it is at the
top level, and nested local composites are followed with cycle protection (``lib.ci_composite``)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.ci.ci_checks import scan_ci_checks
from lib.ci_composite import inline_composites


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   encoding="utf-8", errors="replace")


def _lint(root: Path, files: dict[str, str]) -> tuple[object, object]:
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    block = scan_ci_checks(root)
    return block["lint_runs"], block["lint_basis"]


def _wf(call: str = "      - uses: ./.github/actions/check\n") -> dict[str, str]:
    return {".github/workflows/ci.yml": ("on: pull_request\njobs:\n  t:\n    runs-on: ubuntu-latest\n"
                                         f"    steps:\n      - uses: actions/checkout@v4\n{call}")}


def _composite(steps: str, inputs: str = "") -> str:
    return f"name: check\n{inputs}runs:\n  using: composite\n  steps:\n{steps}"


_ACT = ".github/actions/check/action.yml"
_INNER = ".github/actions/inner/action.yml"
_RUFF = "    - uses: astral-sh/ruff-action@v3\n"
_LINT = (True, "lint_command")
_NO_LINT = (False, "no_lint_command")
_NULL = (None, "check_target")


# --- the issue's success criteria -----------------------------------------------------------

def test_a_ruff_action_inside_a_composite_is_credited(tmp_path: Path) -> None:
    assert _lint(tmp_path, _wf() | {_ACT: _composite(_RUFF)}) == _LINT


def test_an_install_only_ruff_action_inside_a_composite_reads_false(tmp_path: Path) -> None:
    steps = _RUFF + "      with:\n        args: --version\n"
    assert _lint(tmp_path, _wf() | {_ACT: _composite(steps)}) == _NO_LINT


@pytest.mark.parametrize(("with_", "reading"), [
    ("", _LINT),
    ("        with:\n          args: --version\n", _NO_LINT),
    ("        with:\n          args: check .\n", _LINT),
])
def test_top_level_readings_are_unchanged(tmp_path: Path, with_: str, reading: tuple[object, object]) -> None:
    assert _lint(tmp_path, _wf("      - uses: astral-sh/ruff-action@v3\n" + with_)) == reading


# --- inputs, masking and the other actions ---------------------------------------------------

_ARGS_INPUT = "inputs:\n  ruff-args:\n    required: false\n"
_ARGS_STEP = _RUFF + "      with:\n        args: ${{ inputs.ruff-args }}\n"


@pytest.mark.parametrize(("call", "inputs", "reading"), [
    ("        with:\n          ruff-args: --version\n", _ARGS_INPUT, _NO_LINT),
    ("        with:\n          ruff-args: check src\n", _ARGS_INPUT, _LINT),
    ("", _ARGS_INPUT.replace("required: false", "default: --version"), _NO_LINT),
    ("        with:\n          ruff-args: check\n", _ARGS_INPUT.replace("required: false", "default: --version"),
     _LINT),
    ("", _ARGS_INPUT, _NULL),   # an unsettled args: never credited, never a false 0
    ("        with:\n          ruff-args: ${{ matrix.a }}\n", _ARGS_INPUT, _NULL),
])
def test_the_lint_step_takes_the_callers_literal_inputs(
        tmp_path: Path, call: str, inputs: str, reading: tuple[object, object]) -> None:
    files = _wf("      - uses: ./.github/actions/check\n" + call) | {_ACT: _composite(_ARGS_STEP, inputs)}
    assert _lint(tmp_path, files) == reading


@pytest.mark.parametrize("mask", ["        continue-on-error: true\n", "        if: false\n"])
def test_a_masked_caller_masks_the_lint_step(tmp_path: Path, mask: str) -> None:
    files = _wf("      - uses: ./.github/actions/check\n" + mask) | {_ACT: _composite(_RUFF)}
    assert _lint(tmp_path, files) == (False, "not_enforced")


def test_a_golangci_install_only_step_inside_a_composite_reads_false(tmp_path: Path) -> None:
    steps = "    - uses: golangci/golangci-lint-action@v9\n      with:\n        install-only: true\n"
    assert _lint(tmp_path, _wf() | {_ACT: _composite(steps)}) == _NO_LINT


def test_a_pre_commit_action_inside_a_composite_may_lint(tmp_path: Path) -> None:
    steps = "    - uses: pre-commit/action@v3.0.1\n"
    assert _lint(tmp_path, _wf() | {_ACT: _composite(steps)}) == _NULL


@pytest.mark.parametrize("uses", ["actions/setup-python@v5", "acme/lint-everything@v1"])
def test_a_remote_action_inside_a_composite_is_never_credited(tmp_path: Path, uses: str) -> None:
    assert _lint(tmp_path, _wf() | {_ACT: _composite(f"    - uses: {uses}\n")}) == _NO_LINT


def test_a_remote_composite_the_workflow_calls_is_never_credited(tmp_path: Path) -> None:
    assert _lint(tmp_path, _wf("      - uses: acme/actions/.github/actions/check@v1\n")) == _NO_LINT


# --- nested local composites -----------------------------------------------------------------

_CALL_INNER = "    - uses: ./.github/actions/inner\n"


def test_a_lint_action_in_a_nested_composite_is_credited(tmp_path: Path) -> None:
    files = _wf() | {_ACT: _composite(_CALL_INNER), _INNER: _composite(_RUFF)}
    assert _lint(tmp_path, files) == _LINT


def test_a_nested_composite_takes_inputs_through_each_level(tmp_path: Path) -> None:
    outer = _composite(_CALL_INNER + "      with:\n        ruff-args: ${{ inputs.mode }}\n",
                       "inputs:\n  mode:\n    default: check\n")
    files = _wf("      - uses: ./.github/actions/check\n        with:\n          mode: --version\n")
    assert _lint(tmp_path, files | {_ACT: outer, _INNER: _composite(_ARGS_STEP, _ARGS_INPUT)}) == _NO_LINT
    assert _lint(tmp_path / "b", _wf() | {_ACT: outer, _INNER: _composite(_ARGS_STEP, _ARGS_INPUT)}) == _LINT


@pytest.mark.parametrize("mask", ["      continue-on-error: true\n", "      if: false\n"])
def test_a_masked_nested_step_masks_its_copies(tmp_path: Path, mask: str) -> None:
    files = _wf() | {_ACT: _composite(_CALL_INNER + mask), _INNER: _composite(_RUFF)}
    assert _lint(tmp_path, files) == (False, "not_enforced")


@pytest.mark.parametrize("inner", [
    None,                                                  # unresolvable
    "runs:\n  using: node20\n  main: index.js\n",          # not a composite
    _composite("    - uses: ./.github/actions/check\n"),   # a cycle back to the caller
])
def test_an_unreadable_nested_action_reads_null_never_met(tmp_path: Path, inner: str | None) -> None:
    files = _wf() | {_ACT: _composite(_CALL_INNER)} | ({_INNER: inner} if inner else {})
    assert _lint(tmp_path, files) == _NULL


def test_a_self_cycle_terminates_and_reads_null() -> None:
    action = _composite("    - uses: ./.github/actions/check\n")
    got = inline_composites(_wf()[".github/workflows/ci.yml"], {_ACT: action}.get)
    assert got.text.splitlines()[-1] == "      - uses: ./.github/actions/check"
    assert len(got.unknown) == 1


def test_a_cycle_with_a_lint_step_still_credits_the_step(tmp_path: Path) -> None:
    files = _wf() | {_ACT: _composite(_RUFF + _CALL_INNER), _INNER: _composite(_CALL_INNER.replace("inner", "check"))}
    assert _lint(tmp_path, files)[0] is True


def test_a_deep_chain_stops_at_the_depth_limit() -> None:
    # The workflow calls ./a0; ./a1 .. ./a8 are the eight nested levels followed, ./a9 is not.
    chain = {f"a{n}/action.yml": _composite(f"    - uses: ./a{n + 1}\n") for n in range(20)}
    text = "jobs:\n  t:\n    steps:\n      - uses: ./a0\n"
    got = inline_composites(text, chain.get)
    lines = got.text.splitlines()
    assert [lines[i].strip() for i in got.unknown] == ["- uses: ./a9"]


def test_an_eight_level_chain_reaching_ruff_is_credited(tmp_path: Path) -> None:
    chain = {f".github/actions/a{n}/action.yml": _composite(f"    - uses: ./.github/actions/a{n + 1}\n")
             for n in range(8)} | {".github/actions/a8/action.yml": _composite(_RUFF)}
    assert _lint(tmp_path, _wf("      - uses: ./.github/actions/a0\n") | chain) == _LINT


def test_a_wide_diamond_stops_at_the_read_budget() -> None:
    # Each action calls the next ten times: without a budget this is 10**8 copies.
    calls = "".join("    - uses: ./a{n}\n" for _ in range(10))
    chain = {f"a{n}/action.yml": _composite(calls.format(n=n + 1)) for n in range(8)}
    chain["a8/action.yml"] = _composite(_RUFF)
    got = inline_composites("jobs:\n  t:\n    steps:\n      - uses: ./a0\n", chain.get)
    assert got.unknown   # the reads past the budget stay unknown, never credited
    assert len(got.text.splitlines()) < 2_000


_FORWARD = "inputs:\n  run:\n    required: true\n"
_FORWARD_STEP = "    - shell: bash\n      run: ${{ inputs.run }}\n"


@pytest.mark.parametrize(("value", "reading"), [
    ("ruff check .", _LINT),
    ("echo hi", _NO_LINT),
    ("${{ matrix.c }}", _NULL),
])
def test_a_forwarded_run_through_a_nested_composite_keeps_its_command(
        tmp_path: Path, value: str, reading: tuple[object, object]) -> None:
    outer = _composite(_CALL_INNER + f"      with:\n        run: {value}\n")
    files = _wf() | {_ACT: outer, _INNER: _composite(_FORWARD_STEP, _FORWARD)}
    assert _lint(tmp_path, files) == reading
