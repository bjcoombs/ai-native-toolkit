"""CI-line reader gaps (#1061): each form reads in line with its real exit status.

The forms: a single-command subshell (``(pytest)``), a check piped into another command
without pipefail (``pytest | tee log``), ``dash ci.sh``, a line that opens a compound
command (``if``, ``while``, ``until``, ``{``, ``f()``), and a ``||`` fallback judged by its
words rather than its commands (``pytest || echo false``). Each row is read through
``scan_ci_checks`` under GitHub's default shell (``bash -e {0}``), ``shell: bash`` (``bash
--noprofile --norc -eo pipefail {0}``) and ``shell: sh`` (``sh -e {0}``), and
``test_each_reading_matches_the_real_exit`` re-runs it under those shells with a ``pytest``
and ``ruff`` that always exit 1: credit exits non-zero, a masked or may-pass reading exits 0.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lib.ci_checks import scan_ci_checks
from lib.ci_compound import compound_core, segment_core
from lib.ci_exempt import piped_groups, piped_members
from lib.ci_masking import split_statements

SHELLS = {"default": "", "bash": "        shell: bash\n", "sh": "        shell: sh\n"}
CREDIT = (True, "enforced_check")
MASKED = (False, "not_enforced")
MAY = (None, "check_may_pass")

# (run, reading under default, bash, sh)
ROWS = [
    # Gap 1: a single-command subshell.
    ("(pytest)", CREDIT, CREDIT, CREDIT),
    ("( pytest )", CREDIT, CREDIT, CREDIT),
    ("(pytest )", CREDIT, CREDIT, CREDIT),
    ("(ruff check .)", CREDIT, CREDIT, CREDIT),
    ("(pytest) ; echo after", CREDIT, CREDIT, CREDIT),
    ("(pytest && echo ok)", CREDIT, CREDIT, CREDIT),
    # Gap 2: a pipe without pipefail takes the last command's status.
    ("pytest | tee log", MASKED, CREDIT, MASKED),
    ("(pytest | tee log)", MASKED, CREDIT, MASKED),
    ("pytest 2>&1 | tee log", MASKED, CREDIT, MASKED),
    ("pytest | tee log || exit 1", MASKED, CREDIT, MASKED),
    ("pytest | tee log && echo ok", MASKED, CREDIT, MASKED),
    ("set -o pipefail; pytest | tee log", CREDIT, CREDIT, CREDIT),
    ("cat /dev/null | pytest", CREDIT, CREDIT, CREDIT),
    ("pytest | tee log && set -o pipefail", MASKED, CREDIT, MASKED),   # set after the pipe
    ("set -o pipefail && pytest | tee log", CREDIT, CREDIT, CREDIT),
    # A piped group runs in a pipeline subshell: its status is lost without pipefail.
    ("{ pytest || exit 1; } | tee log", MASKED, CREDIT, MASKED),
    ("{ pytest; } | tee log", MASKED, CREDIT, MASKED),
    ("{ pytest; } 2>&1 | tee log", MASKED, CREDIT, MASKED),
    ("(pytest; echo x) | tee log", MASKED, CREDIT, MASKED),
    ("pytest || { echo x; exit 1; } | tee log", MAY, CREDIT, MAY),
    ("{ pytest; } > log", CREDIT, CREDIT, CREDIT),
    ("f() { pytest; }; f | tee log", MASKED, CREDIT, MASKED),   # a piped call runs in a subshell
    # Gap 4: a line that opens a compound command.
    ("if ! pytest; then exit 1; fi", CREDIT, CREDIT, CREDIT),
    ("if pytest; then :; else exit 1; fi", CREDIT, CREDIT, CREDIT),
    ("{ pytest; }; echo next", CREDIT, CREDIT, CREDIT),
    ("{ pytest; }", CREDIT, CREDIT, CREDIT),
    ("f() { pytest; }; f", CREDIT, CREDIT, CREDIT),
    ("for d in sub; do pytest $d; done", CREDIT, CREDIT, CREDIT),
    ("if pytest; then echo ok; fi", MAY, MAY, MAY),
    ("while pytest; do break; done", MAY, MAY, MAY),
    ("until pytest; do break; done", MAY, MAY, MAY),
    ("{ pytest; echo done; } && echo ok", MAY, MAY, MAY),
    ("f() { pytest; }", MASKED, MASKED, MASKED),   # defined, never called
    ("f() { pytest; }; g() { f; }", MASKED, MASKED, MASKED),   # named only in an uncalled body
    ("f() { pytest; }; g() { f; }; g", CREDIT, CREDIT, CREDIT),
    ("f() { pytest; } && f", CREDIT, CREDIT, CREDIT),   # called after the closing brace
    ("f() { pytest; }; out=$(f)", CREDIT, CREDIT, CREDIT),   # an assignment runs it
    ("f() { pytest; }; echo f", MASKED, MASKED, MASKED),   # named in echoed text only
    ("test() { pytest; }; echo test passed", MASKED, MASKED, MASKED),
    ('f() { pytest; }; echo "$(f)"', MASKED, MASKED, MASKED),   # echo hides the status
    # Gap 5: a fallback arm is judged by the commands that run.
    ("pytest || echo false", MAY, MAY, MAY),
    ("pytest || echo exit", MAY, MAY, MAY),
    ("pytest || echo failed || exit 1", MAY, MAY, MAY),
    ("pytest || false || echo x", MAY, MAY, MAY),
    ("pytest || { echo exit 1; }", MAY, MAY, MAY),
    ("pytest || false", CREDIT, CREDIT, CREDIT),
    ("pytest || exit 1", CREDIT, CREDIT, CREDIT),
    ("pytest || exit", CREDIT, CREDIT, CREDIT),
    ("pytest || exit $?", CREDIT, CREDIT, CREDIT),
    ("pytest || echo failed && exit 1", CREDIT, CREDIT, CREDIT),
    ("pytest || { echo failed; exit 1; }", CREDIT, CREDIT, CREDIT),
    ("pytest || (echo failed; exit 1)", CREDIT, CREDIT, CREDIT),
    ("pytest || { echo recovered; } && exit 1", CREDIT, CREDIT, CREDIT),   # read past the closer
    ("pytest || { echo recovered; }", MAY, MAY, MAY),
    ("pytest || exit 1 | tee log", MAY, CREDIT, MAY),   # the exit ends only its pipeline member
    # Not a check before, and still not one: ``!`` inverts the status.
    ("! pytest", (False, "no_check_command"), (False, "no_check_command"), (False, "no_check_command")),
]

# A later statement may carry the piped check's status out: null, never a false 0.
SAFE_NULL = [
    "pytest | tee log; exit ${PIPESTATUS[0]}",
    "{ pytest; } | tee log; exit ${PIPESTATUS[0]}",
    "f() { pytest; }; f | tee log; exit ${PIPESTATUS[0]}",
    "pytest | tee out.txt; grep -q failed out.txt && exit 1",
]


def _step(run: str, shell: str) -> str:
    return ("on: pull_request\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
            f"      - run: {run}\n{SHELLS[shell]}")


def _block(body: str, shell: str = "default") -> str:
    lines = "".join(f"          {line}\n" for line in body.splitlines())
    return ("on: pull_request\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
            f"      - run: |\n{lines}{SHELLS[shell]}")


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    (tmp_path / "sub").mkdir(exist_ok=True)
    (tmp_path / "sub" / "keep").write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, encoding="utf-8", errors="replace")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, encoding="utf-8", errors="replace")
    return tmp_path


def _enforced(tmp_path: Path, workflow: str, **files: str) -> tuple[bool | None, str]:
    block = scan_ci_checks(_repo(tmp_path, {".github/workflows/ci.yml": workflow, **files}))
    return block["enforced_check_runs"], block["enforced_basis"]


@pytest.mark.parametrize("shell", list(SHELLS))
@pytest.mark.parametrize("row", ROWS, ids=[r[0] for r in ROWS])
def test_each_form_reads_as_its_shell_runs_it(tmp_path: Path, row: tuple[str, ...], shell: str) -> None:
    run, *readings = row
    assert _enforced(tmp_path, _step(run, shell)) == readings[list(SHELLS).index(shell)]


@pytest.mark.parametrize("shell", ["default", "sh"])
@pytest.mark.parametrize("run", SAFE_NULL)
def test_a_piped_status_read_later_is_not_measured(tmp_path: Path, run: str, shell: str) -> None:
    assert _enforced(tmp_path, _step(run, shell)) == MAY


@pytest.mark.parametrize("run", SAFE_NULL)
def test_a_piped_status_read_later_under_pipefail_stays_credited(tmp_path: Path, run: str) -> None:
    assert _enforced(tmp_path, _step(run, "bash")) == CREDIT


def _real_shells() -> dict[str, list[list[str]]]:
    bash = shutil.which("bash")
    if bash is None:
        return {}
    sh = [[s, "-e"] for s in (shutil.which("sh"), shutil.which("dash")) if s]
    return {"default": [[bash, "-e"]], "bash": [[bash, "--noprofile", "--norc", "-eo", "pipefail"]],
            "sh": sh}


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="needs a POSIX bash")
@pytest.mark.parametrize("row", [r for r in ROWS if not r[0].startswith("!")], ids=lambda r: r[0])
def test_each_reading_matches_the_real_exit(tmp_path: Path, row: tuple[object, ...]) -> None:
    run = str(row[0])
    (tmp_path / "bin").mkdir()
    (tmp_path / "sub").mkdir()
    for program in ("pytest", "ruff"):
        stub = tmp_path / "bin" / program
        stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        stub.chmod(0o755)
    script = tmp_path / "step.sh"
    script.write_text(run + "\n", encoding="utf-8")
    env = {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin"}
    for i, (name, shells) in enumerate(_real_shells().items()):
        for shell in shells:
            if "pipefail" in run and Path(shell[0]).name == "dash":
                continue   # dash rejects set -o pipefail (exit 2) whatever the check does
            done = subprocess.run([*shell, str(script)], cwd=tmp_path, env=env, capture_output=True,
                                  encoding="utf-8", errors="replace", timeout=10, check=False)
            assert (done.returncode != 0) == (row[1 + i] == CREDIT), (name, shell, run, done.returncode)


@pytest.mark.parametrize(("body", "expected"), [
    ("set -euo pipefail\npytest | tee log", CREDIT),
    ("pytest | tee log\necho done", MASKED),
    ("f() {\n  pytest\n}\nf", CREDIT),
    ("f() {\n  pytest\n}", MASKED),
    ("if ! pytest; then\n  exit 1\nfi", CREDIT),
    ("if pytest; then\n  echo ok\nfi", MAY),
])
def test_a_block_body_reads_the_same(tmp_path: Path, body: str, expected: tuple[object, str]) -> None:
    assert _enforced(tmp_path, _block(body)) == expected


@pytest.mark.parametrize(("run", "expected"), [
    ("if \\\n          pytest; then :; fi", MAY),
    ("if ! \\\n          pytest; then exit 1; fi", CREDIT),
])
def test_an_if_continued_onto_the_next_line_is_read(tmp_path: Path, run: str, expected: tuple[object, str]) -> None:
    """#1107 joins a ``\\`` continuation, so ``if \\`` / ``pytest; then`` is the one-line form."""
    assert _enforced(tmp_path, _step(run, "default")) == expected


def test_a_custom_pipefail_shell_keeps_the_pipe_credited(tmp_path: Path) -> None:
    text = _step("pytest | tee log", "default") + "        shell: bash -eo pipefail {0}\n"
    assert _enforced(tmp_path, text) == CREDIT


@pytest.mark.parametrize("script", ["#!/bin/sh\nset -e\npytest\n", "pytest || true\n", "pytest\n"])
@pytest.mark.parametrize("flags", ["", "-e "])
def test_dash_reads_a_script_as_sh_does(tmp_path: Path, script: str, flags: str) -> None:
    def reading(shell: str, root: Path) -> tuple[object, ...]:
        block = scan_ci_checks(_repo(root, {".github/workflows/ci.yml": _step(f"{shell} {flags}ci.sh", "default"),
                                            "ci.sh": script}))
        return (block["test_or_lint_configured"], block["enforced_check_runs"], block["enforced_basis"],
                block["lint_runs"], block["lint_basis"])
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert reading("dash", tmp_path / "a") == reading("sh", tmp_path / "b")


def test_dash_runs_a_script_that_fails_on_its_check(tmp_path: Path) -> None:
    assert _enforced(tmp_path, _step("dash ci.sh", "default"), **{"ci.sh": "pytest\n"}) == CREDIT


def test_gitlab_keeps_its_pipe_and_function_readings(tmp_path: Path) -> None:
    """GitLab runs its script with pipefail, and joins before_script with script."""
    text = "test:\n  before_script:\n    - f() { pytest; }\n  script:\n    - f\n    - ruff check . | tee log\n"
    block = scan_ci_checks(_repo(tmp_path, {".gitlab-ci.yml": text}))
    assert (block["enforced_check_runs"], block["lint_runs"]) == (True, True)


@pytest.mark.parametrize(("segment", "core"), [
    ("if ! pytest", "pytest"), ("while pytest", "pytest"), ("until pytest", "pytest"),
    ("then pytest", "pytest"), ("do pytest $d", "pytest $d"), ("else exit 1", "exit 1"),
    ("{ pytest", "pytest"), ("f() { pytest", "pytest"), ("function f { pytest", "pytest"),
    ("(pytest)", "pytest"), ("( ruff check . )", "ruff check ."), ("! pytest", "! pytest"),
    ("(cd sub", "(cd sub"), ("$(pytest)", "$(pytest)"), ("(a) || (b)", "(a) || (b)"),
    ("echo '(x)'", "echo '(x)'"), ("{a,b}", "{a,b}"),
])
def test_segment_core(segment: str, core: str) -> None:
    assert segment_core(segment) == core


def test_compound_core_keeps_operators_and_drops_empty_leads() -> None:
    assert compound_core("if ! pytest; then exit 1; fi") == "pytest; exit 1; fi"
    assert compound_core("(pytest) && echo ok") == "pytest && echo ok"


@pytest.mark.parametrize(("line", "piped"), [
    ("pytest | tee log", {0}), ("a | b | c", {0, 1}), ("a && b | c", {1}), ("a || b", set()),
    ("pytest 2>&1 | tee log", {0}),
])
def test_piped_members(line: str, piped: set[int]) -> None:
    assert piped_members(split_statements(line)[0]) == piped


@pytest.mark.parametrize("first", ["yarn lint | tee out.txt || true", "yarn lint | tee out.txt"])
def test_a_piped_capture_a_later_step_checks_stays_credited(tmp_path: Path, first: str) -> None:
    """The capture rule (#775): a later step greps the captured file and exits non-zero."""
    text = (_step(first, "default") + "      - run: grep -q error out.txt && exit 1\n")
    block = scan_ci_checks(_repo(tmp_path, {".github/workflows/ci.yml": text,
                                            "package.json": '{"scripts": {"lint": "eslint ."}}'}))
    assert (block["enforced_check_runs"], block["lint_runs"]) == (True, True)


def test_a_piped_check_nothing_reads_back_is_masked(tmp_path: Path) -> None:
    text = _step("pytest | tee out.txt", "default") + "      - run: echo hi\n"
    assert _enforced(tmp_path, text) == MASKED


@pytest.mark.parametrize("call", [
    "run_tests", "time run_tests", "X=1 run_tests", "out=$(run_tests)", "case x in x) run_tests;; esac",
    "} && run_tests",
])
def test_a_function_named_outside_its_body_is_called(tmp_path: Path, call: str) -> None:
    body = (f"run_tests() {{\n  pytest\n{call}" if call.startswith("}")
            else f"run_tests() {{\n  pytest\n}}\n{call}")
    assert _enforced(tmp_path, _block(body)) == CREDIT


@pytest.mark.parametrize("body", [
    "run_tests() {\n  pytest\n}\nretry 3 run_tests",   # retry may run it
    "run() {\n  pytest\n}\nnpm run build",              # npm's argument, not the function
])
def test_a_function_named_only_as_an_argument_is_not_measured(tmp_path: Path, body: str) -> None:
    """Neither credit nor a false 0: the other command may or may not run the function."""
    assert _enforced(tmp_path, _block(body)) == MAY


@pytest.mark.parametrize(("run", "shell", "expected"), [
    ("(ruff check .)", "default", (True, "lint_command")),
    ("ruff check . | tee log", "default", (False, "not_enforced")),
    ("ruff check . | tee log", "bash", (True, "lint_command")),
    ("ruff check . || echo false", "default", (None, "check_may_pass")),
    ("if ! ruff check .; then exit 1; fi", "default", (True, "lint_command")),
])
def test_the_lint_reading_follows_the_same_rules(tmp_path: Path, run: str, shell: str,
                                                 expected: tuple[object, str]) -> None:
    block = scan_ci_checks(_repo(tmp_path, {".github/workflows/ci.yml": _step(run, shell)}))
    assert (block["lint_runs"], block["lint_basis"]) == expected


@pytest.mark.parametrize(("run", "files"), [
    ("{shell} scripts/foo.sh", {"scripts/foo.sh": "echo hi\n"}),
    ("{shell} test.sh", {}),
    ("{shell} scripts/lint.sh", {"scripts/lint.sh": "ruff check .\n"}),
])
def test_dash_reads_a_named_or_missing_script_as_sh_does(tmp_path: Path, run: str, files: dict[str, str]) -> None:
    def reading(shell: str, root: Path) -> tuple[object, ...]:
        block = scan_ci_checks(_repo(root, {".github/workflows/ci.yml": _step(run.format(shell=shell), "default"),
                                            **files}))
        return (block["test_or_lint_configured"], block["basis"], block["enforced_check_runs"],
                block["enforced_basis"], block["lint_runs"], block["lint_basis"])
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert reading("dash", tmp_path / "a") == reading("sh", tmp_path / "b")


def test_a_subshell_matrix_value_is_not_a_run(tmp_path: Path) -> None:
    text = ("on: pull_request\njobs:\n  t:\n    runs-on: x\n    strategy:\n      matrix:\n"
            "        cmd:\n          - (pytest)\n    steps:\n      - run: echo hi\n")
    block = scan_ci_checks(_repo(tmp_path, {".github/workflows/ci.yml": text}))
    assert (block["test_or_lint_configured"], block["basis"]) == (False, "no_check_command")


@pytest.mark.parametrize("command", ["dash -eo nounset ci.sh", "bash -eo nounset ci.sh", "dash ci.sh"])
def test_a_shell_flag_cluster_takes_its_value(tmp_path: Path, command: str) -> None:
    from lib.command_resolver import resolve
    root = _repo(tmp_path, {"ci.sh": "pytest\n"})
    assert resolve(command, root).verdict == "resolved"


@pytest.mark.parametrize(("body", "default", "bash"), [
    ("{\n  pytest || exit 1\n} | tee log", MASKED, CREDIT),
    ("{\n  pytest\n} 2>&1 | tee log", MASKED, CREDIT),
    ("(\n  pytest\n  echo x\n) | tee log", MASKED, CREDIT),
    ("pytest || {\n  echo x\n  exit 1\n} | tee log", MAY, CREDIT),
    ("pytest || {\n  echo x\n  exit 1\n}", CREDIT, CREDIT),
])
def test_a_piped_group_over_several_lines_reads_the_same(tmp_path: Path, body: str, default: tuple[object, str],
                                                          bash: tuple[object, str]) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert _enforced(tmp_path / "a", _block(body)) == default
    assert _enforced(tmp_path / "b", _block(body, "bash")) == bash


@pytest.mark.parametrize(("line", "groups"), [
    ("{ pytest; } | tee log", {0: {0}, 1: {0}}),
    ("(pytest; echo x) | tee log", {0: {0}, 1: {0}}),
    ("pytest || { echo x; exit 1; } | tee log", {0: {1}, 1: {0}, 2: {0}}),
    ("{ pytest; } > log", {}),
    ("{ pytest; }; echo next | tee log", {}),
    ('x=$(pytest) | tee log', {}),
])
def test_piped_groups(line: str, groups: dict[int, set[int]]) -> None:
    assert piped_groups(split_statements(line)) == groups


_FN = "run_tests() {\n  pytest\n}\n"


@pytest.mark.parametrize(("body", "default", "bash"), [
    # A ${VAR} expansion's closing brace closes no group, so the function stays read (#1061).
    (_FN + "run_tests\necho done ${GITHUB_SHA}", CREDIT, CREDIT),
    ("echo ${A}\n" + _FN + "run_tests", CREDIT, CREDIT),
    # A piped call loses its status without pipefail; a PIPESTATUS read rescues it to null.
    (_FN + "run_tests | tee log\nexit ${PIPESTATUS[0]}", MAY, CREDIT),
    (_FN + "run_tests | tee log\ntest ${PIPESTATUS[0]} -eq 0", MAY, CREDIT),
    (_FN + "run_tests | tee log\nrc=${PIPESTATUS[0]}\nexit $rc", MAY, CREDIT),
    (_FN + "run_tests 2>&1 | tee log", MASKED, CREDIT),
    (_FN + "run_tests\nrun_tests | tee log", CREDIT, CREDIT),   # one plain call enforces it
])
def test_a_function_call_reads_its_pipe_and_expansions(tmp_path: Path, body: str, default: tuple[object, str],
                                                       bash: tuple[object, str]) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert _enforced(tmp_path / "a", _block(body)) == default
    assert _enforced(tmp_path / "b", _block(body, "bash")) == bash


@pytest.mark.parametrize("shell", list(SHELLS))
def test_a_piped_function_call_over_several_lines(tmp_path: Path, shell: str) -> None:
    expected = CREDIT if shell == "bash" else MASKED
    assert _enforced(tmp_path, _block(_FN + "run_tests 2>&1 | tee log", shell)) == expected


def test_a_stray_expansion_brace_closes_no_group() -> None:
    from lib.ci_unsure import _groups
    statements = [s for line in ["f() {", "pytest", "}", "f", "echo ${A}"] for s in split_statements(line)]
    assert _groups(statements) == [(0, 2, "f")]
