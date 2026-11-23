"""Gradle dry runs and exclusions, and pdm / poe read-through, on the CI reader (#1046).

Each expected reading was checked against the real tools (Gradle 9.4 on a two-project
build whose ``check`` runs a failing test and a failing lint task in each project; pdm 2.29.7 and
poethepoet 0.49.0 on a ``pyproject.toml`` with scripts):

- ``-m``, ``-qm``, ``-mq``, ``--dry-run`` and ``--task-graph`` run no task and exit 0;
  ``-Pm`` and ``-Dmode=x`` run everything.
- ``check -x test`` and ``-x test check`` run the lint tasks and no test; ``test -x test``
  and ``check -x check`` run nothing and exit 0; ``check -x :app:test`` still runs the root
  project's test.
- ``pdm run false`` exits 1 (the program runs); ``pdm false`` exits 2 ("Command not found");
  with a ``pytest`` script, ``pdm run pytest`` and ``pdm pytest`` both run the script.
- ``poe lint`` runs the task; ``poe false`` with no such task is an error.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.check_command import check_verdict, is_lint_run, may_check, may_lint
from lib.ci_checks import scan_ci_checks
from lib.lint_vocabulary import gradle_tasks as lint_gradle_tasks
from lib.runner_args import (
    SCRIPT, gradle_dry_run, gradle_run, gradle_skips_tests, gradle_tasks, pdm_scripts,
    pdm_scripts_of, reads_pdm_scripts, runs_no_task, script_run)
from lib.testing.coverage_gate import detect_coverage_gate

SCRIPTS = "[tool.pdm.scripts]\npytest = \"echo SHADOWED\"\nlint = \"ruff check .\"\n_ = {env = {A = \"1\"}}\n"


def _wf(*runs: str) -> str:
    steps = "".join(f"      - run: {r}\n" for r in runs)
    return f"on: pull_request\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n{steps}"


def _repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, encoding="utf-8", errors="replace")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, encoding="utf-8", errors="replace")
    return root


def _scan(root: Path, *runs: str, extra: dict[str, str] | None = None
          ) -> tuple[bool | None, str, bool | None, str]:
    block = scan_ci_checks(_repo(root, {".github/workflows/ci.yml": _wf(*runs), **(extra or {})}))
    return (block["enforced_check_runs"], block["enforced_basis"],
            block["lint_runs"], block["lint_basis"])


# --- Gradle: the parser -------------------------------------------------------------

@pytest.mark.parametrize(("args", "dry"), [
    ("-m check", True), ("-qm check", True), ("-mq check", True), ("--dry-run check", True),
    ("check --task-graph", True), ("check", False), ("-q check", False),
    ("-Pm check", False), ("-Dmode=x check", False), ("-Pmx check", False),
    ("--max-workers=2 check", False),
])
def test_gradle_dry_run_stops_at_a_value_taking_option(args: str, dry: bool) -> None:
    assert gradle_dry_run(args.split()) is dry
    assert runs_no_task(["./gradlew", *args.split()]) is dry


def test_runs_no_task_keeps_the_make_rule() -> None:
    assert runs_no_task(["make", "-n", "test"]) and not runs_no_task(["make", "test"])


@pytest.mark.parametrize(("words", "run"), [
    ("./gradlew check -x test", "./gradlew check"),
    ("./gradlew -x test check", "./gradlew check"),
    ("./gradlew check -x check", "./gradlew"),
    ("./gradlew test --exclude-task=test", "./gradlew"),
    ("./gradlew :app:test -x test", "./gradlew"),
    ("./gradlew :app:test -x :app:test", "./gradlew"),
    ("./gradlew test -x :app:test", "./gradlew test"),
    ("./gradlew -p sub check", "./gradlew -p sub check"),
    ("mvn -x verify", "mvn -x verify"),
])
def test_gradle_run_applies_exclusions(words: str, run: str) -> None:
    assert gradle_run(words.split()) == run.split()


def test_gradle_tasks_skip_option_values() -> None:
    assert gradle_tasks(["-p", "sub", "-P", "k", "check"]) == (["check"], set())
    assert lint_gradle_tasks(["ktlintCheck", "-x", "ktlintCheck", "detekt"]) == ["detekt"]


@pytest.mark.parametrize(("words", "skips"), [
    ("./gradlew check -x test", True), ("./gradlew -x test check", True),
    ("./gradlew check -x :app:test", True), ("./gradlew check", False),
    ("./gradlew check integrationTest -x test", False), ("mvn verify -x test", False),
])
def test_gradle_skips_tests(words: str, skips: bool) -> None:
    assert gradle_skips_tests(words.split()) is skips


# --- Gradle: both directions per form, Layer 5 and Layer 3 --------------------------

@pytest.mark.parametrize(("line", "check", "lint"), [
    ("./gradlew check", True, False),
    ("./gradlew -m check", False, False),
    ("./gradlew -qm check", False, False),
    ("./gradlew --dry-run check", False, False),
    ("./gradlew --task-graph check", False, False),
    ("./gradlew -Pm check", True, False),
    ("./gradlew -Dmode=x check", True, False),
    ("./gradlew check -x check", False, False),
    ("./gradlew test -x test", False, False),
    ("./gradlew assemble -x testDebugUnitTest", False, False),
    ("./gradlew ktlintCheck", False, True),
    ("./gradlew -m ktlintCheck", False, False),
    ("./gradlew ktlintCheck -x ktlintCheck", False, False),
])
def test_gradle_forms(line: str, check: bool, lint: bool) -> None:
    assert (check_verdict(line) is True) is check
    assert is_lint_run(line) is lint


def test_a_dry_run_may_neither_check_nor_lint() -> None:
    for line in ("./gradlew -qm check", "./gradlew --task-graph build"):
        assert not may_check(line) and not may_lint(line)


@pytest.mark.parametrize("line", ["./gradlew -qm check", "./gradlew check -x test"])
def test_issue_lines_do_not_credit_layer_5(tmp_path: Path, line: str) -> None:
    runs, basis, _, _ = _scan(tmp_path, line)
    assert runs is not True
    if "-x" in line:   # check still runs its other tasks: unsure, never false (as -DskipTests)
        assert (runs, basis) == (None, "check_target")
    else:
        assert (runs, basis) == (False, "no_check_command")


def test_check_without_tests_may_still_lint(tmp_path: Path) -> None:
    _, _, lint, lint_basis = _scan(tmp_path, "./gradlew check -x test")
    assert (lint, lint_basis) == (None, "check_target")


def test_plain_gradle_check_still_credits(tmp_path: Path) -> None:
    assert _scan(tmp_path, "./gradlew check")[:2] == (True, "enforced_check")


def test_project_path_check_credits_layer_5(tmp_path: Path) -> None:
    """``:app:check`` runs app's checks: a check in one project is still a check."""
    assert _scan(tmp_path, "./gradlew :app:check")[:2] == (True, "enforced_check")


# --- pdm and poe ----------------------------------------------------------------------

def test_pdm_scripts_reads_the_table_and_skips_shared_options(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(SCRIPTS + "[tool.pdm.scripts.cov]\ncmd = \"x\"\n",
                                             encoding="utf-8")
    assert pdm_scripts(tmp_path) == {"pytest", "lint", "cov"}


def test_pdm_scripts_reads_every_tracked_pyproject(tmp_path: Path) -> None:
    _repo(tmp_path, {"pyproject.toml": "[project]\nname = 'x'\n",
                     "backend/pyproject.toml": "[tool.pdm.scripts]\ncov = \"x\"\n"})
    assert pdm_scripts(tmp_path) == {"cov"}


def test_pdm_scripts_absent_and_unreadable(tmp_path: Path) -> None:
    assert pdm_scripts(tmp_path) == frozenset()
    (tmp_path / "pyproject.toml").write_text("[tool.pdm.scripts\n", encoding="utf-8")
    assert pdm_scripts(tmp_path) is None


@pytest.mark.parametrize(("words", "scripts", "expected"), [
    ("pdm run ruff check .", None, (["ruff", "check", "."], False)),
    ("pdm run -p sub ruff check .", None, (["ruff", "check", "."], False)),
    ("pdm run pytest", frozenset({"pytest"}), (["pytest"], True)),
    ("pdm run pytest", frozenset(), (["pytest"], False)),
    ("pdm pytest", frozenset(), ([], True)),
    ("pdm pytest", frozenset({"pytest"}), (["pytest"], True)),
    ("pdm lint", None, (["lint"], True)),
    ("pdm install", None, None),
    ("poe lint", None, (["lint"], True)),
    ("poe -C sub lint", None, (["lint"], True)),
    ("hatch run lint", None, (["lint"], False)),
    ("hatch fmt --check", None, None),
    ("ruff check .", None, None),
])
def test_script_run(tmp_path: Path, words: str, scripts: frozenset[str] | None,
                    expected: tuple[list[str], bool] | None) -> None:
    if scripts is not None:
        body = "".join(f"{s} = \"x\"\n" for s in scripts)
        (tmp_path / "pyproject.toml").write_text(f"[tool.pdm.scripts]\n{body}", encoding="utf-8")
        with pdm_scripts_of(tmp_path):
            assert script_run(words.split()) == expected
    else:
        assert script_run(words.split()) == expected


@pytest.mark.parametrize(("line", "check", "lint", "maylint"), [
    ("pdm run ruff check .", None, True, False),
    ("pdm run pytest", True, False, False),
    ("pdm run lint", None, True, True),
    ("pdm run foo", None, False, True),
    ("pdm install", False, False, False),
    ("poe lint", None, True, True),
    ("uv run poe lint", None, True, True),
    ("poe test", True, False, True),
    ("poe foo", None, False, True),
    ("poe ruff", None, False, True),   # a task named ruff: its recipe is unread
    ("poe", False, False, False),
    # Wrapped forms read as the bare ones: poe's word is a task, never a program.
    ("uv run poe pytest", None, False, True),
    ("poetry run poe ruff", None, False, True),
    ("uv run poe test", True, False, True),
    ("uv run pdm run ruff check .", True, True, False),
    ("hatch run lint", None, True, True),
    ("uv run hatch run lint", None, True, True),
])
def test_pdm_and_poe_read_through(line: str, check: bool | None, lint: bool, maylint: bool) -> None:
    assert check_verdict(line) is check
    assert is_lint_run(line) is lint
    assert may_lint(line) is maylint


def test_issue_pdm_run_ruff_credits_a_lint_run(tmp_path: Path) -> None:
    assert _scan(tmp_path, "pdm run ruff check .")[2:] == (True, "lint_command")


@pytest.mark.parametrize(("line", "expected"), [
    # A pytest script shadows the program: its recipe is unread, so null, never credit.
    ("pdm run pytest", (None, "check_target")),
    ("pdm pytest", (None, "check_target")),
    ("uv run pdm pytest", (None, "check_target")),
    ("uv run pdm run pytest", (None, "check_target")),
    # No such script: pdm runs the program, and a bare pdm word is "Command not found".
    ("pdm run ruff check .", (True, "enforced_check")),
])
def test_shadowing_script_reads_as_the_script(tmp_path: Path, line: str,
                                              expected: tuple[bool | None, str]) -> None:
    assert _scan(tmp_path, line, extra={"pyproject.toml": SCRIPTS})[:2] == expected


def test_bare_pdm_word_without_a_script_runs_nothing(tmp_path: Path) -> None:
    runs, basis, lint, _ = _scan(tmp_path, "pdm pytest", extra={"pyproject.toml": "[project]\nname = 'x'\n"})
    assert (runs, basis, lint) == (False, "no_check_command", False)


def test_scripts_are_read_only_inside_the_scan(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(SCRIPTS, encoding="utf-8")
    seen: list[tuple[list[str], bool] | None] = []
    reads_pdm_scripts(lambda root: seen.append(script_run(["pdm", "run", "pytest"])))(tmp_path)
    assert seen == [(["pytest"], True)]
    assert script_run(["pdm", "run", "pytest"]) == (["pytest"], False)
    assert reads_pdm_scripts(lambda root: 1)(None) == 1


# --- the coverage gate (#954): shadow and bare pdm ------------------------------------

COV_80 = "[tool.coverage.report]\nfail_under = 80\n"


def _gate(root: Path, line: str, pyproject: str, rel: str = "pyproject.toml",
          ) -> tuple[bool | None, str]:
    block = detect_coverage_gate(_repo(root, {".github/workflows/ci.yml": _wf(line), rel: pyproject}))
    return block["enforced_meets_minimum"], block["enforced_basis"]


@pytest.mark.parametrize(("line", "pyproject", "expected"), [
    ("pdm run pytest --cov", "[project]\nname = 'x'\n\n" + COV_80, (True, "ci_run")),
    ("pdm run pytest --cov", SCRIPTS + COV_80, (None, "indirect_run")),
    ("pdm run pytest --cov --cov-fail-under=99", SCRIPTS, (None, "indirect_run")),
    ("pdm run pytest --cov --cov-fail-under=99", "[project]\nname = 'x'\n", (True, "ci_run")),
    ("pdm pytest --cov --cov-fail-under=99", "[project]\nname = 'x'\n", (False, "not_run_in_ci")),
    ("pdm pytest --cov --cov-fail-under=99", SCRIPTS, (None, "indirect_run")),
])
def test_coverage_gate_reads_pdm_scripts(tmp_path: Path, line: str, pyproject: str,
                                         expected: tuple[bool | None, str]) -> None:
    assert _gate(tmp_path, line, pyproject) == expected


def test_script_word_is_no_shell_word() -> None:
    assert SCRIPT.startswith("<")


@pytest.mark.parametrize("root_pyproject", [None, "[project]\nname = 'x'\n"])
def test_a_subdirectory_pdm_script_still_reads_indirect_run(tmp_path: Path,
                                                            root_pyproject: str | None) -> None:
    """``cd backend && pdm cov``: the script lives in a subdirectory's pyproject.toml, so a
    bare ``pdm cov`` may run it (as on main), never "no such script"."""
    if root_pyproject is not None:
        (tmp_path / "pyproject.toml").write_text(root_pyproject, encoding="utf-8")
    section = "[tool.pdm.scripts]\ncov = \"pytest --cov --cov-fail-under=80\"\n"
    assert _gate(tmp_path, "pdm cov", "[project]\nname = 'b'\n\n" + section,
                 rel="backend/pyproject.toml") == (None, "indirect_run")


def test_poe_pytest_is_a_task_on_the_coverage_gate(tmp_path: Path) -> None:
    """poe runs no program by name: ``poe pytest`` runs a task, whose recipe is unread."""
    section = "[tool.poe.tasks]\npytest = \"pytest --cov --cov-fail-under=80\"\n"
    assert _gate(tmp_path, "poe pytest", "[project]\nname = 'x'\n\n" + section) == (
        None, "indirect_run")
