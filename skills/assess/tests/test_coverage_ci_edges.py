"""Tests for the #954 coverage-gate CI credit edge cases (lib/coverage_ci.py and
lib/coverage_activation.py).

Each form from PR #933's review either reads as the real tool behaves or reads null
with a reason, never true where no CI run enforces the gate. The expected exits were
checked against the tools: pytest-cov 7.1 ``--no-cov`` turns coverage off wherever it
stands (on the line or in ``addopts``, before or after ``--cov``), and
``--no-cov-on-fail`` does not; on Gradle 9.4, ``check -x jacocoTestCoverageVerification``
(or ``--exclude-task=``) runs ``check`` without the verification task it depends on,
``-m`` / ``--dry-run`` marks every task SKIPPED, and ``--task-graph`` prints the graph
and runs nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.coverage_activation import addopts_cov, covers, read_activation
from lib.coverage_ci import gradle_tasks, runs_tool
from lib.coverage_gate import detect_coverage_gate

PYPROJECT_80 = "[project]\nname = 'x'\n\n[tool.coverage.report]\nfail_under = 80\n"
GRADLE_080 = ('plugins { id "jacoco" }\njacocoTestCoverageVerification {\n'
              '  violationRules { rule { limit { minimum = 0.80 } } }\n}\n')
GRADLE_WIRED = GRADLE_080 + "check.dependsOn jacocoTestCoverageVerification\n"
JEST_80 = "module.exports = { coverageThreshold: { global: { lines: 80 } } };\n"


def _ci(*steps: str) -> dict[str, str]:
    body = "".join(f"      - run: {step}\n" for step in steps)
    return {".github/workflows/ci.yml":
            f"on: pull_request\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n{body}"}


def _read(root: Path, files: dict[str, str]) -> tuple[bool | None, str]:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    block = detect_coverage_gate(root)
    return block["enforced_meets_minimum"], block["enforced_basis"]


# --- case 1: the credited run's own threshold ---------------------------------

@pytest.mark.parametrize(("line", "expected"), [
    # The 90 is echo text; the run enforces 10, below the minimum.
    ("echo --cov-fail-under=90 && pytest --cov --cov-fail-under=10", (False, "not_run_in_ci")),
    ("printf --cov-fail-under=90 ; pytest --cov --cov-fail-under=10", (False, "not_run_in_ci")),
    # The mirror: the echo names 10, the run enforces 90 (never a false 0).
    ("echo --cov-fail-under=10 && pytest --cov --cov-fail-under=90", (True, "ci_run")),
    ("pytest --cov --cov-fail-under=90", (True, "ci_run")),
], ids=["echo-90-run-10", "printf-90-run-10", "echo-10-run-90", "plain"])
def test_case1_inline_threshold_is_the_runs_own(
        tmp_path: Path, line: str, expected: tuple[bool | None, str]) -> None:
    assert _read(tmp_path, _ci(line)) == expected


# --- case 2: --no-cov ---------------------------------------------------------

@pytest.mark.parametrize(("files", "expected"), [
    ({**_ci("pytest --cov --no-cov"), "pyproject.toml": PYPROJECT_80},
     (False, "not_run_in_ci")),
    ({**_ci("pytest --no-cov --cov"), "pyproject.toml": PYPROJECT_80},
     (False, "not_run_in_ci")),
    (_ci("pytest --cov --no-cov --cov-fail-under=80"), (False, "not_run_in_ci")),
    ({**_ci("pytest --no-cov"), "pyproject.toml": PYPROJECT_80
      + "[tool.pytest.ini_options]\naddopts = \"--cov=src\"\n"}, (False, "not_run_in_ci")),
    ({**_ci("pytest --cov"), "pyproject.toml": PYPROJECT_80
      + "[tool.pytest.ini_options]\naddopts = \"--no-cov\"\n"}, (False, "not_run_in_ci")),
    ({**_ci("pytest --cov --cov-fail-under=80"),
      "pytest.ini": "[pytest]\naddopts = --no-cov\n"}, (False, "not_run_in_ci")),
    # --no-cov-on-fail only skips the report on a failure: the gate still runs.
    ({**_ci("pytest --cov --no-cov-on-fail"), "pyproject.toml": PYPROJECT_80},
     (True, "ci_run")),
], ids=["after-cov", "before-cov", "inline-gate", "line-beats-addopts", "addopts-beats-line",
        "root-addopts-beats-inline", "no-cov-on-fail"])
def test_case2_no_cov_turns_coverage_off(
        tmp_path: Path, files: dict[str, str], expected: tuple[bool | None, str]) -> None:
    assert _read(tmp_path, files) == expected


# --- cases 3 and 4: Gradle -x and dry runs ------------------------------------

@pytest.mark.parametrize(("line", "build", "expected"), [
    ("./gradlew check -x jacocoTestCoverageVerification", GRADLE_WIRED,
     (False, "not_run_in_ci")),
    ("./gradlew build --exclude-task jacocoTestCoverageVerification", GRADLE_WIRED,
     (False, "not_run_in_ci")),
    ("./gradlew build --exclude-task=:app:jacocoTestCoverageVerification", GRADLE_WIRED,
     (False, "not_run_in_ci")),
    ("./gradlew test jacocoTestCoverageVerification -x jacocoTestCoverageVerification",
     GRADLE_080, (False, "not_run_in_ci")),
    # -x check stops the wired check crediting it; another wiring may still run it.
    ("./gradlew build -x check", GRADLE_WIRED, (None, "indirect_run")),
    ("./gradlew check -x test", GRADLE_WIRED, (True, "ci_run")),
    ("./gradlew jacocoTestCoverageVerification --dry-run", GRADLE_080,
     (False, "not_run_in_ci")),
    ("./gradlew check -m", GRADLE_WIRED, (False, "not_run_in_ci")),
    ("gradle build --task-graph", GRADLE_WIRED, (False, "not_run_in_ci")),
    ("./gradlew test -m", GRADLE_080, (False, "not_run_in_ci")),
], ids=["check-x", "exclude-task", "exclude-task-eq-path", "named-and-excluded",
        "x-check", "x-test", "dry-run", "m-wired-check", "task-graph", "test-dry-run"])
def test_cases3_4_gradle_exclusion_and_dry_run(
        tmp_path: Path, line: str, build: str, expected: tuple[bool | None, str]) -> None:
    assert _read(tmp_path, {**_ci(line), "build.gradle": build}) == expected


def test_gradle_tasks_splits_run_from_excluded() -> None:
    assert gradle_tasks(["check", "-x", ":app:test", "--exclude-task=lint", "-q"]) == (
        ["check"], {"test", "lint"})


@pytest.mark.parametrize(("words", "expected"), [
    (("./gradlew", "check", "-x", "jacocoTestCoverageVerification"), False),
    (("./gradlew", "jacocoTestCoverageVerification", "--dry-run"), False),
    (("./gradlew", "jacocoTestCoverageVerification"), True),
    (("pytest", "--cov", "--no-cov"), False),
])
def test_runs_tool_edges(words: tuple[str, ...], expected: bool) -> None:
    family = "gradle-jacoco" if words[0].endswith("gradlew") else "coverage.py"
    assert runs_tool(words, family, gradle_check=True) is expected


# --- case 5: hatch, pdm and poe script sections -------------------------------

@pytest.mark.parametrize(("section", "line"), [
    ("[tool.hatch.envs.test.scripts]\ncov = \"pytest --cov --cov-fail-under=80\"\n",
     "hatch run test:cov"),
    ("[tool.pdm.scripts]\ncov = \"pytest --cov --cov-fail-under=80\"\n", "pdm run cov"),
    ("[tool.poe.tasks]\ncov = \"pytest --cov --cov-fail-under=80\"\n", "poe cov"),
    ("[tool.poe.tasks.cov]\ncmd = \"coverage report --fail-under=80\"\n", "poe cov"),
    ("[tool.poe.tasks]\ncov = \"pytest --cov --cov-fail-under=80\"\n", "uv run poe cov"),
    ("[tool.poe.tasks]\ncov = \"pytest --cov --cov-fail-under=80\"\n", "poetry run poe cov"),
    ("[tool.pdm.scripts]\ncov = \"pytest --cov --cov-fail-under=80\"\n", "pdm cov"),
    ("[tool.taskipy.tasks]\ncov = \"pytest --cov --cov-fail-under=80\"\n", "task cov"),
], ids=["hatch", "pdm", "poe", "poe-table", "uv-run-poe", "poetry-run-poe", "pdm-bare",
        "taskipy"])
def test_case5_script_section_reads_indirect_run(
        tmp_path: Path, section: str, line: str) -> None:
    files = {**_ci(line), "pyproject.toml": "[project]\nname = 'x'\n\n" + section}
    assert _read(tmp_path, files) == (None, "indirect_run")


def test_pdm_run_reads_through_to_the_program(tmp_path: Path) -> None:
    files = {**_ci("pdm run pytest --cov"), "pyproject.toml": PYPROJECT_80}
    assert _read(tmp_path, files) == (True, "ci_run")


def test_case5_setup_cfg_tox_command_reads_indirect_run(tmp_path: Path) -> None:
    files = {**_ci("tox"), "setup.cfg":
             "[tox:tox]\nenvlist = py\n\n[testenv]\ncommands = pytest --cov --cov-fail-under=80\n"}
    assert _read(tmp_path, files) == (None, "indirect_run")


@pytest.mark.parametrize("line", ["pdm install", "pdm sync -G test", "poe",
                                  "pdm run ruff check .", "pdm run pytest"])
def test_case5_a_pdm_command_or_bare_poe_runs_no_script(tmp_path: Path, line: str) -> None:
    files = {**_ci(line), "pyproject.toml":
             "[tool.pdm.scripts]\ncov = \"pytest --cov --cov-fail-under=80\"\n"}
    assert _read(tmp_path, files) == (False, "not_run_in_ci")


def test_case5_script_section_with_no_ci_run_reads_not_run(tmp_path: Path) -> None:
    files = {**_ci("pytest"), "pyproject.toml":
             "[tool.hatch.envs.test.scripts]\ncov = \"pytest --cov --cov-fail-under=80\"\n"}
    assert _read(tmp_path, files) == (False, "not_run_in_ci")


# --- case 6: activation keyed by the gate's directory --------------------------

ADDOPTS_COV = "[tool.pytest.ini_options]\naddopts = \"--cov=src\"\n"


@pytest.mark.parametrize(("files", "expected"), [
    # One package's addopts does not switch on a sibling's gate.
    ({**_ci("pytest"), "a/pyproject.toml": ADDOPTS_COV, "b/pyproject.toml": PYPROJECT_80},
     (False, "not_run_in_ci")),
    ({**_ci("npx jest"), "a/jest.config.js": "module.exports = { collectCoverage: true };\n",
      "b/jest.config.js": JEST_80}, (None, "indirect_run")),
    # The gate's own package, or the root above it, still does.
    ({**_ci("pytest"), "b/pyproject.toml": PYPROJECT_80 + ADDOPTS_COV}, (True, "ci_run")),
    ({**_ci("pytest"), "pytest.ini": "[pytest]\naddopts = --cov\n",
      "b/pyproject.toml": PYPROJECT_80}, (True, "ci_run")),
    ({**_ci("npx jest"), "b/jest.config.js": "module.exports = { collectCoverage: true,\n"
      "  coverageThreshold: { global: { lines: 80 } } };\n"}, (True, "ci_run")),
    # A gate on a CI line: a package-only addopts may or may not apply (null).
    ({**_ci("pytest --cov-fail-under=80"), "a/pyproject.toml": ADDOPTS_COV},
     (None, "indirect_run")),
    ({**_ci("pytest --cov-fail-under=80"), "pyproject.toml": ADDOPTS_COV}, (True, "ci_run")),
], ids=["pytest-sibling", "jest-sibling", "pytest-same-dir", "pytest-root-addopts",
        "jest-same-dir", "ci-line-package-addopts", "ci-line-root-addopts"])
def test_case6_activation_is_per_directory(
        tmp_path: Path, files: dict[str, str], expected: tuple[bool | None, str]) -> None:
    assert _read(tmp_path, files) == expected


def test_covers_reads_own_directory_and_above() -> None:
    assert covers(frozenset({"a"}), "a/pyproject.toml")
    assert covers(frozenset({"a"}), "a/sub/jest.config.js")
    assert covers(frozenset({""}), "b/pyproject.toml")
    assert not covers(frozenset({"a"}), "b/pyproject.toml")
    assert not covers(frozenset({"a"}), "ab/pyproject.toml")


def test_read_activation_keys_by_directory() -> None:
    act = read_activation([
        ("a/pyproject.toml", ADDOPTS_COV.splitlines()),
        ("b/setup.cfg", ["[tool:pytest]", "addopts = --no-cov"]),
        ("c/package.json", ['{"jest": {"collectCoverage": true}}']),
    ])
    assert (act.pytest_cov, act.pytest_off, act.jest_collects) == (
        frozenset({"a"}), frozenset({"b"}), frozenset({"c"}))
    assert act.pytest_lines == frozenset({("a/pyproject.toml", 2), ("b/setup.cfg", 2)})


@pytest.mark.parametrize(("lines", "expected"), [
    (["[pytest]", "addopts = --cov"], True),
    (["[pytest]", "addopts = --cov --no-cov"], None),
    (["[pytest]", "addopts = --cov --no-cov-on-fail"], True),
    (["[pytest]", "addopts = -q"], False),
])
def test_addopts_cov_states(lines: list[str], expected: bool | None) -> None:
    assert addopts_cov(lines) is expected
