"""Tests for lib/coverage_ci.py: the Step 4 coverage gate needs a CI run (issue #921).

``coverage_gate.enforced_meets_minimum`` is ``threshold_meets_minimum`` with an
enforced CI line that runs the gate required, by #775's masking and trigger
rules. Each repro from the issue reads unmet, a JaCoCo missed-counter gate
reads unknown, and a CI job that runs coverage with the threshold reads met.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.coverage_ci import _command_groups, _word_lists, runs_tool
from lib.coverage_gate import detect_coverage_gate

PYPROJECT_80 = "[project]\nname = 'x'\n\n[tool.coverage.report]\nfail_under = 80\n"
CLAUDE = {"CLAUDE.md": "# Guide\n\nRun `pytest` before pushing.\n"}


def _wf(*steps: str, on: str = "pull_request") -> str:
    body = "".join(f"      - run: {step}\n" for step in steps)
    return f"on: {on}\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n{body}"


def _ci(*steps: str, on: str = "pull_request", name: str = "ci.yml") -> dict[str, str]:
    return {f".github/workflows/{name}": _wf(*steps, on=on)}


def _pom(limit: str) -> str:
    return ("<project><build><plugins><plugin><groupId>org.jacoco</groupId>"
            "<artifactId>jacoco-maven-plugin</artifactId><executions><execution><goals>"
            "<goal>check</goal></goals><configuration><rules><rule><limits><limit>"
            f"<counter>LINE</counter>{limit}</limit></limits></rule></rules></configuration>"
            "</execution></executions></plugin></plugins></build></project>\n")


def _read(root: Path, files: dict[str, str]) -> tuple[bool | None, str]:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    block = detect_coverage_gate(root)
    return block["enforced_meets_minimum"], block["enforced_basis"]


# --- the issue's acceptance --------------------------------------------------

@pytest.mark.parametrize(("files", "expected"), [
    # Met: a CI job runs coverage with the threshold.
    (_ci("pytest --cov --cov-fail-under=80"), (True, "ci_run")),
    ({**_ci("pytest --cov"), "pyproject.toml": PYPROJECT_80}, (True, "ci_run")),
    # Unmet: the issue's repros.
    ({**CLAUDE, **_ci("pytest"), "pyproject.toml": PYPROJECT_80}, (False, "not_run_in_ci")),
    ({**_ci("pytest"), **_ci("pytest --cov-fail-under=85", on="workflow_dispatch",
                             name="cov.yml")}, (False, "not_run_in_ci")),
    (_ci("pytest --cov-fail-under=85 || true"), (False, "not_run_in_ci")),
    ({".github/workflows/ci.yml": "on: pull_request\njobs:\n  t:\n    runs-on: x\n"
      "    steps:\n      - run: |\n          set +e\n          pytest --cov-fail-under=90\n"
      "          echo ok\n"}, (False, "not_run_in_ci")),
    ({".gitlab-ci.yml": "test:\n  script:\n    - pytest --cov-fail-under=90\n  when: manual\n"},
     (False, "not_run_in_ci")),
    (_ci("echo 'use --cov-fail-under=90 locally'"), (False, "not_run_in_ci")),
    # Unknown: a JaCoCo missed-counter maximum, whose percentage is not read.
    ({"pom.xml": _pom("<value>MISSEDCOUNT</value><maximum>100</maximum>")},
     (None, "threshold_not_read")),
], ids=["cov-flag", "cov-with-fail-under", "plain-pytest", "dispatch-only", "or-true",
        "set-plus-e", "gitlab-manual", "echo", "jacoco-missedcount"])
def test_issue_acceptance(tmp_path: Path, files: dict[str, str],
                          expected: tuple[bool | None, str]) -> None:
    assert _read(tmp_path, files) == expected


# --- what counts as a run ----------------------------------------------------

@pytest.mark.parametrize(("files", "expected"), [
    ({"pyproject.toml": PYPROJECT_80}, (False, "no_ci")),
    ({**_ci("coverage run -m pytest"), "pyproject.toml": PYPROJECT_80}, (False, "not_run_in_ci")),
    ({**_ci("coverage run -m pytest && coverage report"), "pyproject.toml": PYPROJECT_80},
     (True, "ci_run")),
    ({**_ci("uv run --locked python -m pytest --cov=src"), "pyproject.toml": PYPROJECT_80},
     (True, "ci_run")),
    ({**_ci("pytest"), "pyproject.toml": "[tool.pytest.ini_options]\naddopts = \"--cov=app\"\n"
      "[tool.coverage.report]\nfail_under = 80\n"}, (True, "ci_run")),
    ({**_ci("pytest"), "pytest.ini": "[pytest]\naddopts = --cov --cov-fail-under=80\n"},
     (True, "ci_run")),
    ({**_ci("pytest"), "tox.ini": "[pytest]\naddopts = --cov=pkg --cov-fail-under=80\n"},
     (True, "ci_run")),
    ({**_ci("pytest"), "setup.cfg": "[tool:pytest]\naddopts = --cov --cov-fail-under=80\n"},
     (True, "ci_run")),
    ({**_ci("pytest"), "tox.ini": "[testenv]\ncommands = pytest --cov --cov-fail-under=80\n"},
     (False, "not_run_in_ci")),
    ({**_ci("pytest"), "pyproject.toml": "[tool.pytest.ini_options]\naddopts = [\n  \"-v\",\n"
      "  \"--cov=app\",\n]\n[tool.coverage.report]\nfail_under = 80\n"}, (True, "ci_run")),
    ({**_ci("pytest"), "pytest.ini": "[pytest]\naddopts =\n    -v\n    --cov=app\n",
      ".coveragerc": "[report]\nfail_under = 80\n"}, (True, "ci_run")),
    ({**_ci("pnpm exec jest --coverage"),
      "package.json": '{"jest":{"coverageThreshold":{"global":{"lines":80}}}}\n'}, (True, "ci_run")),
    ({".github/workflows/ci.yml": "on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n"
      "      - run: |\n          pytest \\\n            --cov=src \\\n            --cov-report=xml\n",
      "pyproject.toml": PYPROJECT_80}, (True, "ci_run")),
    ({".github/workflows/ci.yml": "on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n"
      "      - run: |\n          pytest --cov=src \\\n            --cov-fail-under=80\n"},
     (True, "ci_run")),
    (_ci('"pytest --cov --cov-fail-under=80"'), (True, "ci_run")),
    (_ci("sh -c 'pytest --cov --cov-fail-under=80'"), (True, "ci_run")),
    (_ci("cd pkg && pytest --cov --cov-fail-under=80; echo done"), (True, "ci_run")),
    ({".github/workflows/ci.yml": "on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n"
      "      - run: >-\n          uv run pytest -v\n          --cov=scripts\n",
      "pyproject.toml": PYPROJECT_80}, (True, "ci_run")),
    ({".gitlab-ci.yml": "test:\n  script:\n    - pytest --cov --cov-fail-under=90\n"}, (True, "ci_run")),
    ({**_ci("mvn -B verify"), "pom.xml": _pom("<value>COVEREDRATIO</value><minimum>0.80</minimum>")},
     (True, "ci_run")),
    ({**_ci("mvn -B compile"), "pom.xml": _pom("<value>COVEREDRATIO</value><minimum>0.80</minimum>")},
     (False, "not_run_in_ci")),
    ({**_ci("npx jest --coverage"),
      "package.json": '{"jest":{"coverageThreshold":{"global":{"lines":80}}}}\n'}, (True, "ci_run")),
    ({**_ci("npx jest"),
      "package.json": '{"jest":{"collectCoverage":true,"coverageThreshold":{"global":{"lines":80}}}}\n'},
     (True, "ci_run")),
    ({**_ci("npx vitest run"),
      "vitest.config.ts": "export default { test: { coverage: { thresholds: { lines: 80 } } } }\n"},
     (None, "indirect_run")),
    ({**_ci("go-test-coverage --config=./.testcoverage.yml"), "go.mod": "module x\n",
      ".testcoverage.yml": "threshold:\n  total: 80\n"}, (True, "ci_run")),
    ({**_ci("cargo llvm-cov --fail-under-lines 90")}, (True, "ci_run")),
], ids=["no-ci", "coverage-run-only", "coverage-report", "python-m-pytest", "addopts-cov",
        "addopts-threshold", "tox-pytest-section", "setup-cfg-tool-pytest",
        "tox-testenv-commands", "addopts-toml-list", "addopts-ini-continuation",
        "pnpm-exec-jest", "backslash-config", "backslash-inline", "quoted-run", "sh-c", "compound", "folded-scalar", "gitlab",
        "mvn-verify", "mvn-compile", "jest-coverage", "jest-collect-coverage", "vitest-no-flag",
        "go-test-coverage", "llvm-cov"])
def test_what_counts_as_a_run(tmp_path: Path, files: dict[str, str],
                              expected: tuple[bool | None, str]) -> None:
    assert _read(tmp_path, files) == expected


# --- unknown beats wrong -----------------------------------------------------

@pytest.mark.parametrize(("files", "expected"), [
    ({**_ci("make test"), "pyproject.toml": PYPROJECT_80}, (None, "indirect_run")),
    ({**_ci("tox"), "tox.ini": "[testenv]\ncommands = pytest --cov --cov-fail-under=80\n"},
     (None, "indirect_run")),
    ({"Makefile": "test:\n\tpytest --cov --cov-fail-under=80\n"}, (False, "no_ci")),
    ({**_ci("pytest --cov", on="issue_comment"), **_ci("make check", name="check.yml"),
      "pyproject.toml": PYPROJECT_80}, (None, "indirect_run")),
    ({".github/workflows/ci.yml": "jobs:\n  t:\n    steps:\n      - run: pytest --cov\n",
      "pyproject.toml": PYPROJECT_80}, (None, "unknown_trigger")),
    ({"Jenkinsfile": "sh 'pytest --cov'\n", "pyproject.toml": PYPROJECT_80},
     (None, "unread_ci_system")),
    ({"Jenkinsfile": "sh 'pytest --cov-fail-under=80'\n"}, (None, "unread_ci_system")),
    ({".gitlab-ci.yml": "include:\n  - project: g/t\n    file: py.yml\ntest:\n  script:\n"
      "    - pytest\n", "pyproject.toml": PYPROJECT_80}, (None, "included_jobs")),
    (_ci("pytest --cov-fail-under=${{ env.COV_MIN }}"), (None, "threshold_not_read")),
    ({**_ci("pytest --cov"), "pyproject.toml": PYPROJECT_80.replace("80", "40")},
     (False, "threshold_below_minimum")),
    (_ci("pytest"), (False, "no_gate")),
], ids=["make-target", "tox-target", "makefile-no-ci", "off-trigger-tool-with-target",
        "no-on-key", "jenkins", "jenkins-inline", "gitlab-include", "templated",
        "below-minimum", "no-gate"])
def test_unread_ci_reads_unknown(tmp_path: Path, files: dict[str, str],
                                 expected: tuple[bool | None, str]) -> None:
    assert _read(tmp_path, files) == expected


ADDOPTS_COV = ("[tool.pytest.ini_options]\naddopts = \"--cov=app\"\n"
               "[tool.coverage.report]\nfail_under = 80\n")
JEST_COLLECTS = '{"jest":{"collectCoverage":true,"coverageThreshold":{"global":{"lines":80}}}}\n'


@pytest.mark.parametrize("files", [
    {".github/workflows/ci.yml": "on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n"
     "      - run: >-\n          pytest --cov=pkg\n          --cov-report=xml || true\n",
     "pyproject.toml": PYPROJECT_80},
    {".github/workflows/ci.yml": "on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n"
     "      - run: >-\n          pytest --cov=pkg\n          --cov-fail-under=80 || true\n"},
    {".github/workflows/ci.yml": "on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n"
     "      - run: |\n          pytest --cov=pkg \\\n            --cov-report=xml || true\n",
     "pyproject.toml": PYPROJECT_80},
    {".github/workflows/ci.yml": "on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n"
     "      - run: |\n          pytest --cov=pkg \\\n            --cov-fail-under=80 || true\n"},
    {**_ci("pip install pytest pytest-cov"), "pyproject.toml": ADDOPTS_COV},
    {**_ci("pip install pytest --cov"), "pyproject.toml": PYPROJECT_80},
    {**_ci("echo pytest --cov"), "pyproject.toml": PYPROJECT_80},
    _ci("echo --cov-fail-under=90"),
    _ci("pytest --cov --cov-fail-under=90 --version"),
    {**_ci("pytest --cov --co"), "pyproject.toml": PYPROJECT_80},
    _ci("printf '%s' --cov-fail-under=90"),
    _ci("pip install pytest-cov --cov-fail-under=90"),
    {**_ci("npm install --save-dev jest"), "package.json": JEST_COLLECTS},
    {**_ci("npm install --save-dev jest --coverage"), "package.json": JEST_COLLECTS},
    {**_ci("uv pip install coverage report"), "pyproject.toml": PYPROJECT_80},
    {**_ci("mvn-wrapper-setup verify"),
     "pom.xml": _pom("<value>COVEREDRATIO</value><minimum>0.80</minimum>")},
], ids=["folded-or-true-config", "folded-or-true-inline", "backslash-or-true-config",
        "backslash-or-true-inline", "pip-install-addopts", "pip-install-cov-flag", "echo-pytest", "echo-unquoted-flag", "pytest-version",
        "pytest-collect-only",
        "printf-flag", "pip-install-flag", "npm-install-jest",
        "npm-install-jest-coverage", "uv-pip-coverage", "not-maven"])
def test_no_false_credit(tmp_path: Path, files: dict[str, str]) -> None:
    """Issue #921 review: a masked continuation masks its head, and a tool must be the program
    a command runs, never an argument."""
    assert _read(tmp_path, files)[0] is not True


def test_jacoco_missed_counter_is_a_gate_with_no_percent(tmp_path: Path) -> None:
    """A MISSEDCOUNT maximum is listed (it gates something, zero included), never read as 0."""
    (tmp_path / "pom.xml").write_text(_pom("<value>MISSEDCOUNT</value><maximum>0</maximum>"), encoding="utf-8")
    block = detect_coverage_gate(tmp_path)
    assert [(g["unit"], g["threshold"], g["threshold_percent"]) for g in block["gates"]] == [
        ("uncovered_count", 0.0, None)]
    assert (block["configured"], block["threshold_meets_minimum"]) == (True, None)


def test_jacoco_missed_ratio_and_gradle_maximum(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text(_pom("<value>MISSEDRATIO</value><maximum>0.2</maximum>"), encoding="utf-8")
    (tmp_path / "build.gradle").write_text(
        'plugins { id "jacoco" }\njacocoTestCoverageVerification { violationRules { rule {\n'
        '  limit { counter = "LINE"\n    value = "MISSEDCOUNT"\n    maximum = 10 }\n} } }\n', encoding="utf-8")
    units = sorted((g["file"], g["unit"], g["form"]) for g in detect_coverage_gate(tmp_path)["gates"])
    assert units == [("build.gradle", "uncovered_count", "jacocoTestCoverageVerification maximum"),
                     ("pom.xml", "missed_ratio", "<rules> <maximum>")]


def test_jacoco_covered_ratio_maximum_is_not_a_gate(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text(_pom("<value>COVEREDRATIO</value><maximum>0.99</maximum>"), encoding="utf-8")
    assert detect_coverage_gate(tmp_path)["gates"] == []


# --- helpers -----------------------------------------------------------------

def test_echo_argument_is_one_dropped_word() -> None:
    assert _word_lists("echo 'use --cov-fail-under=90 locally'") == [(("echo",), _word_lists(
        "echo 'use --cov-fail-under=90 locally'")[0][1])]


def test_folded_paragraphs_are_one_command_each() -> None:
    lines = ["      - run: >-", "          pytest -v", "          --cov=x", "",
             "          coverage report", "      - run: ruff check ."]
    groups = _command_groups(lines, list(lines))
    assert [(list(span), text) for span, text in groups] == [
        ([1, 2], "pytest -v --cov=x"), ([4], "coverage report"), ([5], "ruff check .")]


@pytest.mark.parametrize(("words", "family", "expected"), [
    (("pytest", "--cov=src"), "coverage.py", True),
    (("pytest",), "coverage.py", False),
    (("pytest",), "pytest", False),
    (("pytest", "--cov"), "pytest", True),
    (("coverage", "xml"), "coverage.py", True),
    (("npx", "jest"), "jest", None),
    (("./mvnw", "install"), "maven-jacoco", True),
    (("./gradlew", "jacocoTestCoverageVerification"), "gradle-jacoco", True),
    (("./gradlew", "check"), "gradle-jacoco", False),
    (("npx", "c8", "npm", "test"), "nyc", True),
    (("uv", "run", "--locked", "pytest", "--cov"), "coverage.py", True),
    (("python", "-m", "pytest", "--cov"), "coverage.py", True),
    (("yarn", "jest", "--coverage"), "jest", True),
    (("bundle", "exec", "pytest", "--cov"), "coverage.py", True),
    (("docker", "compose", "run", "app", "pytest", "--cov"), "coverage.py", True),
    (("pip", "install", "pytest", "--cov"), "coverage.py", False),
    (("echo", "pytest", "--cov"), "coverage.py", False),
    (("npm", "install", "jest"), "jest", False),
])
def test_runs_tool(words: tuple[str, ...], family: str, expected: bool | None) -> None:
    assert runs_tool(words, family) is expected


# --- final review round (#921) ------------------------------------------------

JEST_80 = {"jest.config.js": "module.exports = { coverageThreshold: { global: { lines: 80 } } };\n"}
GO_TC = {"go.mod": "module x\n", ".testcoverage.yml": "threshold:\n  total: 80\n"}


def _action_step(extra: str) -> dict[str, str]:
    return {".github/workflows/ci.yml": "on: pull_request\njobs:\n  t:\n    runs-on: x\n"
            f"    steps:\n      - uses: vladopajic/go-test-coverage@v2\n{extra}"}


@pytest.mark.parametrize(("files", "expected"), [
    ({**_ci("make coverage"), "pyproject.toml": PYPROJECT_80}, (None, "indirect_run")),
    ({**_ci("make cov"), "pyproject.toml": PYPROJECT_80}, (None, "indirect_run")),
    ({**_ci("npm run coverage"), **JEST_80}, (None, "indirect_run")),
    ({**_ci("just coverage"), "pyproject.toml": PYPROJECT_80}, (None, "indirect_run")),
    ({**_ci("npm install"), "pyproject.toml": PYPROJECT_80}, (False, "not_run_in_ci")),
    ({**_action_step(""), **GO_TC}, (True, "ci_run")),
    ({**_action_step("        continue-on-error: true\n"), **GO_TC}, (False, "not_run_in_ci")),
    ({**_action_step("        if: false\n"), **GO_TC}, (False, "not_run_in_ci")),
    ({**_ci("pytest --cov-report=xml"), "pyproject.toml": PYPROJECT_80}, (False, "not_run_in_ci")),
    (_ci("pytest --cov-fail-under=80"), (False, "not_run_in_ci")),
    ({**_ci("pytest"), "pyproject.toml": "[tool.pytest.ini_options]\naddopts = \"--cov-report=xml\"\n"
      "[tool.coverage.report]\nfail_under = 80\n"}, (False, "not_run_in_ci")),
    ({**_ci("pytest"), "pyproject.toml": "[tool.pytest.ini_options]\naddopts = \"--cov-fail-under=80\"\n"},
     (False, "not_run_in_ci")),
    ({**_ci("pytest --cov"), "pyproject.toml": "[tool.pytest.ini_options]\naddopts = \"--cov-fail-under=80\"\n"},
     (True, "ci_run")),
    ({**_ci("npx jest --coverage=false"), **JEST_80}, (False, "not_run_in_ci")),
    ({**_ci("npx jest --coverage=true"), **JEST_80}, (True, "ci_run")),
    ({**_ci("npx jest --coverageDirectory=out"), **JEST_80}, (None, "indirect_run")),
    ({**_ci("npx jest"), "jest.config.js": "module.exports = {\n  // collectCoverage: true,\n"
      "  coverageThreshold: { global: { lines: 80 } },\n};\n"}, (None, "indirect_run")),
    ({**_ci("npx jest"), "jest.config.js": "module.exports = {\n  /* collectCoverage: true */\n"
      "  coverageThreshold: { global: { lines: 80 } },\n};\n"}, (None, "indirect_run")),
], ids=["make-coverage", "make-cov", "npm-run-coverage", "just-coverage", "npm-install",
        "go-tc-action", "go-tc-action-continue-on-error", "go-tc-action-if-false",
        "pytest-cov-report-only", "pytest-fail-under-only", "addopts-cov-report-only",
        "addopts-threshold-no-cov", "addopts-threshold-cov-on-line", "jest-coverage-false", "jest-coverage-true", "jest-coverage-directory",
        "jest-collect-line-comment", "jest-collect-block-comment"])
def test_final_review_round(tmp_path: Path, files: dict[str, str],
                            expected: tuple[bool | None, str]) -> None:
    assert _read(tmp_path, files) == expected


def test_an_oversized_ci_file_is_unread_not_absent(tmp_path: Path) -> None:
    """A CI file over the read cap was not read: the run is unknown, never absent."""
    big = _wf("pytest --cov") + "#" + "x" * 2_100_000 + "\n"
    assert _read(tmp_path, {".github/workflows/ci.yml": big, "pyproject.toml": PYPROJECT_80}) == (
        None, "unread_ci_system")
