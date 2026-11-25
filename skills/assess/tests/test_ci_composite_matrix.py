"""Issue #923: ``lib.ci_checks`` follows a local composite action (nested ones too, #1194) and reads a
literal matrix command (``lib.ci_composite``, ``lib.ci_matrix``), in both directions."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.ci_checks import scan_ci_checks
from lib.ci_composite import inline_composites
from lib.ci_matrix import expand_matrix


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   encoding="utf-8", errors="replace")


def _scan(root: Path, files: dict[str, str], executable: tuple[str, ...] = ()) -> tuple[object, ...]:
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    if executable:
        _git(root, "update-index", "--chmod=+x", *executable)
    block = scan_ci_checks(root)
    return (block["test_or_lint_configured"], block["enforced_check_runs"], block["enforced_basis"],
            block["lint_runs"], block["lint_basis"])


def _wf(steps: str, job: str = "", on: str = "pull_request") -> dict[str, str]:
    return {".github/workflows/ci.yml": (f"on: {on}\njobs:\n  t:\n    runs-on: ubuntu-latest\n{job}"
                                         f"    steps:\n      - uses: actions/checkout@v4\n{steps}")}


def _action(*runs: str, using: str = "composite") -> str:
    steps = "".join(f"    - name: s{i}\n      shell: bash\n      run: {run}\n" for i, run in enumerate(runs))
    return f"name: check\nruns:\n  using: {using}\n  steps:\n{steps}"


_CALL = "      - uses: ./.github/actions/check\n"
_ACT = ".github/actions/check/action.yml"
_CREDIT = (True, True, "enforced_check", True, "lint_command")
_NONE = (False, False, "no_check_command", False, "no_lint_command")


# --- composite actions --------------------------------------------------------------------------

def test_a_composite_running_ruff_and_pytest_is_credited(tmp_path: Path) -> None:
    assert _scan(tmp_path, _wf(_CALL) | {_ACT: _action("ruff check .", "pytest")}) == _CREDIT


def test_a_composite_running_echo_earns_nothing(tmp_path: Path) -> None:
    assert _scan(tmp_path, _wf(_CALL) | {_ACT: _action("echo ruff check")}) == _NONE


@pytest.mark.parametrize("call", [
    _CALL + "        continue-on-error: true\n",
    _CALL + "        if: false\n",
])
def test_a_masked_caller_masks_the_composite(tmp_path: Path, call: str) -> None:
    reading = _scan(tmp_path, _wf(call) | {_ACT: _action("ruff check .", "pytest")})
    assert reading == (True, False, "not_enforced", False, "not_enforced")


def test_or_true_inside_the_composite_is_masked(tmp_path: Path) -> None:
    reading = _scan(tmp_path, _wf(_CALL) | {_ACT: _action("pytest || true", "ruff check . || true")})
    assert reading == (True, False, "not_enforced", False, "not_enforced")


def test_the_composite_step_keeps_its_own_shell(tmp_path: Path) -> None:
    # bash -e {0} without pipefail: a check piped into tee takes tee's status (#1061).
    action = "runs:\n  using: composite\n  steps:\n    - shell: sh\n      run: pytest | tee log\n"
    assert _scan(tmp_path, _wf(_CALL) | {_ACT: action})[1:3] == (False, "not_enforced")


def test_the_calling_workflow_triggers_apply(tmp_path: Path) -> None:
    files = _wf(_CALL, on="workflow_dispatch") | {_ACT: _action("pytest")}
    assert _scan(tmp_path, files)[:3] == (True, False, "not_enforced")


@pytest.mark.parametrize(("uses", "files"), [
    ("./.github/actions/node", {".github/actions/node/action.yml": _action("pytest", using="node20")}),
    ("../outside", {}),
    ("./.github/actions/missing", {}),
    ("./.github/workflows/r.yml", {".github/workflows/r.yml": "on: workflow_call\njobs: {}\n"}),
])
def test_only_a_local_composite_is_read(tmp_path: Path, uses: str, files: dict[str, str]) -> None:
    assert _scan(tmp_path, _wf(f"      - uses: {uses}\n") | files)[:3] == (False, False, "no_check_command")


_NULL = (None, None, "check_target", None, "check_target")


def test_a_nested_local_action_is_followed(tmp_path: Path) -> None:
    # #1194: nested local composites are followed (cycles and unreadable ones read null,
    # tests/test_ci_composite_lint.py).
    outer = "runs:\n  using: composite\n  steps:\n    - name: x\n      uses: ./.github/actions/inner\n"
    files = _wf(_CALL) | {_ACT: outer, ".github/actions/inner/action.yml": _action("pytest")}
    assert _scan(tmp_path, files)[:3] == (True, True, "enforced_check")


def test_a_missing_nested_local_action_reads_null(tmp_path: Path) -> None:
    outer = "runs:\n  using: composite\n  steps:\n    - name: x\n      uses: ./.github/actions/inner\n"
    assert _scan(tmp_path, _wf(_CALL) | {_ACT: outer}) == _NULL


def test_a_masked_nested_local_action_reads_false(tmp_path: Path) -> None:
    outer = "runs:\n  using: composite\n  steps:\n    - uses: ./.github/actions/inner\n"
    files = _wf(_CALL + "        continue-on-error: true\n") | {_ACT: outer}
    assert _scan(tmp_path, files)[1:] == (False, "no_check_command", False, "no_lint_command")


def test_a_remote_action_inside_a_composite_is_dropped(tmp_path: Path) -> None:
    outer = "runs:\n  using: composite\n  steps:\n    - uses: actions/setup-python@v5\n"
    assert _scan(tmp_path, _wf(_CALL) | {_ACT: outer}) == _NONE


_INPUT = ("inputs:\n  cmd:\n    required: false\nruns:\n  using: composite\n  steps:\n"
          "    - shell: bash\n      run: ${{ inputs.cmd }}\n")


@pytest.mark.parametrize(("with_", "action", "reading"), [
    ("        with:\n          cmd: pytest\n", _INPUT, (True, True, "enforced_check")),
    ("        with:\n          cmd: 'ruff check .'\n", _INPUT, (True, True, "enforced_check")),
    ("        with:\n          cmd: echo hi\n", _INPUT, (False, False, "no_check_command")),
    ("", _INPUT, (None, None, "check_target")),
    ("        with:\n          cmd: ${{ matrix.c }}\n", _INPUT, (None, None, "check_target")),
    ("        with:\n          cmd: ${{ env.CMD }}\n", _INPUT, (None, None, "check_target")),
    ("", _INPUT.replace("required: false", "default: pytest"), (True, True, "enforced_check")),
    ("        with:\n          cmd: ${{ matrix.c }}\n", _INPUT.replace("required: false", "default: pytest"),
     (None, None, "check_target")),   # the caller's value replaces the default
    ("        with:\n          cmd: echo hi\n", _INPUT.replace("run: ${{ inputs.cmd }}", "run: echo ${{ inputs.cmd }}"),
     (False, False, "no_check_command")),
])
def test_a_composite_input_takes_the_callers_literal_value(
        tmp_path: Path, with_: str, action: str, reading: tuple[object, ...]) -> None:
    assert _scan(tmp_path, _wf(_CALL + with_) | {_ACT: action})[:3] == reading


def test_a_symlinked_action_outside_the_checkout_is_not_read(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "action.yml").write_text(_action("pytest"), encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".github" / "actions" / "check").mkdir(parents=True)
    try:
        (repo / _ACT).symlink_to(outside / "action.yml")
    except OSError:
        pytest.skip("symlinks are not available here")
    assert _scan(repo, _wf(_CALL))[:3] == (False, False, "no_check_command")


def test_a_composite_script_is_read_by_the_script_follower(tmp_path: Path) -> None:
    files = _wf(_CALL) | {_ACT: _action("./ci.sh"), "ci.sh": "#!/bin/bash\nset -e\npytest\n"}
    assert _scan(tmp_path, files, executable=("ci.sh",))[:3] == (True, True, "enforced_check")


def test_a_composite_is_inlined_after_the_calling_step() -> None:
    text = "jobs:\n  t:\n    steps:\n      - uses: ./a\n        if: x\n      - run: echo\n"
    action = "runs:\n  using: composite\n  steps:\n  - run: |\n      pytest\n    shell: bash\n"
    assert inline_composites(text, {"a/action.yml": action}.get).text.splitlines()[5:9] == [
        "      - run: |", "          pytest", "        shell: bash", "        if: x"]


# --- matrix commands ----------------------------------------------------------------------------

_MATRIX = "    strategy:\n      matrix:\n        cmd: ['ruff check .', 'pytest']\n"


def test_a_matrix_of_ruff_and_pytest_is_credited(tmp_path: Path) -> None:
    assert _scan(tmp_path, _wf("      - run: ${{ matrix.cmd }}\n", job=_MATRIX)) == _CREDIT


def test_an_unexpandable_matrix_command_reads_null(tmp_path: Path) -> None:
    job = "    strategy:\n      matrix:\n        cmd: ${{ fromJSON(needs.a.outputs.cmds) }}\n"
    reading = _scan(tmp_path, _wf("      - run: ${{ matrix.cmd }}\n", job=job))
    assert reading == (None, None, "check_target", None, "check_target")


def test_a_masked_unexpandable_matrix_command_reads_false(tmp_path: Path) -> None:
    steps = "      - run: ${{ matrix.cmd }}\n        continue-on-error: true\n"
    reading = _scan(tmp_path, _wf(steps, job="    strategy:\n      matrix:\n        cmd: ${{ x }}\n"))
    assert reading == (None, False, "no_check_command", False, "no_lint_command")


def test_each_matrix_value_is_masked_on_its_own(tmp_path: Path) -> None:
    job = "    strategy:\n      matrix:\n        cmd: ['ruff check . && echo ok', 'pytest']\n"
    assert _scan(tmp_path, _wf("      - run: ${{ matrix.cmd }}\n", job=job))[1:3] == (True, "enforced_check")


def test_a_block_list_axis_under_a_masked_step_earns_nothing(tmp_path: Path) -> None:
    job = "    strategy:\n      matrix:\n        cmd:\n          - ruff check .\n          - pytest -q\n"
    steps = "      - run: ${{ matrix.cmd }}\n        continue-on-error: true\n"
    reading = _scan(tmp_path, _wf(steps, job=job))
    assert reading == (True, False, "not_enforced", False, "not_enforced")


def test_an_echoed_matrix_value_earns_nothing(tmp_path: Path) -> None:
    assert _scan(tmp_path, _wf("      - run: echo ${{ matrix.cmd }}\n", job=_MATRIX))[1:3] == (
        False, "no_check_command")


def test_an_unused_axis_earns_nothing(tmp_path: Path) -> None:
    assert _scan(tmp_path, _wf("      - run: echo hi\n", job=_MATRIX))[:3] == (False, False, "no_check_command")


def test_a_matrix_command_through_a_composite_input_is_credited(tmp_path: Path) -> None:
    common = ("runs:\n  using: composite\n  steps:\n    - shell: bash\n      run: ${{ inputs.run }}\n")
    steps = "      - uses: ./.github/actions/check\n        with:\n          run: ${{ matrix.cmd }}\n"
    assert _scan(tmp_path, _wf(steps, job=_MATRIX) | {_ACT: common}) == _CREDIT


def test_a_deeper_composite_is_shifted_left_to_the_caller() -> None:
    text = "jobs:\n  t:\n    steps:\n    - uses: ./a\n"
    action = "runs:\n  using: 'composite'\n  steps:\n        - run: pytest\n          shell: bash\n"
    assert inline_composites(text, {"a/action.yml": action}.get).text.splitlines()[4:] == [
        "    - run: pytest", "      shell: bash"]
    assert inline_composites(text, {"a/action.yml": "name: no runs\n"}.get) == (text, frozenset())


def test_an_include_only_matrix_is_credited(tmp_path: Path) -> None:
    job = "    strategy:\n      matrix:\n        include:\n          - cmd: ruff check .\n          - cmd: pytest\n"
    assert _scan(tmp_path, _wf("      - run: ${{ matrix.cmd }}\n", job=job)) == _CREDIT


def test_a_quoted_matrix_command_is_credited(tmp_path: Path) -> None:
    assert _scan(tmp_path, _wf("      - run: \"${{ matrix.cmd }}\"\n", job=_MATRIX)) == _CREDIT


def test_a_forwarding_composite_called_with_echo_earns_nothing(tmp_path: Path) -> None:
    common = "runs:\n  using: composite\n  steps:\n    - shell: bash\n      run: ${{ inputs.run }}\n"
    steps = "      - uses: ./.github/actions/check\n        with:\n          run: echo hi\n"
    assert _scan(tmp_path, _wf(steps) | {_ACT: common}) == _NONE


@pytest.mark.parametrize(("call", "action"), [
    ("      -   uses: ./.github/actions/check\n          continue-on-error: true\n", _action("pytest")),
    (_CALL + "        continue-on-error: true\n",
     "runs:\n  using: composite\n  steps:\n    -   shell: bash\n        run: pytest\n"),
    ("      -   uses: ./.github/actions/check\n          if: false\n",
     "runs:\n  using: composite\n  steps:\n  - shell: bash\n    run: pytest\n"),
])
def test_caller_masking_lands_in_the_copy_whatever_the_dash_gap(
        tmp_path: Path, call: str, action: str) -> None:
    assert _scan(tmp_path, _wf(call) | {_ACT: action})[:3] == (True, False, "not_enforced")


def test_an_oversized_matrix_reads_null_without_building_it(tmp_path: Path) -> None:
    axes = "".join(f"        a{i}: [x, y]\n" for i in range(32))
    job = f"    strategy:\n      matrix:\n{axes}        cmd: [pytest]\n"
    assert _scan(tmp_path, _wf("      - run: ${{ matrix.cmd }}\n", job=job))[:3] == (
        None, None, "check_target")


def test_an_unknown_line_stays_unknown_in_each_matrix_copy() -> None:
    text = ("jobs:\n  t:\n    strategy:\n      matrix:\n        cmd: [a, b]\n    steps:\n"
            "      - run: |\n          ${{ matrix.cmd }}\n          ${{ inputs.x }}\n")
    out = expand_matrix(text, frozenset({8}))
    assert [out.text.splitlines()[i] for i in sorted(out.unknown)] == ["          ${{ inputs.x }}"] * 2


def test_a_forwarded_run_the_caller_omits_takes_the_default(tmp_path: Path) -> None:
    action = ("inputs:\n  run:\n    default: pytest\nruns:\n  using: composite\n  steps:\n"
              "    - shell: bash\n      run: ${{ inputs.run }}\n")
    assert _scan(tmp_path, _wf(_CALL) | {_ACT: action})[:3] == (True, True, "enforced_check")
    passed = _CALL + "        with:\n          run: echo hi\n"
    assert _scan(tmp_path / "b", _wf(passed) | {_ACT: action})[:3] == (False, False, "no_check_command")


def test_an_unexpandable_github_script_input_is_not_unknown(tmp_path: Path) -> None:
    steps = "      - uses: actions/github-script@v7\n        with:\n          script: ${{ matrix.s }}\n"
    assert _scan(tmp_path, _wf(steps))[1:3] == (False, "no_check_command")
