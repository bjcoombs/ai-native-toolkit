"""CI hazards that hold the ``ci_runs_tests`` gate: a skip label and ignored test failures (#828).

Every fixture is invented: label names, workflow names and build files exist only here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import lib.scoring.level_tally as lt
from lib.ci.ci_hazards import NO_CI_FILES, NO_PR_CHECKS, check_jobs, ignores_test_failures, job_skip_labels, scan_ci_hazards
from lib.counts_only.content_free import counts_only

GATED = """name: checks
on:
  pull_request:
jobs:
  lint:
    if: ${{ !contains(github.event.pull_request.labels.*.name, 'skip-checks') }}
    runs-on: ubuntu-latest
    steps:
      - run: ruff check .
  test:
    needs: [lint]
    runs-on: ubuntu-latest
    steps:
      - run: pytest
"""

PLAIN = """name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: pytest
"""


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def _held(block: dict[str, object]) -> lt.LevelResult:
    data = {"layer_grades": {"instruction_file_present": True}, "ci_checks": {"enforced_check_runs": True},
            "ci_hazards": block}
    return lt.level_result(lt.default_model(), data)


# --- the skip label ----------------------------------------------------------------------


def test_a_label_that_skips_every_pr_check_job_is_counted(tmp_path: Path) -> None:
    block = scan_ci_hazards(_repo(tmp_path, {".github/workflows/checks.yml": GATED}))
    assert (block["skip_labels"], block["ci_hazard_files"]) == (1, [".github/workflows/checks.yml"])


def test_a_job_that_needs_a_skipped_job_is_skipped_with_it() -> None:
    assert job_skip_labels(GATED) == {"lint": frozenset({"skip-checks"}), "test": frozenset({"skip-checks"})}


@pytest.mark.parametrize("status", ["always()", "!cancelled()"])
def test_a_job_with_a_status_function_runs_after_a_skipped_need(status: str) -> None:
    text = GATED.replace("    needs: [lint]\n", f"    needs: [lint]\n    if: ${{{{ {status} }}}}\n")
    assert job_skip_labels(text)["test"] == frozenset()
    assert job_skip_labels(text)["lint"] == frozenset({"skip-checks"})


@pytest.mark.parametrize("files", [
    {".github/workflows/checks.yml": GATED.replace("    needs: [lint]\n", "")},  # one job runs anyway
    {".github/workflows/checks.yml": GATED, ".github/workflows/ci.yml": PLAIN},  # another workflow runs checks
    {".github/workflows/checks.yml": GATED.replace("'skip-checks') }}", "'skip-checks') || true }}")},
])
def test_any_check_the_label_does_not_skip_clears_it(tmp_path: Path, files: dict[str, str]) -> None:
    assert scan_ci_hazards(_repo(tmp_path, files))["skip_labels"] == 0


def test_no_workflow_running_checks_on_pull_requests_reads_not_measured(tmp_path: Path) -> None:
    block = scan_ci_hazards(_repo(tmp_path, {".github/workflows/ci.yml": PLAIN.replace(", pull_request", "")}))
    assert (block["skip_labels"], block["skip_labels_reason"]) == (None, NO_PR_CHECKS)


# --- ignored test failures ---------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "mvn -Dmaven.test.failure.ignore=true verify",
    "mvn -Dmaven.test.failure.ignore verify",
    "<maven.test.failure.ignore>true</maven.test.failure.ignore>",
    "<testFailureIgnore>true</testFailureIgnore>",
])
def test_each_way_to_ignore_test_failures_is_read(text: str) -> None:
    assert ignores_test_failures(text)


@pytest.mark.parametrize("text", ["mvn -Dmaven.test.failure.ignore=false verify", "mvn -DskipTests verify",
                                  "<testFailureIgnore>false</testFailureIgnore>",
                                  "<!-- <testFailureIgnore>true</testFailureIgnore> -->",
                                  "  # mvn -Dmaven.test.failure.ignore=true verify",
                                  "// sh 'mvn -Dmaven.test.failure.ignore=true install'"])
def test_a_setting_that_does_not_ignore_failures_is_not_read(text: str) -> None:
    assert not ignores_test_failures(text)


JENKINS = """pipeline {
  agent any
  stages {
    stage('Build') {
      steps {
        sh 'mvn -B -Dmaven.test.failure.ignore=true install'
      }
    }
  }
}
"""


def test_a_jenkinsfile_that_ignores_test_failures_is_counted(tmp_path: Path) -> None:
    block = scan_ci_hazards(_repo(tmp_path, {"Jenkinsfile": JENKINS}))
    assert (block["test_failures_ignored"], block["ci_hazard_files"]) == (1, ["Jenkinsfile"])


def test_a_make_recipe_the_ci_step_runs_is_followed(tmp_path: Path) -> None:
    files = {".github/workflows/ci.yml": PLAIN.replace("pytest", "make verify"),
             "Makefile": "verify:\n\tmvn -Dmaven.test.failure.ignore=true verify\n"}
    block = scan_ci_hazards(_repo(tmp_path, files))
    assert (block["test_failures_ignored"], block["ci_hazard_files"]) == (1, ["Makefile"])


POM = """<project>
  <modules><module>core</module></modules>
</project>
"""
MODULE = """<project>
  <build><plugins><plugin>
    <artifactId>maven-surefire-plugin</artifactId>
    <configuration><testFailureIgnore>true</testFailureIgnore></configuration>
  </plugin></plugins></build>
</project>
"""


def test_a_reactor_module_pom_is_read_and_a_stray_fixture_pom_is_not(tmp_path: Path) -> None:
    files = {"pom.xml": POM, "core/pom.xml": MODULE, "core/src/test/resources/sample/pom.xml": MODULE}
    block = scan_ci_hazards(_repo(tmp_path, files))
    assert (block["test_failures_ignored"], block["ci_hazard_files"]) == (1, ["core/pom.xml"])


def test_no_ci_file_and_no_pom_reads_not_measured(tmp_path: Path) -> None:
    block = scan_ci_hazards(_repo(tmp_path, {"README.md": "x\n"}))
    assert (block["test_failures_ignored"], block["test_failures_ignored_reason"]) == (None, NO_CI_FILES)
    assert (block["skip_labels"], block["skip_labels_reason"]) == (None, NO_PR_CHECKS)


def test_a_clean_repository_reads_zero(tmp_path: Path) -> None:
    block = scan_ci_hazards(_repo(tmp_path, {".github/workflows/ci.yml": PLAIN, "pom.xml": POM}))
    assert (block["skip_labels"], block["test_failures_ignored"], block["ci_hazard_files"]) == (0, 0, [])


# --- each hazard holds Step 3 and leaves the tally unchanged ------------------------------


@pytest.mark.parametrize(("files", "hazard"), [
    ({".github/workflows/checks.yml": GATED}, "ci-skip-label.1"),
    ({".github/workflows/ci.yml": PLAIN, "Jenkinsfile": JENKINS}, "ci-test-failures-ignored.1"),
])
def test_each_hazard_holds_step_3_and_leaves_the_tally_unchanged(
        tmp_path: Path, files: dict[str, str], hazard: str) -> None:
    clean = _held(scan_ci_hazards(_repo(tmp_path / "clean", {".github/workflows/ci.yml": PLAIN})))  # type: ignore[arg-type]
    present = _held(scan_ci_hazards(_repo(tmp_path / "hazard", files)))  # type: ignore[arg-type]
    assert clean["step"] == 3 and present["step"] == 2
    assert present["tally"] == clean["tally"]
    assert present["next_step"] is not None
    assert present["next_step"]["blockers"] == [
        {"kind": "gate", "gate": "ci_runs_tests", "condition": hazard, "state": "met", "polarity": "hazard"}]
    (entry,) = present["hazards"]
    assert entry["id"] == hazard and entry["gate"] == "ci_runs_tests" and entry["fix"]


def test_the_block_survives_counts_only_with_its_paths(tmp_path: Path) -> None:
    block = scan_ci_hazards(_repo(tmp_path, {"Jenkinsfile": JENKINS}))
    kept = counts_only({"ci_hazards": block})["ci_hazards"]
    assert kept["test_failures_ignored"] == 1 and kept["ci_hazard_files"] == ["Jenkinsfile"]


def test_a_job_that_runs_no_check_does_not_clear_the_label(tmp_path: Path) -> None:
    text = GATED.replace("jobs:\n", "jobs:\n  changes:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo filter\n", 1)
    assert check_jobs(text) == frozenset({"lint", "test"})
    assert scan_ci_hazards(_repo(tmp_path, {".github/workflows/checks.yml": text}))["skip_labels"] == 1


def test_a_quoted_true_is_read() -> None:
    assert ignores_test_failures("sh 'mvn -Dmaven.test.failure.ignore=\"true\" install'")


def test_a_setting_only_inside_a_pom_profile_is_not_read(tmp_path: Path) -> None:
    profiled = MODULE.replace("<build>", "<profiles><profile><id>lenient</id><build>").replace(
        "</build>", "</build></profile></profiles>")
    block = scan_ci_hazards(_repo(tmp_path, {".github/workflows/ci.yml": PLAIN.replace("pytest", "mvn -B verify"),
                                             "pom.xml": profiled}))
    assert block["test_failures_ignored"] == 0


def test_failure_alone_is_skipped_with_its_need_and_a_comparison_is_not_read() -> None:
    text = GATED.replace("    needs: [lint]\n", "    needs: [lint]\n    if: ${{ failure() }}\n")
    assert job_skip_labels(text)["test"] == frozenset({"skip-checks"})
    inverted = GATED.replace("'skip-checks') }}", "'skip-checks') == false }}")
    assert job_skip_labels(inverted)["lint"] == frozenset()


def test_labels_compare_without_case(tmp_path: Path) -> None:
    other = GATED.replace("'skip-checks'", "'SKIP-Checks'").replace("name: checks", "name: more")
    block = scan_ci_hazards(_repo(tmp_path, {".github/workflows/checks.yml": GATED, ".github/workflows/more.yml": other}))
    assert block["skip_labels"] == 1


@pytest.mark.parametrize("refail", ["        junit '**/target/surefire-reports/*.xml'\n",
                                    "        junit testResults: '**/reports/*.xml'\n"])
def test_a_jenkinsfile_that_fails_the_build_from_the_test_reports_is_not_counted(tmp_path: Path, refail: str) -> None:
    text = JENKINS.replace("        sh 'mvn", refail + "        sh 'mvn")
    block = scan_ci_hazards(_repo(tmp_path, {"Jenkinsfile": text}))
    assert block["test_failures_ignored"] == 0


def test_a_report_action_that_fails_on_failure_clears_its_workflow(tmp_path: Path) -> None:
    text = PLAIN.replace("pytest", "mvn -Dmaven.test.failure.ignore=true verify") + (
        "      - uses: example-org/report-action@v1\n        with:\n          fail_on_failure: true\n")
    assert scan_ci_hazards(_repo(tmp_path, {".github/workflows/ci.yml": text}))["test_failures_ignored"] == 0


def test_a_report_step_in_ci_covers_a_pom_that_ignores_test_failures(tmp_path: Path) -> None:
    jenkins = JENKINS.replace("-Dmaven.test.failure.ignore=true ", "").replace(
        "        sh 'mvn", "        junit '**/surefire-reports/*.xml'\n        sh 'mvn")
    block = scan_ci_hazards(_repo(tmp_path, {"Jenkinsfile": jenkins, "pom.xml": POM, "core/pom.xml": MODULE}))
    assert block["test_failures_ignored"] == 0


@pytest.mark.parametrize(("extra", "counted"), [("", 0), ("          fail-on-error: false\n", 1)])
def test_a_test_reporter_action_fails_the_run_unless_told_not_to(tmp_path: Path, extra: str, counted: int) -> None:
    text = PLAIN.replace("pytest", "mvn -Dmaven.test.failure.ignore=true verify") + (
        "      - uses: dorny/test-reporter@v1\n        with:\n          reporter: java-junit\n" + extra)
    assert scan_ci_hazards(_repo(tmp_path, {".github/workflows/ci.yml": text}))["test_failures_ignored"] == counted


def test_a_scheduled_workflow_that_ignores_test_failures_is_not_counted(tmp_path: Path) -> None:
    nightly = PLAIN.replace("on: [push, pull_request]", "on:\n  schedule:\n    - cron: '0 3 * * *'").replace(
        "pytest", "mvn -Dmaven.test.failure.ignore=true verify")
    block = scan_ci_hazards(_repo(tmp_path, {".github/workflows/nightly.yml": nightly, ".github/workflows/ci.yml": PLAIN}))
    assert (block["test_failures_ignored"], block["ci_hazard_files"]) == (0, [])


@pytest.mark.parametrize("step", ["junit allowEmptyResults: true, testResults: '**/reports/*.xml'",
                                  "junit skipPublishingChecks: true, testResults: '**/reports/*.xml'"])
def test_a_junit_step_with_named_arguments_fails_the_build(tmp_path: Path, step: str) -> None:
    text = JENKINS.replace("        sh 'mvn", f"        {step}\n        sh 'mvn")
    assert scan_ci_hazards(_repo(tmp_path, {"Jenkinsfile": text}))["test_failures_ignored"] == 0


# --- #1590: what the detectors follow and where the report-step patterns apply ----------


@pytest.mark.parametrize(("command", "expected"), [
    ("mvn -B verify", ["core/pom.xml"]),
    ("mvn -Dmaven.test.failure.ignore=true verify", [".github/workflows/ci.yml", "core/pom.xml"]),
])
def test_a_github_step_named_for_junit_does_not_clear_the_setting(
        tmp_path: Path, command: str, expected: list[str]) -> None:
    text = PLAIN.replace("pytest", command) + (
        "      - name: Upload junit (XML) reports\n        uses: actions/upload-artifact@v4\n")
    block = scan_ci_hazards(_repo(tmp_path, {".github/workflows/ci.yml": text, "pom.xml": POM, "core/pom.xml": MODULE}))
    assert (block["test_failures_ignored"], block["ci_hazard_files"]) == (len(expected), expected)


@pytest.mark.parametrize(("step", "extra"), [
    ("./ci/build.sh", {"ci/build.sh": "#!/bin/sh\nset -e\ncd service && mvn -B verify\n"}),
    ("make verify", {"Makefile": "verify:\n\tmvn -B -f service/pom.xml verify\n"}),
    ("uses: ./.github/workflows/maven.yml", {".github/workflows/maven.yml": (
        "on: workflow_call\njobs:\n  maven:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - run: mvn -B -f service/pom.xml verify\n")}),
])
def test_a_maven_command_inside_a_followed_file_is_followed(tmp_path: Path, step: str, extra: dict[str, str]) -> None:
    caller = PLAIN.replace("run: pytest", step) if step.startswith("uses:") else PLAIN.replace("pytest", step)
    files = {".github/workflows/ci.yml": caller, "service/pom.xml": MODULE, **extra}
    block = scan_ci_hazards(_repo(tmp_path, files))
    assert (block["test_failures_ignored"], block["ci_hazard_files"]) == (1, ["service/pom.xml"])


def test_every_step_that_runs_the_same_makefile_is_kept(tmp_path: Path) -> None:
    text = PLAIN.replace("      - run: pytest\n", "      - run: make lint\n      - run: make verify\n")
    makefile = "lint:\n\techo lint\n\nverify:\n\tmvn -Dmaven.test.failure.ignore=true verify\n"
    block = scan_ci_hazards(_repo(tmp_path, {".github/workflows/ci.yml": text, "Makefile": makefile}))
    assert (block["test_failures_ignored"], block["ci_hazard_files"]) == (1, ["Makefile"])


ACTION = """name: build
runs:
  using: composite
  steps:
    - run: mvn -Dmaven.test.failure.ignore=true verify
      shell: bash
"""


def test_a_setting_inside_a_local_composite_action_counts_once_as_the_action(tmp_path: Path) -> None:
    text = PLAIN.replace("      - run: pytest\n", "      - uses: ./.github/actions/maven\n")
    block = scan_ci_hazards(_repo(tmp_path, {".github/workflows/ci.yml": text, ".github/actions/maven/action.yml": ACTION}))
    assert (block["test_failures_ignored"], block["ci_hazard_files"]) == (1, [".github/actions/maven/action.yml"])
