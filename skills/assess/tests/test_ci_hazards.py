"""CI hazards that hold the ``ci_runs_tests`` gate: a skip label and ignored test failures (#828).

Every fixture is invented: label names, workflow names and build files exist only here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import lib.scoring.level_tally as lt
from lib.ci.ci_hazards import NO_CI_FILES, NO_PR_CHECKS, ignores_test_failures, job_skip_labels, scan_ci_hazards
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
                                  "<testFailureIgnore>false</testFailureIgnore>"])
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
