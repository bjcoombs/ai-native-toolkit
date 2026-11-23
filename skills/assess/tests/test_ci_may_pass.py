"""A CI check whose failure the step may hide reads not measured, never credited (#1008).

The #1008 triage decision: these forms read null with ``check_may_pass``. Each expected
reading below matches the exit status of real ``bash -e`` (GitHub's default),
``bash --noprofile --norc -eo pipefail`` (``shell: bash``) and dash ``-e`` (``shell:
sh``), with a ``pytest`` that always exits 1: a "may pass" form exits 0 under all three,
a credited form exits 1. ``test_the_table_matches_real_shells`` re-runs the table where
the shells are installed.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lib.ci_checks import scan_ci_checks
from lib.ci_masking import mask_lines
from lib.coverage_gate import detect_coverage_gate

SHELLS = {"default": "", "bash": "        shell: bash\n", "sh": "        shell: sh\n"}

MAY_PASS = [
    "until pytest; do break; done",
    "until pytest; do sleep 0; break; done",
    "pytest && echo ok; echo next",
    "if pytest; then echo ok; fi",
    "while pytest; do break; done",
    "if [ -n x ]; then pytest && echo ok; fi; echo next",
    "for d in a; do pytest && echo ok; done; echo next",
    "{ pytest && echo ok; }; echo next",
    "{ pytest && echo ok; }; exit 0",
    "{ pytest; echo done; } && echo ok",
    "f() { pytest; echo done; }; f && echo ok",
    "f() { pytest; }; if f; then echo ok; fi",
    "pytest || echo failed",
    "pytest || echo failed; echo next",
    "(pytest || echo failed)",
    "ruff check . --exit-zero && pytest || echo x",
]

# Passes or fails on what the later statement finds (``x`` present or not).
DEPENDS = ["pytest && echo ok; [ -f x ] && exit 0"]

CREDITED = [
    "pytest",
    "pytest; echo next",
    "pytest && echo ok",
    "ruff check . && pytest",
    "cd sub && pytest; echo done",
    "if pytest; then echo ok; else exit 1; fi",
    "if ! pytest; then exit 1; fi",
    "if [ -d sub ]; then pytest; fi",
    "for d in a b; do pytest $d; done",
    "{ pytest; }",
    "{ pytest; }; echo next",
    "{ pytest && echo ok; }",
    "f() { pytest; }; f",
    "f() { pytest && echo ok; }; f; echo next",
    "( pytest && echo ok ); echo next",
    "pytest || false",
    "pytest || exit 1",
    "pytest || exit $?",
    "pytest || { echo failed; exit 1; }",
    "pytest || (echo failed; exit 1)",
    "pytest || exit",
    "(cd $(dirname x) && pytest && echo ok); echo next",
    "if [ -n x ]; then pytest && echo ok; else echo no; fi",
]

# Multi-line block bodies, read as a ``run: |`` step.
CREDITED_BLOCKS = [
    "pytest || {\n  echo failed\n  exit 1\n}",
    "(\n  V=$(cat v)\n  pytest && echo ok\n)\necho next",
]


def _step(run: str, shell: str) -> str:
    return ("on: pull_request\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
            f"      - run: {run}\n{SHELLS[shell]}")


def _reads(text: str) -> tuple[bool, bool]:
    """``(credited, may pass)``: does ``pytest`` survive in the unmasked or the unsure lines?"""
    unmasked, unsure = mask_lines(text)
    return (any("pytest" in line for line in unmasked[5:]),
            any("pytest" in line for line in unsure))


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("run", MAY_PASS + DEPENDS)
def test_a_check_the_step_may_hide_is_not_measured(run: str, shell: str) -> None:
    assert _reads(_step(run, shell)) == (False, True)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("run", CREDITED)
def test_a_check_whose_failure_reaches_the_step_stays_credited(run: str, shell: str) -> None:
    assert _reads(_step(run, shell)) == (True, False)


@pytest.mark.parametrize("run", [
    "until pytest; do break; done", "pytest && echo ok\necho next", "if pytest; then\n  echo ok\nfi",
])
def test_a_block_body_reads_the_same(run: str) -> None:
    body = "".join(f"          {line}\n" for line in run.splitlines())
    text = f"on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n      - run: |\n{body}"
    assert _reads(text) == (False, True)


@pytest.mark.parametrize("run", CREDITED_BLOCKS)
def test_a_block_body_that_reaches_the_step_stays_credited(run: str) -> None:
    body = "".join(f"          {line}\n" for line in run.splitlines())
    text = f"on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n      - run: |\n{body}"
    assert _reads(text) == (True, False)


def test_a_github_expression_holds_no_operator() -> None:
    assert _reads(_step("pytest -n ${{ matrix.workers || 'auto' }}", "default")) == (True, False)


def test_a_gitlab_or_arm_that_can_pass_is_not_measured() -> None:
    assert mask_lines("test:\n  script:\n    - pytest || echo failed\n")[1][2] == "pytest"


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, encoding="utf-8", errors="replace")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, encoding="utf-8", errors="replace")
    return tmp_path


@pytest.mark.parametrize(("run", "expected"), [
    ("pytest || echo failed", (None, "check_may_pass", None, "check_may_pass")),
    ("ruff check . || echo failed", (None, "check_may_pass", None, "check_may_pass")),
    ("pytest || exit 1", (True, "enforced_check", None, "check_target")),
    ("pytest || true", (False, "not_enforced", False, "no_lint_command")),
])
def test_ci_checks_reads_check_may_pass(tmp_path: Path, run: str,
                                        expected: tuple[object, ...]) -> None:
    block = scan_ci_checks(_repo(tmp_path, {".github/workflows/ci.yml": _step(run, "default")}))
    got = (block["enforced_check_runs"], block["enforced_basis"], block["lint_runs"],
           block["lint_basis"])
    assert got[:2] == expected[:2]
    if "ruff" in run:
        assert got[2:] == expected[2:]


@pytest.mark.parametrize(("run", "basis"), [
    ("make foo || echo failed", "check_target"),
    ("./ci.sh || echo failed", "script_not_found"),
])
def test_a_target_or_missing_script_that_may_pass_stays_null(tmp_path: Path, run: str,
                                                             basis: str) -> None:
    block = scan_ci_checks(_repo(tmp_path, {".github/workflows/ci.yml": _step(run, "default")}))
    assert (block["enforced_check_runs"], block["enforced_basis"]) == (None, basis)


def test_a_lint_target_that_may_pass_stays_null(tmp_path: Path) -> None:
    block = scan_ci_checks(_repo(tmp_path, {".github/workflows/ci.yml": _step("make check || echo x",
                                                                                "default")}))
    assert (block["lint_runs"], block["lint_basis"]) == (None, "check_target")


def test_an_enforced_check_elsewhere_still_wins(tmp_path: Path) -> None:
    files = {".github/workflows/ci.yml": _step("pytest || echo failed", "default"),
             ".github/workflows/lint.yml": _step("pytest", "default")}
    block = scan_ci_checks(_repo(tmp_path, files))
    assert (block["enforced_check_runs"], block["enforced_basis"]) == (True, "enforced_check")
    assert block["masked_check_commands"] == 0


def test_the_coverage_gate_reads_check_may_pass(tmp_path: Path) -> None:
    files = {".github/workflows/ci.yml": _step("pytest --cov --cov-fail-under=80 || echo failed",
                                               "default")}
    block = detect_coverage_gate(_repo(tmp_path, files))
    assert (block["enforced_meets_minimum"], block["enforced_basis"]) == (None, "check_may_pass")


def _shells() -> list[list[str]]:
    found = [[b, "-e"] for b in (shutil.which("bash"),) if b]
    found += [[b, "--noprofile", "--norc", "-eo", "pipefail"] for b in (shutil.which("bash"),) if b]
    found += [[d, "-e"] for d in (shutil.which("dash"),) if d]
    return found


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="needs a POSIX bash")
@pytest.mark.parametrize(("run", "status"), [*((r, 0) for r in MAY_PASS),
                                             *((r, 1) for r in CREDITED + CREDITED_BLOCKS)])
def test_the_table_matches_real_shells(tmp_path: Path, run: str, status: int) -> None:
    (tmp_path / "bin").mkdir()
    (tmp_path / "sub").mkdir()
    (tmp_path / "v").write_text("1\n", encoding="utf-8")
    stub = tmp_path / "bin" / "pytest"
    stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    stub.chmod(0o755)
    script = tmp_path / "step.sh"
    script.write_text(run + "\n", encoding="utf-8")
    env = {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin"}
    for shell in _shells():
        done = subprocess.run([*shell, str(script)], cwd=tmp_path, env=env, capture_output=True,
                              encoding="utf-8", errors="replace", timeout=10, check=False)
        assert (done.returncode != 0) == bool(status), (shell, run, done.returncode)


def test_a_config_gate_run_that_may_pass_reads_check_may_pass(tmp_path: Path) -> None:
    files = {".github/workflows/ci.yml": _step("pytest --cov || echo failed", "default"),
             "pyproject.toml": "[tool.coverage.report]\nfail_under = 80\n"}
    block = detect_coverage_gate(_repo(tmp_path, files))
    assert (block["enforced_meets_minimum"], block["enforced_basis"]) == (None, "check_may_pass")


def test_a_folded_run_that_may_pass_reads_check_may_pass(tmp_path: Path) -> None:
    text = ("on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n      - run: >-\n"
            "          pytest\n          || echo failed\n")
    block = scan_ci_checks(_repo(tmp_path, {".github/workflows/ci.yml": text}))
    assert (block["enforced_check_runs"], block["enforced_basis"]) == (None, "check_may_pass")


def test_a_folded_coverage_run_that_may_pass_reads_check_may_pass(tmp_path: Path) -> None:
    text = ("on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n      - run: >-\n"
            "          pytest --cov --cov-fail-under=80\n          || echo failed\n")
    block = detect_coverage_gate(_repo(tmp_path, {".github/workflows/ci.yml": text}))
    assert (block["enforced_meets_minimum"], block["enforced_basis"]) == (None, "check_may_pass")


def _block_step(body: str) -> str:
    lines = "".join(f"          {line}\n" for line in body.splitlines())
    return f"on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n      - run: |\n{lines}"


# End to end. The CI-line classifier takes no line that opens with ``if``, ``while``,
# ``until``, ``{`` or ``f()`` as a check (on main too, a known limit tracked on #1061), so
# those forms read no check at all; the masking rules above still apply to them. A
# multi-line ``{ }`` group or function body puts the check on its own line, which reads
# check_may_pass, or credit for a plain call.
@pytest.mark.parametrize(("text", "expected"), [
    pytest.param(_step("if pytest; then echo ok; fi", "default"), (False, "no_check_command"), id="if-line"),
    pytest.param(_step("while pytest; do break; done", "default"), (False, "no_check_command"), id="while-line"),
    pytest.param(_step("until pytest; do break; done", "default"), (False, "no_check_command"), id="until-line"),
    pytest.param(_step("{ pytest && echo ok; }; echo next", "default"), (False, "no_check_command"),
                 id="brace-line"),
    pytest.param(_step("f() { pytest; echo done; }; f && echo ok", "default"),
                 (False, "no_check_command"), id="function-line"),
    pytest.param(_step("if ! pytest; then exit 1; fi", "default"), (False, "no_check_command"),
                 id="if-not-line"),
    pytest.param(_step("{ pytest; }; echo next", "default"), (False, "no_check_command"),
                 id="brace-credited-line"),
    pytest.param(_step("f() { pytest; }; f", "default"), (False, "no_check_command"),
                 id="function-credited-line"),
    pytest.param(_block_step("if pytest; then\n  echo ok\nfi"), (False, "no_check_command"), id="if-block"),
    pytest.param(_block_step("while pytest; do\n  break\ndone"), (False, "no_check_command"),
                 id="while-block"),
    pytest.param(_block_step("until pytest; do\n  break\ndone"), (False, "no_check_command"),
                 id="until-block"),
    pytest.param(_block_step("{\n  pytest && echo ok\n}\necho next"), (None, "check_may_pass"),
                 id="brace-block"),
    pytest.param(_block_step("f() {\n  pytest\n  echo done\n}\nf && echo ok"), (None, "check_may_pass"),
                 id="function-block"),
    pytest.param(_block_step("f() {\n  pytest\n}\nf"), (True, "enforced_check"), id="function-block-credited"),
    pytest.param(_step("pytest && echo ok; echo next", "default"), (None, "check_may_pass"), id="and-next"),
    pytest.param(_step("pytest || echo failed", "default"), (None, "check_may_pass"), id="or-echo"),
])
def test_end_to_end_readings(tmp_path: Path, text: str, expected: tuple[object, str]) -> None:
    block = scan_ci_checks(_repo(tmp_path, {".github/workflows/ci.yml": text}))
    assert (block["enforced_check_runs"], block["enforced_basis"]) == expected
